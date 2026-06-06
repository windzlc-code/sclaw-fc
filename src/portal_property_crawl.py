"""
SUUMO / LIFULL HOMES / AtHome / Yahoo!不動産 — listing hub pages → property detail URLs.

Fetches detail title, short text, og:image + key <img> URLs for 日本房產案源 tagging.
Respect site ToS in production; this is a best-effort HTML parse for internal RAG.
"""

from __future__ import annotations

import json
import html
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse


import httpx
from bs4 import BeautifulSoup

from src.bsoup import soup_from_html
from src.config import DATA_DIR
from src.homes_media_token import homes_listing_image_tokens
from src.portal_http import PORTAL_BROWSER_HEADERS

logger = logging.getLogger(__name__)

# 索引鍵為 host（與 source_registry.SEVEN_JP_PORTAL_HOST_ORDER / crawl_portal_listings 一致）。
# HOMES 賃貸詳情多為 …/chintai/room/{hex}/（非僅 b-數字）；沖繩／關東列表須走分頁見 _is_homes_mansion_catalog_hub。
LISTING_HUB_PAGES: dict[str, list[str]] = {
    "suumo.jp": [
        "https://suumo.jp/chintai/tokyo/",
        "https://suumo.jp/chintai/kanagawa/",
        "https://suumo.jp/chintai/saitama/",
        "https://suumo.jp/chintai/chiba/",
        "https://suumo.jp/chintai/osaka/",
        "https://suumo.jp/chintai/kyoto/",
        "https://suumo.jp/chintai/hyogo/",
        "https://suumo.jp/chintai/aichi/",
        "https://suumo.jp/chintai/fukuoka/",
        "https://suumo.jp/chintai/hiroshima/",
        "https://suumo.jp/chintai/miyagi/",
        "https://suumo.jp/chintai/hokkaido/",
        "https://suumo.jp/ms/chuko/tokyo/",
        "https://suumo.jp/ms/chuko/kanagawa/",
        "https://suumo.jp/ms/chuko/saitama/",
        "https://suumo.jp/ms/chuko/chiba/",
        "https://suumo.jp/ms/chuko/osaka/",
        "https://suumo.jp/ms/chuko/kyoto/",
        "https://suumo.jp/ms/chuko/hyogo/",
        "https://suumo.jp/ms/chuko/aichi/",
        "https://suumo.jp/ms/chuko/fukuoka/",
        "https://suumo.jp/ms/chuko/hiroshima/",
        "https://suumo.jp/ms/chuko/miyagi/",
        "https://suumo.jp/ms/chuko/hokkaido/",
        # 新築マンション（分譲）— prefecture hubs
        "https://suumo.jp/ms/shinchiku/tokyo/",
        "https://suumo.jp/ms/shinchiku/kanagawa/",
        "https://suumo.jp/ms/shinchiku/saitama/",
        "https://suumo.jp/ms/shinchiku/chiba/",
        "https://suumo.jp/ms/shinchiku/osaka/",
        "https://suumo.jp/ms/shinchiku/kyoto/",
        "https://suumo.jp/ms/shinchiku/hyogo/",
        "https://suumo.jp/ms/shinchiku/aichi/",
        "https://suumo.jp/ms/shinchiku/fukuoka/",
        "https://suumo.jp/ms/shinchiku/hiroshima/",
        "https://suumo.jp/ms/shinchiku/miyagi/",
        "https://suumo.jp/ms/shinchiku/hokkaido/",
        "https://suumo.jp/ms/shinchiku/nagano/",
        "https://suumo.jp/ms/shinchiku/niigata/",
        "https://suumo.jp/ms/shinchiku/yamanashi/",
        "https://suumo.jp/ms/shinchiku/ishikawa/",
        "https://suumo.jp/ms/shinchiku/toyama/",
        "https://suumo.jp/ms/shinchiku/fukui/",
        "https://suumo.jp/ms/shinchiku/shizuoka/",
        "https://suumo.jp/ms/shinchiku/gifu/",
        "https://suumo.jp/ms/shinchiku/mie/",
        # 新築マンション — region hubs (often link to /jj/bukken/ichiran)
        "https://suumo.jp/ms/shinchiku/koshinetsu/",
        "https://suumo.jp/ms/shinchiku/tokai/",
        "https://suumo.jp/ms/shinchiku/shikoku/",
        # 中古マンション — region hubs (often link to /jj/bukken/ichiran)
        "https://suumo.jp/ms/chuko/tohoku/",
        "https://suumo.jp/ms/chuko/tokai/",
        "https://suumo.jp/ms/chuko/shikoku/",
        "https://suumo.jp/chukoikkodate/tokyo/",
        "https://suumo.jp/chukoikkodate/kanagawa/",
        "https://suumo.jp/chukoikkodate/saitama/",
        "https://suumo.jp/chukoikkodate/chiba/",
        "https://suumo.jp/chukoikkodate/osaka/",
        "https://suumo.jp/chukoikkodate/kyoto/",
        "https://suumo.jp/chukoikkodate/hyogo/",
        "https://suumo.jp/chukoikkodate/aichi/",
        "https://suumo.jp/chukoikkodate/fukuoka/",
        "https://suumo.jp/chukoikkodate/hiroshima/",
        "https://suumo.jp/chukoikkodate/miyagi/",
        "https://suumo.jp/chukoikkodate/hokkaido/",
        "https://suumo.jp/chukoikkodate/nagano/",
        "https://suumo.jp/chukoikkodate/niigata/",
        "https://suumo.jp/chukoikkodate/yamanashi/",
        "https://suumo.jp/chukoikkodate/ishikawa/",
        "https://suumo.jp/chukoikkodate/toyama/",
        "https://suumo.jp/chukoikkodate/fukui/",
        "https://suumo.jp/chukoikkodate/shizuoka/",
        "https://suumo.jp/chukoikkodate/gifu/",
        "https://suumo.jp/chukoikkodate/mie/",
        # 中古一戸建て — city selector hubs (form → /jj/bukken/ichiran)
        "https://suumo.jp/chukoikkodate/fukui/city/",
        "https://suumo.jp/ikkodate/tokyo/",
        "https://suumo.jp/ikkodate/kanagawa/",
        "https://suumo.jp/ikkodate/saitama/",
        "https://suumo.jp/ikkodate/chiba/",
        "https://suumo.jp/ikkodate/osaka/",
        "https://suumo.jp/ikkodate/kyoto/",
        "https://suumo.jp/ikkodate/hyogo/",
        "https://suumo.jp/ikkodate/aichi/",
        "https://suumo.jp/ikkodate/fukuoka/",
        "https://suumo.jp/ikkodate/hokkaido/",
        "https://suumo.jp/ikkodate/miyagi/",
        "https://suumo.jp/ikkodate/hiroshima/",
        "https://suumo.jp/ikkodate/nagano/",
        "https://suumo.jp/ikkodate/niigata/",
        "https://suumo.jp/ikkodate/yamanashi/",
        "https://suumo.jp/ikkodate/ishikawa/",
        "https://suumo.jp/ikkodate/toyama/",
        "https://suumo.jp/ikkodate/fukui/",
        "https://suumo.jp/ikkodate/shizuoka/",
        "https://suumo.jp/ikkodate/gifu/",
        "https://suumo.jp/ikkodate/mie/",
        # 新築一戸建て — region hubs (often link to /jj/bukken/ichiran)
        "https://suumo.jp/ikkodate/koshinetsu/",
        "https://suumo.jp/ikkodate/kagawa/ensen/",
        # 東急目黒線 西小山駅 周邊中古（與目黑本町等案源一致，列表 → nc_ 詳情）
        "https://suumo.jp/ms/chuko/tokyo/ek_28780/",
        # 東急池上線 旗の台駅 周邊中古（例：長原台アーバンハイム 等，列表 → nc_ 詳情）
        "https://suumo.jp/ms/chuko/tokyo/ek_30650/",
        # 九州（福岡等）中古マンション city 木 → 詳情 nc_ /jnc_
        "https://suumo.jp/ms/chuko/fukuoka/",
    ],
    "homes.co.jp": [
        # 關東新築分譲 city 列表 → /mansion/b-… 詳情
        "https://www.homes.co.jp/mansion/shinchiku/tokyo/city/",
        "https://www.homes.co.jp/mansion/shinchiku/kanagawa/city/",
        "https://www.homes.co.jp/mansion/shinchiku/saitama/city/",
        "https://www.homes.co.jp/mansion/shinchiku/chiba/city/",
        "https://www.homes.co.jp/mansion/shinchiku/osaka/city/",
        "https://www.homes.co.jp/mansion/shinchiku/kyoto/city/",
        "https://www.homes.co.jp/mansion/shinchiku/hyogo/city/",
        "https://www.homes.co.jp/mansion/shinchiku/aichi/city/",
        "https://www.homes.co.jp/mansion/shinchiku/fukuoka/city/",
        "https://www.homes.co.jp/mansion/shinchiku/hiroshima/city/",
        "https://www.homes.co.jp/mansion/shinchiku/miyagi/city/",
        # 東京 23 區中古マンション city
        "https://www.homes.co.jp/mansion/chuko/tokyo/tokyo_23ku/city/",
        "https://www.homes.co.jp/mansion/chuko/kanagawa/city/",
        "https://www.homes.co.jp/mansion/chuko/saitama/city/",
        "https://www.homes.co.jp/mansion/chuko/chiba/city/",
        "https://www.homes.co.jp/mansion/chuko/osaka/city/",
        "https://www.homes.co.jp/mansion/chuko/kyoto/city/",
        "https://www.homes.co.jp/mansion/chuko/hyogo/city/",
        "https://www.homes.co.jp/mansion/chuko/aichi/city/",
        "https://www.homes.co.jp/mansion/chuko/hiroshima/city/",
        "https://www.homes.co.jp/mansion/chuko/miyagi/city/",
        # 北海道（札幌）中古マンション city/list
        "https://www.homes.co.jp/mansion/chuko/hokkaido/sapporo_kita-city/list/",
        "https://www.homes.co.jp/kodate/chuko/tokyo/city/",
        "https://www.homes.co.jp/kodate/chuko/kanagawa/city/",
        "https://www.homes.co.jp/kodate/chuko/saitama/city/",
        "https://www.homes.co.jp/kodate/chuko/chiba/city/",
        "https://www.homes.co.jp/kodate/chuko/osaka/city/",
        "https://www.homes.co.jp/kodate/chuko/kyoto/city/",
        "https://www.homes.co.jp/kodate/chuko/hyogo/city/",
        "https://www.homes.co.jp/kodate/chuko/aichi/city/",
        "https://www.homes.co.jp/kodate/chuko/fukuoka/city/",
        "https://www.homes.co.jp/kodate/chuko/hiroshima/city/",
        "https://www.homes.co.jp/kodate/chuko/miyagi/city/",
        "https://www.homes.co.jp/kodate/chuko/hokkaido/city/",
        "https://www.homes.co.jp/kodate/shinchiku/tokyo/city/",
        "https://www.homes.co.jp/kodate/shinchiku/kanagawa/city/",
        "https://www.homes.co.jp/kodate/shinchiku/saitama/city/",
        "https://www.homes.co.jp/kodate/shinchiku/chiba/city/",
        "https://www.homes.co.jp/kodate/shinchiku/osaka/city/",
        "https://www.homes.co.jp/kodate/shinchiku/aichi/city/",
        # 九州 中古マンション city → /mansion/b-…（補庫前僅關東／札幌則「九州」關鍵字 0 筆）
        "https://www.homes.co.jp/mansion/chuko/fukuoka/fukuoka/city/",
        "https://www.homes.co.jp/mansion/chuko/kumamoto/kumamoto-city/city/",
        # 賃貸／其他門戶匯總（非 b- 物件頁）
        "https://www.homes.co.jp/chintai/tokyo/tokyo_23ku/city/",
        "https://www.homes.co.jp/chintai/kanagawa/",
        "https://www.homes.co.jp/chintai/saitama/",
        "https://www.homes.co.jp/chintai/chiba/",
        "https://www.homes.co.jp/chintai/osaka/",
        "https://www.homes.co.jp/chintai/kyoto/",
        "https://www.homes.co.jp/chintai/hyogo/",
        "https://www.homes.co.jp/chintai/aichi/",
        "https://www.homes.co.jp/chintai/fukuoka/",
        "https://www.homes.co.jp/chintai/hiroshima/",
        "https://www.homes.co.jp/chintai/miyagi/",
        # 沖縄賃貸：列表頁（分頁掃描）→ 詳情多為 …/chintai/room/{40hex}/（見 _is_homes_property_url）
        "https://www.homes.co.jp/chintai/okinawa/list/",
        "https://www.homes.co.jp/chintai/okinawa/",
    ],
    "athome.co.jp": [
        # 新築マンション一覧・駅近特集（優先順：single crawl hub cap が後段まで届かないことへの対策）
        "https://www.athome.co.jp/mansion/shinchiku/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/shutoken/list/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/tokyo/list/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/kanagawa/list/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/chiba/list/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/saitama/list/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/ibaraki/list/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/tochigi/list/",
        "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/gunma/list/",
        "https://www.athome.co.jp/chintai/tokyo/tokyo/",
        "https://www.athome.co.jp/chintai/kanagawa/",
        "https://www.athome.co.jp/chintai/saitama/",
        "https://www.athome.co.jp/chintai/chiba/",
        "https://www.athome.co.jp/chintai/osaka/",
        "https://www.athome.co.jp/chintai/kyoto/",
        "https://www.athome.co.jp/chintai/hyogo/",
        "https://www.athome.co.jp/chintai/aichi/",
        "https://www.athome.co.jp/chintai/fukuoka/",
        "https://www.athome.co.jp/chintai/hiroshima/",
        "https://www.athome.co.jp/chintai/miyagi/",
        "https://www.athome.co.jp/chintai/hokkaido/",
        # 中古マンション全国入口（新着・人気カード → /mansion/{物件番号}/）
        "https://www.athome.co.jp/mansion/chuko/",
        "https://www.athome.co.jp/mansion/chuko/kanto/",
        "https://www.athome.co.jp/mansion/chuko/kansai/",
        "https://www.athome.co.jp/mansion/chuko/tokai/",
        "https://www.athome.co.jp/mansion/chuko/tohoku/",
        "https://www.athome.co.jp/mansion/chuko/chugoku/",
        "https://www.athome.co.jp/mansion/chuko/shikoku/",
        "https://www.athome.co.jp/mansion/chuko/kyushu/",
        # 中古一戸建て（都道府県列表）— 與 SUUMO 物件表欄位對齊之案源
        "https://www.athome.co.jp/kodate/chuko/tokyo/list/",
        "https://www.athome.co.jp/kodate/chuko/kanagawa/list/",
        "https://www.athome.co.jp/kodate/chuko/saitama/list/",
        "https://www.athome.co.jp/kodate/chuko/chiba/list/",
        "https://www.athome.co.jp/kodate/chuko/osaka/list/",
        "https://www.athome.co.jp/kodate/chuko/hyogo/list/",
        "https://www.athome.co.jp/kodate/chuko/aichi/list/",
        "https://www.athome.co.jp/kodate/chuko/fukuoka/list/",
        "https://www.athome.co.jp/kodate/chuko/hokkaido/list/",
        "https://www.athome.co.jp/kodate/chuko/kyoto/list/",
        "https://www.athome.co.jp/kodate/chuko/nagano/list/",
    ],
    "realestate.yahoo.co.jp": [
        # 中古マンションエリア検索 → detail_corp 詳情（例：北九州市小倉北区）
        "https://realestate.yahoo.co.jp/used/mansion/search/09/40/40106/",
        "https://realestate.yahoo.co.jp/used/mansion/search/03/13/",
        "https://realestate.yahoo.co.jp/used/mansion/search/03/14/",
        "https://realestate.yahoo.co.jp/used/mansion/search/03/11/",
        "https://realestate.yahoo.co.jp/used/mansion/search/03/12/",
        "https://realestate.yahoo.co.jp/used/mansion/search/06/27/",
        "https://realestate.yahoo.co.jp/used/mansion/search/06/26/",
        "https://realestate.yahoo.co.jp/used/mansion/search/06/28/",
        "https://realestate.yahoo.co.jp/used/mansion/search/05/23/",
        "https://realestate.yahoo.co.jp/used/mansion/search/07/34/",
        "https://realestate.yahoo.co.jp/used/mansion/search/02/01/",
        "https://realestate.yahoo.co.jp/used/mansion/search/01/04/",
        "https://realestate.yahoo.co.jp/used/house/search/03/13/",
        "https://realestate.yahoo.co.jp/used/house/search/03/14/",
        "https://realestate.yahoo.co.jp/used/house/search/03/11/",
        "https://realestate.yahoo.co.jp/used/house/search/03/12/",
        "https://realestate.yahoo.co.jp/used/house/search/06/27/",
        "https://realestate.yahoo.co.jp/used/house/search/06/26/",
        "https://realestate.yahoo.co.jp/used/house/search/06/28/",
        "https://realestate.yahoo.co.jp/used/house/search/05/23/",
        "https://realestate.yahoo.co.jp/used/house/search/07/34/",
        "https://realestate.yahoo.co.jp/used/house/search/02/01/",
        "https://realestate.yahoo.co.jp/used/house/search/01/04/",
        "https://realestate.yahoo.co.jp/new/house/search/03/13/",
        "https://realestate.yahoo.co.jp/new/house/search/03/14/",
        "https://realestate.yahoo.co.jp/new/house/search/03/11/",
        "https://realestate.yahoo.co.jp/new/house/search/03/12/",
    ],
    # 以下三站：以列表入口＋寬鬆 href 掃描；站方改版時可能需調整 hub
    "realestate.rakuten.co.jp": [
        # Rakuten 站內新版多由 /usedmansion/、/newdetached/ 等入口導頁，舊 /mansion/chuko/* 多為 404。
        "https://realestate.rakuten.co.jp/",
        "https://realestate.rakuten.co.jp/usedmansion/?area=zenkoku",
        "https://realestate.rakuten.co.jp/newdetached/?area=zenkoku",
        "https://realestate.rakuten.co.jp/useddetached/?area=zenkoku",
        "https://realestate.rakuten.co.jp/land/?area=zenkoku",
        "https://realestate.rakuten.co.jp/usedmansion/item/",
        "https://realestate.rakuten.co.jp/newdetached/item/",
        "https://realestate.rakuten.co.jp/useddetached/item/",
        "https://realestate.rakuten.co.jp/land/item/",
    ],
    "yes1.co.jp": [
        # 舊 /mansion/{pref}/ 多為 404；改用目前可用之 area 入口。
        "https://www.yes1.co.jp/",
        "https://www.yes1.co.jp/contents/search_area/mansion/hokkaido/",
        "https://www.yes1.co.jp/contents/search_area/mansion/miyagi/",
        "https://www.yes1.co.jp/contents/search_area/mansion/tokyo/",
        "https://www.yes1.co.jp/contents/search_area/mansion/kanagawa/",
        "https://www.yes1.co.jp/contents/search_area/mansion/saitama/",
        "https://www.yes1.co.jp/contents/search_area/mansion/chiba/",
        "https://www.yes1.co.jp/contents/search_area/mansion/aichi/",
        "https://www.yes1.co.jp/contents/search_area/mansion/osaka/",
        "https://www.yes1.co.jp/contents/search_area/mansion/kyoto/",
        "https://www.yes1.co.jp/contents/search_area/mansion/hyogo/",
        "https://www.yes1.co.jp/contents/search_area/mansion/hiroshima/",
        "https://www.yes1.co.jp/contents/search_area/mansion/fukuoka/",
        "https://www.yes1.co.jp/contents/search_area/house/used/hokkaido/01100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/tokyo/13100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/kanagawa/14100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/saitama/11100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/chiba/12100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/aichi/23100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/osaka/27100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/kyoto/26100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/hyogo/28100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/hiroshima/34100-city",
        "https://www.yes1.co.jp/contents/search_area/house/used/fukuoka/40130-city",
    ],
    "oheya-su.jp": [
        "https://www.oheya-su.jp/chintai/tokyo/",
        "https://www.oheya-su.jp/chintai/kanagawa/",
        "https://www.oheya-su.jp/chintai/saitama/",
        "https://www.oheya-su.jp/chintai/chiba/",
        "https://www.oheya-su.jp/chintai/osaka/",
        "https://www.oheya-su.jp/chintai/kyoto/",
        "https://www.oheya-su.jp/chintai/hyogo/",
        "https://www.oheya-su.jp/chintai/aichi/",
        "https://www.oheya-su.jp/chintai/fukuoka/",
        "https://www.oheya-su.jp/chintai/hiroshima/",
        "https://www.oheya-su.jp/chintai/miyagi/",
        "https://www.oheya-su.jp/chintai/hokkaido/",
    ],
}

# 若 LISTING_HUB_PAGES 過長，單次 crawl 會拖到逾時；縮 hub／縮分頁優先於無上限掃描。
# Expand primary portals (SUUMO / HOME'S / at home) to cover all prefectures.
# This enables coverage bots to backfill non-Kanto regions without manually
# maintaining giant seed lists.
def _append_unique_hub(hubs: list[str], url: str) -> None:
    u = str(url or "").strip()
    if not u:
        return
    if u not in hubs:
        hubs.append(u)


def _expand_primary_portal_hubs() -> None:
    try:
        from src.homes_geo import HOMES_KODATE_CHUKO_PREFS
    except Exception:
        return

    pref_keys = [
        str(p.key or "").strip().lower()
        for p in (HOMES_KODATE_CHUKO_PREFS or [])
        if getattr(p, "key", None)
    ]
    pref_keys = [k for k in pref_keys if k]
    if not pref_keys:
        return

    homes = LISTING_HUB_PAGES.get("homes.co.jp")
    if isinstance(homes, list):
        for pref in pref_keys:
            _append_unique_hub(homes, f"https://www.homes.co.jp/kodate/chuko/{pref}/city/")
            _append_unique_hub(homes, f"https://www.homes.co.jp/kodate/chuko/{pref}/line/")
            _append_unique_hub(homes, f"https://www.homes.co.jp/kodate/shinchiku/{pref}/city/")
            _append_unique_hub(homes, f"https://www.homes.co.jp/mansion/chuko/{pref}/city/")
            _append_unique_hub(homes, f"https://www.homes.co.jp/mansion/shinchiku/{pref}/city/")
            _append_unique_hub(homes, f"https://www.homes.co.jp/chintai/{pref}/")
        _append_unique_hub(homes, "https://www.homes.co.jp/mansion/chuko/tokyo/tokyo_23ku/city/")
        _append_unique_hub(homes, "https://www.homes.co.jp/chintai/tokyo/tokyo_23ku/city/")

    athome = LISTING_HUB_PAGES.get("athome.co.jp")
    if isinstance(athome, list):
        for pref in pref_keys:
            _append_unique_hub(athome, f"https://www.athome.co.jp/chintai/{pref}/")
            _append_unique_hub(athome, f"https://www.athome.co.jp/mansion/chuko/{pref}/")
            _append_unique_hub(athome, f"https://www.athome.co.jp/kodate/chuko/{pref}/list/")

    suumo = LISTING_HUB_PAGES.get("suumo.jp")
    if isinstance(suumo, list):
        for pref in pref_keys:
            _append_unique_hub(suumo, f"https://suumo.jp/chintai/{pref}/")
            _append_unique_hub(suumo, f"https://suumo.jp/ms/chuko/{pref}/")
            _append_unique_hub(suumo, f"https://suumo.jp/ms/chuko/{pref}/ensen/")
            _append_unique_hub(suumo, f"https://suumo.jp/ms/shinchiku/{pref}/")
            _append_unique_hub(suumo, f"https://suumo.jp/chukoikkodate/{pref}/")
            _append_unique_hub(suumo, f"https://suumo.jp/chukoikkodate/{pref}/city/")
            _append_unique_hub(suumo, f"https://suumo.jp/chukoikkodate/{pref}/ensen/")
            _append_unique_hub(suumo, f"https://suumo.jp/ikkodate/{pref}/")
            _append_unique_hub(suumo, f"https://suumo.jp/ikkodate/{pref}/ensen/")


_expand_primary_portal_hubs()

_SLOW_PORTAL_HOSTS = frozenset(
    {
        "athome.co.jp",
        "realestate.rakuten.co.jp",
        "yes1.co.jp",
        "oheya-su.jp",
    }
)

_PORTAL_RATE_LIMIT_STATE_FILE = DATA_DIR / "portal_rate_limit_state.json"
_PORTAL_LAST_REQUEST_AT: dict[str, float] = {}


class PortalRateLimitActive(RuntimeError):
    """Raised when a portal is currently cooling down after an explicit limit signal."""


def _env_float(name: str, default: float, *, min_value: float, max_value: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(min_value, min(max_value, float(raw)))
    except ValueError:
        return default


def _load_portal_rate_limit_state() -> dict:
    try:
        data = json.loads(_PORTAL_RATE_LIMIT_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_portal_rate_limit_state(state: dict) -> None:
    try:
        _PORTAL_RATE_LIMIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PORTAL_RATE_LIMIT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as ex:
        logger.debug("Could not save portal rate-limit state: %s", ex)


def _parse_utc_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _retry_after_seconds(value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return max(0, min(86400, int(raw)))
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = int((dt.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        return max(0, min(86400, seconds))
    except Exception:
        return None


def _portal_cooldown_default_seconds(host_key: str, status_code: int | None = None) -> int:
    if host_key == "suumo.jp":
        default_minutes = 360.0 if status_code == 403 else 180.0
        minutes = _env_float(
            "SCLAW_SUUMO_COOLDOWN_MINUTES",
            default_minutes,
            min_value=15.0,
            max_value=1440.0,
        )
        return int(minutes * 60)
    return 0


def _portal_cooldown_remaining(host_key: str) -> tuple[int, str]:
    if host_key != "suumo.jp":
        return 0, ""
    state = _load_portal_rate_limit_state().get(host_key)
    if not isinstance(state, dict):
        return 0, ""
    until = _parse_utc_iso(state.get("cooldown_until"))
    if until is None:
        return 0, ""
    remaining = int((until - datetime.now(timezone.utc)).total_seconds())
    if remaining <= 0:
        return 0, ""
    reason = str(state.get("reason") or state.get("last_status") or "cooldown")
    return remaining, reason


def _portal_skip_reason(host_key: str) -> str:
    if host_key == "suumo.jp":
        paused = (os.getenv("SCLAW_SUUMO_PAUSE") or "").strip().lower()
        if paused in ("1", "true", "yes", "on"):
            return "SUUMO crawling is paused by SCLAW_SUUMO_PAUSE"
        ignore_cooldown = (os.getenv("SCLAW_SUUMO_IGNORE_COOLDOWN") or "").strip().lower()
        if ignore_cooldown in ("1", "true", "yes", "on"):
            state = _load_portal_rate_limit_state().get(host_key)
            if isinstance(state, dict) and str(state.get("last_url") or "").startswith("manual:"):
                return ""
        remaining, reason = _portal_cooldown_remaining(host_key)
        if remaining > 0:
            minutes = max(1, int((remaining + 59) / 60))
            return f"SUUMO is cooling down for about {minutes} minutes ({reason})"
    return ""


def _mark_portal_rate_limited(
    host_key: str,
    status_code: int | None,
    url: str,
    retry_after: str | None = None,
    *,
    reason: str = "",
) -> None:
    if host_key != "suumo.jp":
        return
    retry_seconds = _retry_after_seconds(retry_after)
    cooldown = retry_seconds if retry_seconds is not None else _portal_cooldown_default_seconds(host_key, status_code)
    cooldown = max(cooldown, _portal_cooldown_default_seconds(host_key, status_code))
    until = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
    state = _load_portal_rate_limit_state()
    state[host_key] = {
        "cooldown_until": until.isoformat(),
        "last_status": status_code,
        "last_url": str(url or "")[:500],
        "reason": reason or f"HTTP {status_code or 'limited'}",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_portal_rate_limit_state(state)
    logger.warning("Portal %s entered cooldown until %s because %s", host_key, until.isoformat(), state[host_key]["reason"])


def _portal_request_interval_seconds(host_key: str) -> float:
    if host_key == "suumo.jp":
        return _env_float("SCLAW_SUUMO_REQUEST_INTERVAL_SEC", 6.0, min_value=1.5, max_value=60.0)
    return 0.0


def _throttle_portal_request(host_key: str) -> None:
    interval = _portal_request_interval_seconds(host_key)
    if interval <= 0:
        return
    now = time.monotonic()
    last = _PORTAL_LAST_REQUEST_AT.get(host_key)
    if last is not None:
        wait = last + interval - now
        if wait > 0:
            time.sleep(wait)
    _PORTAL_LAST_REQUEST_AT[host_key] = time.monotonic()


def _looks_like_portal_limit_page(host_key: str, response: httpx.Response) -> bool:
    text = (response.text or "")[:80000].lower()
    if host_key == "suumo.jp":
        if response.status_code in (403, 429):
            return True
        markers = (
            "too many requests",
            "access denied",
            "captcha",
            "not a robot",
            "verify that you're not a robot",
            "rate limit",
            "request limit",
            "ただいまアクセスが集中",
            "しばらく時間をおいて",
            "アクセスが制限",
            "不正なアクセス",
        )
        return any(marker in text for marker in markers)
    if host_key == "homes.co.jp":
        if response.status_code in (202, 403, 429, 503):
            return True
        markers = (
            "human verification",
            "awswaf",
            "captcha",
            "bot",
            "challenge",
            "verify you are human",
        )
        return any(marker in text for marker in markers)
    return False


class PortalAccessDenied(RuntimeError):
    """Portal returned bot/verification page; do not ingest this response."""


def _portal_get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    host_key = _host_key(urlparse(str(url or "")).netloc)
    skip_reason = _portal_skip_reason(host_key)
    if skip_reason:
        raise PortalRateLimitActive(skip_reason)
    _throttle_portal_request(host_key)
    response = client.get(url, **kwargs)
    if _looks_like_portal_limit_page(host_key, response):
        if host_key == "suumo.jp":
            _mark_portal_rate_limited(
                host_key,
                response.status_code,
                url,
                response.headers.get("Retry-After"),
                reason=f"HTTP {response.status_code} or limit page",
            )
            raise PortalRateLimitActive(f"{host_key} returned HTTP {response.status_code} or a limit page")
        raise PortalAccessDenied(f"{host_key} returned HTTP {response.status_code} or a verification page")
    return response


def _portal_listing_hub_cap(host_key: str) -> int | None:
    """單次 crawl 最多走訪幾個列表 hub（逾時與站方負載平衡）。at home 站點慢但需覆蓋全國→獨立較高上限。"""
    raw = (os.getenv("SCLAW_PORTAL_HUB_CAP") or "").strip()
    if raw.isdigit():
        return max(4, min(99, int(raw)))
    raw_s = (os.getenv("SCLAW_SUUMO_HUB_CAP") or "").strip()
    if host_key == "suumo.jp":
        if raw_s.isdigit():
            return max(1, min(12, int(raw_s)))
        return 4
    raw_a = (os.getenv("SCLAW_ATHOME_HUB_CAP") or "").strip()
    if host_key == "athome.co.jp":
        if raw_a.isdigit():
            return max(12, min(48, int(raw_a)))
        return 40
    raw_r = (os.getenv("SCLAW_REMAINING_PORTAL_HUB_CAP") or "").strip()
    if host_key in {"realestate.rakuten.co.jp", "yes1.co.jp", "oheya-su.jp"}:
        if raw_r.isdigit():
            return max(2, min(18, int(raw_r)))
        return 4
    if host_key in _SLOW_PORTAL_HOSTS:
        return 12
    return None


def _query_hub_priority_terms(query: str) -> tuple[str, ...]:
    """查詢含地區名時，將對應 hub 前置，避免先掃預設熱門地區。"""
    q = (query or "").strip()
    if not q:
        return ()
    ql = q.lower()

    def hits(*parts: str) -> bool:
        return any((p.lower() in ql) or (p in q) for p in parts)

    terms: list[str] = []
    if hits("北海道", "札幌", "hokkaido", "sapporo"):
        terms.extend(["hokkaido", "/02/01", "/chintai/hokkaido", "/mansion/chuko/hokkaido"])
    if hits("東北", "宮城", "仙台", "青森", "岩手", "秋田", "山形", "福島", "tohoku", "miyagi", "sendai"):
        terms.extend(["tohoku", "miyagi", "/01/04", "/chintai/miyagi"])
    if hits("神奈川", "横浜", "横滨", "川崎", "kanagawa", "yokohama", "kawasaki"):
        terms.extend(["kanagawa", "yokohama", "kawasaki", "/03/14"])
    if hits("埼玉", "さいたま", "saitama"):
        terms.extend(["saitama", "/03/11"])
    if hits("千葉", "chiba"):
        terms.extend(["chiba", "/03/12"])
    if hits("東京", "tokyo"):
        terms.extend(["tokyo", "23ku", "/03/13"])
    if hits("首都", "首都圏", "首都圈", "関東", "關東", "kanto"):
        terms.extend(["tokyo", "kanto", "kanagawa", "saitama", "chiba", "23ku", "/03/13", "/03/14", "/03/11", "/03/12"])
    if hits("甲信越", "長野", "新潟", "山梨", "koshinetsu", "nagano", "niigata", "yamanashi"):
        terms.extend(["koshinetsu", "nagano", "niigata", "yamanashi", "/04/15", "/04/19", "/04/20"])
    if hits("北陸", "石川", "富山", "福井", "hokuriku", "ishikawa", "toyama", "fukui", "kanazawa"):
        terms.extend(["hokuriku", "ishikawa", "toyama", "fukui", "/04/16", "/04/17", "/04/18"])
    if hits("東海", "愛知", "名古屋", "岐阜", "三重", "静岡", "tokai", "aichi", "nagoya", "gifu", "mie", "shizuoka"):
        terms.extend(["tokai", "aichi", "nagoya", "gifu", "mie", "shizuoka", "/05/23", "/05/21", "/05/22", "/05/24"])
    if hits("關西", "関西", "大阪", "京都", "兵庫", "kansai", "osaka", "kyoto", "hyogo"):
        terms.extend(["kansai", "osaka", "kyoto", "hyogo", "/06/27", "/06/26", "/06/28"])
    if hits("中國地方", "中国地方", "中國", "中国", "広島", "岡山", "山口", "島根", "鳥取", "chugoku", "hiroshima", "okayama"):
        terms.extend(["chugoku", "hiroshima", "okayama", "yamaguchi", "shimane", "tottori", "/07/34", "/07/33", "/07/35"])
    if hits("四國", "四国", "香川", "愛媛", "高知", "徳島", "shikoku", "kagawa", "ehime", "kochi", "tokushima"):
        terms.extend(["shikoku", "kagawa", "ehime", "kochi", "tokushima", "/08/37", "/08/38", "/08/39", "/08/36"])
    if hits("九州", "福岡", "北九州", "熊本", "長崎", "鹿児島", "大分", "宮崎", "佐賀", "kyushu", "fukuoka", "kitakyushu"):
        terms.extend(["kyushu", "fukuoka", "kumamoto", "kagoshima", "nagasaki", "oita", "miyazaki", "saga", "/09/40", "/09/43"])
    if hits("沖繩", "沖縄", "冲绳", "okinawa", "琉球"):
        terms.extend(["okinawa", "/09/47", "/chintai/okinawa"])
    if not terms:
        return ()
    return tuple(dict.fromkeys(t.lower() for t in terms if t))


def _prioritize_kanto_tokyo_hubs(hub_urls: list[str], query: str) -> list[str]:
    keys = _query_hub_priority_terms(query)
    if not keys:
        return hub_urls
    hi: list[str] = []
    lo: list[str] = []
    for u in hub_urls:
        ul = (u or "").lower()
        if any(k in ul for k in keys):
            hi.append(u)
        else:
            lo.append(u)
    return hi + lo


def _trim_listing_hub_urls(host_key: str, hub_urls: list[str], query: str) -> list[str]:
    ordered = _prioritize_kanto_tokyo_hubs(hub_urls, query)
    cap = _portal_listing_hub_cap(host_key)
    if cap is not None and len(ordered) > cap:
        return ordered[:cap]
    return ordered


def _athome_catalog_max_pages() -> int:
    raw = (os.getenv("SCLAW_ATHOME_CATALOG_MAX_PAGES") or "12").strip()
    try:
        return max(3, min(45, int(raw)))
    except ValueError:
        return 12


def _httpx_timeout_for_portal(host_key: str) -> httpx.Timeout:
    """慢站略縮逾時：單頁卡住較快放棄，避免整批補齊／benchmark 子行程拖到上限。"""
    if host_key in _SLOW_PORTAL_HOSTS:
        return httpx.Timeout(12.0, connect=6.0)
    return httpx.Timeout(18.0, connect=12.0)


def _host_key(netloc: str) -> str:
    h = (netloc or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def _abs_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return None
    full = urljoin(base, href)
    if not full.startswith("http"):
        return None
    return full.split("#")[0]


def _is_suumo_property_url(url: str) -> bool:
    """SUUMO 單一物件詳情 URL。駅別列表 …/ek_30650/、區域 sc_* 目錄等均為 False。"""
    u = (url or "").strip().lower().split("#")[0].split("?", 1)[0].rstrip("/")
    if "suumo.jp" not in u:
        return False
    # /jj/bukken/shosai/ is also a property detail page (often appears in RSS feeds).
    if "/jj/bukken/shosai/" in u:
        return True
    tail = u.split("/")[-1] if u else ""
    # 中古マンション「駅から探す」一覧：路徑末段 ek_數字（例 ek_30650 旗の台）— 非 nc_/jnc_ 詳情頁
    if re.fullmatch(r"ek_\d+", tail or "", flags=re.I):
        return False
    if "/chintai/jnc_" in u or "/chintai/nc_" in u:
        return True
    if "/ikkodate/" in u or "/chukoikkodate/" in u:
        parts = u.split("/")
        if len(parts) >= 5 and parts[-1].startswith(("bc_", "nc_", "jnc_")):
            return True
    if "/ms/shinchiku/" in u or "/ms/chuko/" in u:
        parts = u.split("/")
        if len(parts) >= 6 and parts[-1].startswith(("bc_", "nc_", "jnc_")):
            return True
    return False


def _is_suumo_chintai_listing_hub(url: str) -> bool:
    u = (url or "").strip().lower().split("#")[0].split("?", 1)[0].rstrip("/")
    return "suumo.jp" in u and "/chintai/" in u and not _is_suumo_property_url(u)


def _suumo_chintai_max_pages() -> int:
    raw = (os.getenv("SCLAW_SUUMO_CHINTAI_MAX_PAGES") or "4").strip()
    try:
        return max(1, min(12, int(raw)))
    except ValueError:
        return 4


def _collect_suumo_chintai_listing_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    """SUUMO chintai hubs often expose area/station hubs first; follow a small second layer to jnc_ detail pages."""
    seen_pages: set[str] = set()
    seen_items: set[str] = set()
    queue: list[str] = [hub]
    out: list[str] = []
    max_pages = _suumo_chintai_max_pages()
    for page_url in queue:
        if len(out) >= limit or len(seen_pages) >= max_pages:
            break
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except PortalRateLimitActive:
            break
        except Exception:
            continue
        soup = soup_from_html(r.text)
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full:
                continue
            if _is_suumo_property_url(full) and "/chintai/" in full.lower():
                if full not in seen_items:
                    seen_items.add(full)
                    out.append(full)
                if len(out) >= limit:
                    break
        if len(out) >= limit:
            break
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen_pages or len(queue) >= max_pages:
                continue
            ul = full.lower().split("#")[0].split("?", 1)[0]
            if "suumo.jp" not in ul or "/chintai/" not in ul or _is_suumo_property_url(ul):
                continue
            # Prefer concrete city/station/new-result hubs over top-level navigation.
            if any(tok in ul for tok in ("/sc_", "/ek_", "/new/", "/mansion/", "/apartment/", "/soba/")):
                queue.append(full)
    return out[:limit]


def _is_suumo_bukken_ichiran_hub(url: str) -> bool:
    u = (url or "").strip().lower().split("#")[0]
    if "suumo.jp" not in u:
        return False
    try:
        path = (urlparse(u).path or "").lower()
    except Exception:
        path = u
    return "/jj/bukken/ichiran/" in path


def _suumo_bukken_max_pages() -> int:
    raw = (os.getenv("SCLAW_SUUMO_BUKKEN_MAX_PAGES") or "3").strip()
    try:
        return max(1, min(12, int(raw)))
    except ValueError:
        return 3


def _suumo_page_no(url: str) -> int:
    """Return page number for SUUMO buuken-list pages (pj/pn/page); default 1."""
    try:
        qs = parse_qs(urlparse(str(url or "")).query, keep_blank_values=True)
    except Exception:
        return 1
    for key in ("pj", "pn", "page"):
        raw = (qs.get(key) or [""])[0]
        try:
            v = int(str(raw or "0"))
        except Exception:
            v = 0
        if v > 0:
            return v
    return 1


def _collect_suumo_bukken_ichiran_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    """
    SUUMO buy listing pages under `/jj/bukken/ichiran/...` often contain many detail URLs.
    Follow a small number of pages (pj/pn) to gather enough detail links.
    """
    seen_pages: set[str] = set()
    seen_items: set[str] = set()
    out: list[str] = []
    queue: list[str] = [hub]
    max_pages = _suumo_bukken_max_pages()

    for page_url in queue:
        if len(out) >= limit or len(seen_pages) >= max_pages:
            break
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except PortalRateLimitActive:
            break
        except Exception:
            continue
        raw_text = r.text or ""
        raw_head = raw_text.lstrip()[:600].lower()
        ct = (r.headers.get("content-type") or "").lower()
        looks_rss = (raw_head.startswith("<?xml") and "<rss" in raw_head) or ("xml" in ct and "<rss" in raw_head)
        if looks_rss:
            # RSS feeds embed property pages in <item><link> and expose the HTML listing via channel <link>.
            try:
                for raw in re.findall(r"<item>.*?<link>(.*?)</link>", raw_text, flags=re.I | re.S):
                    full = html.unescape(str(raw or "")).strip()
                    if not full or full in seen_items:
                        continue
                    if not _is_suumo_property_url(full):
                        continue
                    seen_items.add(full)
                    out.append(full)
                    if len(out) >= limit:
                        break
            except Exception:
                pass
            try:
                m = re.search(r"<channel>.*?<link>(.*?)</link>", raw_text, flags=re.I | re.S)
                if m:
                    ch = html.unescape(m.group(1) or "").strip()
                    if ch and ch not in seen_pages and ch not in queue and len(queue) < max_pages:
                        queue.append(ch)
            except Exception:
                pass
            continue

        soup = soup_from_html(raw_text)
        page_root_path = (urlparse(page_url).path or "").rstrip("/") + "/"
        # Detail links.
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen_items:
                continue
            if not _is_suumo_property_url(full):
                continue
            seen_items.add(full)
            out.append(full)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break

        # Pagination / sibling listing pages (pj/pn/page) within the same ichiran endpoint.
        cur_no = _suumo_page_no(page_url)
        candidates: list[tuple[int, str]] = []
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full:
                continue
            if not _is_suumo_bukken_ichiran_hub(full):
                continue
            parsed = urlparse(full)
            if ((parsed.path or "").rstrip("/") + "/") != page_root_path:
                continue
            if full in seen_pages:
                continue
            no = _suumo_page_no(full)
            if no <= cur_no:
                continue
            candidates.append((no, full))
        candidates.sort(key=lambda x: (x[0], x[1]))
        for _no, nxt in candidates:
            if len(queue) >= max_pages:
                break
            if nxt not in queue:
                queue.append(nxt)
    return out[:limit]


def _suumo_city_select_ichiran_hubs_from_html(hub: str, *, html: str, soup: BeautifulSoup) -> list[str]:
    """
    SUUMO city-selector pages (e.g. /chukoikkodate/{pref}/city/) build listing results via a form.
    We synthesize ichiran URLs for the highest-count cities to bootstrap detail crawling.
    """
    form = soup.select_one("form#js-areaSelectForm")
    if not form:
        return []
    hidden: dict[str, str] = {}
    for inp in form.select("input[type=hidden][name]"):
        k = str(inp.get("name") or "").strip()
        if not k:
            continue
        hidden[k] = str(inp.get("value") or "").strip()
    ar = hidden.get("ar", "").strip()
    bs = hidden.get("bs", "").strip()
    ta = hidden.get("ta", "").strip()
    jsp = hidden.get("jspIdFlg", "").strip()
    if not (ar and bs and ta and jsp):
        return []

    m = re.search(r"/jj/bukken/ichiran/([A-Za-z0-9]+)/", str(html or ""))
    if not m:
        return []
    ichiran_id = m.group(1).strip()
    if not ichiran_id:
        return []

    def _count_from_label(t: str) -> int:
        mm = re.search(r"\((\d+)\)", str(t or ""))
        if not mm:
            return 0
        try:
            return int(mm.group(1))
        except Exception:
            return 0

    items: list[tuple[int, str]] = []
    for inp in soup.select("input[name=sc][value]"):
        sc = str(inp.get("value") or "").strip()
        if not sc or not sc.isdigit():
            continue
        lab = soup.find("label", attrs={"for": inp.get("id")}) if inp.get("id") else None
        label_txt = str(lab.get_text(" ", strip=True) if lab else "").strip()
        c = _count_from_label(label_txt)
        if c <= 0:
            continue
        items.append((c, sc))
    if not items:
        return []
    items.sort(key=lambda x: (-int(x[0]), x[1]))

    cap_raw = (os.getenv("SCLAW_SUUMO_CITY_ICHIRAN_CAP") or "3").strip()
    try:
        cap = max(1, min(12, int(cap_raw)))
    except ValueError:
        cap = 3

    base = urljoin(hub, f"/jj/bukken/ichiran/{ichiran_id}/")
    out: list[str] = []
    for _c, sc in items[:cap]:
        q = urlencode({"ar": ar, "bs": bs, "ta": ta, "jspIdFlg": jsp, "sc": sc})
        out.append(f"{base}?{q}")
    return out


def _is_homes_property_url(url: str) -> bool:
    """HOMES 物件詳情：/mansion/b-…、賃貸／chintai/room/{hex}/ 等；排除 city／list 匯總頁。"""
    raw = (url or "").strip()
    ul = raw.lower().split("#")[0]
    u = ul.split("?", 1)[0].rstrip("/")
    if "homes.co.jp" not in u:
        return False
    if "/list/" in u or re.search(r"/[^/]+-city/list", u):
        return False
    if u.endswith("/city") or u.endswith("/list"):
        return False
    # マンション物件詳情（LIFULL HOME'S 分譲・中古共通）
    if re.search(r"/mansion/b-\d{5,}(?:/|$)", u, flags=re.I):
        return True
    if "/chintai/" not in u and "/mansion/" not in u and "/kodate/" not in u and "/ikkodate/" not in u and "/kk/" not in u:
        return False
    tail = (u.split("/")[-1] or "").lower()
    if tail in ("city", "list", "search", "tokyo", "tokyo_23ku", "kanto", "kanagawa", "saitama", "chiba"):
        return False
    # 賃貸物件詳情：全國常見 …/room/{40hex}/；部份為 b- 數字編號或 nc_/jnc_ 形式
    if "/chintai/" in u:
        if re.search(r"/chintai/room/[a-f0-9]{32,}(?:/|$)", u, flags=re.I):
            return True
        if re.search(r"/chintai/b-\d{5,}(?:/|$)", u, flags=re.I):
            return True
        if re.search(r"/chintai/[^?]+\b(?:jnc_|nc_)[0-9_]+", u, flags=re.I):
            return True
        if ".html" in u and re.search(r"/chintai/.+/\d{5,}", u):
            return True
    # 一戶建て等（數字 ID 或 b-）
    if "/kodate/" in u:
        if re.search(r"/kodate/b-\d{6,}(?:/|$)", u, flags=re.I):
            return True
        if re.search(r"/kodate/\d{5,}(?:/|$)", u):
            return True
    if "/ikkodate/" in u:
        if re.search(r"/ikkodate/b-\d{6,}(?:/|$)", u, flags=re.I):
            return True
        if re.search(r"/ikkodate/\d{5,}(?:/|$)", u):
            return True
    if "/kk/" in u and re.search(r"/kk/[0-9a-z_-]{6,}(?:/|$)", u, flags=re.I):
        return True
    if ".html" in u and "/chintai/" in u:
        return True
    return False


def _athome_path_for_match(url: str) -> tuple[str, str]:
    """回傳 (小寫完整 URL（無 hash）、path 去尾 slash 小寫)。"""
    u = (url or "").strip().split("#", 1)[0]
    parsed = urlparse(u.split("?", 1)[0])
    path = (parsed.path or "").rstrip("/").lower()
    return u.lower(), path


def _is_athome_mansion_detail_path(path_lc: str, url_lc_for_html: str) -> bool:
    """path：/mansion… 底下的「單一物件」詳情（排除 tag／地域匯總頁）。"""
    if not path_lc.startswith("/mansion"):
        return False
    if "/tag/" in path_lc or path_lc.endswith("/list"):
        return False
    if re.fullmatch(r"/mansion/\d{5,}", path_lc):
        return True
    if re.fullmatch(r"/mansion/shinchiku/\d{5,}", path_lc):
        return True
    # 深層：/mansion/chuko/{…}/6987613695
    if "/mansion/chuko/" in path_lc and re.match(r"^/mansion/chuko/.+/\d{5,}$", path_lc):
        return True
    if "/mansion/chuko/" in path_lc and (
        ".html" in url_lc_for_html or re.search(r"/mansion/chuko/[^?]+/\d+", url_lc_for_html)
    ):
        return True
    return False


def _is_athome_property_url(url: str) -> bool:
    u = (url or "").strip()
    ul = u.lower()
    if "athome.co.jp" not in ul:
        return False
    if ul.endswith((".pdf", ".jpg", ".png", ".gif", ".webp")):
        return False
    _, path = _athome_path_for_match(u)
    # 一戸建詳情：/kodate/6983375320/（排除 …/…/list 列表）
    if "/list" not in path and re.fullmatch(r"/kodate/\d{5,}", path):
        return True
    if "/chintai/" in ul and (".html" in ul or re.search(r"/chintai/[^/]+/[^/]+/\d+", ul)):
        return True
    if _is_athome_mansion_detail_path(path, ul):
        return True
    return False


def _is_yahoo_mansion_detail_url(url: str) -> bool:
    """Yahoo!不動産 中古マンション詳情：/used/mansion/detail_corp/{id}/ 等。"""
    u = (url or "").strip().lower().split("#")[0].split("?", 1)[0].rstrip("/")
    if "realestate.yahoo.co.jp" not in u or "/used/mansion/search/" in u:
        return False
    return bool(re.search(r"/used/mansion/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$)", u))


def _is_yahoo_land_detail_url(url: str) -> bool:
    """Yahoo!不動産 土地詳情：/land/detail_corp/{id}/ 等。"""
    u = (url or "").strip().lower().split("#")[0].split("?", 1)[0].rstrip("/")
    if "realestate.yahoo.co.jp" not in u or "/land/search/" in u:
        return False
    return bool(re.search(r"/land/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$)", u))


def _is_yahoo_house_detail_url(url: str) -> bool:
    u = (url or "").strip().lower().split("#")[0].split("?", 1)[0].rstrip("/")
    if "realestate.yahoo.co.jp" not in u or "/house/search/" in u:
        return False
    return bool(re.search(r"/(?:used|new)/house/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$)", u))


def _is_yahoo_realestate_property_url(url: str) -> bool:
    """Yahoo!不動産 單一物件（中古マンション or 土地）詳情 URL。"""
    return _is_yahoo_mansion_detail_url(url) or _is_yahoo_land_detail_url(url) or _is_yahoo_house_detail_url(url)


def _is_yahoo_used_mansion_search_hub(hub: str) -> bool:
    """地域検索結果一覧（非物件詳情）。"""
    h = (hub or "").lower().split("#")[0].split("?", 1)[0].rstrip("/")
    return "realestate.yahoo.co.jp" in h and "/used/mansion/search/" in h


def _is_yahoo_land_search_hub(hub: str) -> bool:
    """土地 地域検索結果一覧（非單一物件）。"""
    h = (hub or "").lower().split("#")[0].split("?", 1)[0].rstrip("/")
    return "realestate.yahoo.co.jp" in h and "/land/search/" in h


def _is_yahoo_house_search_hub(hub: str) -> bool:
    h = (hub or "").lower().split("#")[0].split("?", 1)[0].rstrip("/")
    return "realestate.yahoo.co.jp" in h and "/house/search/" in h and ("/used/" in h or "/new/" in h)


def _yahoo_search_url_for_page(hub: str, page: int) -> str:
    """検索結果分頁：?page=2…（官網亦支援 query 疊加）。"""
    base = (hub or "").strip()
    if page <= 1:
        return base
    parts = urlparse(base)
    qs = parse_qs(parts.query, keep_blank_values=True)
    qs["page"] = [str(page)]
    q = urlencode(qs, doseq=True)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, q, parts.fragment))


def _collect_yahoo_used_mansion_search_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    """自 /used/mansion/search/… 列表（含 page 分頁）收集中古マンション詳情連結。"""
    seen: set[str] = set()
    out: list[str] = []
    empty_rounds = 0
    for page in range(1, 45):
        if len(out) >= limit:
            break
        page_url = _yahoo_search_url_for_page(hub, page)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except Exception:
            break
        soup = soup_from_html(r.text)
        page_new = 0
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen:
                continue
            if not _is_yahoo_mansion_detail_url(full):
                continue
            seen.add(full)
            out.append(full)
            page_new += 1
            if len(out) >= limit:
                break
        if page_new == 0:
            empty_rounds += 1
            if empty_rounds >= 2:
                break
        else:
            empty_rounds = 0
    return out[:limit]


def _collect_yahoo_land_search_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    """自 /land/search/… 列表（含 page 分頁）收集土地詳情連結。"""
    seen: set[str] = set()
    out: list[str] = []
    empty_rounds = 0
    for page in range(1, 45):
        if len(out) >= limit:
            break
        page_url = _yahoo_search_url_for_page(hub, page)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except Exception:
            break
        soup = soup_from_html(r.text)
        page_new = 0
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen:
                continue
            if not _is_yahoo_land_detail_url(full):
                continue
            seen.add(full)
            out.append(full)
            page_new += 1
            if len(out) >= limit:
                break
        if page_new == 0:
            empty_rounds += 1
            if empty_rounds >= 2:
                break
        else:
            empty_rounds = 0
    return out[:limit]


def _collect_yahoo_house_search_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    empty_rounds = 0
    for page in range(1, 45):
        if len(out) >= limit:
            break
        page_url = _yahoo_search_url_for_page(hub, page)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except Exception:
            break
        soup = soup_from_html(r.text)
        page_new = 0
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen:
                continue
            if not _is_yahoo_house_detail_url(full):
                continue
            seen.add(full)
            out.append(full)
            page_new += 1
            if len(out) >= limit:
                break
        if page_new == 0:
            empty_rounds += 1
            if empty_rounds >= 2:
                break
        else:
            empty_rounds = 0
    return out[:limit]


def _yahoo_search_total_count(soup: BeautifulSoup) -> int:
    t = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    # 例：相模原市中央区の土地 229件、川崎市川崎区の中古マンション 132件
    m0 = re.search(r"の土地[^0-9]{0,16}([0-9]{1,5})\s*件", t)
    if m0:
        return max(0, int(m0.group(1)))
    m = re.search(r"(?:中古マンション|物件)\s*([0-9]{1,5})\s*件", t)
    if m:
        return max(0, int(m.group(1)))
    # 備援：最先出現的「NN件」
    m2 = re.search(r"([0-9]{1,5})\s*件", t)
    if m2:
        return max(0, int(m2.group(1)))
    return 0


def _yahoo_detail_quick_facts(digest: str) -> str:
    blob = str(digest or "")
    picks: list[str] = []
    for label, pat in (
        ("価格", r"価格:\s*([^:]{1,80}?)(?=\s+[^\s:]{1,16}:|$)"),
        ("所在地", r"所在地:\s*([^:]{1,150}?)(?=\s+[^\s:]{1,16}:|$)"),
        ("交通", r"交通:\s*([^:]{1,180}?)(?=\s+[^\s:]{1,16}:|$)"),
        ("間取り", r"間取り:\s*([^:]{1,50}?)(?=\s+[^\s:]{1,16}:|$)"),
        ("専有面積", r"専有面積:\s*([^:]{1,80}?)(?=\s+[^\s:]{1,16}:|$)"),
        ("土地面積", r"土地面積\s*[:：]?\s*([^:]{1,80}?)(?=\s+[^\s:]{1,16}:|$)"),
        ("築年月", r"築年月:\s*([^:]{1,60}?)(?=\s+[^\s:]{1,16}:|$)"),
        ("所在階", r"所在階:\s*([^:]{1,40}?)(?=\s+[^\s:]{1,16}:|$)"),
    ):
        m = re.search(pat, blob)
        if m:
            picks.append(f"{label}: {m.group(1).strip()}")
    return " / ".join(picks[:6])[:420]


def _yahoo_search_enrich_from_details(
    client: httpx.Client, search_url: str, limit: int = 24, *, land: bool = False
) -> tuple[str, list[str]]:
    """
    Yahoo 検索一覧 URL 補強（中古マンション / 土地）：
    1) 抽出全件數與 detail URL 清單
    2) 回抓前 N 筆 detail，彙整可檢索欄位摘要
    """
    lines: list[str] = []
    imgs: list[str] = []
    if land:
        detail_urls = _collect_yahoo_land_search_links(client, search_url, limit=max(40, limit * 2))
    else:
        detail_urls = _collect_yahoo_used_mansion_search_links(client, search_url, limit=max(40, limit * 2))
    if detail_urls:
        lines.append(f"- 搜尋頁可解析案件數（detail URL）：{len(detail_urls)}")
    for du in detail_urls[: max(1, limit)]:
        try:
            r = _portal_get(client, du)
            if r.status_code >= 400:
                continue
            soup = soup_from_html(r.text)
            title = ""
            og = soup.find("meta", property="og:title")
            if og and og.get("content"):
                title = str(og["content"]).strip()
            if not title and soup.title and soup.title.string:
                title = str(soup.title.string).strip()
            dig = _yahoo_mansion_detail_kv_digest(soup)
            facts = _yahoo_detail_quick_facts(dig)
            if title or facts:
                lines.append(f"- {(title or 'Yahoo 物件')} | {facts}".strip(" |"))
            for u in _jsonld_image_urls(soup, du, limit=3):
                if u not in imgs:
                    imgs.append(u)
            for m in soup.select("meta[property='og:image'][content]"):
                u = str(m.get("content") or "").strip()
                if u and u.startswith("http") and u not in imgs:
                    imgs.append(u)
            if len(imgs) >= 28:
                break
        except Exception:
            continue
    return "\n".join(lines[: max(8, limit + 2)]), imgs[:28]


_YAHOO_DETAIL_TABLE_KEYS = frozenset(
    {
        "価格",
        "所在地",
        "交通",
        "間取り",
        "専有面積",
        "築年月",
        "所在階",
        "バルコニー",
        "管理費",
        "修繕積立金",
        "総戸数",
        "土地面積",
        "建物面積",
    }
)


def _yahoo_mansion_detail_kv_digest(soup: BeautifulSoup) -> str:
    """Yahoo 詳情頁欄位摘要：table + 頁面文字雙路徑抽取，供正則命中。"""
    chunks: list[str] = []
    all_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    all_text = all_text[:12000]
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            tds = tr.find_all("td")
            if len(ths) == 1 and len(tds) == 1:
                k = ths[0].get_text(" ", strip=True)
                v = tds[0].get_text(" ", strip=True)
                if not k or not v:
                    continue
                k_head = (k.split()[0] if k else "")[:16]
                if k in _YAHOO_DETAIL_TABLE_KEYS or k_head in _YAHOO_DETAIL_TABLE_KEYS:
                    chunks.append(f"{k}: {v[:520]}")
                continue
            # 某些 Yahoo 表格是一列多組 th/td；逐欄配對嘗試收集
            if len(ths) >= 1 and len(tds) >= 1:
                pairs = min(len(ths), len(tds))
                for i in range(pairs):
                    k = ths[i].get_text(" ", strip=True)
                    v = tds[i].get_text(" ", strip=True)
                    if not k or not v:
                        continue
                    k_head = (k.split()[0] if k else "")[:16]
                    if k in _YAHOO_DETAIL_TABLE_KEYS or k_head in _YAHOO_DETAIL_TABLE_KEYS:
                        chunks.append(f"{k}: {v[:520]}")
    # table 抓不到時，直接從全文抓 Yahoo 常見標籤句式
    patterns: list[tuple[str, str]] = [
        ("価格", r"価格\s*[:：]?\s*([0-9,]+(?:\.[0-9]+)?\s*万円|未定)"),
        ("管理費", r"管理費\s*[:：]?\s*([0-9,]+\s*円/?月?)"),
        ("修繕積立金", r"修繕積立金\s*[:：]?\s*([0-9,]+\s*円/?月?)"),
        ("所在地", r"所在地\s*[:：]?\s*(.+?)(?=\s*交通\s*[:：]?)"),
        ("交通", r"交通\s*[:：]?\s*(.+?)(?=\s*間取り|\s*専有面積|\s*所在階|\s*主要採光面)"),
        ("間取り", r"間取り\s*[:：]?\s*([0-9]+(?:S)?(?:LDK|DK|K|R))"),
        ("専有面積", r"専有面積\s*[:：]?\s*([0-9][0-9.,]*\s*(?:m2|㎡).{0,20}?)"),
        ("所在階", r"所在階\s*[:：]?\s*([0-9]{1,2}\s*階)"),
        ("築年月", r"築年月\s*[:：]?\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月(?:\s*\([^)]+\))?)"),
        ("総戸数", r"総戸数\s*[:：]?\s*([0-9０-９]{1,6}\s*戸)"),
        ("物件管理番号", r"物件管理番号\s*[:：]?\s*([A-Za-z0-9\-]{6,40})"),
    ]
    for label, pat in patterns:
        m = re.search(pat, all_text)
        if m:
            chunks.append(f"{label}: {str(m.group(1) or '').strip()[:520]}")
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()[:2600]


def _yahoo_update_meta_from_soup(soup: BeautifulSoup) -> list[str]:
    """掲載／更新／次回予定（詳情頁文末付近）。"""
    t = soup.get_text(" ", strip=True)
    t = re.sub(r"\s+", " ", t)
    out: list[str] = []
    for label, pat in (
        ("情報掲載開始日", r"情報掲載開始日\s*[:：]\s*([0-9年月日\s/.·\-]{8,28})"),
        ("情報更新日", r"情報更新日\s*[:：]\s*([0-9年月日\s/.·\-]{8,28})"),
        ("次回更新予定日", r"次回更新予定日\s*[:：]\s*([0-9年月日\s/.·\-]{8,28})"),
    ):
        m = re.search(pat, t)
        if m:
            out.append(f"{label}: {m.group(1).strip()[:36]}")
    return out[:4]


def _is_rakuten_realestate_property_url(url: str) -> bool:
    u = (url or "").lower()
    if "realestate.rakuten.co.jp" not in u:
        return False
    if re.search(r"/(?:usedmansion|newdetached|useddetached|land)/id-[0-9a-z_-]+/?(?:$|\?)", u):
        return True
    if "/bukken/" in u or "/mansion/bk_" in u:
        return True
    if "/mansion/" in u and re.search(r"/[0-9]{5,}", u):
        return True
    if "/chintai/" in u and re.search(r"(/room/|/nc_|/jnc_)", u):
        return True
    return False


def _is_yes1_property_url(url: str) -> bool:
    u = (url or "").lower()
    if "yes1.co.jp" not in u and "yes-station.jp" not in u:
        return False
    # Exclude area listing pages such as /contents/search_area/mansion/.../01100-city[/2]
    if "/contents/search_area/" in u:
        return False
    if any(x in u for x in ("/bukken/", "/chintai/", "/room/", "/detail", "/estate/")):
        return True
    if "/mansion/" in u:
        return re.search(r"/(?:bk_|detail|estate|bukken)", u) is not None or re.search(r"/[0-9]{5,}", u) is not None
    return False


def _is_oheya_su_property_url(url: str) -> bool:
    u = (url or "").lower()
    if "oheya-su.jp" not in u and "oheyasuu.com" not in u:
        return False
    if any(x in u for x in ("/chintai/", "/mansion/", "/bukken/", "/detail", "/room/")):
        return re.search(r"/[0-9]{4,}", u) is not None
    return False


def _property_url_predicate(host_key: str, url: str) -> bool:
    if host_key == "suumo.jp":
        return _is_suumo_property_url(url)
    if host_key == "homes.co.jp":
        return _is_homes_property_url(url)
    if host_key == "athome.co.jp":
        return _is_athome_property_url(url)
    if host_key == "realestate.yahoo.co.jp":
        return _is_yahoo_realestate_property_url(url)
    if host_key == "realestate.rakuten.co.jp":
        return _is_rakuten_realestate_property_url(url)
    if host_key in ("yes1.co.jp", "yes-station.jp"):
        return _is_yes1_property_url(url)
    if host_key in ("oheya-su.jp", "oheyasuu.com"):
        return _is_oheya_su_property_url(url)
    return False


def _query_extra_listing_hubs(host_key: str, query: str) -> list[str]:
    """Map user query tokens to extra listing-hub URLs (search-style entry pages)."""
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()

    def hits(*parts: str) -> bool:
        return any((p.lower() in ql) or (p in q) for p in parts)

    extra: list[str] = []
    if host_key == "athome.co.jp":
        if q.startswith("http") and "athome.co.jp" in ql and "/kodate/chuko/" in ql and "/list" in ql:
            extra.append(q.split("#")[0].strip())
        if (q.startswith("http://") or q.startswith("https://")) and "athome.co.jp" in ql and "/mansion/" in ql:
            q0 = q.split("#")[0].strip()
            if _is_athome_mansion_catalog_hub(q0):
                extra.append(q0)
        if hits("kodate", "戶建", "戸建", "一戶", "一戸", "戸建て", "独栋", "獨棟", "detached"):
            extra.append("https://www.athome.co.jp/chintai/kodate/")
            extra.append("https://www.athome.co.jp/kodate/chuko/tokyo/list/")
        if hits("shinchiku", "新築", "新房", "新建"):
            extra.append("https://www.athome.co.jp/mansion/shinchiku/")
        if hits("中古", "chuko", "二手", "中古マンション", "マンション"):
            extra.append("https://www.athome.co.jp/mansion/chuko/")
            extra.append("https://www.athome.co.jp/mansion/chuko/kanto/")
        if hits("長野", "nagano", "木曽", "kiso"):
            extra.append("https://www.athome.co.jp/kodate/chuko/nagano/list/")
        if hits("北海道", "札幌", "hokkaido", "sapporo"):
            extra.extend(
                [
                    "https://www.athome.co.jp/chintai/hokkaido/",
                    "https://www.athome.co.jp/kodate/chuko/hokkaido/list/",
                    "https://www.athome.co.jp/mansion/chuko/hokkaido/",
                ]
            )
        if hits("東北", "宮城", "仙台", "青森", "岩手", "秋田", "山形", "福島", "tohoku", "miyagi", "sendai"):
            extra.extend(
                [
                    "https://www.athome.co.jp/chintai/miyagi/",
                    "https://www.athome.co.jp/mansion/chuko/tohoku/",
                ]
            )
        if hits("甲信越", "長野", "新潟", "山梨", "koshinetsu", "nagano", "niigata", "yamanashi"):
            extra.extend(
                [
                    "https://www.athome.co.jp/kodate/chuko/nagano/list/",
                    "https://www.athome.co.jp/mansion/chuko/koshinetsu/",
                    "https://www.athome.co.jp/chintai/nagano/",
                ]
            )
        if hits("北陸", "石川", "富山", "福井", "hokuriku", "ishikawa", "toyama", "fukui", "kanazawa"):
            extra.extend(
                [
                    "https://www.athome.co.jp/mansion/chuko/hokuriku/",
                    "https://www.athome.co.jp/chintai/ishikawa/",
                ]
            )
        if hits("東海", "愛知", "名古屋", "岐阜", "三重", "静岡", "tokai", "aichi", "nagoya"):
            extra.extend(
                [
                    "https://www.athome.co.jp/chintai/aichi/",
                    "https://www.athome.co.jp/mansion/chuko/tokai/",
                    "https://www.athome.co.jp/kodate/chuko/aichi/list/",
                ]
            )
        if hits("中国地方", "中國地方", "中国", "中國", "広島", "岡山", "山口", "島根", "鳥取", "chugoku", "hiroshima", "okayama"):
            extra.extend(
                [
                    "https://www.athome.co.jp/chintai/hiroshima/",
                    "https://www.athome.co.jp/mansion/chuko/chugoku/",
                ]
            )
        if hits("四国", "四國", "香川", "愛媛", "高知", "徳島", "shikoku", "kagawa", "ehime"):
            extra.extend(
                [
                    "https://www.athome.co.jp/mansion/chuko/shikoku/",
                    "https://www.athome.co.jp/chintai/kagawa/",
                ]
            )
        if hits("九州", "福岡", "北九州", "熊本", "長崎", "鹿児島", "大分", "宮崎", "佐賀", "kyushu", "fukuoka"):
            extra.extend(
                [
                    "https://www.athome.co.jp/chintai/fukuoka/",
                    "https://www.athome.co.jp/mansion/chuko/kyushu/",
                    "https://www.athome.co.jp/kodate/chuko/fukuoka/list/",
                ]
            )
        if hits("沖繩", "沖縄", "冲绳", "okinawa", "琉球"):
            extra.extend(
                [
                    "https://www.athome.co.jp/chintai/okinawa/",
                    "https://www.athome.co.jp/mansion/chuko/okinawa/",
                ]
            )
        if hits("東京", "tokyo"):
            extra.extend(
                [
                    "https://www.athome.co.jp/chintai/tokyo/tokyo/",
                    "https://www.athome.co.jp/mansion/shinchiku/tokyo/tokyo/",
                ]
            )
        if hits("神奈川", "kanagawa", "橫濱", "横滨", "横浜"):
            extra.append("https://www.athome.co.jp/chintai/kanagawa/")
        if hits("大阪", "osaka"):
            extra.append("https://www.athome.co.jp/chintai/osaka/osaka/")
        if hits("首都", "首都圏", "首都圈", "関東", "關東", "kanto"):
            extra.extend(
                [
                    "https://www.athome.co.jp/mansion/shinchiku/tag/5minute/shutoken/list/",
                    "https://www.athome.co.jp/mansion/chuko/kanto/",
                    "https://www.athome.co.jp/chintai/tokyo/tokyo/",
                    "https://www.athome.co.jp/chintai/kanagawa/",
                    "https://www.athome.co.jp/chintai/saitama/",
                    "https://www.athome.co.jp/chintai/chiba/",
                ]
            )
        if hits("駅近", "駅 近", "徒歩5分", "徒歩 5", "5分以内", "walk", "station", "shutoken"):
            extra.append("https://www.athome.co.jp/mansion/shinchiku/tag/5minute/shutoken/list/")
    elif host_key == "homes.co.jp":
        if hits("首都", "首都圏", "首都圈", "関東", "關東", "kanto"):
            extra.extend(
                [
                    "https://www.homes.co.jp/mansion/chuko/tokyo/tokyo_23ku/city/",
                    "https://www.homes.co.jp/mansion/chuko/kanagawa/city/",
                    "https://www.homes.co.jp/mansion/chuko/saitama/city/",
                    "https://www.homes.co.jp/mansion/chuko/chiba/city/",
                    "https://www.homes.co.jp/mansion/shinchiku/tokyo/city/",
                ]
            )
        # 使用者貼上 HOMES マンション city／list 列表網址（http(s)）
        if (q.startswith("http://") or q.startswith("https://")) and "homes.co.jp" in ql and "/mansion/" in ql:
            q0 = q.split("#")[0].strip()
            if re.search(r"/mansion/(?:shinchiku|chuko)/", ql) and (
                re.search(r"/(?:city|list)(?:/|\?|$)", ql)
                or ql.rstrip("/").endswith("city")
                or ql.rstrip("/").endswith("list")
            ):
                extra.append(q0)
        # 使用者貼上 HOMES 賃貸列表（例：沖縄 …/chintai/okinawa/list?…）
        if (q.startswith("http://") or q.startswith("https://")) and "homes.co.jp" in ql and "/chintai/" in ql and "/list" in ql:
            extra.append(q.split("#")[0].strip())
        if hits("kodate", "戶建", "戸建", "一戶", "一戸", "独栋", "獨棟"):
            extra.append("https://www.homes.co.jp/chintai/kodate/")
        if hits("shinchiku", "新築", "新房", "新建", "分譲"):
            extra.extend(
                [
                    "https://www.homes.co.jp/mansion/shinchiku/",
                    "https://www.homes.co.jp/mansion/shinchiku/tokyo/city/",
                    "https://www.homes.co.jp/mansion/shinchiku/kanagawa/city/",
                    "https://www.homes.co.jp/mansion/shinchiku/saitama/city/",
                    "https://www.homes.co.jp/mansion/shinchiku/chiba/city/",
                ]
            )
        if hits("中古マンション", "中古", "chuko", "二手"):
            extra.append("https://www.homes.co.jp/mansion/chuko/tokyo/tokyo_23ku/city/")
            extra.append("https://www.homes.co.jp/mansion/chuko/hokkaido/sapporo_kita-city/list/")
            extra.append("https://www.homes.co.jp/mansion/chuko/fukuoka/fukuoka/city/")
            extra.append("https://www.homes.co.jp/mansion/chuko/kumamoto/kumamoto-city/city/")
        if hits("東京", "tokyo"):
            extra.append("https://www.homes.co.jp/chintai/tokyo/tokyo_23ku/city/")
            extra.append("https://www.homes.co.jp/mansion/shinchiku/tokyo/city/")
        if hits("北海道", "hokkaido", "札幌", "sapporo", "北区", "kita-city"):
            extra.append("https://www.homes.co.jp/mansion/chuko/hokkaido/sapporo_kita-city/list/")
        if hits(
            "沖繩",
            "冲绳",
            "沖縄",
            "沖縄県",
            "okinawa",
            "琉球",
        ):
            extra.append("https://www.homes.co.jp/chintai/okinawa/list/")
        if hits(
            "九州",
            "福岡",
            "fukuoka",
            "熊本",
            "kumamoto",
            "北九州",
            "kitakyushu",
            "鹿児島",
            "kagoshima",
            "長崎",
            "nagasaki",
            "大分",
            "oita",
            "宮崎",
            "miyazaki",
            "佐賀",
            "saga",
        ):
            extra.append("https://www.homes.co.jp/mansion/chuko/fukuoka/fukuoka/city/")
            extra.append("https://www.homes.co.jp/mansion/chuko/kumamoto/kumamoto-city/city/")
        if hits("中国地方", "中國地方", "中国", "中國", "広島", "岡山", "山口", "島根", "鳥取", "chugoku", "hiroshima", "okayama"):
            extra.extend(
                [
                    "https://www.homes.co.jp/chintai/hiroshima/",
                    "https://www.homes.co.jp/chintai/okayama/",
                ]
            )
        if hits("四国", "四國", "香川", "愛媛", "高知", "徳島", "shikoku", "kagawa", "ehime"):
            extra.extend(
                [
                    "https://www.homes.co.jp/chintai/kagawa/",
                    "https://www.homes.co.jp/chintai/ehime/",
                ]
            )
    elif host_key == "suumo.jp":
        if hits("kodate", "戶建", "戸建", "一戸", "独栋", "獨棟"):
            extra.append("https://suumo.jp/chintai/kodate/tokyo/")
        if hits("shinchiku", "新築", "新房"):
            extra.append("https://suumo.jp/ms/shinchiku/tokyo/")
    elif host_key == "realestate.yahoo.co.jp":
        if (q.startswith("http://") or q.startswith("https://")) and "realestate.yahoo.co.jp" in ql and "/used/mansion/search/" in ql:
            extra.append(q.split("#")[0].strip())
        if (q.startswith("http://") or q.startswith("https://")) and "realestate.yahoo.co.jp" in ql and "/land/search/" in ql:
            extra.append(q.split("#")[0].strip())
        if hits("北九州", "小倉", "kitakyushu", "kokura", "40106"):
            extra.append("https://realestate.yahoo.co.jp/used/mansion/search/09/40/40106/")
        if hits("神奈川", "横浜", "横滨", "川崎", "kanagawa", "yokohama", "kawasaki"):
            extra.append("https://realestate.yahoo.co.jp/used/mansion/search/03/14/")
        if hits("埼玉", "さいたま", "saitama"):
            extra.append("https://realestate.yahoo.co.jp/used/mansion/search/03/11/")
        if hits("千葉", "chiba"):
            extra.append("https://realestate.yahoo.co.jp/used/mansion/search/03/12/")
        if hits("東京", "tokyo"):
            extra.append("https://realestate.yahoo.co.jp/used/mansion/search/03/13/")
        # 路徑為都道府縣代碼，不含「東京」字樣 — 關鍵字含關東／首都圏時須顯式補 hub
        if hits("首都", "首都圏", "首都圈", "関東", "關東", "kanto"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/03/13/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/03/14/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/03/11/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/03/12/",
                ]
            )
        if hits("關西", "関西", "大阪", "京都", "兵庫"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/06/27/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/06/26/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/06/28/",
                ]
            )
        if hits("北海道", "札幌", "hokkaido", "sapporo"):
            extra.extend(["https://realestate.yahoo.co.jp/used/mansion/search/02/01/"])
        if hits("東北", "宮城", "仙台", "青森", "岩手", "秋田", "山形", "福島", "tohoku", "miyagi", "sendai"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/01/04/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/01/07/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/01/02/",
                ]
            )
        if hits("甲信越", "長野", "新潟", "山梨", "koshinetsu", "nagano", "niigata", "yamanashi"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/04/20/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/04/15/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/04/19/",
                ]
            )
        if hits("北陸", "石川", "富山", "福井", "hokuriku", "ishikawa", "toyama", "fukui", "kanazawa"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/04/17/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/04/16/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/04/18/",
                ]
            )
        if hits("東海", "愛知", "名古屋", "岐阜", "三重", "静岡", "tokai", "aichi", "nagoya"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/05/23/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/05/22/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/05/21/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/05/24/",
                ]
            )
        if hits("中国地方", "中國地方", "中国", "中國", "広島", "岡山", "山口", "島根", "鳥取", "chugoku", "hiroshima", "okayama"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/07/34/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/07/33/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/07/35/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/07/32/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/07/31/",
                ]
            )
        if hits("四国", "四國", "香川", "愛媛", "高知", "徳島", "shikoku", "kagawa", "ehime"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/08/37/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/08/38/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/08/39/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/08/36/",
                ]
            )
        if hits("九州", "福岡", "北九州", "熊本", "長崎", "鹿児島", "大分", "宮崎", "佐賀", "kyushu", "fukuoka"):
            extra.extend(
                [
                    "https://realestate.yahoo.co.jp/used/mansion/search/09/40/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/09/40/40106/",
                    "https://realestate.yahoo.co.jp/used/mansion/search/09/43/",
                ]
            )
        if hits("沖繩", "沖縄", "冲绳", "okinawa", "琉球"):
            extra.append("https://realestate.yahoo.co.jp/used/mansion/search/09/47/")
    elif host_key in ("yes1.co.jp", "yes-station.jp"):
        if hits("北海道", "札幌", "hokkaido", "sapporo"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/hokkaido/")
        if hits("東北", "宮城", "仙台", "tohoku", "miyagi", "sendai"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/miyagi/")
        if hits("東京", "tokyo"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/tokyo/")
        if hits("神奈川", "横浜", "横滨", "川崎", "kanagawa", "yokohama", "kawasaki"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/kanagawa/")
        if hits("埼玉", "saitama"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/saitama/")
        if hits("千葉", "chiba"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/chiba/")
        if hits("東海", "愛知", "名古屋", "tokai", "aichi", "nagoya"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/aichi/")
        if hits("関西", "關西", "大阪", "osaka"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/osaka/")
        if hits("京都", "kyoto"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/kyoto/")
        if hits("兵庫", "hyogo"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/hyogo/")
        if hits("中国地方", "中國地方", "中国", "中國", "広島", "hiroshima"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/hiroshima/")
        if hits("九州", "福岡", "北九州", "fukuoka", "kyushu"):
            extra.append("https://www.yes1.co.jp/contents/search_area/mansion/fukuoka/")
    elif host_key in ("oheya-su.jp", "oheyasuu.com"):
        if hits("北海道", "札幌", "hokkaido", "sapporo"):
            extra.append("https://www.oheya-su.jp/chintai/hokkaido/")
        if hits("東北", "宮城", "仙台", "tohoku", "miyagi", "sendai"):
            extra.append("https://www.oheya-su.jp/chintai/miyagi/")
        if hits("東京", "tokyo"):
            extra.append("https://www.oheya-su.jp/chintai/tokyo/")
        if hits("神奈川", "横浜", "横滨", "川崎", "kanagawa", "yokohama", "kawasaki"):
            extra.append("https://www.oheya-su.jp/chintai/kanagawa/")
        if hits("埼玉", "saitama"):
            extra.append("https://www.oheya-su.jp/chintai/saitama/")
        if hits("千葉", "chiba"):
            extra.append("https://www.oheya-su.jp/chintai/chiba/")
        if hits("関西", "關西", "大阪", "osaka"):
            extra.append("https://www.oheya-su.jp/chintai/osaka/")
        if hits("京都", "kyoto"):
            extra.append("https://www.oheya-su.jp/chintai/kyoto/")
        if hits("兵庫", "hyogo"):
            extra.append("https://www.oheya-su.jp/chintai/hyogo/")
        if hits("東海", "愛知", "名古屋", "tokai", "aichi", "nagoya"):
            extra.append("https://www.oheya-su.jp/chintai/aichi/")
        if hits("九州", "福岡", "北九州", "fukuoka", "kyushu"):
            extra.append("https://www.oheya-su.jp/chintai/fukuoka/")
        if hits("中国地方", "中國地方", "中国", "中國", "広島", "hiroshima"):
            extra.append("https://www.oheya-su.jp/chintai/hiroshima/")
    return extra


def _merged_listing_hub_urls(host_key: str, query: str) -> list[str]:
    q = (query or "").strip()
    base = list(_query_extra_listing_hubs(host_key, q)) if q else []
    base.extend(LISTING_HUB_PAGES.get(host_key, []))
    seen: set[str] = set()
    out: list[str] = []
    for h in base:
        h = (h or "").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out


def _is_athome_kodate_chuko_list_hub(hub: str) -> bool:
    h = (hub or "").lower()
    return "athome.co.jp" in h and "/kodate/chuko/" in h and "/list" in h


def _is_athome_mansion_catalog_hub(hub: str) -> bool:
    """chuko／shinchiku の目録・地域・tag/list；`/mansion/{id}` 或 `/mansion/shinchiku/{id}` は詳情ページのため除外。"""
    hl = (hub or "").strip().lower().split("#")[0].split("?", 1)[0].rstrip("/")
    if "athome.co.jp" not in hl:
        return False
    parsed = urlparse(hl)
    path = (parsed.path or "").rstrip("/").lower()
    if _is_athome_mansion_detail_path(path, hl):
        return False
    if "/mansion/chuko" in path:
        return True
    if "/mansion/shinchiku" in path:
        return True
    return False


def _athome_list_url_for_page(hub: str, page: int) -> str:
    """athome 中古戶建列表分頁：以 query 參數 PAGE=（1-based）遞增。"""
    base = (hub or "").strip()
    if page <= 1:
        return base
    parts = urlparse(base)
    qs = parse_qs(parts.query, keep_blank_values=True)
    qs["PAGE"] = [str(page)]
    q = urlencode(qs, doseq=True)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, q, parts.fragment))


def _collect_athome_mansion_catalog_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    """自 athome 中古／新築マンション匯總頁（含 PAGE= 分頁）收集 /mansion/{物件番号}/ 詳情。"""
    seen: set[str] = set()
    out: list[str] = []
    empty_rounds = 0
    max_pages = _athome_catalog_max_pages()
    for page in range(1, max_pages + 1):
        if len(out) >= limit:
            break
        page_url = _athome_list_url_for_page(hub, page)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except Exception:
            break
        soup = soup_from_html(r.text)
        page_new = 0
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen:
                continue
            if not _is_athome_property_url(full):
                continue
            _, mpl = _athome_path_for_match(full)
            if "/mansion/" not in mpl:
                continue
            if "/kodate/" in mpl or "/chintai/" in mpl:
                continue
            if not _is_athome_mansion_detail_path(mpl, full.lower()):
                continue
            seen.add(full)
            out.append(full)
            page_new += 1
            if len(out) >= limit:
                break
        if page_new == 0:
            empty_rounds += 1
            if empty_rounds >= 2:
                break
        else:
            empty_rounds = 0
    return out[:limit]


def _collect_athome_kodate_chuko_list_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    """自 athome 中古一戶建列表（含分頁）收集 /kodate/{id}/ 詳情連結。"""
    seen: set[str] = set()
    out: list[str] = []
    empty_rounds = 0
    max_pages = _athome_catalog_max_pages()
    for page in range(1, max_pages + 1):
        if len(out) >= limit:
            break
        page_url = _athome_list_url_for_page(hub, page)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except Exception:
            break
        soup = soup_from_html(r.text)
        page_new = 0
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen:
                continue
            if not _is_athome_property_url(full):
                continue
            seen.add(full)
            out.append(full)
            page_new += 1
            if len(out) >= limit:
                break
        if page_new == 0:
            empty_rounds += 1
            if empty_rounds >= 2:
                break
        else:
            empty_rounds = 0
    return out[:limit]


def _is_homes_mansion_catalog_hub(hub: str) -> bool:
    """HOME'S 需分頁掃描的目錄：マンション shinchiku／chuko city・list；賃貸 …/chintai/県/list 等。"""
    raw = (hub or "").strip()
    if not raw:
        return False
    hs = raw.lower().split("#")[0].split("?", 1)[0]
    if "homes.co.jp" not in hs:
        return False
    # 詳情／單一物牛 URL 不當 hub
    if "/chintai/room/" in hs:
        return False
    if re.search(r"/chintai/b-[0-9]", hs):
        return False
    if "/mansion/b-" in hs or "/kodate/b-" in hs or "/ikkodate/b-" in hs:
        return False
    try:
        path = (urlparse(raw).path or "").lower().rstrip("/")
    except Exception:
        path = ""

    # 賃貸區域一覧（詳情常為 …/chintai/room/{hex}/，見 _is_homes_property_url）
    if "/chintai/" in hs:
        if "/list/" in hs or (path.endswith("/city")):
            return True
        segs = [s for s in path.split("/") if s]
        if len(segs) >= 2 and segs[0] == "chintai":
            if len(segs) == 2:
                return segs[1] != "room"
            return True

    # マンション新築／中古
    if not any(
        x in hs
        for x in (
            "/mansion/shinchiku/",
            "/mansion/chuko/",
            "/kodate/shinchiku/",
            "/kodate/chuko/",
            "/ikkodate/shinchiku/",
            "/ikkodate/chuko/",
        )
    ):
        return False
    if "/list/" in hs:
        return True
    return bool(re.search(r"/(?:city|list)(?:/|$)", hs)) or hs.rstrip("/").endswith("city")


def _homes_catalog_url_for_page(hub: str, page: int) -> str:
    """HOME'S 列表常見 query：page=（1-based）。"""
    base = (hub or "").strip()
    if page <= 1:
        return base
    parts = urlparse(base)
    qs = parse_qs(parts.query, keep_blank_values=True)
    qs["page"] = [str(page)]
    q = urlencode(qs, doseq=True)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, q, parts.fragment))


def _collect_homes_mansion_catalog_links(client: httpx.Client, hub: str, limit: int) -> list[str]:
    """自 HOME'S 分頁目錄收集物件詳情（/mansion/b-…、/chintai/room/… 等）。"""
    seen: set[str] = set()
    out: list[str] = []
    empty_rounds = 0
    for page in range(1, 45):
        if len(out) >= limit:
            break
        page_url = _homes_catalog_url_for_page(hub, page)
        try:
            r = _portal_get(client, page_url)
            r.raise_for_status()
        except Exception:
            break
        soup = soup_from_html(r.text)
        page_new = 0
        for a in soup.select("a[href]"):
            full = _abs_url(page_url, a.get("href"))
            if not full or full in seen:
                continue
            if not _is_homes_property_url(full):
                continue
            seen.add(full)
            out.append(full)
            page_new += 1
            if len(out) >= limit:
                break
        if page_new == 0:
            empty_rounds += 1
            if empty_rounds >= 2:
                break
        else:
            empty_rounds = 0
    return out[:limit]


def _homes_update_meta_from_soup(soup: BeautifulSoup) -> list[str]:
    """HOMES 詳情頁：情報更新日／次回更新予定日等，寫入摘要前綴供站內新鮮度判斷。"""
    t = soup.get_text(" ", strip=True)
    t = re.sub(r"\s+", " ", t)
    out: list[str] = []
    m1 = re.search(
        r"情報更新日\s*[:：]\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9./\-]{6,14})",
        t,
    )
    if not m1:
        m1 = re.search(r"情報更新\s*[:：]\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9./\-]{6,14})", t)
    if m1:
        out.append(f"情報更新日: {m1.group(1).strip()[:40]}")
    m2 = re.search(
        r"次回更新予定日\s*[:：]\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9/.\s年月日]{6,24})",
        t,
    )
    if not m2:
        m2 = re.search(r"次回更新予定\s*[:：]\s*([0-9/.\s年月日]{6,24})", t)
    if m2:
        out.append(f"次回更新予定日: {m2.group(1).strip()[:28]}")
    return out[:4]


_HOMES_DETAIL_TABLE_KEYS = frozenset(
    {
        "価格",
        "所在地",
        "交通",
        "間取り",
        "専有面積",
        "バルコニー面積",
        "管理費等",
        "修繕積立金",
        "所在階",
        "築年月",
        "総戸数",
        "建物構造",
        "現況",
        "引渡し",
        "物件番号",
    }
)


def _homes_mansion_detail_kv_digest(soup: BeautifulSoup) -> str:
    """HOME'S 詳情頁：把重要欄位抽成 key:value 串，供後續正則穩定命中。"""
    chunks: list[str] = []
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:12000]
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            tds = tr.find_all("td")
            if not ths or not tds:
                continue
            pairs = min(len(ths), len(tds))
            for i in range(pairs):
                k = ths[i].get_text(" ", strip=True)
                v = tds[i].get_text(" ", strip=True)
                if not k or not v:
                    continue
                k0 = (k.split()[0] if k else "").strip()
                if k in _HOMES_DETAIL_TABLE_KEYS or k0 in _HOMES_DETAIL_TABLE_KEYS:
                    chunks.append(f"{k}: {v[:520]}")
    # table 之外補捉常見首頁資訊
    for label, pat in (
        ("価格", r"価格\s*[:：]?\s*([0-9,]+(?:\.[0-9]+)?\s*万円|未定)"),
        ("間取り", r"間取り\s*[:：]?\s*([0-9]+(?:S)?(?:LDK|DK|K|R))"),
        ("専有面積", r"専有面積\s*[:：]?\s*([0-9][0-9.,]*\s*(?:m2|㎡).{0,20}?)"),
        ("所在地", r"所在地\s*[:：]?\s*(.+?)(?=\s*交通\s*[:：]?)"),
        ("交通", r"交通\s*[:：]?\s*(.+?)(?=\s*間取り|\s*専有面積|\s*バルコニー|\s*築年月)"),
    ):
        m = re.search(pat, text)
        if m:
            chunks.append(f"{label}: {str(m.group(1) or '').strip()[:520]}")
    return re.sub(r"\s+", " ", " ".join(chunks)).strip()[:2800]


def _collect_links_from_hub_list(client: httpx.Client, host_key: str, hub_urls: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for hub in hub_urls:
        if len(out) >= limit:
            break
        if host_key == "homes.co.jp" and _is_homes_mansion_catalog_hub(hub):
            for full in _collect_homes_mansion_catalog_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        if host_key == "athome.co.jp" and _is_athome_mansion_catalog_hub(hub):
            for full in _collect_athome_mansion_catalog_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        if host_key == "athome.co.jp" and _is_athome_kodate_chuko_list_hub(hub):
            for full in _collect_athome_kodate_chuko_list_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        if host_key == "suumo.jp" and _is_suumo_chintai_listing_hub(hub):
            for full in _collect_suumo_chintai_listing_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        if host_key == "suumo.jp" and _is_suumo_bukken_ichiran_hub(hub):
            for full in _collect_suumo_bukken_ichiran_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        if host_key == "realestate.yahoo.co.jp" and _is_yahoo_used_mansion_search_hub(hub):
            for full in _collect_yahoo_used_mansion_search_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        if host_key == "realestate.yahoo.co.jp" and _is_yahoo_land_search_hub(hub):
            for full in _collect_yahoo_land_search_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        if host_key == "realestate.yahoo.co.jp" and _is_yahoo_house_search_hub(hub):
            for full in _collect_yahoo_house_search_links(client, hub, limit - len(out)):
                if full in seen:
                    continue
                seen.add(full)
                out.append(full)
            continue
        try:
            r = _portal_get(client, hub)
            r.raise_for_status()
        except PortalRateLimitActive:
            break
        except Exception:
            continue
        soup = soup_from_html(r.text)
        for a in soup.select("a[href]"):
            full = _abs_url(hub, a.get("href"))
            if not full or full in seen:
                continue
            if not _property_url_predicate(host_key, full):
                continue
            seen.add(full)
            out.append(full)
            if len(out) >= limit:
                break
        if host_key == "suumo.jp" and len(out) < limit:
            # Region/pref/city/ensen hubs often require one more jump to `/jj/bukken/ichiran/...`.
            ichiran_hubs: list[str] = []
            for a in soup.select("a[href]"):
                href = str(a.get("href") or "").strip()
                if "/jj/bukken/ichiran/" not in href:
                    continue
                full = _abs_url(hub, href)
                if not full or full in ichiran_hubs:
                    continue
                ichiran_hubs.append(full)
                if len(ichiran_hubs) >= 6:
                    break
            try:
                for extra in _suumo_city_select_ichiran_hubs_from_html(hub, html=r.text, soup=soup):
                    if extra and extra not in ichiran_hubs:
                        ichiran_hubs.append(extra)
            except Exception:
                pass
            for ih in ichiran_hubs[:10]:
                if len(out) >= limit:
                    break
                for full in _collect_suumo_bukken_ichiran_links(client, ih, limit - len(out)):
                    if full in seen:
                        continue
                    seen.add(full)
                    out.append(full)
                    if len(out) >= limit:
                        break
    httpx_count = len(out)
    try:
        from src.portal_property_playwright import maybe_append_playwright_links

        maybe_append_playwright_links(
            host_key,
            hub_urls,
            httpx_count=httpx_count,
            limit=limit,
            seen=seen,
            out=out,
            max_hubs=(2 if host_key == "suumo.jp" else (6 if host_key in _SLOW_PORTAL_HOSTS else 12)),
        )
    except Exception:
        pass
    return out


def _collect_links_from_hubs(client: httpx.Client, host_key: str, limit: int) -> list[str]:
    return _collect_links_from_hub_list(client, host_key, LISTING_HUB_PAGES.get(host_key, []), limit)


def _jsonld_first_name(soup: BeautifulSoup) -> str:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates: list[dict] = []
        if isinstance(data, dict):
            candidates.append(data)
        elif isinstance(data, list):
            for x in data:
                if isinstance(x, dict):
                    candidates.append(x)
        for d in candidates:
            name = d.get("name")
            if isinstance(name, str) and len(name.strip()) >= 4:
                return name.strip()[:200]
    return ""


def _jsonld_image_urls(soup: BeautifulSoup, page_url: str, limit: int = 10) -> list[str]:
    out: list[str] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes: list[dict] = []
        if isinstance(data, dict):
            nodes.append(data)
            graph = data.get("@graph")
            if isinstance(graph, list):
                for g in graph:
                    if isinstance(g, dict):
                        nodes.append(g)
        elif isinstance(data, list):
            for d in data:
                if isinstance(d, dict):
                    nodes.append(d)
        for d in nodes:
            img_val = d.get("image")
            cands: list[str] = []
            if isinstance(img_val, str):
                cands.append(img_val)
            elif isinstance(img_val, list):
                for x in img_val:
                    if isinstance(x, str):
                        cands.append(x)
                    elif isinstance(x, dict):
                        u = str(x.get("url") or "").strip()
                        if u:
                            cands.append(u)
            elif isinstance(img_val, dict):
                u = str(img_val.get("url") or "").strip()
                if u:
                    cands.append(u)
            for c in cands:
                u = urljoin(page_url, c.strip()).split("#", 1)[0]
                if not u.startswith("http"):
                    continue
                if u not in out:
                    out.append(u)
                if len(out) >= limit:
                    return out
    return out


def _athome_breadcrumb_labels_from_ld_json(data: object) -> list[str]:
    """自 JSON-LD 樹挑出 BreadcrumbList → 依 position 排序的 name 列表。"""
    names: list[str] = []

    def walk(x: object) -> None:
        if isinstance(x, dict):
            if str(x.get("@type") or "").strip() == "BreadcrumbList":
                els = x.get("itemListElement")
                if isinstance(els, list):
                    keyed: list[tuple[int, str]] = []
                    for li in els:
                        if not isinstance(li, dict):
                            continue
                        pos = li.get("position")
                        try:
                            ipos = int(pos) if pos is not None else 0
                        except Exception:
                            ipos = 0
                        it = li.get("item")
                        label = ""
                        if isinstance(it, dict):
                            label = str(it.get("name") or "").strip()
                        elif isinstance(it, str):
                            label = it.strip()
                        if label:
                            keyed.append((ipos, label))
                    for _, lab in sorted(keyed, key=lambda t: (t[0], t[1])):
                        names.append(lab)
                return
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(data)
    return names


def _breadcrumb_line_from_ld_json_scripts(soup: BeautifulSoup) -> str:
    """schema.org BreadcrumbList（JSON-LD）→ HOMES／AtHome 等共通。"""
    for script in soup.select('script[type="application/ld+json"]'):
        raw = (script.string or script.get_text() or "").strip()
        if not raw or "BreadcrumbList" not in raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        trail = _athome_breadcrumb_labels_from_ld_json(data)
        if trail:
            return " > ".join(trail[:32])
    return ""


def _homes_breadcrumb_augment_floor_from_title(title: str, trail_line: str) -> str:
    """標題括号內 `/2階/` 類片段併入最後一層（例：… > ルミナス鷺沼 → … > ルミナス鷺沼 2階）。"""
    tl = str(title or "")
    tl = tl.replace("\u3010", "[").replace("\u3011", "]")
    m = re.search(r"\[[^\]]*?/\s*(\d{1,2})\s*階\s*/", tl)
    if not m:
        return trail_line.strip()
    fl = str(int(m.group(1)))
    raw = trail_line.strip()
    if not raw:
        return raw
    if fl + "階" in raw or " " + fl + "階" in raw.replace("／", "/"):
        return raw
    parts = [p.strip() for p in raw.split(">") if str(p).strip()]
    if not parts:
        return raw
    last = parts[-1]
    if last.endswith(fl + "階") or last.endswith("階"):
        return raw
    parts[-1] = f"{last} {fl}階"
    return " > ".join(parts)


def _homes_breadcrumb_line_from_soup(soup: BeautifulSoup, title: str = "") -> str:
    """LIFULL HOME'S（賃貸 room 詳情等）：JSON-LD 麵包屑＋標題括号階數。"""
    base = _breadcrumb_line_from_ld_json_scripts(soup)
    if base:
        return _homes_breadcrumb_augment_floor_from_title(title or "", base)
    return ""


def _homes_chintai_dl_kv_digest(soup: BeautifulSoup) -> str:
    """HOMES 賃貸詳情：新版版面以 dl/dt/dd 呈現物件概要（非傳統 table）。"""
    allow = frozenset(
        {
            "所在地",
            "交通",
            "賃料",
            "家賃",
            "管理費",
            "管理費等",
            "共益費・管理費",
            "敷金・礼金（保証金）",
            "敷金",
            "礼金",
            "保証金",
            "礼金・敷金・保証金",
            "間取り",
            "専有面積",
            "所在階",
            "築年月",
            "総戸数",
            "建物構造",
            "駐車場",
            "主要採光面",
            "バルコニー",
            "バルコニー面積",
            "方角",
            "契約形態",
            "引渡時期",
            "現況",
            "建物種別",
            "位置",
            "入居条件",
        }
    )
    chunks: list[str] = []
    seen_kv: set[str] = set()
    for dt in soup.find_all("dt"):
        key = dt.get_text(" ", strip=True)
        if len(key) < 2 or len(key) > 36:
            continue
        if "徒歩" in key and len(key) > 12:
            continue
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        val = dd.get_text(" ", strip=True)
        if len(val) < 1:
            continue
        if allow and key not in allow:
            kw_ok = False
            for tok in ("所在地", "交通", "賃料", "管理費", "敷金", "礼金", "間取", "専有面積", "築年", "階建", "階/", "駅"):
                if tok in key:
                    kw_ok = True
                    break
            if not kw_ok:
                continue
        val = val[:560]
        kvs = f"{key}: {val}"
        nk = key[:48]
        if nk in seen_kv:
            continue
        seen_kv.add(nk)
        chunks.append(kvs)
        if len(chunks) >= 52:
            break
    merged = " ".join(f"{x} |" for x in chunks)
    return re.sub(r"\s+", " ", merged).strip("| ").strip()[:3200]


def _athome_shinchiku_detail_kv_digest(soup: BeautifulSoup, *, limit: int = 80) -> list[str]:
    """AtHome 新築マンション detail pages keep the full overview in dt/dd rows."""
    allow = frozenset(
        {
            "掲載会社",
            "所在地",
            "交通",
            "引渡可能時期",
            "価格",
            "価格 (予定)",
            "価格（予定）",
            "専有面積",
            "間取り",
            "販売スケジュール",
            "販売情報",
            "販売戸数",
            "販売戸数 (予定)",
            "販売戸数（予定）",
            "管理費",
            "管理費 (予定)",
            "管理費（予定）",
            "管理準備金",
            "管理準備金 (予定)",
            "管理準備金（予定）",
            "修繕積立金",
            "修繕積立金 (予定)",
            "修繕積立金（予定）",
            "修繕積立基金",
            "修繕積立基金 (予定)",
            "修繕積立基金（予定）",
            "完成時期",
            "総戸数",
            "敷地面積",
            "構造・階建て",
            "用途地域",
            "管理形態/ 管理員の勤務形態",
            "建築確認番号",
            "施設",
            "売主",
            "販売代理",
            "管理会社",
            "施工会社",
        }
    )
    key_tokens = (
        "所在地",
        "交通",
        "引渡",
        "価格",
        "専有面積",
        "間取り",
        "販売",
        "管理費",
        "修繕",
        "完成",
        "総戸数",
        "構造",
        "階建",
        "駐車",
        "施設",
        "管理会社",
        "施工会社",
    )
    skip_keys = {
        "借りる",
        "買う",
        "建てる",
        "調べる",
        "マンション",
        "一戸建て",
        "土地",
    }
    rows: list[str] = []
    seen: set[str] = set()
    for dt in soup.find_all("dt"):
        key = re.sub(r"\s+", " ", dt.get_text(" ", strip=True)).strip()
        if not key or key in skip_keys or len(key) > 48:
            continue
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        val = re.sub(r"\s+", " ", dd.get_text(" ", strip=True)).strip()
        if not val:
            continue
        if key not in allow and not any(tok in key for tok in key_tokens):
            continue
        if key == "施設" and "駐車場" not in val:
            continue
        val = val.replace(" 地図を見る", "").strip()
        val = re.sub(r"\s*月々の支払い額を見る\s*", " ", val).strip()
        val = re.sub(r"\s*間取りを見る\(\d+件\)\s*", " ", val).strip()
        row = f"{key}: {val[:720].strip()}"
        dedupe_key = row[:180]
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _athome_breadcrumb_line_from_soup(soup: BeautifulSoup) -> str:
    """AtHome 詳情／特集共通：優先 schema.org breadcrumb JSON-LD，其次 topicPath DOM。"""
    line = _breadcrumb_line_from_ld_json_scripts(soup)
    if line:
        return line
    ul = soup.select_one("ul.c-topicPathList")
    if ul:
        parts = [p.strip() for p in ul.get_text(" > ", strip=True).split(">") if p.strip()]
        if parts:
            return " > ".join(parts[:32])
    return ""


def _athome_publication_iso_from_soup(soup: BeautifulSoup) -> str | None:
    """athome 物件頁「情報提供日」→ ISO 日期（供一年內篩選）。"""
    t = soup.get_text(" ", strip=True)
    t = re.sub(r"\s+", " ", t)
    m = re.search(r"情報提供日\s*[:：]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1990 <= y <= 2036 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m2 = re.search(r"情報提供日\s*[:：]\s*(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", t)
    if m2:
        y, mo, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        if 1990 <= y <= 2036 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    mw = re.search(r"情報提供日\s*[:：]\s*令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t)
    if mw:
        y = 2018 + int(mw.group(1))
        mo, d = int(mw.group(2)), int(mw.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _stale_athome_listing_by_body(body: str, *, max_age_days: int = 370) -> bool:
    m = re.search(r"掲載情報日\(ISO\):\s*(\d{4})-(\d{2})-(\d{2})", body or "")
    if not m:
        return False
    try:
        pub = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except Exception:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(max_age_days))
    return pub < cutoff


def _extract_listing_facts(item_url: str, title: str, desc: str, text: str) -> list[str]:
    blob = " ".join([title or "", desc or "", text or ""])
    blob = re.sub(r"\s+", " ", blob)
    facts: list[str] = []

    def _pick(pats: list[str]) -> str:
        for p in pats:
            m = re.search(p, blob, flags=re.I)
            if m:
                return str(m.group(1) or "").strip()
        return ""

    price = _pick(
        [
            r"(?:価格|賃料|家賃|價[格錢]|總價)\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]+)?\s*(?:万円|万|萬|円|日圓|日元))",
            r"(?:販売予定価格|参考価格|予定価格)\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]+)?\s*万円)",
            r"([0-9][0-9,]*(?:\.[0-9]+)?\s*万円)",
        ]
    )
    if not price:
        price = _pick(
            [
                r"価格\s*[:：]?\s*(未定|要確認|調整中|未公開|公開前|依頼時確認)",
                r"販売価格\s*[:：]?\s*(未定|要確認|調整中|未公開)",
            ]
        )
    layout = _pick(
        [
            r"(?:間取り|格局|layout)\s*[:：]?\s*([0-9]+(?:S)?(?:LDK|DK|K|R))",
            r"\b([0-9]+(?:S)?(?:LDK|DK|K|R))\b",
        ]
    )
    area = _pick(
        [
            r"(?:専有面積|建物面積|面積|坪數|坪)\s*[:：]?\s*([0-9][0-9.,]*\s*(?:m2|㎡|平米|坪))",
            r"([0-9][0-9.,]*\s*(?:㎡|平米|坪))",
        ]
    )
    age = _pick(
        [
            r"(?:築年数|築|屋齡|屋龄)\s*[:：]?\s*([0-9]{1,3}\s*年)",
            r"(新築)",
        ]
    )
    floor = _pick(
        [
            r"(?:所在階|階数|樓層|楼层)\s*[:：]?\s*([0-9]{1,2}\s*/\s*[0-9]{1,2}\s*(?:階|樓|层))",
            r"([0-9]{1,2}\s*階(?:建)?\s*/\s*[0-9]{1,2}\s*階建?)",
            r"(?:所在階\s*/\s*階建)\s*([0-9]{1,2}\s*階\s*/\s*(?:地上階数)?[0-9]{1,3}\s*階)",
            r"所在階\s*[:：]?\s*([0-9]{1,2}\s*階)",
        ]
    )
    btype = _pick(
        [
            r"(?:物件種目|建物種別|種別|建物類型|類型)\s*[:：]?\s*([^\s]{1,16})",
            r"(マンション|アパート|一戸建て|戸建て|中古マンション|新築マンション|大樓|公寓|透天)",
        ]
    )
    pname = _pick(
        [
            r"物件名\s*[:：]?\s*(.+?)(?=\s*(?:販売価格|価格|所在地|沿線|専有面積|間取))",
        ]
    )
    addr = _pick(
        [
            r"所在地\s*[:：]?\s*(.+?)(?=\s*(?:沿線|販売価格|価格|専有面積|間取|バルコニー|築年月))",
        ]
    )
    rail = _pick(
        [
            r"沿線・駅\s*[:：]?\s*(.+?)(?=\s*(?:専有面積|建物面積|間取り|バルコニー|築年月|掲載|土地面積))",
            r"沿線\s*・\s*駅\s*[:：]?\s*(.+?)(?=\s*(?:専有面積|間取り|バルコニー|築年月|土地面積))",
        ]
    )
    if not rail:
        rail = _pick(
            [
                r"交通\s*[:：]?\s*(.+?)(?=\s*(?:土地面積|建物面積|所在地|間取り|築年月|価格|ＪＲ|JR))",
            ]
        )
    balcony = _pick(
        [
            r"バルコニー(?:面積)?\s*[:：]?\s*([0-9.～\-]+\s*m2?|[0-9.]+\s*㎡)",
        ]
    )
    chiku = _pick(
        [
            r"築年月\s*[:：]?\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月(?:\s*\([^)]+\))?)",
            r"築年月\s*[:：]?\s*([0-9]{4}/[0-9]{1,2})",
        ]
    )
    land_area = _pick(
        [
            r"土地面積\s*[:：]?\s*([0-9][0-9.,]*\s*(?:m2|㎡|ｍ２))",
            r"土地面積\s*[:：]?\s*([0-9][0-9.,]*\s*㎡)",
        ]
    )
    building_area = _pick(
        [
            r"建物面積\s*[:：]?\s*([0-9][0-9.,]*\s*(?:m2|㎡|ｍ２))",
            r"建物面積\s*[:：]?\s*([0-9][0-9.,]*\s*㎡)",
        ]
    )
    if not price:
        price = _pick(
            [
                r"価格\s*[:：]?\s*(.+?)(?=所在地|交通|間取り|専有面積|完成予定|次回|情報更新|土地面積|建物面積)",
                r"一般販売住戸\s*[:：]?\s*(.+?)(?=所在地|交通|間取り|専有面積|完成予定|次回|情報更新|土地面積|建物面積|価格帯)",
                r"価格帯\s*[:：]?\s*(.+?)(?=所在地|交通|間取|完成予定|次回|情報更新|$)",
            ]
        )
    kanryo = _pick(
        [
            r"完成予定\s*[:：]?\s*(.+?)(?=\s*(?:間取り|専有面積|所在地|交通|価格|次回|情報更新|土地面積|建物面積|総戸数|$))",
            r"(?:完成時期|引渡時期|竣工予定)\s*[:：]?\s*(.+?)(?=\s*(?:所在地|交通|間取り|価格|総戸数|次回|$))",
        ]
    )
    soko = _pick(
        [
            r"総戸数\s*[:：]?\s*([0-9０-９]{1,6}\s*戸)",
            r"総戸数\s*[:：]?\s*([0-9０-９]{1,6}\s*戸?)",
            r"総戸数\s*[:：]?\s*([0-9]{1,6})",
        ]
    )

    def _add(label: str, val: str, *, cap: int = 40) -> None:
        v = (val or "").strip()
        if not v:
            return
        facts.append(f"{label}: {v[:cap]}")

    _add("總價", price, cap=120)
    _add("格局", layout)
    _add("坪數", area)
    _add("屋齡", age)
    _add("樓層", floor)
    _add("類型", btype)
    _add("物件名", pname)
    _add("所在地", addr)
    _add("沿線駅", rail)
    _add("土地面積", land_area)
    _add("建物面積", building_area)
    _add("陽台", balcony)
    _add("築年月", chiku)
    _add("完成予定", kanryo, cap=100)
    _add("総戸数", soko, cap=48)
    facts.append(f"來源網址: {item_url}")
    return facts[:20]


def _suumo_detail_kv_digest(soup: BeautifulSoup) -> list[str]:
    """SUUMO 單物件頁（nc_/cj_ 等）抽取詳細表格欄位，供後續欄位解析器使用。"""
    out: list[str] = []
    seen: set[str] = set()

    def _push(label: str, value: str) -> None:
        k = re.sub(r"\s+", "", str(label or ""))
        v = re.sub(r"\s+", " ", str(value or "")).strip()
        if not k or not v:
            return
        if len(k) > 40 or len(v) > 380:
            return
        row = f"{k}: {v}"
        if row in seen:
            return
        seen.add(row)
        out.append(row)

    for tr in soup.select("table tr"):
        ths = tr.select("th")
        tds = tr.select("td")
        if ths and tds:
            n = min(len(ths), len(tds))
            for i in range(n):
                _push(ths[i].get_text(" ", strip=True), tds[i].get_text(" ", strip=True))
            if len(out) >= 90:
                break
            continue
        # 新版常見：雙 td（左欄項目名、右欄值），無 th
        if len(tds) == 2 and not ths:
            a = tds[0].get_text(" ", strip=True)
            b = tds[1].get_text(" ", strip=True)
            if 1 <= len(a) <= 36 and len(b) >= 1 and len(b) <= 360 and "http" not in a.lower():
                _push(a, b)
        elif len(tds) == 4 and not ths:
            _push(tds[0].get_text(" ", strip=True), tds[1].get_text(" ", strip=True))
            _push(tds[2].get_text(" ", strip=True), tds[3].get_text(" ", strip=True))
        if len(out) >= 90:
            break
    # dt/dd 與 dl 內 div 常並存於詳細區，一律合併（不再僅在列數少時才跑）
    for dl in soup.select("dl"):
        dts = dl.select("dt")
        dds = dl.select("dd")
        n = min(len(dts), len(dds))
        for i in range(n):
            _push(dts[i].get_text(" ", strip=True), dds[i].get_text(" ", strip=True))
        if len(out) >= 90:
            break
    return out[:90]


def _suumo_resize_decoded_src_is_property(src_dec: str) -> bool:
    """decode 後的 src 路徑是否像物件相簿（排除業者／人像）。"""
    low = unquote(str(src_dec or "")).strip().lower()
    if not low:
        return False
    if any(
        x in low
        for x in (
            "gazo/kaisha",
            "/kaisha/",
            "tantou",
            "staff",
            "/edit/assets/",
            "pagetop",
            "include/",
            "logo",
            "icon",
            "spacer",
        )
    ):
        return False
    return any(
        x in low
        for x in (
            "gazo/bukken",
            "gazo/chuko",
            "gazo/ms",
            "/jj/chuko/",
            "/jj/item/",
            "/mnk/",
            "gaikan",
            "naikan",
            "madori",
            "interior",
            "living",
            "resizeimage",
        )
    )


def _urls_from_srcset_attr(val: str, page_url: str) -> list[str]:
    """解析 img/srcset 或 picture/source，優先回傳最高解析度候選。"""
    out: list[str] = []
    if not val or not str(val).strip():
        return out
    candidates: list[tuple[float, int, str]] = []
    for chunk in str(val).split(","):
        part = chunk.strip().split()
        if not part:
            continue
        u = urljoin(page_url, part[0].strip()).split("#", 1)[0]
        if not u.startswith("http"):
            continue
        rank = 1.0
        if len(part) > 1:
            desc = part[1].strip().lower()
            try:
                if desc.endswith("w"):
                    rank = float(desc[:-1])
                elif desc.endswith("x"):
                    rank = float(desc[:-1]) * 1000.0
            except Exception:
                rank = 1.0
        candidates.append((rank, len(candidates), u))
    for _, _, u in sorted(candidates, key=lambda row: (row[0], -row[1]), reverse=True):
        if u not in out:
            out.append(u)
    return out


def _suumo_detail_image_urls(soup: BeautifulSoup, page_url: str, limit: int = 20) -> list[str]:
    """SUUMO 詳情頁優先抽房源圖（gazo/bukken），排除公司/人像圖。"""
    out: list[str] = []

    def _is_suumo_listing_visual(lu: str) -> bool:
        if "suumo." not in lu:
            return False
        if any(
            x in lu
            for x in (
                "gazo/kaisha",
                "/kaisha/",
                "tantou",
                "staff",
                "/edit/assets/",
                "pagetop",
                "include/",
                "logo",
                "icon",
                "spacer",
            )
        ):
            return False
        return any(
            x in lu
            for x in (
                "gazo/bukken",
                "resizeimage",
                "/jj/chuko/",
                "/jj/item/",
                "/mnk/",
                "gaikan",
                "naikan",
                "madori",
                "interior",
            )
        )

    for img in soup.select("img[src], img[data-src], img[data-original], img[data-lazy-src]"):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            raw = str(img.get(attr) or "").strip()
            if not raw:
                continue
            u = urljoin(page_url, raw).split("#", 1)[0]
            if not u.startswith("http"):
                continue
            lu = u.lower()
            if not _is_suumo_listing_visual(lu):
                continue
            if u not in out:
                out.append(u)
            if len(out) >= limit:
                return out
        for attr in ("srcset", "data-srcset"):
            for u in _urls_from_srcset_attr(str(img.get(attr) or ""), page_url):
                lu = u.lower()
                if not _is_suumo_listing_visual(lu):
                    continue
                if u not in out:
                    out.append(u)
                if len(out) >= limit:
                    return out
    for src in soup.select("picture source[srcset], picture source[data-srcset]"):
        for attr in ("srcset", "data-srcset"):
            for u in _urls_from_srcset_attr(str(src.get(attr) or ""), page_url):
                lu = u.lower()
                if not _is_suumo_listing_visual(lu):
                    continue
                if u not in out:
                    out.append(u)
                if len(out) >= limit:
                    return out
    return out


def _suumo_property_image_urls_from_raw_html(raw_html: str, page_url: str, limit: int = 36) -> list[str]:
    """從 raw HTML（含 script 內嵌）的 resizeImage(src=...) 與直連圖檔還原 SUUMO 房源圖。"""
    out: list[str] = []
    text = str(raw_html or "")
    # 主：resizeImage 完整 URL（script／JSON 內亦常出現）
    for m in re.findall(
        r"https?://img\d+\.suumo\.(?:jp|com)/jj/resizeImage\?[^\"'\s<>]+",
        text,
        flags=re.I,
    ):
        u = m.replace("&amp;", "&")
        if "," in u:
            left, right = u.split(",", 1)
            if left.startswith("http") and right and not right.startswith(("http://", "https://")):
                u = left
        pu = urlparse(u)
        qs = parse_qs(pu.query, keep_blank_values=True)
        src = str((qs.get("src") or [""])[0] or "").strip()
        src_dec = unquote(src)
        if not _suumo_resize_decoded_src_is_property(src_dec):
            continue
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            return out
    # 備援：相對路徑 src=gazo%2F…（不限定僅 bukken）
    for m in re.findall(r"src=(gazo%2F[^&\"'\s<>]{8,})", text, flags=re.I):
        src_dec = unquote(m)
        if not _suumo_resize_decoded_src_is_property(src_dec):
            continue
        uu = urljoin(page_url, src_dec)
        if uu not in out:
            out.append(uu)
        if len(out) >= limit:
            return out
    # 新版偶見直連 jj/chuko 圖檔（無 resizeImage 包一層）
    for m in re.findall(
        r"https?://img\d+\.suumo\.(?:jp|com)/jj/chuko/[^\"'\s<>]+\.(?:jpe?g|png|webp)",
        text,
        flags=re.I,
    ):
        u = m.replace("&amp;", "&").split(",", 1)[0]
        lu = u.lower()
        if any(x in lu for x in ("kaisha", "tantou", "staff")):
            continue
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            return out
    return out


def _suumo_listing_id_from_url(item_url: str) -> str:
    """Return SUUMO nc id from modern path or legacy jj/bukken query URLs."""
    u = str(item_url or "").strip()
    m = re.search(r"/(?:j?nc_|nc_)([0-9A-Za-z]{6,})", u)
    if m:
        return str(m.group(1) or "").strip()
    try:
        raw = str((parse_qs(urlparse(u).query, keep_blank_values=True).get("nc") or [""])[0] or "").strip()
    except Exception:
        raw = ""
    return raw if re.fullmatch(r"[0-9A-Za-z]{6,}", raw) else ""


def _suumo_goo_buy_category(item_url: str) -> str:
    u = str(item_url or "").lower()
    if "/chukoikkodate/" in u or "bs=021" in u:
        return "uh"
    if "/ikkodate/" in u or "bs=020" in u:
        return "bh"
    if "/ms/chuko/" in u or "bs=011" in u:
        return "um"
    if "/ms/shinchiku/" in u or "bs=010" in u:
        return "bm"
    return ""


@lru_cache(maxsize=1)
def _homes_city_code_lookup_for_suumo_goo() -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    """Build city slug/Japanese-label lookup from cached HOME'S city catalogues."""
    slug_to_code: dict[str, str] = {}
    labels: list[tuple[str, str]] = []
    try:
        data = json.loads((DATA_DIR / "homes_kodate_chuko_city_cache.json").read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return {}, ()
    for pref_payload in data.values():
        if not isinstance(pref_payload, dict):
            continue
        for group in pref_payload.get("groups") or []:
            if not isinstance(group, dict):
                continue
            for item in group.get("items") or []:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("id") or "").strip()
                label = str(item.get("label") or "").strip()
                url = str(item.get("url") or "").strip()
                if not code:
                    continue
                if label:
                    labels.append((label, code))
                try:
                    path = urlparse(url).path
                except Exception:
                    path = ""
                m = re.search(r"/([^/]+)-city/list/?$", path)
                if m:
                    slug = str(m.group(1) or "").strip().lower()
                    if slug:
                        slug_to_code[slug] = code
                        slug_to_code[slug.replace("_", "")] = code
    labels.sort(key=lambda row: len(row[0]), reverse=True)
    return slug_to_code, tuple(labels)


def _suumo_city_code_for_goo(item_url: str, fallback_context: str = "") -> str:
    slug_to_code, labels = _homes_city_code_lookup_for_suumo_goo()
    try:
        parsed = urlparse(str(item_url or ""))
    except Exception:
        parsed = urlparse("")
    try:
        sc = str((parse_qs(parsed.query, keep_blank_values=True).get("sc") or [""])[0] or "").strip()
    except Exception:
        sc = ""
    if re.fullmatch(r"\d{5}", sc):
        return sc
    try:
        path = parsed.path
    except Exception:
        path = ""
    m = re.search(r"/sc_([^/?#]+)/", path)
    if m:
        slug = str(m.group(1) or "").strip().lower()
        for key in (slug, slug.replace("_", "")):
            if key in slug_to_code:
                return slug_to_code[key]
    blob = str(fallback_context or "")
    if blob:
        for label, code in labels:
            if label and label in blob:
                return code
    return ""


def _suumo_goo_mirror_url_candidates(item_url: str, fallback_context: str = "") -> list[str]:
    nc = _suumo_listing_id_from_url(item_url)
    category = _suumo_goo_buy_category(item_url)
    city_code = _suumo_city_code_for_goo(item_url, fallback_context=fallback_context)
    if not (nc and category and city_code):
        return []
    return [
        f"https://house.goo.ne.jp/buy/{category}/detail/1/{city_code}/030Z{nc}/000232008/x1030Z{nc}.html"
    ]


def _suumo_direct_resize_from_goo_proxy(raw_url: str, *, listing_id: str) -> str:
    s = str(raw_url or "").strip()
    if not s or not listing_id:
        return ""
    decoded = s
    for _ in range(3):
        nxt = unquote(decoded)
        if nxt == decoded:
            break
        decoded = nxt
    if listing_id not in decoded:
        return ""
    m = re.search(
        r"https?://(?:img\d+\.)?suumo\.(?:jp|com)/(?:front/)?gazo/bukken/[^\"'\s<>?]+\.(?:jpe?g|png|webp)",
        decoded,
        flags=re.I,
    )
    if not m:
        return ""
    inner = m.group(0)
    try:
        parsed = urlparse(inner)
    except Exception:
        return ""
    path = parsed.path or ""
    idx = path.lower().find("/gazo/")
    if idx < 0:
        return ""
    src = path[idx + 1 :].lstrip("/")
    if not src or listing_id not in src:
        return ""
    return "https://img01.suumo.com/jj/resizeImage?" + urlencode({"src": src, "w": "1600", "h": "1200"})


def _suumo_goo_mirror_detail_from_html(
    raw_html: str,
    mirror_url: str,
    *,
    listing_id: str,
    limit: int = 36,
) -> tuple[str, str, list[str]]:
    soup = soup_from_html(raw_html)
    title = ""
    if soup.title and soup.title.string:
        title = str(soup.title.string).strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(" ", strip=True):
        title = str(h1.get_text(" ", strip=True)).strip()
    main = soup.find("main") or soup.find(id="contents") or soup.find("body")
    text = re.sub(r"\s+", " ", main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)).strip()
    if listing_id and listing_id not in raw_html:
        return title[:200], text[:7000], []
    out: list[str] = []
    seen: set[str] = set()
    for tag in soup.select("img[src], img[data-src], img[data-original], a[href]"):
        for attr in ("src", "data-src", "data-original", "href"):
            raw = str(tag.get(attr) or "").strip()
            if not raw:
                continue
            full = urljoin(mirror_url, raw)
            u = _suumo_direct_resize_from_goo_proxy(full, listing_id=listing_id)
            if not u or u in seen:
                continue
            seen.add(u)
            out.append(u)
            if len(out) >= max(1, int(limit or 36)):
                return title[:200], text[:7000], out
    return title[:200], text[:7000], out


def _fetch_suumo_goo_mirror_detail(
    client: httpx.Client,
    item_url: str,
    *,
    fallback_context: str = "",
) -> tuple[str, str, list[str]] | None:
    listing_id = _suumo_listing_id_from_url(item_url)
    if not listing_id:
        return None
    for mirror_url in _suumo_goo_mirror_url_candidates(item_url, fallback_context=fallback_context):
        try:
            r = client.get(
                mirror_url,
                headers={
                    "Referer": "https://house.goo.ne.jp/",
                    "User-Agent": PORTAL_BROWSER_HEADERS.get("User-Agent", "Mozilla/5.0"),
                    "Accept-Language": "ja,en-US;q=0.8",
                },
                timeout=18.0,
                follow_redirects=True,
            )
        except Exception:
            continue
        if r.status_code >= 400:
            continue
        title, text, imgs = _suumo_goo_mirror_detail_from_html(
            r.text,
            str(r.url),
            listing_id=listing_id,
            limit=48,
        )
        if not imgs:
            continue
        body = (
            f"{text[:7000]}\n\n"
            "[SUUMO 同物件鏡像補圖]\n"
            f"- 同物件 ID: {listing_id}\n"
            f"- 補圖來源: {str(r.url)}\n"
            "- 圖片已由 goo 轉載頁還原為 SUUMO 物件圖 URL；僅保留 URL 含同一物件 ID 的照片。"
        ).strip()
        return title or "", body, imgs
    return None


def _suumo_bukkengaiyo_url(item_url: str) -> str:
    base = str(item_url or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if not base:
        return ""
    if base.endswith("/bukkengaiyo"):
        return base + "/"
    return base + "/bukkengaiyo/"


def _suumo_bukkengaiyo_url_from_soup(soup: BeautifulSoup, page_url: str) -> str:
    """Fallback: discover a concrete bukkengaiyo URL from the current SUUMO detail page."""
    best = ""
    for a in soup.select("a[href]"):
        href = str(a.get("href") or "").strip()
        if not href or "/bukkengaiyo/" not in href:
            continue
        full = _abs_url(page_url, href)
        if not full:
            continue
        lf = full.lower()
        if "suumo.jp" not in lf:
            continue
        # Prefer canonical item URLs that include /nc_ in the path.
        if "/nc_" in lf:
            return full
        if not best:
            best = full
    return best


def _suumo_sonota_url(item_url: str) -> str:
    u = str(item_url or "")
    m = re.search(r"/nc_(\d{6,})/?", u)
    nc = ""
    if m:
        nc = m.group(1)
    else:
        # /jj/bukken/shosai/... uses query nc= instead of /nc_123/ path.
        try:
            qs = parse_qs(urlparse(u).query, keep_blank_values=True)
            raw = (qs.get("nc") or [""])[0]
            raw = str(raw or "").strip()
            if raw.isdigit() and len(raw) >= 6:
                nc = raw
        except Exception:
            nc = ""
    if not nc:
        return ""
    # 關西(060) / 中古マンション(011) 口碑頁；查無時會 404，呼叫端容錯。
    return f"https://suumo.jp/jj/mnk/sonota/JJ905FH002/?ar=060&bs=011&nc={nc}&bnjChuKbn=2"


def _suumo_kv_digest_from_soup(soup: BeautifulSoup, *, limit: int = 90) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tr in soup.select("table tr"):
        ths = tr.select("th")
        tds = tr.select("td")
        if not ths or not tds:
            continue
        n = min(len(ths), len(tds))
        for i in range(n):
            k = re.sub(r"\s+", "", ths[i].get_text(" ", strip=True))
            v = re.sub(r"\s+", " ", tds[i].get_text(" ", strip=True)).strip()
            if not k or not v:
                continue
            row = f"{k}: {v}"
            if row in seen:
                continue
            seen.add(row)
            out.append(row)
            if len(out) >= limit:
                return out
    return out


def _suumo_sonota_digest_from_soup(soup: BeautifulSoup, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    # 口コミカード常見結構：年齡/性別 + 評語
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    pat = re.compile(r"((?:\d{2}代)/(?:男性|女性)/(?:住人|元住人)).{0,120}?。", re.I)
    for m in pat.finditer(text):
        s = m.group(0).strip()
        if len(s) < 16:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s[:180])
        if len(out) >= limit:
            break
    return out


def _extract_meta_and_images(soup: BeautifulSoup, page_url: str, raw_html: str = "") -> tuple[str, str, list[str]]:
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = str(og["content"]).strip()
    if not title and soup.title and soup.title.string:
        title = str(soup.title.string).strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = str(h1.get_text(" ", strip=True) or "").strip()
    if not title:
        title = _jsonld_first_name(soup)
    title = title[:200]

    desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        desc = str(md["content"]).strip()
    if not desc:
        ogd = soup.find("meta", property="og:description")
        if ogd and ogd.get("content"):
            desc = str(ogd["content"]).strip()

    def _img_url_keep_query(u: str) -> str:
        """SUUMO／部分入口的 resize 圖網址必須保留 query，否則只剩 /jj/resizeImage 會回 HTML 錯誤頁。

        HOMES 等站點常回相對路徑（/data/... 或 //image...），需以 page_url 補齊為絕對網址，否則會誤判為無圖。
        """
        s = str(u).strip().split("#", 1)[0]
        # 某些來源把「URL,圖片說明」拼成同一欄，去除逗號後的非 URL 標籤，避免破圖。
        if "," in s:
            left, right = s.split(",", 1)
            if left.startswith("http") and right and not right.startswith(("http://", "https://")):
                s = left
        # 去掉常見尾部汙染符號
        s = re.sub(r"[\"')\]\s]+$", "", s)
        if not s:
            return ""
        if s.startswith("//"):
            try:
                base = urlparse(page_url)
                scheme = base.scheme or "https"
            except Exception:
                scheme = "https"
            s = f"{scheme}:{s}"
        if not s.startswith(("http://", "https://")):
            try:
                s = urljoin(page_url, s)
            except Exception:
                return ""
        try:
            parsed = urlparse(s)
            host = (parsed.netloc or "").lower()
            path = (parsed.path or "").lower()
            if "suumo." in host and "/gazo/bukken/" in path and any(
                path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")
            ):
                raw_path = parsed.path or ""
                idx = raw_path.lower().find("/gazo/")
                if idx >= 0:
                    src = raw_path[idx + 1 :].lstrip("/")
                    return "https://img01.suumo.com/jj/resizeImage?" + urlencode(
                        {"src": src, "w": "1600", "h": "1200"}
                    )
            if "suumo." in host and "resizeimage" in path:
                q = {str(k): str(v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
                if str(q.get("src") or "").strip():
                    q["w"] = "1600"
                    q["h"] = "1200"
                    return parsed._replace(query=urlencode(list(q.items()), doseq=True)).geturl()
            if ("homes.jp" in host or "homes.co.jp" in host) and ("image.php" in path or "/smallimg/" in path):
                q = {str(k): str(v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
                q["width"] = "1600"
                q["height"] = "1600"
                return parsed._replace(query=urlencode(list(q.items()), doseq=True)).geturl()
        except Exception:
            pass
        return s if s.startswith(("http://", "https://")) else ""

    imgs: list[str] = []
    pul = (page_url or "").lower()
    homes_tokens = homes_listing_image_tokens(page_url) if ("homes.co.jp" in pul or "homes.jp" in pul) else ()
    homes_matched: list[str] = []
    homes_seen: set[str] = set()

    def _track_homes_match(u: str) -> None:
        if not homes_tokens:
            return
        if not u or u in homes_seen:
            return
        try:
            dec = unquote(u).lower()
        except Exception:
            dec = u.lower()
        if any(tok in dec for tok in homes_tokens):
            homes_seen.add(u)
            homes_matched.append(u)

    scan_cap = 200 if homes_tokens else 48
    if "suumo.jp" in pul and re.search(r"/(?:ms|chuko|mansion|chintai|ikkodate)/", pul):
        for ju in _suumo_property_image_urls_from_raw_html(raw_html, page_url, limit=36):
            if ju not in imgs:
                imgs.append(ju)
                _track_homes_match(ju)
        for ju in _suumo_detail_image_urls(soup, page_url, limit=28):
            if ju not in imgs:
                imgs.append(ju)
                _track_homes_match(ju)
    for ju in _jsonld_image_urls(soup, page_url, limit=18):
        if ju not in imgs:
            imgs.append(ju)
            _track_homes_match(ju)
    for tag in soup.find_all("meta", property="og:image"):
        c = tag.get("content")
        nu = _img_url_keep_query(c or "")
        if nu:
            imgs.append(nu)
            _track_homes_match(nu)
    for img in soup.select(
        "img[src], img[data-src], img[data-original], img[data-lazy-src], "
        "img[srcset], img[data-srcset]"
    ):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            raw = img.get(attr)
            nu = _img_url_keep_query(raw or "")
            if not nu:
                continue
            lu = nu.lower()
            if any(x in lu for x in ("logo", "icon", "spacer", "pixel", "1x1")):
                continue
            if nu not in imgs:
                imgs.append(nu)
                _track_homes_match(nu)
        for attr in ("srcset", "data-srcset"):
            for nu2 in _urls_from_srcset_attr(str(img.get(attr) or ""), page_url):
                lu2 = nu2.lower()
                if any(x in lu2 for x in ("logo", "icon", "spacer", "pixel", "1x1")):
                    continue
                if nu2 not in imgs:
                    imgs.append(nu2)
                    _track_homes_match(nu2)
        if len(imgs) >= scan_cap:
            break

    # HOMES：只要是 b-id 詳情頁就必須命中 token；若完全沒命中，多半是 JS/WAF 導致抓到「推薦物件」縮圖或站台樣板圖，
    # 直接丟棄以避免跨案汙染。
    if homes_tokens:
        imgs = homes_matched if homes_matched else []

    main = soup.find("main") or soup.find("article") or soup.find("body")
    text = ""
    if main:
        text = main.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    homes_chintai_digest = ""
    if "homes.co.jp" in pul and "/mansion/b-" in pul:
        hdigest = _homes_mansion_detail_kv_digest(soup)
        if hdigest:
            text = hdigest + " " + text
    if "homes.co.jp" in pul and "/chintai/room/" in pul:
        homes_chintai_digest = _homes_chintai_dl_kv_digest(soup)
        if homes_chintai_digest:
            text = homes_chintai_digest + " " + text
    athome_shinchiku_digest: list[str] = []
    if "athome.co.jp" in pul and "/mansion/shinchiku/" in pul:
        athome_shinchiku_digest = _athome_shinchiku_detail_kv_digest(soup)
        if athome_shinchiku_digest:
            text = " ".join(athome_shinchiku_digest) + " " + text
    if "realestate.yahoo.co.jp" in pul and (
        "/used/mansion/detail" in pul or "/land/detail" in pul
    ):
        ydigest = _yahoo_mansion_detail_kv_digest(soup)
        if ydigest:
            text = ydigest + " " + text
    if "suumo.jp" in pul:
        sdigest = _suumo_detail_kv_digest(soup)
        if sdigest:
            text = " ".join(sdigest) + " " + text
    # Yahoo 詳情欄位較長，保留較大字數避免後段欄位被截掉
    if "homes.co.jp" in pul and "/mansion/b-" in pul:
        max_len = 5200
    elif "homes.co.jp" in pul and "/chintai/room/" in pul:
        max_len = 5200
    elif "realestate.yahoo.co.jp" in pul and ("/used/mansion/detail" in pul or "/land/detail" in pul):
        max_len = 5200
    elif "athome.co.jp" in pul and "/mansion/" in pul:
        max_len = 5200
    elif "suumo.jp" in pul:
        max_len = 9000
    else:
        max_len = 3600
    text = text[:max_len]
    body = (desc or text[:1000]).strip()
    facts = _extract_listing_facts(page_url, title, desc, text)
    athome_trail = ""
    if "athome.co.jp" in (page_url or "").lower():
        athome_trail = _athome_breadcrumb_line_from_soup(soup)
    meta_prefix: list[str] = []
    homes_trail_line = ""
    if "homes.co.jp" in (page_url or "").lower():
        homes_trail_line = _homes_breadcrumb_line_from_soup(soup, title)
        meta_prefix.extend(_homes_update_meta_from_soup(soup))
        if homes_trail_line:
            meta_prefix.append(f"HOMESサイト階層（パンくず）: {homes_trail_line}")
    if "realestate.yahoo.co.jp" in pul:
        meta_prefix.extend(_yahoo_update_meta_from_soup(soup))
    if "athome.co.jp" in (page_url or "").lower():
        iso = _athome_publication_iso_from_soup(soup)
        if iso:
            meta_prefix.insert(0, f"掲載情報日(ISO): {iso}")
        if athome_trail:
            meta_prefix.append(f"AtHomeサイト階層（パンくず）: {athome_trail}")
    if meta_prefix:
        facts = meta_prefix + facts
    if facts:
        body = (
            f"{body}\n\n"
            "[物件欄位摘要]\n"
            + "\n".join(f"- {x}" for x in facts)
        )
    if homes_chintai_digest:
        ln: list[str] = []
        for part in homes_chintai_digest.split("|"):
            p = part.strip()
            if ":" not in p:
                continue
            k0, v0 = p.split(":", 1)
            kk = k0.strip()
            vv = v0.strip()
            if not kk or not vv:
                continue
            if "徒歩" in kk and len(kk) > 14:
                continue
            ln.append(f"- {kk}: {vv[:460]}")
        if ln:
            body += "\n\n[HOMES 賃貸詳細欄位]\n" + "\n".join(ln[:64])
    if athome_shinchiku_digest:
        body += "\n\n[AtHome 詳細欄位]\n" + "\n".join(f"- {x}" for x in athome_shinchiku_digest[:80])
    if "suumo.jp" in pul:
        sdigest = _suumo_detail_kv_digest(soup)
        if sdigest:
            body += "\n\n[SUUMO 詳細欄位]\n" + "\n".join(f"- {x}" for x in sdigest[:80])
    if homes_trail_line:
        body += "\n\n[HOMES 階層導覽]\n" + homes_trail_line
    if athome_trail:
        body += "\n\n[AtHome 階層導覽]\n" + athome_trail
    return title, body, imgs[:48]


def _fallback_listing_title(page_url: str) -> str:
    """當頁面無法擷取標題時，給可讀占位（禁止把 item_url 當標題寫入資料庫）。"""
    u = (page_url or "").strip().lower()
    site = "門戶網站"
    if "homes.co.jp" in u:
        site = "LIFULL HOMES（HOMES）"
    elif "suumo.jp" in u:
        site = "SUUMO"
    elif "athome.co.jp" in u:
        site = "AtHome"
    elif "realestate.yahoo.co.jp" in u:
        site = "Yahoo!不動産"
    slot = "不動產物件"
    if "/chintai/" in u:
        slot = "租賃物件"
    elif "/mansion/" in u or "/ms/" in u:
        slot = "買賣／新建・中古物件"
    elif "/kodate/" in u:
        slot = "戶建／一戶建物件"
    return f"{site} {slot}（標題未能自動擷取，請以來源頁為準）"


def coerce_listing_display_title(raw: str, item_url: str, *, max_len: int = 240) -> str:
    """若標題為空、為網址或與 item_url 相同，改為占位說明。"""
    t = (raw or "").strip()
    u = (item_url or "").strip()
    if not u:
        return (t or "不動產物件")[:max_len]
    if not t:
        return _fallback_listing_title(u)[:max_len]
    tl, ul = t.lower(), u.lower()
    if t.startswith(("http://", "https://")) or tl == ul:
        return _fallback_listing_title(u)[:max_len]
    if "://" in t and len(t) >= 12 and t.startswith("http"):
        return _fallback_listing_title(u)[:max_len]
    return t[:max_len]


def _finalize_property_detail(item_url: str, title: str, body: str, imgs: list[str]) -> tuple[str, str, list[str]]:
    title = coerce_listing_display_title(title, item_url)
    img_block = "\n".join(imgs) if imgs else ""
    body_original = (
        f"{title}\n\n{body}\n\n"
        f"[物件參考圖像 URL]\n{img_block}\n\n"
        f"來源物件頁（請以官方頁面為準）：{item_url}\n"
        "用途：站內摘要、導覽與連結索引；不主張為完整契約內容。"
    )
    return title, body_original, imgs


def fetch_property_detail(
    client: httpx.Client,
    item_url: str,
    *,
    fallback_context: str = "",
) -> tuple[str, str, list[str]]:
    """SUUMO 等站點詳情偶發 502/503/429；短重試避免整批爬蟲 0 筆。"""
    r: httpx.Response | None = None
    referer = str(item_url or "").strip().split("#", 1)[0]
    extra_h: dict[str, str] = {"Referer": referer} if referer.startswith("http") else {}
    for attempt in range(3):
        try:
            r = _portal_get(client, item_url, headers=extra_h)
        except PortalRateLimitActive:
            if "suumo.jp" in str(item_url or "").lower():
                mirror = _fetch_suumo_goo_mirror_detail(
                    client,
                    item_url,
                    fallback_context=fallback_context,
                )
                if mirror:
                    title_m, body_m, imgs_m = mirror
                    return _finalize_property_detail(item_url, title_m, body_m, imgs_m)
            raise
        if r.status_code < 400:
            break
        if r.status_code in (429, 502, 503, 504) and attempt < 2:
            time.sleep(1.3 * (attempt + 1))
            continue
        r.raise_for_status()
    if r is None:
        raise RuntimeError("no response")
    r.raise_for_status()
    soup = soup_from_html(r.text)
    title, body, imgs = _extract_meta_and_images(soup, item_url, raw_html=r.text)
    pul = (item_url or "").lower()
    if "suumo.jp" in pul and not imgs:
        mirror = _fetch_suumo_goo_mirror_detail(
            client,
            item_url,
            fallback_context="\n".join([fallback_context, title, body]),
        )
        if mirror:
            title_m, body_m, imgs_m = mirror
            return _finalize_property_detail(item_url, title_m or title, body_m, imgs_m)
    # SUUMO: 追加補抓「物件概要」與「周邊口コミ」，讓案例頁可查到周邊與相關參考資訊。
    if "suumo.jp" in pul:
        try:
            bkg_url = _suumo_bukkengaiyo_url(item_url)
            if "/jj/bukken/shosai/" in pul:
                bkg_url = _suumo_bukkengaiyo_url_from_soup(soup, item_url) or bkg_url
            if bkg_url:
                rb = _portal_get(client, bkg_url, headers=extra_h)
                if rb.status_code < 400:
                    sb = soup_from_html(rb.text)
                    bkg = _suumo_kv_digest_from_soup(sb, limit=120)
                    if bkg:
                        body += "\n\n[SUUMO 物件概要]\n" + "\n".join(f"- {x}" for x in bkg[:120])
        except Exception:
            pass
        try:
            so_url = _suumo_sonota_url(item_url)
            if so_url:
                rs = _portal_get(client, so_url, headers=extra_h)
                if rs.status_code < 400:
                    ss = soup_from_html(rs.text)
                    reviews = _suumo_sonota_digest_from_soup(ss, limit=8)
                    if reviews:
                        body += "\n\n[周邊口コミ摘錄]\n" + "\n".join(f"- {x}" for x in reviews)
        except Exception:
            pass
    if "realestate.yahoo.co.jp" in pul and "/used/mansion/search/" in pul:
        try:
            total = _yahoo_search_total_count(soup)
            ext, ext_imgs = _yahoo_search_enrich_from_details(client, item_url, limit=24, land=False)
            if total > 0:
                body += f"\n\n[Yahoo 搜尋結果]\n- 搜尋頁總件數：{total}件"
            if ext:
                body += "\n- 搜尋策略：由列表展開 detail 頁摘要（供站內查詢）\n" + ext
            for u in ext_imgs:
                if u not in imgs:
                    imgs.append(u)
        except Exception:
            pass
    if "realestate.yahoo.co.jp" in pul and "/land/search/" in pul:
        try:
            total = _yahoo_search_total_count(soup)
            ext, ext_imgs = _yahoo_search_enrich_from_details(client, item_url, limit=24, land=True)
            if total > 0:
                body += f"\n\n[Yahoo 土地搜尋結果]\n- 搜尋頁總件數：{total}件"
            if ext:
                body += "\n- 搜尋策略：由土地列表展開 detail 頁摘要（供站內查詢）\n" + ext
            for u in ext_imgs:
                if u not in imgs:
                    imgs.append(u)
        except Exception:
            pass
    return _finalize_property_detail(item_url, title, body, imgs)


def crawl_portal_listings(source: dict, per_source_limit: int, search_query: str = "") -> list:
    """Returns list of CrawledItem for one registry source dict."""
    from datetime import datetime, timezone

    from src.crawler import CrawledItem  # late import avoids import cycle

    root = (source.get("url") or "").strip()
    if not root:
        return []
    host_key = _host_key(urlparse(root).netloc)
    if host_key not in LISTING_HUB_PAGES:
        return []
    skip_reason = _portal_skip_reason(host_key)
    if skip_reason:
        logger.warning("Skipping portal crawl for %s: %s", host_key, skip_reason)
        return []

    lim = max(1, min(int(per_source_limit or 20), 2000))
    now = datetime.now(timezone.utc).isoformat()
    out: list[CrawledItem] = []
    timeout = _httpx_timeout_for_portal(host_key)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers=PORTAL_BROWSER_HEADERS,
    ) as client:
        hub_urls = _merged_listing_hub_urls(host_key, search_query)
        if (urlparse(root).path not in ("", "/")) or urlparse(root).query:
            root_hub = root.split("#", 1)[0].strip()
            hub_urls = [root_hub] + [h for h in hub_urls if h.rstrip("/") != root_hub.rstrip("/")]
        hub_urls = _trim_listing_hub_urls(
            host_key,
            hub_urls,
            search_query,
        )
        detail_urls = _collect_links_from_hub_list(client, host_key, hub_urls, lim)
        for item_url in detail_urls:
            try:
                title, body_original, imgs = fetch_property_detail(client, item_url)
            except PortalRateLimitActive:
                break
            except Exception:
                continue
            # at home 以「情報提供日」硬過濾會導致新抓入庫量極低（矩陣長期接近 0）。
            # 預設交給後續 age_days / updated_at 口徑控管；若要啟用舊行為可設 SCLAW_ATHOME_ENFORCE_PUBDATE=1
            enforce_pubdate = (os.getenv("SCLAW_ATHOME_ENFORCE_PUBDATE") or "").strip().lower() in ("1", "true", "yes")
            if enforce_pubdate and "athome.co.jp" in item_url.lower() and _stale_athome_listing_by_body(body_original):
                continue
            img_join = "\n".join(imgs)
            out.append(
                CrawledItem(
                    source_name=str(source.get("name") or ""),
                    source_category=str(source.get("category") or "大型房仲"),
                    source_url=root,
                    item_url=item_url,
                    title_original=title[:240],
                    body_original=body_original,
                    language="ja",
                    published_at=now,
                    access_status="public",
                    access_note="",
                    image_urls=img_join,
                    content_kind="jp_listing",
                )
            )
    return out
