import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler import crawl_manual_links, crawl_seed_items
from src.db import init_db
from src.pipeline import process_crawled_items
from src.source_registry import load_crawl_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SCLAW crawl and content pipeline.")
    parser.add_argument(
        "--per-source-limit",
        type=int,
        default=None,
        help="Override config/crawl_settings.json per_source_limit for this run.",
    )
    parser.add_argument(
        "--investment-limit",
        type=int,
        default=500,
        help="Incremental investment metrics rows to backfill after ingestion; 0 disables.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    init_db()
    settings = load_crawl_settings()
    per_source_limit = (
        args.per_source_limit
        if args.per_source_limit is not None
        else settings["per_source_limit"]
    )
    if per_source_limit < 1:
        raise ValueError("--per-source-limit must be >= 1")
    os.environ["SCLAW_PIPELINE_INVESTMENT_LIMIT"] = str(max(0, int(args.investment_limit or 0)))

    crawled = crawl_seed_items(per_source_limit=per_source_limit) + crawl_manual_links()
    print(f"Pipeline crawled items: {len(crawled)} | per_source_limit={per_source_limit}", flush=True)
    processed = process_crawled_items(crawled)
    print(f"Pipeline completed. Processed items: {processed} | per_source_limit={per_source_limit}", flush=True)


if __name__ == "__main__":
    main()
