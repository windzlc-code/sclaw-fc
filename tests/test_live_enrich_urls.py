import unittest

from src.live_enrich_urls import live_enrich_eligible_url


class LiveEnrichUrlTests(unittest.TestCase):
    def test_athome_direct_kodate_detail_url_is_eligible(self):
        self.assertTrue(
            live_enrich_eligible_url(
                "https://www.athome.co.jp/kodate/6986903423/?DOWN=1&BKLISTID=001LPC&SEARCHDIV=1"
            )
        )


if __name__ == "__main__":
    unittest.main()
