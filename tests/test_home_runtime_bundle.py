from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "templates" / "index.html"
RUNTIME = ROOT / "static" / "index-app.js"
APP = ROOT / "app.py"


class HomeRuntimeBundleTests(unittest.TestCase):
    def test_home_runtime_is_cacheable_static_asset(self):
        source = INDEX.read_text(encoding="utf-8")
        self.assertIn('src="/static/index-app.js?v=', source)
        self.assertTrue(RUNTIME.is_file())
        self.assertIn("function runSclawStartupChecks()", RUNTIME.read_text(encoding="utf-8"))

    def test_dynamic_home_payload_stays_small_and_inline(self):
        source = INDEX.read_text(encoding="utf-8")
        self.assertIn("window.__SCLAW_INDEX_BOOTSTRAP", source)
        self.assertNotIn("function runSclawStartupChecks()", source)

    def test_below_fold_home_images_are_not_eager(self):
        source = INDEX.read_text(encoding="utf-8")
        self.assertIn('loading="{% if loop.index <= 2 %}eager{% else %}lazy{% endif %}"', source)
        self.assertNotIn('feature_img_1.jpg" alt="日本住宅外觀參考" loading="eager"', source)

    def test_generic_type_recommendations_do_not_reuse_spotlight_cards(self):
        index = INDEX.read_text(encoding="utf-8")
        runtime = RUNTIME.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")

        self.assertIn("home_featured_type_preloads = {}", app)
        self.assertIn("{% set type_ssr_items = type_ssr_raw_items[:12] %}", index)
        self.assertNotIn("else featured_ssr_items", index[index.index("type_ssr_raw_items"):index.index("home-featured-type-grid")])
        start = runtime.index("function homeFeaturedFilterTypeItems")
        body = runtime[start:runtime.index("function homeFeaturedTrustedImageUrl", start)]
        self.assertIn("return filtered;", body)
        self.assertNotIn("? filtered : list", body)


if __name__ == "__main__":
    unittest.main()
