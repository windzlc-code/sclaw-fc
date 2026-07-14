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

        refresh_start = runtime.index("async function refreshHomeFeaturedSpotlight")
        refresh_body = runtime[refresh_start:runtime.index("async function loadHomeFeaturedCases", refresh_start)]
        self.assertIn("await loadHomeFeaturedTypeCases({", refresh_body)
        self.assertIn("forceRefresh: true", refresh_body)

    def test_type_tabs_begin_single_payload_prewarm_on_first_load(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        start = runtime.index("function prefetchHomeFeaturedTypes")
        body = runtime[start:runtime.index("function homeFeaturedApplyType", start)]
        startup = runtime[runtime.index("function runSclawStartupChecks()"):]

        self.assertIn("/api/home-featured-type-preloads", body)
        self.assertNotIn("requestIdleCallback", body)
        self.assertIn("prefetchHomeFeaturedTypes();", startup)

    def test_home_featured_client_cache_is_bumped_after_promo_image_cleanup(self):
        index = INDEX.read_text(encoding="utf-8")
        runtime = RUNTIME.read_text(encoding="utf-8")
        self.assertIn("sclaw.homeFeatured.v28.promo-image-clean.", runtime)
        self.assertIn("gallery-v22-promo-image-clean", runtime)
        self.assertRegex(index, r"index-app\.js\?v=20260715-[A-Za-z0-9-]+")
        self.assertRegex(index, r"site\.css\?v=20260715-[A-Za-z0-9-]+")

    def test_mobile_featured_cards_keep_price_and_specs_visible_without_hover(self):
        css = (ROOT / "static" / "site.css").read_text(encoding="utf-8")
        marker = "Touch devices have no hover state"
        start = css.index(marker)
        block = css[start:css.index("body.site-home .hero-media-overlay-copy", start)]

        self.assertIn("body.site-home .home-featured-media-spec", block)
        self.assertIn("opacity: 1 !important;", block)
        self.assertIn("transform: none !important;", block)

    def test_mobile_support_panel_stays_in_widget_for_scoped_layout_rules(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        start = runtime.index("function ensureSupportChatPanelInWidget")
        block = runtime[start:runtime.index("function toggleSupportChat", start)]

        self.assertIn("widget.insertBefore(panel, widget.firstChild);", block)
        self.assertNotIn("document.body.appendChild(panel);", runtime)

    def test_mobile_support_open_state_does_not_tint_the_homepage(self):
        css = (ROOT / "static" / "site.css").read_text(encoding="utf-8")
        self.assertIn("Mobile support open state must not tint the homepage", css)
        self.assertIn("background: transparent !important;", css)

    def test_mobile_featured_cards_show_desktop_equivalent_tags_compactly(self):
        css = (ROOT / "static" / "site.css").read_text(encoding="utf-8")
        start = css.index("Mobile featured cards retain their desktop facts")
        block = css[start:css.index("@media (max-width: 640px)", start)]

        self.assertIn("body.site-home .home-featured-tags", block)
        self.assertIn("opacity: 1 !important;", block)
        self.assertIn("flex-wrap: wrap !important;", block)
        self.assertIn("overflow: visible !important;", block)
        self.assertIn("right: 8px !important;", block)
        self.assertIn("min-height: 34px !important;", block)
        self.assertIn("font-size: clamp(9.5px, 2.5vw, 10.5px) !important;", block)
        self.assertIn("font-size: clamp(6.5px, 1.8vw, 7.5px) !important;", block)

    def test_narrow_featured_cards_have_live_viewport_fallback(self):
        css = (ROOT / "static" / "site.css").read_text(encoding="utf-8")
        self.assertIn("Narrow featured cards: final fallback", css)
        self.assertIn("@media (max-width: 1080px)", css)
        self.assertIn("font-size: 10px !important;", css)
        self.assertIn("font-size: 7px !important;", css)


if __name__ == "__main__":
    unittest.main()
