from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.config import DB_PATH
from src.db import get_conn


def _json_empty_sql(alias: str = "c") -> str:
    return f"(TRIM(COALESCE({alias}.listing_media_json, '')) = '' OR TRIM(COALESCE({alias}.listing_media_json, '[]')) = '[]')"


def _retry_db(fn, *, retries: int = 8):
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower() or attempt >= retries - 1:
                raise
            time.sleep(0.25 * (2**attempt))
    raise last or RuntimeError("unknown db retry failure")


def counts_snapshot() -> dict[str, Any]:
    def run() -> dict[str, Any]:
        with get_conn() as conn:
            conn.execute("BEGIN")
            try:
                media_empty = _json_empty_sql("c")
                row = conn.execute(
                    f"""
                    SELECT
                      (SELECT COUNT(1) FROM source_items WHERE content_kind='jp_listing') AS source_jp_listing,
                      (SELECT COUNT(1) FROM content_items c JOIN source_items s ON s.id=c.source_item_id WHERE s.content_kind='jp_listing') AS content_jp_listing,
                      (SELECT COUNT(1) FROM source_items s LEFT JOIN content_items c ON c.source_item_id=s.id WHERE s.content_kind='jp_listing' AND c.id IS NULL) AS missing_content,
                      (SELECT COUNT(1) FROM content_items c JOIN source_items s ON s.id=c.source_item_id WHERE s.content_kind='jp_listing' AND {media_empty} AND TRIM(COALESCE(s.image_urls,'')) <> '') AS syncable_media,
                      (SELECT COUNT(1) FROM content_items c JOIN source_items s ON s.id=c.source_item_id WHERE s.content_kind='jp_listing' AND {media_empty} AND TRIM(COALESCE(s.image_urls,'')) = '') AS missing_source_images,
                      (SELECT COUNT(1) FROM content_items c JOIN source_items s ON s.id=c.source_item_id WHERE s.content_kind='jp_listing' AND (length(TRIM(COALESCE(c.body_zh_hant,''))) < 180 OR length(TRIM(COALESCE(c.title_zh_hant,''))) < 8)) AS weak_text,
                      (SELECT COUNT(1) FROM content_fts) AS fts_rows,
                      (SELECT COUNT(1) FROM keyword_search_stats) AS keyword_stats
                    """
                ).fetchone()
                top_keywords = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT keyword, channel, search_count, last_searched_at
                        FROM keyword_search_stats
                        ORDER BY search_count DESC, datetime(last_searched_at) DESC
                        LIMIT 15
                        """
                    ).fetchall()
                ]
                portal_counts = [
                    dict(r)
                    for r in conn.execute(
                        """
                        SELECT
                          CASE
                            WHEN item_url LIKE '%suumo.jp%' THEN 'SUUMO'
                            WHEN item_url LIKE '%homes.co.jp%' THEN 'LIFULL HOME''S'
                            WHEN item_url LIKE '%athome.co.jp%' THEN 'at home'
                            WHEN item_url LIKE '%realestate.yahoo.co.jp%' OR item_url LIKE '%yahoo.co.jp%' THEN 'Yahoo'
                            WHEN item_url LIKE '%rakuten%' THEN '楽天'
                            WHEN item_url LIKE '%yes1%' OR item_url LIKE '%yes-myhome%' THEN 'イエステーション'
                            WHEN item_url LIKE '%oheyasu%' THEN 'OHEYASU'
                            ELSE COALESCE(NULLIF(TRIM(source_name), ''), '(unknown)')
                          END AS portal,
                          COUNT(1) AS count
                        FROM source_items
                        WHERE content_kind='jp_listing'
                        GROUP BY portal
                        ORDER BY count DESC, portal
                        """
                    ).fetchall()
                ]
                buy_focus = dict(
                    conn.execute(
                        """
                        SELECT
                          SUM(CASE WHEN item_url LIKE '%/mansion/%' OR item_url LIKE '%/ms/%'
                                    OR body_original LIKE '%マンション%' THEN 1 ELSE 0 END) AS mansion_like,
                          SUM(CASE WHEN item_url LIKE '%/kodate/%' OR item_url LIKE '%/ikkodate/%'
                                    OR item_url LIKE '%/chukoikkodate/%' OR body_original LIKE '%一戸建て%'
                                    OR body_original LIKE '%戸建%' THEN 1 ELSE 0 END) AS house_like,
                          SUM(CASE WHEN body_original LIKE '%1R%' OR body_original LIKE '%ワンルーム%'
                                    OR body_original LIKE '%1K%' OR body_original LIKE '%單間%' THEN 1 ELSE 0 END) AS one_room_like,
                          SUM(CASE WHEN body_original LIKE '%平屋%' OR body_original LIKE '%單層%'
                                    OR body_original LIKE '%1階建%' OR body_original LIKE '%一階建%' THEN 1 ELSE 0 END) AS single_floor_like,
                          SUM(CASE WHEN body_original LIKE '%徒歩%' OR body_original LIKE '%駅%'
                                    OR item_url LIKE '%/ek_%' THEN 1 ELSE 0 END) AS transit_like,
                          SUM(CASE WHEN body_original LIKE '%新着%' OR body_original LIKE '%更新%'
                                    OR body_original LIKE '%掲載%' OR published_at IS NOT NULL THEN 1 ELSE 0 END) AS update_signal_like
                        FROM source_items
                        WHERE content_kind='jp_listing'
                        """
                    ).fetchone()
                )
                return {**dict(row), "portal_counts": portal_counts, "buy_focus": buy_focus, "top_keywords": top_keywords}
            finally:
                conn.rollback()

    return _retry_db(run)


def sync_media(limit: int) -> dict[str, Any]:
    if limit <= 0:
        return {"attempted": 0, "fixed": 0, "items": []}
    from app import _sync_case_listing_media_from_source_item_id

    def select_ids() -> list[int]:
        with get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT s.id
                FROM content_items c
                JOIN source_items s ON s.id=c.source_item_id
                WHERE s.content_kind='jp_listing'
                  AND {_json_empty_sql("c")}
                  AND TRIM(COALESCE(s.image_urls,'')) <> ''
                ORDER BY datetime(s.last_checked_at) DESC, s.id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [int(r["id"]) for r in rows]

    ids = _retry_db(select_ids)
    fixed = 0
    items: list[dict[str, Any]] = []
    for sid in ids:
        res = _retry_db(lambda sid=sid: _sync_case_listing_media_from_source_item_id(sid, force=True, dry_run=False))
        if res.get("fixed"):
            fixed += 1
        items.append({"source_item_id": sid, "fixed": bool(res.get("fixed")), "media_count": int(res.get("media_count") or 0)})
    return {"attempted": len(ids), "fixed": fixed, "items": items[:20]}


def repair_text(limit: int) -> dict[str, Any]:
    if limit <= 0:
        return {"attempted": 0, "fixed": 0, "items": []}
    from app import _repair_case_text_fields_from_source_item_id

    def select_ids() -> list[int]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT s.id
                FROM content_items c
                JOIN source_items s ON s.id=c.source_item_id
                WHERE s.content_kind='jp_listing'
                  AND (
                    length(TRIM(COALESCE(c.body_zh_hant,''))) < 180
                    OR length(TRIM(COALESCE(c.title_zh_hant,''))) < 8
                  )
                ORDER BY datetime(s.last_checked_at) DESC, s.id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [int(r["id"]) for r in rows]

    ids = _retry_db(select_ids)
    fixed = 0
    items: list[dict[str, Any]] = []
    for sid in ids:
        res = _retry_db(lambda sid=sid: _repair_case_text_fields_from_source_item_id(sid, dry_run=False))
        if res.get("fixed") or res.get("fields"):
            fixed += 1
        items.append({"source_item_id": sid, "fields": list(res.get("fields") or []), "reason": res.get("reason", "")})
    return {"attempted": len(ids), "fixed": fixed, "items": items[:20]}


def generate_missing_content(limit: int) -> dict[str, Any]:
    if limit <= 0:
        return {"attempted": 0, "generated": 0, "items": []}
    from src.pipeline import generate_content_for_source

    def select_ids() -> list[int]:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT s.id
                FROM source_items s
                LEFT JOIN content_items c ON c.source_item_id=s.id
                WHERE s.content_kind='jp_listing'
                  AND c.id IS NULL
                  AND length(TRIM(COALESCE(s.body_original,''))) >= 120
                ORDER BY datetime(s.last_checked_at) DESC, s.id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [int(r["id"]) for r in rows]

    ids = _retry_db(select_ids)
    generated = 0
    items: list[int] = []
    for sid in ids:
        def one() -> bool:
            with get_conn() as conn:
                before = conn.execute("SELECT 1 FROM content_items WHERE source_item_id=?", (sid,)).fetchone()
                if before:
                    return False
                generate_content_for_source(conn, sid)
                conn.commit()
                after = conn.execute("SELECT 1 FROM content_items WHERE source_item_id=?", (sid,)).fetchone()
                return bool(after)

        if _retry_db(one):
            generated += 1
            items.append(sid)
    return {"attempted": len(ids), "generated": generated, "items": items[:30]}


def rebuild_fts() -> dict[str, Any]:
    def run() -> dict[str, Any]:
        with get_conn() as conn:
            before = int(conn.execute("SELECT COUNT(1) AS c FROM content_fts").fetchone()["c"] or 0)
            conn.execute("INSERT INTO content_fts(content_fts) VALUES('rebuild')")
            conn.commit()
            after = int(conn.execute("SELECT COUNT(1) AS c FROM content_fts").fetchone()["c"] or 0)
            return {"before": before, "after": after}

    return _retry_db(run)


def write_report(report: dict[str, Any], out_path: str) -> str:
    p = Path(out_path)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose and repair SCLAW site data, media, text, and search quality.")
    parser.add_argument("--sync-media-limit", type=int, default=0)
    parser.add_argument("--repair-text-limit", type=int, default=0)
    parser.add_argument("--generate-missing-content-limit", type=int, default=0)
    parser.add_argument("--rebuild-fts", action="store_true")
    parser.add_argument("--write-report", default="")
    args = parser.parse_args()

    started = datetime.now().isoformat(timespec="seconds")
    before = counts_snapshot()
    report: dict[str, Any] = {
        "ok": True,
        "started_at": started,
        "db_path": str(DB_PATH),
        "before": before,
        "repairs": {},
    }
    report["repairs"]["sync_media"] = sync_media(max(0, int(args.sync_media_limit or 0)))
    report["repairs"]["repair_text"] = repair_text(max(0, int(args.repair_text_limit or 0)))
    report["repairs"]["generate_missing_content"] = generate_missing_content(max(0, int(args.generate_missing_content_limit or 0)))
    if args.rebuild_fts:
        report["repairs"]["rebuild_fts"] = rebuild_fts()
    report["after"] = counts_snapshot()
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if args.write_report:
        report["report_path"] = write_report(report, args.write_report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
