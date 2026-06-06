from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.config import DATA_DIR
from src.homes_geo import HOMES_KODATE_CHUKO_PREFS, HomesPref
from src.portal_http import PORTAL_BROWSER_HEADERS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HomesTransitLine:
    line_id: int
    trans_type: str
    line_name: str
    url: str
    homes_count: int


@dataclass(frozen=True)
class HomesTransitStation:
    station_id: int
    station_name: str
    url: str
    homes_count: int


_TRANSIT_CACHE_PATH = DATA_DIR / "homes_kodate_chuko_transit_cache.json"
_TRANSIT_CACHE_LOCK = threading.Lock()
_TRANSIT_CACHE_TTL_SEC = 12 * 60 * 60
_transit_cache_mem: dict[str, Any] | None = None

_TRANSIT_REFRESH_LOCK = threading.Lock()
_TRANSIT_REFRESH_INFLIGHT: set[str] = set()

# Station pages can be expensive (Playwright); cache them longer.
_STATION_CACHE_TTL_SEC = 24 * 60 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_num(raw: str) -> int:
    s = str(raw or "").strip()
    if not s:
        return 0
    m = re.search(r"([\d,]+)", s)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return 0


def _looks_like_homes_challenge(html: str) -> bool:
    if not html:
        return True
    h = html.lower()
    markers = (
        "human verification",
        "awswaf.com",
        "awswaf",
        "gokuprops",
        "aws waf",
        "challenge.js",
        "captcha.awswaf",
        "/challenge/",
    )
    if any(m in h for m in markers):
        return True
    if "homes.co.jp" in h and len(html) < 12000 and h.count("<a ") < 3:
        if "aws" in h or "waf" in h or "challenge" in h:
            return True
    return False


def _fetch_html_httpx(url: str) -> str:
    try:
        with httpx.Client(
            headers=dict(PORTAL_BROWSER_HEADERS),
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        ) as client:
            res = client.get(url)
            return res.text or ""
    except Exception:
        return ""


def _fetch_html_playwright(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return ""

    from src.portal_property_playwright import default_playwright_state_path

    state_path = default_playwright_state_path()
    storage_state = str(state_path) if state_path.is_file() else None
    ua = str(PORTAL_BROWSER_HEADERS.get("User-Agent") or "").strip() or None
    accept_lang = str(PORTAL_BROWSER_HEADERS.get("Accept-Language") or "ja").strip()

    init_script = """
(() => {
  try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); } catch (e) {}
})();
"""

    def _safe_page_content(page, *, retries: int = 40, wait_ms: int = 500) -> str:
        html = ""
        for _ in range(max(1, int(retries))):
            try:
                html = page.content() or ""
                if html:
                    return html
            except Exception:
                pass
            try:
                page.wait_for_timeout(int(wait_ms))
            except Exception:
                break
        return html or ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx_kw: dict[str, Any] = {
                "locale": "ja-JP",
                "java_script_enabled": True,
                "viewport": {"width": 1365, "height": 900},
                "extra_http_headers": {"Accept-Language": accept_lang},
            }
            if ua:
                ctx_kw["user_agent"] = ua
            if storage_state:
                ctx_kw["storage_state"] = storage_state
            ctx = browser.new_context(**ctx_kw)
            ctx.add_init_script(init_script)
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            _ = getattr(resp, "status", None) if resp else None
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass

            html = ""
            for _i in range(40):
                try:
                    page.wait_for_timeout(350)
                except Exception:
                    pass
                try:
                    html = page.content() or ""
                except Exception:
                    continue
                if html and not _looks_like_homes_challenge(html):
                    break
            if not html:
                html = _safe_page_content(page)
            browser.close()
            return html or ""
    except Exception:
        return ""


def _fetch_html(url: str, *, must_contain: str = "mod-checkList") -> str:
    html = _fetch_html_httpx(url)
    if html and not _looks_like_homes_challenge(html) and must_contain in html:
        return html
    html2 = _fetch_html_playwright(url)
    if html2 and not _looks_like_homes_challenge(html2) and must_contain in html2:
        return html2
    return html or html2 or ""


def _cache_load() -> dict[str, Any]:
    global _transit_cache_mem
    if _transit_cache_mem is not None:
        return _transit_cache_mem
    try:
        if _TRANSIT_CACHE_PATH.is_file():
            _transit_cache_mem = json.loads(_TRANSIT_CACHE_PATH.read_text("utf-8"))
        else:
            _transit_cache_mem = {}
    except Exception:
        _transit_cache_mem = {}
    if not isinstance(_transit_cache_mem, dict):
        _transit_cache_mem = {}
    _transit_cache_mem.setdefault("version", 1)
    _transit_cache_mem.setdefault("lines_by_pref", {})
    _transit_cache_mem.setdefault("stations_by_key", {})
    return _transit_cache_mem


def _cache_save(cache: dict[str, Any]) -> None:
    try:
        _TRANSIT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TRANSIT_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), "utf-8")
    except Exception as ex:
        logger.warning("Could not persist HOMES transit cache: %s", ex)


def _line_name_with_prefix(line_name: str, trans_type: str) -> str:
    name = str(line_name or "").strip()
    typ = str(trans_type or "").strip()
    if not name:
        return ""
    if typ.startswith("JR") and not name.startswith("JR"):
        return f"JR{name}"
    if typ.startswith("東京メトロ") and not name.startswith("東京メトロ"):
        return f"東京メトロ{name}"
    if typ.startswith("Osaka Metro") and not (name.startswith("Osaka Metro") or name.startswith("大阪メトロ")):
        return f"Osaka Metro{name}"
    return name


def _parse_transit_lines(pref: HomesPref, html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "lxml")
    root = soup.select_one("div.mod-checkList.rosen")
    if not root:
        return []

    groups: list[dict[str, Any]] = []
    for fs in root.select("fieldset"):
        legend = fs.select_one("legend")
        if not legend:
            continue
        num_el = legend.select_one("span.num")
        group_count = _parse_num(num_el.get_text(" ", strip=True) if num_el else "")
        group_label = legend.get_text(" ", strip=True)
        if num_el:
            num_txt = num_el.get_text(" ", strip=True)
            group_label = group_label.replace(num_txt, "").strip()
        group_label = group_label.strip()

        items: list[dict[str, Any]] = []
        for li in fs.select("li"):
            inp = li.find("input")
            if not inp or not inp.has_attr("value"):
                continue
            try:
                line_id = int(str(inp.get("value") or "").strip())
            except Exception:
                continue
            if line_id <= 0:
                continue
            label_el = li.select_one("label")
            if not label_el:
                continue
            num2_el = label_el.select_one("span.num")
            count = _parse_num(num2_el.get_text(" ", strip=True) if num2_el else "")
            a = label_el.find("a", href=True)
            url = str(a.get("href") or "").strip() if a else ""
            label = a.get_text(" ", strip=True) if a else label_el.get_text(" ", strip=True)
            if num2_el:
                num_txt = num2_el.get_text(" ", strip=True)
                label = label.replace(num_txt, "").strip()
            label = _line_name_with_prefix(label, group_label)
            enabled = bool(url) and count > 0
            items.append(
                {
                    "line_id": int(line_id),
                    "city_area": pref.label,
                    "trans_type": group_label,
                    "line_name": label,
                    "url": url,
                    "homes_count": int(count),
                    "enabled": enabled,
                }
            )
        if not items:
            continue
        groups.append(
            {
                "label": group_label,
                "count": int(group_count),
                "items": items,
            }
        )
    return groups


def _parse_transit_stations(pref: HomesPref, line_id: int, html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "lxml")
    root = soup.select_one("div.mod-checkList.eki")
    if not root:
        return []

    stations: list[dict[str, Any]] = []
    for li in root.select("li"):
        inp = li.find("input")
        if not inp or not inp.has_attr("value"):
            continue
        try:
            station_id = int(str(inp.get("value") or "").strip())
        except Exception:
            continue
        if station_id <= 0:
            continue
        label_el = li.select_one("label")
        if not label_el:
            continue
        num_el = label_el.select_one("span.num")
        count = _parse_num(num_el.get_text(" ", strip=True) if num_el else "")
        a = label_el.find("a", href=True)
        url = str(a.get("href") or "").strip() if a else ""
        label = a.get_text(" ", strip=True) if a else label_el.get_text(" ", strip=True)
        if num_el:
            num_txt = num_el.get_text(" ", strip=True)
            label = label.replace(num_txt, "").strip()
        name = str(label).strip().replace("駅", "").strip()
        if not name:
            continue
        enabled = bool(url) and count > 0
        stations.append(
            {
                "station_id": int(station_id),
                "line_id": int(line_id),
                "station_name": name,
                "prefecture": pref.label,
                "city": pref.label,
                "ward": "",
                "full_address": name,
                "url": url,
                "homes_count": int(count),
                "enabled": enabled,
            }
        )
    return stations


def _schedule_refresh(key: str, worker) -> bool:
    with _TRANSIT_REFRESH_LOCK:
        if key in _TRANSIT_REFRESH_INFLIGHT:
            return False
        _TRANSIT_REFRESH_INFLIGHT.add(key)

    def _run() -> None:
        try:
            worker()
        except Exception as ex:
            logger.warning("HOMES transit refresh failed for %s: %s", key, ex)
        finally:
            with _TRANSIT_REFRESH_LOCK:
                _TRANSIT_REFRESH_INFLIGHT.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"homes-transit-refresh:{key}").start()
    return True


def _refresh_lines_now(pref_key: str) -> dict[str, Any]:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")

    url = f"https://www.homes.co.jp/kodate/chuko/{key}/line/"
    html = _fetch_html(url, must_contain="mod-checkList")
    if _looks_like_homes_challenge(html) or "mod-checkList" not in html:
        raise RuntimeError("homes blocked or returned unexpected content")
    groups = _parse_transit_lines(pref, html)

    entry: dict[str, Any] = {
        "ok": True,
        "pref": {"id": pref.key, "label": pref.label, "region": pref.region},
        "url": url,
        "fetched_at": _now_iso(),
        "fetched_ts": float(time.time()),
        "groups": groups,
        "pending_fetch": False,
    }

    with _TRANSIT_CACHE_LOCK:
        cache = _cache_load()
        cache.setdefault("lines_by_pref", {})[key] = entry
        _cache_save(cache)
    return entry


def _refresh_stations_now(pref_key: str, line_id: int, line_url: str) -> dict[str, Any]:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")
    lid = int(line_id or 0)
    if lid <= 0:
        raise ValueError("invalid line_id")
    url = str(line_url or "").strip()
    if not url:
        raise ValueError("missing line_url")

    html = _fetch_html(url, must_contain="mod-checkList")
    if _looks_like_homes_challenge(html) or "mod-checkList" not in html:
        raise RuntimeError("homes blocked or returned unexpected content")
    stations = _parse_transit_stations(pref, lid, html)

    entry: dict[str, Any] = {
        "ok": True,
        "pref": {"id": pref.key, "label": pref.label, "region": pref.region},
        "line_id": lid,
        "url": url,
        "fetched_at": _now_iso(),
        "fetched_ts": float(time.time()),
        "stations": stations,
        "pending_fetch": False,
    }

    skey = f"{key}:{lid}"
    with _TRANSIT_CACHE_LOCK:
        cache = _cache_load()
        cache.setdefault("stations_by_key", {})[skey] = entry
        _cache_save(cache)
    return entry


def homes_kodate_chuko_transit_lines(pref_key: str, *, force_refresh: bool = False) -> dict[str, Any]:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")

    with _TRANSIT_CACHE_LOCK:
        cache = _cache_load()
        lines_by_pref = cache.get("lines_by_pref") if isinstance(cache, dict) else None
        entry = (lines_by_pref or {}).get(key) if isinstance(lines_by_pref, dict) else None
        if not force_refresh and isinstance(entry, dict):
            fetched_ts = float(entry.get("fetched_ts") or 0)
            if fetched_ts > 0 and time.time() - fetched_ts <= _TRANSIT_CACHE_TTL_SEC:
                return entry
            scheduled = _schedule_refresh(key, lambda: _refresh_lines_now(key))
            out = dict(entry)
            out["stale"] = True
            out["stale_age_sec"] = max(0.0, time.time() - fetched_ts) if fetched_ts > 0 else None
            out["refresh_scheduled"] = scheduled
            return out

    if force_refresh:
        return _refresh_lines_now(key)

    scheduled = _schedule_refresh(key, lambda: _refresh_lines_now(key))
    return {
        "ok": True,
        "pref": {"id": pref.key, "label": pref.label, "region": pref.region},
        "url": f"https://www.homes.co.jp/kodate/chuko/{key}/line/",
        "fetched_at": "",
        "fetched_ts": 0,
        "groups": [],
        "pending_fetch": True,
        "refresh_scheduled": scheduled,
    }


def homes_kodate_chuko_transit_stations(
    pref_key: str,
    *,
    line_id: int,
    line_url: str,
    force_refresh: bool = False,
) -> dict[str, Any]:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")
    lid = int(line_id or 0)
    if lid <= 0:
        raise ValueError("invalid line_id")
    skey = f"{key}:{lid}"

    with _TRANSIT_CACHE_LOCK:
        cache = _cache_load()
        stations_by_key = cache.get("stations_by_key") if isinstance(cache, dict) else None
        entry = (stations_by_key or {}).get(skey) if isinstance(stations_by_key, dict) else None
        if not force_refresh and isinstance(entry, dict):
            fetched_ts = float(entry.get("fetched_ts") or 0)
            if fetched_ts > 0 and time.time() - fetched_ts <= _STATION_CACHE_TTL_SEC:
                return entry
            scheduled = _schedule_refresh(skey, lambda: _refresh_stations_now(key, lid, line_url))
            out = dict(entry)
            out["stale"] = True
            out["stale_age_sec"] = max(0.0, time.time() - fetched_ts) if fetched_ts > 0 else None
            out["refresh_scheduled"] = scheduled
            return out

    if force_refresh:
        return _refresh_stations_now(key, lid, line_url)

    scheduled = _schedule_refresh(skey, lambda: _refresh_stations_now(key, lid, line_url))
    return {
        "ok": True,
        "pref": {"id": pref.key, "label": pref.label, "region": pref.region},
        "line_id": lid,
        "url": str(line_url or "").strip(),
        "fetched_at": "",
        "fetched_ts": 0,
        "stations": [],
        "pending_fetch": True,
        "refresh_scheduled": scheduled,
    }
