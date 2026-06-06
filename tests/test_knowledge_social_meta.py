import unittest

from src.knowledge_service import (
    build_social_knowledge_digest,
    format_knowledge_for_prompt,
    knowledge_items_for_api,
    knowledge_source_meta,
)


class KnowledgeSocialMetaTests(unittest.TestCase):
    def test_tiktok_video_rows_expose_social_reading_mode(self):
        row = {
            "content_id": 1,
            "source_name": "TikTok",
            "item_url": "https://www.tiktok.com/@tokyoestate/video/7350000000000000000",
            "content_kind": "social_video_knowledge",
            "title_ja": "日本不動産購入の注意点",
            "title_zh_hant": "日本買房前先看稅費與貸款",
            "title_zh_hans": "日本买房前先看税费与贷款",
            "body_zh_hant_excerpt": "影片文案重點：先確認用途、持有成本與外國人貸款條件。",
            "body_zh_hans_excerpt": "视频文案重点：先确认用途、持有成本与外国人贷款条件。",
            "topic_category": "日本購房知識",
            "intent_target": "購房流程",
            "image_urls": '["https://cdn.example.test/cover.jpg"]',
        }

        meta = knowledge_source_meta(row)
        self.assertEqual(meta["platform_key"], "tiktok")
        self.assertEqual(meta["platform_logo"], "TK")
        self.assertEqual(meta["reading_mode"], "視頻文案播放")
        self.assertEqual(meta["source_badge"], "社媒")

        api_row = knowledge_items_for_api([row], zh_variant="hant")[0]
        self.assertEqual(api_row["platform_key"], "tiktok")
        self.assertEqual(api_row["platform_logo"], "TK")
        self.assertEqual(api_row["reading_mode"], "視頻文案播放")
        self.assertEqual(api_row["source_badge"], "社媒")
        self.assertEqual(api_row["thumbnail_urls"], ["https://cdn.example.test/cover.jpg"])
        self.assertIn("影片文案重點", api_row["excerpt_display"])

        prompt = format_knowledge_for_prompt([row], zh_variant="hant")
        self.assertIn("平台:TikTok", prompt)
        self.assertIn("閱讀模式:視頻文案播放", prompt)
        self.assertIn("內容類型:社媒影片", prompt)

    def test_portal_listing_rows_are_marked_as_property_sources(self):
        row = {
            "content_id": 2,
            "source_name": "SUUMO",
            "item_url": "https://suumo.jp/chukoikkodate/tokyo/sc_example/nc123/",
            "content_kind": "jp_listing",
            "title_ja": "中古戸建",
            "title_zh_hant": "東京中古一戶建 3LDK",
            "title_zh_hans": "东京中古一户建 3LDK",
            "body_zh_hant_excerpt": "七大來源翻譯：價格、交通、屋齡與管理費需一起確認。",
            "body_zh_hans_excerpt": "七大来源翻译：价格、交通、房龄与管理费需一起确认。",
            "topic_category": "日本房產案源",
            "intent_target": "購屋比較",
            "image_urls": "https://cdn.example.test/listing.jpg\nhttps://cdn.example.test/map.png",
        }

        api_row = knowledge_items_for_api([row], zh_variant="hant")[0]
        self.assertEqual(api_row["platform_key"], "suumo")
        self.assertEqual(api_row["platform_logo"], "SUUMO")
        self.assertEqual(api_row["reading_mode"], "七大來源翻譯")
        self.assertEqual(api_row["content_badge"], "物件")
        self.assertTrue(api_row["is_case_content"])
        self.assertEqual(api_row["thumbnail_urls"], ["https://cdn.example.test/listing.jpg", "https://cdn.example.test/map.png"])

        prompt = format_knowledge_for_prompt([row], zh_variant="hant")
        self.assertIn("平台:SUUMO", prompt)
        self.assertIn("內容標籤:物件", prompt)
        self.assertIn("閱讀模式:七大來源翻譯", prompt)

    def test_social_knowledge_digest_prioritizes_recent_video_copy_and_property_labels(self):
        rows = [
            {
                "content_id": 3,
                "source_name": "TikTok",
                "item_url": "https://vt.tiktok.com/ZSxcW2GMp/",
                "content_kind": "social_video_knowledge",
                "title_ja": "日本不動産を買う前に",
                "title_zh_hant": "日本買房前先看持有成本",
                "title_zh_hans": "日本买房前先看持有成本",
                "body_zh_hant_excerpt": "影片文案重點：先確認用途、持有成本與外國人貸款條件。再比較管理費、修繕積立金與稅金。",
                "body_zh_hans_excerpt": "视频文案重点：先确认用途、持有成本与外国人贷款条件。再比较管理费、修缮积立金与税金。",
                "topic_category": "日本購房知識",
                "intent_target": "購房流程",
                "image_urls": '["https://cdn.example.test/tk-cover.jpg"]',
                "crawled_at": "2026-06-02 10:00:00",
            },
            {
                "content_id": 4,
                "source_name": "小紅書",
                "item_url": "https://www.xiaohongshu.com/explore/abc",
                "content_kind": "social_note",
                "title_zh_hant": "日本買房圖文筆記：看懂稅費",
                "body_zh_hant_excerpt": "圖文筆記重點：契約前要整理頭期款、仲介費、登記費與固定資產稅。",
                "topic_category": "日本房地產圖文",
                "intent_target": "稅費清單",
                "crawled_at": "2026-06-02 11:00:00",
            },
            {
                "content_id": 5,
                "source_name": "SUUMO",
                "item_url": "https://suumo.jp/chukoikkodate/tokyo/sc_example/nc777/",
                "content_kind": "jp_listing",
                "title_zh_hant": "東京中古一戶建 3LDK",
                "body_zh_hant_excerpt": "物件資料：價格、交通、屋齡與面積需與貸款條件一起看。",
                "topic_category": "日本房產案源",
                "intent_target": "物件比較",
                "image_urls": "https://cdn.example.test/listing.jpg",
                "crawled_at": "2026-06-02 12:00:00",
            },
        ]

        digest = build_social_knowledge_digest(rows, zh_variant="hant", window_days=15)

        self.assertEqual(digest["window_days"], 15)
        self.assertIn("近 15 天", digest["freshness_label"])
        self.assertTrue(digest["source_policy"].startswith("社媒影片/圖文優先"))
        self.assertEqual([p["logo"] for p in digest["platforms"]], ["TK", "小紅書", "SUUMO"])

        first = digest["items"][0]
        self.assertEqual(first["reading_card"]["logo"], "TK")
        self.assertEqual(first["reading_card"]["mode"], "視頻文案播放")
        self.assertTrue(first["video_text_focus"])
        self.assertIn("先確認用途", "".join(first["summary_points"]))

        listing = digest["items"][2]
        self.assertEqual(listing["reading_card"]["logo"], "SUUMO")
        self.assertEqual(listing["property_tag"], "物件")
        self.assertIn("物件", listing["reading_card"]["badges"])

    def test_video_digest_extracts_short_points_from_structured_script_sections(self):
        row = {
            "content_id": 6,
            "source_name": "TikTok｜@jp.home",
            "item_url": "https://www.tiktok.com/@jp.home/video/123",
            "content_kind": "social_video_knowledge",
            "title_zh_hant": "日本買房影片文案",
            "body_zh_hant_excerpt": (
                "TikTok 影片知識來源（日本房地產／海外置業）\n\n"
                "[影片文案]\n"
                "買房前先確認用途與持有成本。\n\n"
                "[作者]\n"
                "- 帳號：@jp.home\n\n"
                "[字幕逐字稿]\n"
                "到了日本\n"
                "你會發現\n"
                "外國人房貸要先看收入證明。\n"
                "管理費與修繕積立金會影響長期成本。"
            ),
            "topic_category": "日本購房知識",
            "intent_target": "購房流程",
        }

        digest = build_social_knowledge_digest([row], zh_variant="hant", window_days=15)
        points = digest["items"][0]["summary_points"]

        self.assertEqual(points[0], "影片文案重點：買房前先確認用途與持有成本")
        self.assertIn("外國人房貸要先看收入證明", points)
        self.assertNotIn("到了日本", points)
        self.assertNotIn("你會發現", points)
        self.assertNotIn("帳號", " ".join(points))


if __name__ == "__main__":
    unittest.main()
