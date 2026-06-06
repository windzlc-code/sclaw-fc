from __future__ import annotations

import argparse
import json
import sqlite3
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
from src.crawler import CrawledItem
from src.pipeline import process_crawled_items
from scripts.expand_three_portals import (
    collect_homes_listing_cards_playwright,
    collect_homes_listing_snippets_playwright,
    homes_expanded_hubs,
    homes_hubs,
    parse_set,
    snippet_payload,
    source_count,
)


def existing_urls(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    out: set[str] = set()
    for start in range(0, len(urls), 700):
        chunk = urls[start : start + 700]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(f"SELECT item_url FROM source_items WHERE item_url IN ({marks})", chunk).fetchall()
        out.update(str(r[0]) for r in rows)
    conn.close()
    return out


def process_chunked(items: list[CrawledItem], chunk_size: int, *, chunk_sleep: float, label: str = "") -> int:
    done = 0
    size = max(1, int(chunk_size or 1))
    for start in range(0, len(items), size):
        end = min(len(items), start + size)
        chunk = items[start:end]
        for attempt in range(8):
            try:
                done += int(process_crawled_items(chunk) or 0)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= 7:
                    raise
                wait = min(20.0, 0.75 * (2**attempt))
                if label:
                    print(f"{label} db_locked retry={attempt + 1}/8 wait={wait:.1f}s", flush=True)
                time.sleep(wait)
        if label:
            print(f"{label} chunk={end}/{len(items)} processed_total={done}", flush=True)
        if chunk_sleep > 0 and start + size < len(items):
            time.sleep(chunk_sleep)
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand LIFULL HOME'S buy listings from paged list-card summaries.")
    parser.add_argument("--modes", default="mansion,house")
    parser.add_argument("--start-index", type=int, default=1)
    parser.add_argument("--max-targets", type=int, default=0)
    parser.add_argument("--per-target", type=int, default=800)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--chunk-sleep-sec", type=float, default=0.5)
    parser.add_argument("--target-sleep-sec", type=float, default=1.5)
    parser.add_argument("--write-report", default="")
    args = parser.parse_args()

    modes = parse_set(args.modes, {"mansion", "house"})
    targets = homes_hubs(modes)
    start_index = max(1, int(args.start_index or 1))
    targets = targets[start_index - 1 :]
    if args.max_targets and args.max_targets > 0:
        targets = targets[: int(args.max_targets)]

    report: dict[str, object] = {
        "ok": True,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "modes": sorted(modes),
        "start_index": start_index,
        "target_count": len(targets),
        "source_count_before": source_count(),
        "homes_before": source_count("%homes.co.jp%"),
        "rows": [],
    }
    print(
        f"start homes_snippet source_jp={report['source_count_before']} homes={report['homes_before']} "
        f"targets={len(targets)} modes={','.join(sorted(modes))}",
        flush=True,
    )

    now = datetime.now(timezone.utc).isoformat()
    for idx, target in enumerate(targets, start=start_index):
        before_all = source_count()
        before_host = source_count("%homes.co.jp%")
        hubs = homes_expanded_hubs(str(target["hub"]))
        print(
            f"[{idx}] {target['label']} collecting hubs={len(hubs)} limit={max(1, int(args.per_target or 1))}",
            flush=True,
        )
        cards = collect_homes_listing_cards_playwright(hubs, limit=max(1, int(args.per_target or 1)))
        if cards:
            snippets = {
                u: str(card.get("text") or "")
                for u, card in cards.items()
                if str(card.get("text") or "").strip()
            }
            snippet_images = {
                u: [str(x) for x in (card.get("image_urls") or []) if str(x or "").strip()]
                for u, card in cards.items()
            }
        else:
            snippets = collect_homes_listing_snippets_playwright(hubs, limit=max(1, int(args.per_target or 1)))
            snippet_images = {}
        urls = list(snippets.keys())
        print(f"[{idx}] {target['label']} collected snippets={len(snippets)}", flush=True)
        known = existing_urls(urls)
        new_urls = [u for u in urls if u not in known]
        items: list[CrawledItem] = []
        for url in new_urls:
            snippet = snippets.get(url, "").strip()
            if not snippet:
                continue
            title, body_original, imgs = snippet_payload(
                target,
                url,
                snippet,
                note="HOME'S 分頁列表摘要先入庫，後續再補詳頁、圖片與欄位。",
                image_urls=snippet_images.get(url) or [],
            )
            items.append(
                CrawledItem(
                    source_name=str(target["name"]),
                    source_category=str(target.get("source_category") or "大型房仲"),
                    source_url=str(target["hub"]),
                    item_url=url,
                    title_original=title[:240],
                    body_original=body_original,
                    language="ja",
                    published_at=now,
                    access_status="public",
                    access_note="",
                    image_urls="\n".join(imgs),
                    content_kind="jp_listing",
                )
            )
        processed = process_chunked(
            items,
            max(1, int(args.chunk_size or 1)),
            chunk_sleep=max(0.0, float(args.chunk_sleep_sec or 0.0)),
            label=f"[{idx}] {target['label']}",
        )
        after_all = source_count()
        after_host = source_count("%homes.co.jp%")
        row = {
            "index": idx,
            "label": target["label"],
            "hub": target["hub"],
            "expanded_hubs": len(hubs),
            "snippets": len(snippets),
            "new_urls": len(new_urls),
            "items": len(items),
            "processed": processed,
            "source_count_before": before_all,
            "source_count_after": after_all,
            "homes_before": before_host,
            "homes_after": after_host,
            "delta": after_all - before_all,
        }
        rows = report["rows"]
        assert isinstance(rows, list)
        rows.append(row)
        print(
            f"[{idx}] {row['label']} snippets={len(snippets)} new={len(new_urls)} items={len(items)} "
            f"processed={processed} all={before_all}->{after_all} homes={before_host}->{after_host}",
            flush=True,
        )
        sleep_sec = max(0.0, float(args.target_sleep_sec or 0.0))
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    report["source_count_after"] = source_count()
    report["homes_after"] = source_count("%homes.co.jp%")
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if args.write_report:
        out = Path(args.write_report)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done source_jp={report['source_count_after']} homes={report['homes_after']}", flush=True)


if __name__ == "__main__":
    main()
