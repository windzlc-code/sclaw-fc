import unittest

from src.case_quality_batch import _parse_taipei_slot, _row_needs_quality_recrawl


class CaseQualityBatchTests(unittest.TestCase):
    def test_parse_taipei_slot_accepts_hour_minute(self):
        self.assertEqual(_parse_taipei_slot("04:30"), (4, 30))
        self.assertEqual(_parse_taipei_slot("25:99"), (23, 59))
        self.assertEqual(_parse_taipei_slot("bad", default="03:15"), (3, 15))

    def test_row_without_images_needs_recrawl(self):
        needs, reason = _row_needs_quality_recrawl(
            {
                "image_urls": "",
                "body_original": "販売価格 4980万円",
            }
        )

        self.assertTrue(needs)
        self.assertTrue(reason["image_bad"])
        self.assertGreaterEqual(reason["missing_field_count"], 1)

    def test_row_with_listing_images_and_core_fields_is_ok(self):
        body = "所在地 東京都\n沿線・駅 JR山手線\n専有面積 80m2\n間取り 3LDK\n築年月 2010年1月\n所在階 5階"
        needs, reason = _row_needs_quality_recrawl(
            {
                "image_urls": "https://img.example.test/listing-room.jpg\nhttps://img.example.test/floorplan.jpg",
                "body_original": body * 4,
            }
        )

        self.assertFalse(needs)
        self.assertFalse(reason["image_bad"])


if __name__ == "__main__":
    unittest.main()
