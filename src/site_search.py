from urllib.parse import quote_plus, urlparse

import httpx

from src.bsoup import soup_from_html


def search_site_links(keyword: str, domains: list[str], limit: int = 12) -> list[dict]:
    q = (keyword or "").strip()
    site_clause = " OR ".join([f"site:{d}" for d in domains if d.strip()])
    search_q = f"{q} {site_clause}".strip()
    url = f"https://duckduckgo.com/html/?q={quote_plus(search_q)}"

    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()
    soup = soup_from_html(resp.text)

    rows = []
    for item in soup.select(".result"):
        a = item.select_one("a.result__a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        title = a.get_text(" ").strip()
        snippet_el = item.select_one(".result__snippet")
        snippet = snippet_el.get_text(" ").strip() if snippet_el else ""
        if not href or not title:
            continue
        host = (urlparse(href).netloc or "").lower()
        if not any(d in host for d in domains):
            continue
        rows.append(
            {
                "title": title,
                "snippet": snippet,
                "url": href,
                "domain": host,
            }
        )
        if len(rows) >= limit:
            break
    return rows
