#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import _build_portal_listing_panel, _case_missing_verified_gallery_unavailable_reason
from src.case_delist import trusted_property_gallery_urls
from src.db import get_conn
from src.live_enrich_urls import live_enrich_eligible_url


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(",", " ").split():
        if not part.isdigit():
            continue
        n = int(part)
        if n > 0 and n not in out:
            out.append(n)
    return out


def _select_rows(limit: int, ids: list[int] | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"""
                SELECT
                  s.id AS source_item_id,
                  s.id,
                  s.source_name,
                  s.item_url,
                  s.title_original,
                  COALESCE(s.body_original,'') AS body_original,
                  COALESCE(s.image_urls,'') AS image_urls,
                  COALESCE(s.content_kind,'') AS content_kind,
                  COALESCE(s.access_status,'public') AS access_status,
                  COALESCE(s.access_note,'') AS access_note,
                  COALESCE(s.last_checked_at,'') AS last_checked_at,
                  COALESCE(c.title_zh_hant,'') AS title_zh_hant,
                  COALESCE(c.title_zh_hans,'') AS title_zh_hans,
                  COALESCE(c.body_zh_hant,'') AS body_zh_hant,
                  COALESCE(c.body_zh_hans,'') AS body_zh_hans,
                  COALESCE(c.listing_media_json,'[]') AS listing_media_json
                FROM source_items s
                LEFT JOIN content_items c ON c.source_item_id = s.id
                WHERE s.id IN ({marks})
                ORDER BY s.id DESC
                """,
                ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                  s.id AS source_item_id,
                  s.id,
                  s.source_name,
                  s.item_url,
                  s.title_original,
                  COALESCE(s.body_original,'') AS body_original,
                  COALESCE(s.image_urls,'') AS image_urls,
                  COALESCE(s.content_kind,'') AS content_kind,
                  COALESCE(s.access_status,'public') AS access_status,
                  COALESCE(s.access_note,'') AS access_note,
                  COALESCE(s.last_checked_at,'') AS last_checked_at,
                  COALESCE(c.title_zh_hant,'') AS title_zh_hant,
                  COALESCE(c.title_zh_hans,'') AS title_zh_hans,
                  COALESCE(c.body_zh_hant,'') AS body_zh_hant,
                  COALESCE(c.body_zh_hans,'') AS body_zh_hans,
                  COALESCE(c.listing_media_json,'[]') AS listing_media_json
                FROM source_items s
                LEFT JOIN content_items c ON c.source_item_id = s.id
                WHERE COALESCE(s.content_kind,'') = 'jp_listing'
                  AND COALESCE(s.access_status,'public') = 'public'
                ORDER BY s.id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 1), 500000)),),
            ).fetchall()
    return [dict(row) for row in rows]


def delist_public_no_verified_gallery_cases(
    *,
    limit: int = 200000,
    ids: list[int] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = _select_rows(limit=limit, ids=ids)
    scanned = direct_missing = rendered_missing = skipped_non_eligible = 0
    updates: list[tuple[str, int]] = []
    sample: list[dict[str, Any]] = []
    borrowed_ok: list[int] = []

    for idx, row in enumerate(rows, start=1):
        scanned += 1
        if not live_enrich_eligible_url(str(row.get("item_url") or "")):
            skipped_non_eligible += 1
            continue
        if trusted_property_gallery_urls(row, limit=1):
            continue
        direct_missing += 1
        try:
            panel = _build_portal_listing_panel(row)
            reason = _case_missing_verified_gallery_unavailable_reason(row, panel)
        except Exception as exc:
            reason = (
                "公開查詢下架：案件圖片完整度檢查失敗，暫停公開以避免空白圖片或錯誤物件照片誤導。"
                f" 檢查錯誤：{type(exc).__name__}: {str(exc)[:160]}"
            )
        if not reason:
            borrowed_ok.append(int(row.get("source_item_id") or row.get("id") or 0))
            continue
        rendered_missing += 1
        sid = int(row.get("source_item_id") or row.get("id") or 0)
        if sid <= 0:
            continue
        updates.append((reason, sid))
        if len(sample) < 40:
            sample.append(
                {
                    "source_item_id": sid,
                    "source_name": str(row.get("source_name") or ""),
                    "title_original": str(row.get("title_original") or "")[:140],
                    "item_url": str(row.get("item_url") or ""),
                    "reason": reason[:220],
                }
            )
        if idx == 1 or idx % 200 == 0 or idx == len(rows):
            print(
                "no_verified_gallery_progress="
                f"{idx}/{len(rows)} direct_missing={direct_missing} "
                f"rendered_missing={rendered_missing} borrowed_ok={len(borrowed_ok)}",
                file=sys.stderr,
                flush=True,
            )

    updated = 0
    if updates and not dry_run:
        with get_conn() as conn:
            cur = conn.executemany(
                """
                UPDATE source_items
                SET access_status = 'restricted',
                    access_note = ?,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND COALESCE(access_status,'public') = 'public'
                """,
                updates,
            )
            updated = max(0, int(getattr(cur, "rowcount", 0) or 0))
            conn.commit()

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "scanned_rows": int(scanned),
        "skipped_non_eligible": int(skipped_non_eligible),
        "direct_missing_rows": int(direct_missing),
        "borrowed_gallery_ok_rows": len(borrowed_ok),
        "matched_rows": int(rendered_missing),
        "updated_rows": int(updated),
        "sample": sample,
        "borrowed_gallery_ok_ids": borrowed_ok[:120],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delist public jp_listing rows that still have no verified property gallery after same-property borrow."
    )
    parser.add_argument("--dry-run", action="store_true", help="scan only; do not update source_items")
    parser.add_argument("--limit", type=int, default=200000, help="max public jp_listing rows to scan")
    parser.add_argument("--ids", default="", help="optional comma/space separated source_item_id list")
    args = parser.parse_args()

    report = delist_public_no_verified_gallery_cases(
        limit=max(1, min(int(args.limit or 1), 500000)),
        ids=_parse_ids(args.ids),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
