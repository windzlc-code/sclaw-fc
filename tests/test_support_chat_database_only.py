import json
import unittest
from unittest.mock import patch

import app as app_module


class SupportChatDatabaseOnlyTests(unittest.TestCase):
    def test_completed_requirements_use_the_prewarmed_database_index_without_sql_fallback(self):
        indexed_rows = [
            {
                "source_item_id": 302,
                "title_zh_hant": "東京公寓 2LDK｜2,680萬日圓",
                "title_zh_hans": "东京公寓 2LDK｜2,680万日元",
                "region": "東京",
                "price_man": 2680.0,
                "item_url": "https://example.test/case/302",
            }
        ]
        with patch.object(app_module, "_support_market_price_rows", return_value=indexed_rows), patch.object(
            app_module,
            "get_conn",
            side_effect=AssertionError("completed requirements must not use the slow SQL fallback"),
        ):
            rows = app_module._support_lookup_managed_case_rows(
                message="我想買東京自住房，預算 3,000 萬日圓，公寓 2LDK",
                tx_hint="buy",
                allow_slow_fallback=False,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_item_id"], 302)
        self.assertEqual(rows[0]["price_man"], 2680.0)

    def test_partial_multi_turn_requirements_continue_with_one_question(self):
        history = [
            {"role": "user", "content": "我想買東京自住房，請幫我推薦"},
            {"role": "assistant", "content": "請問您這次主要是自住、收租，還是資產配置？"},
            {"role": "user", "content": "自住"},
        ]
        with patch.object(
            app_module,
            "chat_support_reply_gemini",
            side_effect=AssertionError("partial requirements must not call an LLM"),
        ):
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="預算 3,000 萬日圓",
                    history=history,
                    sales_session_id="sess-test-database-only-partial",
                    use_knowledge=False,
                )
            )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertTrue(data["llm"]["purchase_discovery_fast_reply"])
        self.assertFalse(data["llm"]["enabled"])
        self.assertIn("公寓", data["reply"])

    def test_completed_multi_turn_purchase_requirements_use_only_managed_cases(self):
        history = [
            {"role": "user", "content": "我想買東京自住房，請幫我推薦"},
            {"role": "assistant", "content": "請問您這次主要是自住、收租，還是資產配置？"},
            {"role": "user", "content": "自住"},
            {"role": "assistant", "content": "您方便先給一個總預算上限嗎？"},
        ]
        rows = [
            {
                "source_item_id": 301,
                "title_zh_hant": "東京公寓 2LDK｜2,980萬日圓",
                "title_zh_hans": "东京公寓 2LDK｜2,980万日元",
                "case_jp_region_override": "東京",
                "case_transit_override": "東京站步行8分",
                "item_url": "https://example.test/case/301",
            }
        ]
        with patch.object(app_module, "_support_lookup_managed_case_rows", return_value=rows), patch.object(
            app_module,
            "chat_support_reply_gemini",
            side_effect=AssertionError("final recommendations must not call an LLM"),
        ):
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="預算 3,000 萬日圓，公寓 2LDK",
                    history=history,
                    sales_session_id="sess-test-database-only-final",
                    use_knowledge=False,
                )
            )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertTrue(data["llm"]["managed_case_database_fast_reply"])
        self.assertFalse(data["llm"]["enabled"])
        self.assertEqual(data["knowledge"]["managed_case_count"], 1)
        self.assertIn("東京公寓 2LDK", data["reply"])
        self.assertIn("/case/301", data["reply"])


if __name__ == "__main__":
    unittest.main()
