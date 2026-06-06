"""Shared HTTP headers for Japanese portal scraping helpers.

Keep this module dependency-free to avoid import cycles across crawler components.
"""

from __future__ import annotations

PORTAL_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

