"""
Offline cleanup for HOMES listings where `content_items.listing_media_json` contains
unrelated recommended-listing thumbnails (cross-listing contamination).

Background:
  - Older crawls sometimes failed to derive HOMES listing-specific media tokens from
    `item_url` (b-<digits>), so image extraction kept "recommended listings" images.
  - We already provide `scripts/clean_homes_image_urls_by_tokens.py` to fix
    `source_items.image_urls`. This script does the same for `listing_media_json`.

Strategy:
  - Derive listing-specific image tokens from `source_items.item_url` (b-<digits>) when available.
  - Parse `content_items.listing_media_json` (list of strings/dicts).
  - Keep only entries whose URL matches any token (or, for token-less chintai
    pages, entries that pass HOME'S URL/context noise filters).

This script does not fetch network resources; it only rewrites existing DB rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DB_PATH
from src.homes_media_filter import is_homes_non_property_media, media_entry_url_context
from src.homes_media_token import homes_listing_image_tokens

_URL_KEYS = ("url", "src", "image", "img", "thumbnail", "thumb", "href")


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


def _entry_url(entry) -> str:
    url, _context = media_entry_url_context(entry)
    return url


def _entry_is_clean(entry) -> bool:
    url, context = media_entry_url_context(entry)
    return bool(url) and not is_homes_non_property_media(url, context)


def _dedupe_keep_order(entries: list) -> list:
    out: list = []
    seen: set[str] = set()
    for e in entries:
        u = _entry_url(e)
        if not u:
            continue
        try:
            key = unquote(u).lower()
        except Exception:
            key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Clean HOMES content_items.listing_media_json by listing token match"
    )
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
    ap.add_argument("--commit-every", type=int, default=200, help="commit batch size (default 200)")
    ap.add_argument("--max-entries", type=int, default=60, help="cap kept entries per row (default 60)")
    ap.add_argument(
        "--clear-when-no-match",
        action="store_true",
        help="when tokens exist but no media entries match (and cannot rebuild), clear listing_media_json to []",
    )
    args = ap.parse_args()

    ids = _parse_ids(args.ids)
    lim = max(1, min(int(args.limit or 1), 200000))
    commit_every = max(20, min(int(args.commit_every or 200), 2000))
    max_entries = max(1, min(int(args.max_entries or 60), 200))

    conn = sqlite3.connect(str(DB_PATH), timeout=90.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=90000")

    try:
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"""
                SELECT
                  s.id AS source_item_id,
                  s.item_url,
                  COALESCE(s.image_urls,'') AS image_urls,
                  COALESCE(c.listing_media_json,'[]') AS listing_media_json
                FROM source_items s
                JOIN content_items c ON c.source_item_id = s.id
                WHERE s.id IN ({marks})
                ORDER BY s.id
                """,
                ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                  s.id AS source_item_id,
                  s.item_url,
                  COALESCE(s.image_urls,'') AS image_urls,
                  COALESCE(c.listing_media_json,'[]') AS listing_media_json
                FROM source_items s
                JOIN content_items c ON c.source_item_id = s.id
                WHERE lower(COALESCE(s.item_url,'')) LIKE '%homes.co.jp%'
                  AND TRIM(COALESCE(c.listing_media_json,'[]')) NOT IN ('', '[]')
                ORDER BY s.id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()

        scanned = len(rows)
        updated = 0
        skipped = 0
        no_token_match = 0
        invalid_json = 0
        rebuilt_from_image_urls = 0
        cleared = 0

        pending: list[tuple[str, int]] = []
        for r in rows:
            sid = int(r["source_item_id"])
            item_url = str(r["item_url"] or "").strip()
            raw = str(r["listing_media_json"] or "").strip() or "[]"
            tokens = homes_listing_image_tokens(item_url)
            if not tokens:
                clean_data = _dedupe_keep_order([e for e in data if _entry_is_clean(e)])[:max_entries]
                new_raw = json.dumps(clean_data, ensure_ascii=False)
                if new_raw.strip() == raw.strip():
                    skipped += 1
                    continue
                updated += 1
                if args.dry_run:
                    continue
                pending.append((new_raw, sid))
                if len(pending) >= commit_every:
                    conn.executemany(
                        "UPDATE content_items SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE source_item_id = ?",
                        pending,
                    )
                    conn.commit()
                    pending.clear()
                continue
            try:
                data = json.loads(raw)
            except Exception:
                invalid_json += 1
                continue
            if not isinstance(data, list) or not data:
                skipped += 1
                continue

            clean_data = [e for e in data if _entry_is_clean(e)]
            matched = [e for e in clean_data if _match_any_token(_entry_url(e), tokens)]
            if not matched:
                # Fallback: rebuild from source_items.image_urls when we can.
                img_lines = [x.strip() for x in str(r["image_urls"] or "").splitlines() if x.strip()]
                img_matched = [u for u in img_lines if _entry_is_clean(u) and _match_any_token(u, tokens)]
                if not img_matched:
                    no_token_match += 1
                    if not args.clear_when_no_match:
                        continue
                    new_raw = "[]"
                    if raw.strip() in ("", "[]"):
                        continue
                    updated += 1
                    cleared += 1
                    if args.dry_run:
                        continue
                    pending.append((new_raw, sid))
                    if len(pending) >= commit_every:
                        conn.executemany(
                            "UPDATE content_items SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE source_item_id = ?",
                            pending,
                        )
                        conn.commit()
                        pending.clear()
                    continue
                new_entries = [
                    {
                        "type": "image",
                        "url": u,
                        "source": "source_items.image_urls",
                        "note": "homes_listing_media_json_rebuild",
                    }
                    for u in list(dict.fromkeys(img_matched))[:max_entries]
                ]
                new_raw = json.dumps(new_entries, ensure_ascii=False)
                if new_raw.strip() == raw.strip():
                    continue
                updated += 1
                rebuilt_from_image_urls += 1
                if args.dry_run:
                    continue
                pending.append((new_raw, sid))
                if len(pending) >= commit_every:
                    conn.executemany(
                        "UPDATE content_items SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE source_item_id = ?",
                        pending,
                    )
                    conn.commit()
                    pending.clear()
                continue

            if args.mode == "filter":
                new_entries = matched
            else:
                # reorder: matched first, then keep remaining (dedup will remove overlaps)
                matched_set = {_entry_url(e) for e in matched if _entry_url(e)}
                tail = [e for e in clean_data if _entry_url(e) and _entry_url(e) not in matched_set]
                new_entries = [*matched, *tail]

            new_entries = _dedupe_keep_order(new_entries)[:max_entries]
            new_raw = json.dumps(new_entries, ensure_ascii=False)
            if new_raw.strip() == raw.strip():
                continue
            updated += 1
            if args.dry_run:
                continue
            pending.append((new_raw, sid))
            if len(pending) >= commit_every:
                conn.executemany(
                    "UPDATE content_items SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE source_item_id = ?",
                    pending,
                )
                conn.commit()
                pending.clear()

        if pending and not args.dry_run:
            conn.executemany(
                "UPDATE content_items SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE source_item_id = ?",
                pending,
            )
            conn.commit()
            pending.clear()

        print(
            f"scanned={scanned} updated={updated} skipped={skipped} "
            f"no_token_match={no_token_match} invalid_json={invalid_json} "
            f"rebuilt_from_image_urls={rebuilt_from_image_urls} cleared={cleared} "
            f"dry_run={bool(args.dry_run)} mode={args.mode} max_entries={max_entries}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
