import unittest
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import (
    _build_portal_listing_panel,
    _case_detail_unavailable_reason,
    _case_missing_verified_gallery_unavailable_reason,
    _case_image_cache_hash,
    case_static_image_url,
    _listing_media_entries_from_case_row,
    ordered_listing_image_urls,
    _row_image_url_is_usable,
)
from src.live_enrich_urls import live_enrich_eligible_url
from src.portal_property_crawl import (
    _suumo_direct_resize_from_goo_proxy,
    _suumo_goo_mirror_detail_from_html,
    _suumo_goo_mirror_url_candidates,
)


class CaseMediaFilterTests(unittest.TestCase):
    def test_athome_image_files_path_builds_listing_media(self):
        row = {
            "source_item_id": 97558,
            "item_url": "https://www.athome.co.jp/kodate/1193560219/",
            "image_urls": "\n".join(
                [
                    "https://www.athome.co.jp/image_files/path/XhhSCezB3GEO6fT64CWcag==?width=572&height=418&margin=false",
                    "https://www.athome.co.jp/image_files/path/XhhSCezB3GHWTOPfQSDAMg==?width=572&height=418&margin=false",
                ]
            ),
            "body_original": "",
            "listing_media_json": "[]",
        }

        entries = _listing_media_entries_from_case_row(row, limit=4)

        self.assertEqual(len(entries), 2)
        self.assertTrue(all("image_files/path" in item["url"] for item in entries))

    def test_athome_image_files_path_is_allowed_in_ordered_gallery(self):
        image_urls = "\n".join(
            [
                "https://www.athome.co.jp/image_files/path/XhhSCezB3GEO6fT64CWcag==?width=572&height=418&margin=false",
                "https://www.athome.co.jp/image_files/path/XhhSCezB3GHWTOPfQSDAMg==?width=572&height=418&margin=false",
            ]
        )

        gallery = ordered_listing_image_urls(
            image_urls,
            "",
            "[]",
            item_url="https://www.athome.co.jp/kodate/1193560219/",
            limit=4,
        )

        self.assertEqual(len(gallery), 2)
        self.assertTrue(all("/image_files/path/" in u for u in gallery))

    def test_homes_image_php_without_file_parameter_is_not_usable(self):
        url = "https://image4.homes.jp/smallimg/image.php?width=1600&height=1600"

        self.assertFalse(_row_image_url_is_usable(url))

    def test_case_static_image_url_uses_local_proxy_for_uncached_remote_images(self):
        url = (
            "https://example.test/images/unit-case-image-uncached.jpg"
            "?case_static_proxy=1"
        )

        rendered = case_static_image_url(url)

        self.assertTrue(rendered.startswith(f"/api/case-image-cache/{_case_image_cache_hash(url)}?u="))
        self.assertIn("unit-case-image-uncached.jpg", rendered)
        self.assertNotEqual(rendered, url)

    def test_failed_top_hero_collapses_empty_image_area(self):
        css = Path("static/site.css").read_text(encoding="utf-8")

        self.assertIn(".portal-suumo-media--top .portal-suumo-herofig--failed", css)
        self.assertIn("aspect-ratio: auto", css)
        self.assertIn("min-height: 170px", css)

    def test_homes_listing_rejects_mixed_recommendation_images_without_listing_token(self):
        image_urls = "\n".join(
            [
                "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F145740%2Fsale%2F3261%2F2%2F1%2Fgmss.jpg&width=1600&height=1600",
                "https://image4.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F138495%2Fsale%2F4259%2F2%2F2%2F74e5.jpg&width=1600&height=1600",
                "https://image4.homes.jp/smallimg/image.php?file=https%3A%2F%2Fcdn-lambda-img.cloud.ielove.jp%2Fimage%2Fsale%2Ff7c508e%2F91285_4529832_3_132000_132000.jpg%3Ft%3D20260416121243&width=1600&height=1600",
            ]
        )

        gallery = ordered_listing_image_urls(
            image_urls,
            "",
            "[]",
            item_url="https://www.homes.co.jp/kodate/b-93810001007/",
            limit=10,
        )

        self.assertEqual(gallery, [])

    def test_homes_ielove_listing_keeps_only_leading_property_group(self):
        target_group = [
            "https://image2.homes.jp/smallimg/image.php?file=https%3A%2F%2Fcdn-lambda-img.cloud.ielove.jp%2Fimage%2Fsale%2F710c87c%2F7420_106144_4_132000_132000.jpg%3Ft%3D20260315141438&width=1600&height=1600",
            "https://image1.homes.jp/smallimg/image.php?file=https%3A%2F%2Fcdn-lambda-img.cloud.ielove.jp%2Fimage%2Fsale%2F7ecc70d8%2F7420_106144_1_132000_132000.jpg%3Ft%3D20260315141431&width=1600&height=1600",
            "https://image2.homes.jp/smallimg/image.php?file=https%3A%2F%2Fcdn-lambda-img.cloud.ielove.jp%2Fimage%2Fsale%2F90c211f4%2F7420_106144_2_132000_132000.jpg%3Ft%3D20260315141433&width=1600&height=1600",
            "https://image3.homes.jp/smallimg/image.php?file=https%3A%2F%2Fcdn-lambda-img.cloud.ielove.jp%2Fimage%2Fsale%2Fe0a8e57b%2F7420_106144_3_132000_132000.jpg%3Ft%3D20260315141436&width=1600&height=1600",
        ]
        recommendation_group = [
            "https://image.homes.jp/smallimg/image.php?file=https%3A%2F%2Fcdn-lambda-img.cloud.ielove.jp%2Fimage%2Fsale%2Fb4a8de33%2F15738_103599_1_132000_132000.jpg%3Ft%3D20251129160131&width=1600&height=1600",
            "https://image2.homes.jp/smallimg/image.php?file=https%3A%2F%2Fcdn-lambda-img.cloud.ielove.jp%2Fimage%2Fsale%2F1cb945a1%2F15552_80131_1_132000_132000.jpg%3Ft%3D20251201152300&width=1600&height=1600",
        ]

        gallery = ordered_listing_image_urls(
            "\n".join([*target_group, *recommendation_group]),
            "この物件を見ている人におすすめ "
            "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F145131%2Fsale%2F1257%2F2%2F2%2Fw9ks.jpg&width=1600&height=1600",
            "[]",
            item_url="https://www.homes.co.jp/mansion/b-1405270007258/",
            limit=10,
        )

        self.assertEqual(gallery, target_group)
        self.assertTrue(all("7420_106144" in u for u in gallery))

    def test_listing_panel_live_enriches_when_cached_text_has_no_valid_gallery(self):
        row = {
            "source_item_id": 77721,
            "source_name": "LIFULL HOME'S",
            "item_url": "https://www.homes.co.jp/kodate/b-93810001007/",
            "title_original": "豊川市美園3丁目 中古戸建",
            "title_zh_hant": "豐川市美園三丁目二手獨立住宅",
            "body_original": "販売価格 2780万円 所在地 愛知県豊川市美園3丁目 交通 名鉄名古屋本線 伊奈駅 徒歩14分",
            "body_zh_hant": "日本房產案源（本地快取重整） 所在地：愛知県豊川市美園3丁目 交通：伊奈站步行14分 專有面積：110㎡ 格局：4LDK",
            "image_urls": "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F145740%2Fsale%2F3261%2F2%2F1%2Fgmss.jpg&width=1600&height=1600",
            "listing_media_json": "[]",
        }
        live_row = {
            **row,
            "image_urls": "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F9381%2Fsale%2F1007%2F2%2F1%2Fgmss.jpg&width=1600&height=1600",
        }

        with patch.dict(os.environ, {"SCLAW_CASE_DISPLAY_LIVE_ENRICH": "1"}), patch(
            "app._enrich_listing_row_from_live_page", return_value=live_row
        ) as enrich:
            panel = _build_portal_listing_panel(row)

        enrich.assert_called_once()
        self.assertEqual(len(panel.get("gallery_property_urls") or []), 1)

    def test_listing_panel_does_not_live_enrich_during_default_case_render(self):
        row = {
            "source_item_id": 54150,
            "source_name": "LIFULL HOME'S",
            "item_url": "https://www.homes.co.jp/kodate/b-55070000012/",
            "title_original": "三鷹市大沢2丁目戸建 交通",
            "title_zh_hant": "日本房產案源：三鷹市大沢2丁目戸建",
            "body_original": "該当物件の掲載は終了しました 掲載中の物件情報は状況に応じ常に変動します。",
            "body_zh_hant": "日本房產案源（本地快取重整） 所在地：東京都三鷹市大沢2丁目 交通：多磨站步行20分 專有面積：100㎡ 格局：3SLDK",
            "image_urls": "",
            "listing_media_json": json.dumps(
                [
                    {
                        "type": "image",
                        "url": "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F145740%2Fsale%2F3261%2F2%2F1%2Fgmss.jpg&width=1600&height=1600",
                    }
                ],
                ensure_ascii=False,
            ),
        }

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SCLAW_CASE_DISPLAY_LIVE_ENRICH", None)
            with patch("app._enrich_listing_row_from_live_page", side_effect=AssertionError("live fetch on render")):
                panel = _build_portal_listing_panel(row)

        self.assertEqual(panel.get("gallery_property_urls") or [], [])

    def test_homes_kodate_listing_fields_are_extracted_from_detail_overview(self):
        row = {
            "source_item_id": 40033,
            "source_name": "LIFULL HOME'S",
            "item_url": "https://www.homes.co.jp/kodate/b-93070000869/",
            "title_original": "【ホームズ】豊橋市花中町23ーP1 (全2棟)2号棟｜豊橋市、豊橋鉄道渥美線 柳生橋駅 徒歩5分の中古一戸建て",
            "title_zh_hant": "日本房產案源：豊橋市花中町23ーP1 中古戸建(全2棟)2号棟 交通",
            "body_original": (
                "トップ 間取り 内観外観 設備 評価保証 周辺環境 物件概要 支払い目安 会社情報 "
                "2,990万円 支払い目安：8.2万円／月 4LDK/114㎡ 築年数 築2年 "
                "駐車場 空有 (2台) 無料 交通 豊橋鉄道渥美線 柳生橋駅 徒歩5分 全2駅 "
                "愛知県豊橋市花中町 資料請求する 無料 "
                "物件概要 価格 2,990万円 支払い目安：8.2万円／月 "
                "間取り 4LDK（リビングダイニングキッチン 18帖(1階) 洋室 7帖(2階)） "
                "建物面積 114㎡ 土地面積 104.75㎡（実測） 駐車場 空有 (2台) 無料 "
                "築年月 2025年2月（築2年） 所在地 愛知県豊橋市花中町 "
                "交通 豊橋鉄道渥美線 柳生橋駅 徒歩5分 豊橋鉄道渥美線 小池駅 徒歩12分 "
                "主要採光面 南 建物構造 木造/2階建 接道状況 一方 ( 東 3.6m 公道 ) "
                "土地権利 所有権 現況 空家 実際に見てみたい 無料 "
                "引渡し 相談 取引態様 一般媒介 建築確認番号 第R06SHC108491号 "
                "LIFULL HOME'S 物件番号 0009307-0000869 自社管理番号 202301-1346 "
                "情報公開日：2026/02/03 最新情報提供日：2026/06/02 情報有効期限：2026/06/16 "
                "支払い目安 月々支払額 - 万円/月"
            ),
            "body_zh_hant": "",
            "body_zh_hans": "",
            "image_urls": "",
            "listing_media_json": "[]",
        }

        fields = _build_portal_listing_panel(row)["fields"]

        self.assertEqual(fields["building_name_jp"], "豊橋市花中町23ーP1 (全2棟)2号棟")
        self.assertEqual(fields["price_text_hant"], "2,990萬日圓")
        self.assertEqual(fields["layout_line_jp"], "4LDK")
        self.assertEqual(fields["exclusive_area_jp"], "建物 114㎡ / 土地 104.75㎡（実測）")
        self.assertEqual(fields["address_line_jp"], "愛知県豊橋市花中町")
        self.assertEqual(fields["access_line_jp"], "豊橋鉄道渥美線 柳生橋駅 徒歩5分 / 豊橋鉄道渥美線 小池駅 徒歩12分")
        self.assertEqual(fields["parking_jp"], "空有 (2台) 無料")
        self.assertEqual(fields["built_ym_jp"], "2025年2月（築2年）")
        self.assertEqual(fields["structure_jp"], "木造/2階建")
        self.assertEqual(fields["status_jp"], "空家")
        self.assertEqual(fields["handover_jp"], "相談")
        self.assertEqual(fields["property_no_jp"], "0009307-0000869")
        self.assertEqual(fields["building_type_zh"], "透天/一戶建")

    def test_listing_panel_borrows_gallery_from_verified_same_property_source(self):
        suumo_interview_img = (
            "https://img01.suumo.com/jj/resizeImage?"
            "src=gazo%2Fbukken%2F030%2FN001000%2Fimg%2F19%2F67733019%2F67733019_000056.jpg"
            "&w=1600&h=1200"
        )
        suumo_floorplan_img = (
            "https://img01.suumo.com/jj/resizeImage?"
            "src=gazo%2Fbukken%2F030%2FN001000%2Fimg%2F19%2F67733019%2F67733019_000124.jpg"
            "&w=1600&h=1200"
        )
        suumo_building_img = (
            "https://img01.suumo.com/jj/resizeImage?"
            "src=gazo%2Fbukken%2F030%2FN001000%2Fimg%2F19%2F67733019%2F67733019_000001.jpg"
            "&w=1600&h=1200"
        )
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE source_items (
                id INTEGER PRIMARY KEY,
                source_name TEXT,
                item_url TEXT,
                title_original TEXT,
                body_original TEXT,
                image_urls TEXT,
                content_kind TEXT,
                last_checked_at TEXT
            );
            CREATE TABLE content_items (
                id INTEGER PRIMARY KEY,
                source_item_id INTEGER,
                title_zh_hant TEXT,
                body_zh_hant TEXT,
                listing_media_json TEXT
            );
            """
        )
        db.execute(
            """
            INSERT INTO source_items
            (id, source_name, item_url, title_original, body_original, image_urls, content_kind, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                63012,
                "SUUMO",
                "https://suumo.jp/ms/shinchiku/kanagawa/sc_yokohamashikanagawa/nc_67733019/",
                "【SUUMO】プレディア横浜反町 | 新築マンション・分譲マンション物件情報",
                "所在地 神奈川県横浜市神奈川区松本町１丁目3番1（地番） 交通 東急東横線「反町」歩2分 総戸数 69戸",
                "\n".join([suumo_interview_img, suumo_floorplan_img, suumo_building_img]),
                "jp_listing",
                "2026-06-02 13:10:00",
            ),
        )
        db.execute(
            """
            INSERT INTO content_items (id, source_item_id, title_zh_hant, body_zh_hant, listing_media_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                42236,
                63012,
                "日本房產案源：プレディア横浜反町",
                "",
                json.dumps(
                    [
                        {"type": "image", "url": suumo_interview_img},
                        {"type": "image", "url": suumo_floorplan_img},
                        {"type": "image", "url": suumo_building_img},
                    ],
                    ensure_ascii=False,
                ),
            ),
        )
        db.commit()

        @contextmanager
        def fake_get_conn():
            yield db

        row = {
            "source_item_id": 48157,
            "id": 48157,
            "source_name": "LIFULL HOME'S",
            "item_url": "https://www.homes.co.jp/mansion/b-16001060000048/",
            "title_original": "【ホームズ】プレディア横浜反町｜新築マンションの物件情報",
            "title_zh_hant": "日本房產案源：マンション プレディア横浜反町 閲覧済",
            "body_original": "所在地 神奈川県横浜市神奈川区松本町1丁目3番1（地番） 交通 東急東横線「反町」駅 徒歩2分 総戸数 69戸",
            "body_zh_hant": "日本房產案源（本地快取重整） 所在地：神奈川県横浜市神奈川区松本町1丁目3番1 交通：反町站步行2分 專有面積：11.55坪 格局：1房 + 客餐廚",
            "body_zh_hans": "",
            "image_urls": "",
            "listing_media_json": "[]",
        }

        with patch("app.get_conn", fake_get_conn), patch(
            "app._enrich_listing_row_from_live_page", return_value=None
        ):
            panel = _build_portal_listing_panel(row)

        gallery = panel.get("gallery_property_urls") or []
        self.assertTrue(gallery)
        self.assertIn("67733019_000001.jpg", gallery[0])

    def test_athome_authentication_page_is_not_rendered_as_listing(self):
        reason = _case_detail_unavailable_reason(
            {
                "content_kind": "jp_listing",
                "source_name": "アットホーム（AtHome）",
                "item_url": "https://www.athome.co.jp/kodate/6990642378/",
                "title_original": "【アットホーム】認証中",
                "title_zh_hant": "",
                "body_original": "",
                "body_zh_hant": "",
                "body_zh_hans": "",
                "image_urls": "",
            }
        )

        self.assertIn("認證", reason)

    def test_suumo_listing_summary_without_images_uses_unavailable_page(self):
        reason = _case_detail_unavailable_reason(
            {
                "content_kind": "jp_listing",
                "source_name": "SUUMO",
                "item_url": "https://suumo.jp/ikkodate/tokyo/sc_kodaira/nc_78610841/",
                "title_original": "小金井公園徒歩4分 北東角地の完成済3LDK",
                "title_zh_hant": "",
                "body_original": "小金井公園徒歩4分 北東角地の完成済3LDK\n\n[SUUMO 列表摘要]\n販売価格 4980万円",
                "body_zh_hant": "",
                "body_zh_hans": "",
                "image_urls": "",
                "listing_media_json": "[]",
            }
        )

        self.assertIn("SUUMO", reason)
        self.assertIn("空白圖片", reason)

    def test_suumo_goo_mirror_keeps_only_same_listing_images(self):
        same_listing_proxy = (
            "https://img.house.goo.ne.jp/uh/1/"
            "https%253A%252F%252Fimg01.suumo.com%252Ffront%252Fgazo%252Fbukken%252F030%252F"
            "N010000%252Fimg%252F888%252F79080888%252F79080888_0026.jpg?400x400"
        )
        other_listing_proxy = (
            "https://img.house.goo.ne.jp/uh/1/"
            "https%253A%252F%252Fimg01.suumo.com%252Ffront%252Fgazo%252Fbukken%252F030%252F"
            "N010000%252Fimg%252F999%252F79181999%252F79181999_0001.jpg?400x400"
        )
        html = f"""
        <html>
          <head><title>関谷 中古一戸建て</title></head>
          <body>
            <main>
              <h1>神奈川県鎌倉市関谷 中古住宅</h1>
              <img src="{same_listing_proxy}">
              <a href="{other_listing_proxy}">推薦物件</a>
            </main>
          </body>
        </html>
        """

        converted = _suumo_direct_resize_from_goo_proxy(same_listing_proxy, listing_id="79080888")
        title, text, images = _suumo_goo_mirror_detail_from_html(
            html,
            "https://house.goo.ne.jp/buy/uh/detail/1/14204/030Z79080888/000232008/x1030Z79080888.html",
            listing_id="79080888",
            limit=10,
        )

        self.assertIn("関谷", title)
        self.assertIn("中古住宅", text)
        self.assertEqual(images, [converted])
        self.assertIn("79080888_0026.jpg", converted)
        self.assertIn("w=1600", converted)
        self.assertIn("h=1200", converted)

    def test_suumo_goo_mirror_candidates_use_legacy_query_city_code(self):
        candidates = _suumo_goo_mirror_url_candidates(
            "https://suumo.jp/jj/bukken/shosai/JJ010FJ100/?ar=010&bs=011&nc=20868114&ta=01&sc=01106"
        )

        self.assertEqual(
            candidates,
            [
                "https://house.goo.ne.jp/buy/um/detail/1/01106/030Z20868114/000232008/x1030Z20868114.html"
            ],
        )

    def test_legacy_suumo_bukken_detail_url_is_live_enrich_eligible(self):
        self.assertTrue(
            live_enrich_eligible_url(
                "https://suumo.jp/jj/bukken/shosai/JJ010FJ100/?ar=010&bs=011&nc=20868114&ta=01&sc=01106"
            )
        )

    def test_homes_ended_listing_with_only_recommendation_media_uses_unavailable_page(self):
        reason = _case_detail_unavailable_reason(
            {
                "content_kind": "jp_listing",
                "source_name": "LIFULL HOME'S",
                "item_url": "https://www.homes.co.jp/kodate/b-55070000012/",
                "title_original": "三鷹市大沢2丁目戸建 交通",
                "title_zh_hant": "日本房產案源：三鷹市大沢2丁目戸建",
                "body_original": "中古一戸建て（物件番号：0005507-0000012） 該当物件の掲載は終了しました この物件を見ている人におすすめの物件",
                "body_zh_hant": "日本房產案源（本地快取重整） 所在地：東京都三鷹市大沢2丁目 交通：多磨站步行20分 專有面積：100㎡ 格局：3SLDK",
                "body_zh_hans": "",
                "image_urls": "",
                "listing_media_json": json.dumps(
                    [
                        {
                            "type": "image",
                            "url": "https://image1.homes.jp/smallimg/image.php?file=http%3A%2F%2Fimg.homes.jp%2F145740%2Fsale%2F3261%2F2%2F1%2Fgmss.jpg&width=1600&height=1600",
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )

        self.assertIn("掲載終了", reason)
        self.assertIn("空白圖片", reason)

    def test_listing_without_verified_gallery_after_borrow_uses_unavailable_page(self):
        row = {
            "content_kind": "jp_listing",
            "source_name": "LIFULL HOME'S",
            "item_url": "https://www.homes.co.jp/mansion/b-16007950000001/",
            "title_original": "【ホームズ】シュロスガーデン千葉｜新築マンションの物件情報",
            "title_zh_hant": "日本房產案源：シュロスガーデン千葉",
            "body_original": "シュロスガーデン千葉 更新日：2026/05/19",
            "body_zh_hant": "日本房產案源（本地快取重整） 所在地：千葉県千葉市中央区 交通：東千葉站步行 專有面積：60㎡ 格局：3LDK",
            "image_urls": "",
            "listing_media_json": "[]",
        }
        panel = {"gallery_property_urls": [], "meta": {}}

        reason = _case_missing_verified_gallery_unavailable_reason(row, panel)

        self.assertIn("可信圖片", reason)
        self.assertIn("大圖物件版型", reason)


if __name__ == "__main__":
    unittest.main()
