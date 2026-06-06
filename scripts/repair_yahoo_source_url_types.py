from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.config import DB_PATH


PREF_CODES: tuple[tuple[str, str], ...] = (
    ("北海道", "02/01"),
    ("青森", "01/02"),
    ("岩手", "01/03"),
    ("宮城", "01/04"),
    ("仙台", "01/04"),
    ("秋田", "01/05"),
    ("山形", "01/06"),
    ("福島", "01/07"),
    ("茨城", "03/08"),
    ("栃木", "03/09"),
    ("群馬", "03/10"),
    ("埼玉", "03/11"),
    ("さいたま", "03/11"),
    ("千葉", "03/12"),
    ("東京", "03/13"),
    ("東京都", "03/13"),
    ("神奈川", "03/14"),
    ("横浜", "03/14"),
    ("川崎", "03/14"),
    ("新潟", "04/15"),
    ("富山", "04/16"),
    ("石川", "04/17"),
    ("金沢", "04/17"),
    ("福井", "04/18"),
    ("山梨", "04/19"),
    ("長野", "04/20"),
    ("岐阜", "05/21"),
    ("静岡", "05/22"),
    ("愛知", "05/23"),
    ("名古屋", "05/23"),
    ("三重", "05/24"),
    ("滋賀", "06/25"),
    ("京都", "06/26"),
    ("大阪", "06/27"),
    ("兵庫", "06/28"),
    ("神戸", "06/28"),
    ("奈良", "06/29"),
    ("和歌山", "06/30"),
    ("鳥取", "07/31"),
    ("島根", "07/32"),
    ("岡山", "07/33"),
    ("広島", "07/34"),
    ("廣島", "07/34"),
    ("山口", "07/35"),
    ("徳島", "08/36"),
    ("香川", "08/37"),
    ("愛媛", "08/38"),
    ("高知", "08/39"),
    ("福岡", "09/40"),
    ("北九州", "09/40"),
    ("佐賀", "09/41"),
    ("長崎", "09/42"),
    ("熊本", "09/43"),
    ("大分", "09/44"),
    ("宮崎", "09/45"),
    ("鹿児島", "09/46"),
    ("沖縄", "09/47"),
    ("沖繩", "09/47"),
    ("那覇", "09/47"),
)


def yahoo_search_path(item_url: str) -> str:
    u = (item_url or "").lower()
    if "/used/mansion/" in u:
        return "used/mansion/search"
    if "/used/house/" in u:
        return "used/house/search"
    if "/new/house/" in u:
        return "new/house/search"
    if "/land/" in u:
        return "land/search"
    return ""


def infer_pref_code(*parts: str) -> str:
    text = "\n".join(str(p or "") for p in parts)
    for token, code in PREF_CODES:
        if token in text:
            return code
    return ""


def expected_source_url(path: str, code: str) -> str:
    base = f"https://realestate.yahoo.co.jp/{path}/"
    if code:
        return f"{base}{code}/"
    return base


def should_update(current: str, path: str, code: str) -> bool:
    cur = (current or "").lower()
    if f"/{path}/" not in cur:
        return True
    if code and f"/{code}/" not in cur:
        return True
    return False


def repair(*, limit: int, dry_run: bool) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    rows = conn.execute(
        """
        SELECT id, source_url, item_url, title_original, body_original
        FROM source_items
        WHERE content_kind='jp_listing'
          AND item_url LIKE '%realestate.yahoo.co.jp%'
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    attempted = 0
    fixed = 0
    samples: list[dict] = []
    for row in rows:
        path = yahoo_search_path(str(row["item_url"] or ""))
        if not path:
            continue
        code = infer_pref_code(row["title_original"], row["body_original"], row["source_url"])
        new_url = expected_source_url(path, code)
        if not should_update(str(row["source_url"] or ""), path, code):
            continue
        attempted += 1
        if not dry_run:
            conn.execute(
                "UPDATE source_items SET source_url = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_url, int(row["id"])),
            )
        fixed += 1
        if len(samples) < 30:
            samples.append(
                {
                    "id": int(row["id"]),
                    "old_source_url": row["source_url"],
                    "new_source_url": new_url,
                    "item_url": row["item_url"],
                }
            )
    if not dry_run:
        conn.commit()
    conn.close()
    return {
        "ok": True,
        "dry_run": dry_run,
        "limit": limit,
        "attempted": attempted,
        "fixed": fixed,
        "samples": samples,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize Yahoo real-estate source_url types after mixed fallback runs.")
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(limit=args.limit, dry_run=bool(args.dry_run)), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
