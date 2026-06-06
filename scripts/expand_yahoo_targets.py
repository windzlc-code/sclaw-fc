from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

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
from src.portal_property_crawl import (
    PORTAL_BROWSER_HEADERS,
    _collect_yahoo_house_search_links,
    _collect_yahoo_land_search_links,
    _collect_yahoo_used_mansion_search_links,
    fetch_property_detail,
)


REGION_CODES: tuple[tuple[str, str, str], ...] = (
    ("tokyo", "東京", "03/13"),
    ("kanagawa", "神奈川", "03/14"),
    ("saitama", "埼玉", "03/11"),
    ("chiba", "千葉", "03/12"),
    ("osaka", "大阪", "06/27"),
    ("kyoto", "京都", "06/26"),
    ("hyogo", "兵庫", "06/28"),
    ("aichi", "名古屋", "05/23"),
    ("hiroshima", "中國地方", "07/34"),
    ("hokkaido", "北海道", "02/01"),
    ("miyagi", "東北", "01/04"),
    ("nagano", "甲信越", "04/20"),
    ("niigata", "甲信越", "04/15"),
    ("yamanashi", "甲信越", "04/19"),
    ("ishikawa", "北陸", "04/17"),
    ("toyama", "北陸", "04/16"),
    ("fukui", "北陸", "04/18"),
    ("shizuoka", "東海", "05/22"),
    ("gifu", "東海", "05/21"),
    ("mie", "東海", "05/24"),
    ("kagawa", "四國", "08/37"),
    ("ehime", "四國", "08/38"),
    ("kochi", "四國", "08/39"),
    ("tokushima", "四國", "08/36"),
    ("fukuoka", "福岡", "09/40"),
    ("kumamoto", "九州", "09/43"),
    ("okinawa", "沖繩", "09/47"),
)

CODE_GROUPS: dict[str, tuple[str, ...]] = {
    "major": ("03/13", "03/14", "03/11", "03/12", "06/27", "06/26", "06/28", "05/23", "07/34"),
    "metro": ("03/13", "03/14", "03/11", "03/12", "06/27", "06/26", "06/28", "05/23"),
    "kanto": ("03/13", "03/14", "03/11", "03/12"),
    "kansai": ("06/27", "06/26", "06/28"),
    "regional": tuple(code for _, _, code in REGION_CODES[8:]),
    "all": tuple(code for _, _, code in REGION_CODES),
}


def target_specs(types: list[str], codes: list[str]) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    selected = {c.strip() for c in codes if c.strip()}
    for _, query, code in REGION_CODES:
        if code not in selected:
            continue
        for kind in types:
            if kind == "used-mansion":
                path = "used/mansion/search"
                label = f"yahoo-used-mansion-{code}"
            elif kind == "used-house":
                path = "used/house/search"
                label = f"yahoo-used-house-{code}"
            elif kind == "new-house":
                path = "new/house/search"
                label = f"yahoo-new-house-{code}"
            elif kind == "land":
                path = "land/search"
                label = f"yahoo-land-{code}"
            else:
                continue
            specs.append(
                {
                    "kind": kind,
                    "label": label,
                    "query": query,
                    "code": code,
                    "url": f"https://realestate.yahoo.co.jp/{path}/{code}/",
                }
            )
    return specs


def collect_links(client: httpx.Client, kind: str, url: str, limit: int) -> list[str]:
    if kind == "land":
        return _collect_yahoo_land_search_links(client, url, limit)
    if kind in {"used-house", "new-house"}:
        return _collect_yahoo_house_search_links(client, url, limit)
    return _collect_yahoo_used_mansion_search_links(client, url, limit)


def existing_urls(urls: list[str]) -> set[str]:
    if not urls:
        return set()
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA busy_timeout=60000")
    out: set[str] = set()
    for start in range(0, len(urls), 600):
        chunk = urls[start : start + 600]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(f"SELECT item_url FROM source_items WHERE item_url IN ({marks})", chunk).fetchall()
        out.update(str(r[0]) for r in rows)
    conn.close()
    return out


def source_count() -> int:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    n = int(conn.execute("SELECT COUNT(*) FROM source_items WHERE content_kind='jp_listing'").fetchone()[0] or 0)
    conn.close()
    return n


def process_chunked(items: list[CrawledItem], chunk_size: int) -> int:
    processed = 0
    size = max(1, int(chunk_size or 1))
    for start in range(0, len(items), size):
        chunk = items[start : start + size]
        for attempt in range(8):
            try:
                processed += int(process_crawled_items(chunk) or 0)
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= 7:
                    raise
                wait = min(20.0, 0.75 * (2**attempt))
                print(f"db_locked retry={attempt + 1}/8 wait={wait:.1f}s", flush=True)
                time.sleep(wait)
    return processed


def fetch_new_items(
    client: httpx.Client,
    *,
    spec: dict[str, str],
    urls: list[str],
    sleep_sec: float,
) -> list[CrawledItem]:
    now = datetime.now(timezone.utc).isoformat()
    out: list[CrawledItem] = []
    for url in urls:
        try:
            title, body_original, imgs = fetch_property_detail(client, url)
        except Exception:
            continue
        out.append(
            CrawledItem(
                source_name="Yahoo!不動産",
                source_category="大型房仲",
                source_url=spec["url"],
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
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return out


def parse_codes(raw: str) -> list[str]:
    v = (raw or "major").strip()
    if v in CODE_GROUPS:
        return list(CODE_GROUPS[v])
    out: list[str] = []
    for part in v.split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out or list(CODE_GROUPS["major"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand Yahoo real-estate listings by collecting detail URLs first, then fetching only new items.")
    parser.add_argument("--types", default="new-house,land,used-house,used-mansion")
    parser.add_argument("--codes", default="major", help="major, metro, kanto, kansai, regional, all, or comma-separated codes like 03/13,03/14")
    parser.add_argument("--per-source", type=int, default=800)
    parser.add_argument("--max-targets", type=int, default=0)
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--sleep-sec", type=float, default=0.05)
    parser.add_argument("--write-report", default="")
    args = parser.parse_args()

    os.environ.setdefault("SCLAW_PLAYWRIGHT", "0")

    types = [x.strip() for x in str(args.types or "").split(",") if x.strip()]
    codes = parse_codes(str(args.codes or "major"))
    specs = target_specs(types, codes)
    if args.max_targets and args.max_targets > 0:
        specs = specs[: int(args.max_targets)]

    report: dict[str, object] = {
        "ok": True,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "types": types,
        "codes": codes,
        "per_source": int(args.per_source),
        "rows": [],
        "source_count_before": source_count(),
    }
    print(
        f"start source_jp={report['source_count_before']} targets={len(specs)} "
        f"types={','.join(types)} per_source={args.per_source}",
        flush=True,
    )

    with httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True, headers=PORTAL_BROWSER_HEADERS) as client:
        for idx, spec in enumerate(specs, start=1):
            before = source_count()
            urls = collect_links(client, spec["kind"], spec["url"], max(1, int(args.per_source)))
            known = existing_urls(urls)
            new_urls = [u for u in urls if u not in known]
            items = fetch_new_items(client, spec=spec, urls=new_urls, sleep_sec=max(0.0, float(args.sleep_sec or 0)))
            processed = process_chunked(items, max(1, int(args.chunk_size or 1)))
            after = source_count()
            row = {
                "index": idx,
                "label": spec["label"],
                "url": spec["url"],
                "collected": len(urls),
                "new_urls": len(new_urls),
                "fetched": len(items),
                "processed": processed,
                "source_count_before": before,
                "source_count_after": after,
                "delta": after - before,
            }
            cast_rows = report["rows"]
            assert isinstance(cast_rows, list)
            cast_rows.append(row)
            print(
                f"[{idx}/{len(specs)}] {spec['label']} collected={len(urls)} new_urls={len(new_urls)} "
                f"fetched={len(items)} processed={processed} source_jp={before}->{after} delta={after-before}",
                flush=True,
            )

    report["source_count_after"] = source_count()
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if args.write_report:
        out = Path(args.write_report)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(out)
    print(f"done source_jp={report['source_count_after']}", flush=True)


if __name__ == "__main__":
    main()
