from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.config import DB_PATH


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_claim_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS homes_media_repair_claims (
            source_item_id INTEGER PRIMARY KEY,
            worker TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            attempts INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            report_path TEXT,
            last_error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_homes_media_repair_claims_status ON homes_media_repair_claims(status, updated_at)"
    )
    conn.commit()


def claim_batch(worker: str, *, batch_size: int, max_id: int, min_id: int) -> list[int]:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    ensure_claim_table(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        where = [
            "COALESCE(s.content_kind, '') = 'jp_listing'",
            "lower(COALESCE(s.item_url,'')) LIKE '%homes.co.jp%'",
            "TRIM(COALESCE(s.image_urls,'')) = ''",
            "c.source_item_id IS NULL",
        ]
        params: list[object] = []
        if max_id > 0:
            where.append("s.id <= ?")
            params.append(int(max_id))
        if min_id > 0:
            where.append("s.id >= ?")
            params.append(int(min_id))
        sql = f"""
            SELECT s.id
            FROM source_items s
            LEFT JOIN homes_media_repair_claims c
              ON c.source_item_id = s.id AND c.status IN ('running', 'ok')
            WHERE {' AND '.join(where)}
            ORDER BY s.id DESC
            LIMIT ?
        """
        rows = conn.execute(sql, (*params, max(1, int(batch_size)))).fetchall()
        ids = [int(r["id"]) for r in rows]
        now = utc_now()
        if ids:
            conn.executemany(
                """
                INSERT INTO homes_media_repair_claims
                    (source_item_id, worker, status, attempts, claimed_at, updated_at)
                VALUES (?, ?, 'running', 1, ?, ?)
                ON CONFLICT(source_item_id) DO UPDATE SET
                    worker = excluded.worker,
                    status = 'running',
                    attempts = homes_media_repair_claims.attempts + 1,
                    updated_at = excluded.updated_at,
                    last_error = NULL
                """,
                [(sid, worker, now, now) for sid in ids],
            )
        conn.commit()
        return ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_report(ids: list[int], *, report_path: Path, returncode: int) -> None:
    statuses: dict[int, tuple[str, str]] = {sid: ("failed", f"repair_homes_media exited {returncode}") for sid in ids}
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            for row in payload.get("rows") or []:
                if not isinstance(row, dict):
                    continue
                sid = int(row.get("source_item_id") or 0)
                if sid <= 0:
                    continue
                if row.get("ok"):
                    statuses[sid] = ("ok", "")
                else:
                    statuses[sid] = ("failed", str(row.get("reason") or "repair returned ok=false")[:800])
        except Exception as exc:
            for sid in ids:
                statuses[sid] = ("failed", f"report parse failed: {type(exc).__name__}: {exc}"[:800])

    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        now = utc_now()
        conn.executemany(
            """
            UPDATE homes_media_repair_claims
            SET status = ?, updated_at = ?, report_path = ?, last_error = ?
            WHERE source_item_id = ?
            """,
            [(status, now, str(report_path), err, sid) for sid, (status, err) in statuses.items()],
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Claim and continuously repair HOMES empty media rows without overlap.")
    ap.add_argument("--worker", required=True)
    ap.add_argument("--batch-size", type=int, default=120)
    ap.add_argument("--max-batches", type=int, default=0, help="0 means keep going until no rows match.")
    ap.add_argument("--max-id", type=int, default=0)
    ap.add_argument("--min-id", type=int, default=0)
    ap.add_argument("--sleep-between-batches", type=float, default=2.0)
    ap.add_argument("--repair-sleep-sec", type=float, default=0.1)
    ap.add_argument("--channel", default="chrome")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    worker = str(args.worker).strip() or f"queue-{int(time.time())}"
    batch_no = 0
    print(
        f"homes_media_repair_queue worker={worker} batch_size={args.batch_size} "
        f"max_id={args.max_id} min_id={args.min_id} max_batches={args.max_batches}",
        flush=True,
    )
    while True:
        if int(args.max_batches or 0) > 0 and batch_no >= int(args.max_batches):
            print("done max_batches reached", flush=True)
            return
        ids = claim_batch(worker, batch_size=max(1, int(args.batch_size)), max_id=int(args.max_id or 0), min_id=int(args.min_id or 0))
        if not ids:
            print("done no rows to claim", flush=True)
            return
        batch_no += 1
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report = ROOT / "logs" / f"repair_homes_media_queue_{worker}_batch{batch_no}_{stamp}.json"
        cmd = [
            str(args.python),
            "-u",
            "scripts/repair_homes_media.py",
            "--source-ids",
            ",".join(str(x) for x in ids),
            "--sleep-sec",
            str(max(0.0, float(args.repair_sleep_sec))),
            "--write-report",
            str(report.relative_to(ROOT)),
        ]
        channel = str(args.channel or "").strip()
        if channel:
            cmd.extend(["--channel", channel])
        print(f"batch={batch_no} claimed={len(ids)} range={min(ids)}..{max(ids)} report={report}", flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), text=True)
        mark_report(ids, report_path=report, returncode=int(proc.returncode))
        print(f"batch={batch_no} finished returncode={proc.returncode}", flush=True)
        if args.sleep_between_batches:
            time.sleep(max(0.0, float(args.sleep_between_batches)))


if __name__ == "__main__":
    main()
