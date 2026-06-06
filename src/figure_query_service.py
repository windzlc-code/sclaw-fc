import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from src.config import DATA_DIR
from src.market_portal import SOURCE_PORTALS, search_market_portal
from src.text_utils import dual_translate

CACHE_PATH = DATA_DIR / "figure_query_cache.json"
_REFRESH_LOCK = threading.Lock()
_REFRESH_INFLIGHT: set[str] = set()

TAB_DEFAULTS = {
    "athome": "中古 マンション",
    "suumo": "関東 賃貸",
    "homes": "全国 賃貸 住宅",
}

SOURCE_HUB = {
    "suumo": {
        "home_url": "https://suumo.jp/chintai/kanto/",
        "feature_links": [
            {"label_jp": "沿線・エリアから探す", "url": "https://suumo.jp/chintai/kanto/"},
            {"label_jp": "通勤・通学時間から探す", "url": "https://suumo.jp/chintai/kanto/"},
            {"label_jp": "家賃相場から探す", "url": "https://suumo.jp/chintai/kanto/"},
            {"label_jp": "地図から探す", "url": "https://suumo.jp/chintai/kanto/"},
            {"label_jp": "キーワード検索", "url": "https://suumo.jp/chintai/kanto/"},
        ],
    },
    "homes": {
        "home_url": "https://www.homes.co.jp/chintai/",
        "feature_links": [
            {"label_jp": "都道府県から探す", "url": "https://www.homes.co.jp/chintai/"},
            {"label_jp": "沿線・駅から探す", "url": "https://www.homes.co.jp/chintai/"},
            {"label_jp": "地図から探す", "url": "https://www.homes.co.jp/chintai/"},
            {"label_jp": "通勤・通学時間から探す", "url": "https://www.homes.co.jp/chintai/"},
            {"label_jp": "こだわり条件で探す", "url": "https://www.homes.co.jp/chintai/"},
        ],
    },
    "athome": {
        "home_url": "https://www.athome.co.jp/chintai/",
        "feature_links": [
            {"label_jp": "アットホーム トップ", "url": "https://www.athome.co.jp/"},
            {"label_jp": "借りる（賃貸）", "url": "https://www.athome.co.jp/chintai/"},
            {"label_jp": "買う（マンション）", "url": "https://www.athome.co.jp/mansion/"},
            {"label_jp": "中古マンション", "url": "http://athome.co.jp/mansion/chuko/"},
            {"label_jp": "新築マンション", "url": "https://www.athome.co.jp/mansion/shinchiku/"},
            {"label_jp": "一戸建て", "url": "https://www.athome.co.jp/kodate/"},
            {"label_jp": "土地", "url": "https://www.athome.co.jp/tochi/"},
            {"label_jp": "売る・貸す", "url": "https://www.athome.co.jp/satei/"},
            {"label_jp": "投資・収益物件", "url": "https://www.athome.co.jp/"},
        ],
    },
}

KEYWORD_DIRECT_LINKS = {
    "homes": [
        {
            "patterns": ["セキスイハイム", "スマートハイムプレイス川口市末広", "川口市末広", "sid-517", "smart heim place"],
            "url": "https://www.homes.co.jp/kodate/shinchiku/special/sid-517/?o=48h60VWRlShzwk",
            "title_jp": "【セキスイハイム】スマートハイムプレイス川口市末広",
            "snippet_jp": "LIFULL HOME'S 新築一戸建て特集ページ。対象物件の詳細と問い合わせ導線を確認できます。",
        }
    ],
    "athome": [
        {
            "patterns": ["関東住宅(株)", "関東住宅", "kanto-j", "kanto j"],
            "url": "https://www.athome.co.jp/ahch/kanto-j.html",
            "title_jp": "関東住宅(株) 店舗ページ",
            "snippet_jp": "アットホーム掲載の関東住宅(株)詳細ページ。会社情報・問い合わせ導線を確認できます。",
        }
    ]
}


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _ensure_store() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CACHE_PATH.exists():
        empty = {"last_refresh_at": "", "records": []}
        CACHE_PATH.write_text(json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8")
        return empty
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"last_refresh_at": "", "records": []}
        if "records" not in data or not isinstance(data["records"], list):
            data["records"] = []
        if "last_refresh_at" not in data:
            data["last_refresh_at"] = ""
        return data
    except Exception:
        return {"last_refresh_at": "", "records": []}


def _save_store(store: dict) -> None:
    CACHE_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_record(
    tab_key: str,
    keyword: str,
    source_name: str,
    source_note: str,
    title_jp: str,
    title_zh_hant: str,
    title_zh_hans: str,
    snippet_jp: str,
    snippet_zh_hant: str,
    snippet_zh_hans: str,
    url: str,
    source_icon: str,
    source_query_url: str = "",
    is_direct_match: bool = False,
) -> dict:
    return {
        "tab_key": tab_key,
        "keyword_input": keyword,
        "source_name": source_name,
        "source_note": source_note,
        "source_icon": source_icon,
        "title_jp": title_jp,
        "title_zh_hant": title_zh_hant,
        "title_zh_hans": title_zh_hans,
        "snippet_jp": snippet_jp,
        "snippet_zh_hant": snippet_zh_hant,
        "snippet_zh_hans": snippet_zh_hans,
        "url": url,
        "source_query_url": source_query_url,
        "is_direct_match": bool(is_direct_match),
        "fetched_at": _now_iso(),
    }


def _source_query_link(source_id: str, keyword: str) -> str:
    kw = quote_plus(keyword.strip())
    if source_id == "suumo":
        return f"https://www.google.com/search?q=site%3Asuumo.jp+{kw}"
    if source_id == "homes":
        return f"https://www.google.com/search?q=site%3Ahomes.co.jp+{kw}"
    if source_id == "athome":
        return f"http://athome.co.jp/mansion/chuko/?q={kw}"
    return ""


def _fetch_live_tab(tab_key: str, keyword: str) -> tuple[list[dict], str, str, str]:
    source_map = {x["id"]: x for x in SOURCE_PORTALS}
    if tab_key not in source_map:
        tab_key = "suumo"
    data = search_market_portal(keyword)
    out = []
    for item in data.get("items", []):
        sid = item.get("source_id", "")
        if sid != tab_key:
            continue
        out.append(
            _build_record(
                tab_key=tab_key,
                keyword=keyword,
                source_name=item.get("source_name", ""),
                source_note=item.get("source_note", ""),
                source_icon=item.get("source_icon", ""),
                title_jp=item.get("title_jp", ""),
                title_zh_hant=item.get("title_zh_hant", ""),
                title_zh_hans=item.get("title_zh_hans", ""),
                snippet_jp=item.get("snippet_jp", ""),
                snippet_zh_hant=item.get("snippet_zh_hant", ""),
                snippet_zh_hans=item.get("snippet_zh_hans", ""),
                url=item.get("url", ""),
                source_query_url=_source_query_link(sid, keyword),
            )
        )

    out = _inject_direct_links(tab_key=tab_key, keyword=keyword, rows=out)
    return (
        out,
        data.get("keyword_zh_hant", keyword),
        data.get("keyword_zh_hans", keyword),
        data.get("conclusion_zh_hant", ""),
    )


def _build_feature_links(tab_key: str) -> list[dict]:
    cfg = SOURCE_HUB.get(tab_key, {})
    out = []
    for row in cfg.get("feature_links", []):
        # Keep interactive queries fast: avoid network translation here.
        hant, hans = row["label_jp"], row["label_jp"]
        out.append(
            {
                "label_jp": row["label_jp"],
                "label_zh_hant": hant,
                "label_zh_hans": hans,
                "url": row["url"],
            }
        )
    return out


def _inject_direct_links(tab_key: str, keyword: str, rows: list[dict]) -> list[dict]:
    q = (keyword or "").lower().strip()
    rules = KEYWORD_DIRECT_LINKS.get(tab_key, [])
    out = list(rows)
    existing_urls = {x.get("url", "") for x in out}
    for rule in rules:
        pats = [p.lower() for p in rule.get("patterns", [])]
        if not any(p in q for p in pats):
            continue
        url = rule.get("url", "")
        if not url or url in existing_urls:
            continue
        title_jp = rule.get("title_jp", "")
        snippet_jp = rule.get("snippet_jp", "")
        title_hant, title_hans = title_jp, title_jp
        snippet_hant, snippet_hans = snippet_jp, snippet_jp
        source_cfg = next((x for x in SOURCE_PORTALS if x.get("id") == tab_key), {})
        out.insert(
            0,
            _build_record(
                tab_key=tab_key,
                keyword=keyword,
                source_name=source_cfg.get("name", tab_key),
                source_note=source_cfg.get("note", ""),
                source_icon=source_cfg.get("icon", ""),
                title_jp=title_jp,
                title_zh_hant=title_hant,
                title_zh_hans=title_hans,
                snippet_jp=snippet_jp,
                snippet_zh_hant=snippet_hant,
                snippet_zh_hans=snippet_hans,
                url=url,
                source_query_url=_source_query_link(tab_key, keyword),
                is_direct_match=True,
            ),
        )
        existing_urls.add(url)
    return out


def _trim_records(records: list[dict], max_records: int, retention_days: int) -> list[dict]:
    cutoff = datetime.utcnow() - timedelta(days=max(1, retention_days))
    kept = []
    for item in records:
        ts = _parse_time(item.get("fetched_at", ""))
        if ts and ts < cutoff:
            continue
        kept.append(item)
    kept.sort(key=lambda x: x.get("fetched_at", ""), reverse=True)
    return kept[: max(100, max_records)]


def _is_stale(last_refresh_at: str, hours: int) -> bool:
    ts = _parse_time(last_refresh_at)
    if not ts:
        return True
    return datetime.utcnow() - ts >= timedelta(hours=max(1, hours))


def _search_cached(records: list[dict], tab_key: str, keyword: str, limit: int = 60) -> list[dict]:
    q = (keyword or "").strip()
    filtered = [x for x in records if x.get("tab_key") == tab_key]
    if not q:
        return filtered[:limit]

    q_norm = _normalize_for_match(q)
    direct_hits = [
        x
        for x in filtered
        if x.get("is_direct_match") and _normalize_for_match(x.get("keyword_input", "")) == q_norm
    ]
    if direct_hits:
        direct_hits.sort(key=lambda x: x.get("fetched_at", ""), reverse=True)
        return direct_hits[:limit]

    tokens = _keyword_tokens(q)
    core_tokens = [t for t in tokens if len(t) >= 2]
    required_hits = 1 if len(core_tokens) <= 1 else 2
    scored: list[tuple[int, dict]] = []
    for row in filtered:
        score, hit_count = _row_match_score(row, tokens)
        # Strict filtering: only keep genuinely related results.
        if score >= 6 and hit_count >= required_hits:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in scored[:limit]]


def _normalize_for_match(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[()\[\]（）「」『』【】\-_/\\:：,.，。!?！？&+＋]", "", t)
    return t


def _keyword_tokens(keyword: str) -> list[str]:
    raw = keyword or ""
    parts = re.split(r"[\s\u3000]+", raw.strip())
    tokens = [_normalize_for_match(p) for p in parts if p.strip()]
    whole = _normalize_for_match(raw)
    if whole and whole not in tokens:
        tokens.append(whole)
    return [x for x in tokens if x]


def _row_match_score(row: dict, tokens: list[str]) -> tuple[int, int]:
    if not tokens:
        return 0, 0
    title_jp = _normalize_for_match(row.get("title_jp", ""))
    title_hant = _normalize_for_match(row.get("title_zh_hant", ""))
    title_hans = _normalize_for_match(row.get("title_zh_hans", ""))
    snippet_jp = _normalize_for_match(row.get("snippet_jp", ""))
    snippet_hant = _normalize_for_match(row.get("snippet_zh_hant", ""))
    snippet_hans = _normalize_for_match(row.get("snippet_zh_hans", ""))
    haystack = [title_jp, title_hant, title_hans, snippet_jp, snippet_hant, snippet_hans]

    score = 0
    hit_count = 0
    for tk in tokens:
        if not tk:
            continue
        if any(tk in h for h in [title_jp, title_hant, title_hans]):
            score += 6
            hit_count += 1
        elif any(tk in h for h in [snippet_jp, snippet_hant, snippet_hans]):
            score += 2
            hit_count += 1
        elif any(h in tk for h in haystack if h):
            score += 1
            hit_count += 1
    return score, hit_count


def _build_query_only_entry(tab_key: str, keyword: str) -> dict:
    source_cfg = next((x for x in SOURCE_PORTALS if x.get("id") == tab_key), {})
    qurl = _source_query_link(tab_key, keyword)
    title_jp = f"{source_cfg.get('name', tab_key)} 検索入口"
    snippet_jp = f"未命中可用條目，請改用此來源的關鍵字搜尋：{keyword}"
    title_hant, title_hans = dual_translate(title_jp)
    snippet_hant, snippet_hans = dual_translate(snippet_jp)
    return _build_record(
        tab_key=tab_key,
        keyword=keyword,
        source_name=source_cfg.get("name", tab_key),
        source_note=source_cfg.get("note", ""),
        source_icon=source_cfg.get("icon", ""),
        title_jp=title_jp,
        title_zh_hant=title_hant,
        title_zh_hans=title_hans,
        snippet_jp=snippet_jp,
        snippet_zh_hant=snippet_hant,
        snippet_zh_hans=snippet_hans,
        url=qurl or source_cfg.get("url", ""),
        source_query_url=qurl,
    )


def query_figure_tab(tab_key: str, keyword: str, settings: dict) -> dict:
    if tab_key not in TAB_DEFAULTS:
        tab_key = "athome"
    keyword = (keyword or TAB_DEFAULTS[tab_key]).strip() or TAB_DEFAULTS[tab_key]
    interval_hours = int(settings.get("market_interval_hours", 1))
    max_records = int(settings.get("market_max_records", 10000))
    retention_days = int(settings.get("market_retention_days", 15))

    store = _ensure_store()
    records = list(store.get("records", []))

    # Keep interactive queries fast (<0.5s): serve cache immediately and refresh in background.
    live_fetch_enabled = str(os.getenv("SCLAW_MARKET_LIVE_FETCH", "0") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    refresh_due = _is_stale(store.get("last_refresh_at", ""), interval_hours)
    refresh_scheduled = False

    def _schedule_refresh(refresh_key: str) -> bool:
        if not live_fetch_enabled:
            return False
        with _REFRESH_LOCK:
            if refresh_key in _REFRESH_INFLIGHT:
                return False
            _REFRESH_INFLIGHT.add(refresh_key)

        def _worker() -> None:
            try:
                st = _ensure_store()
                recs = list(st.get("records", []))
                if _is_stale(st.get("last_refresh_at", ""), interval_hours):
                    for key, kw in TAB_DEFAULTS.items():
                        try:
                            rows, _, _, _ = _fetch_live_tab(key, kw)
                            recs = rows + recs
                        except Exception:
                            continue
                # Warm current tab/keyword for the next request.
                try:
                    rows2, _, _, _ = _fetch_live_tab(tab_key, keyword)
                    recs = rows2 + recs
                except Exception:
                    pass
                st["last_refresh_at"] = _now_iso()
                recs = _trim_records(recs, max_records=max_records, retention_days=retention_days)
                st["records"] = recs
                _save_store(st)
            finally:
                with _REFRESH_LOCK:
                    _REFRESH_INFLIGHT.discard(refresh_key)

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"figure-market-refresh:{refresh_key}",
        ).start()
        return True

    if refresh_due:
        refresh_scheduled = _schedule_refresh("due")

    rows = _search_cached(records, tab_key=tab_key, keyword=keyword, limit=80)
    if not rows:
        source_cfg = next((x for x in SOURCE_PORTALS if x.get("id") == tab_key), {})
        qurl = _source_query_link(tab_key, keyword)
        title_jp = f"{source_cfg.get('name', tab_key)} 入口查詢"
        snippet_jp = f"未命中快取結果，請點此直接開啟查詢頁：{keyword}"
        rows = [
            _build_record(
                tab_key=tab_key,
                keyword=keyword,
                source_name=source_cfg.get("name", tab_key),
                source_note=source_cfg.get("note", ""),
                source_icon=source_cfg.get("icon", ""),
                title_jp=title_jp,
                title_zh_hant=title_jp,
                title_zh_hans=title_jp,
                snippet_jp=snippet_jp,
                snippet_zh_hant=snippet_jp,
                snippet_zh_hans=snippet_jp,
                url=qurl or source_cfg.get("url", ""),
                source_query_url=qurl,
            )
        ]

    keyword_hant = keyword
    keyword_hans = keyword
    conclusion_hant = "已使用快取與最新來源綜合查詢。"
    conclusion_hans = conclusion_hant.replace("關鍵字", "关键词").replace("買賣", "买卖").replace("租賃", "租赁")

    return {
        "ok": True,
        "tab_key": tab_key,
        "keyword_input": keyword,
        "keyword_zh_hant": keyword_hant,
        "keyword_zh_hans": keyword_hans,
        "conclusion_zh_hant": conclusion_hant,
        "conclusion_zh_hans": conclusion_hans,
        "count": len(rows),
        "items": rows,
        "cache_total_records": len(records),
        "cache_last_refresh_at": store.get("last_refresh_at", ""),
        "market_interval_hours": interval_hours,
        "market_max_records": max_records,
        "market_retention_days": retention_days,
        "source_home_url": SOURCE_HUB.get(tab_key, {}).get("home_url", ""),
        "source_query_url": _source_query_link(tab_key, keyword),
        "feature_links": _build_feature_links(tab_key),
        "live_fetch_enabled": live_fetch_enabled,
        "refresh_due": refresh_due,
        "refresh_scheduled": refresh_scheduled,
    }
