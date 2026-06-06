from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.bsoup import soup_from_html
from src.portal_http import PORTAL_BROWSER_HEADERS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SuumoTransitLine:
    pref_key: str
    rn: int
    trans_type: str
    line_name: str
    url: str
    count: int


@dataclass(frozen=True)
class SuumoTransitStation:
    pref_key: str
    rn: int
    station_code: int
    station_name: str
    url: str


_RE_PARENS_COUNT = re.compile(r"[\(（]\s*([\d,]+)\s*[\)）]")
_RE_EK_CODE = re.compile(r"/ek_(\d+)/", re.I)


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def _host_is_suumo(url: str) -> bool:
    try:
        host = (urlparse(str(url or "")).netloc or "").lower()
    except Exception:
        return False
    return host.endswith("suumo.jp")


def _infer_trans_type(line_name: str) -> str:
    s = _norm(line_name).replace(" ", "")
    if not s:
        return ""
    if s.startswith("JR") or s.startswith("ＪＲ"):
        return "JR"
    if "メトロ" in s or "地下鉄" in s:
        return "地下鉄"
    if "モノレール" in s:
        return "モノレール"
    if any(k in s for k in ("電鉄", "鉄道", "急行", "本線", "線")):
        return "私鉄"
    return "その他"


def _parse_count_and_name(raw: str) -> tuple[str, int]:
    txt = _norm(raw)
    if not txt:
        return "", 0
    m = _RE_PARENS_COUNT.search(txt)
    count = 0
    if m:
        try:
            count = int(str(m.group(1) or "0").replace(",", ""))
        except ValueError:
            count = 0
        txt = _RE_PARENS_COUNT.sub("", txt).strip()
    return txt, max(0, count)


def _http_get_html(url: str) -> str:
    if not _host_is_suumo(url):
        return ""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers=dict(PORTAL_BROWSER_HEADERS),
        ) as client:
            res = client.get(url)
            if res.status_code >= 400:
                return ""
            return res.text or ""
    except Exception:
        return ""


def suumo_chukoikkodate_ensen_lines(pref_key: str) -> list[SuumoTransitLine]:
    """
    Scrape SUUMO chuko-ikkodate "沿線から探す" page for one prefecture.

    Example
      https://suumo.jp/chukoikkodate/tokushima/ensen/
    """
    pref = str(pref_key or "").strip().lower()
    if not pref:
        return []
    base_url = f"https://suumo.jp/chukoikkodate/{pref}/ensen/"
    html = _http_get_html(base_url)
    if not html:
        return []
    soup = soup_from_html(html)

    # Map line-name -> detail URL (en_* page).
    link_map: dict[str, str] = {}
    for a in soup.select(f'a[href^="/chukoikkodate/{pref}/en_"]'):
        name = _norm(a.get_text(" ", strip=True))
        if not name:
            continue
        href = str(a.get("href") or "").strip()
        if not href:
            continue
        full = urljoin(base_url, href)
        if not _host_is_suumo(full):
            continue
        link_map.setdefault(name.replace(" ", ""), full.split("#", 1)[0])

    out: list[SuumoTransitLine] = []
    seen: set[tuple[int, str]] = set()
    for inp in soup.select('input[name="rn"][value]'):
        raw_rn = str(inp.get("value") or "").strip()
        if not raw_rn.isdigit():
            continue
        rn = int(raw_rn)
        iid = str(inp.get("id") or "").strip()
        if not iid:
            continue
        lab = soup.find("label", attrs={"for": iid})
        if not lab:
            continue
        name_raw = lab.get_text(" ", strip=True)
        line_name, count = _parse_count_and_name(name_raw)
        if not line_name:
            continue
        key = (rn, line_name)
        if key in seen:
            continue
        seen.add(key)
        url = link_map.get(_norm(line_name).replace(" ", ""), "")
        trans_type = _infer_trans_type(line_name)
        out.append(
            SuumoTransitLine(
                pref_key=pref,
                rn=rn,
                trans_type=trans_type,
                line_name=line_name,
                url=url,
                count=count,
            )
        )

    # Prefer enabled/real lines: with URL and/or positive counts.
    out.sort(key=lambda x: (0 if (x.count > 0) else 1, 0 if x.url else 1, -int(x.count or 0), x.rn))
    return out


def suumo_chukoikkodate_line_stations(pref_key: str, *, line_url: str, rn: int, line_name: str) -> list[SuumoTransitStation]:
    """
    Scrape station links for one line from a SUUMO en_* page.

    Note: the page includes an "otherlink" accordion with all lines/stations in the prefecture;
    we select the block whose title matches `line_name`.
    """
    pref = str(pref_key or "").strip().lower()
    url = str(line_url or "").strip()
    if not pref or not url:
        return []
    if not _host_is_suumo(url):
        return []
    html = _http_get_html(url)
    if not html:
        return []
    soup = soup_from_html(html)

    target_key = _norm(line_name).replace(" ", "")
    if not target_key:
        return []

    items = soup.select("div.otherlink div.otherlink_item")
    if not items:
        return []

    stations: list[SuumoTransitStation] = []
    seen: set[int] = set()

    for it in items:
        title_el = it.select_one("div.otherlink_item-title")
        title = _norm(title_el.get_text(" ", strip=True) if title_el else "")
        if not title:
            continue
        if _norm(title).replace(" ", "") != target_key:
            continue
        for a in it.select('ul.otherlink_item-body a[href]'):
            href = str(a.get("href") or "").strip()
            if not href:
                continue
            m = _RE_EK_CODE.search(href)
            if not m:
                continue
            try:
                station_code = int(m.group(1))
            except ValueError:
                continue
            if station_code in seen:
                continue
            name = _norm(a.get_text(" ", strip=True))
            if not name:
                continue
            full = urljoin(url, href).split("#", 1)[0]
            if not _host_is_suumo(full):
                continue
            seen.add(station_code)
            stations.append(
                SuumoTransitStation(
                    pref_key=pref,
                    rn=int(rn or 0),
                    station_code=station_code,
                    station_name=name,
                    url=full,
                )
            )
        break

    # Stable ordering: by station name (SUUMO UI order is fine but can differ per page)
    stations.sort(key=lambda x: _norm(x.station_name).lower())
    return stations


def debug_suumo_transit_pref(pref_key: str) -> dict[str, Any]:
    """Small helper for ad-hoc diagnostics (keeps output JSON-safe)."""
    lines = suumo_chukoikkodate_ensen_lines(pref_key)
    payload = []
    for ln in lines[:12]:
        payload.append(
            {
                "rn": int(ln.rn),
                "trans_type": str(ln.trans_type or ""),
                "line_name": str(ln.line_name or ""),
                "count": int(ln.count or 0),
                "url": str(ln.url or ""),
            }
        )
    return {"pref": str(pref_key or ""), "lines": payload}
