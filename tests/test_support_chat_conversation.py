import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app as app_module


class SupportChatConversationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()

    def _cleanup_session(self, session_id: str):
        if not session_id:
            return
        app_module._ensure_support_channel_tables()
        with app_module.get_conn() as conn:
            app_module._ensure_support_case_interest_table(conn)
            conn.execute("DELETE FROM support_staff_inbox WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM support_session_case_interest WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM human_handoff_requests WHERE session_id = ?", (session_id,))
            conn.commit()

    def test_greeting_returns_project_welcome_and_options(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="hello", sales_session_id="sess-test-welcome")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("顧問", data["reply"])
        self.assertIn("日本房產", data["reply"])
        self.assertIn("真人顧問", data["reply"])
        self.assertTrue(data["llm"]["knowledge_skipped"])

    def test_small_talk_is_natural_but_guides_back_to_site_journey(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="有點無聊，陪我聊聊", sales_session_id="sess-test-smalltalk")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("真人顧問", data["reply"])
        self.assertTrue("東京" in data["reply"] or "大阪" in data["reply"])
        self.assertTrue("自住" in data["reply"] or "投資" in data["reply"])
        self.assertNotIn("離線模式", data["reply"])
        self.assertNotIn("本次查詢結果", data["reply"])
        self.assertTrue(data["llm"]["light_chat_only"])

    def test_purchase_discovery_only_turns_real_handoff_on_direct_buy_intent(self):
        soft = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="我想先了解日本买房流程，预算还没定", sales_session_id="sess-soft-intent")
        )
        soft_data = json.loads(soft.body)
        self.assertTrue(soft_data["ok"])
        self.assertFalse(soft_data["sales_mcp"]["real_handoff_ready"])
        self.assertFalse(soft_data["sales_mcp"]["should_notify_human"])

        direct = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="我想买房，想尽快安排顾问带看并开始对接", sales_session_id="sess-direct-intent")
        )
        direct_data = json.loads(direct.body)
        self.assertTrue(direct_data["ok"])
        self.assertTrue(direct_data["sales_mcp"]["real_handoff_ready"])
        self.assertTrue(direct_data["sales_mcp"]["should_notify_human"])

        explicit = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(
                message="我确定三个月内要在东京买自住房，请安排人工顾问接手",
                sales_session_id="sess-explicit-direct-intent",
            )
        )
        explicit_data = json.loads(explicit.body)
        self.assertTrue(explicit_data["ok"])
        self.assertTrue(explicit_data["sales_mcp"]["real_handoff_ready"])
        self.assertTrue(explicit_data["sales_mcp"]["should_notify_human"])

    def test_handoff_chain_syncs_frontend_conversation_to_admin_and_back(self):
        session_id = f"sess-test-handoff-{uuid4().hex[:10]}"
        self._cleanup_session(session_id)
        try:
            intake_payload = {
                "session_id": session_id,
                "name": "测试客户",
                "phone": "0912345678",
                "email": "buyer@example.com",
                "action_id": "clarify_purchase_plan",
                "context_message": "客户明确表示想买房，并希望有人接手。",
                "note": "请人工尽快接手",
                "conversation": [
                    {"role": "assistant", "content": "欢迎来到日本不动产智能客服。"},
                    {"role": "user", "content": "我想买房，想找顾问接手。"},
                ],
            }
            intake_resp = self.client.post("/api/support/human-intake", json=intake_payload)
            self.assertEqual(intake_resp.status_code, 200, intake_resp.text)
            intake_data = intake_resp.json()
            self.assertTrue(intake_data["ok"])
            handoff_id = int(intake_data["handoff_id"])

            sync_resp = self.client.post(
                "/api/support/handoff-conversation-sync",
                json={
                    "handoff_id": handoff_id,
                    "session_id": session_id,
                    "conversation": [
                        {"role": "assistant", "content": "欢迎来到日本不动产智能客服。"},
                        {"role": "user", "content": "我想买房，想找顾问接手。"},
                        {"role": "assistant", "content": "好的，我先帮您整理需求。"},
                        {"role": "user", "content": "希望东京 2LDK，近期看房。"},
                    ],
                },
            )
            self.assertEqual(sync_resp.status_code, 200, sync_resp.text)
            self.assertTrue(sync_resp.json()["updated"])

            detail_resp = self.client.get(
                f"/api/admin/handoff-requests/{handoff_id}",
                headers={"x-admin-password": app_module.ADMIN_PANEL_PASSWORD},
            )
            self.assertEqual(detail_resp.status_code, 200, detail_resp.text)
            detail_data = detail_resp.json()
            self.assertTrue(detail_data["ok"])
            item = detail_data["item"]
            self.assertEqual(item["session_id"], session_id)
            self.assertGreaterEqual(len(item["conversation"]), 4)
            self.assertIn("东京 2LDK", json.dumps(item["conversation"], ensure_ascii=False))

            reply_resp = self.client.post(
                f"/api/admin/handoff-requests/{handoff_id}/reply",
                headers={"x-admin-password": app_module.ADMIN_PANEL_PASSWORD},
                json={
                    "message": "您好，这里是人工顾问小美，已经收到您的需求，我先帮您整理东京 2LDK 看房方向。",
                    "channel": "web",
                    "sender_name": "工号1001小美",
                },
            )
            self.assertEqual(reply_resp.status_code, 200, reply_resp.text)
            reply_data = reply_resp.json()
            self.assertTrue(reply_data["ok"])
            self.assertEqual(reply_data["reply"]["channel_used"], "web")

            inbox_resp = self.client.get(
                "/api/support/telegram-inbox",
                params={"session_id": session_id, "after_id": 0},
            )
            self.assertEqual(inbox_resp.status_code, 200, inbox_resp.text)
            inbox_data = inbox_resp.json()
            self.assertTrue(inbox_data["ok"])
            bodies = [str(row.get("body") or "") for row in inbox_data["items"]]
            self.assertTrue(any("人工顾问小美" in body or "人工顧問小美" in body or "东京 2LDK" in body for body in bodies))
        finally:
            self._cleanup_session(session_id)


if __name__ == "__main__":
    unittest.main()
