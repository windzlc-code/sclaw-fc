import json
from urllib.parse import urlparse

from src.config import CONFIG_DIR, CRAWL_SETTINGS_PATH, DEFAULT_SOURCES, SOURCE_REGISTRY_PATH

_SOURCES_CACHE: list[dict] | None = None
_SOURCES_CACHE_KEY: tuple[int, int] | None = None
_CRAWL_SETTINGS_CACHE: dict | None = None
_CRAWL_SETTINGS_CACHE_KEY: tuple[int, int] | None = None
_CRAWL_SETTINGS_SOURCES_KEY: tuple[int, int] | None = None


def _path_stat_key(path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return (int(getattr(st, "st_mtime_ns", 0) or 0), int(getattr(st, "st_size", 0) or 0))
    except Exception:
        return None

# 與智慧查詢七站一致；一鍵爬文僅處理此清單（去重後依此順序執行）
SEVEN_JP_PORTAL_SEEDS: list[tuple[str, str, str]] = [
    ("SUUMO", "https://suumo.jp", "七大門戶預設；智慧查詢半年內主資料源"),
    ("LIFULL HOME'S", "https://www.homes.co.jp", "七大門戶預設；智慧查詢半年內主資料源"),
    ("at home", "https://www.athome.co.jp", "七大門戶預設；智慧查詢半年內主資料源"),
    ("Yahoo!不動産", "https://realestate.yahoo.co.jp", "七大門戶預設；智慧查詢半年內主資料源"),
    ("楽天不動産", "https://realestate.rakuten.co.jp", "七大門戶預設；智慧查詢半年內主資料源"),
    ("イエステーション", "https://www.yes1.co.jp", "七大門戶預設；智慧查詢半年內主資料源"),
    ("OHEYASU", "https://www.oheya-su.jp", "七大門戶預設；智慧查詢半年內主資料源"),
]
# 以正規化 host 做對齊與排序（同 host 不重复）
SEVEN_JP_PORTAL_HOST_ORDER: tuple[str, ...] = (
    "suumo.jp",
    "homes.co.jp",
    "athome.co.jp",
    "realestate.yahoo.co.jp",
    "realestate.rakuten.co.jp",
    "yes1.co.jp",
    "oheya-su.jp",
)

# 第一波補全：SUUMO → LIFULL HOME'S → at home → Yahoo!（與 SEVEN_JP 前四項一致）
PRIMARY_FOUR_PORTAL_HOSTS: tuple[str, ...] = SEVEN_JP_PORTAL_HOST_ORDER[:4]
# 第二波：楽天／イエステーション／OHEYASU
REMAINING_THREE_PORTAL_HOSTS: tuple[str, ...] = SEVEN_JP_PORTAL_HOST_ORDER[4:]

SOURCE_GROUP_DEFS = [
    {"id": "mainstream_portal", "label": "主流找房", "description": "SUUMO/HOME'S/AtHome/Yahoo/楽天 等門戶"},
    {"id": "official_market_data", "label": "官方價量", "description": "MLIT/reinfo/REINS/租金相場/官方統計"},
    {"id": "ownership_registry", "label": "產權登記", "description": "法務省登記、公圖等權利查核"},
    {"id": "auction", "label": "法拍競売", "description": "BIT 與民間法拍整合來源"},
    {"id": "sell_assessment", "label": "賣房査定", "description": "SUUMO/LIFULL/sumai-value 等賣房估價"},
    {"id": "developer_brand", "label": "開發商品牌", "description": "三井、三菱、住友、東急、野村等"},
    {"id": "kansai_local", "label": "區域專用", "description": "關西地區房產來源"},
    {"id": "investment_portal", "label": "投資專用", "description": "利回り/投資報表站點"},
    {"id": "other", "label": "其他", "description": "未分類或自訂來源"},
]
SOURCE_GROUP_IDS = [g["id"] for g in SOURCE_GROUP_DEFS]
HOME_HERO_KEYS: tuple[str, ...] = (
    "global-entry-classic",
    "tech-lines-skyline",
    "tech-orbit-grid",
    "brand-tokyo-night",
    "brand-gold-residence",
    "custom-upload",
)
DEFAULT_HOME_HERO_KEY = "brand-tokyo-night"


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def normalize_home_hero_key(raw: str | None) -> str:
    key = str(raw or "").strip()
    return key if key in HOME_HERO_KEYS else DEFAULT_HOME_HERO_KEY


def _normalize_home_hero_selection(key: str | None, custom_url: str | None) -> tuple[str, str]:
    hero_key = normalize_home_hero_key(key)
    hero_custom_url = str(custom_url or "").strip()[:1000]
    if hero_key == "custom-upload" and not hero_custom_url:
        hero_key = DEFAULT_HOME_HERO_KEY
    return hero_key, hero_custom_url


def _home_carousel_media_type(url: object, explicit: object = "") -> str:
    raw = str(explicit or "").strip().lower()
    path = urlparse(str(url or "").strip()).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    if ext in {"mp4", "webm", "mov", "m4v"}:
        return "video"
    return ""


def _normalize_home_carousel_items(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("source_url") or "").strip()[:1000]
        if not url or url in seen:
            continue
        media_type = _home_carousel_media_type(url, item.get("media_type"))
        if media_type != "video":
            continue
        try:
            sort_order = int(item.get("sort_order", idx + 1))
        except Exception:
            sort_order = idx + 1
        seen.add(url)
        out.append(
            {
                "url": url,
                "source_url": str(item.get("source_url") or url).strip()[:1000],
                "title": str(item.get("title") or "首頁影片").strip()[:160],
                "kind_label": str(item.get("kind_label") or "發布影片").strip()[:80],
                "media_type": "video",
                "time_label": str(item.get("time_label") or "").strip()[:80],
                "enabled": bool(item.get("enabled", True)),
                "sort_order": max(1, min(999, sort_order)),
            }
        )
    out.sort(key=lambda x: (int(x.get("sort_order") or 999), str(x.get("title") or "")))
    return out[:24]


def _host(url: str) -> str:
    try:
        return (urlparse(str(url or "").strip()).netloc or "").lower()
    except Exception:
        return ""


def infer_source_group(item: dict) -> str:
    h = _host(item.get("url", ""))
    n = str(item.get("name") or "").lower()
    c = str(item.get("category") or "").lower()
    t = f"{n} {c} {h}"
    if any(
        k in t
        for k in (
            "suumo.jp",
            "homes.co.jp",
            "athome.co.jp",
            "yahoo.co.jp",
            "rakuten.co.jp",
            "yes1.co.jp",
            "yes-station.jp",
            "oheya-su.jp",
            "oheyasuu.com",
        )
    ):
        return "mainstream_portal"
    if any(k in t for k in ("mlit.go.jp", "reinfolib", "reins.or.jp", "touchi-kanri.jp", "nta.go.jp", "e-stat.go.jp")):
        return "official_market_data"
    if any(k in t for k in ("touki.or.jp", "graphic")):
        return "ownership_registry"
    if any(k in t for k in ("courts.go.jp", "touki-k.com", "競売", "法拍")):
        return "auction"
    if any(k in t for k in ("sell.suumo.jp", "sell.lifull.co.jp", "ieuro.jp", "sumai-value.jp", "iesel.jp")):
        return "sell_assessment"
    if any(k in t for k in ("mitsui-fudosan.co.jp", "mec.co.jp", "sumitomo-rd.co.jp", "tokyu-land.co.jp", "nomura-fudosan.co.jp", "panahome.jp")):
        return "developer_brand"
    if any(k in t for k in ("housedo.co.jp", "c21-kansai.com", "osaka-fudosan.net", "kansai", "關西", "関西")):
        return "kansai_local"
    if any(k in t for k in ("reism.jp", "rimawari.jp", "rml.co.jp", "不動産投資", "利回り")):
        return "investment_portal"
    return "other"


def load_sources() -> list[dict]:
    global _SOURCES_CACHE, _SOURCES_CACHE_KEY
    _ensure_dir()
    key = _path_stat_key(SOURCE_REGISTRY_PATH) if SOURCE_REGISTRY_PATH.exists() else None
    if key is not None and _SOURCES_CACHE is not None and key == _SOURCES_CACHE_KEY:
        return [dict(x) for x in _SOURCES_CACHE]
    if not SOURCE_REGISTRY_PATH.exists():
        with_priority = []
        for idx, item in enumerate(DEFAULT_SOURCES):
            copied = dict(item)
            copied["priority"] = int(copied.get("priority", len(DEFAULT_SOURCES) - idx))
            copied["source_group"] = str(copied.get("source_group") or infer_source_group(copied))
            with_priority.append(copied)
        save_sources(with_priority)
        _SOURCES_CACHE = [dict(x) for x in with_priority]
        _SOURCES_CACHE_KEY = _path_stat_key(SOURCE_REGISTRY_PATH)
        return with_priority
    data = json.loads(SOURCE_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    changed = False
    normalized: list[dict] = []
    for idx, item in enumerate(data):
        copied = dict(item)
        if "priority" not in copied:
            copied["priority"] = len(data) - idx
            changed = True
        if not str(copied.get("source_group") or "").strip():
            copied["source_group"] = infer_source_group(copied)
            changed = True
        normalized.append(copied)
    if changed:
        save_sources(normalized)
        key = _path_stat_key(SOURCE_REGISTRY_PATH)
    _SOURCES_CACHE = [dict(x) for x in normalized]
    _SOURCES_CACHE_KEY = key
    return normalized


def save_sources(sources: list[dict]) -> None:
    global _SOURCES_CACHE, _SOURCES_CACHE_KEY
    _ensure_dir()
    SOURCE_REGISTRY_PATH.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    _SOURCES_CACHE = [dict(x) for x in sources]
    _SOURCES_CACHE_KEY = _path_stat_key(SOURCE_REGISTRY_PATH)


def get_enabled_sources() -> list[dict]:
    groups = load_source_group_enabled()
    enabled = []
    for x in load_sources():
        if not x.get("enabled", True):
            continue
        gid = str(x.get("source_group") or infer_source_group(x) or "other")
        if not bool(groups.get(gid, False)):
            continue
        enabled.append(x)
    return sorted(enabled, key=lambda x: int(x.get("priority", 0)), reverse=True)


def _norm_portal_host(url: str) -> str:
    try:
        h = (urlparse(str(url or "").strip()).netloc or "").lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


def ensure_seven_jp_portal_sources() -> None:
    """補齊七大日本門戶來源列；並將已存在同 host 之列設為啟用、群組 mainstream_portal。"""
    sources = load_sources()
    changed = False

    for name, url, note in SEVEN_JP_PORTAL_SEEDS:
        h = _norm_portal_host(url)
        if not h:
            continue
        found = None
        for i, item in enumerate(sources):
            if _norm_portal_host(str(item.get("url", ""))) == h:
                found = i
                break
        if found is not None:
            it = sources[found]
            if not bool(it.get("enabled", True)):
                it["enabled"] = True
                changed = True
            if str(it.get("source_group") or "").strip() != "mainstream_portal":
                it["source_group"] = "mainstream_portal"
                changed = True
            continue
        pr = max([int(x.get("priority", 0)) for x in sources], default=0) + 1
        sources.append(
            {
                "name": name.strip() or h,
                "category": "大型房仲",
                "url": url.strip(),
                "note": note.strip(),
                "enabled": True,
                "priority": pr,
                "source_group": "mainstream_portal",
            }
        )
        changed = True
    if changed:
        save_sources(sources)
    st = load_crawl_settings()
    m = _normalize_source_group_enabled(st.get("source_group_enabled"), load_sources())
    m["mainstream_portal"] = True
    st["source_group_enabled"] = m
    save_crawl_settings(st)


def ordered_seven_jp_portal_sources_for_crawl() -> list[dict]:
    """啟用且屬主流群組的七大門戶，依固定順序（可能少於 7 若庫損毀）。"""
    ensure_seven_jp_portal_sources()
    by_h: dict[str, dict] = {}
    for s in get_enabled_sources():
        if str(s.get("source_group") or "").strip() != "mainstream_portal":
            continue
        h = _norm_portal_host(str(s.get("url", "")))
        if h in SEVEN_JP_PORTAL_HOST_ORDER and h not in by_h:
            by_h[h] = s
    return [by_h[h] for h in SEVEN_JP_PORTAL_HOST_ORDER if h in by_h]


def ordered_primary_four_portal_sources_for_crawl() -> list[dict]:
    """主流七大站中的前四站（依固定順）；用於分段補抓、先跑滿 SUUMO／HOMES／at home／Yahoo。"""
    ensure_seven_jp_portal_sources()
    by_h: dict[str, dict] = {}
    for s in get_enabled_sources():
        if str(s.get("source_group") or "").strip() != "mainstream_portal":
            continue
        h = _norm_portal_host(str(s.get("url", "")))
        if h in PRIMARY_FOUR_PORTAL_HOSTS and h not in by_h:
            by_h[h] = s
    return [by_h[h] for h in PRIMARY_FOUR_PORTAL_HOSTS if h in by_h]


def ordered_remaining_three_portal_sources_for_crawl() -> list[dict]:
    """楽天／YES／OHEYASU 三站，固定順序。"""
    ensure_seven_jp_portal_sources()
    by_h: dict[str, dict] = {}
    for s in get_enabled_sources():
        if str(s.get("source_group") or "").strip() != "mainstream_portal":
            continue
        h = _norm_portal_host(str(s.get("url", "")))
        if h in REMAINING_THREE_PORTAL_HOSTS and h not in by_h:
            by_h[h] = s
    return [by_h[h] for h in REMAINING_THREE_PORTAL_HOSTS if h in by_h]


def add_source(name: str, category: str, url: str, note: str = "") -> dict:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Invalid URL")
    sources = load_sources()
    if any(x.get("url", "").rstrip("/") == url.rstrip("/") for x in sources):
        raise ValueError("Source URL already exists")
    item = {
        "name": name.strip() or parsed.netloc,
        "category": category.strip() or "自訂來源",
        "url": url.strip(),
        "note": note.strip(),
        "enabled": True,
        "priority": (max([int(x.get("priority", 0)) for x in sources], default=0) + 1),
        "source_group": "other",
    }
    sources.append(item)
    save_sources(sources)
    return item


def set_source_enabled(url: str, enabled: bool) -> dict:
    target = url.strip().rstrip("/")
    sources = load_sources()
    for item in sources:
        if item.get("url", "").rstrip("/") == target:
            item["enabled"] = bool(enabled)
            save_sources(sources)
            return item
    raise ValueError("Source URL not found")


def set_source_priority(url: str, priority: int) -> dict:
    target = url.strip().rstrip("/")
    sources = load_sources()
    for item in sources:
        if item.get("url", "").rstrip("/") == target:
            item["priority"] = int(priority)
            save_sources(sources)
            return item
    raise ValueError("Source URL not found")


def load_crawl_settings() -> dict:
    global _CRAWL_SETTINGS_CACHE, _CRAWL_SETTINGS_CACHE_KEY, _CRAWL_SETTINGS_SOURCES_KEY
    _ensure_dir()
    key = _path_stat_key(CRAWL_SETTINGS_PATH) if CRAWL_SETTINGS_PATH.exists() else None
    skey = _path_stat_key(SOURCE_REGISTRY_PATH) if SOURCE_REGISTRY_PATH.exists() else None
    if (
        _CRAWL_SETTINGS_CACHE is not None
        and key == _CRAWL_SETTINGS_CACHE_KEY
        and skey == _CRAWL_SETTINGS_SOURCES_KEY
    ):
        out = dict(_CRAWL_SETTINGS_CACHE)
        out["source_group_enabled"] = dict(out.get("source_group_enabled") or {})
        out["home_carousel_items"] = [dict(x) for x in out.get("home_carousel_items") or []]
        return out
    default_map = _default_source_group_enabled(load_sources())
    default_data = {
        "per_source_limit": 8,
        "interval_hours": 2,
        "market_interval_hours": 1,
        "market_max_records": 10000,
        "market_retention_days": 15,
        "portal_query_max_records": 100000,
        "portal_query_max_age_days": 180,
        "home_hero_key": DEFAULT_HOME_HERO_KEY,
        "home_hero_custom_url": "",
        "home_carousel_items": [],
        "smart_query_show_sell": False,
        "smart_query_show_rent": False,
        # 智慧查詢列表無縮圖時向原站補圖（可用環境變數覆寫）
        "portal_backfill_empty_thumbs": True,
        "portal_backfill_max": 3,
        "portal_backfill_persist": True,
        "source_group_enabled": default_map,
    }
    if not CRAWL_SETTINGS_PATH.exists():
        save_crawl_settings(default_data)
        _CRAWL_SETTINGS_CACHE = dict(default_data)
        _CRAWL_SETTINGS_CACHE_KEY = _path_stat_key(CRAWL_SETTINGS_PATH)
        _CRAWL_SETTINGS_SOURCES_KEY = skey
        return default_data
    data = json.loads(CRAWL_SETTINGS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return default_data
    group_enabled = _normalize_source_group_enabled(data.get("source_group_enabled"), load_sources())
    hero_key, hero_custom_url = _normalize_home_hero_selection(
        data.get("home_hero_key"),
        data.get("home_hero_custom_url"),
    )
    out = {
        "per_source_limit": int(data.get("per_source_limit", 8)),
        "interval_hours": int(data.get("interval_hours", 2)),
        "market_interval_hours": int(data.get("market_interval_hours", 1)),
        "market_max_records": int(data.get("market_max_records", 10000)),
        "market_retention_days": int(data.get("market_retention_days", 15)),
        "portal_query_max_records": max(10, min(100000, int(data.get("portal_query_max_records", 100000)))),
        "portal_query_max_age_days": max(1, min(366, int(data.get("portal_query_max_age_days", 180)))),
        "home_hero_key": hero_key,
        "home_hero_custom_url": hero_custom_url,
        "home_carousel_items": _normalize_home_carousel_items(data.get("home_carousel_items")),
        "smart_query_show_sell": bool(data.get("smart_query_show_sell", False)),
        "smart_query_show_rent": bool(data.get("smart_query_show_rent", False)),
        "portal_backfill_empty_thumbs": bool(data.get("portal_backfill_empty_thumbs", True)),
        "portal_backfill_max": max(1, min(12, int(data.get("portal_backfill_max", 3)))),
        "portal_backfill_persist": bool(data.get("portal_backfill_persist", True)),
        "source_group_enabled": group_enabled,
    }
    _CRAWL_SETTINGS_CACHE = dict(out)
    _CRAWL_SETTINGS_CACHE_KEY = key
    _CRAWL_SETTINGS_SOURCES_KEY = skey
    out["source_group_enabled"] = dict(group_enabled or {})
    out["home_carousel_items"] = [dict(x) for x in out.get("home_carousel_items") or []]
    return out


def save_crawl_settings(data: dict) -> dict:
    global _CRAWL_SETTINGS_CACHE, _CRAWL_SETTINGS_CACHE_KEY, _CRAWL_SETTINGS_SOURCES_KEY
    _ensure_dir()
    current = {}
    if CRAWL_SETTINGS_PATH.exists():
        try:
            current = json.loads(CRAWL_SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except Exception:
            current = {}
    hero_key, hero_custom_url = _normalize_home_hero_selection(
        data.get("home_hero_key", current.get("home_hero_key")),
        data.get("home_hero_custom_url", current.get("home_hero_custom_url", "")),
    )
    settings = {
        "per_source_limit": max(1, int(data.get("per_source_limit", 8))),
        "interval_hours": max(1, int(data.get("interval_hours", 2))),
        "market_interval_hours": max(1, int(data.get("market_interval_hours", 1))),
        "market_max_records": max(100, int(data.get("market_max_records", 10000))),
        "market_retention_days": max(1, int(data.get("market_retention_days", 15))),
        "portal_query_max_records": max(10, min(100000, int(data.get("portal_query_max_records", 100000)))),
        "portal_query_max_age_days": max(1, min(366, int(data.get("portal_query_max_age_days", 180)))),
        "home_hero_key": hero_key,
        "home_hero_custom_url": hero_custom_url,
        "home_carousel_items": _normalize_home_carousel_items(
            data.get("home_carousel_items", current.get("home_carousel_items", []))
        ),
        "smart_query_show_sell": bool(data.get("smart_query_show_sell", current.get("smart_query_show_sell", False))),
        "smart_query_show_rent": bool(data.get("smart_query_show_rent", current.get("smart_query_show_rent", False))),
        "portal_backfill_empty_thumbs": bool(
            data.get("portal_backfill_empty_thumbs", current.get("portal_backfill_empty_thumbs", True))
        ),
        "portal_backfill_max": max(
            1,
            min(12, int(data.get("portal_backfill_max", current.get("portal_backfill_max", 3)))),
        ),
        "portal_backfill_persist": bool(
            data.get("portal_backfill_persist", current.get("portal_backfill_persist", True))
        ),
        "source_group_enabled": _normalize_source_group_enabled(
            data.get("source_group_enabled", current.get("source_group_enabled")),
            load_sources(),
        ),
    }
    CRAWL_SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    _CRAWL_SETTINGS_CACHE = dict(settings)
    _CRAWL_SETTINGS_CACHE_KEY = _path_stat_key(CRAWL_SETTINGS_PATH)
    _CRAWL_SETTINGS_SOURCES_KEY = _path_stat_key(SOURCE_REGISTRY_PATH) if SOURCE_REGISTRY_PATH.exists() else None
    return settings


def _default_source_group_enabled(sources: list[dict]) -> dict[str, bool]:
    out = {gid: False for gid in SOURCE_GROUP_IDS}
    out["mainstream_portal"] = True
    for s in sources:
        if not s.get("enabled", True):
            continue
        gid = str(s.get("source_group") or infer_source_group(s) or "other")
        if gid in out:
            out[gid] = True
    return out


def _normalize_source_group_enabled(raw: object, sources: list[dict]) -> dict[str, bool]:
    base = _default_source_group_enabled(sources)
    if isinstance(raw, dict):
        for gid in SOURCE_GROUP_IDS:
            if gid in raw:
                base[gid] = bool(raw.get(gid))
    return base


def load_source_group_enabled() -> dict[str, bool]:
    st = load_crawl_settings()
    return _normalize_source_group_enabled(st.get("source_group_enabled"), load_sources())


def set_source_group_enabled(group_id: str, enabled: bool) -> dict[str, bool]:
    gid = str(group_id or "").strip()
    if gid not in SOURCE_GROUP_IDS:
        raise ValueError("source_group not found")
    st = load_crawl_settings()
    m = _normalize_source_group_enabled(st.get("source_group_enabled"), load_sources())
    m[gid] = bool(enabled)
    st["source_group_enabled"] = m
    save_crawl_settings(st)
    return m
