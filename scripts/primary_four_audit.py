import json
import sqlite3
from pathlib import Path

DB = Path("data/jp_real_estate.sqlite3")


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT s.id, s.item_url, COALESCE(s.image_urls,'') AS image_urls, COALESCE(s.body_original,'') AS body_original
        FROM source_items s
        JOIN content_items c ON c.source_item_id = s.id
        WHERE COALESCE(s.content_kind,'')='jp_listing'
          AND (
            instr(lower(COALESCE(s.item_url,'')),'suumo.jp')>0 OR
            instr(lower(COALESCE(s.item_url,'')),'homes.co.jp')>0 OR
            instr(lower(COALESCE(s.item_url,'')),'athome.co.jp')>0 OR
            instr(lower(COALESCE(s.item_url,'')),'realestate.yahoo.co.jp')>0
          )
        ORDER BY s.id DESC
        LIMIT 8000
        """
    ).fetchall()
    conn.close()

    req_fields = ("所在地", "住所", "沿線・駅", "交通", "専有面積", "間取り", "築年月", "所在階")
    by_host = {
        "suumo.jp": {"total": 0, "bad_img": 0, "bad_fields": 0},
        "homes.co.jp": {"total": 0, "bad_img": 0, "bad_fields": 0},
        "athome.co.jp": {"total": 0, "bad_img": 0, "bad_fields": 0},
        "realestate.yahoo.co.jp": {"total": 0, "bad_img": 0, "bad_fields": 0},
    }

    for r in rows:
        u = str(r["item_url"] or "").lower()
        host = next((h for h in by_host.keys() if h in u), "")
        if not host:
            continue
        by_host[host]["total"] += 1
        imgs = [x.strip() for x in str(r["image_urls"] or "").splitlines() if x.strip()]
        bad_img = (not imgs) or all(("kaisha" in x.lower() or "tantou" in x.lower()) for x in imgs[:3])
        if bad_img:
            by_host[host]["bad_img"] += 1
        miss = sum(1 for k in req_fields if k not in str(r["body_original"] or ""))
        if miss >= 6:
            by_host[host]["bad_fields"] += 1

    print(json.dumps(by_host, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
