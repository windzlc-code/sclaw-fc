import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DB_PATH  # noqa: E402
from src.portal_case_search import is_suumo_non_property_image_url, ordered_listing_image_urls  # noqa: E402

_SUUMO_DETAIL_URL_RE = re.compile(
    r"/(?:chintai|ms/(?:chuko|shinchiku)|ikkodate|chukoikkodate)/.*(?:jnc_|nc_|\bnc=)",
    re.I,
)


def _media_json_from_urls(urls: list[str]) -> str:
    entries = [
        {
            "type": "image",
            "url": u,
            "source": "repair_suumo_case_gallery_media",
            "note": "filtered_original_site_gallery",
        }
        for u in urls
        if str(u or "").strip()
    ]
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"))


def _clean_row(row: sqlite3.Row, *, limit: int) -> tuple[str, str]:
    urls = ordered_listing_image_urls(
        str(row["image_urls"] or ""),
        str(row["body_original"] or ""),
        str(row["listing_media_json"] or "[]"),
        item_url=str(row["item_url"] or ""),
        limit=limit,
    )
    return "\n".join(urls), _media_json_from_urls(urls)


def _row_has_suumo_pollution(row: sqlite3.Row) -> bool:
    text = f"{row['image_urls'] or ''}\n{row['listing_media_json'] or ''}"
    for u in re.findall(r"https?://[^\s\"\]})]+", text):
        if is_suumo_non_property_image_url(u):
            return True
    return False


def run(args: argparse.Namespace) -> dict[str, Any]:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    where = """
        WHERE (
            si.content_kind = 'jp_listing'
            OR COALESCE(ci.listing_media_json, '[]') != '[]'
            OR TRIM(COALESCE(si.image_urls, '')) != ''
          )
          AND si.item_url LIKE '%suumo.jp/%'
          AND (
            COALESCE(si.image_urls, '') LIKE '%suumo%'
            OR COALESCE(ci.listing_media_json, '[]') LIKE '%suumo%'
          )
    """
    params: list[Any] = []
    if args.case_id:
        where += " AND si.id = ?"
        params.append(int(args.case_id))
    sql = f"""
        SELECT si.id, si.item_url, si.title_original, si.body_original, si.image_urls,
               COALESCE(ci.listing_media_json, '[]') AS listing_media_json
        FROM source_items si
        JOIN content_items ci ON ci.source_item_id = si.id
        {where}
        ORDER BY si.id
    """
    rows = [
        row
        for row in conn.execute(sql, params).fetchall()
        if _SUUMO_DETAIL_URL_RE.search(str(row["item_url"] or ""))
        and (_row_has_suumo_pollution(row) or args.all_detail)
    ]
    if args.limit:
        rows = rows[: max(1, int(args.limit))]

    changes: list[dict[str, Any]] = []
    for row in rows:
        new_image_urls, new_media_json = _clean_row(row, limit=max(1, min(int(args.gallery_limit or 80), 120)))
        old_image_urls = str(row["image_urls"] or "")
        old_media_json = str(row["listing_media_json"] or "[]")
        if new_image_urls == old_image_urls and new_media_json == old_media_json:
            continue
        changes.append(
            {
                "id": int(row["id"]),
                "item_url": row["item_url"],
                "title_original": row["title_original"],
                "old_image_count": len([x for x in old_image_urls.splitlines() if x.strip()]),
                "new_image_count": len([x for x in new_image_urls.splitlines() if x.strip()]),
                "old_image_urls": old_image_urls,
                "old_listing_media_json": old_media_json,
                "new_image_urls": new_image_urls,
                "new_listing_media_json": new_media_json,
            }
        )

    backup_path = ""
    if changes:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = ROOT / "logs" / f"suumo_case_gallery_media_backup_{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")
        backup_path = str(backup)

    updated = 0
    if args.apply and changes:
        with conn:
            for item in changes:
                conn.execute(
                    "UPDATE source_items SET image_urls = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (item["new_image_urls"], item["id"]),
                )
                conn.execute(
                    "UPDATE content_items SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP WHERE source_item_id = ?",
                    (item["new_listing_media_json"], item["id"]),
                )
                updated += 1
    conn.close()

    report = {
        "ok": True,
        "mode": "apply" if args.apply else "dry_run",
        "db_path": str(DB_PATH),
        "scanned_rows": len(rows),
        "changed_rows": len(changes),
        "updated_rows": updated,
        "backup_path": backup_path,
        "examples": [
            {
                "id": x["id"],
                "item_url": x["item_url"],
                "old_image_count": x["old_image_count"],
                "new_image_count": x["new_image_count"],
            }
            for x in changes[: int(args.report_examples or 20)]
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair SUUMO case gallery media pollution.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--case-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--gallery-limit", type=int, default=80)
    parser.add_argument("--report-examples", type=int, default=20)
    parser.add_argument("--all-detail", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
