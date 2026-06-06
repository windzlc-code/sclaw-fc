from pathlib import Path

import httpx

from src.bsoup import soup_from_html
from src.db import get_conn, init_db


def read_links(path: Path) -> list[str]:
    lines = [x.strip() for x in path.read_text(encoding="utf-8").splitlines()]
    return [x for x in lines if x and not x.startswith("#")]


def get_page_title(url: str) -> str:
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
        soup = soup_from_html(resp.text)
        title = (soup.title.get_text(" ") if soup.title else "").strip()
        return title or "未命名來源"
    except Exception:
        return "需授權或無法抓取"


def main() -> None:
    init_db()
    path = Path("config/manual_links.txt")
    links = read_links(path)
    ok_count = 0

    with get_conn() as conn:
        for link in links:
            title = get_page_title(link)
            access_status = "public" if title != "需授權或無法抓取" else "restricted"
            access_note = "" if access_status == "public" else "需要授權或登入"
            source_name = "手動來源"
            source_category = "使用者提供"
            if "athome.co.jp" in link:
                source_name = "at home"
                source_category = "大型房仲"
            conn.execute(
                """
                INSERT OR IGNORE INTO source_items (
                    source_name, source_category, source_url, item_url, title_original, body_original,
                    language, published_at, access_status, access_note, last_checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    source_name,
                    source_category,
                    link,
                    link,
                    title,
                    f"人工輸入來源：{link}",
                    "ja",
                    access_status,
                    access_note,
                ),
            )
            conn.execute(
                """
                UPDATE source_items
                SET title_original = ?, body_original = ?, access_status = ?, access_note = ?,
                    source_name = ?, source_category = ?, last_checked_at = CURRENT_TIMESTAMP
                WHERE item_url = ?
                """,
                (
                    title,
                    f"人工輸入來源：{link}",
                    access_status,
                    access_note,
                    source_name,
                    source_category,
                    link,
                ),
            )
            ok_count += 1
        conn.commit()
    print(f"Ingested links: {ok_count}")


if __name__ == "__main__":
    main()
