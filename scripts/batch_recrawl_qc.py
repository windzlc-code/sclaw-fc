import argparse
import json
import multiprocessing as mp
import queue
import sqlite3
import sys
import urllib.request
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler import crawl_item_url
from src.pipeline import process_crawled_items

DB = Path("data/jp_real_estate.sqlite3")


class _RecrawlTimeout(Exception):
    pass


def _crawl_worker(url: str, out_q) -> None:
    try:
        out_q.put(("ok", crawl_item_url(url)))
    except Exception as ex:
        out_q.put(("err", f"{type(ex).__name__}: {ex}"))


def crawl_item_url_hard_timeout(url: str, timeout_s: int):
    if timeout_s <= 0:
        return crawl_item_url(url)
    ctx = mp.get_context("fork")
    out_q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_crawl_worker, args=(url, out_q))
    proc.daemon = True
    proc.start()
    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        if proc.is_alive():
            proc.kill()
            proc.join(2)
        raise _RecrawlTimeout(f"recrawl item timed out after {timeout_s}s")
    try:
        status, payload = out_q.get_nowait()
    except queue.Empty:
        return []
    if status == "ok":
        return payload or []
    raise RuntimeError(str(payload))


def pick_candidates(*, limit: int = 120, scan_limit: int = 8000) -> list[tuple[int, str, int, int]]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    sql = """
    SELECT s.id, s.item_url, COALESCE(s.image_urls,'') AS image_urls, COALESCE(s.body_original,'') AS body_original
    FROM source_items s
    JOIN content_items c ON c.source_item_id = s.id
    WHERE COALESCE(s.content_kind,'') = 'jp_listing'
    ORDER BY s.id DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (max(1000, int(scan_limit or 8000)),)).fetchall()
    conn.close()
    keys = ("所在地", "住所", "沿線・駅", "交通", "専有面積", "間取り", "築年月", "所在階")
    out: list[tuple[int, str, int, int]] = []
    for r in rows:
        imgs = [x.strip() for x in str(r["image_urls"] or "").splitlines() if x.strip()]
        body = str(r["body_original"] or "")
        missing = sum(1 for k in keys if k not in body)
        img_bad = (not imgs) or all(
            ("gazo%2fkaisha" in x.lower() or "/kaisha/" in x.lower() or "tantou" in x.lower()) for x in imgs[:3]
        )
        if img_bad or missing >= 6:
            out.append((int(r["id"]), str(r["item_url"]), len(imgs), missing))
        if len(out) >= limit:
            break
    return out


def recrawl(
    ids: list[tuple[int, str, int, int]],
    *,
    timeout_s: int = 45,
    progress_every: int = 25,
    process_batch_size: int = 20,
) -> tuple[int, list[int]]:
    ok = 0
    touched: list[int] = []
    pending_items: list = []
    pending_ids: list[int] = []
    total = len(ids)

    def flush_pending() -> None:
        nonlocal ok, pending_items, pending_ids
        if not pending_items:
            return
        try:
            n = process_crawled_items(pending_items)
        except Exception as ex:
            print(f"recrawl_batch_process_error size={len(pending_items)} error={type(ex).__name__}: {ex}", flush=True)
        else:
            if n > 0:
                ok += int(n)
                touched.extend(pending_ids[: int(n)])
                print(f"recrawl_batch_processed size={len(pending_items)} processed={n} ok={ok}", flush=True)
        pending_items = []
        pending_ids = []

    for idx, (cid, url, _imgn, _miss) in enumerate(ids, start=1):
        if progress_every > 0 and (idx == 1 or idx % progress_every == 0 or idx == total):
            print(f"recrawl_progress={idx}/{total} ok={ok} current_id={cid}", flush=True)
        try:
            items = crawl_item_url_hard_timeout(url, timeout_s)
            if not items:
                continue
            pending_items.extend(items)
            pending_ids.append(cid)
            if len(pending_items) >= process_batch_size:
                flush_pending()
        except _RecrawlTimeout as ex:
            print(f"recrawl_timeout id={cid} url={url} error={ex}", flush=True)
        except Exception:
            continue
    flush_pending()
    return ok, touched


def matrix_totals(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Recrawl incomplete jp_listing items (missing fields / bad images).")
    ap.add_argument("--limit", type=int, default=120, help="Candidates to recrawl (default 120).")
    ap.add_argument("--scan-limit", type=int, default=8000, help="Recent rows to scan (default 8000).")
    ap.add_argument(
        "--matrix-url",
        type=str,
        default="http://127.0.0.1:8000/api/cases/coverage-matrix?age_days=180&threshold_per_cell=15",
        help="Optional matrix endpoint for summary.",
    )
    ap.add_argument("--skip-matrix", action="store_true", help="Skip calling the matrix endpoint.")
    ap.add_argument("--timeout", type=int, default=45, help="Max seconds per candidate recrawl (default 45).")
    ap.add_argument("--progress-every", type=int, default=25, help="Print recrawl progress every N rows (default 25).")
    ap.add_argument("--process-batch-size", type=int, default=20, help="Commit crawled items in batches (default 20).")
    args = ap.parse_args()

    cand = pick_candidates(limit=max(1, int(args.limit or 1)), scan_limit=max(1000, int(args.scan_limit or 8000)))
    print(f"candidates={len(cand)}", flush=True)
    for cid, url, imgn, miss in cand[:30]:
        print(f"{cid}\timg={imgn}\tmissing={miss}\t{url}", flush=True)
    ok, touched = recrawl(
        cand,
        timeout_s=max(0, int(args.timeout or 0)),
        progress_every=max(0, int(args.progress_every or 0)),
        process_batch_size=max(1, int(args.process_batch_size or 1)),
    )
    print(f"recrawled_ok={ok}", flush=True)
    print("touched_ids=", ",".join(str(x) for x in touched[:120]), flush=True)
    if not args.skip_matrix:
        try:
            m = matrix_totals(str(args.matrix_url or ""))
            print("by_portal_total=", m.get("by_portal_total", {}), flush=True)
        except (URLError, TimeoutError, ConnectionError, ValueError) as ex:
            print(f"matrix_totals_error={type(ex).__name__}: {ex}", flush=True)
