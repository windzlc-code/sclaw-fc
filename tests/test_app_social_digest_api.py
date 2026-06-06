import json
import unittest
from unittest.mock import patch

import app as app_module


class AppSocialDigestApiTests(unittest.TestCase):
    def test_social_digest_endpoint_returns_cards_and_bootstrap_queries(self):
        rows = [
            {
                "content_id": 10,
                "source_name": "TikTok",
                "item_url": "https://vt.tiktok.com/ZSxcW2GMp/",
                "content_kind": "social_video_knowledge",
                "title_zh_hant": "日本買房影片：外國人房貸",
                "body_zh_hant_excerpt": "影片文案重點：先確認用途，再看外國人房貸、稅金與持有成本。",
                "topic_category": "日本購房知識",
                "intent_target": "房貸",
                "crawled_at": "2026-06-02 09:00:00",
            },
            {
                "content_id": 11,
                "source_name": "SUUMO",
                "item_url": "https://suumo.jp/chukoikkodate/tokyo/sc_example/nc11/",
                "content_kind": "jp_listing",
                "title_zh_hant": "東京中古一戶建",
                "body_zh_hant_excerpt": "物件資料：價格、交通與屋齡需交叉確認。",
                "topic_category": "日本房產案源",
                "intent_target": "物件比較",
                "crawled_at": "2026-06-02 10:00:00",
            },
        ]

        with patch.object(app_module, "fetch_knowledge_snippets", return_value=rows) as mocked:
            resp = app_module.api_knowledge_social_digest(
                q="日本買房 tk 小紅書",
                days=15,
                limit=12,
                sort_by="crawled_desc",
                zh_variant="hant",
            )

        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertTrue(mocked.called)
        self.assertEqual(data["digest"]["window_days"], 15)
        self.assertEqual(data["digest"]["items"][0]["reading_card"]["logo"], "TK")
        self.assertEqual(data["digest"]["items"][0]["reading_card"]["mode"], "視頻文案播放")
        self.assertEqual(data["digest"]["items"][1]["property_tag"], "物件")
        self.assertTrue(data["digest"]["needs_bootstrap"])
        self.assertIn("日本買房 流程 社媒", data["digest"]["query_suggestions"])

    def test_dialog_run_keeps_social_knowledge_when_case_items_are_present(self):
        social_row = {
            "content_id": 20,
            "source_name": "TikTok",
            "item_url": "https://www.tiktok.com/@jp.home/video/20",
            "content_kind": "social_video_knowledge",
            "title_zh_hant": "日本買房影片：持有成本",
            "body_zh_hant_excerpt": "影片文案重點：買房前先確認用途、貸款與持有成本。",
            "topic_category": "日本購房知識",
            "intent_target": "購房流程",
        }
        case_item = {
            "id": 97558,
            "source_item_id": 97558,
            "source_name": "HOME'S",
            "title_zh_hant": "福岡 4LDK 物件",
            "price_text_hant": "2,680萬日圓",
            "layout_text_hant": "4房 + 客餐廚",
            "article_url": "/case/97558",
            "image_urls": "https://cdn.example.test/case.jpg",
        }

        with patch.object(app_module, "fetch_knowledge_snippets", return_value=[social_row]) as kb_mock, patch.object(
            app_module,
            "run_dialog_ai_summary",
            return_value={"title": "智慧查詢", "bullets": ["先讀社媒重點，再看物件。"], "links": [], "voice_script": ""},
        ):
            resp = app_module.api_ai_dialog_run(
                app_module.DialogRunRequest(q="日本買房 外國人房貸", knowledge_zh_variant="hant", case_items=[case_item])
            )

        data = json.loads(resp.body)
        self.assertTrue(kb_mock.called)
        self.assertEqual(data["kb_count"], 1)
        digest_items = data["knowledge"]["digest"]["items"]
        self.assertEqual(digest_items[0]["reading_card"]["logo"], "TK")
        self.assertEqual(digest_items[0]["reading_card"]["primary_action"], "看影片文案")
        self.assertEqual(digest_items[1]["property_tag"], "物件")


if __name__ == "__main__":
    unittest.main()
