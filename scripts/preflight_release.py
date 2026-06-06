"""
Preflight checks for a stable "go-live" build.

Focus:
  - SQLite schema sanity (FTS exists + triggers exist)
  - HOMES (homes.co.jp) b-id listings: ensure no cross-listing image contamination remains
    (i.e. any non-empty images must match the listing token derived from /b-<digits>/)

This script is read-only: it does NOT write to the DB.
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.config import DB_PATH  # noqa: E402
from src.homes_media_token import homes_listing_image_tokens  # noqa: E402


def _match_any(url: str, tokens: tuple[str, ...]) -> bool:
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


def _sqlite_objects(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"table": [], "trigger": [], "index": [], "view": []}
    rows = conn.execute("SELECT type, name FROM sqlite_master ORDER BY type, name").fetchall()
    for t, n in rows:
        if t in out:
            out[t].append(n)
    return out


def _homes_image_contamination_counts(conn: sqlite3.Connection, *, limit: int) -> dict[str, int]:
    lim = max(1, min(int(limit or 1), 500000))
    rows = conn.execute(
        """
        SELECT id, item_url, COALESCE(image_urls,'') AS image_urls
        FROM source_items
        WHERE lower(COALESCE(item_url,'')) LIKE '%homes.co.jp%'
          AND instr(lower(COALESCE(item_url,'')),'/b-') > 0
        ORDER BY id DESC
        LIMIT ?
        """,
        (lim,),
    ).fetchall()
    total = len(rows)
    nonempty = 0
    no_match = 0
    some_match = 0
    for sid, item_url, image_urls in rows:
        toks = homes_listing_image_tokens(item_url)
        lines = [x.strip() for x in str(image_urls or "").splitlines() if x.strip()]
        if not toks or not lines:
            continue
        nonempty += 1
        hits = sum(1 for u in lines if _match_any(u, toks))
        if hits <= 0:
            no_match += 1
        else:
            some_match += 1
    return {
        "homes_b_total_scanned": int(total),
        "homes_b_image_urls_nonempty": int(nonempty),
        "homes_b_image_urls_some_token_match": int(some_match),
        "homes_b_image_urls_no_token_match": int(no_match),
    }


def _homes_listing_media_contamination_counts(conn: sqlite3.Connection, *, limit: int) -> dict[str, int]:
    lim = max(1, min(int(limit or 1), 500000))
    rows = conn.execute(
        """
        SELECT s.id AS source_item_id, s.item_url, COALESCE(c.listing_media_json,'[]') AS listing_media_json
        FROM source_items s
        JOIN content_items c ON c.source_item_id = s.id
        WHERE lower(COALESCE(s.item_url,'')) LIKE '%homes.co.jp%'
          AND instr(lower(COALESCE(s.item_url,'')),'/b-') > 0
          AND TRIM(COALESCE(c.listing_media_json,'[]')) NOT IN ('', '[]')
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (lim,),
    ).fetchall()
    total = len(rows)
    nonempty = 0
    invalid_json = 0
    no_match = 0
    some_match = 0
    for sid, item_url, raw in rows:
        toks = homes_listing_image_tokens(item_url)
        if not toks:
            continue
        blob = str(raw or "").strip() or "[]"
        nonempty += 1
        try:
            data = json.loads(blob)
        except Exception:
            invalid_json += 1
            continue
        urls: list[str] = []
        if isinstance(data, list):
            for e in data:
                if isinstance(e, str):
                    urls.append(e)
                elif isinstance(e, dict):
                    for k in ("url", "src", "image", "img", "thumbnail", "thumb", "href"):
                        v = e.get(k)
                        if v and str(v).strip().startswith("http"):
                            urls.append(str(v).strip())
                            break
        hits = sum(1 for u in urls if _match_any(u, toks))
        if hits <= 0 and urls:
            no_match += 1
        elif hits > 0:
            some_match += 1
    return {
        "homes_b_listing_media_nonempty": int(nonempty),
        "homes_b_listing_media_some_token_match": int(some_match),
        "homes_b_listing_media_no_token_match": int(no_match),
        "homes_b_listing_media_invalid_json": int(invalid_json),
        "homes_b_listing_media_total_scanned": int(total),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Preflight checks for SCLAW go-live stability.")
    ap.add_argument("--limit", type=int, default=200000, help="Max HOMES rows to scan (default 200000).")
    ap.add_argument("--json", action="store_true", help="Print JSON only (no human text).")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=60000")
    except sqlite3.Error:
        pass
    try:
        objects = _sqlite_objects(conn)
        fts_ok = "content_fts" in objects.get("table", [])
        trig_ok = all(
            name in objects.get("trigger", [])
            for name in ("content_ai", "content_ad", "content_au")
        )
        homes_images = _homes_image_contamination_counts(conn, limit=int(args.limit))
        homes_media = _homes_listing_media_contamination_counts(conn, limit=int(args.limit))
    finally:
        conn.close()

    report = {
        "ok": bool(fts_ok and trig_ok and homes_images["homes_b_image_urls_no_token_match"] == 0 and homes_media["homes_b_listing_media_no_token_match"] == 0),
        "db_path": str(DB_PATH),
        "fts_ok": bool(fts_ok),
        "fts_triggers_ok": bool(trig_ok),
        **homes_images,
        **homes_media,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return

    lines = [
        f"ok={report['ok']}",
        f"db={report['db_path']}",
        f"fts_ok={report['fts_ok']} fts_triggers_ok={report['fts_triggers_ok']}",
        f"HOMES image_urls: nonempty={report['homes_b_image_urls_nonempty']} no_token_match={report['homes_b_image_urls_no_token_match']}",
        f"HOMES listing_media_json: nonempty={report['homes_b_listing_media_nonempty']} no_token_match={report['homes_b_listing_media_no_token_match']} invalid_json={report['homes_b_listing_media_invalid_json']}",
    ]
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
