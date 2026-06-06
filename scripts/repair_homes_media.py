from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.config import DB_PATH
from src.homes_media_token import homes_listing_image_tokens
from src.portal_property_crawl import PORTAL_BROWSER_HEADERS
from src.portal_property_playwright import default_playwright_state_path


def clean_text(text: str, *, limit: int = 9000) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s[:limit]


def parse_source_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[\s,;]+", str(raw or "").strip()):
        if part.isdigit():
            n = int(part)
            if n > 0 and n not in out:
                out.append(n)
    return out


def normalize_homes_image_url(url: str, *, image_size: int = 1600) -> str:
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


def is_homes_property_image_url(url: str) -> bool:
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


def image_identity(url: str) -> str:
    try:
        p = urlparse(url)
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        inner = str(q.get("file") or q.get("src") or "").strip().lower()
        if inner:
            return "homes-file:" + inner
        return f"{(p.netloc or '').lower()}{(p.path or '').lower()}?{p.query}"
    except Exception:
        return url.lower()


def listing_media_entries(images: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for img in images:
        url = str(img.get("url") or "").strip()
        if not url:
            continue
        key = image_identity(url)
        if key in seen:
            continue
        seen.add(key)
        entry = {
            "type": "image",
            "url": url,
            "source": "homes_playwright_detail",
            "note": "homes_media_repair",
        }
        alt = clean_text(str(img.get("alt") or ""), limit=120)
        if alt:
            entry["alt"] = alt
        out.append(entry)
        if len(out) >= 36:
            break
    return out


def fetch_rows(source_ids: list[int], *, limit: int, only_empty: bool) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        if source_ids:
            marks = ",".join("?" for _ in source_ids)
            return conn.execute(
                f"""
                SELECT s.*, COALESCE(c.id, 0) AS content_id, COALESCE(c.listing_media_json, '[]') AS listing_media_json
                FROM source_items s
                LEFT JOIN content_items c ON c.source_item_id = s.id
                WHERE s.id IN ({marks})
                ORDER BY s.id
                """,
                source_ids,
            ).fetchall()
        where = [
            "("
            "lower(COALESCE(s.item_url,'')) LIKE '%homes.co.jp%' "
            "OR lower(COALESCE(s.source_name,'')) LIKE '%lifull%' "
            "OR lower(COALESCE(s.source_name,'')) LIKE '%home''s%'"
            ")",
            "COALESCE(s.content_kind, '') = 'jp_listing'",
        ]
        if only_empty:
            where.append(
                "(TRIM(COALESCE(s.image_urls,'')) = '' OR COALESCE(c.listing_media_json, '[]') IN ('', '[]'))"
            )
        sql = f"""
            SELECT s.*, COALESCE(c.id, 0) AS content_id, COALESCE(c.listing_media_json, '[]') AS listing_media_json
            FROM source_items s
            LEFT JOIN content_items c ON c.source_item_id = s.id
            WHERE {' AND '.join(where)}
            ORDER BY
              CASE WHEN TRIM(COALESCE(s.image_urls,'')) = '' THEN 1 ELSE 0 END DESC,
              s.id DESC
            LIMIT ?
        """
        return conn.execute(sql, (max(1, int(limit or 1)),)).fetchall()
    finally:
        conn.close()


def looks_like_bot_challenge(html: str, page_title: str | None = None) -> bool:
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
    # Heuristic: HOMES challenge pages are short and have very few anchors.
    if "homes.co.jp" in h and len(html) < 12000 and html.count("<a ") < 3:
        if "aws" in h or "waf" in h or "challenge" in h:
            return True
    t = str(page_title or "").lower().strip()
    if t and any(x in t for x in ("human verification", "captcha", "challenge")):
        return True
    return False


def browser_snapshot(page: object, item_url: str, *, image_size: int) -> dict[str, object]:
    page.goto(item_url, wait_until="domcontentloaded", timeout=60000)
    for _ in range(120):
        try:
            html = str(page.content() or "")
            title = str(page.title() or "")
        except Exception:
            html, title = "", ""
        if not looks_like_bot_challenge(html, title):
            break
        page.wait_for_timeout(500)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(1800)
    title = ""
    try:
        title = str(page.locator("h1").first.inner_text(timeout=3000) or "").strip()
    except Exception:
        title = ""
    if not title:
        title = str(page.title() or "").strip()
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
          const push = (value, alt, cls) => {
            if (!value) return;
            String(value).split(',').forEach((part) => {
              const token = part.trim().split(/\\s+/)[0];
              if (!token || /^data:/i.test(token)) return;
              const url = abs(token);
              if (/^https?:/i.test(url)) out.push({url, alt: alt || '', cls: cls || ''});
            });
          };
          document.querySelectorAll('img, source, picture, [style]').forEach((el) => {
            const alt = el.getAttribute('alt') || el.getAttribute('aria-label') || '';
            const cls = String(el.className || '');
            attrs.forEach((attr) => push(el.getAttribute(attr), alt, cls));
            const style = el.getAttribute('style') || '';
            [...style.matchAll(/url\\((['\\"]?)(.*?)\\1\\)/g)].forEach((m) => push(m[2], alt, cls));
          });
          return out;
        }
        """
    )
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_images if isinstance(raw_images, list) else []:
        if not isinstance(raw, dict):
            continue
        u = normalize_homes_image_url(str(raw.get("url") or ""), image_size=image_size)
        if not u or not is_homes_property_image_url(u):
            continue
        key = image_identity(u)
        if key in seen:
            continue
        seen.add(key)
        images.append({"url": u, "alt": clean_text(str(raw.get("alt") or ""), limit=120)})
        if len(images) >= 48:
            break
    tokens = homes_listing_image_tokens(item_url)
    if tokens and images:
        matched: list[dict[str, str]] = []
        for img in images:
            u = str(img.get("url") or "").strip()
            if not u:
                continue
            try:
                dec = unquote(u).lower()
            except Exception:
                dec = u.lower()
            if any(tok in dec for tok in tokens):
                matched.append(img)
        if matched:
            images = matched
    return {"title": clean_text(title, limit=240), "body_text": body_text, "images": images}


def build_body(old_body: str, snap: dict[str, object], item_url: str) -> str:
    base = re.split(r"\n\n\[HOMES 明細補圖\]", str(old_body or ""), maxsplit=1)[0].strip()
    if "JavaScript is disabled" in base and len(base) < 1800:
        base = ""
    body_text = clean_text(str(snap.get("body_text") or ""), limit=7200)
    if "JavaScript is disabled" in body_text and len(body_text) < 1200:
        body_text = ""
    images = snap.get("images") if isinstance(snap.get("images"), list) else []
    url_lines = "\n".join(str(x.get("url") or "") for x in images if isinstance(x, dict) and x.get("url"))
    parts = [base] if base else []
    if body_text:
        parts.append("[HOMES 明細補圖]\n" + body_text)
    if url_lines:
        parts.append("[物件參考圖像 URL]\n" + url_lines)
    parts.append(f"來源物件頁（請以官方頁面為準）：{item_url}")
    return "\n\n".join(p for p in parts if p.strip())


def persist_snapshot(row: sqlite3.Row, snap: dict[str, object], *, dry_run: bool = False) -> dict[str, object]:
    images = snap.get("images") if isinstance(snap.get("images"), list) else []
    entries = listing_media_entries([x for x in images if isinstance(x, dict)])
    if not entries:
        return {"ok": False, "source_item_id": int(row["id"]), "reason": "no_images"}
    now = datetime.now(timezone.utc).isoformat()
    image_urls = "\n".join(entry["url"] for entry in entries)
    first = entries[0]["url"]
    new_title = str(snap.get("title") or "").strip()
    old_title = str(row["title_original"] or "").strip()
    title = new_title if new_title and "JavaScript is disabled" not in new_title else old_title
    body = build_body(str(row["body_original"] or ""), snap, str(row["item_url"] or ""))
    media_json = json.dumps(entries, ensure_ascii=False)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "source_item_id": int(row["id"]),
            "title": title,
            "image_count": len(entries),
            "preview": entries[:3],
        }
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        conn.execute(
            """
            UPDATE source_items
            SET title_original = ?,
                body_original = ?,
                image_urls = ?,
                thumbnail_url = ?,
                hero_image_url = ?,
                access_status = 'public',
                access_note = '',
                last_checked_at = ?,
                crawled_at = ?
            WHERE id = ?
            """,
            (title[:400], body, image_urls, first, first, now, now, int(row["id"])),
        )
        if int(row["content_id"] or 0) > 0:
            conn.execute(
                """
                UPDATE content_items
                SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE source_item_id = ?
                """,
                (media_json, int(row["id"])),
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "source_item_id": int(row["id"]),
        "content_id": int(row["content_id"] or 0),
        "image_count": len(entries),
        "first": first,
        "title": title,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair HOME'S jp_listing media using rendered detail pages.")
    parser.add_argument("--source-ids", default="", help="Comma/space separated source_items ids.")
    parser.add_argument("--limit", type=int, default=50, help="Rows to repair when --source-ids is omitted.")
    parser.add_argument("--sleep-sec", type=float, default=0.8)
    parser.add_argument("--image-size", type=int, default=1600)
    parser.add_argument("--include-filled", action="store_true", help="Also refresh rows that already have media.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-report", default="")
    parser.add_argument("--headful", action="store_true", help="Launch a visible browser window (solve WAF if needed).")
    parser.add_argument("--channel", default="", help="Chromium channel: chrome | msedge | chromium (optional).")
    parser.add_argument(
        "--storage-state",
        default="",
        help="Path to Playwright storage_state.json (cookies). Default: data/playwright_storage_state.json",
    )
    args = parser.parse_args()

    source_ids = parse_source_ids(args.source_ids)
    rows = fetch_rows(source_ids, limit=max(1, int(args.limit or 1)), only_empty=not args.include_filled)
    print(f"homes_media_repair rows={len(rows)} source_ids={source_ids or 'auto'} dry_run={bool(args.dry_run)}", flush=True)
    report: dict[str, object] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "rows": [],
        "source_ids": source_ids,
        "dry_run": bool(args.dry_run),
    }
    if not rows:
        print("done no rows", flush=True)
        return

    from playwright.sync_api import sync_playwright

    ua = PORTAL_BROWSER_HEADERS.get("User-Agent") or "Mozilla/5.0"
    with sync_playwright() as p:
        launch_kw = {
            "headless": not bool(args.headful),
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        ch = str(args.channel or "").strip()
        if ch:
            launch_kw["channel"] = ch
        browser = p.chromium.launch(**launch_kw)
        try:
            ctx_kw = {
                "user_agent": ua,
                "locale": "ja-JP",
                "viewport": {"width": 1365, "height": 900},
                "extra_http_headers": {"Accept-Language": "ja,zh-TW;q=0.9,en-US;q=0.8,en;q=0.7"},
            }
            storage_path = str(args.storage_state or "").strip()
            if not storage_path:
                storage_path = str(default_playwright_state_path())
            if storage_path and Path(storage_path).is_file():
                ctx_kw["storage_state"] = storage_path
            ctx = browser.new_context(**ctx_kw)
            try:
                ctx.add_init_script(
                    "(() => { try { Object.defineProperty(navigator,'webdriver',{get:()=>undefined}); } catch(e){} })();"
                )
            except Exception:
                pass
            try:
                for idx, row in enumerate(rows, start=1):
                    url = str(row["item_url"] or "").strip()
                    if not url:
                        continue
                    page = ctx.new_page()
                    try:
                        snap = browser_snapshot(page, url, image_size=max(0, int(args.image_size or 0)))
                        # Persist cookies after a successful non-challenge load.
                        try:
                            html = str(page.content() or "")
                            title = str(page.title() or "")
                        except Exception:
                            html, title = "", ""
                        if storage_path and not looks_like_bot_challenge(html, title):
                            try:
                                ctx.storage_state(path=storage_path)
                            except Exception:
                                pass
                        result = persist_snapshot(row, snap, dry_run=bool(args.dry_run))
                    except Exception as exc:
                        result = {"ok": False, "source_item_id": int(row["id"]), "reason": f"{type(exc).__name__}: {exc}"}
                    finally:
                        page.close()
                    rows_report = report["rows"]
                    assert isinstance(rows_report, list)
                    rows_report.append(result)
                    print(
                        f"[{idx}/{len(rows)}] source_id={row['id']} ok={result.get('ok')} "
                        f"images={result.get('image_count', 0)} reason={result.get('reason', '')}",
                        flush=True,
                    )
                    if args.sleep_sec and idx < len(rows):
                        time.sleep(max(0.0, float(args.sleep_sec)))
            finally:
                ctx.close()
        finally:
            browser.close()

    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if args.write_report:
        out = Path(args.write_report)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_count = sum(1 for row in report["rows"] if isinstance(row, dict) and row.get("ok"))
    print(f"done repaired={ok_count}/{len(rows)}", flush=True)


if __name__ == "__main__":
    main()
