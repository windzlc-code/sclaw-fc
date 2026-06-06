import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import httpx

from src.bsoup import soup_from_html
from src.config import DATA_DIR

try:
    from opencc import OpenCC

    _cc_t2s = OpenCC("t2s")
except Exception:  # pragma: no cover
    _cc_t2s = None

NTA_TAXANSWER_URL = "https://www.nta.go.jp/taxes/shiraberu/taxanswer/index2.htm"
NTA_CSE_BASE = "https://cse.google.com/cse?cx=002894216937212238947%3Aaumvpxlzgxq"

_CACHE_PATH = DATA_DIR / "nta_taxanswer_cache.json"
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SEC = 12 * 60 * 60
_cache_mem: dict[str, dict] | None = None
_REFRESH_LOCK = threading.Lock()
_REFRESH_INFLIGHT: set[str] = set()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _cache_key(keyword: str) -> str:
    return " ".join((keyword or "").strip().split()).lower()


def _load_cache_from_disk() -> dict[str, dict]:
    if not _CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache_to_disk(cache: dict[str, dict]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _get_cache() -> dict[str, dict]:
    global _cache_mem
    if _cache_mem is None:
        _cache_mem = _load_cache_from_disk()
    return _cache_mem


def _build_conclusion(keyword_hant: str) -> str:
    kw = keyword_hant
    if any(x in kw for x in ["綜合所得稅", "所得稅", "確定申告", "申報"]):
        return "此關鍵字屬於所得稅申報主題，建議先看申報資格、扣除額與申報更正流程，再比對年度更新公告。"
    if any(x in kw for x in ["房貸", "貸款", "住宅ローン", "ローン"]):
        return "此關鍵字屬於貸款與扣除主題，建議優先確認住宅貸款控除適用條件與必要證明文件。"
    if any(x in kw for x in ["相續", "贈與", "遺產", "贈與稅"]):
        return "此關鍵字屬於財產移轉稅務主題，建議先確認申報義務、估價方式與申報期限。"
    return "此關鍵字屬於稅務查詢主題，建議先閱讀官方定義與適用對象，再查閱對應的申報與更正流程。"


def _should_translate_keyword(query: str) -> bool:
    text = query or ""
    # Keep Traditional Chinese keywords unchanged.
    if re.search(r"[綜稅臺灣買樓產務關鍵詞]", text):
        return False
    # Translate when likely Japanese input (kana or common JP tax terms).
    return bool(re.search(r"[\u3040-\u30ff]|総合|所得税|申告|控除|国税|令和", text))


def _search_with_cse(client: httpx.Client, query: str) -> list[dict]:
    cse_url = f"{NTA_CSE_BASE}&q={quote_plus(query)}"
    resp = client.get(cse_url)
    resp.raise_for_status()
    soup = soup_from_html(resp.text)

    rows = []
    for node in soup.select(".gsc-webResult, .gs-webResult"):
        title_el = node.select_one("a.gs-title")
        if not title_el:
            continue
        url = (title_el.get("href") or "").strip()
        title_jp = _clean(title_el.get_text(" "))
        snippet_el = node.select_one(".gs-snippet")
        snippet_jp = _clean(snippet_el.get_text(" ")) if snippet_el else ""
        if not title_jp or not url:
            continue
        if "nta.go.jp" not in url and "taxanswer" not in url:
            continue
        rows.append({"title_jp": title_jp, "snippet_jp": snippet_jp, "url": url})
    return rows


def _fallback_from_taxanswer_top(client: httpx.Client) -> list[dict]:
    resp = client.get(NTA_TAXANSWER_URL)
    resp.raise_for_status()
    soup = soup_from_html(resp.text)
    candidate_links = []
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        title = _clean(a.get_text(" "))
        if not title:
            continue
        if "taxanswer" in href or "taxanswer" in title.lower():
            full = urljoin(NTA_TAXANSWER_URL, href)
            candidate_links.append((title, full))

    # If taxanswer-specific anchors are sparse, include useful tax links from top page.
    if len(candidate_links) < 3:
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            title = _clean(a.get_text(" "))
            if not title:
                continue
            if any(x in title for x in ["タックスアンサー", "確定申告", "税", "申告"]):
                full = urljoin(NTA_TAXANSWER_URL, href)
                candidate_links.append((title, full))

    unique = []
    seen = set()
    for title, full in candidate_links:
        key = (title, full)
        if key in seen:
            continue
        seen.add(key)
        unique.append({"title_jp": title, "snippet_jp": "", "url": full})
    return unique[:8]


def _refresh_now(query: str) -> dict:
    q = (query or "綜合所得稅").strip() or "綜合所得稅"
    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "SCLAWBot/1.0"}) as client:
        rows = _search_with_cse(client, q)
        if not rows:
            rows = _fallback_from_taxanswer_top(client)

    query_re = re.escape(q)
    matched = [x for x in rows if re.search(query_re, x["title_jp"], re.I) or re.search(query_re, x["snippet_jp"], re.I)]
    selected = (matched or rows)[:8]

    items = []
    for row in selected:
        title_jp = row["title_jp"]
        snippet_jp = row.get("snippet_jp", "") or ""
        items.append(
            {
                "title_jp": title_jp,
                "title_zh_hant": title_jp,
                "title_zh_hans": title_jp,
                "snippet_jp": snippet_jp,
                "snippet_zh_hant": snippet_jp,
                "snippet_zh_hans": snippet_jp,
                "url": row["url"],
            }
        )

    keyword_hant = q
    keyword_hans = q
    cse_url = f"{NTA_CSE_BASE}&q={quote_plus(q)}"
    conclusion_hant = _build_conclusion(keyword_hant)
    conclusion_hans = _to_zh_hans_fast(conclusion_hant)
    payload = {
        "ok": True,
        "keyword_input": q,
        "keyword_zh_hant": keyword_hant,
        "keyword_zh_hans": keyword_hans,
        "taxanswer_url": NTA_TAXANSWER_URL,
        "cse_url": cse_url,
        "count": len(items),
        "items": items,
        "conclusion_zh_hant": conclusion_hant,
        "conclusion_zh_hans": conclusion_hans,
        "fetched_at": _now_iso(),
        "fetched_ts": time.time(),
    }
    with _CACHE_LOCK:
        cache = _get_cache()
        cache[_cache_key(q)] = payload
        _save_cache_to_disk(cache)
    return payload


def _schedule_refresh(query: str) -> bool:
    key = _cache_key(query)
    if not key:
        return False
    with _REFRESH_LOCK:
        if key in _REFRESH_INFLIGHT:
            return False
        _REFRESH_INFLIGHT.add(key)

    def _worker() -> None:
        try:
            _refresh_now(query)
        finally:
            with _REFRESH_LOCK:
                _REFRESH_INFLIGHT.discard(key)

    threading.Thread(target=_worker, daemon=True, name=f"nta-taxanswer-refresh:{key}").start()
    return True


def search_nta_taxanswer(keyword: str) -> dict:
    query = (keyword or "綜合所得稅").strip() or "綜合所得稅"
    key = _cache_key(query)
    now = time.time()

    with _CACHE_LOCK:
        cache = _get_cache()
        entry = cache.get(key) if isinstance(cache, dict) else None
        if isinstance(entry, dict):
            fetched_ts = float(entry.get("fetched_ts") or 0)
            if fetched_ts > 0 and now - fetched_ts <= _CACHE_TTL_SEC:
                out = dict(entry)
                out["stale"] = False
                return out
            scheduled = _schedule_refresh(query)
            out = dict(entry)
            out["stale"] = True
            out["stale_age_sec"] = max(0.0, now - fetched_ts) if fetched_ts > 0 else None
            out["refresh_scheduled"] = scheduled
            return out

    scheduled = _schedule_refresh(query)
    return {
        "ok": True,
        "keyword_input": query,
        "keyword_zh_hant": query,
        "keyword_zh_hans": query,
        "taxanswer_url": NTA_TAXANSWER_URL,
        "cse_url": f"{NTA_CSE_BASE}&q={quote_plus(query)}",
        "count": 0,
        "items": [],
        "conclusion_zh_hant": "資料準備中，請稍後重試（已在背景抓取）。",
        "conclusion_zh_hans": "资料准备中，请稍后重试（已在后台抓取）。",
        "pending_fetch": True,
        "refresh_scheduled": scheduled,
    }
