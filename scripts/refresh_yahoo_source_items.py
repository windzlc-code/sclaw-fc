"""
將 Yahoo!不動產 列表／詳情 URL 重新擷取並寫入 source_items（供案內查詢與版型欄位）。

用法:
  .\\.venv\\Scripts\\python.exe scripts/refresh_yahoo_source_items.py 4446 4447
  .\\.venv\\Scripts\\python.exe scripts/refresh_yahoo_source_items.py 4446-4450
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

import httpx

from src.crawler import BROWSER_HEADERS
from src.db import get_conn
from src.portal_property_crawl import fetch_property_detail


def _parse_ids(argv: list[str]) -> list[int]:
    out: list[int] = []
    for a in argv:
        s = a.strip()
        if not s:
            continue
        m = re.match(r"^(\d+)-(\d+)$", s)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, hi + 1))
            continue
        if s.isdigit():
            out.append(int(s))
    return sorted(set(out))


def main() -> None:
    ids = _parse_ids(sys.argv[1:])
    if not ids:
        print("usage: refresh_yahoo_source_items.py <id> [id2] [start-end]", file=sys.stderr)
        sys.exit(2)
    now = datetime.now(timezone.utc).isoformat()
    for sid in ids:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id, item_url, title_original FROM source_items WHERE id = ?",
                (sid,),
            ).fetchone()
        if not row:
            print(f"skip missing id {sid}")
            continue
        item_url = str(row["item_url"] or "").strip()
        if "realestate.yahoo.co.jp" not in item_url.lower():
            print(f"skip {sid}: not Yahoo URL")
            continue
        to = 60.0 if ("/land/search/" in item_url or "/used/mansion/search/" in item_url) else 25.0
        try:
            with httpx.Client(timeout=to, follow_redirects=True, headers=BROWSER_HEADERS) as client:
                title, body_original, imgs = fetch_property_detail(client, item_url)
        except Exception as e:
            print(f"ERR {sid}: {e}")
            continue
        img_block = "\n".join(imgs) if imgs else ""
        # 與站內 fetch 邏輯一致；略過重複的 title 行首若已在 body
        if not str(body_original or "").strip() and not img_block:
            print(f"skip {sid}: empty body")
            continue
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE source_items
                SET title_original = ?,
                    body_original = ?,
                    image_urls = ?,
                    last_checked_at = ?,
                    crawled_at = ?
                WHERE id = ?
                """,
                (
                    (str(title).strip()[:200] if title else str(row["title_original"] or "")[:200]),
                    str(body_original or "").strip(),
                    "\n".join((imgs or [])[:60]),
                    now,
                    now,
                    sid,
                ),
            )
            conn.commit()
        print(f"ok {sid} images={len(imgs or [])} body_len={len(str(body_original or ''))}")


if __name__ == "__main__":
    main()
