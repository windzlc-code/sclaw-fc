"""
Playwright fallback for portal listing hubs: JS-rendered or anti-bot (HTTP 202) pages
where httpx + static parse yield few or zero property links.

Env:
  SCLAW_PLAYWRIGHT=0 — disable browser fallback.
  SCLAW_PLAYWRIGHT_CHANNEL — e.g. chrome / msedge (use installed browser; overrides auto order).
  SCLAW_PLAYWRIGHT_HEADLESS=0 — headed Chromium (debug / manual WAF solve).
  SCLAW_PLAYWRIGHT_STORAGE_STATE — explicit path to storage_state.json (read/write when persist on).
  SCLAW_DATA_DIR — optional base dir (default: project data/).
  SCLAW_PLAYWRIGHT_PERSIST=0 — disable auto save of storage_state after a successful real page load.
  SCLAW_PLAYWRIGHT_AUTO_FALLBACK=0 — disable trying chrome/msedge after bundled Chromium when channel unset.
  SCLAW_PLAYWRIGHT_PROXY — proxy server URL (e.g. http://127.0.0.1:7890). Falls back to HTTPS_PROXY / HTTP_PROXY.
  SCLAW_SUUMO_PLAYWRIGHT=1 — opt in to SUUMO browser fallback; default is off to avoid worsening rate limits.

Note: LIFULL HOMES often serves AWS WAF "Human Verification"; automation uses persisted cookies when
available and saves state after loads that are not challenge pages so repeat runs can succeed.

Requires: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Iterable

from src.bsoup import soup_from_html

logger = logging.getLogger(__name__)

# Hide typical automation flag; sites may still use TLS/IP/WAF beyond this.
_PW_INIT_SCRIPT = """
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (e) {}
})();
"""

# These hosts often need a real browser; others only use Playwright when httpx found no links.
_PLAYWRIGHT_PREFERRED_HOSTS = frozenset(
    {
        "homes.co.jp",
        "athome.co.jp",
        "realestate.rakuten.co.jp",
        "yes1.co.jp",
        "oheya-su.jp",
        "oheyasuu.com",
        "oheyago.jp",
    }
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_playwright_state_path() -> Path:
    raw = (os.getenv("SCLAW_DATA_DIR") or "").strip()
    if raw:
        base = Path(raw)
    else:
        base = _project_root() / "data"
    return base / "playwright_storage_state.json"


def playwright_fallback_enabled() -> bool:
    v = (os.getenv("SCLAW_PLAYWRIGHT") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def playwright_persist_enabled() -> bool:
    return (os.getenv("SCLAW_PLAYWRIGHT_PERSIST") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _looks_like_bot_challenge(html: str, page_title: str | None) -> bool:
    """AWS WAF / similar interstitials: almost no real anchors; obvious markers in HTML/title."""
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
    return False


def _env_playwright_headless() -> bool:
    return (os.getenv("SCLAW_PLAYWRIGHT_HEADLESS") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _base_launch_kwargs() -> dict:
    return {
        "headless": _env_playwright_headless(),
        "args": [
            "--disable-blink-features=AutomationControlled",
        ],
    }


def _launch_variants() -> list[dict]:
    """Ordered launch configs: explicit channel first, else bundled Chromium then chrome, msedge."""
    base = _base_launch_kwargs()
    ch = (os.getenv("SCLAW_PLAYWRIGHT_CHANNEL") or "").strip()
    if ch:
        return [{**base, "channel": ch}]
    variants = [dict(base)]
    if (os.getenv("SCLAW_PLAYWRIGHT_AUTO_FALLBACK") or "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return variants
    for channel in ("chrome", "msedge"):
        variants.append({**base, "channel": channel})
    return variants


def _proxy_for_context() -> dict[str, str] | None:
    raw = (
        (os.getenv("SCLAW_PLAYWRIGHT_PROXY") or "").strip()
        or (os.getenv("HTTPS_PROXY") or "").strip()
        or (os.getenv("HTTP_PROXY") or "").strip()
    )
    if not raw:
        return None
    return {"server": raw}


def _storage_state_for_read() -> str | None:
    explicit = (os.getenv("SCLAW_PLAYWRIGHT_STORAGE_STATE") or "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit
    default = default_playwright_state_path()
    if default.is_file():
        return str(default)
    return None


def _storage_state_write_target() -> Path | None:
    if not playwright_persist_enabled():
        return None
    explicit = (os.getenv("SCLAW_PLAYWRIGHT_STORAGE_STATE") or "").strip()
    if explicit:
        return Path(explicit)
    return default_playwright_state_path()


def _atomic_replace(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _save_context_storage_state(context, dest: Path) -> None:
    """Write Playwright storage_state JSON atomically."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        suffix=".json",
        prefix="pw_state_",
        dir=str(dest.parent),
    )
    tmp_path = Path(tmp)
    try:
        os.close(fd)
        context.storage_state(path=str(tmp_path))
        _atomic_replace(tmp_path, dest)
        logger.info("Playwright saved storage state to %s", dest)
    except Exception as ex:
        try:
            if tmp_path.is_file():
                tmp_path.unlink()
        except OSError:
            pass
        logger.warning("Playwright could not save storage state %s: %s", dest, ex)


def _should_try_playwright(host_key: str, out_count: int, limit: int) -> bool:
    if host_key == "suumo.jp":
        enabled = (os.getenv("SCLAW_SUUMO_PLAYWRIGHT") or "").strip().lower() in ("1", "true", "yes", "on")
        if not enabled:
            return False
    if out_count >= limit:
        return False
    if out_count == 0:
        return True
    if host_key in _PLAYWRIGHT_PREFERRED_HOSTS:
        return True
    return False


def _session_run(
    p,
    launch_kw: dict,
    *,
    host_key: str,
    hub_list: list[str],
    limit: int,
    seen: set[str],
    out: list[str],
    ua: str,
) -> bool:
    """
    Open one browser with launch_kw, collect links. Returns True if any hub showed a non-challenge HTML
    (safe to persist cookies).
    """
    from src.portal_property_crawl import (
        _abs_url,
        _is_yahoo_house_detail_url,
        _is_yahoo_house_search_hub,
        _is_yahoo_land_detail_url,
        _is_yahoo_land_search_hub,
        _is_yahoo_mansion_detail_url,
        _is_yahoo_used_mansion_search_hub,
        _property_url_predicate,
    )

    def link_matches_hub_type(hub: str, full: str) -> bool:
        if host_key == "realestate.yahoo.co.jp":
            if _is_yahoo_used_mansion_search_hub(hub):
                return _is_yahoo_mansion_detail_url(full)
            if _is_yahoo_land_search_hub(hub):
                return _is_yahoo_land_detail_url(full)
            if _is_yahoo_house_search_hub(hub):
                return _is_yahoo_house_detail_url(full)
        return _property_url_predicate(host_key, full)

    persist_candidate = False
    browser = p.chromium.launch(**launch_kw)
    ch_label = launch_kw.get("channel") or "chromium"
    try:
        ctx_kw: dict = {
            "user_agent": ua,
            "locale": "ja-JP",
            "java_script_enabled": True,
            "viewport": {"width": 1365, "height": 900},
            "extra_http_headers": {
                "Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        }
        ss = _storage_state_for_read()
        if ss:
            ctx_kw["storage_state"] = ss
        proxy = _proxy_for_context()
        if proxy:
            ctx_kw["proxy"] = proxy
        context = browser.new_context(**ctx_kw)
        context.add_init_script(_PW_INIT_SCRIPT)
        page = context.new_page()
        for hub in hub_list:
            if len(out) >= limit:
                break
            try:
                resp = page.goto(
                    hub,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                for _ in range(40):
                    html = page.content()
                    title = page.title()
                    if not _looks_like_bot_challenge(html, title):
                        break
                    page.wait_for_timeout(500)
                else:
                    html = page.content()
                    title = page.title()
                st = getattr(resp, "status", None) if resp else None
                if _looks_like_bot_challenge(html, title):
                    logger.warning(
                        "Playwright hub blocked or challenge page (channel=%s status=%s anchors~%s): %s",
                        ch_label,
                        st,
                        html.lower().count("<a "),
                        hub[:120],
                    )
                    continue
                persist_candidate = True
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                page.wait_for_timeout(800)
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(600)
                    page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass
                html = page.content()
            except Exception as ex:
                logger.debug("Playwright goto failed %s: %s", hub[:80], ex)
                continue
            soup = soup_from_html(html)
            for a in soup.select("a[href]"):
                full = _abs_url(hub, a.get("href"))
                if not full or full in seen:
                    continue
                if not link_matches_hub_type(hub, full):
                    continue
                seen.add(full)
                out.append(full)
                if len(out) >= limit:
                    break

        write_path = _storage_state_write_target()
        if persist_candidate and write_path is not None:
            _save_context_storage_state(context, write_path)
    finally:
        browser.close()

    return persist_candidate


def collect_hub_links_playwright(
    host_key: str,
    hub_urls: Iterable[str],
    *,
    limit: int,
    seen: set[str],
    max_hubs: int = 12,
) -> list[str]:
    """
    Return up to `limit` new property detail URLs (not already in `seen`), using Chromium instance(s).
    """
    if not playwright_fallback_enabled() or limit <= 0:
        return []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    from src.portal_http import PORTAL_BROWSER_HEADERS

    hub_list = [u for u in hub_urls if u][:max_hubs]
    if not hub_list:
        return []

    out: list[str] = []
    ua = (PORTAL_BROWSER_HEADERS or {}).get("User-Agent") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    try:
        with sync_playwright() as p:
            before = len(out)
            for launch_kw in _launch_variants():
                if len(out) >= limit:
                    break
                try:
                    _session_run(
                        p,
                        launch_kw,
                        host_key=host_key,
                        hub_list=hub_list,
                        limit=limit,
                        seen=seen,
                        out=out,
                        ua=ua,
                    )
                except Exception as ex:
                    logger.debug(
                        "Playwright session failed channel=%s: %s",
                        launch_kw.get("channel") or "bundled",
                        ex,
                    )
                    continue
                if len(out) > before:
                    break
                before = len(out)
    except Exception:
        return out

    return out


def maybe_append_playwright_links(
    host_key: str,
    hub_urls: list[str],
    *,
    httpx_count: int,
    limit: int,
    seen: set[str],
    out: list[str],
    max_hubs: int = 12,
) -> None:
    """If policy allows, fill `out` and `seen` with Playwright-extracted links up to `limit` total in `out`."""
    need = limit - len(out)
    if need <= 0:
        return
    if not _should_try_playwright(host_key, httpx_count, limit):
        return
    extra = collect_hub_links_playwright(
        host_key,
        hub_urls,
        limit=need,
        seen=seen,
        max_hubs=max_hubs,
    )
    for u in extra:
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            break
