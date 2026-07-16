"""Rebuild public listing cache fields as Chinese-only structured summaries.

The crawler's Japanese source is intentionally preserved in ``source_items``.
This utility only rewrites the display/SEO fields in ``content_items`` for
``jp_listing`` records.  It is idempotent and creates a SQLite snapshot before
an apply run so the cache can be restored without touching source data.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DB_PATH
from src.db import get_conn
from src.pipeline import _build_listing_zh_fallback, _build_listing_zh_title, _save_content_item_fast


_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")


def _default_backup_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path("/tmp") / f"sclaw-jp-listing-cache-before-chinese-{stamp}.sqlite3"


def _snapshot_database(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination}")
    source = sqlite3.connect(str(DB_PATH))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target, pages=2048)
    finally:
        target.close()
        source.close()


def _candidate_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(1)
        FROM content_items c
        JOIN source_items s ON s.id = c.source_item_id
        WHERE COALESCE(s.content_kind, '') = 'jp_listing'
        """
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _iter_rows(conn: sqlite3.Connection, limit: int):
    sql = """
        SELECT s.*
        FROM source_items s
        JOIN content_items c ON c.source_item_id = s.id
        WHERE COALESCE(s.content_kind, '') = 'jp_listing'
        ORDER BY s.id ASC
    """
    params: tuple[Any, ...] = ()
    if limit > 0:
        sql += " LIMIT ?"
        params = (limit,)
    return conn.execute(sql, params)


def run(*, apply: bool, limit: int, batch_size: int, backup: Path | None) -> dict[str, Any]:
    safe_limit = max(0, int(limit or 0))
    safe_batch = max(50, min(2000, int(batch_size or 500)))
    if apply:
        backup_path = backup or _default_backup_path()
        _snapshot_database(backup_path)
    else:
        backup_path = None

    scanned = updated = kana_failures = 0
    examples: list[int] = []
    with get_conn() as conn:
        total = _candidate_count(conn)
        rows = _iter_rows(conn, safe_limit)
        for row in rows:
            source = dict(row)
            source_id = int(source.get("id") or 0)
            title_hant, title_hans = _build_listing_zh_title(source)
            body_hant, body_hans = _build_listing_zh_fallback(source)
            all_display = "\n".join((title_hant, title_hans, body_hant, body_hans))
            scanned += 1
            if _KANA.search(all_display):
                kana_failures += 1
                if len(examples) < 20:
                    examples.append(source_id)
                continue
            if apply:
                _save_content_item_fast(
                    conn,
                    source_item_id=source_id,
                    title_hant=title_hant,
                    title_hans=title_hans,
                    body_hant=body_hant,
                    body_hans=body_hans,
                    source_name=str(source.get("source_name") or ""),
                    keyword_type="case",
                    intent_target="房地產",
                    topic_category="日本房產案件",
                    keyword_tags="日本房產案件,日本買房,中古住宅,新成屋,獨棟住宅,公寓大樓,車站步行",
                )
                updated += 1
                if updated % safe_batch == 0:
                    conn.commit()
            if scanned % 5000 == 0:
                print(
                    f"[jp-listing-chinese-cache] scanned={scanned} updated={updated} failures={kana_failures}",
                    file=sys.stderr,
                    flush=True,
                )
        if apply:
            conn.commit()
    return {
        "ok": kana_failures == 0,
        "apply": bool(apply),
        "candidate_rows": total,
        "scanned_rows": scanned,
        "updated_rows": updated,
        "kana_failures": kana_failures,
        "failure_source_item_ids": examples,
        "backup_path": str(backup_path) if backup_path else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write rebuilt public cache fields")
    parser.add_argument("--limit", type=int, default=0, help="0 means every jp_listing row")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--backup", type=Path, help="SQLite snapshot path; automatic under /tmp on --apply")
    args = parser.parse_args()
    report = run(
        apply=bool(args.apply),
        limit=int(args.limit or 0),
        batch_size=int(args.batch_size or 500),
        backup=args.backup,
    )
    import json

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
