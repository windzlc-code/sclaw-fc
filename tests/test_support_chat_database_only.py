import json
import unittest
from unittest.mock import patch

import app as app_module


class SupportChatDatabaseOnlyTests(unittest.TestCase):
    def test_completed_multi_turn_purchase_requirements_use_only_managed_cases(self):
        history = [
            {"role": "user", "content": "請幫我找東京自住房"},
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
