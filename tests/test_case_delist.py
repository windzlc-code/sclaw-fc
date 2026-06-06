import sqlite3
import unittest

from src.case_delist import (
    ENDED_HOMES_NO_TRUSTED_IMAGE_REASON,
    delist_ended_homes_without_trusted_images,
    should_delist_ended_homes_without_trusted_images,
)


class CaseDelistTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE source_items (
                id INTEGER PRIMARY KEY,
                item_url TEXT,
                title_original TEXT,
                body_original TEXT,
                access_status TEXT DEFAULT 'public',
                access_note TEXT DEFAULT '',
                image_urls TEXT DEFAULT '',
                content_kind TEXT DEFAULT 'jp_listing',
                last_checked_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE content_items (
                id INTEGER PRIMARY KEY,
                source_item_id INTEGER,
                title_zh_hant TEXT DEFAULT '',
                title_zh_hans TEXT DEFAULT '',
                body_zh_hant TEXT DEFAULT '',
                body_zh_hans TEXT DEFAULT '',
                listing_media_json TEXT DEFAULT '[]',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    def tearDown(self):
        self.db.close()

    def _insert_case(self, sid, item_url, body, image_urls=""):
        self.db.execute(
            """
            INSERT INTO source_items
            (id, item_url, title_original, body_original, image_urls, content_kind, access_status)
            VALUES (?, ?, ?, ?, ?, 'jp_listing', 'public')
            """,
            (sid, item_url, f"case {sid}", body, image_urls),
        )
        self.db.execute(
            "INSERT INTO content_items (id, source_item_id, listing_media_json) VALUES (?, ?, '[]')",
            (sid, sid),
        )
        self.db.commit()

    def test_ended_homes_without_trusted_image_is_delisted(self):
        self._insert_case(
            1,
            "https://www.homes.co.jp/kodate/b-93810001007/",
            "該当物件の掲載は終了しました この物件を見ている人におすすめの物件",
            "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F145740%2Fsale%2F3261%2F2%2F1%2Fgmss.jpg&width=1600&height=1600",
        )

        row = self.db.execute("SELECT * FROM source_items WHERE id = 1").fetchone()
        self.assertTrue(should_delist_ended_homes_without_trusted_images(row))

        dry = delist_ended_homes_without_trusted_images(conn=self.db, dry_run=True)
        self.assertEqual(dry["matched_rows"], 1)
        self.assertEqual(dry["updated_rows"], 0)
        self.assertEqual(self.db.execute("SELECT access_status FROM source_items WHERE id = 1").fetchone()[0], "public")

        report = delist_ended_homes_without_trusted_images(conn=self.db)
        self.assertEqual(report["matched_rows"], 1)
        self.assertEqual(report["updated_rows"], 1)
        updated = self.db.execute("SELECT access_status, access_note FROM source_items WHERE id = 1").fetchone()
        self.assertEqual(updated["access_status"], "restricted")
        self.assertEqual(updated["access_note"], ENDED_HOMES_NO_TRUSTED_IMAGE_REASON)

    def test_ended_homes_with_matching_listing_image_stays_public(self):
        self._insert_case(
            2,
            "https://www.homes.co.jp/kodate/b-93810001007/",
            "該当物件の掲載は終了しました",
            "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F9381%2Fsale%2F1007%2F2%2F1%2Fgmss.jpg&width=1600&height=1600",
        )

        row = self.db.execute("SELECT * FROM source_items WHERE id = 2").fetchone()
        self.assertFalse(should_delist_ended_homes_without_trusted_images(row))

        report = delist_ended_homes_without_trusted_images(conn=self.db)
        self.assertEqual(report["matched_rows"], 0)
        self.assertEqual(self.db.execute("SELECT access_status FROM source_items WHERE id = 2").fetchone()[0], "public")


if __name__ == "__main__":
    unittest.main()
