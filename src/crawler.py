from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.bsoup import soup_from_html
from src.source_registry import get_enabled_sources, load_sources

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 物件詳情頁主機（七大門戶；含 suumo.com 轉址／圖床來源頁）
# 有 LISTING_HUB_PAGES 者另支援「列表 hub → 詳情」批次爬；其餘仍可用貼上詳情 URL 入庫。
PORTAL_LISTING_HOSTS = frozenset(
    {
        "suumo.jp",
        "suumo.com",
        "homes.co.jp",
        "athome.co.jp",
        "realestate.yahoo.co.jp",
        "realestate.rakuten.co.jp",
        "yes1.co.jp",
        "yes-station.jp",
        "oheya-su.jp",
        "oheyasuu.com",
    }
)


@dataclass
class CrawledItem:
    source_name: str
    source_category: str
    source_url: str
    item_url: str
    title_original: str
    body_original: str
    language: str
    published_at: str | None
    access_status: str = "public"
    access_note: str = ""
    image_urls: str = ""
    content_kind: str = ""


def _safe_text(value: str | None) -> str:
    return (value or "").strip()


def _norm_host(netloc: str) -> str:
    h = (netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def _collect_links(url: str, limit: int = 8) -> list[tuple[str, str]]:
    from src.link_quality import url_is_low_value_for_link_list

    with httpx.Client(timeout=20, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        resp = client.get(url)
        resp.raise_for_status()
        soup = soup_from_html(resp.text)
        links: list[tuple[str, str]] = []
        for a in soup.select("a[href]"):
            href = _safe_text(a.get("href"))
            text = _safe_text(a.get_text(" "))
            if not href or not text:
                continue
            if href.startswith("/"):
                href = url.rstrip("/") + href
            if not href.startswith("http"):
                continue
            if url_is_low_value_for_link_list(href):
                continue
            if len(text) < 6:
                continue
            links.append((text[:120], href))
            if len(links) >= limit:
                break
        return links


def _srcset_urls(value: str, base_url: str) -> list[str]:
    candidates: list[tuple[float, int, str]] = []
    for chunk in str(value or "").split(","):
        part = chunk.strip().split()
        if not part:
            continue
        u = urljoin(base_url, part[0]).split("#", 1)[0]
        if not u.startswith("http"):
            continue
        rank = 1.0
        if len(part) > 1:
            desc = part[1].strip().lower()
            try:
                if desc.endswith("w"):
                    rank = float(desc[:-1])
                elif desc.endswith("x"):
                    rank = float(desc[:-1]) * 1000.0
            except Exception:
                rank = 1.0
        candidates.append((rank, len(candidates), u))
    out: list[str] = []
    for _, _, u in sorted(candidates, key=lambda row: (row[0], -row[1]), reverse=True):
        if u not in out:
            out.append(u)
    return out


def _normalize_source_image_high_res(url: str) -> str:
    raw = str(url or "").strip()
    if not raw.startswith("http"):
        return raw
    try:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
        if "suumo." in host and "/gazo/bukken/" in path and any(
            path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")
        ):
            raw_path = parsed.path or ""
            idx = raw_path.lower().find("/gazo/")
            if idx >= 0:
                src = raw_path[idx + 1 :].lstrip("/")
                return "https://img01.suumo.com/jj/resizeImage?" + urlencode(
                    {"src": src, "w": "1600", "h": "1200"}
                )
        if "suumo." in host and "resizeimage" in path:
            q = {str(k): str(v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
            if str(q.get("src") or "").strip():
                q["w"] = "1600"
                q["h"] = "1200"
                return parsed._replace(query=urlencode(list(q.items()), doseq=True)).geturl()
        if ("homes.jp" in host or "homes.co.jp" in host) and ("image.php" in path or "/smallimg/" in path):
            q = {str(k): str(v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
            q["width"] = "1600"
            q["height"] = "1600"
            return parsed._replace(query=urlencode(list(q.items()), doseq=True)).geturl()
    except Exception:
        return raw
    return raw


def _jsonld_image_urls(soup: BeautifulSoup, base_url: str, limit: int = 16) -> list[str]:
    out: list[str] = []

    def walk(value) -> None:
        if len(out) >= limit:
            return
        if isinstance(value, dict):
            for key, val in value.items():
                if str(key).lower() in {"image", "images", "photo", "thumbnailurl"}:
                    walk(val)
                elif isinstance(val, (dict, list)):
                    walk(val)
        elif isinstance(value, list):
            for item in value:
                walk(item)
                if len(out) >= limit:
                    break
        elif isinstance(value, str):
            u = urljoin(base_url, value.strip()).split("#", 1)[0]
            if u.startswith("http") and u not in out:
                out.append(u)

    for script in soup.select('script[type="application/ld+json"]'):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out[:limit]


def _image_score(url: str, attrs: dict[str, str] | None = None) -> int:
    lu = str(url or "").lower()
    if not lu.startswith("http"):
        return -999
    bad = (
        "logo",
        "icon",
        "sprite",
        "spacer",
        "blank",
        "pixel",
        "avatar",
        "banner",
        "ads",
        "barcode",
        "qr",
        "loading",
        "placeholder",
        "sns",
        "share",
    )
    score = 0
    if any(x in lu for x in bad):
        score -= 260
    good = (
        "bukken",
        "property",
        "estate",
        "mansion",
        "house",
        "room",
        "living",
        "kitchen",
        "interior",
        "exterior",
        "gaikan",
        "naikan",
        "madori",
        "floor",
        "layout",
        "photo",
        "image",
        "gallery",
        "resizeimage",
        "smallimg",
    )
    if any(x in lu for x in good):
        score += 120
    if any(lu.split("?", 1)[0].endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
        score += 42
    attrs = attrs or {}
    for key in ("width", "data-width", "naturalwidth"):
        try:
            width = int(re.sub(r"\D+", "", str(attrs.get(key) or "")) or "0")
        except Exception:
            width = 0
        if width >= 900:
            score += 30
            break
        if width and width <= 180:
            score -= 45
            break
    alt_title = f"{attrs.get('alt','')} {attrs.get('title','')} {attrs.get('class','')}".lower()
    if any(x in alt_title for x in ("間取り", "外観", "内観", "リビング", "キッチン", "物件", "photo", "gallery")):
        score += 60
    return score


def _collect_image_urls(soup: BeautifulSoup, base_url: str, limit: int = 24) -> list[str]:
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    def push(raw: str, attrs: dict[str, str] | None = None, boost: int = 0) -> None:
        s = _safe_text(raw).replace("&amp;", "&")
        if not s:
            return
        if s.startswith("//"):
            parsed = urlparse(base_url)
            s = f"{parsed.scheme or 'https'}:{s}"
        u = urljoin(base_url, s).split("#", 1)[0].rstrip(").,;\"'")
        u = _normalize_source_image_high_res(u)
        if not u.startswith("http") or u in seen:
            return
        score = _image_score(u, attrs) + boost
        if score <= -120:
            return
        seen.add(u)
        scored.append((score, len(scored), u))

    for u in _jsonld_image_urls(soup, base_url, limit=limit):
        push(u, boost=70)
    for meta in soup.select("meta[property='og:image'][content], meta[name='twitter:image'][content], meta[itemprop='image'][content]"):
        push(str(meta.get("content") or ""), boost=80)
    for media in soup.select("video[poster], source[src], source[srcset], picture source[srcset], picture source[data-srcset]"):
        attrs = {str(k).lower(): str(v) for k, v in (media.attrs or {}).items()}
        push(str(media.get("poster") or media.get("src") or ""), attrs, boost=25)
        for attr in ("srcset", "data-srcset"):
            for u in _srcset_urls(str(media.get(attr) or ""), base_url):
                push(u, attrs, boost=25)
    selector = (
        "img[src], img[data-src], img[data-original], img[data-lazy-src], img[data-srcset], img[srcset], "
        "[style*='background-image'], [data-bg], [data-background], [data-background-image]"
    )
    for node in soup.select(selector):
        attrs = {str(k).lower(): str(v) for k, v in (node.attrs or {}).items()}
        for attr in ("src", "data-src", "data-original", "data-lazy-src", "data-bg", "data-background", "data-background-image"):
            push(str(node.get(attr) or ""), attrs)
        for attr in ("srcset", "data-srcset"):
            for u in _srcset_urls(str(node.get(attr) or ""), base_url):
                push(u, attrs)
        style = str(node.get("style") or "")
        for m in re.finditer(r"url\((['\"]?)(.*?)\1\)", style, re.I):
            push(m.group(2), attrs)

    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    out: list[str] = []
    for _, _, u in scored:
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def _page_title(soup: BeautifulSoup) -> str:
    for selector, attr in (
        ("meta[property='og:title'][content]", "content"),
        ("meta[name='twitter:title'][content]", "content"),
    ):
        node = soup.select_one(selector)
        if node and _safe_text(node.get(attr)):
            return _safe_text(node.get(attr))[:180]
    if soup.title:
        title = _safe_text(soup.title.get_text(" "))
        if title:
            return title[:180]
    h1 = soup.find("h1")
    return _safe_text(h1.get_text(" ") if h1 else "")[:180]


def _page_description(soup: BeautifulSoup) -> str:
    for selector in ("meta[name='description'][content]", "meta[property='og:description'][content]"):
        node = soup.select_one(selector)
        if node and _safe_text(node.get("content")):
            return _safe_text(node.get("content"))[:900]
    return ""


def _content_lines(soup: BeautifulSoup, limit: int = 80) -> list[str]:
    root = soup.find("main") or soup.find("article") or soup.find("body") or soup
    lines: list[str] = []
    seen: set[str] = set()
    for bad in root.select("script, style, noscript, svg, nav, footer, header, form"):
        bad.decompose()
    for node in root.select("h1, h2, h3, p, li, th, td, dt, dd"):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if len(text) < 12:
            continue
        low = text.lower()
        if any(x in low for x in ("cookie", "javascript", "privacy policy", "copyright", "all rights reserved")):
            continue
        key = low[:180]
        if key in seen:
            continue
        seen.add(key)
        lines.append(text[:420])
        if len(lines) >= limit:
            break
    return lines


def _fetch_generic_page_preview(url: str) -> tuple[str, str, list[str]]:
    with httpx.Client(timeout=25, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        resp = client.get(url)
        resp.raise_for_status()
    soup = soup_from_html(resp.text)
    title = _page_title(soup)
    desc = _page_description(soup)
    lines = _content_lines(soup, limit=90)
    images = _collect_image_urls(soup, url, limit=24)
    sections: list[str] = []
    if desc:
        sections.append("[頁面摘要]\n" + desc)
    if lines:
        sections.append("[正文重點]\n" + "\n".join(f"- {line}" for line in lines[:70]))
    sections.append(
        "[爬文品質]\n"
        f"- text_lines: {len(lines)}\n"
        f"- image_count: {len(images)}\n"
        f"- source_url: {url}"
    )
    if images:
        sections.append("[素材圖片]\n" + "\n".join(images[:18]))
    return title, "\n\n".join(sections), images


def _crawl_sources(sources: list[dict], per_source_limit: int = 8) -> list[CrawledItem]:
    from src.listing_hub_preview import fetch_hub_page_preview, is_suumo_area_hub_url

    crawled: list[CrawledItem] = []
    now = datetime.now(timezone.utc).isoformat()
    for source in sources:
        try:
            links = _collect_links(source["url"], limit=per_source_limit)
            for title, href in links:
                tail = (
                    f"來源網站：{source['name']}\n"
                    "用途：僅做資訊摘要、趨勢觀察與制度導覽，不直接複製原文。"
                )
                snippet = ""
                imgs: list[str] = []
                if is_suumo_area_hub_url(href):
                    try:
                        snippet, imgs = fetch_hub_page_preview(href)
                    except Exception:
                        snippet, imgs = "", []
                else:
                    try:
                        detail_title, snippet, imgs = _fetch_generic_page_preview(href)
                        if detail_title:
                            title = detail_title
                    except Exception:
                        snippet, imgs = "", []
                if snippet.strip():
                    body = f"{title}\n\n{snippet.strip()[:5200]}\n\n{tail}"
                else:
                    body = f"{title}\n\n{tail}"
                img_join = "\n".join(imgs) if imgs else ""
                crawled.append(
                    CrawledItem(
                        source_name=source["name"],
                        source_category=source["category"],
                        source_url=source["url"],
                        item_url=href,
                        title_original=title,
                        body_original=body,
                        language="ja",
                        published_at=now,
                        access_status="public",
                        access_note="",
                        image_urls=img_join,
                        content_kind="",
                    )
                )
        except Exception:
            continue
    return crawled


def crawl_seed_items(per_source_limit: int = 8) -> list[CrawledItem]:
    return _crawl_sources(get_enabled_sources(), per_source_limit=per_source_limit)


def crawl_one_source(url: str, per_source_limit: int = 8, search_query: str = "") -> list[CrawledItem]:
    """Match registry row by site hostname (ignores scheme / www / trailing slash)."""
    from src.portal_property_crawl import crawl_portal_listings

    ru = urlparse(url.strip())
    target_host = _norm_host(ru.netloc)
    matched: list[dict] = []
    for x in load_sources():
        pu = urlparse(str(x.get("url", "")).strip())
        if _norm_host(pu.netloc) == target_host:
            matched.append(x)
    if not matched:
        return []
    if target_host in PORTAL_LISTING_HOSTS:
        source_for_crawl = dict(matched[0])
        if (ru.path and ru.path != "/") or ru.query:
            source_for_crawl["url"] = url.strip()
        portal_items = crawl_portal_listings(
            source_for_crawl, per_source_limit, search_query=(search_query or "").strip()
        )
        # 門戶來源只接受「列表頁→物件詳情」結果；不再回退為首頁隨機連結，
        # 避免公告頁/品牌頁被誤當作物件導致欄位大量缺值。
        return portal_items
    return _crawl_sources(matched, per_source_limit=per_source_limit)


def crawl_manual_links(path: str = "config/manual_links.txt") -> list[CrawledItem]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    urls = [x.strip() for x in file_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    out: list[CrawledItem] = []
    now = datetime.now(timezone.utc).isoformat()

    with httpx.Client(timeout=20, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        for url in urls:
            title = "需授權或無法抓取"
            body = f"來源連結：{url}\n用途：供人工補充摘要與翻譯整理。"
            images: list[str] = []
            access_status = "restricted"
            access_note = "需要授權或登入"
            source_name = "使用者提供來源"
            source_category = "手動來源"
            if "athome.co.jp" in url:
                source_name = "at home"
                source_category = "大型房仲"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                soup = soup_from_html(resp.text)
                page_title = _safe_text(soup.title.get_text(" ") if soup.title else "")
                if page_title:
                    title = page_title[:120]
                body = (
                    f"{title}\n\n來源連結：{url}\n"
                    "用途：僅做資訊摘要、趨勢觀察與制度導覽，不直接複製原文。"
                )
                lines = _content_lines(soup, limit=90)
                images = _collect_image_urls(soup, url, limit=24)
                if lines or images:
                    img_block = "\n".join(images)
                    body = (
                        f"{title}\n\n"
                        f"[正文重點]\n" + "\n".join(f"- {line}" for line in lines[:70]) + "\n\n"
                        f"[素材圖片]\n{img_block}\n\n"
                        f"[爬文品質]\n- text_lines: {len(lines)}\n- image_count: {len(images)}\n- source_url: {url}\n"
                    )
                access_status = "public"
                access_note = ""
            except Exception:
                pass

            out.append(
                CrawledItem(
                    source_name=source_name,
                    source_category=source_category,
                    source_url=url,
                    item_url=url,
                    title_original=title,
                    body_original=body,
                    language="ja",
                    published_at=now,
                    access_status=access_status,
                    access_note=access_note,
                    image_urls="\n".join(images),
                    content_kind="manual_url",
                )
            )
    return out


def crawl_item_url(url: str) -> list[CrawledItem]:
    target = _safe_text(url)
    if not target:
        return []
    now = datetime.now(timezone.utc).isoformat()
    pu = urlparse(target)
    host = _norm_host(pu.netloc)
    src = next((x for x in load_sources() if _norm_host(urlparse(str(x.get("url", "")).strip()).netloc) == host), None)
    source_name = str((src or {}).get("name") or pu.netloc or "指定來源")
    source_category = str((src or {}).get("category") or "手動來源")
    source_url = str((src or {}).get("url") or f"{pu.scheme}://{pu.netloc}")
    # 僅在確認為「單一物件詳情」時標為 jp_listing；列表／駅別 ek_* hub 走下方通用摘要
    content_kind = "manual_url"

    if "tiktok.com" in host:
        try:
            from src.tiktok_video_crawl import build_tiktok_knowledge_body, crawl_tiktok_video_metadata

            meta = crawl_tiktok_video_metadata(target)
            body = build_tiktok_knowledge_body(meta)
            cover = str(meta.get("cover") or "").strip()
            return [
                CrawledItem(
                    source_name=str(meta.get("source_name") or source_name or "TikTok"),
                    source_category=str(meta.get("source_category") or "社群影片知識"),
                    source_url=str(meta.get("source_url") or source_url),
                    item_url=str(meta.get("item_url") or target),
                    title_original=str(meta.get("title") or "TikTok 日本房地產影片知識")[:200],
                    body_original=body,
                    language="zh-Hant",
                    published_at=str(meta.get("published_at") or now),
                    access_status="public",
                    access_note="tiktok_video_metadata",
                    image_urls=cover,
                    content_kind="social_video_knowledge",
                )
            ]
        except Exception as exc:
            title = "TikTok 影片需重新擷取"
            body = (
                "TikTok 影片知識來源\n\n"
                f"[來源網址]\n{target}\n\n"
                f"[擷取狀態]\n- 無法解析 TikTok metadata：{str(exc)[:240]}\n"
                "- 可稍後重新抓取，或貼上影片文案/字幕後入庫。\n"
            )
            return [
                CrawledItem(
                    source_name="TikTok",
                    source_category="社群影片知識",
                    source_url="https://www.tiktok.com/",
                    item_url=target,
                    title_original=title,
                    body_original=body,
                    language="zh-Hant",
                    published_at=now,
                    access_status="restricted",
                    access_note=f"tiktok_metadata_failed:{str(exc)[:180]}",
                    image_urls="",
                    content_kind="social_video_knowledge",
                )
            ]

    # 門戶物件詳情頁：與列表爬蟲相同邏輯（保留 resize 圖 query、較完整摘要區塊）
    if host in PORTAL_LISTING_HOSTS:
        try:
            from src.portal_property_crawl import (
                _host_key,
                _property_url_predicate,
                coerce_listing_display_title,
                fetch_property_detail,
            )

            if not _property_url_predicate(_host_key(pu.netloc), target):
                raise ValueError("not a portal single-property detail URL")

            with httpx.Client(timeout=25, follow_redirects=True, headers=BROWSER_HEADERS) as client:
                title, body_original, imgs = fetch_property_detail(client, target)
            img_join = "\n".join(imgs) if imgs else ""
            return [
                CrawledItem(
                    source_name=source_name,
                    source_category=source_category,
                    source_url=source_url,
                    item_url=target,
                    title_original=coerce_listing_display_title(title, target)[:200],
                    body_original=body_original,
                    language="ja",
                    published_at=now,
                    access_status="public",
                    access_note="",
                    image_urls=img_join,
                    content_kind="jp_listing",
                )
            ]
        except Exception:
            pass

    title = "需授權或無法抓取"
    body = f"來源連結：{target}\n用途：供人工補充摘要與翻譯整理。"
    access_status = "restricted"
    access_note = "需要授權或登入"
    images: list[str] = []
    try:
        with httpx.Client(timeout=25, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            resp = client.get(target)
            resp.raise_for_status()
            soup = soup_from_html(resp.text)
            page_title = _safe_text(soup.title.get_text(" ") if soup.title else "")
            if page_title:
                title = page_title[:180]
            text_lines = []
            for n in soup.select("h1, h2, p, li"):
                t = _safe_text(n.get_text(" "))
                if len(t) >= 16:
                    text_lines.append(t)
                if len(text_lines) >= 20:
                    break
            snippet = " ".join(text_lines)[:1800]
            images = _collect_image_urls(soup, target, limit=14)
            img_block = "\n".join(images) if images else "（無）"
            body = (
                f"{title}\n\n{snippet}\n\n"
                f"[來源網址]\n{target}\n\n"
                f"[圖片網址]\n{img_block}\n\n"
                "用途：僅做資訊摘要、趨勢觀察與制度導覽，不直接複製原文。"
            )
            lines = _content_lines(soup, limit=100)
            images = _collect_image_urls(soup, target, limit=28)
            if lines or images:
                img_block = "\n".join(images)
                body = (
                    f"{title}\n\n"
                    f"[正文重點]\n" + "\n".join(f"- {line}" for line in lines[:80]) + "\n\n"
                    f"[素材圖片]\n{img_block}\n\n"
                    f"[爬文品質]\n- text_lines: {len(lines)}\n- image_count: {len(images)}\n- source_url: {target}\n"
                )
            access_status = "public"
            access_note = ""
    except Exception:
        pass

    return [
        CrawledItem(
            source_name=source_name,
            source_category=source_category,
            source_url=source_url,
            item_url=target,
            title_original=title,
            body_original=body,
            language="ja",
            published_at=now,
            access_status=access_status,
            access_note=access_note,
            image_urls="\n".join(images),
            content_kind=content_kind,
        )
    ]
