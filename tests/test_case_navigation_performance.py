from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
CASE_TEMPLATE = ROOT / "templates" / "case.html"


class CaseNavigationPerformanceTests(unittest.TestCase):
    def test_related_card_uses_direct_thumb_before_deep_gallery_scan(self):
        source = APP.read_text(encoding="utf-8")
        start = source.index("def _related_article_thumb_url")
        body = source[start:source.index("def _related_article_identity_keys", start)]

        self.assertIn("for raw in (row.get(\"thumbnail_url\"), row.get(\"hero_image_url\"))", body)
        self.assertLess(
            body.index("for raw in (row.get(\"thumbnail_url\"), row.get(\"hero_image_url\"))"),
            body.index("_case_stored_representative_static_url"),
        )
        self.assertIn("if not allow_sync_fetch:\n        return \"\"", body)
        self.assertLess(
            body.index("if not allow_sync_fetch:\n        return \"\""),
            body.index("_case_stored_representative_static_url"),
        )

    def test_related_card_images_do_not_block_case_navigation(self):
        source = CASE_TEMPLATE.read_text(encoding="utf-8")
        start = source.index("<section class=\"panel case-related-lite\"")
        related_section = source[start:source.index("</section>", start)]
        self.assertIn('loading="lazy"', related_section)
        self.assertIn('fetchpriority="low"', related_section)
        self.assertNotIn("case_card_thumb_url", related_section)


if __name__ == "__main__":
    unittest.main()
