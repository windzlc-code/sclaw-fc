"""
批次重新擷取來源頁圖片網址，合併寫入 source_items.image_urls（並更新 body_original）。

支援門戶：SUUMO、HOMES、AtHome、Yahoo 不動産、楽天不動産、yes1、お部屋さん等
（與 fetch_property_detail / live_enrich_eligible_url 一致）。

用法（專案根目錄）:
  .venv\\Scripts\\python.exe scripts/batch_backfill_image_urls.py --limit 50
  .venv\\Scripts\\python.exe scripts/batch_backfill_image_urls.py --dry-run --limit 5
  .venv\\Scripts\\python.exe scripts/batch_backfill_image_urls.py --hosts suumo.jp,homes.co.jp

選列條件預設：image_urls 為空或僅空白；可加 --force 連同已有圖一併重抓合併。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.thumb_backfill_service import run_empty_image_backfill


def _parse_hosts(s: str) -> frozenset[str] | None:
    if not (s or "").strip():
        return None
    return frozenset({x.strip().lower() for x in s.split(",") if x.strip()})


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch backfill source_items.image_urls via fetch_property_detail")
    ap.add_argument("--limit", type=int, default=30, help="max rows to process (default 30)")
    ap.add_argument("--sleep", type=float, default=0.35, help="seconds between requests (default 0.35)")
    ap.add_argument("--dry-run", action="store_true", help="print actions only, no DB writes")
    ap.add_argument(
        "--force",
        action="store_true",
        help="also process rows that already have image_urls (merge new first)",
    )
    ap.add_argument(
        "--hosts",
        type=str,
        default="",
        help="comma-separated host keys (e.g. suumo.jp,homes.co.jp); empty = all supported",
    )
    args = ap.parse_args()
    host_filter = _parse_hosts(args.hosts)
    lim = max(1, min(int(args.limit or 30), 5000))

    out = run_empty_image_backfill(
        host_filter=host_filter,
        limit=lim,
        sleep_s=float(args.sleep),
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )

    msg = str(out.get("message") or "").strip()
    if msg == "no rows matched filters":
        print("no rows matched filters")
        sys.exit(0)

    print(
        f"processing {out.get('processed_rows', 0)} row(s), dry_run={args.dry_run} "
        f"ok={out.get('ok')} err={out.get('err')} skip={out.get('skip')}"
    )


if __name__ == "__main__":
    main()
