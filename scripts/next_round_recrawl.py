import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crawler import crawl_item_url
from src.pipeline import process_crawled_items


def main() -> None:
    db = ROOT / "data" / "jp_real_estate.sqlite3"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT s.id,s.item_url,COALESCE(s.image_urls,'') AS image_urls,COALESCE(s.body_original,'') AS body_original
        FROM source_items s
        JOIN content_items c ON c.source_item_id=s.id
        WHERE COALESCE(s.content_kind,'')='jp_listing'
        ORDER BY s.id DESC
        LIMIT 6000
        """
    ).fetchall()
    conn.close()

    keys = ("所在地", "住所", "沿線・駅", "交通", "専有面積", "間取り", "築年月", "所在階")
    candidates: list[tuple[int, str, int, int]] = []
    for r in rows:
        imgs = [x.strip() for x in str(r["image_urls"] or "").splitlines() if x.strip()]
        body = str(r["body_original"] or "")
        missing = sum(1 for k in keys if k not in body)
        img_bad = (not imgs) or all(
            ("gazo%2fkaisha" in x.lower() or "/kaisha/" in x.lower() or "tantou" in x.lower()) for x in imgs[:3]
        )
        if img_bad or missing >= 6:
            candidates.append((int(r["id"]), str(r["item_url"]), len(imgs), missing))
        if len(candidates) >= 40:
            break

    print(f"next_round_candidates {len(candidates)}")
    ok: list[int] = []
    for cid, url, img_before, missing_before in candidates:
        try:
            items = crawl_item_url(url)
            n = process_crawled_items(items) if items else 0
            if n > 0:
                ok.append(cid)
            print(f"recrawl {cid} processed={n} img_before={img_before} miss_before={missing_before}")
        except Exception as e:
            print(f"recrawl {cid} error={type(e).__name__}")
    print(f"next_round_done {len(ok)}")
    print("ids", ",".join(str(x) for x in ok))


if __name__ == "__main__":
    main()
