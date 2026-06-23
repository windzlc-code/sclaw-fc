import json
import unittest

import app as app_module


class SupportChatConversationTests(unittest.TestCase):
    def test_greeting_returns_project_welcome_and_options(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="hello", sales_session_id="sess-test-welcome")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("日本不動產", data["reply"])
        self.assertIn("查日本不動產案件", data["reply"])
        self.assertIn("人工", data["reply"])
        self.assertTrue(data["llm"]["knowledge_skipped"])

    def test_small_talk_is_natural_but_guides_back_to_site_journey(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="有點無聊，陪我聊聊", sales_session_id="sess-test-smalltalk")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("日本不動產", data["reply"])
        self.assertIn("自住還是投資", data["reply"])
        self.assertNotIn("離線模式", data["reply"])
        self.assertNotIn("本次查詢結果", data["reply"])
        self.assertTrue(data["llm"]["light_chat_only"])


if __name__ == "__main__":
    unittest.main()
