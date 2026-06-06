#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.case_delist import delist_ended_homes_without_trusted_images


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(",", " ").split():
        if not part.isdigit():
            continue
        n = int(part)
        if n > 0 and n not in out:
            out.append(n)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delist HOME'S ended listings that have no trusted same-listing images."
    )
    parser.add_argument("--dry-run", action="store_true", help="scan only; do not update source_items")
    parser.add_argument("--limit", type=int, default=200000, help="max public HOME'S rows to scan")
    parser.add_argument("--ids", default="", help="optional comma/space separated source_item_id list")
    args = parser.parse_args()

    report = delist_ended_homes_without_trusted_images(
        limit=max(1, min(int(args.limit or 1), 500000)),
        ids=_parse_ids(args.ids),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
