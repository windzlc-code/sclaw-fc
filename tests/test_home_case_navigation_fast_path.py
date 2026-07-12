from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "index-app.js").read_text(encoding="utf-8")
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
PROD_COMPOSE = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")


class HomeCaseNavigationFastPathTests(unittest.TestCase):
    def test_featured_cards_keep_the_standalone_detail_layout(self):
        start = SOURCE.index("function homeFeaturedOpenCase")
        body = SOURCE[start: SOURCE.index("function homeFeaturedCacheKey", start)]
        self.assertIn("navigatePortalCaseStandalone(url, '/#home-featured-cases');", body)
        self.assertNotIn("openPortalCaseInlineDetail(url, card", body)

    def test_global_card_delegate_keeps_the_standalone_detail_layout(self):
        start = SOURCE.index("function bindPortalCaseCardDetailDelegation")
        body = SOURCE[start: SOURCE.index("function seoLlmProviderPayload", start)]
        self.assertIn("navigatePortalCaseStandalone(url, returnTo);", body)
        self.assertNotIn("openPortalCaseInlineDetail(url, card, { returnTo });", body)

    def test_details_are_prefetched_on_intent_without_changing_the_page_layout(self):
        self.assertIn("const portalCaseInlineDocumentCache = new Map()", SOURCE)
        self.assertIn("function portalCaseInlineFetchHtml", SOURCE)
        self.assertIn("function prefetchPortalCaseStandalone", SOURCE)
        self.assertIn("prefetchFromEvent", SOURCE)

    def test_legacy_history_cannot_reopen_embedded_detail(self):
        start = SOURCE.index("function installPortalCaseInlineHistory")
        body = SOURCE[start: SOURCE.index("function portalCaseCardOpenDetail", start)]
        self.assertNotIn("addEventListener('popstate'", body)
        self.assertNotIn("openPortalCaseInlineDetail", body)

    def test_startup_does_not_queue_speculative_case_pages_or_all_tabs(self):
        start = SOURCE.index("function runSclawStartupChecks")
        body = SOURCE[start: SOURCE.index("if (document.readyState === 'loading')", start)]
        self.assertNotIn("prefetchHomeFeaturedTypes();", body)
        self.assertNotIn("prefetchVisiblePortalCaseDetails();", body)
        self.assertIn("their own detail on hover, touch, or keyboard focus", body)

    def test_concurrent_case_requests_share_one_cold_render(self):
        self.assertIn("def _serialize_case_page_render", APP_SOURCE)
        self.assertIn("@_serialize_case_page_render\ndef source_case_page", APP_SOURCE)
        self.assertIn("def _case_page_html_disk_cache_path", APP_SOURCE)
        self.assertIn("_CASE_PAGE_HTML_DISK_CACHE_DIR", APP_SOURCE)

    def test_production_runs_configured_uvicorn_workers(self):
        self.assertIn('--workers \\"${WEB_CONCURRENCY:-1}\\"', DOCKERFILE)
        self.assertIn('WEB_CONCURRENCY=${WEB_CONCURRENCY:-1}', PROD_COMPOSE)

    def test_homepage_cache_serves_stale_html_while_refreshing(self):
        self.assertIn("def _refresh_index_html_cache_in_background", APP_SOURCE)
        self.assertIn('"X-SCLAW-Index-Cache": "stale"', APP_SOURCE)
        self.assertIn('SCLAW_INDEX_HTML_CACHE_TTL=${SCLAW_INDEX_HTML_CACHE_TTL:-86400}', PROD_COMPOSE)


if __name__ == "__main__":
    unittest.main()
