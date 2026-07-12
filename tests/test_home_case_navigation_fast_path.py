from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "index-app.js").read_text(encoding="utf-8")


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

    def test_details_are_prefetched_without_changing_the_page_layout(self):
        self.assertIn("const portalCaseInlineDocumentCache = new Map()", SOURCE)
        self.assertIn("function portalCaseInlineFetchHtml", SOURCE)
        self.assertIn("function prefetchVisiblePortalCaseDetails()", SOURCE)
        self.assertIn("prefetchVisiblePortalCaseDetails();", SOURCE)

    def test_legacy_history_cannot_reopen_embedded_detail(self):
        start = SOURCE.index("function installPortalCaseInlineHistory")
        body = SOURCE[start: SOURCE.index("function portalCaseCardOpenDetail", start)]
        self.assertNotIn("addEventListener('popstate'", body)
        self.assertNotIn("openPortalCaseInlineDetail", body)


if __name__ == "__main__":
    unittest.main()
