"""
Offline cleanup for HOMES listings where `source_items.image_urls` contains unrelated
recommended-listing thumbnails.

Strategy:
  - Derive listing-specific image tokens from `item_url` (b-<digits>).
  - If any image URLs match those tokens, rewrite `image_urls` to keep matches first
    (or only matches, depending on mode).

This script does not fetch network resources; it only rewrites existing DB rows.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DB_PATH
from src.homes_media_token import homes_listing_image_tokens


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(",", " ").split():
        if part.isdigit():
            n = int(part)
            if n > 0 and n not in out:
                out.append(n)
    return out


def _match_any_token(url: str, tokens: tuple[str, ...]) -> bool:
    s = str(url or "").strip()
    if not s:
        return False
    try:
        dec = unquote(s).lower()
    except Exception:
        dec = s.lower()
    if (
        "homes.jp/smallimg/image.php" in dec
        and "cdn-lambda-img.cloud.ielove.jp/image/sale/" in dec
    ):
        return True
    if not tokens:
        return False
    return any(tok in dec for tok in tokens)


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean HOMES source_items.image_urls by listing token match")
    ap.add_argument("--dry-run", action="store_true", help="print stats only; no DB writes")
    ap.add_argument("--limit", type=int, default=50000, help="max rows to scan (default 50000)")
    ap.add_argument(
        "--mode",
        type=str,
        default="filter",
        choices=("filter", "reorder"),
        help="filter=keep only matched; reorder=matched first then rest",
    )
    ap.add_argument(
        "--ids",
        type=str,
        default="",
        help="optional: space/comma separated source_item_id list",
    )
    ap.add_argument("--commit-every", type=int, default=250, help="commit batch size (default 250)")
    ap.add_argument(
        "--clear-when-no-match",
        action="store_true",
        help="when tokens exist but no images match, clear image_urls to avoid cross-listing contamination",
    )
    args = ap.parse_args()

    ids = _parse_ids(args.ids)
    lim = max(1, min(int(args.limit or 1), 200000))
    commit_every = max(20, min(int(args.commit_every or 250), 2000))

    conn = sqlite3.connect(str(DB_PATH), timeout=90.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=90000")

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
            rows = conn.execute(
                """
                SELECT id, item_url, COALESCE(image_urls,'') AS image_urls
                FROM source_items
                WHERE lower(COALESCE(item_url,'')) LIKE '%homes.co.jp%'
                  AND instr(lower(COALESCE(item_url,'')),'/b-') > 0
                  AND trim(COALESCE(image_urls,'')) != ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()

        scanned = len(rows)
        updated = 0
        skipped = 0
        no_token_match = 0
        cleared = 0

        pending: list[tuple[str, int]] = []
        for r in rows:
            sid = int(r["id"])
            item_url = str(r["item_url"] or "").strip()
            blob = str(r["image_urls"] or "")
            tokens = homes_listing_image_tokens(item_url)
            if not tokens:
                skipped += 1
                continue
            lines = [x.strip() for x in blob.splitlines() if x.strip()]
            if not lines:
                skipped += 1
                continue
            matched = [ln for ln in lines if _match_any_token(ln, tokens)]
            if not matched:
                no_token_match += 1
                if not args.clear_when_no_match:
                    continue
                new_blob = ""
                if new_blob.strip() == blob.strip():
                    continue
                if args.dry_run:
                    updated += 1
                    cleared += 1
                    continue
                pending.append((new_blob, sid))
                updated += 1
                cleared += 1
                if len(pending) >= commit_every:
                    conn.executemany("UPDATE source_items SET image_urls = ? WHERE id = ?", pending)
                    conn.commit()
                    pending.clear()
                continue
            if args.mode == "filter":
                new_lines = matched
            else:
                matched_set = set(matched)
                new_lines = [*matched, *[ln for ln in lines if ln not in matched_set]]
            new_lines = list(dict.fromkeys(new_lines))[:80]
            new_blob = "\n".join(new_lines)
            if new_blob.strip() == blob.strip():
                continue
            if args.dry_run:
                updated += 1
                continue
            pending.append((new_blob, sid))
            updated += 1
            if len(pending) >= commit_every:
                conn.executemany("UPDATE source_items SET image_urls = ? WHERE id = ?", pending)
                conn.commit()
                pending.clear()

        if pending and not args.dry_run:
            conn.executemany("UPDATE source_items SET image_urls = ? WHERE id = ?", pending)
            conn.commit()
            pending.clear()

        print(
            f"scanned={scanned} updated={updated} skipped={skipped} "
            f"no_token_match={no_token_match} cleared={cleared} "
            f"dry_run={bool(args.dry_run)} mode={args.mode}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
