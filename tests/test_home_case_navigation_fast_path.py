from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "index-app.js").read_text(encoding="utf-8")


class HomeCaseNavigationFastPathTests(unittest.TestCase):
    def test_featured_cards_use_inline_detail_without_full_navigation(self):
        start = SOURCE.index("function homeFeaturedOpenCase")
        body = SOURCE[start: SOURCE.index("function homeFeaturedCacheKey", start)]
        self.assertIn("openPortalCaseInlineDetail(url, card", body)
        self.assertNotIn("navigatePortalCaseStandalone(url", body)

    def test_global_card_delegate_also_uses_inline_detail(self):
        start = SOURCE.index("function bindPortalCaseCardDetailDelegation")
        body = SOURCE[start: SOURCE.index("function seoLlmProviderPayload", start)]
        self.assertIn("openPortalCaseInlineDetail(url, card, { returnTo });", body)
        self.assertNotIn("navigatePortalCaseStandalone(url, returnTo);", body)

    def test_prefetch_and_history_share_the_same_detail_document_cache(self):
        self.assertIn("const portalCaseInlineDocumentCache = new Map()", SOURCE)
        self.assertIn("function portalCaseInlineFetchHtml", SOURCE)
        self.assertIn("window.addEventListener('popstate'", SOURCE)
        self.assertIn("function prefetchVisiblePortalCaseDetails()", SOURCE)
        self.assertIn("prefetchVisiblePortalCaseDetails();", SOURCE)


if __name__ == "__main__":
    unittest.main()
