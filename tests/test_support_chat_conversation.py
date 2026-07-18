import json
import unittest
from uuid import uuid4
from unittest.mock import patch

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

    def test_bootstrap_welcome_has_no_simulated_service(self):
        resp = self.client.get("/api/ai/chat-support/bootstrap", params={"session_id": "sess-test-bootstrap"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()

        self.assertTrue(data["ok"])
        self.assertIn("日本不動產線上客服", data["reply"])
        self.assertIn("日本房產", data["reply"])
        self.assertNotIn("工單", data["reply"])
        self.assertNotIn("排隊", data["reply"])
        self.assertNotIn("接待等待", data["reply"])
        self.assertNotIn("simulated_service", data["sales_mcp"])

    def test_greeting_returns_project_welcome_and_options(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="hello", sales_session_id="sess-test-welcome")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("線上客服", data["reply"])
        self.assertIn("日本不動產", data["reply"])
        self.assertNotIn("顧問接待", data["reply"])
        self.assertTrue(data["llm"]["knowledge_skipped"])

    def test_identity_question_is_transparent_and_does_not_fabricate_advisor(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="你是真人嗎", sales_session_id="sess-test-identity")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("網站線上客服", data["reply"])
        self.assertIn("不是人工顧問", data["reply"])
        self.assertNotIn("工號", data["reply"])
        self.assertNotIn("小美", data["reply"])
        self.assertTrue(data["llm"]["light_chat_only"])
        self.assertNotIn("simulated_service", data["sales_mcp"])

    def test_simplified_identity_question_uses_same_transparent_identity(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="你是谁", sales_session_id="sess-test-identity-simplified")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("網站線上客服", data["reply"])
        self.assertIn("不是人工顧問", data["reply"])
        self.assertNotIn("工號", data["reply"])
        self.assertNotIn("simulated_service", data["sales_mcp"])

    def test_daily_small_talk_receives_a_natural_reply_without_forcing_purchase_inputs(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(message="吃飯了嗎？", sales_session_id="sess-test-smalltalk")
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("不需要吃飯", data["reply"])
        self.assertNotIn("地區、用途或預算", data["reply"])
        self.assertNotIn("離線模式", data["reply"])
        self.assertNotIn("本次查詢結果", data["reply"])
        self.assertTrue(data["llm"]["light_chat_only"])
        self.assertTrue(data["llm"]["daily_chat"])

    def test_purchase_follow_up_questions_include_usable_examples(self):
        questions = {
            "用途（自住／收租／資產配置）": "例如：自住、收租或資產配置",
            "總預算帶": "例如：3,000 萬日圓以內",
            "偏好地區或車站": "例如：東京港區、東京 23 區或 JR 山手線沿線",
            "物件類型": "例如：公寓、一戶建或不限類型",
            "格局／房數": "例如：2LDK、3 房以上或 70㎡以上",
        }
        for field, example in questions.items():
            with self.subTest(field=field):
                reply = app_module._support_single_followup_question(
                    "我想買日本房",
                    missing_fields=[field],
                )
                self.assertIn(example, reply)

    def test_market_price_uses_non_contact_intake_form_answers_for_the_next_question(self):
        rows = [
            {"region": "青森", "price_man": 900.0, "source_item_id": 101},
            {"region": "青森", "price_man": 988.0, "source_item_id": 102},
            {"region": "青森", "price_man": 1200.0, "source_item_id": 103},
        ]
        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module,
            "chat_support_reply_gemini",
            return_value="青森約 988 萬日圓。請再補條件。",
        ):
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪個地區房價最便宜？",
                    history=[{"role": "user", "content": "我想自住買日本房"}],
                    intake_summary={
                        "purchase_purpose": "自用（本人、家人或子女居住 / 自用辦公室）",
                        "budget_total_yen": "3,000 萬日圓以內",
                        "property_type": "公寓",
                        "target_city": "東京",
                        "contact_phone": "should-not-be-kept",
                    },
                    sales_session_id="sess-test-intake-summary-market",
                )
            )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertIn("東京港區、東京 23 區或 JR 山手線沿線", data["reply"])
        self.assertNotIn("總預算上限", data["reply"])
        self.assertEqual(data["knowledge"]["intake_summary"]["target_city"], "東京")
        self.assertNotIn("contact_phone", data["knowledge"]["intake_summary"])

    def test_keyword_preset_follow_ups_include_usable_examples(self):
        cases = {
            "日本買房流程怎麼開始？": ("buying_flow", "例如：自住、收租或資產配置"),
            "日本房貸和稅費怎麼算？": ("cost_loan", "例如：總預算 3,000 萬日圓"),
            "日本買房每年的持有成本大概怎麼算？": ("cost_loan", "例如：總預算 3,000 萬日圓"),
        }
        for message, (kind, example) in cases.items():
            with self.subTest(message=message):
                preset = app_module._support_keyword_preset_reply(
                    message,
                    session_id="sess-test-keyword-example",
                )
                self.assertIsNotNone(preset)
                self.assertEqual(preset["llm"]["keyword_preset_kind"], kind)
                self.assertIn(example, preset["reply"])

    def test_natural_purchase_language_and_region_typo_use_fast_discovery(self):
        with patch.object(
            app_module,
            "chat_support_reply_gemini",
            return_value="收到，我先按大阪理解；接著想確認用途，例如自住、收租或資產配置？",
        ):
            for message in ("我需要大阪的房子", "我需要大板的房子"):
                with self.subTest(message=message):
                    resp = app_module.api_ai_chat_support(
                        app_module.ChatSupportRequest(message=message, sales_session_id="sess-test-natural-purchase")
                    )
                    data = json.loads(resp.body)

                    self.assertTrue(data["llm"]["purchase_discovery_fast_reply"])
                    self.assertTrue(data["knowledge"]["purchase_discovery"]["dimensions"]["region"])
                    self.assertNotIn("東京港區", data["reply"])

            typo = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(message="我需要大板的房子", sales_session_id="sess-test-typo-note")
            )
        typo_data = json.loads(typo.body)
        self.assertIn("大阪", typo_data["reply"])
        self.assertIn({"from": "大板", "to": "大阪"}, typo_data["knowledge"]["input_corrections"])

    def test_normal_language_is_not_silently_converted_to_a_region(self):
        normalized, corrections = app_module._support_normalize_purchase_context(
            "日本買房以後每年的持有成本大概怎麼計算？"
        )
        self.assertIn("大概", normalized)
        self.assertNotIn({"from": "大概", "to": "大阪"}, corrections)
        self.assertTrue(app_module._support_message_is_guidance_question(normalized))

    def test_chat_uses_next_configured_provider_after_primary_timeout(self):
        def credentials(provider):
            return ("https://llm.example.test", "test-key", f"{provider}-model")

        with patch.object(app_module, "resolve_llm_provider", return_value="deepseek"), patch.object(
            app_module, "is_llm_configured", side_effect=lambda provider=None: str(provider or "deepseek") in {"deepseek", "gemini"}
        ), patch.object(app_module, "get_chat_credentials", side_effect=credentials), patch.object(
            app_module,
            "chat_support_reply_gemini",
            side_effect=[app_module.httpx.ReadTimeout("primary timed out"), "Gemini 已依您的問題整理重點。"],
        ) as mocked_llm:
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="日本房產未來三年的市場趨勢怎麼看？",
                    use_knowledge=False,
                    sales_session_id="sess-test-provider-fallback",
                )
            )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertEqual(mocked_llm.call_count, 2)
        self.assertEqual(mocked_llm.call_args_list[0].kwargs["provider"], "deepseek")
        self.assertEqual(mocked_llm.call_args_list[1].kwargs["provider"], "gemini")
        self.assertTrue(data["llm"]["provider_retry_succeeded"])
        self.assertIn("Gemini", data["reply"])

    def test_normalized_region_does_not_repeat_the_region_question(self):
        history = [
            {"role": "user", "content": "我想買日本房"},
            {"role": "assistant", "content": "請問您這次主要是自住、收租，還是資產配置？"},
            {"role": "user", "content": "自住"},
            {"role": "assistant", "content": "您方便先給一個總預算上限嗎？"},
            {"role": "user", "content": "預算 3,000 萬日圓"},
        ]
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(
                message="我需要大板的房子",
                history=history,
                sales_session_id="sess-test-typo-no-repeat",
            )
        )
        data = json.loads(resp.body)

        self.assertTrue(data["knowledge"]["purchase_discovery"]["dimensions"]["region"])
        self.assertIn("公寓、一戶建", data["reply"])
        self.assertNotIn("東京港區", data["reply"])

    def test_purchase_discovery_only_turns_real_handoff_on_direct_buy_intent(self):
        self.assertFalse(app_module._support_message_is_guidance_question("東京 5000萬 自住 公寓"))
        self.assertTrue(app_module._support_message_is_guidance_question("日本買房流程怎麼開始"))
        self.assertFalse(
            app_module._support_should_enter_purchase_discovery(
                "日本買房流程怎麼開始",
                raw_user_message="日本買房流程怎麼開始",
            )
        )
        self.assertFalse(app_module._support_has_direct_buying_commitment("我想買日本房"))
        self.assertTrue(app_module._support_has_direct_buying_commitment("我準備三個月內在東京買房，想安排看屋"))

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

    def test_human_keywords_open_lead_capture_even_when_ai_is_enabled(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(
                message="我想留单，请联系真人顾问",
                sales_session_id="sess-human-keyword-lead-form",
            )
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        sales = data["sales_mcp"]
        self.assertTrue(sales["human_handoff_intent"])
        self.assertTrue(sales["real_handoff_ready"])
        self.assertTrue(sales["should_notify_human"])
        self.assertTrue(sales["lead_capture"]["ready"])
        action_ids = [str(x.get("id") or "") for x in sales["next_actions"]]
        self.assertIn("handoff_human", action_ids)
        self.assertIn("填寫需求表", data["reply"])
        self.assertNotIn("工號", data["reply"])
        self.assertTrue(data["llm"].get("keyword_preset"))
        self.assertEqual(data["llm"].get("keyword_preset_kind"), "human_handoff")

    def test_human_advisor_keyword_uses_fast_preset_without_llm_or_knowledge(self):
        resp = app_module.api_ai_chat_support(
            app_module.ChatSupportRequest(
                message="我想聯絡顧問，請先告訴我怎麼處理",
                sales_session_id="sess-human-keyword-fast-preset",
            )
        )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertTrue(data["llm"]["keyword_preset"])
        self.assertEqual(data["llm"]["keyword_preset_kind"], "human_handoff")
        self.assertFalse(data["llm"]["enabled"])
        self.assertTrue(data["llm"]["knowledge_skipped"])
        self.assertTrue(data["knowledge"]["skipped_lookup"])
        self.assertTrue(data["sales_mcp"]["human_handoff_intent"])
        self.assertIn("填寫需求表", data["reply"])
        self.assertIn("實際顧問", data["reply"])
        self.assertNotIn("工號", data["reply"])

    def test_buying_flow_keyword_uses_model_with_grounded_flow_context(self):
        with patch.object(app_module, "chat_support_reply_gemini", return_value="可以先確認用途與總預算，再依站內案件縮小範圍。您是自住還是收租？") as mocked_llm:
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="我想先了解日本买房流程",
                    sales_session_id="sess-buying-flow-model-first",
                )
            )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertTrue(data["llm"]["keyword_preset"])
        self.assertEqual(data["llm"]["keyword_preset_kind"], "buying_flow")
        self.assertTrue(data["llm"]["enabled"])
        self.assertTrue(data["llm"]["purchase_model_reply"])
        self.assertTrue(mocked_llm.called)
        self.assertTrue(data["knowledge"]["skipped_lookup"])
        self.assertIn("總預算", data["reply"])

    def test_broad_price_question_uses_model_with_grounded_market_stats(self):
        """Market answers are model-written but can only use comparable records."""
        self.assertTrue(app_module._support_is_market_price_question("哪個地區房價最便宜？"))
        self.assertTrue(app_module._support_is_market_price_question("哪個地區房價最便宜？有沒有什麼推薦？"))
        self.assertFalse(app_module._support_is_market_price_question("這筆案件價格多少？"))
        rows = [
            {"region": "福岡", "price_man": 1200.0, "source_item_id": 101},
            {"region": "福岡", "price_man": 1300.0, "source_item_id": 102},
            {"region": "福岡", "price_man": 1400.0, "source_item_id": 103},
            {"region": "東京", "price_man": 5000.0, "source_item_id": 201},
            {"region": "東京", "price_man": 5400.0, "source_item_id": 202},
            {"region": "東京", "price_man": 5600.0, "source_item_id": 203},
        ]
        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module, "chat_support_reply_gemini", return_value="依站內可比案件，福岡的中位總價較低；若您提供預算，我再幫您找實際案件。"
        ) as mocked_llm:
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪個地區房價最便宜？",
                    sales_session_id="sess-test-grounded-market-price",
                )
            )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertTrue(data["llm"]["market_data_ai_reply"])
        self.assertTrue(data["llm"]["enabled"])
        self.assertTrue(mocked_llm.called)
        self.assertTrue(data["knowledge"]["skipped_lookup"])
        self.assertIn("福岡", data["reply"])
        self.assertIn("總價中位數", data["reply"])
        self.assertIn("福岡", mocked_llm.call_args.kwargs["knowledge_text"])
        self.assertIn("1,300", mocked_llm.call_args.kwargs["knowledge_text"])

    def test_market_price_prefers_form_city_over_the_global_lowest_region(self):
        rows = [
            {"region": "青森", "price_man": 900.0, "source_item_id": 101},
            {"region": "青森", "price_man": 988.0, "source_item_id": 102},
            {"region": "青森", "price_man": 1200.0, "source_item_id": 103},
            {"region": "東京", "price_man": 5000.0, "source_item_id": 201},
            {"region": "東京", "price_man": 5400.0, "source_item_id": 202},
            {"region": "東京", "price_man": 5600.0, "source_item_id": 203},
        ]
        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module,
            "chat_support_reply_gemini",
            return_value="東京約 5,400 萬日圓。請再補條件。",
        ):
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪個地區房價最便宜？有什麼推薦？",
                    intake_summary={"target_city": "東京", "budget_total_yen": "6,000 萬日圓以內"},
                    sales_session_id="sess-test-market-form-city-first",
                )
            )
        data = json.loads(resp.body)

        self.assertEqual(data["knowledge"]["market_data"]["focus_region"], "東京")
        self.assertEqual(data["knowledge"]["market_data"]["regions"][0]["region"], "東京")
        self.assertIn("東京", data["reply"])
        self.assertNotIn("青森", data["reply"])

    def test_unscoped_market_price_keeps_a_short_real_region_comparison(self):
        rows = [
            {"region": "青森", "price_man": 900.0, "source_item_id": 101},
            {"region": "青森", "price_man": 988.0, "source_item_id": 102},
            {"region": "青森", "price_man": 1200.0, "source_item_id": 103},
            {"region": "福岡", "price_man": 1200.0, "source_item_id": 201},
            {"region": "福岡", "price_man": 1300.0, "source_item_id": 202},
            {"region": "福岡", "price_man": 1400.0, "source_item_id": 203},
            {"region": "香川", "price_man": 1500.0, "source_item_id": 301},
            {"region": "香川", "price_man": 1600.0, "source_item_id": 302},
            {"region": "香川", "price_man": 1700.0, "source_item_id": 303},
        ]
        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module,
            "chat_support_reply_gemini",
            return_value="青森約 988 萬日圓。請再補條件。",
        ):
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(message="哪個地區房價最便宜？", sales_session_id="sess-test-market-compare-set")
            )
        data = json.loads(resp.body)

        self.assertIn("青森", data["reply"])
        self.assertIn("福岡", data["reply"])
        self.assertIn("香川", data["reply"])

    def test_price_recommendation_question_uses_model_with_database_grounding(self):
        """Price-led recommendations must pass database facts into the model."""
        managed_rows = [
            {
                "source_item_id": 301,
                "title_zh_hant": "福岡市中央區公寓",
                "title_zh_hans": "福冈市中央区公寓",
                "price_text_hant": "1,280萬日圓",
                "layout_text_hant": "1LDK",
                "building_area": "38.2㎡",
                "case_jp_region_override": "福岡",
                "item_url": "https://example.test/case/301",
            }
        ]
        market_rows = [
            {"region": "福岡", "price_man": 1200.0, "source_item_id": 101, "title_zh_hant": "福岡低總價公寓A"},
            {"region": "福岡", "price_man": 1280.0, "source_item_id": 102, "title_zh_hant": "福岡低總價公寓B"},
            {"region": "福岡", "price_man": 1400.0, "source_item_id": 103, "title_zh_hant": "福岡低總價公寓C"},
            {"region": "東京", "price_man": 5400.0, "source_item_id": 201, "title_zh_hant": "東京公寓A"},
            {"region": "東京", "price_man": 5600.0, "source_item_id": 202, "title_zh_hant": "東京公寓B"},
            {"region": "東京", "price_man": 5800.0, "source_item_id": 203, "title_zh_hant": "東京公寓C"},
        ]
        with patch.object(app_module, "_support_lookup_managed_case_rows", return_value=managed_rows), patch.object(
            app_module, "is_llm_configured", return_value=True
        ), patch.object(
            app_module, "_support_market_price_rows", return_value=market_rows
        ), patch.object(
            app_module,
            "chat_support_reply_gemini",
            return_value="依站內可比案件，福岡的總價中位數較低；若您要，我可依預算繼續篩選。",
        ) as mocked_llm:
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪個地區房價最便宜？有沒有什麼推薦？",
                    sales_session_id="sess-test-price-recommendation-ai",
                    use_knowledge=False,
                )
            )
        data = json.loads(resp.body)

        self.assertTrue(data["ok"])
        self.assertTrue(mocked_llm.called)
        self.assertTrue(data["llm"]["market_data_ai_reply"])
        self.assertTrue(data["llm"]["enabled"])
        self.assertTrue(data["knowledge"]["skipped_lookup"])
        self.assertEqual(data["knowledge"]["market_data"]["regions"][0]["region"], "福岡")
        self.assertIn("福岡", data["reply"])
        self.assertIn("福岡", mocked_llm.call_args.kwargs["knowledge_text"])
        self.assertIn("1,280", mocked_llm.call_args.kwargs["knowledge_text"])

    def test_market_price_uses_real_database_fallback_only_after_all_models_fail(self):
        rows = [
            {"region": "福岡", "price_man": 1200.0, "source_item_id": 101},
            {"region": "福岡", "price_man": 1300.0, "source_item_id": 102},
            {"region": "福岡", "price_man": 1400.0, "source_item_id": 103},
        ]

        def credentials(provider):
            return ("https://llm.example.test", "test-key", f"{provider}-model")

        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module, "resolve_llm_provider", return_value="deepseek"
        ), patch.object(
            app_module, "is_llm_configured", side_effect=lambda provider=None: str(provider or "deepseek") in {"deepseek", "gemini"}
        ), patch.object(app_module, "get_chat_credentials", side_effect=credentials), patch.object(
            app_module,
            "chat_support_reply_gemini",
            side_effect=[app_module.httpx.ReadTimeout("primary timeout"), app_module.httpx.ReadTimeout("fallback timeout")],
        ) as mocked_llm:
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪個地區房價最便宜？",
                    sales_session_id="sess-test-market-all-provider-fallback",
                )
            )
        data = json.loads(resp.body)

        self.assertEqual(mocked_llm.call_count, 2)
        self.assertFalse(data["llm"]["enabled"])
        self.assertTrue(data["llm"]["all_providers_failed"])
        self.assertFalse(data["llm"]["market_data_ai_reply"])
        self.assertIn("福岡", data["reply"])
        self.assertIn("站內目前可比的在售資料", data["reply"])

    def test_market_price_reply_is_direct_and_keeps_one_guided_follow_up(self):
        rows = [
            {"region": "青森", "price_man": 900.0, "source_item_id": 101},
            {"region": "青森", "price_man": 988.0, "source_item_id": 102},
            {"region": "青森", "price_man": 1200.0, "source_item_id": 103},
            {"region": "香川", "price_man": 1400.0, "source_item_id": 201},
            {"region": "香川", "price_man": 1450.0, "source_item_id": 202},
            {"region": "香川", "price_man": 1500.0, "source_item_id": 203},
        ]
        verbose_reply = (
            "您好呀！想在日本找预算比较亲民的物件，确实可以先从一些地方开始比较。"
            "根据我们网站目前在售的物件数据，青森和香川都有较低总价的选择，青森的中位数约 988 万日圆。"
            "不过买日本房子除了总价，还要注意管理费、修缮积立金和固定资产税。"
            "为了帮您更精准筛选，想先请教您这间房子是自己住还是出租收租呢？"
            "提醒：实际仍以契约、法規与官方公告为准。"
        )
        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module, "chat_support_reply_gemini", return_value=verbose_reply
        ) as mocked_llm:
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪里房子比较便宜？推荐一下。",
                    sales_session_id="sess-test-market-direct-guidance",
                )
            )
        data = json.loads(resp.body)

        self.assertLessEqual(len(data["reply"]), 180)
        self.assertIn("青森", data["reply"])
        self.assertIn("香川", data["reply"])
        self.assertIn("自住", data["reply"])
        self.assertIn("收租", data["reply"])
        self.assertEqual(mocked_llm.call_args.kwargs["max_tokens"], 220)
        self.assertIn("嚴格短回合", mocked_llm.call_args.kwargs["scenario_coaching"])

    def test_market_price_uses_next_missing_intake_field_for_follow_up(self):
        rows = [
            {"region": "青森", "price_man": 900.0, "source_item_id": 101},
            {"region": "青森", "price_man": 988.0, "source_item_id": 102},
            {"region": "青森", "price_man": 1200.0, "source_item_id": 103},
            {"region": "香川", "price_man": 1400.0, "source_item_id": 201},
            {"region": "香川", "price_man": 1450.0, "source_item_id": 202},
            {"region": "香川", "price_man": 1500.0, "source_item_id": 203},
        ]
        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module,
            "chat_support_reply_gemini",
            return_value="青森約 988 萬日圓。香川也不錯，還要看管理費。您自住還是收租？",
        ) as mocked_llm:
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪個地區房子最便宜？有什麼推薦？",
                    history=[{"role": "user", "content": "我想自住買日本房"}],
                    sales_session_id="sess-test-market-intake-order",
                )
            )
        data = json.loads(resp.body)

        self.assertIn("青森", data["reply"])
        self.assertIn("總預算", data["reply"])
        self.assertIn("3,000 萬日圓", data["reply"])
        self.assertIn("用途與預算", data["reply"])
        self.assertIn("需求表欄位", mocked_llm.call_args.kwargs["knowledge_text"])
        self.assertIn("總預算", mocked_llm.call_args.kwargs["scenario_coaching"])

    def test_market_price_fills_the_intake_explanation_when_model_returns_only_two_sentences(self):
        rows = [
            {"region": "青森", "price_man": 900.0, "source_item_id": 101},
            {"region": "青森", "price_man": 988.0, "source_item_id": 102},
            {"region": "青森", "price_man": 1200.0, "source_item_id": 103},
        ]
        with patch.object(app_module, "_support_market_price_rows", return_value=rows), patch.object(
            app_module,
            "chat_support_reply_gemini",
            return_value="青森的總價中位數約 988 萬日圓。您方便先給一個總預算上限嗎？",
        ):
            resp = app_module.api_ai_chat_support(
                app_module.ChatSupportRequest(
                    message="哪個地區房子最便宜？",
                    history=[{"role": "user", "content": "我想自住買日本房"}],
                    sales_session_id="sess-test-market-three-sentence-guard",
                )
            )
        data = json.loads(resp.body)

        self.assertGreaterEqual(len([x for x in data["reply"].splitlines() if x.strip()]), 3)
        self.assertIn("用途與預算", data["reply"])
        self.assertIn("例如：3,000 萬日圓", data["reply"])
        self.assertTrue(data["llm"]["model_response_guarded"])

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
                    {"role": "assistant", "content": "欢迎来到日本不动产线上客服。"},
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
                        {"role": "assistant", "content": "欢迎来到日本不动产线上客服。"},
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
                    "message": "您好，顧問團隊已收到您的需求，我先幫您整理東京 2LDK 看房方向。",
                    "channel": "web",
                    "sender_name": "顧問團隊",
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
            self.assertTrue(any("顧問團隊" in body or "東京 2LDK" in body for body in bodies))
        finally:
            self._cleanup_session(session_id)


if __name__ == "__main__":
    unittest.main()
