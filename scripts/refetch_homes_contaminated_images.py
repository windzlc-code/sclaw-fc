"""
Refetch and repair HOMES (homes.co.jp) listings whose stored `source_items.image_urls`
appear contaminated with recommended-listing thumbnails from a different b-id.

Why this exists:
  - When HOMES b-id token extraction failed historically, our crawls could capture
    "recommended listings" images and save them into `image_urls`, causing many
    unrelated /case/{id} pages to show the same photos.
  - We now have robust b-id token extraction and HOMES image filtering, but older
    rows remain contaminated and cannot be fixed by simple filtering if they have
    zero in-listing matches.

What it does:
  - Scans HOMES rows where `image_urls` contains HOMES "sale image" patterns but none
    match the listing's own b-id-derived tokens.
  - Refetches the live listing page via `fetch_property_detail` and merges new images
    ahead of the old ones.
  - If any merged images match the listing tokens, keeps only matched images (fully
    removes cross-listing contamination).

This script updates `source_items` only. It does not modify `content_items`.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DB_PATH
from src.crawler import BROWSER_HEADERS
from src.homes_media_token import homes_listing_image_tokens
from src.portal_property_crawl import fetch_property_detail

_RE_HOMES_SALE_A = re.compile(r"img\.homes\.jp/\d{1,10}/sale/\d{1,10}/", re.I)
_RE_HOMES_SALE_B = re.compile(r"/data/\d{1,10}/sale/image/\d{6,8}-", re.I)


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(",", " ").split():
        if part.isdigit():
            n = int(part)
            if n > 0 and n not in out:
                out.append(n)
    return out


def _match_any_token(url: str, tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return False
    try:
        dec = unquote(str(url)).lower()
    except Exception:
        dec = str(url).lower()
    return any(tok in dec for tok in tokens)


def _looks_like_homes_sale_image(url: str) -> bool:
    try:
        dec = unquote(str(url)).lower()
    except Exception:
        dec = str(url).lower()
    return bool(_RE_HOMES_SALE_A.search(dec) or _RE_HOMES_SALE_B.search(dec))


def _is_contaminated_image_blob(image_urls: str, *, item_url: str) -> bool:
    tokens = homes_listing_image_tokens(item_url)
    if not tokens:
        return False
    lines = [x.strip() for x in str(image_urls or "").splitlines() if x.strip()]
    if not lines:
        return False
    any_match = any(_match_any_token(u, tokens) for u in lines)
    if any_match:
        return False
    # If it contains HOMES sale-image patterns but none match this listing, it's very likely contamination.
    return any(_looks_like_homes_sale_image(u) for u in lines)


def _merge_image_lines(existing: str, new_imgs: list[str]) -> list[str]:
    old = [x.strip() for x in str(existing or "").splitlines() if x.strip()]
    merged = list(dict.fromkeys([*(new_imgs or []), *old]))
    return merged


def main() -> None:
    ap = argparse.ArgumentParser(description="Refetch HOMES listings with contaminated image_urls")
    ap.add_argument("--dry-run", action="store_true", help="print stats only; no DB writes")
    ap.add_argument("--limit", type=int, default=40, help="max rows to refetch (default 40)")
    ap.add_argument("--sleep", type=float, default=0.35, help="sleep between requests (default 0.35s)")
    ap.add_argument("--ids", type=str, default="", help="optional: space/comma separated source_item_id list")
    ap.add_argument("--fetch-cap-mult", type=int, default=60, help="scan cap multiplier (default 60)")
    ap.add_argument("--pick-mult", type=int, default=12, help="over-pick multiplier to tolerate skips (default 12)")
    ap.add_argument("--commit-every", type=int, default=10, help="commit batch size (default 10)")
    args = ap.parse_args()

    ids = _parse_ids(args.ids)
    lim = max(1, min(int(args.limit or 1), 2000))
    sleep_s = max(0.0, min(float(args.sleep or 0.0), 8.0))
    fetch_cap_mult = max(2, min(int(args.fetch_cap_mult or 60), 200))
    pick_mult = max(2, min(int(args.pick_mult or 12), 80))
    commit_every = max(1, min(int(args.commit_every or 10), 200))

    conn = sqlite3.connect(str(DB_PATH), timeout=120.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")

    try:
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"""
                SELECT id, item_url, COALESCE(image_urls,'') AS image_urls
                FROM source_items
                WHERE id IN ({marks})
                ORDER BY id
                """,
                ids,
            ).fetchall()
        else:
            fetch_cap = lim * fetch_cap_mult
            rows = conn.execute(
                """
                SELECT id, item_url, COALESCE(image_urls,'') AS image_urls
                FROM source_items
                WHERE lower(COALESCE(item_url,'')) LIKE '%homes.co.jp%'
                  AND instr(lower(COALESCE(item_url,'')),'/b-') > 0
                  AND COALESCE(trim(item_url),'') LIKE 'http%'
                  AND trim(COALESCE(image_urls,'')) != ''
                ORDER BY datetime(COALESCE(last_checked_at,'1970-01-01')) DESC, id DESC
                LIMIT ?
                """,
                (fetch_cap,),
            ).fetchall()

        scanned = len(rows)
        picked: list[sqlite3.Row] = []
        pick_cap = max(lim, lim * pick_mult)
        for r in rows:
            item_url = str(r["item_url"] or "").strip()
            if not item_url:
                continue
            if _is_contaminated_image_blob(str(r["image_urls"] or ""), item_url=item_url):
                picked.append(r)
                if len(picked) >= pick_cap:
                    break

        ok = err = skip = 0
        skip_no_token_hit = 0
        updated = 0
        now = datetime.now(timezone.utc).isoformat()
        pending: list[tuple[str, str, str, str, str, int]] = []

        with httpx.Client(timeout=25.0, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            for i, r in enumerate(picked):
                if ok >= lim:
                    break
                sid = int(r["id"])
                item_url = str(r["item_url"] or "").strip()
                existing = str(r["image_urls"] or "")
                if sleep_s > 0 and i > 0:
                    time.sleep(sleep_s)
                try:
                    title, body_original, imgs = fetch_property_detail(client, item_url)
                except Exception:
                    err += 1
                    continue
                if not str(body_original or "").strip() and not (imgs or []):
                    skip += 1
                    continue

                tokens = homes_listing_image_tokens(item_url)
                new_imgs = list(imgs or [])
                if tokens:
                    new_matched = [u for u in new_imgs if _match_any_token(u, tokens)]
                    if not new_matched:
                        skip_no_token_hit += 1
                        continue
                    new_imgs = new_matched

                merged = _merge_image_lines(existing, new_imgs)
                if tokens:
                    merged = [u for u in merged if _match_any_token(u, tokens)] or merged
                merged = merged[:60]
                new_blob = "\n".join(merged)
                title_new = str(title or "").strip()[:200]
                body_new = str(body_original or "").strip()

                if args.dry_run:
                    ok += 1
                    continue

                pending.append((title_new, body_new, new_blob, now, now, sid))
                updated += 1
                ok += 1
                if len(pending) >= commit_every:
                    conn.executemany(
                        """
                        UPDATE source_items
                        SET title_original = ?,
                            body_original = ?,
                            image_urls = ?,
                            last_checked_at = ?,
                            crawled_at = ?
                        WHERE id = ?
                        """,
                        pending,
                    )
                    conn.commit()
                    pending.clear()

        if pending and not args.dry_run:
            conn.executemany(
                """
                UPDATE source_items
                SET title_original = ?,
                    body_original = ?,
                    image_urls = ?,
                    last_checked_at = ?,
                    crawled_at = ?
                WHERE id = ?
                """,
                pending,
            )
            conn.commit()
            pending.clear()

        print(
            f"scanned={scanned} picked={len(picked)} updated={updated} "
            f"ok={ok} err={err} skip={skip} skip_no_token_hit={skip_no_token_hit} "
            f"dry_run={bool(args.dry_run)}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
