"""首頁「日本找房工作台」：同一畫面切換地圖／都道府縣／通勤・駅（HOME'S／SUUMO 概念）；chip 與站內 keyword／region。"""
from __future__ import annotations

from typing import Any

# region 必須能對應到 case_metadata.JP_AREA_FILTER_LABELS 其中一項；若無則留空改以 keyword 命中。
HOMES_STYLE_PREF_CLUSTERS: list[dict[str, Any]] = [
    {
        "title": "北海道",
        "items": [{"label": "北海道", "kw": "北海道 不動產", "region": "北海道"}],
    },
    {
        "title": "東北",
        "items": [
            {"label": "青森縣", "kw": "青森 不動產", "region": "東北"},
            {"label": "岩手縣", "kw": "岩手 不動產", "region": "東北"},
            {"label": "宮城縣", "kw": "仙台 不動產", "region": "東北"},
            {"label": "秋田縣", "kw": "秋田 不動產", "region": "東北"},
            {"label": "山形縣", "kw": "山形 不動產", "region": "東北"},
            {"label": "福島縣", "kw": "福島 不動產", "region": "東北"},
        ],
    },
    {
        "title": "關東",
        "items": [
            {"label": "茨城縣", "kw": "茨城 不動產", "region": "關東"},
            {"label": "栃木縣", "kw": "栃木 不動產", "region": "關東"},
            {"label": "群馬縣", "kw": "群馬 不動產", "region": "關東"},
            {"label": "埼玉縣", "kw": "埼玉 不動產", "region": "埼玉"},
            {"label": "千葉縣", "kw": "千葉 不動產", "region": "千葉"},
            {"label": "東京都", "kw": "東京 不動產", "region": "東京"},
            {"label": "神奈川縣", "kw": "神奈川 不動產", "region": "神奈川"},
        ],
    },
    {
        "title": "甲信越",
        "items": [
            {"label": "山梨縣", "kw": "山梨 不動產", "region": "甲信越"},
            {"label": "長野縣", "kw": "長野 不動產", "region": "甲信越"},
            {"label": "新潟縣", "kw": "新潟 不動產", "region": "甲信越"},
        ],
    },
    {
        "title": "北陸",
        "items": [
            {"label": "富山縣", "kw": "富山 不動產", "region": "北陸"},
            {"label": "石川縣", "kw": "金澤 不動產", "region": "北陸"},
            {"label": "福井縣", "kw": "福井 不動產", "region": "北陸"},
        ],
    },
    {
        "title": "東海",
        "items": [
            {"label": "岐阜縣", "kw": "岐阜 不動產", "region": "東海"},
            {"label": "靜岡縣", "kw": "靜岡 不動產", "region": "東海"},
            {"label": "愛知縣", "kw": "名古屋 不動產", "region": "名古屋"},
            {"label": "三重縣", "kw": "三重 不動產", "region": "東海"},
        ],
    },
    {
        "title": "關西",
        "items": [
            {"label": "滋賀縣", "kw": "滋賀 不動產", "region": "關西"},
            {"label": "京都府", "kw": "京都 不動產", "region": "京都市"},
            {"label": "大阪府", "kw": "大阪 不動產", "region": "大阪"},
            {"label": "兵庫縣", "kw": "神戶 不動產", "region": "關西"},
            {"label": "奈良縣", "kw": "奈良 不動產", "region": "關西"},
            {"label": "和歌山縣", "kw": "和歌山 不動產", "region": "關西"},
        ],
    },
    {
        "title": "中國",
        "items": [
            {"label": "鳥取縣", "kw": "鳥取 不動產", "region": "中國地方"},
            {"label": "島根縣", "kw": "島根 不動產", "region": "中國地方"},
            {"label": "岡山縣", "kw": "岡山 不動產", "region": "中國地方"},
            {"label": "廣島縣", "kw": "廣島 不動產", "region": "中國地方"},
            {"label": "山口縣", "kw": "山口 不動產", "region": "中國地方"},
        ],
    },
    {
        "title": "四國",
        "items": [
            {"label": "德島縣", "kw": "德島 不動產", "region": "四國"},
            {"label": "香川縣", "kw": "高松 不動產", "region": "四國"},
            {"label": "愛媛縣", "kw": "愛媛 不動產", "region": "四國"},
            {"label": "高知縣", "kw": "高知 不動產", "region": "四國"},
        ],
    },
    {
        "title": "九州",
        "items": [
            {"label": "福岡縣", "kw": "福岡 不動產", "region": "福岡"},
            {"label": "佐賀縣", "kw": "佐賀 不動產", "region": "九州"},
            {"label": "長崎縣", "kw": "長崎 不動產", "region": "九州"},
            {"label": "熊本縣", "kw": "熊本 不動產", "region": "九州"},
            {"label": "大分縣", "kw": "大分 不動產", "region": "九州"},
            {"label": "宮崎縣", "kw": "宮崎 不動產", "region": "九州"},
            {"label": "鹿兒島縣", "kw": "鹿兒島 不動產", "region": "九州"},
        ],
    },
    {
        "title": "沖繩",
        "items": [{"label": "沖繩縣", "kw": "沖繩 不動產", "region": "沖繩"}],
    },
]

def split_pref_clusters_for_map_orbit(
    clusters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """地圖周邊十個白框：西側（沖繩→中國）、東側（北海道→關西）；甲信越與北陸合併為「甲信越・北陸」。"""
    by_title: dict[str, dict[str, Any]] = {}
    for c in clusters:
        t = str(c.get("title") or "").strip()
        if t:
            by_title[t] = dict(c)

    y = by_title.pop("甲信越", None)
    h = by_title.pop("北陸", None)
    merged_items: list[dict[str, Any]] = []
    if y:
        merged_items.extend(list(y.get("items") or []))
    if h:
        merged_items.extend(list(h.get("items") or []))
    if merged_items:
        by_title["甲信越・北陸"] = {"title": "甲信越・北陸", "items": merged_items}

    west_order = ("沖繩", "九州", "四國", "中國")
    east_order = ("北海道", "東北", "關東", "甲信越・北陸", "東海", "關西")

    west = [by_title[t] for t in west_order if t in by_title]
    east = [by_title[t] for t in east_order if t in by_title]
    return west, east


JP_MAP_ORBIT_WEST, JP_MAP_ORBIT_EAST = split_pref_clusters_for_map_orbit(HOMES_STYLE_PREF_CLUSTERS)


HOMES_STYLE_HUB_RAIL: list[dict[str, str]] = [
    {
        "id": "map",
        "icon": "🗺️",
        "label": "地圖",
        "hint": "中央地圖可點大區；左右白框綠邊為都道府縣，點縣名帶入查詢。",
    },
    {
        "id": "rail",
        "icon": "🗾",
        "label": "都道府縣",
        "hint": "HOME'S 式分組都道府縣 chip（homes.co.jp/chintai）；點縣名帶入下方智慧查詢。",
    },
    {
        "id": "commute",
        "icon": "🕒",
        "label": "通勤與車站",
        "hint": "都市圈 → 路線 → 車站（HOME'S 車站／路線與通勤／通學）；完成後按「帶入③並查詢案件」。",
    },
]
