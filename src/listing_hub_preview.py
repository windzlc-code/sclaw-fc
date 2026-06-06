"""Best-effort preview text + images for major portal *area hub* pages (e.g. SUUMO /kyushu/)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from src.bsoup import soup_from_html

# 與 crawler 相同之瀏覽器偽裝（避免循環 import）
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7",
}

# SUUMO 地域トップ（単一路径）— 物件一覧ではなくエリア入口だが、og 説明と本文断片が取れることが多い
_SUUMO_AREA_SEGMENTS = frozenset(
    {
        "hokkaido",
        "tohoku",
        "kanto",
        "koshinetsu",
        "tokai",
        "kansai",
        "chugoku",
        "shikoku",
        "kyushu",
        "okinawa",
    }
)


def is_suumo_area_hub_url(url: str) -> bool:
    try:
        p = urlparse((url or "").strip())
        host = (p.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if "suumo.jp" not in host:
            return False
        segs = [s for s in (p.path or "").split("/") if s]
        if len(segs) != 1:
            return False
        return segs[0].lower() in _SUUMO_AREA_SEGMENTS
    except Exception:
        return False


def fetch_hub_page_preview(url: str, *, timeout: float = 14.0) -> tuple[str, list[str]]:
    """
    回傳 (日文／原文系摘要片段, 圖片 URL 列表)。
    失敗時 ("", [])。
    """
    u = (url or "").strip()
    if not u.startswith("http"):
        return "", []
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_BROWSER_HEADERS) as client:
            r = client.get(u)
            r.raise_for_status()
    except Exception:
        return "", []
    soup = soup_from_html(r.text)
    parts: list[str] = []

    def _meta(prop: str, name: str | None = None) -> str:
        tag = soup.find("meta", property=prop) if prop else None
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
        if name:
            tag2 = soup.find("meta", attrs={"name": name})
            if tag2 and tag2.get("content"):
                return str(tag2["content"]).strip()
        return ""

    ogd = _meta("og:description")
    md = _meta("", "description")
    if ogd:
        parts.append(ogd[:900])
    elif md:
        parts.append(md[:900])

    lines: list[str] = []
    for n in soup.select("h1, h2, p, li"):
        t = re.sub(r"\s+", " ", n.get_text(" ", strip=True) or "").strip()
        if len(t) < 28:
            continue
        if "cookie" in t.lower() or "javascript" in t.lower():
            continue
        lines.append(t[:420])
        if len(lines) >= 10:
            break
    if lines:
        parts.append("\n".join(lines)[:2200])

    imgs: list[str] = []
    for sel in ("meta[property='og:image']", "meta[name='twitter:image']"):
        for meta in soup.select(sel):
            c = (meta.get("content") or "").strip()
            if c.startswith("http") and c not in imgs:
                imgs.append(c)
            if len(imgs) >= 6:
                break
        if len(imgs) >= 6:
            break

    blob = "\n\n".join(p for p in parts if p).strip()
    return blob[:2800], imgs[:6]
