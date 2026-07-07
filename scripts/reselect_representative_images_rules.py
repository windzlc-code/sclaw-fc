#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _duplicate_source_item_ids(app: Any, *, limit: int, offset: int) -> list[int]:
    lim = max(1, min(50000, int(limit or 1)))
    off = max(0, int(offset or 0))
    with app.get_conn() as conn:
        rows = conn.execute(
            """
            WITH duplicate_urls AS (
                SELECT representative_static_url
                FROM case_representative_images
                WHERE COALESCE(status, '') IN ('selected', 'fallback', 'rules')
                  AND TRIM(COALESCE(representative_static_url, '')) <> ''
                GROUP BY representative_static_url
                HAVING COUNT(*) > 1
            )
            SELECT r.source_item_id
            FROM case_representative_images r
            JOIN duplicate_urls d ON d.representative_static_url = r.representative_static_url
            LEFT JOIN content_items c ON c.source_item_id = r.source_item_id
            WHERE COALESCE(r.status, '') IN ('selected', 'fallback', 'rules')
            ORDER BY COALESCE(c.featured_weight, 0) DESC, r.selected_at DESC, r.source_item_id DESC
            LIMIT ? OFFSET ?
            """,
            (lim, off),
        ).fetchall()
    return [int(row[0]) for row in rows if int(row[0] or 0) > 0]


def _render_suspect_source_item_ids(app: Any, *, limit: int, offset: int) -> list[int]:
    lim = max(1, min(50000, int(limit or 1)))
    off = max(0, int(offset or 0))
    with app.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT source_item_id
            FROM case_representative_images
            WHERE COALESCE(status, '') IN ('selected', 'fallback', 'rules')
              AND (
                LOWER(COALESCE(representative_url, '')) LIKE '%render%'
                OR LOWER(COALESCE(representative_url, '')) LIKE '%perspective%'
                OR COALESCE(representative_url, '') LIKE '%パース%'
                OR COALESCE(representative_url, '') LIKE '%完成予想%'
                OR COALESCE(representative_url, '') LIKE '%イメージ図%'
                OR COALESCE(reason, '') LIKE '%渲染%'
                OR COALESCE(reason, '') LIKE '%パース%'
                OR COALESCE(reason, '') LIKE '%完成予想%'
              )
            ORDER BY selected_at DESC, source_item_id DESC
            LIMIT ? OFFSET ?
            """,
            (lim, off),
        ).fetchall()
    return [int(row[0]) for row in rows if int(row[0] or 0) > 0]


def _yahoo_opaque_source_item_ids(app: Any, *, limit: int, offset: int) -> list[int]:
    lim = max(1, min(50000, int(limit or 1)))
    off = max(0, int(offset or 0))
    with app.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT r.source_item_id
            FROM case_representative_images r
            LEFT JOIN content_items c ON c.source_item_id = r.source_item_id
            WHERE COALESCE(r.status, '') IN ('selected', 'fallback', 'rules')
              AND LOWER(COALESCE(r.representative_url, '')) LIKE 'https://realestate-pctr.c.yimg.jp/m%'
            ORDER BY COALESCE(c.featured_weight, 0) DESC, r.selected_at DESC, r.source_item_id DESC
            LIMIT ? OFFSET ?
            """,
            (lim, off),
        ).fetchall()
    return [int(row[0]) for row in rows if int(row[0] or 0) > 0]


def _row_for_source_item(app: Any, source_item_id: int) -> dict[str, Any] | None:
    with app.get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                c.id AS content_id,
                c.source_item_id,
                c.seo_title,
                c.title_zh_hant,
                c.title_zh_hans,
                c.case_jp_region_override,
                c.case_transit_override,
                c.listing_media_json,
                c.featured_weight,
                s.id,
                s.source_name,
                s.item_url,
                s.title_original,
                substr(COALESCE(s.body_original, ''), 1, 1200) AS body_original,
                s.image_urls,
                s.thumbnail_url,
                s.hero_image_url,
                s.last_checked_at,
                s.source_category
            FROM source_items s
            JOIN content_items c ON c.source_item_id = s.id
            WHERE s.id = ?
            LIMIT 1
            """,
            (int(source_item_id),),
        ).fetchone()
    return dict(row) if row else None


def _remaining_duplicate_groups(app: Any) -> tuple[int, int]:
    with app.get_conn() as conn:
        groups = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT representative_static_url
                FROM case_representative_images
                WHERE COALESCE(status, '') IN ('selected', 'fallback', 'rules')
                  AND TRIM(COALESCE(representative_static_url, '')) <> ''
                GROUP BY representative_static_url
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        rows = conn.execute(
            """
            WITH duplicate_urls AS (
                SELECT representative_static_url
                FROM case_representative_images
                WHERE COALESCE(status, '') IN ('selected', 'fallback', 'rules')
                  AND TRIM(COALESCE(representative_static_url, '')) <> ''
                GROUP BY representative_static_url
                HAVING COUNT(*) > 1
            )
            SELECT COUNT(*)
            FROM case_representative_images r
            JOIN duplicate_urls d ON d.representative_static_url = r.representative_static_url
            WHERE COALESCE(r.status, '') IN ('selected', 'fallback', 'rules')
            """
        ).fetchone()[0]
    return int(groups or 0), int(rows or 0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reselect duplicate/render/Yahoo opaque representative images with local rules only."
    )
    parser.add_argument("--mode", choices=("duplicates", "render", "yahoo-opaque", "both", "all"), default="duplicates")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--audit-gallery", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    import app  # noqa: WPS433

    app.is_gemini_configured = lambda: False
    before_groups, before_rows = _remaining_duplicate_groups(app)
    ids: list[int] = []
    if args.mode in {"duplicates", "both", "all"}:
        ids.extend(_duplicate_source_item_ids(app, limit=args.limit, offset=args.offset))
    if args.mode in {"render", "both", "all"}:
        ids.extend(_render_suspect_source_item_ids(app, limit=args.limit, offset=args.offset))
    if args.mode in {"yahoo-opaque", "all"}:
        ids.extend(_yahoo_opaque_source_item_ids(app, limit=args.limit, offset=args.offset))
    seen: set[int] = set()
    ids = [sid for sid in ids if not (sid in seen or seen.add(sid))]

    selected = 0
    emptied = 0
    failed = 0
    unchanged = 0
    samples: list[dict[str, Any]] = []
    started = time.time()
    for index, sid in enumerate(ids, start=1):
        row = _row_for_source_item(app, sid)
        if not row:
            failed += 1
            continue
        title = str(row.get("seo_title") or row.get("title_zh_hans") or row.get("title_original") or "")[:80]
        if args.dry_run:
            result = {"ok": True, "status": "dry_run", "source_item_id": sid, "reason": title}
        else:
            result = app._case_select_representative_image_with_ai(
                row,
                force=True,
                audit_gallery_after=bool(args.audit_gallery),
            )
        status = str(result.get("status") or "")
        if result.get("ok") and status not in {"empty", "dry_run"}:
            selected += 1
        elif status == "empty":
            emptied += 1
        elif status == "dry_run":
            unchanged += 1
        else:
            failed += 1
        if len(samples) < 20:
            samples.append(
                {
                    "source_item_id": sid,
                    "title": title,
                    "status": status,
                    "static_url": result.get("static_url") or "",
                    "reason": str(result.get("reason") or "")[:160],
                }
            )
        if index % 25 == 0:
            print(
                json.dumps(
                    {
                        "progress": index,
                        "total": len(ids),
                        "selected": selected,
                        "emptied": emptied,
                        "failed": failed,
                        "elapsed_sec": round(time.time() - started, 1),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if args.sleep > 0:
            time.sleep(max(0.0, float(args.sleep)))

    after_groups, after_rows = _remaining_duplicate_groups(app)
    print(
        json.dumps(
            {
                "ok": True,
                "mode": args.mode,
                "dry_run": bool(args.dry_run),
                "processed": len(ids),
                "selected": selected,
                "emptied": emptied,
                "failed": failed,
                "unchanged": unchanged,
                "duplicate_groups_before": before_groups,
                "duplicate_rows_before": before_rows,
                "duplicate_groups_after": after_groups,
                "duplicate_rows_after": after_rows,
                "elapsed_sec": round(time.time() - started, 1),
                "samples": samples,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
