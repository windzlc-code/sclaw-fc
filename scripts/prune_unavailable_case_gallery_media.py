"""Verify listing-gallery files and remove source entries that cannot be served.

This is deliberately a low-priority, resumable maintenance job.  It reuses the
same media validation used by case pages, downloads each candidate once into
the local cache, and only removes a gallery URL when the server cannot obtain
or validate it.  Run with ``--apply``; without that flag it exits safely.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app import _case_audit_gallery_images_for_row, get_conn


def _load_rows(after_id: int, limit: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              c.id AS content_id, c.source_item_id, c.seo_title, c.title_zh_hant,
              c.title_zh_hans, c.case_jp_region_override, c.listing_media_json,
              c.topic_category, s.id, s.source_name, s.source_category, s.item_url,
              s.title_original, substr(COALESCE(s.body_original, ''), 1, 50000) AS body_original,
              s.image_urls, s.thumbnail_url, s.hero_image_url, s.last_checked_at
            FROM content_items c
            JOIN source_items s ON s.id = c.source_item_id
            WHERE COALESCE(s.content_kind, '') = 'jp_listing'
              AND s.id > ?
              AND (
                TRIM(COALESCE(s.hero_image_url, '')) <> ''
                OR TRIM(COALESCE(s.thumbnail_url, '')) <> ''
                OR TRIM(COALESCE(s.image_urls, '')) <> ''
                OR TRIM(COALESCE(c.listing_media_json, '[]')) NOT IN ('', '[]')
              )
            ORDER BY s.id ASC
            LIMIT ?
            """,
            (int(after_id), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove unavailable case-gallery images after server-side verification.")
    parser.add_argument("--apply", action="store_true", help="Required: performs cleanup after each image is verified.")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--max-images", type=int, default=16)
    parser.add_argument("--start-after", type=int, default=0)
    parser.add_argument("--max-cases", type=int, default=0, help="0 scans every eligible listing.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Pause between cases to protect live traffic.")
    parser.add_argument("--state-file", default="data/unavailable_gallery_prune_state.json")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("Refusing to change gallery data without --apply.")

    batch_size = max(1, min(80, int(args.batch_size)))
    max_images = max(1, min(80, int(args.max_images)))
    max_cases = max(0, int(args.max_cases))
    state_path = Path(args.state_file)
    cursor = max(0, int(args.start_after))
    summary: dict[str, Any] = {
        "started_at": int(time.time()),
        "last_source_item_id": cursor,
        "processed_cases": 0,
        "classified_images": 0,
        "unavailable_images_removed": 0,
        "low_quality_images_removed": 0,
        "errors": 0,
    }
    while True:
        rows = _load_rows(cursor, batch_size)
        if not rows or (max_cases and summary["processed_cases"] >= max_cases):
            break
        for row in rows:
            cursor = int(row.get("source_item_id") or row.get("id") or cursor)
            if max_cases and summary["processed_cases"] >= max_cases:
                break
            try:
                result = _case_audit_gallery_images_for_row(
                    row,
                    limit=max_images,
                    remove_low_quality=True,
                    remove_unavailable=True,
                )
                summary["classified_images"] += int(result.get("classified") or 0)
                summary["unavailable_images_removed"] += int(result.get("removed") or 0)
                summary["low_quality_images_removed"] += int(result.get("low_quality") or 0)
            except Exception:
                summary["errors"] += 1
            summary["processed_cases"] += 1
            summary["last_source_item_id"] = cursor
            _write_state(state_path, summary)
            if args.sleep > 0:
                time.sleep(min(5.0, float(args.sleep)))
    summary["finished_at"] = int(time.time())
    _write_state(state_path, summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
