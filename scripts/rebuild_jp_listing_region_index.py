from __future__ import annotations

import argparse
import time
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_conn
from src.jp_listing_region_index import infer_region_keys


def rebuild(*, batch_size: int = 1000, body_chars: int = 12000) -> dict[str, object]:
    started = time.perf_counter()
    with get_conn() as conn:
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS source_items_fts_ai;
            DROP TRIGGER IF EXISTS source_items_fts_ad;
            DROP TRIGGER IF EXISTS source_items_fts_au;
            DROP TABLE IF EXISTS source_items_fts;
            DROP TABLE IF EXISTS jp_listing_region_index;
            CREATE TABLE jp_listing_region_index (
                region_key TEXT NOT NULL,
                source_item_id INTEGER NOT NULL,
                sort_time TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(region_key, source_item_id)
            );
            CREATE INDEX IF NOT EXISTS idx_jp_listing_region_source
                ON jp_listing_region_index(source_item_id);
            CREATE INDEX IF NOT EXISTS idx_jp_listing_region_sort
                ON jp_listing_region_index(region_key, sort_time DESC, source_item_id DESC);
            """
        )
        conn.commit()
        rows = conn.execute(
            """
            SELECT
                s.id,
                COALESCE(
                    NULLIF(TRIM(s.published_at), ''),
                    NULLIF(TRIM(s.last_checked_at), ''),
                    NULLIF(TRIM(s.crawled_at), ''),
                    NULLIF(TRIM(c.updated_at), ''),
                    ''
                ) AS sort_time,
                COALESCE(s.item_url, '') AS item_url,
                COALESCE(s.title_original, '') AS title_original,
                substr(COALESCE(s.body_original, ''), 1, ?) AS body_original,
                COALESCE(c.title_zh_hant, '') AS title_zh_hant,
                substr(COALESCE(c.body_zh_hant, ''), 1, 900) AS body_zh_hant
            FROM source_items s
            JOIN content_items c ON c.source_item_id = s.id
            WHERE COALESCE(s.content_kind, '') = 'jp_listing'
            ORDER BY s.id
            """,
            (max(1000, int(body_chars)),),
        )
        pending: list[tuple[str, int, str]] = []
        counts: Counter[str] = Counter()
        scanned = 0
        for row in rows:
            sid = int(row["id"])
            sort_time = str(row["sort_time"] or "")
            title_original = "\n".join(
                [str(row["title_original"] or ""), str(row["title_zh_hant"] or "")]
            )
            body_original = "\n".join([str(row["body_original"] or ""), str(row["body_zh_hant"] or "")])
            regs = infer_region_keys(
                item_url=str(row["item_url"] or ""),
                title_original=title_original,
                body_original=body_original,
            )
            scanned += 1
            for reg in sorted(regs):
                pending.append((reg, sid, sort_time))
                counts[reg] += 1
            if len(pending) >= batch_size:
                conn.executemany(
                    "INSERT OR IGNORE INTO jp_listing_region_index(region_key, source_item_id, sort_time) VALUES (?, ?, ?)",
                    pending,
                )
                conn.commit()
                pending.clear()
        if pending:
            conn.executemany(
                "INSERT OR IGNORE INTO jp_listing_region_index(region_key, source_item_id, sort_time) VALUES (?, ?, ?)",
                pending,
            )
            conn.commit()
        conn.execute("ANALYZE jp_listing_region_index")
        conn.commit()
        total = int(conn.execute("SELECT COUNT(1) FROM jp_listing_region_index").fetchone()[0] or 0)
    return {
        "scanned": scanned,
        "index_rows": total,
        "top_regions": counts.most_common(20),
        "seconds": round(time.perf_counter() - started, 2),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--body-chars", type=int, default=12000)
    args = ap.parse_args()
    print(rebuild(batch_size=args.batch_size, body_chars=args.body_chars))


if __name__ == "__main__":
    main()
