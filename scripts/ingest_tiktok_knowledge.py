#!/usr/bin/env python3
"""Ingest TikTok video links as searchable SCLAW knowledge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler import crawl_item_url
from src.pipeline import process_crawled_items


def _read_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    for u in args.urls or []:
        s = str(u or "").strip()
        if s:
            urls.append(s)
    if args.file:
        p = Path(args.file).expanduser()
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            urls.append(s)
    return list(dict.fromkeys(urls))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest TikTok videos into source_items/content_items knowledge tables.")
    ap.add_argument("urls", nargs="*", help="TikTok video URLs or vt.tiktok.com short links.")
    ap.add_argument("--file", help="Text file containing one TikTok URL per line.")
    ap.add_argument("--dry-run", action="store_true", help="Parse only; do not write to DB.")
    args = ap.parse_args()

    urls = _read_urls(args)
    if not urls:
        print(json.dumps({"ok": False, "reason": "no_urls"}, ensure_ascii=False))
        return 2

    results: list[dict] = []
    processed = 0
    for url in urls:
        try:
            items = crawl_item_url(url)
            preview = []
            for it in items:
                preview.append(
                    {
                        "source_name": it.source_name,
                        "item_url": it.item_url,
                        "title": it.title_original,
                        "content_kind": it.content_kind,
                        "access_status": it.access_status,
                        "body_chars": len(it.body_original or ""),
                    }
                )
            if not args.dry_run and items:
                processed += int(process_crawled_items(items) or 0)
            results.append({"url": url, "ok": bool(items), "items": preview})
        except Exception as exc:
            results.append({"url": url, "ok": False, "error": str(exc)[:500]})
    print(json.dumps({"ok": True, "url_count": len(urls), "processed": processed, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
