from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from src.coverage_matrix_sql import CASE_INV_JP_LISTING_SQL
from src.db import get_conn


PROPERTY_TYPE_INDEX_TABLE = "jp_listing_property_type_index"


def ensure_jp_listing_property_type_index_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PROPERTY_TYPE_INDEX_TABLE} (
            source_item_id INTEGER NOT NULL,
            content_item_id INTEGER NOT NULL DEFAULT 0,
            property_type TEXT NOT NULL,
            source_last_checked_at TEXT NOT NULL DEFAULT '',
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(source_item_id, property_type),
            FOREIGN KEY(source_item_id) REFERENCES source_items(id)
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_jp_listing_type_type_time "
        f"ON {PROPERTY_TYPE_INDEX_TABLE}(property_type, source_last_checked_at DESC, source_item_id DESC)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_jp_listing_type_source "
        f"ON {PROPERTY_TYPE_INDEX_TABLE}(source_item_id)"
    )


def property_type_index_ready(conn: sqlite3.Connection, *, min_rows: int = 1) -> bool:
    try:
        row = conn.execute(f"SELECT COUNT(1) AS c FROM {PROPERTY_TYPE_INDEX_TABLE}").fetchone()
        return int((row["c"] if row else 0) or 0) >= int(min_rows or 1)
    except sqlite3.Error:
        return False


def property_type_hits_for_probe(probe: dict[str, Any], *, source_url: str = "") -> set[str]:
    from src.portal_case_search import (
        _smart_item_matches_property_types,
        _smart_probe_has_studio_like_layout,
        _smart_property_type_hits,
    )

    hits = set(_smart_property_type_hits(probe))
    if _smart_probe_has_studio_like_layout(probe):
        hits.add("套房")
    # `其他` is a useful browse bucket for homepage cards, but should only be
    # assigned when no stronger type signal is present.
    if not any(_smart_item_matches_property_types(probe, [t]) for t in hits):
        hits = set()
    if not hits:
        hits.add("其他")
    return hits


def rebuild_jp_listing_property_type_index(
    *,
    limit: int = 0,
    batch_size: int = 500,
    clear: bool = True,
) -> dict[str, Any]:
    from src.portal_case_search import _row_to_smart_type_probe, _smart_type_probe_is_listing_detail

    limit_sql = "LIMIT ?" if int(limit or 0) > 0 else ""
    params: list[Any] = [int(limit)] if int(limit or 0) > 0 else []
    scanned = 0
    indexed_sources = 0
    inserted = 0
    skipped = 0
    counts: dict[str, int] = {}
    pending: list[tuple[int, int, str, str]] = []
    with get_conn() as conn:
        ensure_jp_listing_property_type_index_schema(conn)
        cur = conn.execute(
            f"""
            SELECT
              c.id,
              c.seo_slug,
              c.title_zh_hant,
              c.title_zh_hans,
              c.seo_title,
              substr(COALESCE(c.body_zh_hant,''),1,420) AS body_zh_hant,
              substr(COALESCE(c.body_zh_hans,''),1,420) AS body_zh_hans,
              c.keyword_type,
              c.topic_category,
              COALESCE(c.case_transaction_override, '') AS case_transaction_override,
              s.id AS source_item_id,
              s.source_name,
              s.item_url,
              s.title_original,
              substr(COALESCE(s.body_original,''),1,3200) AS body_original,
              COALESCE(s.content_kind, '') AS content_kind,
              COALESCE(s.last_checked_at, '') AS last_checked_at
            FROM source_items s
            JOIN content_items c ON c.source_item_id = s.id
            WHERE ({CASE_INV_JP_LISTING_SQL})
            ORDER BY s.last_checked_at DESC, s.id DESC
            {limit_sql}
            """,
            params,
        )
        cur.arraysize = max(100, min(2000, int(batch_size or 500)))
        while True:
            rows = cur.fetchmany()
            if not rows:
                break
            for row in rows:
                scanned += 1
                probe = _row_to_smart_type_probe(row)
                if not _smart_type_probe_is_listing_detail(row, probe):
                    skipped += 1
                    continue
                source_id = int(row["source_item_id"] or 0)
                content_id = int(row["id"] or 0)
                last_checked = str(row["last_checked_at"] or "")
                hits = sorted(property_type_hits_for_probe(probe, source_url=str(row["item_url"] or "")))
                if not hits:
                    skipped += 1
                    continue
                indexed_sources += 1
                for hit in hits:
                    pending.append((source_id, content_id, hit, last_checked))
                    counts[hit] = int(counts.get(hit, 0) or 0) + 1
        cur.close()
        if clear:
            conn.execute(f"DELETE FROM {PROPERTY_TYPE_INDEX_TABLE}")
        if pending:
            conn.executemany(
                f"""
                INSERT OR REPLACE INTO {PROPERTY_TYPE_INDEX_TABLE}
                  (source_item_id, content_item_id, property_type, source_last_checked_at, indexed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                pending,
            )
            inserted += len(pending)
        conn.commit()
    return {
        "scanned": scanned,
        "indexed_sources": indexed_sources,
        "inserted": inserted,
        "skipped": skipped,
        "counts": counts,
    }


def summarize_property_type_index(types: Iterable[str] | None = None) -> dict[str, int]:
    with get_conn() as conn:
        ensure_jp_listing_property_type_index_schema(conn)
        params: list[Any] = []
        where = ""
        selected = [str(t or "").strip() for t in (types or []) if str(t or "").strip()]
        if selected:
            where = "WHERE property_type IN ({})".format(",".join("?" for _ in selected))
            params.extend(selected)
        rows = conn.execute(
            f"""
            SELECT property_type, COUNT(DISTINCT source_item_id) AS c
            FROM {PROPERTY_TYPE_INDEX_TABLE}
            {where}
            GROUP BY property_type
            ORDER BY c DESC, property_type
            """,
            params,
        ).fetchall()
    return {str(r["property_type"]): int(r["c"] or 0) for r in rows}
