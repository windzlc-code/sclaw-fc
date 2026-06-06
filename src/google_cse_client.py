"""Google Custom Search JSON API (Programmable Search Engine)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from src.config import GOOGLE_CSE_API_KEY, GOOGLE_CSE_CX

GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


def is_google_cse_configured() -> bool:
    return bool(GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX)


def search_cse(query: str, *, num: int = 10, start: int = 1) -> list[dict[str, Any]]:
    """
    Returns a list of {title, link, snippet, displayLink} for one query page.
    `num` is capped at 10 (Google API limit per request).
    """
    if not is_google_cse_configured():
        raise RuntimeError("Google CSE is not configured (GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX).")
    q = (query or "").strip()
    if not q:
        return []
    safe_num = max(1, min(10, int(num)))
    safe_start = max(1, min(91, int(start)))
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_CX,
        "q": q,
        "num": safe_num,
        "start": safe_start,
    }
    url = f"{GOOGLE_CSE_ENDPOINT}?{urlencode(params)}"
    with httpx.Client(timeout=45.0) as client:
        r = client.get(url)
        if r.status_code != 200:
            try:
                detail = r.json()
            except Exception:
                detail = {"raw": r.text[:500]}
            raise RuntimeError(f"Google CSE HTTP {r.status_code}: {detail}")
        data = r.json()
    items = data.get("items") or []
    out: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        title = row.get("title") or row.get("htmlTitle") or ""
        link = row.get("link") or ""
        snippet = row.get("snippet") or ""
        disp = row.get("displayLink") or ""
        out.append(
            {
                "title": str(title)[:300],
                "link": str(link)[:2000],
                "snippet": str(snippet)[:800],
                "displayLink": str(disp)[:200],
            }
        )
    return out
