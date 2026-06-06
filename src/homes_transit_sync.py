from __future__ import annotations

import logging
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from bs4 import BeautifulSoup

from src.db import get_conn
from src.homes_geo import HOMES_KODATE_CHUKO_PREFS, HomesPref
from src.homes_transit import _looks_like_homes_challenge, homes_kodate_chuko_transit_lines
from src.jp_transit_model import ensure_jp_transit_schema_and_seed
from src.pipeline import _ensure_jp_transit_station_row

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HomesTransitSyncResult:
    pref_key: str
    city_area: str
    lines_seen: int
    lines_upserted: int
    stations_seen: int
    stations_upserted: int
    elapsed_sec: float


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def _line_match_keys(line_name: str) -> list[str]:
    """Return a few normalized keys for fuzzy-ish matching existing jp_trans_line rows."""
    base = _norm(line_name).replace(" ", "")
    keys = [base]
    # Common alt: 京浜東北線 vs 京浜東北・根岸線 (HOMES uses the latter on many pages).
    if "・根岸" in base:
        keys.append(base.replace("・根岸", ""))
    # Common alt: fullwidth slashes/dashes etc are already handled by NFKC, but keep a minimal list.
    keys.extend([k for k in keys if k])
    # De-dup while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _pref_meta(pref_key: str) -> HomesPref:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")
    return pref


def _bulk_fetch_html_playwright(urls: Iterable[str], *, must_contain: str) -> dict[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {}

    from src.portal_http import PORTAL_BROWSER_HEADERS
    from src.portal_property_playwright import default_playwright_state_path

    url_list = [str(u or "").strip() for u in urls if str(u or "").strip()]
    if not url_list:
        return {}

    state_path = default_playwright_state_path()
    storage_state = str(state_path) if state_path.is_file() else None
    ua = str(PORTAL_BROWSER_HEADERS.get("User-Agent") or "").strip() or None
    accept_lang = str(PORTAL_BROWSER_HEADERS.get("Accept-Language") or "ja").strip()

    init_script = """
(() => {
  try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); } catch (e) {}
})();
"""

    out: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
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
            try:
                ctx.route(
                    "**/*",
                    lambda route, request: route.abort()
                    if (request.resource_type in ("image", "stylesheet", "font", "media"))
                    else route.continue_(),
                )
            except Exception:
                pass
            page = ctx.new_page()
            for url in url_list:
                html = ""
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception:
                        pass
                    for _ in range(45):
                        try:
                            page.wait_for_timeout(250)
                        except Exception:
                            pass
                        try:
                            html = page.content() or ""
                        except Exception:
                            html = ""
                        if html and not _looks_like_homes_challenge(html) and must_contain in html:
                            break
                except Exception:
                    html = ""
                out[url] = html or ""
        finally:
            browser.close()
    return out


def sync_homes_kodate_chuko_transit_pref(
    pref_key: str,
    *,
    force_refresh_lines: bool = True,
    include_stations: bool = True,
    max_lines: int | None = None,
) -> HomesTransitSyncResult:
    """
    Scrape HOMES `/kodate/chuko/{pref}/line/` + each line page to backfill our jp_trans_* tables.

    Notes
    - Uses Playwright in a single browser session for station pages (much faster than per-line launches).
    - Keeps `jp_trans_line.line_id` within <= 9999 so `station_id = line_id*1000+n` stays <= 9_999_999.
    """
    start = time.time()
    pref = _pref_meta(pref_key)
    city_area = str(pref.label or "").strip()
    if not city_area:
        raise ValueError("pref label missing")

    lines_payload = homes_kodate_chuko_transit_lines(pref.key, force_refresh=bool(force_refresh_lines))
    groups = lines_payload.get("groups") if isinstance(lines_payload, dict) else None
    items: list[dict[str, Any]] = []
    for g in groups or []:
        for it in (g or {}).get("items") or []:
            if not isinstance(it, dict):
                continue
            if not it.get("enabled"):
                continue
            if not (it.get("line_name") and it.get("url")):
                continue
            items.append(it)

    if max_lines is not None:
        items = items[: max(0, int(max_lines))]

    lines_seen = len(items)
    lines_upserted = 0
    stations_seen = 0
    stations_upserted = 0

    with get_conn() as conn:
        ensure_jp_transit_schema_and_seed(conn)

        # Load existing lines for this pref into a match map.
        existing_rows = conn.execute(
            "SELECT line_id, trans_type, line_name FROM jp_trans_line WHERE city_area = ?",
            (city_area,),
        ).fetchall()
        by_key: dict[str, int] = {}
        for r in existing_rows or []:
            lid = int(r["line_id"] or 0)
            name = str(r["line_name"] or "")
            for k in _line_match_keys(name):
                by_key.setdefault(k, lid)

        mx_row = conn.execute("SELECT MAX(line_id) FROM jp_trans_line").fetchone()
        next_line_id = int((mx_row[0] if mx_row and mx_row[0] is not None else 0) or 0) + 1

        def _ensure_line_id(line_name: str, trans_type: str) -> int:
            nonlocal next_line_id, lines_upserted
            keys = _line_match_keys(line_name)
            for k in keys:
                if k in by_key:
                    lid = int(by_key[k] or 0)
                    if lid > 0:
                        # Optionally align label/type to HOMES for this prefecture.
                        try:
                            conn.execute(
                                "UPDATE jp_trans_line SET trans_type = ?, line_name = ? WHERE line_id = ?",
                                (str(trans_type or ""), str(line_name or ""), int(lid)),
                            )
                        except Exception:
                            pass
                        return lid

            lid = int(next_line_id)
            if lid <= 0 or lid > 9999:
                raise RuntimeError("jp_trans_line id capacity exceeded (need <= 9999)")
            next_line_id += 1
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO jp_trans_line (line_id, city_area, trans_type, line_name, line_color, main_ward)
                    VALUES (?, ?, ?, ?, '', '')
                    """,
                    (int(lid), city_area, str(trans_type or ""), str(line_name or "")),
                )
            except Exception:
                pass
            for k in keys:
                by_key.setdefault(k, lid)
            lines_upserted += 1
            return lid

        # Ensure line rows exist first.
        for it in items:
            _ensure_line_id(str(it.get("line_name") or ""), str(it.get("trans_type") or ""))

        if not include_stations:
            conn.commit()
            return HomesTransitSyncResult(
                pref_key=pref.key,
                city_area=city_area,
                lines_seen=lines_seen,
                lines_upserted=lines_upserted,
                stations_seen=0,
                stations_upserted=0,
                elapsed_sec=time.time() - start,
            )

        # Bulk fetch station pages in a single Playwright session.
        url_to_html = _bulk_fetch_html_playwright([str(it.get("url") or "") for it in items], must_contain="mod-checkList")

        for it in items:
            line_name = str(it.get("line_name") or "")
            trans_type = str(it.get("trans_type") or "")
            url = str(it.get("url") or "").strip()
            if not line_name or not url:
                continue
            lid = _ensure_line_id(line_name, trans_type)
            html = url_to_html.get(url) or ""
            if _looks_like_homes_challenge(html) or "mod-checkList" not in html:
                logger.warning("HOMES station page blocked/unexpected for %s (%s)", pref.key, url[:120])
                continue

            # Parse station names by reusing the same CSS hooks used in homes_transit.
            soup = BeautifulSoup(html, "lxml")
            root = soup.select_one("div.mod-checkList.eki")
            if not root:
                continue

            for li in root.select("li"):
                label_el = li.select_one("label")
                if not label_el:
                    continue
                a = label_el.find("a")
                label = a.get_text(" ", strip=True) if a else label_el.get_text(" ", strip=True)
                # Remove counts and trailing '駅'.
                label = _norm(label)
                label = label.replace("駅", "").strip()
                if not label:
                    continue
                stations_seen += 1
                before = conn.execute(
                    "SELECT 1 FROM jp_trans_station WHERE line_id = ? AND station_name = ? LIMIT 1",
                    (int(lid), label),
                ).fetchone()
                _ensure_jp_transit_station_row(conn, line_id=lid, station_name=label, pref_hint=city_area, addr_hint="")
                after = conn.execute(
                    "SELECT 1 FROM jp_trans_station WHERE line_id = ? AND station_name = ? LIMIT 1",
                    (int(lid), label),
                ).fetchone()
                if not before and after:
                    stations_upserted += 1

        conn.commit()

    return HomesTransitSyncResult(
        pref_key=pref.key,
        city_area=city_area,
        lines_seen=lines_seen,
        lines_upserted=lines_upserted,
        stations_seen=stations_seen,
        stations_upserted=stations_upserted,
        elapsed_sec=time.time() - start,
    )
