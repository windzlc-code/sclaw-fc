import re
import os
from urllib.parse import urljoin, urlparse

import httpx

from src.bsoup import soup_from_html
from src.db import get_conn
from src.text_utils import dual_translate

try:
    from opencc import OpenCC

    _cc_t2s = OpenCC("t2s")
except Exception:  # pragma: no cover
    _cc_t2s = None

SOURCE_PORTALS = [
    {
        "id": "suumo",
        "name": "SUUMO",
        "url": "https://suumo.jp/kanto/",
        "note": "市場趨勢、租金行情、地區分析",
        "icon": "https://suumo.jp/favicon.ico",
    },
    {
        "id": "homes",
        "name": "HOMES",
        "url": "https://www.homes.co.jp/",
        "note": "地區與物件趨勢",
        "icon": "https://www.homes.co.jp/favicon.ico",
    },
    {
        "id": "athome",
        "name": "AtHome",
        "url": "https://www.athome.co.jp/",
        "note": "區域行情與價格觀察",
        "icon": "https://www.athome.co.jp/favicon.ico",
    },
]

RENT_WORDS = ["賃貸", "借りる", "賃貸物件", "家賃", "chintai", "rent"]
BUY_WORDS = ["買う", "新築", "中古", "一戸建", "マンション", "土地", "注文住宅", "売買", "購入"]


def _strip_media_noise(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"\[(?:財產調查圖片網址|物件參考圖像\s*URL)\]?\s*", " ", s, flags=re.IGNORECASE)
    # 兼容完整與截斷版（resizeImage / resizeIm / resize...）
    s = re.sub(r"https?://img\d*\.suumo\.com/jj/resize[a-z]*[^\s\]\)'\"]*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"src\s*=\s*[^\s\]\)'\"]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"gazo%2F[^\s\]\)'\"]*", " ", s, flags=re.IGNORECASE)
    return s


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_media_noise(text)).strip()


def _decode(resp: httpx.Response) -> str:
    raw = resp.content
    for enc in [resp.encoding, "utf-8", "cp932", "euc_jp", "shift_jis"]:
        if not enc:
            continue
        try:
            return raw.decode(enc, errors="strict")
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore")


def _classify_tab(text: str) -> str:
    t = (text or "").lower()
    if any(x.lower() in t for x in RENT_WORDS):
        return "rent"
    if any(x.lower() in t for x in BUY_WORDS):
        return "buy"
    return "buy"


def _should_translate_keyword(query: str) -> bool:
    text = query or ""
    if re.search(r"[\u3040-\u30ff]", text):
        return True
    return any(
        x in text for x in ["関東", "不動産", "賃貸", "マンション", "一戸建", "新築", "中古", "土地", "買う", "借りる"]
    )


def _score_link(query: str, title: str, snippet: str, href: str) -> int:
    score = 0
    q = (query or "").strip().lower()
    t = (title or "").lower()
    s = (snippet or "").lower()
    h = (href or "").lower()
    if q and (q in t or q in s):
        score += 8
    if any(x.lower() in t for x in RENT_WORDS + BUY_WORDS):
        score += 3
    if any(x in h for x in ["/chintai/", "/ms/", "/ikkodate/", "/tochi/", "/mansion", "/rent"]):
        score += 2
    if "news" in h or "journal" in h:
        score += 1
    return score


def _extract_from_source(client: httpx.Client, source: dict, query: str, limit: int = 6) -> list[dict]:
    from src.link_quality import url_is_low_value_for_link_list

    resp = client.get(source["url"])
    resp.raise_for_status()
    html = _decode(resp)
    soup = soup_from_html(html)
    host = urlparse(source["url"]).netloc

    rows = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        full = urljoin(source["url"], href)
        if url_is_low_value_for_link_list(full):
            continue
        parsed = urlparse(full)
        if host not in parsed.netloc:
            continue
        title_jp = _clean(a.get_text(" "))
        if len(title_jp) < 3:
            continue
        parent_text = _clean(a.parent.get_text(" ")) if a.parent else ""
        snippet_jp = parent_text[:180]
        score = _score_link(query=query, title=title_jp, snippet=snippet_jp, href=full)
        rows.append({"title_jp": title_jp, "snippet_jp": snippet_jp, "url": full, "score": score})

    # Deduplicate by URL and keep highest score.
    best_by_url: dict[str, dict] = {}
    for row in rows:
        key = row["url"]
        old = best_by_url.get(key)
        if not old or row["score"] > old["score"]:
            best_by_url[key] = row
    sorted_rows = sorted(best_by_url.values(), key=lambda x: x["score"], reverse=True)
    return sorted_rows[:limit]


def _fallback_rows(source: dict) -> list[dict]:
    seed_titles = {
        "suumo": [
            ("關東住宅市場入口", "關東區域的租賃、買賣與建屋資訊入口。", "https://suumo.jp/kanto/"),
            ("賃貸與新築專區", "可快速切換租賃、新築、中古等分類。", "https://suumo.jp/"),
        ],
        "homes": [
            ("LIFULL HOME'S 首頁", "提供地區與物件趨勢查詢入口。", "https://www.homes.co.jp/"),
            ("買屋與租屋分類", "可從地區、路線、地圖進行查詢。", "https://www.homes.co.jp/chintai/"),
        ],
        "athome": [
            ("AtHome 首頁", "提供區域行情與價格觀察入口。", "https://www.athome.co.jp/"),
            ("租屋查詢入口", "支援租屋、買屋、土地等分類。", "https://www.athome.co.jp/chintai/"),
        ],
    }
    rows = []
    for title, snippet, url in seed_titles.get(source["id"], []):
        rows.append({"title_jp": title, "snippet_jp": snippet, "url": url, "score": 1})
    return rows


def _build_conclusion(count_buy: int, count_rent: int, keyword_hant: str) -> str:
    if count_buy == 0 and count_rent == 0:
        return "目前未命中明確條目，建議改用更短關鍵字（例：賃貸、マンション、新築）重新查詢。"
    if count_buy >= count_rent:
        return (
            f"關鍵字「{keyword_hant}」目前以買賣相關資訊為主，建議先看新築/中古/土地分類，再比對區域價格與供給量。"
        )
    return (
        f"關鍵字「{keyword_hant}」目前以租賃相關資訊為主，建議先看家賃帶與通勤條件，再交叉比對不同平台物件差異。"
    )


def _classify_tab_by_record(title: str, snippet: str, url: str) -> str:
    text = f"{title} {snippet} {url}".lower()
    if any(x.lower() in text for x in RENT_WORDS):
        return "rent"
    if any(x.lower() in text for x in BUY_WORDS):
        return "buy"
    return "buy"


def _search_db_market_records(keyword: str, collect_limit: int = 10000, display_limit: int = 400) -> dict | None:
    from src.link_quality import url_is_low_value_for_link_list

    q = (keyword or "").strip()
    terms = [x.strip() for x in re.split(r"[\s\u3000]+", q) if x.strip()]
    if q and not terms:
        terms = [q]

    def _fts5_quote_term(term: str) -> str:
        t = str(term or "").strip()
        if not t:
            return ""
        return '"' + t.replace('"', '""') + '"'

    def _fts5_or_query(tokens: list[str]) -> str:
        parts = []
        for tok in tokens:
            qt = _fts5_quote_term(tok)
            if qt:
                parts.append(qt)
        return " OR ".join(parts)

    fts_q = _fts5_or_query(terms[:10]) if q else ""
    with get_conn() as conn:
        if q and not fts_q:
            rows = []
        elif q and fts_q:
            rowid_floor = 0
            try:
                mx = conn.execute("SELECT MAX(id) FROM source_items").fetchone()
                max_rowid = int((mx[0] if mx else 0) or 0)
                # Keep interactive lookups snappy by limiting the FTS scan window.
                window = min(220000, max(60000, int(collect_limit) * 8))
                if max_rowid > 0:
                    rowid_floor = max(0, max_rowid - window)
            except Exception:
                rowid_floor = 0

            cand_limit = min(20000, max(2400, int(collect_limit) * 6))
            sub_sql = "SELECT rowid FROM source_fts WHERE source_fts MATCH ?"
            sub_params: list = [fts_q]
            if rowid_floor > 0:
                sub_sql += " AND rowid >= ?"
                sub_params.append(int(rowid_floor))
            sub_sql += " ORDER BY rowid DESC LIMIT ?"
            sub_params.append(int(cand_limit))

            rows = conn.execute(
                f"""
                WITH hit(id) AS ({sub_sql})
                SELECT
                  s.id, s.source_name, s.item_url, s.title_original,
                  substr(COALESCE(s.body_original,''),1,5200) AS body_original,
                  s.last_checked_at
                FROM hit
                JOIN source_items s ON s.id = hit.id
                WHERE s.content_kind = 'jp_listing'
                ORDER BY s.last_checked_at DESC, s.id DESC
                LIMIT ?
                """,
                (*sub_params, int(collect_limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                  s.id, s.source_name, s.item_url, s.title_original,
                  substr(COALESCE(s.body_original,''),1,5200) AS body_original,
                  s.last_checked_at
                FROM source_items s
                WHERE s.content_kind = 'jp_listing'
                ORDER BY s.last_checked_at DESC, s.id DESC
                LIMIT ?
                """,
                (collect_limit,),
            ).fetchall()

    if not rows:
        return None

    count_buy = 0
    count_rent = 0
    items: list[dict] = []
    for idx, row in enumerate(rows):
        title_jp = _clean(str(row["title_original"] or ""))
        snippet_jp = _clean(str(row["body_original"] or ""))[:220]
        url = str(row["item_url"] or "")
        tab = _classify_tab_by_record(title_jp, snippet_jp, url)
        if tab == "rent":
            count_rent += 1
        else:
            count_buy += 1

        if idx < display_limit and url and not url_is_low_value_for_link_list(url):
            source_name = str(row["source_name"] or "來源")
            items.append(
                {
                    "source_id": source_name.lower(),
                    "source_name": source_name,
                    "source_note": "依日期入庫排序",
                    "source_icon": _source_placeholder_image(url),
                    "title_jp": title_jp or source_name,
                    "title_zh_hant": title_jp or source_name,
                    "title_zh_hans": title_jp or source_name,
                    "snippet_jp": snippet_jp,
                    "snippet_zh_hant": snippet_jp,
                    "snippet_zh_hans": snippet_jp,
                    "url": url,
                    "property_tab": tab,
                    "last_checked_at": str(row["last_checked_at"] or ""),
                }
            )

    return {
        "ok": True,
        "count": len(rows),
        "count_buy": count_buy,
        "count_rent": count_rent,
        "items": items,
        "is_db_collected": True,
        "collect_limit": collect_limit,
        "display_limit": display_limit,
    }


def _source_placeholder_image(url: str) -> str:
    host = urlparse(url).netloc or "example.com"
    return f"https://www.google.com/s2/favicons?domain={host}&sz=128"


def _fallback_translate_keyword(query: str) -> tuple[str, str]:
    hant = query
    hans = query
    replacements = [
        ("関東", "關東", "关东"),
        ("不動産", "不動產", "不动产"),
        ("賃貸", "租賃", "租赁"),
        ("買う", "購買", "购买"),
        ("借りる", "租屋", "租房"),
        ("一戸建", "獨棟住宅", "独栋住宅"),
        ("新築", "新建", "新建"),
        ("中古", "中古", "中古"),
        ("土地", "土地", "土地"),
        ("マンション", "公寓", "公寓"),
    ]
    for jp, tw, cn in replacements:
        hant = hant.replace(jp, tw)
        hans = hans.replace(jp, cn)
    return hant, hans


def _to_zh_hans_fast(text: str) -> str:
    s = str(text or "")
    if not s:
        return ""
    if _cc_t2s is not None:
        try:
            return _cc_t2s.convert(s)
        except Exception:
            return s
    return s


def _fast_keyword_dual(query: str) -> tuple[str, str]:
    q = (query or "").strip()
    if not q:
        return "", ""
    if not _should_translate_keyword(q):
        return q, q
    hant, hans = _fallback_translate_keyword(q)
    if (hant or "").strip() == q.strip():
        return q, q
    return hant, hans


def _source_action_hint(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "suumo.jp" in host:
        return "建議先看「新築 / 中古 / 賃貸」分類，再按區域比較價格帶與供給量。"
    if "homes.co.jp" in host:
        return "建議先用地區、路線、地圖三種入口交叉查詢，確認同區域物件差異。"
    if "athome.co.jp" in host:
        return "建議先看租屋與買屋雙入口，並比對同條件物件的區域行情與單價。"
    return "建議先用同關鍵字比對不同來源結果，再進一步確認官方資料與最新時間。"


def _build_limited_points(title_hant: str, snippet_hant: str, url: str) -> list[str]:
    text = f"{title_hant} {snippet_hant}".lower()
    points = []
    if "javascript" in text or "未啟用" in text or "启用" in text:
        points.append("此頁面主要依賴瀏覽器 JavaScript 動態載入，所以伺服器端可讀取內容有限。")
    points.append(f"目前可讀重點：{title_hant}")
    if snippet_hant:
        points.append(f"摘要判讀：{snippet_hant[:130]}")
    points.append(_source_action_hint(url))
    points.append("建議同步比對 SUUMO / HOMES / AtHome 同關鍵字，提高查詢可靠度。")
    return points[:5]


def search_market_portal(keyword: str, per_source_limit: int = 6) -> dict:
    query = (keyword or "關東 不動產").strip() or "關東 不動產"
    keyword_hant, keyword_hans = _fast_keyword_dual(query)

    # Keep interactive lookup snappy (<0.5s) by limiting scan size; UI only needs a few hundred cards.
    collect_limit = 1200
    db_result = _search_db_market_records(keyword=query, collect_limit=collect_limit, display_limit=400)
    if db_result:
        conclusion_hant = _build_conclusion(
            count_buy=int(db_result.get("count_buy", 0)),
            count_rent=int(db_result.get("count_rent", 0)),
            keyword_hant=keyword_hant,
        )
        conclusion_hans = _to_zh_hans_fast(conclusion_hant)
        return {
            "ok": True,
            "keyword_input": query,
            "keyword_zh_hant": keyword_hant,
            "keyword_zh_hans": keyword_hans,
            "count": int(db_result.get("count", 0)),
            "count_buy": int(db_result.get("count_buy", 0)),
            "count_rent": int(db_result.get("count_rent", 0)),
            "items": db_result.get("items", []),
            "conclusion_zh_hant": conclusion_hant,
            "conclusion_zh_hans": conclusion_hans,
            "sources": SOURCE_PORTALS,
            "is_db_collected": True,
            "collect_limit": int(db_result.get("collect_limit", collect_limit)),
            "display_limit": int(db_result.get("display_limit", 400)),
        }

    live_fetch_enabled = str(os.getenv("SCLAW_MARKET_LIVE_FETCH", "0") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not live_fetch_enabled:
        items = []
        for source in SOURCE_PORTALS:
            items.append(
                {
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "source_note": source["note"],
                    "source_icon": source["icon"],
                    "title_jp": f"{source['name']} 搜尋入口",
                    "title_zh_hant": f"{source['name']} 搜尋入口",
                    "title_zh_hans": f"{source['name']} 搜尋入口",
                    "snippet_jp": f"未命中資料庫快取；請點此開啟來源頁再用關鍵字搜尋：{query}",
                    "snippet_zh_hant": f"未命中資料庫快取；請點此開啟來源頁再用關鍵字搜尋：{query}",
                    "snippet_zh_hans": f"未命中資料库缓存；请点此开启来源页再用关键词搜索：{query}",
                    "url": source["url"],
                    "property_tab": "buy",
                }
            )
        conclusion_hant = "未命中資料庫快取，已提供三站入口連結；如需即時抓取請開啟 SCLAW_MARKET_LIVE_FETCH=1。"
        conclusion_hans = _to_zh_hans_fast(conclusion_hant)
        return {
            "ok": True,
            "keyword_input": query,
            "keyword_zh_hant": keyword_hant,
            "keyword_zh_hans": keyword_hans,
            "count": len(items),
            "count_buy": len(items),
            "count_rent": 0,
            "items": items,
            "conclusion_zh_hant": conclusion_hant,
            "conclusion_zh_hans": conclusion_hans,
            "sources": SOURCE_PORTALS,
            "live_fetch_enabled": False,
        }

    items: list[dict] = []
    with httpx.Client(timeout=22, follow_redirects=True, headers={"User-Agent": "SCLAWBot/1.0"}) as client:
        for source in SOURCE_PORTALS:
            try:
                rows = _extract_from_source(client=client, source=source, query=query, limit=per_source_limit)
                if not rows:
                    rows = _fallback_rows(source)
            except Exception:
                rows = _fallback_rows(source)
            for row in rows:
                title_hant = row["title_jp"]
                title_hans = row["title_jp"]
                snippet_hant = row["snippet_jp"]
                snippet_hans = row["snippet_jp"]
                tab = _classify_tab(f"{row['title_jp']} {row['snippet_jp']}")
                items.append(
                    {
                        "source_id": source["id"],
                        "source_name": source["name"],
                        "source_note": source["note"],
                        "source_icon": source["icon"],
                        "title_jp": row["title_jp"],
                        "title_zh_hant": title_hant,
                        "title_zh_hans": title_hans,
                        "snippet_jp": row["snippet_jp"],
                        "snippet_zh_hant": snippet_hant,
                        "snippet_zh_hans": snippet_hans,
                        "url": row["url"],
                        "property_tab": tab,
                    }
                )

    count_buy = len([x for x in items if x["property_tab"] == "buy"])
    count_rent = len([x for x in items if x["property_tab"] == "rent"])
    conclusion_hant = _build_conclusion(count_buy=count_buy, count_rent=count_rent, keyword_hant=keyword_hant)
    conclusion_hans = _to_zh_hans_fast(conclusion_hant)
    return {
        "ok": True,
        "keyword_input": query,
        "keyword_zh_hant": keyword_hant,
        "keyword_zh_hans": keyword_hans,
        "count": len(items),
        "count_buy": count_buy,
        "count_rent": count_rent,
        "items": items,
        "conclusion_zh_hant": conclusion_hant,
        "conclusion_zh_hans": conclusion_hans,
        "sources": SOURCE_PORTALS,
    }


def collect_live_portal_search_links(keyword: str, per_source_limit: int = 6) -> list[dict]:
    """
    Always fetch SUUMO / HOMES / AtHome root pages and score in-page links by ``keyword``
    (same logic as ``search_market_portal`` live path, without DB shortcut).
    Used to turn「站內搜尋式」導覽連結 into crawl targets for the knowledge base.
    """
    from src.crawler import BROWSER_HEADERS

    query = (keyword or "").strip() or "關東 不動產"
    lim = max(1, min(12, int(per_source_limit)))
    rows_out: list[dict] = []
    with httpx.Client(timeout=22, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        for source in SOURCE_PORTALS:
            try:
                rows = _extract_from_source(client=client, source=source, query=query, limit=lim)
                if not rows:
                    rows = _fallback_rows(source)
            except Exception:
                rows = _fallback_rows(source)
            for row in rows:
                rows_out.append(dict(row))
    return rows_out


def build_market_detail(url: str) -> dict:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("網址格式不正確")
    with httpx.Client(timeout=22, follow_redirects=True, headers={"User-Agent": "SCLAWBot/1.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        html = _decode(resp)
    soup = soup_from_html(html)

    title_jp = _clean(soup.title.get_text(" ")) if soup.title else "未命名頁面"
    texts = []
    for node in soup.select("h1, h2, h3, p, li"):
        t = _clean(node.get_text(" "))
        if len(t) >= 18:
            texts.append(t)
        if len(texts) >= 20:
            break
    body_jp = "\n".join(texts)[:5000] if texts else title_jp
    title_hant, title_hans = dual_translate(title_jp)
    body_hant, body_hans = dual_translate(body_jp)

    chunks = [x.strip() for x in re.split(r"[。！？!?]\s*|\.\s+", body_hant) if len(x.strip()) >= 12]
    key_points = chunks[:6]

    image_urls = []
    for img in soup.select("img[src]"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        full = urljoin(url, src)
        if full not in image_urls:
            image_urls.append(full)
        if len(image_urls) >= 6:
            break

    return {
        "ok": True,
        "is_limited": False,
        "url": url,
        "title_jp": title_jp,
        "title_zh_hant": title_hant,
        "title_zh_hans": title_hans,
        "body_zh_hant": body_hant[:2200],
        "body_zh_hans": body_hans[:2200],
        "key_points_zh_hant": key_points,
        "count_paragraphs": len(texts),
        "count_images": len(image_urls),
        "image_urls": image_urls,
    }


def build_market_detail_fallback(url: str, title_jp: str = "", snippet_jp: str = "", source_name: str = "") -> dict:
    safe_title = (title_jp or source_name or "來源頁面").strip() or "來源頁面"
    safe_snippet = (snippet_jp or "此來源限制直接抓取內文，已改用查詢摘要與翻譯結果呈現。").strip()
    title_hant, title_hans = dual_translate(safe_title)
    body_hant, body_hans = dual_translate(safe_snippet)
    points = _build_limited_points(title_hant=title_hant, snippet_hant=body_hant, url=url)
    return {
        "ok": True,
        "is_limited": True,
        "url": url,
        "title_jp": safe_title,
        "title_zh_hant": title_hant,
        "title_zh_hans": title_hans,
        "body_zh_hant": body_hant,
        "body_zh_hans": body_hans,
        "key_points_zh_hant": points,
        "count_paragraphs": 1,
        "count_images": 1,
        "image_urls": [_source_placeholder_image(url)],
    }
