#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.jp_listing_property_type_index import rebuild_jp_listing_property_type_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild jp_listing property type index.")
    parser.add_argument("--limit", type=int, default=0, help="Limit rows for testing. Default: full rebuild.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--no-clear", action="store_true", help="Do not clear existing index first.")
    args = parser.parse_args()
    result = rebuild_jp_listing_property_type_index(
        limit=max(0, int(args.limit or 0)),
        batch_size=max(100, int(args.batch_size or 500)),
        clear=not bool(args.no_clear),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
