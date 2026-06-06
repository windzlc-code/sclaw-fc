from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is importable when running `python scripts/...` directly.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.db import get_conn  # noqa: E402
from src.pipeline import _bind_jp_transit_to_content_item  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill jp_station_id / walk_min on content_items for jp_listing rows.")
    ap.add_argument("--limit", type=int, default=0, help="Max source_items rows to process (0 = no limit).")
    ap.add_argument("--commit-every", type=int, default=400, help="Commit every N rows.")
    args = ap.parse_args()

    lim = max(0, int(args.limit or 0))
    commit_every = max(50, int(args.commit_every or 400))

    processed = 0
    updated = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM source_items WHERE COALESCE(content_kind,'') = ? ORDER BY id ASC",
            ("jp_listing",),
        ).fetchall()
        total = len(rows)
        if lim and lim < total:
            rows = rows[:lim]
            total = len(rows)

        for i, r in enumerate(rows, 1):
            sid = int(r[0])
            src = conn.execute("SELECT * FROM source_items WHERE id = ?", (sid,)).fetchone()
            if not src:
                continue
            before = conn.execute(
                "SELECT COALESCE(jp_station_id,0) AS s, COALESCE(walk_min,0) AS w FROM content_items WHERE source_item_id = ?",
                (sid,),
            ).fetchone()
            before_s = int(before["s"]) if before else 0
            before_w = int(before["w"]) if before else 0
            _bind_jp_transit_to_content_item(conn, source_item_id=sid, src_row=dict(src))
            after = conn.execute(
                "SELECT COALESCE(jp_station_id,0) AS s, COALESCE(walk_min,0) AS w FROM content_items WHERE source_item_id = ?",
                (sid,),
            ).fetchone()
            after_s = int(after["s"]) if after else 0
            after_w = int(after["w"]) if after else 0
            processed += 1
            if (after_s, after_w) != (before_s, before_w) and (after_s > 0 or after_w > 0):
                updated += 1
            if i % commit_every == 0:
                conn.commit()
                print(f"[backfill] {i}/{total} processed; updated={updated}", flush=True)

        conn.commit()
    print(f"[backfill] done: processed={processed} updated={updated}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
