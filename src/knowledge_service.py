"""Recent crawled content as bilingual (JA original + ZH) knowledge for RAG / AI."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any
from urllib.parse import urlparse

from src.site_public_config import get_effective_site_url

# 不動產／投資常用主題：排序加權（仍保留使用者 sort_by 作次排序）
_REAL_ESTATE_ORDER_PREFIX = """CASE WHEN (
  c.topic_category LIKE '%房地%' OR c.topic_category LIKE '%不動%' OR c.topic_category LIKE '%貸款%'
  OR c.topic_category LIKE '%稅%' OR c.topic_category LIKE '%市場%' OR c.topic_category LIKE '%投資%'
  OR c.topic_category LIKE '%日本%' OR c.intent_target LIKE '%房地%' OR c.intent_target LIKE '%投資%'
  OR c.keyword_tags LIKE '%不動產%' OR c.keyword_tags LIKE '%日本買%' OR c.keyword_tags LIKE '%房貸%'
  OR c.topic_category LIKE '%購物%' OR c.topic_category LIKE '%生活機能%' OR c.topic_category LIKE '%搬家%'
  OR c.keyword_tags LIKE '%日本購物%' OR c.keyword_tags LIKE '%超市%' OR c.keyword_tags LIKE '%藥妝%'
  OR c.keyword_tags LIKE '%免稅%' OR c.keyword_tags LIKE '%家電%' OR c.keyword_tags LIKE '%商圈%'
) THEN 0 ELSE 1 END, """

# Whitelist ORDER BY fragments (avoid string injection from sort_by)
_ORDER_SQL: dict[str, str] = {
    "crawled_desc": "datetime(s.crawled_at) DESC, c.id DESC",
    "crawled_asc": "datetime(s.crawled_at) ASC, c.id ASC",
    "updated_desc": "datetime(c.updated_at) DESC, c.id DESC",
    "updated_asc": "datetime(c.updated_at) ASC, c.id ASC",
    "topic": "c.topic_category COLLATE NOCASE ASC, c.intent_target COLLATE NOCASE ASC, datetime(s.crawled_at) DESC",
    "title_zh": "c.title_zh_hant COLLATE NOCASE ASC, datetime(s.crawled_at) DESC",
    "content_zh": "length(COALESCE(c.body_zh_hant,'')) DESC, datetime(s.crawled_at) DESC",
}

_SOCIAL_PLATFORM_RULES: tuple[dict[str, Any], ...] = (
    {
        "key": "tiktok",
        "label": "TikTok",
        "logo": "TK",
        "mode": "視頻文案播放",
        "type": "社媒影片",
        "needles": ("tiktok.com", "vt.tiktok.com", "tiktok", "抖音"),
    },
    {
        "key": "instagram",
        "label": "Instagram",
        "logo": "IG",
        "mode": "短影音／圖文閱讀",
        "type": "社媒圖文／短影音",
        "needles": ("instagram.com", "instagr.am", "instagram", "reels", "reel", "ig"),
    },
    {
        "key": "facebook",
        "label": "Facebook",
        "logo": "FB",
        "mode": "社群貼文閱讀",
        "type": "社群貼文",
        "needles": ("facebook.com", "fb.watch", "facebook", "臉書", "脸书"),
    },
    {
        "key": "xiaohongshu",
        "label": "小紅書",
        "logo": "小紅書",
        "mode": "圖文筆記閱讀",
        "type": "社媒圖文",
        "needles": ("xiaohongshu.com", "xhslink.com", "xhs", "rednote", "小紅書", "小红书"),
    },
)

_SEVEN_SOURCE_RULES: tuple[dict[str, Any], ...] = (
    {"key": "suumo", "label": "SUUMO", "logo": "SUUMO", "needles": ("suumo.jp", "suumo")},
    {"key": "homes", "label": "HOME'S", "logo": "HOME'S", "needles": ("homes.co.jp", "homes.jp", "home's", "lifull")},
    {"key": "athome", "label": "AtHome", "logo": "AtHome", "needles": ("athome.co.jp", "at home", "athome", "アットホーム")},
    {"key": "yahoo", "label": "Yahoo不動產", "logo": "Yahoo", "needles": ("realestate.yahoo.co.jp", "yahoo")},
    {"key": "rakuten", "label": "樂天不動產", "logo": "Rakuten", "needles": ("realestate.rakuten.co.jp", "rakuten", "楽天")},
    {"key": "yes", "label": "YES不動產", "logo": "YES", "needles": ("yes1.co.jp", "yes-station.jp", "yes")},
    {"key": "oheyasu", "label": "OHEYASU", "logo": "OHEYASU", "needles": ("oheya-su.jp", "oheyasuu.com", "oheyasu")},
)

_BUYING_KNOWLEDGE_RE = re.compile(
    r"(購房|购房|買房|买房|購屋|购屋|買屋|买屋|日本房地產|日本房产|日本房產|不動產|不动产|"
    r"置業|置业|貸款|贷款|房貸|房贷|稅|税|持有成本|斡旋|契約|签约|簽約|流程|投資|投资|移居)",
    re.I,
)


def _row_text_blob(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in (
            "source_name",
            "item_url",
            "content_kind",
            "topic_category",
            "intent_target",
            "keyword_tags",
            "title_ja",
            "title_original",
            "title_zh_hant",
            "title_zh_hans",
        )
    ).lower()


def _platform_match(blob: str, rules: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    for rule in rules:
        if any(str(n).lower() in blob for n in rule.get("needles", ())):
            return rule
    return None


def _host_from_row(row: dict[str, Any]) -> str:
    raw = str(row.get("item_url") or "").strip()
    if not raw:
        return ""
    try:
        return (urlparse(raw).netloc or "").lower()
    except Exception:
        return ""


def _is_case_content(row: dict[str, Any]) -> bool:
    content_kind = str(row.get("content_kind") or "").strip().lower()
    if content_kind == "jp_listing":
        return True
    topic = str(row.get("topic_category") or "")
    if any(x in topic for x in ("案源", "物件", "房源", "案件")):
        return True
    slug = str(row.get("seo_slug") or "")
    return slug.startswith("case-")


def knowledge_source_meta(row: dict[str, Any]) -> dict[str, Any]:
    """Classify a knowledge row for UI badges and LLM prompt hints."""
    blob = _row_text_blob(row)
    content_kind = str(row.get("content_kind") or "").strip().lower()
    case_content = _is_case_content(row)
    social_rule = _platform_match(blob, _SOCIAL_PLATFORM_RULES)
    seven_rule = _platform_match(blob, _SEVEN_SOURCE_RULES)

    if social_rule:
        platform_key = str(social_rule["key"])
        platform_label = str(social_rule["label"])
        platform_logo = str(social_rule["logo"])
        reading_mode = str(social_rule["mode"])
        content_type = str(social_rule["type"])
        source_badge = "社媒"
    elif seven_rule:
        platform_key = str(seven_rule["key"])
        platform_label = str(seven_rule["label"])
        platform_logo = str(seven_rule["logo"])
        reading_mode = "七大來源翻譯"
        content_type = "物件資料" if case_content else "來源翻譯"
        source_badge = "七大來源"
    elif content_kind == "social_video_knowledge":
        platform_key = "social"
        platform_label = "社媒"
        platform_logo = "SOC"
        reading_mode = "視頻文案播放"
        content_type = "社媒影片"
        source_badge = "社媒"
    else:
        platform_key = "site"
        platform_label = str(row.get("source_name") or _host_from_row(row) or "站內")
        platform_logo = "站內"
        reading_mode = "知識摘要閱讀"
        content_type = "站內知識"
        source_badge = "知識"

    if content_kind == "social_video_knowledge" and "影片" not in content_type and "短影音" not in content_type:
        content_type = "社媒影片"
    if content_kind == "social_video_knowledge" and "視頻" not in reading_mode and "短影音" not in reading_mode:
        reading_mode = "視頻文案播放"

    return {
        "platform_key": platform_key,
        "platform_label": platform_label,
        "platform_logo": platform_logo,
        "reading_mode": reading_mode,
        "content_type": content_type,
        "source_badge": source_badge,
        "content_badge": "物件" if case_content else "",
        "is_case_content": bool(case_content),
    }


def _parse_media_url_list(value: Any, *, max_items: int = 3) -> list[str]:
    if value is None:
        return []
    raw_items: list[Any] = []
    if isinstance(value, (list, tuple)):
        raw_items = list(value)
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                loaded = json.loads(s)
                if isinstance(loaded, list):
                    raw_items = loaded
                else:
                    raw_items = [loaded]
            except Exception:
                raw_items = re.split(r"[\n\r,]+", s)
        else:
            raw_items = re.split(r"[\n\r,]+", s)
    else:
        raw_items = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        u = str(item or "").strip().strip('"').strip("'")
        if not u or u in seen:
            continue
        if not re.match(r"^https?://", u, re.I):
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= max_items:
            break
    return out


def _knowledge_order_prefix(q_clean: str) -> str:
    """Prioritize social readings + seven-source property knowledge for buying summaries."""
    wants_buying_knowledge = bool(_BUYING_KNOWLEDGE_RE.search(q_clean or ""))
    wants_property = bool(re.search(r"(案件|案源|物件|房源|listing|case|中古|新築|新建|一戶建|公寓|マンション|戸建)", q_clean, re.I))
    if wants_property:
        return (
            "CASE "
            "WHEN COALESCE(NULLIF(TRIM(s.content_kind), ''), '') = 'jp_listing' THEN 0 "
            "WHEN COALESCE(NULLIF(TRIM(s.content_kind), ''), '') = 'social_video_knowledge' THEN 1 "
            "WHEN lower(COALESCE(s.item_url,'')) LIKE '%suumo.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%homes.co.jp%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%homes.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%athome.co.jp%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%realestate.yahoo.co.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%realestate.rakuten.co.jp%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%yes1.co.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%oheya-su.jp%' THEN 2 "
            "ELSE 3 END, "
        )
    if wants_buying_knowledge or q_clean:
        return (
            "CASE "
            "WHEN COALESCE(NULLIF(TRIM(s.content_kind), ''), '') = 'social_video_knowledge' THEN 0 "
            "WHEN lower(COALESCE(s.item_url,'')) LIKE '%tiktok.com%' OR lower(COALESCE(s.item_url,'')) LIKE '%instagram.com%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%facebook.com%' OR lower(COALESCE(s.item_url,'')) LIKE '%xiaohongshu.com%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%xhslink.com%' THEN 1 "
            "WHEN lower(COALESCE(s.item_url,'')) LIKE '%suumo.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%homes.co.jp%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%homes.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%athome.co.jp%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%realestate.yahoo.co.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%realestate.rakuten.co.jp%' "
            "OR lower(COALESCE(s.item_url,'')) LIKE '%yes1.co.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%oheya-su.jp%' THEN 2 "
            "ELSE 3 END, "
        )
    return ""


def fetch_knowledge_snippets(
    *,
    days: int = 15,
    q: str = "",
    limit: int = 16,
    sort_by: str = "crawled_desc",
    prefer_real_estate: bool = True,
    query_timeout_ms: int | None = None,
) -> list[dict[str, Any]]:
    """
    Pull `source_items` joined with `content_items` in a time window on `crawled_at`.
    Includes Japanese title/body excerpt and both zh-Hant / zh-Hans excerpts for RAG.
    When ``prefer_real_estate`` is True, rows whose taxonomy looks like property / tax /
    loan topics are ranked earlier (within the same sort window).
    """
    from src.db import get_conn

    lim = max(1, min(200, int(limit)))
    d = max(1, min(186, int(days)))
    date_mod = f"-{d} days"
    params: list[Any] = [date_mod]

    q_clean = re.sub(r"\s+", " ", (q or "").strip())
    where_extra = ""
    if q_clean:
        terms = [q_clean]
        for tok in re.split(r"[\s,，、/｜|]+", q_clean):
            tok = tok.strip()
            if len(tok) >= 2 and tok not in terms:
                terms.append(tok)
            low_tok = tok.lower()
            platform_synonyms = {
                "tk": ["TikTok", "tiktok", "抖音", "social_video_knowledge"],
                "tiktok": ["TikTok", "抖音", "social_video_knowledge"],
                "ig": ["Instagram", "instagram"],
                "fb": ["Facebook", "facebook", "臉書", "脸书"],
                "xhs": ["小紅書", "小红书", "xiaohongshu", "xhslink"],
                "小紅書": ["xiaohongshu", "xhslink"],
                "小红书": ["xiaohongshu", "xhslink"],
                "社媒": ["social_video_knowledge", "TikTok", "Instagram", "Facebook", "小紅書"],
            }.get(low_tok, [])
            for syn in platform_synonyms:
                if syn not in terms:
                    terms.append(syn)
            if len(terms) >= 8:
                break
        term_sql: list[str] = []
        for term in terms:
            like = f"%{term}%"
            term_sql.append(
                "(s.title_original LIKE ? OR c.title_zh_hant LIKE ? OR c.title_zh_hans LIKE ? "
                "OR c.body_zh_hant LIKE ? OR c.body_zh_hans LIKE ? "
                "OR c.topic_category LIKE ? OR c.intent_target LIKE ? OR c.keyword_tags LIKE ? "
                "OR s.source_name LIKE ? OR s.item_url LIKE ? OR s.content_kind LIKE ?)"
            )
            params.extend([like, like, like, like, like, like, like, like, like, like, like])
        where_extra = " AND (" + " OR ".join(term_sql) + ")"

    base_order = _ORDER_SQL.get((sort_by or "crawled_desc").strip().lower(), _ORDER_SQL["crawled_desc"])
    source_mode_boost = _knowledge_order_prefix(q_clean) if prefer_real_estate else ""
    order_sql = source_mode_boost + ((_REAL_ESTATE_ORDER_PREFIX + base_order) if prefer_real_estate else base_order)
    params.append(lim)

    with get_conn() as conn:
        deadline = 0.0
        if query_timeout_ms is not None and int(query_timeout_ms) > 0:
            deadline = time.monotonic() + max(0.05, int(query_timeout_ms) / 1000.0)
            # RAG is supplemental.  A large LIKE + CASE sort must never hold
            # up a visitor chat request or a real listing lookup.
            conn.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 4000)
        try:
            rows = conn.execute(
                f"""
            SELECT
              c.id AS content_id,
              s.id AS source_item_id,
              s.title_original AS title_ja,
              c.title_zh_hant,
              c.title_zh_hans,
              c.seo_slug,
              c.keyword_tags,
              c.topic_category,
              c.intent_target,
              substr(COALESCE(s.body_original, ''), 1, 500) AS body_ja_excerpt,
              substr(COALESCE(c.body_zh_hant, ''), 1, 500) AS body_zh_hant_excerpt,
              substr(COALESCE(c.body_zh_hans, ''), 1, 500) AS body_zh_hans_excerpt,
              s.item_url,
              s.source_name,
              COALESCE(NULLIF(TRIM(s.content_kind), ''), '') AS content_kind,
              COALESCE(s.image_urls, '') AS image_urls,
              s.crawled_at,
              c.updated_at
            FROM content_items c
            JOIN source_items s ON s.id = c.source_item_id
            WHERE datetime(s.crawled_at) >= datetime('now', ?)
            {where_extra}
            ORDER BY {order_sql}
            LIMIT ?
            """,
                params,
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if deadline and "interrupted" in str(exc).lower():
                return []
            raise
        finally:
            if deadline:
                conn.set_progress_handler(None, 0)
    return [dict(r) for r in rows]


def _kb_dedupe_key(row: dict[str, Any]) -> tuple[str, Any]:
    cid = row.get("content_id")
    if cid is not None and str(cid).strip() != "":
        try:
            return ("id", int(cid))
        except (TypeError, ValueError):
            return ("id", str(cid))
    return ("url", str(row.get("item_url") or ""))


def fetch_knowledge_for_chat(
    *,
    knowledge_query: str,
    days: int = 120,
    keyword_limit: int = 40,
    recent_limit: int = 40,
    merged_max: int = 56,
    query_timeout_ms: int | None = None,
) -> list[dict[str, Any]]:
    """
    For RAG: keyword-scoped hits first (all enabled sources in DB), then fill with
    recent rows in the same time window so every crawl source can surface even
    when the user question does not literal-match stored text.
    """
    kq = re.sub(r"\s+", " ", (knowledge_query or "").strip())
    matched: list[dict[str, Any]] = []
    if kq:
        matched = fetch_knowledge_snippets(
            days=days,
            q=kq,
            limit=max(1, min(200, keyword_limit)),
            sort_by="crawled_desc",
            prefer_real_estate=True,
            query_timeout_ms=query_timeout_ms,
        )
    recent = fetch_knowledge_snippets(
        days=days,
        q="",
        limit=max(1, min(200, recent_limit)),
        sort_by="crawled_desc",
        prefer_real_estate=True,
        query_timeout_ms=query_timeout_ms,
    )
    cap = max(1, min(120, int(merged_max)))
    seen: set[tuple[str, Any]] = set()
    out: list[dict[str, Any]] = []

    for row in matched:
        k = _kb_dedupe_key(row)
        if k in seen:
            continue
        seen.add(k)
        rr = dict(row)
        rr["kb_hit"] = "keyword"
        out.append(rr)
        if len(out) >= cap:
            return out

    for row in recent:
        k = _kb_dedupe_key(row)
        if k in seen:
            continue
        seen.add(k)
        rr = dict(row)
        rr["kb_hit"] = "recent"
        out.append(rr)
        if len(out) >= cap:
            break
    return out


def pick_article_title_for_ui(row: dict[str, Any], zh_variant: str = "hans") -> str:
    """Prefer 简体 for dialog / list titles; optional 繁体 or both."""
    v = (zh_variant or "hans").strip().lower()
    if v not in ("hans", "hant", "both"):
        v = "hans"
    hant = str(row.get("title_zh_hant") or row.get("title") or "").strip()
    hans = str(row.get("title_zh_hans") or "").strip()
    seo = str(row.get("seo_title") or "").strip()
    if v == "hant":
        return (seo or hant or hans)[:500]
    if v == "both":
        base = (hans or hant)[:400]
        if hans and hant and hans != hant:
            return f"{base}（繁：{hant[:180]}）"[:500]
        return (seo or base)[:500]
    return (seo or hans or hant)[:500]


def _pick_zh_line(hans: str, hant: str, variant: str, label_hans: str, label_hant: str) -> str:
    hans = (hans or "").strip()
    hant = (hant or "").strip()
    if variant == "hant":
        body = hant or hans
        if hant and hans and hant != hans:
            return f"{label_hant}:{body}\n（简：{hans}）"
        return f"{label_hant}:{body}"
    if variant == "both":
        if hans and hant and hans != hant:
            return f"{label_hans}:{hans}\n{label_hant}:{hant}"
        return f"{label_hans}:{hans or hant}"
    body = hans or hant
    if hans and hant and hans != hant:
        return f"{label_hans}:{body}\n（繁：{hant}）"
    return f"{label_hans}:{body}"


def knowledge_items_for_api(
    rows: list[dict[str, Any]],
    *,
    zh_variant: str = "hans",
) -> list[dict[str, Any]]:
    """Slim, JSON-safe rows for UI (no long body excerpts)."""
    v = (zh_variant or "hans").strip().lower()
    if v not in ("hans", "hant", "both"):
        v = "hans"
    out: list[dict[str, Any]] = []
    for r in rows:
        hit = str(r.get("kb_hit") or "recent")
        mt = "keyword" if hit == "keyword" else "recent"
        hant_t = str(r.get("title_zh_hant") or "")[:400]
        hans_t = str(r.get("title_zh_hans") or "")[:400]
        hant_b = str(r.get("body_zh_hant_excerpt") or r.get("body_zh_hant") or "")[:420]
        hans_b = str(r.get("body_zh_hans_excerpt") or r.get("body_zh_hans") or "")[:420]
        if v == "hant":
            title_display = (hant_t or hans_t).strip()
            excerpt_display = (hant_b or hans_b).strip()
        elif v == "both":
            if hans_t and hant_t and hans_t.strip() != hant_t.strip():
                title_display = f"{hans_t.strip()}（繁：{hant_t.strip()}）"
            else:
                title_display = (hans_t or hant_t).strip()
            if hans_b and hant_b and hans_b.strip() != hant_b.strip():
                excerpt_display = f"{hans_b.strip()}（繁：{hant_b.strip()}）"
            else:
                excerpt_display = (hans_b or hant_b).strip()
        else:
            title_display = (hans_t or hant_t).strip()
            excerpt_display = (hans_b or hant_b).strip()
        meta = knowledge_source_meta(r)
        thumbs = _parse_media_url_list(r.get("image_urls"), max_items=3)
        slug = str(r.get("seo_slug") or "").strip()
        try:
            sid = int(r.get("source_item_id") or 0)
        except Exception:
            sid = 0
        article_path = ""
        if slug:
            article_path = f"/article/case-{sid}-{slug}" if sid > 0 else f"/article/{slug}"
        out.append(
            {
                "content_id": r.get("content_id"),
                "source_item_id": r.get("source_item_id"),
                "source_name": str(r.get("source_name") or ""),
                "title_ja": str(r.get("title_ja") or "")[:400],
                "title_zh_hant": hant_t,
                "title_zh_hans": hans_t,
                "title_display": title_display[:500],
                "excerpt_display": excerpt_display[:520],
                "item_url": str(r.get("item_url") or ""),
                "article_path": article_path,
                "seo_slug": slug,
                "topic_category": str(r.get("topic_category") or ""),
                "intent_target": str(r.get("intent_target") or ""),
                "keyword_tags": str(r.get("keyword_tags") or ""),
                "content_kind": str(r.get("content_kind") or ""),
                "thumbnail_urls": thumbs,
                "crawled_at": str(r.get("crawled_at") or ""),
                "match_type": mt,
                **meta,
            }
        )
    return out


_SOCIAL_DIGEST_DEFAULT_QUERIES = [
    "日本買房 流程 社媒",
    "外國人 日本 房貸 TikTok 小紅書",
    "日本不動產 稅金 持有成本",
    "日本中古屋 注意事項",
    "東京 不動產 投資 七大來源",
    "日本房產 仲介 房展",
]


def _digest_summary_points(text: str, *, video_text_focus: bool = False, limit: int = 3) -> list[str]:
    raw_text = (text or "").strip()
    if not raw_text:
        return []
    candidates: list[str] = []
    current_section = ""
    for line in re.split(r"[\n\r]+", raw_text):
        s = re.sub(r"\s+", " ", line).strip()
        if not s:
            continue
        section = re.fullmatch(r"[【\[]([^】\]]+)[】\]]", s)
        if section:
            current_section = section.group(1).strip()
            continue
        if re.match(r"^(TikTok|Instagram|Facebook|小紅書|小红书)\s*(影片|圖文|图文|貼文|贴文)?知識來源", s, re.I):
            continue
        if current_section in {"作者", "主題標籤", "主题标签"}:
            continue
        if s.startswith(("- 帳號", "- 账号", "- 名稱", "- 名称", "- 簡介", "- 简介")):
            continue
        candidates.extend(p.strip(" ：:;；,，") for p in re.split(r"[。！？!?]\s*", s) if p.strip(" ：:;；,，"))
    raw = re.sub(r"\s+", " ", raw_text)
    if not raw:
        return []
    parts = candidates or [p.strip(" ：:;；,，") for p in re.split(r"[。！？!?]\s*|\n+", raw) if p.strip(" ：:;；,，")]
    out: list[str] = []
    seen: set[str] = set()
    min_len = 8 if video_text_focus else 3
    for part in parts:
        point = part[:140]
        if len(point) < min_len or point in seen:
            continue
        seen.add(point)
        out.append(point)
        if len(out) >= limit:
            break
    if video_text_focus and out and not re.match(r"^(影片|視頻|视频|字幕|口播|文案)", out[0]):
        out[0] = f"影片文案重點：{out[0]}"
    return out


def _digest_primary_action(meta: dict[str, Any], *, video_text_focus: bool) -> str:
    if video_text_focus:
        return "看影片文案"
    if bool(meta.get("is_case_content")):
        return "看物件資料"
    mode = str(meta.get("reading_mode") or "")
    if "圖文" in mode or "图文" in mode:
        return "閱讀圖文"
    if "翻譯" in mode or "翻译" in mode:
        return "看翻譯重點"
    return "閱讀摘要"


def build_social_knowledge_digest(
    rows: list[dict[str, Any]],
    *,
    zh_variant: str = "hans",
    window_days: int = 15,
    max_items: int = 12,
) -> dict[str, Any]:
    """
    Structured digest for AI 摘要 UI: social video/image posts first, then seven-source
    translated buying knowledge and property rows. It is deterministic so the same
    metadata can feed the API, prompt, and frontend cards.
    """
    win = max(1, min(30, int(window_days or 15)))
    cap = max(1, min(40, int(max_items or 12)))
    api_items = knowledge_items_for_api(list(rows or [])[:cap], zh_variant=zh_variant)

    platform_map: dict[str, dict[str, Any]] = {}
    digest_items: list[dict[str, Any]] = []
    social_count = 0
    seven_source_count = 0
    property_count = 0
    video_count = 0

    for item in api_items:
        platform_key = str(item.get("platform_key") or "site")
        platform_label = str(item.get("platform_label") or item.get("source_name") or "站內")
        platform_logo = str(item.get("platform_logo") or platform_label or "知識")
        reading_mode = str(item.get("reading_mode") or "")
        content_type = str(item.get("content_type") or "")
        source_badge = str(item.get("source_badge") or "").strip()
        content_badge = str(item.get("content_badge") or "").strip()
        content_kind = str(item.get("content_kind") or "").strip().lower()
        is_case = bool(item.get("is_case_content"))
        video_text_focus = (
            content_kind == "social_video_knowledge"
            or "影片" in content_type
            or "視頻" in reading_mode
            or "视频" in reading_mode
            or "短影音" in reading_mode
        )

        if source_badge == "社媒":
            social_count += 1
        if source_badge == "七大來源":
            seven_source_count += 1
        if is_case:
            property_count += 1
        if video_text_focus:
            video_count += 1

        plat = platform_map.setdefault(
            platform_key,
            {
                "key": platform_key,
                "label": platform_label,
                "logo": platform_logo[:8],
                "count": 0,
                "reading_modes": [],
            },
        )
        plat["count"] = int(plat.get("count") or 0) + 1
        modes = plat.setdefault("reading_modes", [])
        if reading_mode and reading_mode not in modes:
            modes.append(reading_mode)

        badges = [b for b in (source_badge, content_badge) if b]
        if is_case and "物件" not in badges:
            badges.append("物件")
        if not badges:
            badges.append("知識")

        points = _digest_summary_points(str(item.get("excerpt_display") or ""), video_text_focus=video_text_focus)
        if not points:
            points = _digest_summary_points(str(item.get("title_display") or ""), video_text_focus=video_text_focus, limit=2)

        digest_items.append(
            {
                **item,
                "summary_points": points,
                "video_text_focus": video_text_focus,
                "property_tag": "物件" if is_case else "",
                "reading_card": {
                    "logo": platform_logo[:8],
                    "platform_label": platform_label,
                    "mode": reading_mode,
                    "content_type": content_type,
                    "badges": badges,
                    "primary_action": _digest_primary_action(
                        {
                            "reading_mode": reading_mode,
                            "is_case_content": is_case,
                        },
                        video_text_focus=video_text_focus,
                    ),
                },
                "media": {
                    "thumbnails": list(item.get("thumbnail_urls") or []),
                    "videos": [],
                },
            }
        )

    missing_social = social_count == 0
    too_few = len(digest_items) < 6
    return {
        "window_days": win,
        "freshness_label": f"近 {win} 天最新資料",
        "summary_title": "近半個月日本購房知識摘要",
        "source_policy": "社媒影片/圖文優先，其次七大來源翻譯與物件資料",
        "items": digest_items,
        "platforms": list(platform_map.values()),
        "counts": {
            "total": len(digest_items),
            "social": social_count,
            "seven_source": seven_source_count,
            "property": property_count,
            "video": video_count,
        },
        "query_suggestions": list(_SOCIAL_DIGEST_DEFAULT_QUERIES),
        "needs_bootstrap": bool(too_few or missing_social),
        "bootstrap_reason": "最近 15 天社媒/七大來源資料不足，建議用預設關鍵字補抓。"
        if (too_few or missing_social)
        else "",
    }


def format_knowledge_for_prompt(
    rows: list[dict[str, Any]],
    max_chars: int = 12000,
    *,
    zh_variant: str = "hans",
) -> str:
    """Build RAG text for LLM: default Simplified Chinese, optional Traditional + source URL + JA excerpt."""
    v = (zh_variant or "hans").strip().lower()
    if v not in ("hans", "hant", "both"):
        v = "hans"
    header = (
        "【摘錄使用規則】以下為站內爬取摘要（含日文原文摘錄＋中文）。"
        "請只採用與「使用者當前查詢意圖」及「日本不動產／自住或投資／稅務／貸款／區域市場／租售與交易流程」同時相關的段落；"
        "與查詢無關的條目、廣告、泛新聞、非房地產主題一律忽略、勿引用。\n"
        "摘要優先級：先採用社媒（TikTok/IG/Facebook/小紅書）中已擷取的影片文案、字幕重點或圖文筆記；"
        "遇到影片時以文字重點整理，不描述空泛畫面；再融合七大來源網站的翻譯購房知識與案件資料。"
        "若條目是具體案件／物件，請明確標示「物件」小標籤，避免把它寫成一般知識。\n"
        "輸出給使用者時預設以簡體中文為主（若使用者明顯為台港澳語境可簡繁並用）；條列宜短、可掃讀。\n\n"
    )
    parts: list[str] = [header]
    total = len(header)
    for i, r in enumerate(rows, 1):
        hans_t = str(r.get("title_zh_hans") or "")
        hant_t = str(r.get("title_zh_hant") or "")
        title_block = _pick_zh_line(hans_t, hant_t, v, "简中标题", "繁中標題")
        hans_b = str(r.get("body_zh_hans_excerpt") or "")
        hant_b = str(r.get("body_zh_hant_excerpt") or "")
        body_block = _pick_zh_line(hans_b, hant_b, v, "简中摘要", "繁中摘要")
        meta = knowledge_source_meta(r)
        block = (
            f"[{i}] 来源:{r.get('source_name','')} | 平台:{meta.get('platform_label','')} | "
            f"閱讀模式:{meta.get('reading_mode','')} | 內容類型:{meta.get('content_type','')} | "
            f"內容標籤:{meta.get('content_badge') or '一般'} | 主题:{r.get('topic_category','')}/{r.get('intent_target','')}\n"
            f"日文标题:{r.get('title_ja','')}\n"
            f"{title_block}\n"
            f"日文摘录:{r.get('body_ja_excerpt','')}\n"
            f"{body_block}\n"
            f"来源网址:{r.get('item_url','')}\n"
        )
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def fetch_featured_cases_for_chat(*, limit: int = 8) -> list[dict[str, Any]]:
    """後台標示之重點推薦案件（依 featured_weight 由高到低）。"""
    from src.db import get_conn

    lim = max(1, min(24, int(limit)))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              c.id AS content_id,
              c.title_zh_hant,
              c.title_zh_hans,
              c.featured_weight,
              c.seo_slug,
              s.item_url,
              s.source_name
            FROM content_items c
            JOIN source_items s ON s.id = c.source_item_id
            WHERE COALESCE(c.featured_weight, 0) > 0
            ORDER BY c.featured_weight DESC, datetime(c.updated_at) DESC, c.id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    return [dict(r) for r in rows]


def format_featured_for_prompt(
    rows: list[dict[str, Any]],
    *,
    zh_variant: str = "hans",
    max_chars: int = 2800,
) -> str:
    """供 LLM：主動條列重點推薦並保留權重數字。"""
    if not rows:
        return ""
    v = (zh_variant or "hans").strip().lower()
    if v not in ("hans", "hant", "both"):
        v = "hans"
    header = (
        "【站內重點推薦】以下條目為站內營運標示的優先推薦（數字為推薦權重，愈高愈優先）。"
        "請在回覆中適度主動提出 1～3 則與使用者問題相關的推薦，並明確寫出「權重」數字；"
        "若與提問無關則可略過。每則請附來源網址。\n\n"
    )
    parts: list[str] = [header]
    total = len(header)
    for i, r in enumerate(rows, 1):
        w = int(r.get("featured_weight") or 0)
        hant_t = str(r.get("title_zh_hant") or "").strip()
        hans_t = str(r.get("title_zh_hans") or "").strip()
        if v == "hant":
            title = (hant_t or hans_t)[:400]
        elif v == "both" and hant_t and hans_t and hant_t != hans_t:
            title = f"{hans_t[:320]}（繁：{hant_t[:200]}）"
        else:
            title = (hans_t or hant_t)[:400]
        block = (
            f"[推薦{i}] 權重:{w}\n"
            f"標題:{title}\n"
            f"來源:{r.get('source_name','')}\n"
            f"網址:{r.get('item_url','')}\n"
        )
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def featured_items_for_api(
    rows: list[dict[str, Any]],
    *,
    zh_variant: str = "hans",
) -> list[dict[str, Any]]:
    v = (zh_variant or "hans").strip().lower()
    if v not in ("hans", "hant", "both"):
        v = "hans"
    out: list[dict[str, Any]] = []
    for r in rows:
        hant_t = str(r.get("title_zh_hant") or "")[:400]
        hans_t = str(r.get("title_zh_hans") or "")[:400]
        if v == "hant":
            title_display = (hant_t or hans_t).strip()
        elif v == "both" and hans_t and hant_t and hans_t.strip() != hant_t.strip():
            title_display = f"{hans_t.strip()}（繁：{hant_t.strip()}）"
        else:
            title_display = (hans_t or hant_t).strip()
        out.append(
            {
                "content_id": r.get("content_id"),
                "featured_weight": int(r.get("featured_weight") or 0),
                "title_display": title_display[:500],
                "item_url": str(r.get("item_url") or ""),
                "source_name": str(r.get("source_name") or ""),
                "seo_slug": str(r.get("seo_slug") or ""),
            }
        )
    return out


_PROPERTY_LISTING_KEYS = (
    "物件",
    "房源",
    "屋",
    "買房",
    "賣房",
    "購屋",
    "租屋",
    "租賃",
    "租金",
    "投資",
    "公寓",
    "套房",
    "一戶建",
    "透天",
    "車站",
    "徒步",
    "步行",
    "不動產",
    "日本房",
    "chintai",
    "suumo",
    "homes",
    "athome",
    "マンション",
    "戸建",
    "賃貸",
    "売買",
    "坪",
    "日元",
    "日圓",
    "斡旋",
    "看屋",
)


def message_signals_property_listing(q: str) -> bool:
    """使用者是否在問「商品房／物件／買賣租」等可對應案件管理的情境。"""
    s = (q or "").strip()
    if not s:
        return False
    low = s.lower()
    for k in _PROPERTY_LISTING_KEYS:
        if k.lower() in low or k in s:
            return True
    return False


def transaction_hint_from_message(q: str) -> str:
    """從訊息粗分買賣租，供案件 SQL 篩選；無則空字串。"""
    s = (q or "").strip()
    if not s:
        return ""
    if any(x in s for x in ("賣房", "釋出", "出售", "急售", "売却", "讓售")):
        return "sell"
    if any(x in s for x in ("租屋", "租賃", "租金", "月租", "租客", "chintai", "賃貸")):
        return "rent"
    if any(x in s for x in ("買房", "購屋", "買入", "置產", "下斡旋", "中古", "新建")):
        return "buy"
    return ""


def fetch_managed_property_cases_for_chat(
    *,
    knowledge_query: str,
    limit: int = 6,
    transaction_hint: str = "",
) -> list[dict[str, Any]]:
    """
    站內「案件管理」型資料：個別物件／案源（jp_listing、案源主題或已設權重），
    並以查詢詞比對標題／內文／覆寫欄位；供智能客服優先於一般摘錄。
    """
    from src.case_metadata import transaction_sql_clause
    from src.db import get_conn

    kq = re.sub(r"\s+", " ", (knowledge_query or "").strip())
    lim = max(1, min(12, int(limit)))
    tx_sql, tx_params = transaction_sql_clause((transaction_hint or "").strip().lower())
    listing_clause = (
        "("
        "COALESCE(NULLIF(TRIM(s.content_kind), ''), '') = 'jp_listing' "
        "OR c.topic_category LIKE '%日本房產案源%' OR c.topic_category LIKE '%案源%' "
        "OR COALESCE(c.featured_weight, 0) > 0"
        ")"
    )
    parts: list[str] = [listing_clause, f"({tx_sql})"]
    params: list[Any] = list(tx_params)
    if kq:
        like = f"%{kq}%"
        parts.append(
            "("
            "s.title_original LIKE ? OR c.title_zh_hant LIKE ? OR c.title_zh_hans LIKE ? "
            "OR c.body_zh_hant LIKE ? OR c.body_zh_hans LIKE ? OR s.item_url LIKE ? "
            "OR COALESCE(c.case_jp_region_override, '') LIKE ? "
            "OR COALESCE(c.case_transit_override, '') LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like, like, like])
    where_sql = " AND ".join(parts)
    params.append(lim)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT
              c.id AS content_id,
              c.title_zh_hant,
              c.title_zh_hans,
              c.seo_slug,
              c.seo_title,
              substr(COALESCE(c.body_zh_hant, ''), 1, 900) AS body_zh_hant,
              substr(COALESCE(c.body_zh_hans, ''), 1, 900) AS body_zh_hans,
              s.title_original,
              substr(COALESCE(s.body_original, ''), 1, 1200) AS body_original,
              s.item_url,
              s.source_name,
              COALESCE(NULLIF(TRIM(s.content_kind), ''), '') AS content_kind,
              COALESCE(c.featured_weight, 0) AS featured_weight,
              c.topic_category,
              COALESCE(c.case_transaction_override, '') AS case_transaction_override,
              COALESCE(c.case_jp_region_override, '') AS case_jp_region_override,
              COALESCE(c.case_transit_override, '') AS case_transit_override,
              c.region_code
            FROM content_items c
            JOIN source_items s ON s.id = c.source_item_id
            WHERE {where_sql}
            ORDER BY COALESCE(c.featured_weight, 0) DESC, datetime(c.updated_at) DESC, c.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def format_managed_cases_for_prompt(
    rows: list[dict[str, Any]],
    *,
    site_url: str,
    zh_variant: str = "hans",
    max_chars: int = 3600,
) -> str:
    """RAG 首段：本站案件＋站內文章 URL＋來源物件頁（必須可點、有來源）。"""
    if not rows:
        return ""
    from src.case_metadata import infer_case_metadata, transaction_label_zh

    base = (site_url or "").strip().rstrip("/")
    v = (zh_variant or "hans").strip().lower()
    if v not in ("hans", "hant", "both"):
        v = "hans"
    header = (
        "【本站案件商品（案件管理，優先）】以下為站內已入庫之個別物件／案源，與查詢或買賣租語意相關；"
        "每則含「站內文章頁」與「來源官方物件頁」。請優先用口語、自然私訊語氣介紹 1～3 則最相關者，並鼓勵客戶點連結；"
        "若本段為空再依後段一般摘錄回答。禁止捏造不存在的連結。\n\n"
    )
    parts: list[str] = [header]
    total = len(header)
    for i, r in enumerate(rows, 1):
        meta = infer_case_metadata(r)
        side = str(meta.get("transaction_side") or "unknown")
        tx_zh = transaction_label_zh(side)
        hant_t = str(r.get("title_zh_hant") or "").strip()
        hans_t = str(r.get("title_zh_hans") or "").strip()
        if v == "hant":
            title = (hant_t or hans_t)[:400]
        elif v == "both" and hans_t and hant_t and hant_t != hans_t:
            title = f"{hans_t[:320]}（繁：{hant_t[:200]}）"
        else:
            title = (hans_t or hant_t)[:400]
        slug = str(r.get("seo_slug") or "").strip()
        article = f"{base}/article/{slug}" if slug else ""
        item_u = str(r.get("item_url") or "").strip()
        reg = str(meta.get("jp_region_display_zh") or "").strip()
        tr = str(meta.get("transit_line_zh") or "").strip()
        w = int(r.get("featured_weight") or 0)
        block = (
            f"[案件{i}] 買賣租:{tx_zh}｜日本區域:{reg or '—'}｜交通:{tr or '—'}｜權重:{w}\n"
            f"標題:{title}\n"
            f"來源:{r.get('source_name','')}\n"
            f"站內文章:{article or '（無 slug，請僅用來源網址）'}\n"
            f"來源物件頁:{item_u}\n"
        )
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def managed_items_for_api(
    rows: list[dict[str, Any]],
    *,
    zh_variant: str = "hans",
) -> list[dict[str, Any]]:
    """供前端摺疊區：本站案件優先列表。"""
    from src.case_metadata import infer_case_metadata

    v = (zh_variant or "hans").strip().lower()
    if v not in ("hans", "hant", "both"):
        v = "hans"
    out: list[dict[str, Any]] = []
    for r in rows:
        meta = infer_case_metadata(r)
        hant_t = str(r.get("title_zh_hant") or "")[:400]
        hans_t = str(r.get("title_zh_hans") or "")[:400]
        if v == "hant":
            title_display = (hant_t or hans_t).strip()
        elif v == "both" and hans_t and hant_t and hant_t.strip() != hant_t.strip():
            title_display = f"{hans_t.strip()}（繁：{hant_t.strip()}）"
        else:
            title_display = (hans_t or hant_t).strip()
        slug = str(r.get("seo_slug") or "").strip()
        base = (get_effective_site_url() or "").strip().rstrip("/")
        article_url = f"{base}/article/{slug}" if (base and slug) else ""
        out.append(
            {
                "content_id": r.get("content_id"),
                "featured_weight": int(r.get("featured_weight") or 0),
                "title_display": title_display[:500],
                "item_url": str(r.get("item_url") or ""),
                "source_name": str(r.get("source_name") or ""),
                "seo_slug": slug,
                "article_url": article_url,
                "transaction_label_zh": str(meta.get("transaction_label_zh") or ""),
                "jp_region_display_zh": str(meta.get("jp_region_display_zh") or ""),
                "transit_line_zh": str(meta.get("transit_line_zh") or ""),
            }
        )
    return out
