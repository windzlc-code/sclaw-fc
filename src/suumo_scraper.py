from urllib.parse import urljoin, urlparse

import httpx

from src.bsoup import soup_from_html

SUUMO_TABS = {
    "fig24": {
        "name": "SUUMO 新築",
        "base_url": "https://suumo.jp/ms/shinchiku/kanto/",
        "note": "關東新築分譲マンション",
        "default_keyword": "新築 マンション",
    },
    "fig3": {
        "name": "SUUMO 中古",
        "base_url": "https://suumo.jp/ms/chuko/kanto/",
        "note": "關東中古マンション購入情報",
        "default_keyword": "中古 マンション",
    },
    "fig5": {
        "name": "SUUMO 關東",
        "base_url": "https://suumo.jp/kanto/",
        "note": "關東住宅與不動產綜合入口",
        "default_keyword": "関東 不動産",
    },
}


def _score(keyword: str, title: str, snippet: str, href: str) -> int:
    q = (keyword or "").strip().lower()
    t = (title or "").lower()
    s = (snippet or "").lower()
    h = (href or "").lower()
    score = 0
    if q and (q in t or q in s):
        score += 6
    if any(x in t for x in ["新築", "中古", "マンション", "一戸建", "賃貸", "物件"]):
        score += 3
    if any(x in h for x in ["/ms/", "/chuko/", "/shinchiku/", "/chintai/"]):
        score += 2
    return score


def fetch_suumo_tab_items(tab_key: str, keyword: str, limit: int = 18) -> tuple[dict, list[dict]]:
    tab = SUUMO_TABS.get(tab_key, SUUMO_TABS["fig24"])
    base_url = tab["base_url"]
    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        resp = client.get(base_url)
        resp.raise_for_status()
    soup = soup_from_html(resp.text)

    rows = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        full = urljoin(base_url, href)
        host = (urlparse(full).netloc or "").lower()
        if "suumo.jp" not in host:
            continue
        title = a.get_text(" ").strip()
        if len(title) < 3:
            continue
        parent = a.parent.get_text(" ").strip() if a.parent else ""
        snippet = parent[:200]
        img_el = a.find("img")
        if not img_el and a.parent:
            img_el = a.parent.find("img")
        if not img_el and a.parent and getattr(a.parent, "parent", None):
            img_el = a.parent.parent.find("img")
        img = ""
        if img_el:
            src = (
                img_el.get("src")
                or img_el.get("data-src")
                or img_el.get("data-original")
                or img_el.get("data-lazy")
                or ""
            )
            if src:
                img = urljoin(base_url, src)
        rows.append(
            {
                "url": full,
                "title_jp": title,
                "snippet_jp": snippet,
                "image_url": img,
                "score": _score(keyword=keyword, title=title, snippet=snippet, href=full),
            }
        )

    # dedupe by url, keep highest score
    by_url = {}
    for row in rows:
        old = by_url.get(row["url"])
        if not old or row["score"] > old["score"]:
            by_url[row["url"]] = row

    selected = sorted(by_url.values(), key=lambda x: x["score"], reverse=True)[:limit]
    return tab, selected
