import json
import os
import re
from urllib.parse import unquote
from datetime import datetime, timezone

from src.classifier import classify_content, infer_region_code
from src.db import get_conn
from src.ingest_gate import should_write_content_item
from src.portal_property_crawl import coerce_listing_display_title
from src.text_utils import (
    body_zh_field_is_corrupt_jp_placeholder,
    build_schema_json,
    build_seo_description,
    build_seo_title,
    build_slug,
    dual_translate,
    rewrite_for_originality,
)


_JP_ACCESS_WALK_RE = re.compile(r"(?:徒歩|歩|步行)\s*(?:約)?\s*([0-9]{1,3})\s*分")
_JP_ACCESS_STATION_QUOTE_RE = re.compile(r"「([^」]{1,40})」")
_JP_PREF_RE = re.compile(r"([\u3040-\u30FF\u3400-\u9FFF]{2,12}(?:都|道|府|県))")
_JP_ACCESS_LINE_RE = re.compile(
    r"((?:JR|ＪＲ|東京メトロ|都営地下鉄|都営|地下鉄|東急|西武|小田急|京王|京急|相鉄|阪急|名鉄)"
    r"[^\n]{0,24}?線)"
)
_JP_WARD_RE = re.compile(r"([\u3040-\u30FF\u3400-\u9FFF]{1,16}区)")
_JP_CITY_RE = re.compile(r"([\u3040-\u30FF\u3400-\u9FFF]{1,16}市)")


def _parse_jp_access_station_walk(access: str) -> tuple[str, int, str]:
    """
    嘗試從交通字串擷取 (station_name, walk_min, line_name_hint)。
    - station_name 不含「駅」字樣（例：新宿）。
    - line_name_hint 用於 station_name 重名時縮小候選。
    """
    text = str(access or "").strip()
    if not text:
        return "", 0, ""

    best_station = ""
    best_walk = 0
    best_line = ""

    for m in _JP_ACCESS_WALK_RE.finditer(text):
        try:
            walk = int(m.group(1))
        except Exception:
            continue
        if walk <= 0 or walk > 240:
            continue
        ctx = text[max(0, m.start() - 90) : m.start()]
        station = ""
        mq = None
        for mq in _JP_ACCESS_STATION_QUOTE_RE.finditer(ctx):
            pass
        if mq:
            station = str(mq.group(1) or "").strip()
        if not station:
            ms = re.search(r"([\u3040-\u30FF\u3400-\u9FFFー々ヶ]{1,40})\s*駅", ctx)
            if ms:
                station = str(ms.group(1) or "").strip()
        if not station:
            ms2 = re.search(r"/\s*([\u3040-\u30FF\u3400-\u9FFFー々ヶ]{1,40})\s*駅", ctx)
            if ms2:
                station = str(ms2.group(1) or "").strip()
        if not station:
            continue
        # 拆掉可能尾巴（例：新宿駅）
        station = station.replace("駅", "").strip()
        if not station or len(station) > 40:
            continue

        line = ""
        ml = None
        for ml in _JP_ACCESS_LINE_RE.finditer(ctx):
            pass
        if ml:
            line = str(ml.group(1) or "").strip()
        if not best_station or walk < best_walk or best_walk <= 0:
            best_station, best_walk, best_line = station, walk, line
    return best_station, best_walk, best_line


def _lookup_jp_station_id(conn, *, station_name: str, line_hint: str = "", pref_hint: str = "") -> int:
    s = str(station_name or "").strip()
    if not s:
        return 0
    pref = str(pref_hint or "").strip()
    line = str(line_hint or "").strip()

    line_ids: list[int] = []
    if line:
        rows = conn.execute("SELECT line_id FROM jp_trans_line WHERE line_name = ? LIMIT 3", (line,)).fetchall()
        if not rows:
            rows = conn.execute("SELECT line_id FROM jp_trans_line WHERE line_name LIKE ? LIMIT 8", (f"%{line}%",)).fetchall()
        line_ids = [int(r[0]) for r in rows if r and r[0] is not None]

    if line_ids:
        for lid in line_ids:
            if pref:
                row = conn.execute(
                    "SELECT station_id FROM jp_trans_station WHERE station_name = ? AND line_id = ? AND prefecture = ? LIMIT 1",
                    (s, int(lid), pref),
                ).fetchone()
                if row:
                    return int(row[0])
            row = conn.execute(
                "SELECT station_id FROM jp_trans_station WHERE station_name = ? AND line_id = ? LIMIT 1",
                (s, int(lid)),
            ).fetchone()
            if row:
                return int(row[0])

    if pref:
        row = conn.execute(
            "SELECT station_id FROM jp_trans_station WHERE station_name = ? AND prefecture = ? LIMIT 1",
            (s, pref),
        ).fetchone()
        if row:
            return int(row[0])
    row = conn.execute("SELECT station_id FROM jp_trans_station WHERE station_name = ? LIMIT 1", (s,)).fetchone()
    return int(row[0]) if row else 0


def _ensure_jp_transit_station_row(
    conn,
    *,
    line_id: int,
    station_name: str,
    pref_hint: str = "",
    addr_hint: str = "",
) -> int:
    """Ensure jp_trans_station has (line_id, station_name) row and return station_id.

    Station IDs from seed data typically use `line_id*100 + n`.
    For dynamically discovered stations, allocate within `line_id*1000 + n` to avoid collisions.
    """
    lid = int(line_id or 0)
    name = str(station_name or "").strip().replace("駅", "").strip()
    if lid <= 0 or not name:
        return 0
    row = conn.execute(
        "SELECT station_id FROM jp_trans_station WHERE line_id = ? AND station_name = ? LIMIT 1",
        (lid, name),
    ).fetchone()
    if row and row[0] is not None:
        return int(row[0])

    pref = str(pref_hint or "").strip()
    addr = str(addr_hint or "").strip()
    ward = ""
    city = ""
    if addr:
        mw = _JP_WARD_RE.search(addr)
        if mw:
            ward = str(mw.group(1) or "").strip()
        mc = _JP_CITY_RE.search(addr)
        if mc:
            city = str(mc.group(1) or "").strip()
    if not city and pref:
        city = pref

    base = lid * 1000
    try:
        mx = conn.execute(
            "SELECT MAX(station_id) FROM jp_trans_station WHERE line_id = ? AND station_id >= ? AND station_id < ?",
            (lid, base, base + 1000),
        ).fetchone()
        mx_id = int((mx[0] if mx else 0) or 0)
    except Exception:
        mx_id = 0
    seq = (mx_id - base + 1) if mx_id >= base else 1
    sid = base + max(1, min(999, seq))

    # Race-safe insert: if another worker inserts concurrently, we fall back to the existing row.
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO jp_trans_station
            (station_id, line_id, station_name, prefecture, city, ward, full_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (int(sid), int(lid), name, pref, city, ward, addr or name),
        )
    except Exception:
        pass
    row = conn.execute(
        "SELECT station_id FROM jp_trans_station WHERE line_id = ? AND station_name = ? LIMIT 1",
        (lid, name),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else int(sid)


def _bind_jp_transit_to_content_item(conn, *, source_item_id: int, src_row: dict) -> None:
    try:
        from src.case_metadata import infer_case_metadata
        from src.portal_case_search import _extract_listing_fields
    except Exception:
        return
    try:
        d = dict(src_row or {})
        meta = infer_case_metadata(d)
        f = _extract_listing_fields(d, meta=meta)
        access = str(f.get("access_line_jp") or "").strip()
        addr = str(f.get("address_line_jp") or "").strip()
        if not access:
            return
        station, walk_min, line_hint = _parse_jp_access_station_walk(access)
        pref_hint = ""
        if addr:
            mp = _JP_PREF_RE.search(addr)
            if mp:
                pref_hint = str(mp.group(1) or "").strip()
        station_id = _lookup_jp_station_id(conn, station_name=station, line_hint=line_hint, pref_hint=pref_hint) if station else 0
        if station and station_id <= 0 and line_hint:
            try:
                rows = conn.execute("SELECT line_id FROM jp_trans_line WHERE line_name = ? LIMIT 3", (line_hint,)).fetchall()
                if not rows:
                    rows = conn.execute(
                        "SELECT line_id FROM jp_trans_line WHERE line_name LIKE ? LIMIT 5",
                        (f"%{line_hint}%",),
                    ).fetchall()
                for r in rows or []:
                    lid = int(r[0]) if r and r[0] is not None else 0
                    if lid <= 0:
                        continue
                    station_id = _ensure_jp_transit_station_row(
                        conn, line_id=lid, station_name=station, pref_hint=pref_hint, addr_hint=addr
                    )
                    if station_id > 0:
                        break
            except Exception:
                station_id = 0
        walk_eff = int(walk_min or 0) if walk_min and 0 < int(walk_min) <= 240 else 0
        if station_id <= 0 and walk_eff <= 0:
            return
        conn.execute(
            "UPDATE content_items SET jp_station_id = ?, walk_min = ?, updated_at = CURRENT_TIMESTAMP WHERE source_item_id = ?",
            (int(station_id or 0), int(walk_eff or 0), int(source_item_id)),
        )
    except Exception:
        return


def _build_listing_zh_fallback(src: dict) -> tuple[str, str]:
    """翻譯服務失敗時，依已抽取欄位生成可讀的中日房源摘要。"""
    try:
        from src.case_metadata import infer_case_metadata
        from src.portal_case_search import _extract_listing_fields
    except Exception:
        return "", ""
    d = dict(src or {})
    meta = infer_case_metadata(d)
    f = _extract_listing_fields(d, meta=meta)

    def _v(raw: object, *, max_len: int = 220) -> str:
        s = re.sub(r"\s+", " ", str(raw or "")).strip(" ：:-")
        if not s or s in {"—", "-", "None", "null"}:
            return "—"
        return s[:max_len]

    def _source_highlights(*, max_items: int = 6) -> list[str]:
        body = re.sub(r"\s+", " ", str(d.get("body_original") or "")).strip()
        if not body:
            return []
        parts = re.split(r"(?<=[。！？!?])\s+", body)
        if len(parts) <= 1:
            parts = re.split(r"\s{2,}|(?<=\))\s+|(?<=）)\s+", body)
        good_tokens = (
            "徒歩",
            "駅",
            "専有面積",
            "平米",
            "㎡",
            "LDK",
            "収納",
            "ウォークイン",
            "キッチン",
            "ディスポーザ",
            "食器洗い",
            "オートロック",
            "宅配ボックス",
            "スーパー",
            "コンビニ",
            "学校",
            "公園",
            "バルコニー",
            "南向き",
            "眺望",
            "総戸数",
            "新築",
            "分譲",
            "即入居",
            "価格",
            "管理費",
            "修繕",
            "ペット",
        )
        bad_tokens = (
            "メインコンテンツ",
            "最近見た物件",
            "お気に入り",
            "メニュー",
            "資料請求",
            "無料",
            "問合せ",
            "QRコード",
            "電話",
            "会社情報",
            "営業時",
            "免許番号",
            "口コミ",
            "この物件が気にな",
            "ご意見",
            "利用規約",
            "個人情報",
            "※",
        )
        out: list[str] = []
        seen: set[str] = set()
        for raw in parts:
            s = re.sub(r"\s+", " ", str(raw or "")).strip(" ・、。")
            if len(s) < 24 or len(s) > 220:
                continue
            if any(tok in s for tok in bad_tokens):
                continue
            if not any(tok in s for tok in good_tokens):
                continue
            s = s[:118].strip(" ・、。")
            key = re.sub(r"[0-9０-９,，.．]", "", s)[:42]
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= max_items:
                break
        return out

    img_count = 0
    img_lines = [x.strip() for x in str(d.get("image_urls") or "").splitlines() if x.strip()]
    img_count = len(img_lines)
    if img_count <= 0:
        try:
            media = json.loads(str(d.get("listing_media_json") or "[]"))
            if isinstance(media, list):
                img_count = len(media)
        except Exception:
            img_count = 0
    floor_structure_raw = _v(f.get("floor_structure_jp"))
    floor_text_hant_raw = _v(f.get("floor_text_hant"))
    floor_text = floor_structure_raw or floor_text_hant_raw
    m_building_floor = re.search(r"(?:地上)?[0-9０-９]{1,3}\s*階建", floor_structure_raw)
    if m_building_floor:
        floor_text = m_building_floor.group(0)
        floor_label = "建物階數"
    elif floor_text_hant_raw.endswith("樓建物"):
        floor_label = "建物階數"
    else:
        floor_label = "樓層"
    lines_hant = [
        "日本房產案源（本地快取重整）",
        f"物件名：{_v(f.get('building_name_jp'))}",
        f"價格：{_v(f.get('price_text_hant'))}",
        f"格局：{_v(f.get('layout_line_jp') or f.get('layout_text_hant'))}",
        f"專有面積：{_v(f.get('exclusive_area_jp') or f.get('area_text_hant'))}",
        f"所在地：{_v(f.get('address_line_jp'))}",
        f"交通：{_v(f.get('access_line_jp') or meta.get('transit_line_zh'), max_len=320)}",
        f"{floor_label}：{floor_text}",
        f"築年月：{_v(f.get('built_ym_jp') or f.get('age_text_hant'))}",
        f"總戶數：{_v(f.get('total_units_jp'))}",
        f"建物構造：{_v(f.get('structure_jp'))}",
        f"管理費：{_v(f.get('manage_fee_jp'))}",
        f"修繕積立金：{_v(f.get('reserve_fee_jp'))}",
        f"停車場：{_v(f.get('parking_jp'))}",
        f"現況：{_v(f.get('status_jp'))}",
        f"引渡：{_v(f.get('handover_jp'))}",
    ]
    if img_count > 0:
        lines_hant.append(f"圖片：已保留原站素材（{img_count} 張）")
    feature_tags = [str(x).strip() for x in (f.get("feature_tags_hant") or []) if str(x).strip()]
    if feature_tags:
        lines_hant.append("標籤：" + "、".join(feature_tags[:8]))
    date_parts = [
        f"公開：{_v(f.get('info_open_jp'), max_len=80)}" if f.get("info_open_jp") else "",
        f"更新：{_v(f.get('next_update_jp'), max_len=80)}" if f.get("next_update_jp") else "",
    ]
    date_line = "；".join([x for x in date_parts if x])
    if date_line:
        lines_hant.append(f"資訊日期：{date_line}")
    highlights = _source_highlights(max_items=6)
    if highlights:
        lines_hant.append("原站重點：")
        lines_hant.extend([f"- {x}" for x in highlights])
    if f.get("property_no_jp"):
        lines_hant.append(f"物件番號：{_v(f.get('property_no_jp'))}")
    lines_hant.append(f"來源：{_v(d.get('item_url'), max_len=360)}")
    hant = "\n".join(lines_hant).strip()
    hans = (
        hant.replace("房產", "房产")
        .replace("本地快取重整", "本地缓存重整")
        .replace("專有面積", "专有面积")
        .replace("樓層", "楼层")
        .replace("建物階數", "建筑层数")
        .replace("總戶數", "总户数")
        .replace("建物構造", "建筑构造")
        .replace("停車場", "停车场")
        .replace("現況", "现况")
        .replace("圖片", "图片")
        .replace("已保留原站素材", "已保留原站素材")
        .replace("來源", "来源")
        .replace("幣", "币")
    )
    return hant, hans


def _env_enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _simple_hans(text: str) -> str:
    return (
        (text or "")
        .replace("房產", "房产")
        .replace("日本買屋", "日本买房")
        .replace("專有面積", "专有面积")
        .replace("樓層", "楼层")
        .replace("總戶數", "总户数")
        .replace("來源", "来源")
        .replace("幣", "币")
    )


def _save_content_item_fast(
    conn,
    *,
    source_item_id: int,
    title_hant: str,
    title_hans: str,
    body_hant: str,
    body_hans: str,
    source_name: str,
    keyword_type: str,
    intent_target: str,
    topic_category: str,
    keyword_tags: str,
) -> None:
    region_code = infer_region_code(title_hans, body_hans)
    region_name = {"tw": "台灣", "hk": "香港", "cn": "中國", "sg": "東南亞"}.get(region_code, "全球華人")
    slug = build_slug(region_code, keyword_type, title_hans)
    seo_title = build_seo_title(title_hant, region_name)
    seo_description = build_seo_description(title_hant, source_name)
    schema_json = build_schema_json(slug, seo_title, seo_description, region_name, body_hant)
    exists = conn.execute("SELECT 1 FROM content_items WHERE source_item_id = ?", (source_item_id,)).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE content_items
            SET title_zh_hant = ?, title_zh_hans = ?, body_zh_hant = ?, body_zh_hans = ?,
                region_code = ?, keyword_type = ?, intent_target = ?, topic_category = ?, keyword_tags = ?,
                seo_slug = ?, seo_title = ?, seo_description = ?, schema_json = ?,
                created_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE source_item_id = ?
            """,
            (
                title_hant,
                title_hans,
                body_hant,
                body_hans,
                region_code,
                keyword_type,
                intent_target,
                topic_category,
                keyword_tags,
                slug,
                seo_title,
                seo_description,
                schema_json,
                source_item_id,
            ),
        )
        return
    conn.execute(
        """
        INSERT INTO content_items (
            source_item_id, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans,
            region_code, keyword_type, intent_target, topic_category, keyword_tags,
            seo_slug, seo_title, seo_description, schema_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            source_item_id,
            title_hant,
            title_hans,
            body_hant,
            body_hans,
            region_code,
            keyword_type,
            intent_target,
            topic_category,
            keyword_tags,
            slug,
            seo_title,
            seo_description,
            schema_json,
        ),
    )


def upsert_source_item(conn, item) -> int:
    item_url = str(getattr(item, "item_url", "") or "")
    img = getattr(item, "image_urls", "") or ""
    ck = getattr(item, "content_kind", "") or ""
    safe_title = coerce_listing_display_title(
        str(getattr(item, "title_original", "") or ""),
        item_url,
    )
    # HOMES：避免「推薦物件」縮圖跨案汙染。
    # - 若 b-id token 可得，則僅接受命中 token 的圖片。
    # - 若新抓取完全無命中，且舊資料有命中 → 保留舊資料。
    # - 若新舊皆無命中 → 直接清空，避免錯圖。
    try:
        from src.homes_media_token import homes_listing_image_tokens

        homes_tokens = homes_listing_image_tokens(item_url)
    except Exception:
        homes_tokens = ()
    if homes_tokens:
        existing = conn.execute(
            "SELECT COALESCE(image_urls,'') AS image_urls FROM source_items WHERE item_url = ?",
            (item_url,),
        ).fetchone()
        existing_img = str(existing["image_urls"] or "") if existing else ""

        def _matched_lines(blob: str) -> list[str]:
            out: list[str] = []
            for ln in str(blob or "").splitlines():
                s = ln.strip()
                if not s:
                    continue
                try:
                    dec = unquote(s).lower()
                except Exception:
                    dec = s.lower()
                if any(tok in dec for tok in homes_tokens):
                    out.append(s)
            return list(dict.fromkeys(out))

        new_matched = _matched_lines(img)
        old_matched = _matched_lines(existing_img)
        if new_matched:
            img = "\n".join(new_matched)
        elif old_matched:
            img = "\n".join(old_matched)
        else:
            img = ""
    conn.execute(
        """
        INSERT OR IGNORE INTO source_items (
            source_name, source_category, source_url, item_url, title_original, body_original,
            language, published_at, access_status, access_note, last_checked_at,
            image_urls, content_kind
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        """,
        (
            item.source_name,
            item.source_category,
            item.source_url,
            item_url,
            safe_title,
            item.body_original,
            item.language,
            item.published_at,
            item.access_status,
            item.access_note,
            img,
            ck,
        ),
    )
    conn.execute(
        """
        UPDATE source_items
        SET title_original = ?, body_original = ?, source_category = ?, source_url = ?,
            language = ?, published_at = ?, access_status = ?, access_note = ?, last_checked_at = CURRENT_TIMESTAMP,
            image_urls = ?, content_kind = ?
        WHERE item_url = ?
        """,
        (
            safe_title,
            item.body_original,
            item.source_category,
            item.source_url,
            item.language,
            item.published_at,
            item.access_status,
            item.access_note,
            img,
            ck,
            item_url,
        ),
    )
    row = conn.execute("SELECT id FROM source_items WHERE item_url = ?", (item_url,)).fetchone()
    return int(row["id"])


def generate_content_for_source(conn, source_item_id: int) -> None:
    src = conn.execute("SELECT * FROM source_items WHERE id = ?", (source_item_id,)).fetchone()
    tit_orig = coerce_listing_display_title(
        str(src["title_original"] or ""),
        str(src["item_url"] or ""),
    )
    raw_title = str(src["title_original"] or "").strip()
    if tit_orig != raw_title:
        conn.execute(
            "UPDATE source_items SET title_original = ? WHERE id = ?",
            (tit_orig, source_item_id),
        )
    ck = str(src["content_kind"] or "") if src and "content_kind" in src.keys() else ""
    if ck == "jp_listing" and _env_enabled("SCLAW_FAST_JP_LISTING_CONTENT"):
        fb_hant, fb_hans = _build_listing_zh_fallback(dict(src))
        body_hant = fb_hant or (
            "日本房產案源（列表摘要）\n"
            + str(src["body_original"] or "")[:3600]
            + f"\n來源：{src['item_url'] or ''}"
        )
        body_hans = fb_hans or _simple_hans(body_hant)
        title_hant = f"日本房產案源：{tit_orig}"[:500]
        title_hans = _simple_hans(title_hant)[:500]
        _save_content_item_fast(
            conn,
            source_item_id=source_item_id,
            title_hant=title_hant,
            title_hans=title_hans,
            body_hant=body_hant,
            body_hans=body_hans,
            source_name=str(src["source_name"] or ""),
            keyword_type="case",
            intent_target="房地產",
            topic_category="日本房產案源",
            keyword_tags="日本房產案源,日本買屋,買屋,中古マンション,新築マンション,一戸建て,1R,1K,平屋,駅徒歩",
        )
        _bind_jp_transit_to_content_item(conn, source_item_id=source_item_id, src_row=dict(src))
        return
    if ck == "social_video_knowledge":
        title_hant = tit_orig[:500]
        title_hans = _simple_hans(title_hant)[:500]
        body_hant = str(src["body_original"] or "").strip()
        body_hans = _simple_hans(body_hant)
        classified = {
            "keyword_type": "howto",
            "intent_target": "房地產",
            "topic_category": "社群影片知識",
            "keyword_tags": "TikTok,日本房地產,日本買房,海外置業,日本房產,影片文案,字幕逐字稿",
        }
        region_code = infer_region_code(title_hans, body_hans)
        region_name = {"tw": "台灣", "hk": "香港", "cn": "中國", "sg": "東南亞"}.get(region_code, "全球華人")
        slug = build_slug(region_code, classified["keyword_type"], title_hans)
        seo_title = build_seo_title(title_hant, region_name)
        seo_description = build_seo_description(title_hant, src["source_name"])
        schema_json = build_schema_json(slug, seo_title, seo_description, region_name, body_hant)
        try:
            schema_d = json.loads(schema_json)
            schema_d["@type"] = ["Article", "LearningResource"]
            schema_d["learningResourceType"] = "Social video transcript and real estate knowledge summary"
            schema_d["about"] = ["TikTok 影片知識", "日本房地產", "日本買房", "海外置業", "日本房產仲介"]
            schema_d["video"] = {
                "@type": "VideoObject",
                "name": title_hant,
                "description": seo_description,
                "uploadDate": str(src["published_at"] or datetime.now(timezone.utc).isoformat()),
                "contentUrl": str(src["item_url"] or ""),
                "thumbnailUrl": [x for x in str(src["image_urls"] or "").splitlines() if x.strip()][:1],
            }
            schema_json = json.dumps(schema_d, ensure_ascii=False)
        except Exception:
            pass
        exists = conn.execute("SELECT 1 FROM content_items WHERE source_item_id = ?", (source_item_id,)).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE content_items
                SET title_zh_hant = ?, title_zh_hans = ?, body_zh_hant = ?, body_zh_hans = ?,
                    region_code = ?, keyword_type = ?, intent_target = ?, topic_category = ?, keyword_tags = ?,
                    seo_slug = ?, seo_title = ?, seo_description = ?, schema_json = ?,
                    created_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE source_item_id = ?
                """,
                (
                    title_hant,
                    title_hans,
                    body_hant,
                    body_hans,
                    region_code,
                    classified["keyword_type"],
                    classified["intent_target"],
                    classified["topic_category"],
                    classified["keyword_tags"],
                    slug,
                    seo_title,
                    seo_description,
                    schema_json,
                    source_item_id,
                ),
            )
            return
        conn.execute(
            """
            INSERT INTO content_items (
                source_item_id, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans,
                region_code, keyword_type, intent_target, topic_category, keyword_tags,
                seo_slug, seo_title, seo_description, schema_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                source_item_id,
                title_hant,
                title_hans,
                body_hant,
                body_hans,
                region_code,
                classified["keyword_type"],
                classified["intent_target"],
                classified["topic_category"],
                classified["keyword_tags"],
                slug,
                seo_title,
                seo_description,
                schema_json,
            ),
        )
        return
    title_hant, title_hans = dual_translate(tit_orig)
    body_hant, body_hans = dual_translate(src["body_original"])
    body_hant, body_hans = rewrite_for_originality(body_hant, body_hans, src["source_name"])

    write_item, gate_note = should_write_content_item(
        title_hans=title_hans,
        body_hans=body_hans,
        title_original=tit_orig,
        body_original=str(src["body_original"] or ""),
        content_kind=ck,
        item_url=str(src["item_url"] or ""),
    )
    if not write_item:
        print(
            f"[ingest-gate] SKIP content_items source_item_id={source_item_id} url={src.get('item_url','')!s} :: {gate_note}",
            flush=True,
        )
        return

    classified = classify_content(title_hans, body_hans, src["source_category"])
    if ck == "jp_listing":
        classified = dict(classified)
        classified["topic_category"] = "日本房產案源"
        kt = classified.get("keyword_tags") or ""
        if "日本房產案源" not in kt:
            classified["keyword_tags"] = f"日本房產案源,{kt}".strip(",")[:500]
        # jp_listing 一律優先用結構化中文摘要，避免機翻漂移或日文殘留。
        fb_hant, fb_hans = _build_listing_zh_fallback(dict(src))
        if fb_hant and fb_hans:
            body_hant, body_hans = fb_hant, fb_hans
        elif body_zh_field_is_corrupt_jp_placeholder(body_hant):
            body_hant, body_hans = body_hant, body_hans
    elif ck == "suumo_faq":
        classified = dict(classified)
        classified["intent_target"] = "房地產"
        classified["topic_category"] = "常見問答"
        kt = classified.get("keyword_tags") or ""
        if "常見問答" not in kt:
            classified["keyword_tags"] = f"常見問答,日本買房,{kt}".strip(",")[:500]
    region_code = infer_region_code(title_hans, body_hans)
    region_name = {"tw": "台灣", "hk": "香港", "cn": "中國", "sg": "東南亞"}.get(region_code, "全球華人")
    keyword_type = classified["keyword_type"]
    slug = build_slug(region_code, keyword_type, title_hans)
    seo_title = build_seo_title(title_hant, region_name)
    seo_description = build_seo_description(title_hant, src["source_name"])
    schema_json = build_schema_json(slug, seo_title, seo_description, region_name, body_hant)

    exists = conn.execute("SELECT 1 FROM content_items WHERE source_item_id = ?", (source_item_id,)).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE content_items
            SET title_zh_hant = ?, title_zh_hans = ?, body_zh_hant = ?, body_zh_hans = ?,
                region_code = ?, keyword_type = ?, intent_target = ?, topic_category = ?, keyword_tags = ?,
                seo_slug = ?, seo_title = ?, seo_description = ?, schema_json = ?,
                created_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE source_item_id = ?
            """,
            (
                title_hant,
                title_hans,
                body_hant,
                body_hans,
                region_code,
                keyword_type,
                classified["intent_target"],
                classified["topic_category"],
                classified["keyword_tags"],
                slug,
                seo_title,
                seo_description,
                schema_json,
                source_item_id,
            ),
        )
        if ck == "jp_listing":
            _bind_jp_transit_to_content_item(conn, source_item_id=source_item_id, src_row=dict(src))
        return

    conn.execute(
        """
        INSERT INTO content_items (
            source_item_id, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans,
            region_code, keyword_type, intent_target, topic_category, keyword_tags,
            seo_slug, seo_title, seo_description, schema_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            source_item_id,
            title_hant,
            title_hans,
            body_hant,
            body_hans,
            region_code,
            keyword_type,
            classified["intent_target"],
            classified["topic_category"],
            classified["keyword_tags"],
            slug,
            seo_title,
            seo_description,
            schema_json,
        ),
    )
    if ck == "jp_listing":
        _bind_jp_transit_to_content_item(conn, source_item_id=source_item_id, src_row=dict(src))


def process_crawled_items(items: list) -> int:
    processed = 0
    with get_conn() as conn:
        for item in items:
            source_id = upsert_source_item(conn, item)
            try:
                ck = str(getattr(item, "content_kind", "") or "").strip()
            except Exception:
                ck = ""
            if ck == "jp_listing":
                try:
                    from src.jp_listing_region_index import ensure_jp_listing_region_index_for_item

                    sort_time = str(getattr(item, "published_at", "") or "").strip()
                    ensure_jp_listing_region_index_for_item(
                        conn,
                        source_item_id=int(source_id),
                        item_url=str(getattr(item, "item_url", "") or ""),
                        title_original=str(getattr(item, "title_original", "") or ""),
                        body_original=str(getattr(item, "body_original", "") or ""),
                        sort_time=sort_time,
                    )
                except Exception:
                    pass
            generate_content_for_source(conn, source_id)
            processed += 1
        conn.commit()
    return processed
