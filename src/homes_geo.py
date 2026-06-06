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
from src.portal_http import PORTAL_BROWSER_HEADERS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HomesPref:
    key: str
    label: str
    region: str


HOMES_KODATE_CHUKO_PREFS: list[HomesPref] = [
    HomesPref("hokkaido", "北海道", "北海道"),
    HomesPref("aomori", "青森県", "東北"),
    HomesPref("iwate", "岩手県", "東北"),
    HomesPref("miyagi", "宮城県", "東北"),
    HomesPref("akita", "秋田県", "東北"),
    HomesPref("yamagata", "山形県", "東北"),
    HomesPref("fukushima", "福島県", "東北"),
    HomesPref("ibaraki", "茨城県", "關東"),
    HomesPref("tochigi", "栃木県", "關東"),
    HomesPref("gunma", "群馬県", "關東"),
    HomesPref("saitama", "埼玉県", "關東"),
    HomesPref("chiba", "千葉県", "關東"),
    HomesPref("tokyo", "東京都", "關東"),
    HomesPref("kanagawa", "神奈川県", "關東"),
    HomesPref("niigata", "新潟県", "甲信越"),
    HomesPref("toyama", "富山県", "北陸"),
    HomesPref("ishikawa", "石川県", "北陸"),
    HomesPref("fukui", "福井県", "北陸"),
    HomesPref("yamanashi", "山梨県", "甲信越"),
    HomesPref("nagano", "長野県", "甲信越"),
    HomesPref("gifu", "岐阜県", "東海"),
    HomesPref("shizuoka", "静岡県", "東海"),
    HomesPref("aichi", "愛知県", "東海"),
    HomesPref("mie", "三重県", "東海"),
    HomesPref("shiga", "滋賀県", "關西"),
    HomesPref("kyoto", "京都府", "關西"),
    HomesPref("osaka", "大阪府", "關西"),
    HomesPref("hyogo", "兵庫県", "關西"),
    HomesPref("nara", "奈良県", "關西"),
    HomesPref("wakayama", "和歌山県", "關西"),
    HomesPref("tottori", "鳥取県", "中國地方"),
    HomesPref("shimane", "島根県", "中國地方"),
    HomesPref("okayama", "岡山県", "中國地方"),
    HomesPref("hiroshima", "広島県", "中國地方"),
    HomesPref("yamaguchi", "山口県", "中國地方"),
    HomesPref("tokushima", "徳島県", "四國"),
    HomesPref("kagawa", "香川県", "四國"),
    HomesPref("ehime", "愛媛県", "四國"),
    HomesPref("kochi", "高知県", "四國"),
    HomesPref("fukuoka", "福岡県", "九州"),
    HomesPref("saga", "佐賀県", "九州"),
    HomesPref("nagasaki", "長崎県", "九州"),
    HomesPref("kumamoto", "熊本県", "九州"),
    HomesPref("oita", "大分県", "九州"),
    HomesPref("miyazaki", "宮崎県", "九州"),
    HomesPref("kagoshima", "鹿児島県", "九州"),
    HomesPref("okinawa", "沖縄県", "沖繩"),
]

HOMES_KODATE_CHUKO_REGIONS: list[str] = [
    "北海道",
    "東北",
    "關東",
    "甲信越",
    "北陸",
    "東海",
    "關西",
    "中國地方",
    "四國",
    "九州",
    "沖繩",
]


def homes_kodate_chuko_pref_catalog() -> dict[str, Any]:
    """Static region→pref catalog for cascading dropdowns."""
    by_region: dict[str, list[HomesPref]] = {r: [] for r in HOMES_KODATE_CHUKO_REGIONS}
    for p in HOMES_KODATE_CHUKO_PREFS:
        by_region.setdefault(p.region, []).append(p)
    regions: list[dict[str, Any]] = []
    for r in HOMES_KODATE_CHUKO_REGIONS:
        prefs = by_region.get(r) or []
        regions.append(
            {
                "id": r,
                "label": r,
                "prefs": [{"id": p.key, "label": p.label} for p in prefs],
            }
        )
    return {
        "regions": regions,
        "prefs": [{"id": p.key, "label": p.label, "region": p.region} for p in HOMES_KODATE_CHUKO_PREFS],
    }


_CITY_CACHE_PATH = DATA_DIR / "homes_kodate_chuko_city_cache.json"
_CITY_CACHE_LOCK = threading.Lock()
_CITY_CACHE_TTL_SEC = 12 * 60 * 60
_city_cache_mem: dict[str, Any] | None = None
_CITY_REFRESH_LOCK = threading.Lock()
_CITY_REFRESH_INFLIGHT: set[str] = set()


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


def _load_city_cache_from_disk() -> dict[str, Any]:
    if not _CITY_CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(_CITY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_city_cache_to_disk(cache: dict[str, Any]) -> None:
    try:
        _CITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CITY_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as ex:
        logger.warning("Could not persist HOMES city cache: %s", ex)


def _get_city_cache() -> dict[str, Any]:
    global _city_cache_mem
    if _city_cache_mem is None:
        _city_cache_mem = _load_city_cache_from_disk()
    return _city_cache_mem


def _refresh_city_groups_now(pref_key: str) -> dict[str, Any]:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")

    url = f"https://www.homes.co.jp/kodate/chuko/{key}/city/"
    html = _fetch_html(url)
    if _looks_like_homes_challenge(html) or "mod-checkList" not in html:
        raise RuntimeError("homes blocked or returned unexpected content")
    groups = _parse_city_groups(key, html)
    payload = {
        "ok": True,
        "pref": {"id": pref.key, "label": pref.label, "region": pref.region},
        "url": url,
        "fetched_at": _now_iso(),
        "fetched_ts": time.time(),
        "groups": groups,
    }
    with _CITY_CACHE_LOCK:
        cache = _get_city_cache()
        cache[key] = payload
        _save_city_cache_to_disk(cache)
    return payload


def _schedule_city_groups_refresh(pref_key: str) -> bool:
    key = str(pref_key or "").strip().lower()
    if not key:
        return False
    with _CITY_REFRESH_LOCK:
        if key in _CITY_REFRESH_INFLIGHT:
            return False
        _CITY_REFRESH_INFLIGHT.add(key)

    def _worker() -> None:
        try:
            _refresh_city_groups_now(key)
        except Exception as ex:
            logger.warning("HOMES city refresh failed for %s: %s", key, ex)
        finally:
            with _CITY_REFRESH_LOCK:
                _CITY_REFRESH_INFLIGHT.discard(key)

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"homes-city-refresh:{key}",
    ).start()
    return True


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


def _fetch_html(url: str) -> str:
    html = _fetch_html_httpx(url)
    if html and not _looks_like_homes_challenge(html) and "mod-checkList" in html:
        return html
    html2 = _fetch_html_playwright(url)
    if html2 and not _looks_like_homes_challenge(html2) and "mod-checkList" in html2:
        return html2
    return html or html2 or ""


def _parse_city_groups(pref_key: str, html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "lxml")
    root = soup.select_one("div.mod-checkList.area")
    if not root:
        return []

    out: list[dict[str, Any]] = []
    for fs in root.select("fieldset"):
        legend_label = fs.select_one("legend label")
        if not legend_label:
            continue
        group_num_el = legend_label.select_one("span.num")
        group_count = _parse_num(group_num_el.get_text(" ", strip=True) if group_num_el else "")
        group_text = legend_label.get_text(" ", strip=True)
        if group_num_el:
            num_txt = group_num_el.get_text(" ", strip=True)
            group_text = group_text.replace(num_txt, "").strip()
        group_url = ""
        group_a = legend_label.find("a", href=True)
        if group_a:
            group_url = str(group_a.get("href") or "").strip()

        items: list[dict[str, Any]] = []
        for li in fs.select("li"):
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
            city_id = ""
            inp = li.find("input")
            if inp and inp.has_attr("value"):
                city_id = str(inp.get("value") or "").strip()
            enabled = bool(url) and count > 0
            items.append(
                {
                    "id": city_id,
                    "label": label,
                    "count": count,
                    "url": url,
                    "enabled": enabled,
                }
            )

        out.append(
            {
                "id": f"{pref_key}:{len(out)+1}",
                "label": group_text,
                "count": group_count,
                "url": group_url,
                "items": items,
            }
        )
    return out


def homes_kodate_chuko_city_groups(pref_key: str, *, force_refresh: bool = False) -> dict[str, Any]:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")

    with _CITY_CACHE_LOCK:
        cache = _get_city_cache()
        entry = cache.get(key) if isinstance(cache, dict) else None
        if not force_refresh and isinstance(entry, dict):
            fetched_ts = float(entry.get("fetched_ts") or 0)
            if fetched_ts > 0 and time.time() - fetched_ts <= _CITY_CACHE_TTL_SEC:
                return entry
            # Serve stale immediately (to keep UI <0.5s) and refresh in background.
            scheduled = _schedule_city_groups_refresh(key)
            out = dict(entry)
            out["stale"] = True
            out["stale_age_sec"] = max(0.0, time.time() - fetched_ts) if fetched_ts > 0 else None
            out["refresh_scheduled"] = scheduled
            return out

    if force_refresh:
        return _refresh_city_groups_now(key)

    scheduled = _schedule_city_groups_refresh(key)
    return {
        "ok": True,
        "pref": {"id": pref.key, "label": pref.label, "region": pref.region},
        "url": f"https://www.homes.co.jp/kodate/chuko/{key}/city/",
        "fetched_at": "",
        "fetched_ts": 0,
        "groups": [],
        "pending_fetch": True,
        "refresh_scheduled": scheduled,
    }
