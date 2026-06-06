from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse, urlunparse, urlencode

from src.homes_media_token import homes_listing_image_tokens
from src.portal_property_playwright import default_playwright_state_path


def _looks_like_bot_challenge(html: str, page_title: str | None = None) -> bool:
    if not html:
        return True
    h = html.lower()
    markers = (
        "human verification",
        "awswaf.com",
        "gokuprops",
        "aws waf",
        "challenge.js",
        "captcha.awswaf",
        "/challenge/",
    )
    if any(m in h for m in markers):
        return True
    if "homes.co.jp" in h and len(html) < 12000 and html.count("<a ") < 3:
        if "aws" in h or "waf" in h or "challenge" in h:
            return True
    t = str(page_title or "").lower().strip()
    if t and any(x in t for x in ("human verification", "captcha", "challenge")):
        return True
    return False


def _normalize_homes_image_url(url: str, *, image_size: int = 1600) -> str:
    u = str(url or "").strip().rstrip(").,;\"'")
    if not u.startswith("http"):
        return ""
    try:
        p = urlparse(u)
    except Exception:
        return u
    host = (p.netloc or "").lower()
    path = (p.path or "").lower()
    if "homes.jp" in host and ("image.php" in path or "/smallimg/" in path):
        pairs = parse_qsl(p.query, keep_blank_values=True)
        q: dict[str, str] = {str(k): str(v) for k, v in pairs}
        if image_size > 0:
            q["width"] = str(max(600, min(1600, int(image_size))))
            q["height"] = str(max(600, min(1600, int(image_size))))
        try:
            return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))
        except Exception:
            return u
    return u


def _is_homes_property_image_url(url: str) -> bool:
    u = str(url or "").strip()
    if not u.startswith("http"):
        return False
    lu = u.lower()
    try:
        parsed = urlparse(u)
        path = parsed.path.lower()
        path_haystack = f"{(parsed.netloc or '').lower()}{path}"
    except Exception:
        path = lu.split("?", 1)[0]
        path_haystack = path
    if any(
        bad in lu
        for bad in (
            "icon.lifull",
            "/svg-icon/",
            "header-footer",
            "blank",
            "pixel",
            "avatar",
        )
    ):
        return False
    if any(bad in path_haystack for bad in ("logo", "sprite", "loading", "noimage", "no_image", "banner")):
        return False
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
        return True
    return ("homes.jp" in lu or "homes.co.jp" in lu) and any(
        tok in lu for tok in ("/smallimg/", "image.php", "/sale/", "/rent/", "/image/", "/photo/")
    )


def _title_fallback(page) -> str:
    title = ""
    try:
        title = str(page.locator("h1").first.inner_text(timeout=2500) or "").strip()
    except Exception:
        title = ""
    if not title:
        try:
            title = str(page.title() or "").strip()
        except Exception:
            title = ""
    return title[:400]


def fetch_homes_detail_playwright(
    item_url: str,
    *,
    storage_state_path: str | None = None,
    image_size: int = 1600,
    headful: bool = False,
    channel: str = "",
    timeout_ms: int = 60000,
) -> dict[str, Any]:
    url = str(item_url or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "error": "invalid url"}

    tokens = homes_listing_image_tokens(url)
    storage = str(storage_state_path or "").strip()
    if not storage:
        storage = str(default_playwright_state_path())
    storage_file = Path(storage)
    storage_state = str(storage_file) if storage_file.is_file() else None

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"ok": False, "error": f"playwright unavailable: {type(exc).__name__}: {exc}"}

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    with sync_playwright() as p:
        launch_kw: dict[str, Any] = {
            "headless": not bool(headful),
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if str(channel or "").strip():
            launch_kw["channel"] = str(channel).strip()
        browser = p.chromium.launch(**launch_kw)
        try:
            ctx_kw: dict[str, Any] = {
                "user_agent": ua,
                "locale": "ja-JP",
                "viewport": {"width": 1365, "height": 900},
                "extra_http_headers": {"Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7"},
            }
            if storage_state:
                ctx_kw["storage_state"] = storage_state
            ctx = browser.new_context(**ctx_kw)
            try:
                ctx.add_init_script(
                    "(() => { try { Object.defineProperty(navigator,'webdriver',{get:()=>undefined}); } catch(e){} })();"
                )
            except Exception:
                pass
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=max(1, int(timeout_ms)))
                # Wait out WAF challenge interstitials (best-effort).
                for _ in range(120):
                    try:
                        html = str(page.content() or "")
                        title0 = str(page.title() or "")
                    except Exception:
                        html, title0 = "", ""
                    if not _looks_like_bot_challenge(html, title0):
                        break
                    page.wait_for_timeout(500)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(1200)
                title = _title_fallback(page)
                try:
                    body_text = str(page.locator("body").inner_text(timeout=10000) or "")
                except Exception:
                    body_text = ""

                raw_images = page.evaluate(
                    """
                    () => {
                      const attrs = ['src','data-src','data-original','data-lazy-src','data-original-src',
                                     'data-img','data-image','data-main-src','srcset','data-srcset'];
                      const out = [];
                      const abs = (v) => { try { return new URL(v, location.href).href; } catch(e) { return ''; } };
                      const push = (value) => {
                        if (!value) return;
                        String(value).split(',').forEach((part) => {
                          const token = part.trim().split(/\\s+/)[0];
                          if (!token || /^data:/i.test(token)) return;
                          const u = abs(token);
                          if (/^https?:/i.test(u)) out.push(u);
                        });
                      };
                      document.querySelectorAll('img, source, picture, [style]').forEach((el) => {
                        attrs.forEach((attr) => push(el.getAttribute(attr)));
                        const style = el.getAttribute('style') || '';
                        [...style.matchAll(/url\\((['\\"]?)(.*?)\\1\\)/g)].forEach((m) => push(m[2]));
                      });
                      return out;
                    }
                    """
                )

                images: list[str] = []
                seen: set[str] = set()
                for raw in raw_images if isinstance(raw_images, list) else []:
                    if not isinstance(raw, str):
                        continue
                    nu = _normalize_homes_image_url(raw, image_size=image_size)
                    if not nu or not _is_homes_property_image_url(nu):
                        continue
                    if tokens:
                        try:
                            dec = unquote(nu).lower()
                        except Exception:
                            dec = nu.lower()
                        if not any(tok in dec for tok in tokens):
                            continue
                    key = nu.lower().split("#", 1)[0]
                    if key in seen:
                        continue
                    seen.add(key)
                    images.append(nu)
                    if len(images) >= 48:
                        break

                # Persist cookies if we successfully passed the challenge.
                if storage_file and storage_file.parent.exists():
                    try:
                        html = str(page.content() or "")
                        title0 = str(page.title() or "")
                    except Exception:
                        html, title0 = "", ""
                    if storage_file and not _looks_like_bot_challenge(html, title0):
                        try:
                            ctx.storage_state(path=str(storage_file))
                        except Exception:
                            pass

                return {
                    "ok": True,
                    "item_url": url,
                    "title": title,
                    "body_original": re.sub(r"\s+", " ", str(body_text or "")).strip()[:9000],
                    "image_urls": images,
                    "image_count": len(images),
                    "used_tokens": list(tokens),
                }
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    ctx.close()
                except Exception:
                    pass
        finally:
            try:
                browser.close()
            except Exception:
                pass
