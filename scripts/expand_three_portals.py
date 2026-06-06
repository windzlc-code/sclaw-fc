from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.bsoup import soup_from_html
from src.config import DB_PATH
from src.crawler import CrawledItem
from src.pipeline import process_crawled_items
from src.portal_property_crawl import (
    LISTING_HUB_PAGES,
    PORTAL_BROWSER_HEADERS,
    PortalRateLimitActive,
    _collect_links_from_hub_list,
    _is_homes_property_url,
    _is_suumo_property_url,
    _portal_get,
    _portal_skip_reason,
    fetch_property_detail,
)


SUUMO_PREFS: tuple[str, ...] = (
    "tokyo",
    "kanagawa",
    "saitama",
    "chiba",
    "osaka",
    "kyoto",
    "hyogo",
    "aichi",
    "fukuoka",
    "hiroshima",
    "miyagi",
    "hokkaido",
    "nagano",
    "niigata",
    "yamanashi",
    "ishikawa",
    "toyama",
    "fukui",
    "shizuoka",
    "gifu",
    "mie",
)


def _norm_host(url: str) -> str:
    h = (urlparse(url).netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def existing_urls(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    out: set[str] = set()
    for start in range(0, len(urls), 700):
        chunk = urls[start : start + 700]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(f"SELECT item_url FROM source_items WHERE item_url IN ({marks})", chunk).fetchall()
        out.update(str(r[0]) for r in rows)
    conn.close()
    return out


def source_count(host_like: str = "") -> int:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    if host_like:
        n = int(
            conn.execute(
                "SELECT COUNT(*) FROM source_items WHERE content_kind='jp_listing' AND item_url LIKE ?",
                (host_like,),
            ).fetchone()[0]
            or 0
        )
    else:
        n = int(conn.execute("SELECT COUNT(*) FROM source_items WHERE content_kind='jp_listing'").fetchone()[0] or 0)
    conn.close()
    return n


def process_chunked(items: list[CrawledItem], chunk_size: int) -> int:
    processed = 0
    size = max(1, int(chunk_size or 1))
    chunk_sleep_raw = (os.getenv("SCLAW_PROCESS_CHUNK_SLEEP_SEC") or "0").strip()
    try:
        chunk_sleep = max(0.0, min(10.0, float(chunk_sleep_raw)))
    except ValueError:
        chunk_sleep = 0.0
    for start in range(0, len(items), size):
        chunk = items[start : start + size]
        for attempt in range(8):
            try:
                processed += int(process_crawled_items(chunk) or 0)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= 7:
                    raise
                wait = min(20.0, 0.75 * (2**attempt))
                print(f"db_locked retry={attempt + 1}/8 wait={wait:.1f}s", flush=True)
                time.sleep(wait)
        if chunk_sleep > 0 and start + size < len(items):
            time.sleep(chunk_sleep)
    return processed


def discover_suumo_child_hubs(client: httpx.Client, hub: str, *, max_pages: int) -> list[str]:
    """Top SUUMO category pages often link to city/station result pages before detail pages."""
    out: list[str] = [hub]
    seen: set[str] = {hub.rstrip("/")}
    try:
        r = _portal_get(client, hub)
        r.raise_for_status()
    except PortalRateLimitActive:
        return []
    except Exception:
        return out
    soup = soup_from_html(r.text)
    base_path = (urlparse(hub).path or "").strip("/")
    base_parts = [p for p in base_path.split("/") if p]
    category = "/".join(base_parts[:2]) if len(base_parts) >= 2 and base_parts[0] == "ms" else (base_parts[0] if base_parts else "")
    for a in soup.select("a[href]"):
        full = urljoin(hub, str(a.get("href") or "")).split("#", 1)[0]
        if not full.startswith("http") or "suumo.jp" not in full.lower():
            continue
        if _is_suumo_property_url(full):
            continue
        parsed = urlparse(full)
        path = (parsed.path or "").lower()
        if not category or f"/{category}/" not in path:
            continue
        if not any(tok in path for tok in ("/sc_", "/ek_", "/en_", "/new/", "/city/", "/ensen/", "/soba/")):
            continue
        key = full.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(full)
        if len(out) >= max_pages:
            break
    return out


def suumo_page_sleep() -> float:
    raw = (os.getenv("SCLAW_SUUMO_PAGE_SLEEP") or "0").strip()
    try:
        return max(0.0, min(12.0, float(raw)))
    except ValueError:
        return 1.2


def env_enabled(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def listing_title_from_snippet(snippet: str, fallback_url: str) -> str:
    text = clean_text(snippet)
    patterns = (
        r"物件名\s+(.+?)\s+(?:販売価格|価格|所在地|沿線・駅|専有面積|土地面積|建物面積)",
        r"(?:中古マンション|新築マンション|中古一戸建て|新築一戸建て)\s+(.+?)\s+(?:販売価格|価格|所在地)",
        r"^(?:NEW\s+)?(.+?)\s+(?:\d[\d,]*(?:億\d*万円|億円|万円)|価格未定|未定)",
    )
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        title = clean_text(m.group(1))
        title = re.sub(r"^(?:チェック|新着|購入サポート情報)\s+", "", title).strip()
        if 2 <= len(title) <= 160:
            return title
    return text[:160] or str(fallback_url)


def listing_card_text(anchor: object, *, limit: int = 1000) -> str:
    """Extract the nearest listing-card text around a detail anchor."""
    tokens = (
        "万円",
        "価格",
        "徒歩",
        "階",
        "㎡",
        "m²",
        "間取り",
        "専有",
        "土地",
        "建物",
        "LDK",
        "DK",
        "1K",
        "1R",
        "ワンルーム",
    )
    get_text = getattr(anchor, "get_text", None)
    best = clean_text(get_text(" ", strip=True)) if callable(get_text) else ""
    if len(best) >= 20 and any(tok in best for tok in tokens):
        return best[:limit]
    node = anchor
    for _ in range(7):
        node = getattr(node, "parent", None)
        if node is None:
            break
        get_node_text = getattr(node, "get_text", None)
        if not callable(get_node_text):
            continue
        text = clean_text(get_node_text(" ", strip=True))
        if len(text) > 1800:
            continue
        if len(text) >= 20 and any(tok in text for tok in tokens):
            return text[:limit]
    return best[:limit]


def snippet_payload(
    target: dict[str, object],
    url: str,
    snippet: str,
    *,
    note: str,
    image_urls: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    source_name = str(target.get("name") or "不動産ポータル")
    title = listing_title_from_snippet(snippet, url)
    body_original = (
        f"{title}\n\n"
        f"[{source_name} 列表摘要]\n{clean_text(snippet)}\n\n"
        f"來源物件頁（請以官方頁面為準）：{url}\n"
        f"用途：站內摘要、導覽與連結索引；{note}"
    )
    imgs: list[str] = []
    for raw in image_urls or []:
        u = str(raw or "").strip()
        if u.startswith("http") and _homes_listing_image_url_is_usable(u) and u not in imgs:
            imgs.append(u)
    return title, body_original, imgs[:12]


def _homes_listing_image_url_is_usable(url: str) -> bool:
    u = str(url or "").strip()
    if not u.startswith("http"):
        return False
    lu = u.lower()
    if any(
        bad in lu
        for bad in (
            "logo",
            "icon.lifull",
            "/svg-icon/",
            "header-footer",
            "sprite",
            "blank",
            "pixel",
            "loading",
            "noimage",
        )
    ):
        return False
    path = urlparse(u).path.lower()
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
        return True
    if ("homes.jp" in lu or "homes.co.jp" in lu) and any(
        tok in lu for tok in ("/smallimg/", "image.php", "/sale/", "/rent/", "/image/", "/photo/")
    ):
        return True
    return False


def _homes_srcset_urls(value: str, base_url: str) -> list[str]:
    out: list[str] = []
    for part in str(value or "").split(","):
        token = part.strip().split()
        if not token:
            continue
        u = urljoin(base_url, token[0].strip())
        if u.startswith("http") and u not in out:
            out.append(u)
    return out


def _homes_listing_card_images_from_node(node: object, base_url: str, *, limit: int = 8) -> list[str]:
    select = getattr(node, "select", None)
    if not callable(select):
        return []
    out: list[str] = []

    def push(raw: object) -> None:
        val = str(raw or "").strip()
        if not val or val.startswith("data:"):
            return
        for cand in _homes_srcset_urls(val, base_url):
            if _homes_listing_image_url_is_usable(cand) and cand not in out:
                out.append(cand[:1200])
                if len(out) >= limit:
                    return

    for el in select("img, source, picture, [style]"):
        for attr in (
            "src",
            "data-src",
            "data-original",
            "data-lazy-src",
            "data-original-src",
            "data-img",
            "data-image",
            "data-main-src",
            "srcset",
            "data-srcset",
        ):
            push(getattr(el, "get", lambda *_: "")(attr))
            if len(out) >= limit:
                return out[:limit]
        style = str(getattr(el, "get", lambda *_: "")("style") or "")
        for m in re.findall(r"url\((['\"]?)(.*?)\1\)", style, flags=re.I):
            push(m[1])
            if len(out) >= limit:
                return out[:limit]
    return out[:limit]


def homes_listing_card_images(anchor: object, hub: str, *, limit: int = 8) -> list[str]:
    best: list[str] = []
    node = anchor
    for _ in range(10):
        imgs = _homes_listing_card_images_from_node(node, hub, limit=limit)
        if len(imgs) > len(best):
            best = imgs
        if len(best) >= limit:
            return best[:limit]
        node = getattr(node, "parent", None)
        if node is None:
            break
    return best[:limit]


def collect_suumo_detail_links_with_snippets(
    client: httpx.Client,
    hub: str,
    *,
    limit: int,
    max_pages: int,
) -> tuple[list[str], dict[str, str]]:
    """Fast SUUMO mode: collect official detail URLs and list-card text from result pages."""
    skip_reason = _portal_skip_reason("suumo.jp")
    if skip_reason:
        print(f"skip suumo: {skip_reason}", flush=True)
        return [], {}
    pages = discover_suumo_child_hubs(client, hub, max_pages=max_pages)
    seen: set[str] = set()
    out: list[str] = []
    snippets: dict[str, str] = {}
    page_delay = suumo_page_sleep()
    for page_idx, page_url in enumerate(pages, start=1):
        if len(out) >= limit:
            break
        if page_idx > 1 and page_delay > 0:
            time.sleep(page_delay)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except PortalRateLimitActive as ex:
            print(f"cooldown suumo: {ex}", flush=True)
            break
        except Exception:
            continue
        soup = soup_from_html(r.text)
        for a in soup.select("a[href]"):
            full = urljoin(page_url, str(a.get("href") or "")).split("#", 1)[0]
            if not full.startswith("http"):
                continue
            if not _is_suumo_property_url(full):
                continue
            snippet = listing_card_text(a)
            if snippet and len(snippet) > len(snippets.get(full, "")):
                snippets[full] = snippet
            if full in seen:
                continue
            seen.add(full)
            out.append(full)
            if len(out) >= limit:
                break
    return out[:limit], snippets


def collect_suumo_detail_links(client: httpx.Client, hub: str, *, limit: int, max_pages: int) -> list[str]:
    """Fast SUUMO mode: expand one top hub to city/station pages, then collect detail URLs directly."""
    urls, _ = collect_suumo_detail_links_with_snippets(client, hub, limit=limit, max_pages=max_pages)
    return urls


def _merge_homes_card(
    out: dict[str, dict[str, object]],
    url: str,
    text: str,
    image_urls: list[str] | None = None,
) -> None:
    if not url or not text:
        return
    cur = out.get(url)
    imgs = [u for u in image_urls or [] if _homes_listing_image_url_is_usable(u)]
    if not cur:
        out[url] = {"text": text[:1200], "image_urls": imgs[:12]}
        return
    if len(text) > len(str(cur.get("text") or "")):
        cur["text"] = text[:1200]
    merged: list[str] = []
    for u in [*(cur.get("image_urls") if isinstance(cur.get("image_urls"), list) else []), *imgs]:
        su = str(u or "").strip()
        if su and su not in merged:
            merged.append(su)
    cur["image_urls"] = merged[:12]


def collect_homes_listing_cards_httpx(client: httpx.Client, hub: str, *, limit: int) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    try:
        r = client.get(hub)
        r.raise_for_status()
    except Exception:
        return out
    collect_homes_cards_from_html(out, hub, r.text, limit=limit)
    return out


def collect_homes_listing_snippets_httpx(client: httpx.Client, hub: str, *, limit: int) -> dict[str, str]:
    cards = collect_homes_listing_cards_httpx(client, hub, limit=limit)
    return {u: str(card.get("text") or "")[:700] for u, card in cards.items() if str(card.get("text") or "").strip()}


def homes_expanded_hubs(hub: str) -> list[str]:
    """HOME'S buy hubs often have city selector pages; list pages contain the detail cards."""
    base = str(hub or "").strip()
    if not base:
        return []
    out: list[str] = [base]
    parsed = urlparse(base)
    path = (parsed.path or "").strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] in {"mansion", "kodate"} and parts[1] in {"chuko", "shinchiku"}:
        pref = parts[2]
        out.append(urljoin(base, f"/{parts[0]}/{parts[1]}/{pref}/list/"))
        if len(parts) >= 4 and parts[-1] == "city":
            if len(parts) >= 5 and parts[-2].endswith("_23ku"):
                out.append(urljoin(base, f"/{parts[0]}/{parts[1]}/{pref}/list/"))
            else:
                out.append(urljoin(base, f"/{parts[0]}/{parts[1]}/{pref}/list/"))
        if len(parts) >= 4 and parts[-1] != "list" and parts[-1].endswith("-city"):
            out.append(urljoin(base, f"/{parts[0]}/{parts[1]}/{pref}/{parts[-1]}/list/"))
    expanded = list(dict.fromkeys(out))
    page_limit = homes_list_page_limit()
    with_pages: list[str] = []
    for u in expanded:
        with_pages.append(u)
        path = (urlparse(u).path or "").rstrip("/")
        if not path.endswith("/list"):
            continue
        for page in range(2, page_limit + 1):
            with_pages.append(url_with_page(u, page))
    return list(dict.fromkeys(with_pages))


def homes_list_page_limit() -> int:
    raw = (os.getenv("SCLAW_HOMES_LIST_PAGES") or "5").strip()
    try:
        return max(1, min(20, int(raw)))
    except ValueError:
        return 5


def url_with_page(url: str, page: int) -> str:
    parts = urlparse(url)
    qs = parse_qs(parts.query, keep_blank_values=True)
    qs["page"] = [str(max(1, int(page)))]
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(qs, doseq=True), parts.fragment))


def is_homes_detail_url_for_ingest(url: str) -> bool:
    u = (url or "").strip().lower().split("#", 1)[0].split("?", 1)[0]
    if "homes.co.jp" not in u or "/inquire/" in u:
        return False
    return bool(re.search(r"/(?:mansion|kodate)/b-[^/]+/?$", u) or "/chintai/room/" in u)


def homes_listing_card_text(anchor: object, *, limit: int = 1200) -> str:
    tokens = ("万円", "価格", "徒歩", "階", "㎡", "m²", "間取り", "専有面積", "土地面積", "建物面積", "LDK", "DK", "1K", "1R")
    get_text = getattr(anchor, "get_text", None)
    best = clean_text(get_text(" ", strip=True)) if callable(get_text) else ""
    node = anchor
    for _ in range(10):
        node = getattr(node, "parent", None)
        if node is None:
            break
        get_node_text = getattr(node, "get_text", None)
        if not callable(get_node_text):
            continue
        text = clean_text(get_node_text(" ", strip=True))
        if len(text) >= 20 and len(text) <= 3200 and any(tok in text for tok in tokens):
            return text[:limit]
        if len(text) > len(best) and any(tok in text for tok in tokens):
            best = text[:limit]
    return best[:limit]


def collect_homes_cards_from_html(out: dict[str, dict[str, object]], hub: str, html: str, *, limit: int) -> None:
    soup = soup_from_html(html)
    for a in soup.select("a[href]"):
        full = urljoin(hub, str(a.get("href") or "")).split("#", 1)[0]
        if not full.startswith("http") or not is_homes_detail_url_for_ingest(full):
            continue
        text = homes_listing_card_text(a)
        if len(text) < 12:
            continue
        images = homes_listing_card_images(a, hub, limit=10)
        _merge_homes_card(out, full, text[:1000], images)
        if len(out) >= limit:
            break


def collect_homes_snippets_from_html(out: dict[str, str], hub: str, html: str, *, limit: int) -> None:
    cards: dict[str, dict[str, object]] = {
        u: {"text": text, "image_urls": []} for u, text in out.items() if str(text or "").strip()
    }
    collect_homes_cards_from_html(cards, hub, html, limit=limit)
    for u, card in cards.items():
        text = str(card.get("text") or "")
        if text and len(text) > len(out.get(u, "")):
            out[u] = text[:1000]


def collect_homes_listing_cards_playwright(hubs: list[str], *, limit: int) -> dict[str, dict[str, object]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {}
    out: dict[str, dict[str, object]] = {}
    ua = PORTAL_BROWSER_HEADERS.get("User-Agent") or "Mozilla/5.0"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                ctx = browser.new_context(
                    user_agent=ua,
                    locale="ja-JP",
                    viewport={"width": 1365, "height": 900},
                    extra_http_headers={"Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7"},
                )
                progress = env_enabled("SCLAW_HOMES_PROGRESS_LOG")
                for page_index, hub in enumerate(hubs, start=1):
                    if len(out) >= limit:
                        break
                    page = ctx.new_page()
                    try:
                        page.goto(hub, wait_until="domcontentloaded", timeout=60000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=12000)
                        except Exception:
                            pass
                        page.wait_for_timeout(800)
                        collect_homes_cards_from_html(out, hub, page.content(), limit=limit)
                        if progress:
                            print(
                                f"homes-playwright page={page_index}/{len(hubs)} cards={len(out)}/{limit} url={hub}",
                                flush=True,
                            )
                    finally:
                        page.close()
            finally:
                browser.close()
    except Exception:
        return out
    return out


def collect_homes_listing_snippets_playwright(hubs: list[str], *, limit: int) -> dict[str, str]:
    cards = collect_homes_listing_cards_playwright(hubs, limit=limit)
    return {u: str(card.get("text") or "")[:1000] for u, card in cards.items() if str(card.get("text") or "").strip()}


def suumo_hubs(modes: set[str], *, max_pref: int) -> list[dict[str, object]]:
    prefs = SUUMO_PREFS[: max(1, min(len(SUUMO_PREFS), max_pref))]
    out: list[dict[str, object]] = []
    for pref in prefs:
        if "rent" in modes:
            out.append({"host": "suumo.jp", "name": "SUUMO", "label": f"suumo-chintai-new-{pref}", "hub": f"https://suumo.jp/chintai/{pref}/new/", "source_category": "大型房仲"})
            out.append({"host": "suumo.jp", "name": "SUUMO", "label": f"suumo-chintai-{pref}", "hub": f"https://suumo.jp/chintai/{pref}/", "source_category": "大型房仲"})
        if "mansion" in modes:
            out.append({"host": "suumo.jp", "name": "SUUMO", "label": f"suumo-ms-shinchiku-{pref}", "hub": f"https://suumo.jp/ms/shinchiku/{pref}/", "source_category": "大型房仲"})
            out.append({"host": "suumo.jp", "name": "SUUMO", "label": f"suumo-ms-chuko-{pref}", "hub": f"https://suumo.jp/ms/chuko/{pref}/", "source_category": "大型房仲"})
        if "house" in modes:
            out.append({"host": "suumo.jp", "name": "SUUMO", "label": f"suumo-chukoikkodate-{pref}", "hub": f"https://suumo.jp/chukoikkodate/{pref}/", "source_category": "大型房仲"})
            out.append({"host": "suumo.jp", "name": "SUUMO", "label": f"suumo-ikkodate-{pref}", "hub": f"https://suumo.jp/ikkodate/{pref}/", "source_category": "大型房仲"})
    return out


def homes_hubs(modes: set[str]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for hub in LISTING_HUB_PAGES.get("homes.co.jp", []):
        hl = hub.lower()
        if "rent" not in modes and "/chintai/" in hl:
            continue
        if "mansion" not in modes and "/mansion/" in hl:
            continue
        if "house" not in modes and ("/kodate/" in hl or "/ikkodate/" in hl):
            continue
        out.append({"host": "homes.co.jp", "name": "LIFULL HOME'S", "label": "homes-" + re.sub(r"[^a-z0-9]+", "-", urlparse(hub).path.strip("/").lower()).strip("-"), "hub": hub, "source_category": "大型房仲"})
    return out


def athome_hubs(modes: set[str]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for hub in LISTING_HUB_PAGES.get("athome.co.jp", []):
        hl = hub.lower()
        if "rent" not in modes and "/chintai/" in hl:
            continue
        if "mansion" not in modes and "/mansion/" in hl:
            continue
        if "house" not in modes and "/kodate/" in hl:
            continue
        out.append({"host": "athome.co.jp", "name": "at home", "label": "athome-" + re.sub(r"[^a-z0-9]+", "-", urlparse(hub).path.strip("/").lower()).strip("-"), "hub": hub, "source_category": "大型房仲"})
    return out


def build_targets(portals: set[str], modes: set[str], *, suumo_pref_limit: int) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    if "suumo" in portals:
        targets.extend(suumo_hubs(modes, max_pref=suumo_pref_limit))
    if "homes" in portals:
        targets.extend(homes_hubs(modes))
    if "athome" in portals:
        targets.extend(athome_hubs(modes))
    return targets


def collect_target_links(
    client: httpx.Client,
    target: dict[str, object],
    *,
    per_target: int,
    suumo_child_pages: int,
) -> list[str]:
    host = str(target["host"])
    hub = str(target["hub"])
    if host == "suumo.jp":
        urls, snippets = collect_suumo_detail_links_with_snippets(
            client,
            hub,
            limit=per_target,
            max_pages=suumo_child_pages,
        )
        if snippets:
            target["_snippets"] = snippets
        return urls
    hubs = homes_expanded_hubs(hub) if host == "homes.co.jp" else [hub]
    urls = _collect_links_from_hub_list(client, host, hubs, per_target)
    if host == "homes.co.jp":
        cards: dict[str, dict[str, object]] = {}
        for h in hubs:
            for u, card in collect_homes_listing_cards_httpx(client, h, limit=per_target).items():
                _merge_homes_card(
                    cards,
                    u,
                    str(card.get("text") or ""),
                    card.get("image_urls") if isinstance(card.get("image_urls"), list) else [],
                )
            if len(cards) >= per_target:
                break
        snippets: dict[str, str] = {
            u: str(card.get("text") or "")[:1000] for u, card in cards.items() if str(card.get("text") or "").strip()
        }
        if len(snippets) < min(8, per_target):
            pw_cards = collect_homes_listing_cards_playwright(hubs, limit=per_target)
            for u, card in pw_cards.items():
                _merge_homes_card(
                    cards,
                    u,
                    str(card.get("text") or ""),
                    card.get("image_urls") if isinstance(card.get("image_urls"), list) else [],
                )
            snippets = {
                u: str(card.get("text") or "")[:1000]
                for u, card in cards.items()
                if str(card.get("text") or "").strip()
            }
        if snippets:
            target["_snippets"] = snippets
            target["_snippet_images"] = {
                u: [str(x) for x in (cards.get(u, {}).get("image_urls") or []) if str(x or "").strip()]
                for u in snippets
            }
            merged = list(dict.fromkeys([*snippets.keys(), *urls]))
            return merged[:per_target]
    return urls


def fetch_items(
    client: httpx.Client,
    target: dict[str, object],
    urls: list[str],
    *,
    sleep_sec: float,
    skip_js_disabled: bool,
) -> list[CrawledItem]:
    now = datetime.now(timezone.utc).isoformat()
    out: list[CrawledItem] = []
    snippets = target.get("_snippets") if isinstance(target, dict) else None
    snippet_map: dict[str, str] = snippets if isinstance(snippets, dict) else {}
    snippet_images = target.get("_snippet_images") if isinstance(target, dict) else None
    snippet_image_map: dict[str, list[str]] = snippet_images if isinstance(snippet_images, dict) else {}
    host = str(target.get("host") or "")
    suumo_snippet_only = host == "suumo.jp" and env_enabled("SCLAW_SUUMO_LISTING_SNIPPET_ONLY")
    homes_snippet_only = host == "homes.co.jp" and env_enabled("SCLAW_HOMES_LISTING_SNIPPET_ONLY")
    for url in urls:
        if suumo_snippet_only or homes_snippet_only:
            snippet = snippet_map.get(url, "").strip()
            if snippet:
                source_note = (
                    "SUUMO 詳頁低頻補抓，先以列表卡片欄位建立買屋案件，可搜尋價格、間取り、階層、交通與徒歩時間。"
                    if suumo_snippet_only
                    else "HOME'S 詳頁後續補抓，先以列表卡片欄位建立買屋案件，可搜尋價格、間取り、階層、交通與徒歩時間。"
                )
                title, body_original, imgs = snippet_payload(
                    target,
                    url,
                    snippet,
                    note=source_note,
                    image_urls=snippet_image_map.get(url) or [],
                )
                out.append(
                    CrawledItem(
                        source_name=str(target["name"]),
                        source_category=str(target.get("source_category") or "大型房仲"),
                        source_url=str(target["hub"]),
                        item_url=url,
                        title_original=title[:240],
                        body_original=body_original,
                        language="ja",
                        published_at=now,
                        access_status="public",
                        access_note="",
                        image_urls="\n".join(imgs),
                        content_kind="jp_listing",
                    )
                )
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
            continue
        try:
            title, body_original, imgs = fetch_property_detail(client, url)
        except Exception:
            snippet = snippet_map.get(url, "").strip()
            if not snippet:
                continue
            title, body_original, imgs = snippet_payload(
                target,
                url,
                snippet,
                note="detail 頁暫時不可讀時，先以列表卡片欄位建立可搜尋案件。",
                image_urls=snippet_image_map.get(url) or [],
            )
        if snippet_map.get(url) and (
            len(clean_text(title)) < 4
            or "ページがみつかりません" in (body_original or "")
            or "キーワード検索" in (body_original or "")
        ):
            title, body_original, imgs = snippet_payload(
                target,
                url,
                snippet_map[url],
                note="detail 頁回傳暫時頁或內容不足，先以列表卡片欄位建立可搜尋案件。",
                image_urls=snippet_image_map.get(url) or [],
            )
        if skip_js_disabled and "javascript is disabled" in (title or "").lower():
            snippet = snippet_map.get(url, "").strip()
            if not snippet:
                continue
            title, body_original, imgs = snippet_payload(
                target,
                url,
                snippet,
                note="detail 頁若需 JavaScript 驗證，先以列表卡片欄位建立可搜尋案件。",
                image_urls=snippet_image_map.get(url) or [],
            )
        out.append(
            CrawledItem(
                source_name=str(target["name"]),
                source_category=str(target.get("source_category") or "大型房仲"),
                source_url=str(target["hub"]),
                item_url=url,
                title_original=title[:240],
                body_original=body_original,
                language="ja",
                published_at=now,
                access_status="public",
                access_note="",
                image_urls="\n".join(imgs),
                content_kind="jp_listing",
            )
        )
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return out


def parse_set(raw: str, default: set[str]) -> set[str]:
    vals = {x.strip().lower() for x in str(raw or "").split(",") if x.strip()}
    return vals or default


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand SUUMO / LIFULL HOME'S / at home listings with URL-level de-duplication.")
    parser.add_argument("--portals", default="suumo,homes,athome")
    parser.add_argument("--modes", default="rent,mansion,house")
    parser.add_argument("--per-target", type=int, default=500)
    parser.add_argument("--max-targets", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--sleep-sec", type=float, default=0.05)
    parser.add_argument("--target-sleep-sec", type=float, default=0.0)
    parser.add_argument("--suumo-pref-limit", type=int, default=4)
    parser.add_argument("--suumo-child-pages", type=int, default=4)
    parser.add_argument("--skip-js-disabled", action="store_true")
    parser.add_argument("--write-report", default="")
    args = parser.parse_args()

    portals = parse_set(args.portals, {"suumo", "homes", "athome"})
    modes = parse_set(args.modes, {"rent", "mansion", "house"})
    targets = build_targets(portals, modes, suumo_pref_limit=max(1, int(args.suumo_pref_limit or 1)))
    start_index = max(1, int(args.start_index or 1))
    targets = targets[start_index - 1 :]
    if args.max_targets and args.max_targets > 0:
        targets = targets[: int(args.max_targets)]

    report: dict[str, object] = {
        "ok": True,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "portals": sorted(portals),
        "modes": sorted(modes),
        "per_target": int(args.per_target),
        "start_index": start_index,
        "target_count": len(targets),
        "source_count_before": source_count(),
        "portal_counts_before": {
            "suumo": source_count("%suumo.jp%"),
            "homes": source_count("%homes.co.jp%"),
            "athome": source_count("%athome.co.jp%"),
        },
        "rows": [],
    }
    print(
        f"start source_jp={report['source_count_before']} targets={len(targets)} "
        f"portals={','.join(sorted(portals))} modes={','.join(sorted(modes))}",
        flush=True,
    )

    timeout = httpx.Timeout(24.0, connect=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=PORTAL_BROWSER_HEADERS) as client:
        for idx, target in enumerate(targets, start=start_index):
            host = str(target["host"])
            before_all = source_count()
            before_host = source_count(f"%{host}%")
            urls = collect_target_links(
                client,
                target,
                per_target=max(1, int(args.per_target or 1)),
                suumo_child_pages=max(1, int(args.suumo_child_pages or 1)),
            )
            known = existing_urls(urls)
            new_urls = [u for u in urls if u not in known]
            items = fetch_items(
                client,
                target,
                new_urls,
                sleep_sec=max(0.0, float(args.sleep_sec or 0)),
                skip_js_disabled=bool(args.skip_js_disabled),
            )
            processed = process_chunked(items, max(1, int(args.chunk_size or 1)))
            after_all = source_count()
            after_host = source_count(f"%{host}%")
            row = {
                "index": idx,
                "label": target["label"],
                "host": host,
                "hub": target["hub"],
                "collected": len(urls),
                "new_urls": len(new_urls),
                "fetched": len(items),
                "processed": processed,
                "source_count_before": before_all,
                "source_count_after": after_all,
                "host_count_before": before_host,
                "host_count_after": after_host,
                "delta": after_all - before_all,
                "host_delta": after_host - before_host,
            }
            rows = report["rows"]
            assert isinstance(rows, list)
            rows.append(row)
            print(
                f"[{idx}] {row['label']} host={host} collected={len(urls)} new={len(new_urls)} "
                f"fetched={len(items)} processed={processed} all={before_all}->{after_all} "
                f"host={before_host}->{after_host} delta={after_all-before_all}",
                flush=True,
            )
            target_sleep = max(0.0, float(args.target_sleep_sec or 0))
            if target_sleep > 0:
                time.sleep(target_sleep)

    report["source_count_after"] = source_count()
    report["portal_counts_after"] = {
        "suumo": source_count("%suumo.jp%"),
        "homes": source_count("%homes.co.jp%"),
        "athome": source_count("%athome.co.jp%"),
    }
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if args.write_report:
        out = Path(args.write_report)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(out)
    print(f"done source_jp={report['source_count_after']} portals={report['portal_counts_after']}", flush=True)


if __name__ == "__main__":
    main()
