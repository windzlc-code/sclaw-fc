import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fill_matrix_min10 import matrix
from src.crawler import crawl_one_source
from src.pipeline import process_crawled_items


def coverage_total(age_days: int) -> int:
    m = matrix(age_days)
    return int(sum(m.values()))


def jp_listing_counts() -> dict[str, int]:
    import sqlite3

    conn = sqlite3.connect(str(ROOT / "data" / "jp_real_estate.sqlite3"))
    rows = conn.execute(
        """
        SELECT
          SUM(CASE WHEN COALESCE(content_kind,'')='jp_listing' THEN 1 ELSE 0 END) AS jp_listing,
          SUM(CASE WHEN COALESCE(content_kind,'')='jp_listing' AND (
            item_url LIKE '%/ikkodate/%' OR
            item_url LIKE '%/chukoikkodate/%' OR
            item_url LIKE '%/kodate/%' OR
            item_url LIKE '%/used/house/%' OR
            item_url LIKE '%/new/house/%' OR
            item_url LIKE '%detached%' OR
            item_url LIKE '%/contents/detail/%'
          ) THEN 1 ELSE 0 END) AS detached_like,
          SUM(CASE WHEN COALESCE(content_kind,'')='jp_listing' AND (
            item_url LIKE '%/chintai/%' OR
            item_url LIKE '%/room/%' OR
            body_original LIKE '%賃貸%' OR
            body_original LIKE '%ワンルーム%' OR
            body_original LIKE '%1R%' OR
            body_original LIKE '%1K%'
          ) THEN 1 ELSE 0 END) AS rental_like,
          SUM(CASE WHEN COALESCE(content_kind,'')='jp_listing' AND (
            item_url LIKE '%/ek_%' OR
            body_original LIKE '%徒歩%' OR
            body_original LIKE '%駅%'
          ) THEN 1 ELSE 0 END) AS transit_like
        FROM source_items
        """
    ).fetchone()
    conn.close()
    return {
        "jp_listing": int(rows[0] or 0),
        "detached_like": int(rows[1] or 0),
        "rental_like": int(rows[2] or 0),
        "transit_like": int(rows[3] or 0),
    }


def process_crawled_items_chunked(items: list, chunk_size: int) -> int:
    if not items:
        return 0
    size = max(1, int(chunk_size or 1))
    if size >= len(items):
        return int(process_crawled_items(items) or 0)
    processed = 0
    for start in range(0, len(items), size):
        processed += int(process_crawled_items(items[start : start + size]) or 0)
    return processed


REGION_CODES = [
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
]

SUUMO_PREFS = [
    ("tokyo", "東京"),
    ("kanagawa", "神奈川"),
    ("saitama", "埼玉"),
    ("chiba", "千葉"),
    ("osaka", "大阪"),
    ("kyoto", "京都"),
    ("hyogo", "兵庫"),
    ("aichi", "名古屋"),
    ("fukuoka", "福岡"),
    ("hiroshima", "中國地方"),
    ("miyagi", "東北"),
    ("hokkaido", "北海道"),
    ("nagano", "甲信越"),
    ("niigata", "甲信越"),
    ("yamanashi", "甲信越"),
    ("ishikawa", "北陸"),
]

YES1_CITY = [
    ("hokkaido/01100-city", "北海道"),
    ("tokyo/13100-city", "東京"),
    ("kanagawa/14100-city", "神奈川"),
    ("saitama/11100-city", "埼玉"),
    ("chiba/12100-city", "千葉"),
    ("aichi/23100-city", "名古屋"),
    ("osaka/27100-city", "大阪"),
    ("kyoto/26100-city", "京都"),
    ("hyogo/28100-city", "關西"),
    ("hiroshima/34100-city", "中國地方"),
    ("fukuoka/40130-city", "福岡"),
]

ATHOME_PREFS = [
    ("tokyo", "東京"),
    ("kanagawa", "神奈川"),
    ("saitama", "埼玉"),
    ("chiba", "千葉"),
    ("osaka", "大阪"),
    ("hyogo", "關西"),
    ("aichi", "名古屋"),
    ("fukuoka", "福岡"),
    ("hokkaido", "北海道"),
    ("kyoto", "京都"),
    ("nagano", "甲信越"),
]


def build_batches() -> list[tuple[str, str, str]]:
    batches: list[tuple[str, str, str]] = []

    # Current result pages are processed from page 1 first, so these batches bias toward fresh listings.
    for pref, query, code in REGION_CODES:
        batches.append((f"yahoo-used-house-{code}", f"https://realestate.yahoo.co.jp/used/house/search/{code}/", query))
        batches.append((f"yahoo-used-mansion-{code}", f"https://realestate.yahoo.co.jp/used/mansion/search/{code}/", query))
    for pref, query, code in REGION_CODES[:16]:
        batches.append((f"yahoo-new-house-{code}", f"https://realestate.yahoo.co.jp/new/house/search/{code}/", query))
    for pref, query, code in REGION_CODES[:18]:
        batches.append((f"yahoo-land-{code}", f"https://realestate.yahoo.co.jp/land/search/{code}/", query))

    # Single-room/rental + station/walk entry points.  SUUMO exposes explicit new/rental/line hubs.
    for pref, query in SUUMO_PREFS:
        batches.append((f"suumo-chintai-new-{pref}", f"https://suumo.jp/chintai/{pref}/new/", f"{query} 新着 賃貸"))
        batches.append((f"suumo-chintai-{pref}", f"https://suumo.jp/chintai/{pref}/", f"{query} 賃貸 1R 1K"))
        batches.append((f"suumo-chintai-mansion-{pref}", f"https://suumo.jp/chintai/{pref}/mansion/", f"{query} 賃貸 ワンルーム"))
        batches.append((f"suumo-chintai-apartment-{pref}", f"https://suumo.jp/chintai/{pref}/apartment/", f"{query} 賃貸 1K"))
        batches.append((f"suumo-chintai-line-{pref}", f"https://suumo.jp/chintai/{pref}/ensen/", f"{query} 沿線 駅 徒歩"))
        batches.append((f"suumo-chintai-walk-{pref}", f"https://suumo.jp/chintai/soba/{pref}/", f"{query} 駅近 徒歩5分"))

    for pref, query in SUUMO_PREFS:
        batches.append((f"suumo-chukoikkodate-{pref}", f"https://suumo.jp/chukoikkodate/{pref}/", query))
    for pref, query in SUUMO_PREFS[:10]:
        batches.append((f"suumo-ikkodate-{pref}", f"https://suumo.jp/ikkodate/{pref}/", query))
    for pref, query in ATHOME_PREFS:
        batches.append((f"athome-chintai-{pref}", f"https://www.athome.co.jp/chintai/{pref}/", f"{query} 賃貸 1R 1K"))
        batches.append((f"athome-walk5-{pref}", f"https://www.athome.co.jp/mansion/shinchiku/tag/5minute/{pref}/list/", f"{query} 徒歩5分"))
    for pref, query in ATHOME_PREFS:
        batches.append((f"athome-kodate-{pref}", f"https://www.athome.co.jp/kodate/chuko/{pref}/list/", query))
    for path, query in YES1_CITY:
        batches.append((f"yes1-house-{path.replace('/', '-')}", f"https://www.yes1.co.jp/contents/search_area/house/used/{path}", query))
    batches.extend(
        [
            ("rakuten-useddetached-item", "https://realestate.rakuten.co.jp/useddetached/item/", "戸建"),
            ("rakuten-newdetached-item", "https://realestate.rakuten.co.jp/newdetached/item/", "戸建"),
            ("rakuten-useddetached", "https://realestate.rakuten.co.jp/useddetached/?area=zenkoku", "戸建"),
            ("rakuten-newdetached", "https://realestate.rakuten.co.jp/newdetached/?area=zenkoku", "戸建"),
        ]
    )
    return batches


def main() -> None:
    parser = argparse.ArgumentParser(description="Boost SCLAW coverage matrix to target with fresh regional, rental, transit, and detached-house listings.")
    parser.add_argument("--target", type=int, default=6000)
    parser.add_argument("--age-days", type=int, default=180)
    parser.add_argument("--per-source", type=int, default=80)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--sleep-sec", type=float, default=0.5)
    parser.add_argument("--check-every", type=int, default=1, help="Recompute the coverage matrix after this many batches.")
    parser.add_argument("--process-chunk-size", type=int, default=40, help="Commit crawled items in smaller chunks to avoid long SQLite write locks.")
    parser.add_argument("--start-index", type=int, default=1, help="1-based batch index to start from when resuming.")
    parser.add_argument("--skip-label-regex", default="", help="Regex of batch labels to skip during this run.")
    args = parser.parse_args()
    check_every = max(1, int(args.check_every or 1))
    process_chunk_size = max(1, int(args.process_chunk_size or 1))
    start_index = max(1, int(args.start_index or 1))
    skip_label_re = re.compile(args.skip_label_regex) if (args.skip_label_regex or "").strip() else None

    os.environ.setdefault("SCLAW_PORTAL_HUB_CAP", "90")
    os.environ.setdefault("SCLAW_ATHOME_HUB_CAP", "48")
    os.environ.setdefault("SCLAW_REMAINING_PORTAL_HUB_CAP", "18")
    os.environ.setdefault("SCLAW_ATHOME_CATALOG_MAX_PAGES", "18")
    os.environ.setdefault("SCLAW_HOMES_CATALOG_MAX_PAGES", "18")

    started = datetime.now().isoformat(timespec="seconds")
    report: dict[str, object] = {
        "started_at": started,
        "target": args.target,
        "age_days": args.age_days,
        "per_source": args.per_source,
        "check_every": check_every,
        "process_chunk_size": process_chunk_size,
        "start_index": start_index,
        "skip_label_regex": args.skip_label_regex,
        "rounds": [],
    }
    total = coverage_total(args.age_days)
    counts = jp_listing_counts()
    print(
        f"start total={total} jp_listing={counts['jp_listing']} detached_like={counts['detached_like']} "
        f"rental_like={counts['rental_like']} transit_like={counts['transit_like']} target={args.target}",
        flush=True,
    )

    batches = build_batches()
    for round_idx in range(1, max(1, args.max_rounds) + 1):
        round_rows: list[dict[str, object]] = []
        handled_idx = 0
        print(f"round {round_idx} batches={len(batches)}", flush=True)
        for idx, (label, url, query) in enumerate(batches, start=1):
            if idx < start_index:
                continue
            if skip_label_re and skip_label_re.search(label):
                continue
            handled_idx += 1
            total_before = total
            try:
                items = crawl_one_source(url, per_source_limit=args.per_source, search_query=query)
                processed = process_crawled_items_chunked(items or [], process_chunk_size)
                should_check_total = (handled_idx % check_every == 0) or idx == len(batches)
                if should_check_total:
                    total = coverage_total(args.age_days)
                counts = jp_listing_counts()
                row = {
                    "label": label,
                    "url": url,
                    "query": query,
                    "items": len(items or []),
                    "processed": int(processed or 0),
                    "before": total_before,
                    "after": total if should_check_total else None,
                    "delta": total - total_before if should_check_total else None,
                    "checked_total": should_check_total,
                    "jp_listing": counts["jp_listing"],
                    "detached_like": counts["detached_like"],
                    "rental_like": counts["rental_like"],
                    "transit_like": counts["transit_like"],
                }
                total_part = (
                    f"total={total_before}->{total} delta={row['delta']}"
                    if should_check_total
                    else f"total={total_before}->pending delta=pending"
                )
                print(
                    f"[{round_idx}:{idx}/{len(batches)}] {label} items={row['items']} processed={row['processed']} "
                    f"{total_part} detached={counts['detached_like']} "
                    f"rental={counts['rental_like']} transit={counts['transit_like']}",
                    flush=True,
                )
            except Exception as exc:
                row = {
                    "label": label,
                    "url": url,
                    "query": query,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "before": total_before,
                    "after": total,
                    "delta": 0,
                }
                print(f"[{round_idx}:{idx}/{len(batches)}] {label} error={row['error']}", flush=True)
            round_rows.append(row)
            if total >= args.target:
                break
            if args.sleep_sec > 0:
                time.sleep(args.sleep_sec)
        report["rounds"].append({"round": round_idx, "rows": round_rows, "total_after": total})
        if total >= args.target:
            break
        if not any(int(r.get("delta") or 0) > 0 for r in round_rows if r.get("checked_total")):
            print("no positive delta in this round; stopping early", flush=True)
            break

    total = coverage_total(args.age_days)
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    report["final_total"] = total
    report["final_counts"] = jp_listing_counts()
    out = ROOT / "data" / "boost_matrix_to_6000_last.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    target_out = ROOT / "data" / f"boost_matrix_to_{args.target}_last.json"
    target_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    stamped = ROOT / "data" / f"boost_matrix_to_6000_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    stamped.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done final_total={total} report={stamped}", flush=True)


if __name__ == "__main__":
    main()
