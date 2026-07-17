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
        self.assertRegex(index, r"index-app\.js\?v=20260717-[A-Za-z0-9-]+")
        self.assertRegex(index, r"site\.css\?v=20260717-[A-Za-z0-9-]+")

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

    def test_support_chat_does_not_abort_real_data_or_truncate_its_reply(self):
        runtime = RUNTIME.read_text(encoding="utf-8")
        fallback = (ROOT / "templates" / "partials" / "support_chat_widget.html").read_text(encoding="utf-8")

        self.assertIn("const SUPPORT_CHAT_REQUEST_TIMEOUT_MS = 35000;", runtime)
        self.assertIn("window.setTimeout(() => supportRequestController.abort(), SUPPORT_CHAT_REQUEST_TIMEOUT_MS)", runtime)
        self.assertNotIn("const limit = 118;", runtime)
        self.assertNotIn("compactSupportChatReplyText", runtime)
        self.assertNotIn("supportFallbackCompactReply", fallback)
        self.assertNotIn("compact.slice(0, 118)", fallback)

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
        self.assertIn("#home-featured-type-grid .home-featured-card h3", css)
        self.assertIn("font-size: 11px !important;", css)
        self.assertIn("font-size: 7px !important;", css)
        self.assertIn("min-height: 14px !important;", css)
        self.assertIn("bottom: 22px !important;", css)
        self.assertIn("flex-wrap: nowrap !important;", css)

    def test_mobile_hero_keeps_centered_bilingual_title_and_compact_video_overlay(self):
        css = (ROOT / "static" / "site.css").read_text(encoding="utf-8")
        start = css.index("mobile hero keeps the desktop title hierarchy")
        block = css[start:]

        self.assertIn("display: block !important;", block)
        self.assertIn("text-align: center !important;", block)
        self.assertIn("height: clamp(54px, 9svh, 68px) !important;", block)
        self.assertNotIn("background: linear-gradient", block)
        self.assertIn("font-size: clamp(23px, 6.3vw, 26px) !important;", block)
        self.assertIn("line-height: 0.98 !important;", block)
        self.assertIn("gap: 4px !important;", block)
        self.assertIn("height: 28px !important;", block)
        self.assertIn("max-height: 28px !important;", block)
        self.assertIn("min-height: 38px !important;", block)
        self.assertIn("min-height: 26px !important;", block)
        self.assertIn("grid-template-areas: \"brand search actions\" !important;", block)
        self.assertIn("grid-template-columns: 150px minmax(0, 1fr) 28px !important;", block)
        self.assertIn("transform: scale(0.62) !important;", block)
        self.assertIn("height: 18px !important;", block)
        self.assertIn("font-size: 8.5px !important;", block)
        self.assertIn("min-width: 12px !important;", block)
        self.assertIn("display: flex !important;", block)
        self.assertIn("flex: 1 1 auto !important;", block)
        self.assertIn("flex: 0 0 36px !important;", block)
        self.assertIn("width: min(344px, calc(100vw - 40px)) !important;", block)
        self.assertIn("left: -155px !important;", block)
        self.assertIn("body.site-home .bh-title-search-keywords {", block)
        self.assertIn("body.site-home .bh-title-search-keyword-group {", block)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr)) !important;", block)
        self.assertIn("a:nth-of-type(n + 6)", block)
        self.assertIn("display: block !important;", block)

    def test_mobile_header_search_results_expand_beyond_the_compact_input(self):
        header = (ROOT / "templates" / "partials" / "site_header.html").read_text(encoding="utf-8")

        self.assertIn("form.classList.toggle('has-query', Boolean(q));", header)
        self.assertIn("form.classList.remove('is-open', 'is-loading', 'has-query');", header)

    def test_mobile_keyword_dropdown_matches_compact_header_scale(self):
        css = (ROOT / "static" / "site.css").read_text(encoding="utf-8")
        start = css.index("Keep the keyword navigation dropdown proportional")
        block = css[start:]

        self.assertIn("width: 184px !important;", block)
        self.assertIn("max-height: 272px !important;", block)
        self.assertIn("min-height: 24px !important;", block)
        self.assertIn("font-size: 11px !important;", block)


if __name__ == "__main__":
    unittest.main()
