import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DB_PATH  # noqa: E402
from src.portal_case_search import ordered_listing_image_urls  # noqa: E402
from src.portal_media_filter import is_portal_non_property_image_url  # noqa: E402

DETAIL_URL_RE = re.compile(
    r"("
    r"suumo\.jp/.+(?:jnc_|nc_|\bnc=)"
    r"|homes\.co\.jp/.+(?:/b-|/chintai/room/)"
    r"|athome\.co\.jp/(?:kodate|mansion|chintai|tochi|土地)/(?:[^/?#]+/)*[0-9][0-9A-Za-z]{5,}"
    r"|realestate\.yahoo\.co\.jp/.+/detail"
    r"|rehouse\.co\.jp/.+/bkn"
    r"|sumifu"
    r"|realestate\.rakuten\.co\.jp/(?:useddetached|usedmansion|newmansion|land)/id-"
    r"|yes1\.co\.jp"
    r"|yes-station\.jp"
    r"|oheya-su\.jp"
    r"|oheyasuu\.com"
    r")",
    re.I,
)


def _host(url: str) -> str:
    try:
        return (urlparse(str(url or "")).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _media_json_from_urls(urls: list[str]) -> str:
    entries = [
        {
            "type": "image",
            "url": u,
            "source": "repair_portal_case_gallery_media",
            "note": "filtered_original_site_gallery",
        }
        for u in urls
        if str(u or "").strip()
    ]
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"))


def _row_bad_urls(row: sqlite3.Row) -> list[str]:
    item_url = str(row["item_url"] or "")
    text = f"{row['image_urls'] or ''}\n{row['listing_media_json'] or ''}"
    bad: list[str] = []
    seen: set[str] = set()
    for u in re.findall(r"https?://[^\s\"\]})]+", text):
        if u in seen:
            continue
        seen.add(u)
        if is_portal_non_property_image_url(u, item_url=item_url):
            bad.append(u)
    return bad


def _clean_row(row: sqlite3.Row, *, limit: int) -> tuple[str, str]:
    urls = ordered_listing_image_urls(
        str(row["image_urls"] or ""),
        str(row["body_original"] or ""),
        str(row["listing_media_json"] or "[]"),
        item_url=str(row["item_url"] or ""),
        limit=limit,
    )
    return "\n".join(urls), _media_json_from_urls(urls)


def run(args: argparse.Namespace) -> dict[str, Any]:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    params: list[Any] = []
    sql = """
        SELECT si.id, si.item_url, si.title_original, si.body_original,
               COALESCE(si.image_urls, '') AS image_urls,
               COALESCE(ci.listing_media_json, '[]') AS listing_media_json
        FROM source_items si
        JOIN content_items ci ON ci.source_item_id = si.id
        WHERE (
            si.content_kind = 'jp_listing'
            OR TRIM(COALESCE(si.image_urls, '')) != ''
            OR COALESCE(ci.listing_media_json, '[]') NOT IN ('', '[]')
        )
    """
    if args.case_id:
        sql += " AND si.id = ?"
        params.append(int(args.case_id))
    sql += " ORDER BY si.id"

    rows = [
        row
        for row in conn.execute(sql, params).fetchall()
        if DETAIL_URL_RE.search(str(row["item_url"] or ""))
        and "/tag/" not in str(row["item_url"] or "").lower()
    ]
    if args.host:
        wanted = str(args.host or "").strip().lower().removeprefix("www.")
        rows = [row for row in rows if wanted in _host(str(row["item_url"] or ""))]
    if args.limit:
        rows = rows[: max(1, int(args.limit))]

    changes: list[dict[str, Any]] = []
    by_host: dict[str, int] = {}
    for row in rows:
        bad = _row_bad_urls(row)
        if not bad and not args.all_detail:
            continue
        new_image_urls, new_media_json = _clean_row(row, limit=max(1, min(int(args.gallery_limit or 80), 120)))
        old_image_urls = str(row["image_urls"] or "")
        old_media_json = str(row["listing_media_json"] or "[]")
        if new_image_urls == old_image_urls and new_media_json == old_media_json:
            continue
        host = _host(str(row["item_url"] or ""))
        by_host[host] = by_host.get(host, 0) + 1
        changes.append(
            {
                "id": int(row["id"]),
                "host": host,
                "item_url": row["item_url"],
                "title_original": row["title_original"],
                "bad_urls": bad[:20],
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
        backup = ROOT / "logs" / f"portal_case_gallery_media_backup_{stamp}.json"
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
        "changed_by_host": by_host,
        "backup_path": backup_path,
        "examples": [
            {
                "id": x["id"],
                "host": x["host"],
                "item_url": x["item_url"],
                "old_image_count": x["old_image_count"],
                "new_image_count": x["new_image_count"],
                "bad_urls": x["bad_urls"][:3],
            }
            for x in changes[: int(args.report_examples or 20)]
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and repair polluted original-portal case galleries.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--case-id", type=int, default=0)
    parser.add_argument("--host", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--gallery-limit", type=int, default=80)
    parser.add_argument("--report-examples", type=int, default=20)
    parser.add_argument("--all-detail", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
