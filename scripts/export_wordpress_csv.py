import csv

from src.config import EXPORT_DIR
from src.db import get_conn


def main() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = EXPORT_DIR / "wordpress_posts.csv"

    rows = []
    with get_conn() as conn:
        sql = """
            SELECT
                c.id,
                c.seo_slug,
                c.seo_title,
                c.seo_description,
                c.title_zh_hant,
                c.title_zh_hans,
                c.body_zh_hant,
                c.body_zh_hans,
                c.region_code,
                c.keyword_type,
                c.intent_target,
                c.topic_category,
                c.keyword_tags,
                c.updated_at,
                c.schema_json,
                s.source_name,
                s.item_url,
                s.access_status
            FROM content_items c
            JOIN source_items s ON s.id = c.source_item_id
            ORDER BY datetime(c.updated_at) DESC, c.id DESC
        """
        rows = conn.execute(sql).fetchall()

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "post_title",
                "post_name",
                "post_excerpt",
                "post_content",
                "post_status",
                "language",
                "region_code",
                "keyword_type",
                "intent_target",
                "topic_category",
                "keyword_tags",
                "updated_at",
                "source_access_status",
                "schema_json_ld",
                "source_name",
                "source_url",
            ]
        )
        for row in rows:
            content = (
                f"<h2>{row['title_zh_hant']}</h2>\n"
                f"<p>{row['body_zh_hant']}</p>\n"
                f"<hr/>\n"
                f"<p><strong>简体版：</strong>{row['body_zh_hans']}</p>\n"
                f"<p><strong>來源：</strong><a href=\"{row['item_url']}\" rel=\"nofollow noopener\" target=\"_blank\">"
                f"{row['source_name']}</a></p>"
            )
            writer.writerow(
                [
                    row["seo_title"],
                    row["seo_slug"],
                    row["seo_description"],
                    content,
                    "publish",
                    "zh",
                    row["region_code"],
                    row["keyword_type"],
                    row["intent_target"],
                    row["topic_category"],
                    row["keyword_tags"],
                    row["updated_at"],
                    row["access_status"],
                    row["schema_json"],
                    row["source_name"],
                    row["item_url"],
                ]
            )
    print(f"Exported: {output_path}")


if __name__ == "__main__":
    main()
