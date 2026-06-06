from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.case_quality_batch import run_case_quality_batch_once


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the daily SCLAW case image and quality maintenance batch.")
    ap.add_argument("--dry-run", action="store_true", help="Inspect candidates without DB writes.")
    ap.add_argument("--backfill-limit", type=int, default=None, help="Missing-image rows to backfill across all portals.")
    ap.add_argument("--remaining-three-limit", type=int, default=None, help="Missing-image rows to backfill for Rakuten/Yes/OHEYASU.")
    ap.add_argument("--force-refresh-limit", type=int, default=None, help="Rows to refresh even if image_urls already exist.")
    ap.add_argument("--recrawl-limit", type=int, default=None, help="Low-quality listing rows to recrawl.")
    ap.add_argument("--scan-limit", type=int, default=None, help="Recent DB rows to scan for quality recrawl.")
    ap.add_argument("--sleep", type=float, default=None, help="Seconds between source requests.")
    args = ap.parse_args()

    report = run_case_quality_batch_once(
        reason="daily_cli",
        dry_run=bool(args.dry_run),
        backfill_limit=args.backfill_limit,
        remaining_three_limit=args.remaining_three_limit,
        force_refresh_limit=args.force_refresh_limit,
        recrawl_limit=args.recrawl_limit,
        scan_limit=args.scan_limit,
        sleep_s=args.sleep,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
