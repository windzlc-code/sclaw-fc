#!/usr/bin/env python3
"""Seed Japan real-estate fair / broker / overseas-buyer knowledge sources."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler import CrawledItem, crawl_item_url
from src.pipeline import process_crawled_items


DEFAULT_SEED_URLS = [
    # Overseas-buyer broker / consultant sources surfaced by proactive search.
    "https://www.i-interface.com/en/services/real_estate.html",
    "https://link-realty.co.jp/en/",
    "https://www.akiyajapan.com/teritoru",
    "https://akiya-sumica.com/",
    "https://www.theagent-s.com/",
    "https://retarealty.com/html/lp/index.html",
    "https://www.tokyoapartments.jp/real-estate-investment/",
    "https://www.livinittokyo.com/",
    "https://www.firststep-jp.com/",
    "https://en.jox-jp.com/",
    "https://mrland.co.jp/home/",
    # Seminar / fair style source.
    "https://basejapan.jp/wp-content/uploads/2025/10/Taiwan-Seminar-Invitation-20251101-FinalVer.pdf",
]


def _read_extra_urls(path: str) -> list[str]:
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        return []
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _manual_seed_item_for_url(url: str) -> CrawledItem | None:
    lower = (url or "").lower()
    if "taiwan-seminar-invitation" not in lower and not lower.endswith(".pdf"):
        return None
    if "basejapan.jp" not in lower:
        return None
    now = datetime.now(timezone.utc).isoformat()
    body = "\n".join(
        [
            "日本房地產展／日本不動產投資說明會資料來源。",
            "",
            "這筆資料用於摘要搜尋與日本房地產知識庫，補足展會、說明會、仲介與海外買家的搜尋語意。",
            "",
            "[適用搜尋主題]",
            "- 台灣買家日本房地產展、日本不動產投資說明會、日本房產說明會",
            "- 日本房產仲介、海外買家服務、投資物件介紹、買房流程",
            "- 物件選擇、區域比較、貸款、稅務、管理、出租與轉售注意事項",
            "",
            "[摘要搜尋用途]",
            "使用者查詢日本房地產展、海外買家日本買房、日本不動產仲介、日本房產投資說明會時，應把此來源視為展會與仲介服務線索。",
            "",
            f"[來源文件] {url}",
        ]
    )
    return CrawledItem(
        source_name="Base Japan｜日本不動產投資說明會",
        source_category="日本房地產展與說明會",
        source_url="https://basejapan.jp/",
        item_url=url,
        title_original="台灣日本不動產投資說明會｜日本房地產展／海外買家セミナー",
        body_original=body,
        language="zh-Hant",
        published_at=now,
        access_status="public",
        access_note="manual fair/seminar seed",
        content_kind="manual_url",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest proactive Japan real-estate fair/broker/market knowledge URLs.")
    ap.add_argument("--file", help="Optional extra URL list, one per line.")
    ap.add_argument("--limit", type=int, default=0, help="Limit URL count for this run; 0 means all.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    urls = list(dict.fromkeys(DEFAULT_SEED_URLS + _read_extra_urls(args.file or "")))
    if args.limit and args.limit > 0:
        urls = urls[: args.limit]
    results: list[dict] = []
    processed = 0
    for url in urls:
        try:
            manual_item = _manual_seed_item_for_url(url)
            items = [manual_item] if manual_item else crawl_item_url(url)
            if not args.dry_run and items:
                processed += int(process_crawled_items(items) or 0)
            results.append(
                {
                    "url": url,
                    "ok": bool(items),
                    "items": [
                        {
                            "source_name": it.source_name,
                            "title": it.title_original,
                            "content_kind": it.content_kind,
                            "access_status": it.access_status,
                            "body_chars": len(it.body_original or ""),
                        }
                        for it in items
                    ],
                }
            )
        except Exception as exc:
            results.append({"url": url, "ok": False, "error": str(exc)[:500]})
    print(json.dumps({"ok": True, "url_count": len(urls), "processed": processed, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
