"""Per-portal crawl_one_source benchmark: hard timeout per portal (spawn + terminate).

Run from repo root:

  python scripts/benchmark_portal_crawl.py
  python scripts/benchmark_portal_crawl.py --timeout 120 --limit 20 --query 東京

Requires cwd or PYTHONPATH such that `src` resolves (script inserts repo root on sys.path).
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_URLS = [
    "https://suumo.jp/",
    "https://www.homes.co.jp/",
    "https://www.athome.co.jp/",
    "https://realestate.yahoo.co.jp/",
    "https://realestate.rakuten.co.jp/",
    "https://www.yes1.co.jp/",
    "https://www.oheya-su.jp/",
]


def _worker(url: str, limit: int, query: str, out: mp.Queue) -> None:
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    try:
        from src.crawler import crawl_one_source

        items = crawl_one_source(url, per_source_limit=limit, search_query=query)
        out.put({"url": url, "ok": True, "count": len(items), "error": None})
    except Exception as e:
        out.put(
            {
                "url": url,
                "ok": False,
                "count": 0,
                "error": f"{type(e).__name__}: {e}",
            }
        )


def run_one(url: str, limit: int, query: str, timeout_sec: float) -> dict[str, Any]:
    ctx = mp.get_context("spawn")
    q: mp.Queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_worker, args=(url, limit, query, q))
    proc.start()
    proc.join(timeout=timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=8)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=8)
        return {"url": url, "ok": False, "count": 0, "error": "TIMEOUT"}
    try:
        return q.get_nowait()
    except Exception:
        return {"url": url, "ok": False, "count": 0, "error": "NO_RESULT"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark crawl_one_source per portal with isolated process and hard timeout."
    )
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--query", type=str, default="東京")
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Seconds per portal; child process is terminated after this (default 180).",
    )
    parser.add_argument("--urls", nargs="*", default=None, help="Override default seven portal roots.")
    args = parser.parse_args()
    urls = list(args.urls) if args.urls else DEFAULT_URLS

    print(
        f"benchmark_portal_crawl limit={args.limit} query={args.query!r} "
        f"timeout={args.timeout}s portals={len(urls)}",
        flush=True,
    )
    t0 = time.perf_counter()
    failed = 0
    for url in urls:
        r = run_one(url, args.limit, args.query, args.timeout)
        err = r.get("error")
        cnt = int(r.get("count") or 0)
        ok = bool(r.get("ok"))
        if not ok:
            failed += 1
        status = "OK" if ok else "FAIL"
        line = f"{status}\t{cnt}\t{url}"
        if err:
            line += f"\t{err}"
        print(line, flush=True)
    elapsed = time.perf_counter() - t0
    print(f"done_elapsed_sec={elapsed:.1f} failed_portals={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
