from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import get_conn, init_db


SOURCE_NAME = "SCLAW 日本生活購物知識庫"
SOURCE_URL = "sclaw://knowledge/japan-shopping"
CONTENT_KIND = "japan_shopping_kb"


HANS_REPL = [
    ("臺", "台"),
    ("灣", "湾"),
    ("購", "购"),
    ("買", "买"),
    ("賣", "卖"),
    ("藥", "药"),
    ("妝", "妆"),
    ("價", "价"),
    ("錢", "钱"),
    ("稅", "税"),
    ("費", "费"),
    ("貸", "贷"),
    ("產", "产"),
    ("務", "务"),
    ("機", "机"),
    ("關", "关"),
    ("聯", "联"),
    ("區", "区"),
    ("與", "与"),
    ("對", "对"),
    ("應", "应"),
    ("樓", "楼"),
    ("層", "层"),
    ("總", "总"),
    ("積", "积"),
    ("處", "处"),
    ("適", "适"),
    ("優", "优"),
    ("點", "点"),
    ("風", "风"),
    ("險", "险"),
    ("顧", "顾"),
    ("問", "问"),
    ("題", "题"),
    ("據", "据"),
    ("維", "维"),
    ("護", "护"),
    ("後", "后"),
    ("續", "续"),
    ("廣", "广"),
    ("場", "场"),
    ("雙", "双"),
    ("現", "现"),
    ("實", "实"),
    ("數", "数"),
    ("據", "据"),
]


AREA_PROFILES = [
    {
        "slug": "tokyo-23ku",
        "region": "東京",
        "title": "東京23區購物生活機能與找房判斷",
        "persona": "首次赴日自住、商務派駐、想靠近山手線與地下鐵的人",
        "keywords": ["東京超市", "山手線", "藥妝", "便利店", "徒歩5分", "生活機能"],
        "chains": "Life、Maruetsu、My Basket、成城石井、Matsukiyo、Welcia、Bic Camera、Yodobashi",
        "case_link": "優先關聯東京、首都圏、駅徒歩10分內、1R/1K/小宅與中古マンション。",
    },
    {
        "slug": "yokohama-kawasaki",
        "region": "神奈川",
        "title": "橫濱川崎購物圈與通勤生活比較",
        "persona": "重視通勤到品川/東京、同時希望生活成本低於核心東京的人",
        "keywords": ["横浜 買物", "川崎 商圈", "東急線", "京急線", "大型商場", "通勤"],
        "chains": "OK Store、AEON、Lazona川崎、Sogo横浜、Don Quijote、Nitori、Yodobashi",
        "case_link": "優先關聯神奈川、横浜、川崎、JR/東急/京急沿線、徒歩15分內案件。",
    },
    {
        "slug": "osaka-kansai",
        "region": "大阪",
        "title": "大阪購物與關西生活機能導覽",
        "persona": "想兼顧投資收益、觀光流量與生活便利性的買方",
        "keywords": ["大阪超市", "梅田", "難波", "關西生活", "藥妝", "家電配送"],
        "chains": "Life、Kohyo、阪急百貨、Yodobashi梅田、Bic Camera難波、Sugi藥局",
        "case_link": "優先關聯大阪、關西、御堂筋線/JR環狀線、徒歩10分內與小坪數租售案件。",
    },
    {
        "slug": "kyoto-kobe",
        "region": "關西",
        "title": "京都神戶日常採買、觀光商圈與住宅安靜度",
        "persona": "偏好文化城市、家庭自住、留學陪讀或長住型買方",
        "keywords": ["京都超市", "神戶生活", "百貨", "市場", "觀光區", "安靜住宅"],
        "chains": "Fresco、Izumiya、Daimaru京都、Mina京都、阪急西宮、神戶三宮商圈",
        "case_link": "優先關聯京都市、關西、阪急/JR/地下鐵沿線與低噪音住宅區案件。",
    },
    {
        "slug": "nagoya-aichi",
        "region": "名古屋",
        "title": "名古屋生活購物、車站商圈與車生活成本",
        "persona": "製造業/商務常駐、家庭自住、想買較大空間的人",
        "keywords": ["名古屋超市", "AEON Mall", "地下鐵", "車生活", "家電", "家具"],
        "chains": "AEON、Valor、Apita、Yamada Denki、Nitori、JR Gate Tower",
        "case_link": "優先關聯名古屋、東海、地下鐵東山線/名城線、車位與戸建案件。",
    },
    {
        "slug": "fukuoka-kyushu",
        "region": "福岡",
        "title": "福岡購物生活、機場距離與移居便利性",
        "persona": "移居入門、退休長住、低壓城市生活與租金收益兼顧者",
        "keywords": ["福岡超市", "天神", "博多", "機場線", "移居", "生活成本"],
        "chains": "Sunny、TRIAL、AEON、Canal City、Bic Camera天神、Drug Eleven",
        "case_link": "優先關聯福岡、九州、機場線/七隈線、徒歩10分內租售案件。",
    },
    {
        "slug": "sapporo-hokkaido",
        "region": "北海道",
        "title": "札幌冬季採買、暖房用品與日常購物",
        "persona": "北海道自住、長租、滑雪旅居與低總價買方",
        "keywords": ["札幌超市", "北海道生活", "暖房", "冬季用品", "地下鐵", "除雪"],
        "chains": "Coop Sapporo、AEON、Tsuruha Drug、Nitori、Yodobashi札幌、DCM",
        "case_link": "優先關聯北海道、札幌地下鐵、暖房設備、徒歩10分內與管理良好マンション。",
    },
    {
        "slug": "sendai-tohoku",
        "region": "東北",
        "title": "仙台與東北城市購物圈、車站生活與家庭需求",
        "persona": "家庭型自住、留學陪讀、希望低密度城市的人",
        "keywords": ["仙台超市", "東北生活", "車站商圈", "AEON", "藥妝", "家族"],
        "chains": "AEON、SEIYU、York Benimaru、Tsuruha、Yodobashi仙台、Loft",
        "case_link": "優先關聯東北、仙台站/地下鐵沿線、家庭格局與停車便利案件。",
    },
    {
        "slug": "hiroshima-chugoku",
        "region": "中國地方",
        "title": "廣島生活採買、電車沿線與平價日用品",
        "persona": "地方核心城市投資、自住、希望生活成本可控的人",
        "keywords": ["廣島超市", "路面電車", "藥妝", "商店街", "生活成本", "中古屋"],
        "chains": "Youme Town、Fuji、MaxValu、Wants、Edion、Daiso",
        "case_link": "優先關聯中國地方、廣島電鐵沿線、生活圈成熟的中古マンション與戸建。",
    },
    {
        "slug": "okinawa",
        "region": "沖繩",
        "title": "沖繩購物、車生活與離島居住成本",
        "persona": "度假自用、長住、觀光收益與車生活可接受者",
        "keywords": ["沖繩超市", "車生活", "AEON", "San-A", "藥妝", "度假宅"],
        "chains": "San-A、AEON、Ryubo、Don Quijote、Makeman、Drug Eleven",
        "case_link": "優先關聯沖繩、停車位、商場距離、車程與生活用品採買便利性。",
    },
]


CATEGORY_GUIDES = [
    {
        "slug": "supermarket",
        "title": "日本超市怎麼看：日常預算、營業時間與房源生活圈",
        "keywords": ["日本超市", "AEON", "Life", "SEIYU", "業務スーパー", "生活成本"],
        "persona": "自住、親子、長住與希望控管每月生活費的客戶",
        "points": [
            "看房時把最近超市、營業到幾點、是否有惣菜熟食列入比較。",
            "步行5至10分內有超市，對自住與出租吸引力都更穩。",
            "業務スーパー、TRIAL、OK Store偏價格導向；成城石井、紀ノ国屋偏品質導向。",
        ],
    },
    {
        "slug": "convenience-store",
        "title": "便利店密度與夜間生活：7-Eleven、Lawson、FamilyMart",
        "keywords": ["便利店", "コンビニ", "夜間採買", "ATM", "公共料金", "宅配"],
        "persona": "短住、單身上班族、留學生與高流動租客",
        "points": [
            "便利店可處理ATM、影印、包裹、公共料金與簡餐，是單身租屋的重要生活機能。",
            "離站遠但附近有便利店，生活便利性可部分補強。",
            "投資出租可把便利店、藥妝、超市作為租客溝通素材。",
        ],
    },
    {
        "slug": "drugstore",
        "title": "藥妝店與日用品：Matsukiyo、Welcia、Sugi、Tsuruha",
        "keywords": ["藥妝", "ドラッグストア", "免稅", "日用品", "保健品", "美妝"],
        "persona": "旅日購物、家庭自住、銀髮長住與代購敏感客群",
        "points": [
            "藥妝店常兼賣食品、清潔、嬰幼兒與保健用品，是生活圈評估的核心。",
            "觀光區藥妝多但價格未必最低；住宅區大型店常更適合長住採買。",
            "免稅與折扣券條件需以店內標示為準，付款前先確認護照與商品分類。",
        ],
    },
    {
        "slug": "electronics",
        "title": "日本家電採買：Bic Camera、Yodobashi、Yamada 與配送安裝",
        "keywords": ["家電", "Bic Camera", "Yodobashi", "配送", "安裝", "冷蔵庫"],
        "persona": "新屋交屋、出租佈置、短期入住與海外屋主管理",
        "points": [
            "冰箱、洗衣機、冷暖氣要先確認尺寸、搬入路線、電壓與安裝費。",
            "大型家電通常可約配送時段，熱門季節需預留時間。",
            "投資出租時可建立標準家電清單，降低空租期與管理溝通成本。",
        ],
    },
    {
        "slug": "furniture-home",
        "title": "家具與生活用品：Nitori、MUJI、IKEA、Cainz 的用途差異",
        "keywords": ["家具", "Nitori", "無印良品", "IKEA", "ホームセンター", "入住"],
        "persona": "新入住、家庭自住、民泊/長租佈置與遠距屋主",
        "points": [
            "Nitori適合高性價比基本家具；MUJI偏簡潔質感；IKEA適合整體配置。",
            "Cainz、Konan、DCM 等 home center 可買工具、收納、園藝與維修用品。",
            "購屋前先估家具搬入、收納與垃圾分類空間，避免交屋後才發現不合用。",
        ],
    },
    {
        "slug": "hundred-yen",
        "title": "百元店：Daiso、Seria、Can Do 的入住採買清單",
        "keywords": ["百元店", "Daiso", "Seria", "Can Do", "收納", "清潔用品"],
        "persona": "低預算入住、留學生、短租與首次赴日生活者",
        "points": [
            "百元店適合清潔、廚房小物、收納、文具與臨時生活用品。",
            "大量、耐用或安全相關用品仍建議改在專門店或 home center 比較。",
            "把百元店距離列入單間租屋話術，能貼近初到日本的真實需求。",
        ],
    },
    {
        "slug": "tax-free",
        "title": "日本免稅購物：誰適用、怎麼問、哪些商品要注意",
        "keywords": ["免稅", "tax free", "護照", "藥妝免稅", "消耗品", "日本購物"],
        "persona": "觀光購物、短期看房、陪同考察與跨境客戶",
        "points": [
            "免稅多面向短期停留旅客，長期簽證或居住者未必適用。",
            "消耗品與一般物品規則、封袋、金額門檻與店家政策需當場確認。",
            "看房行程可安排商圈與免稅購物點，但交易建議仍以生活機能為主。",
        ],
    },
    {
        "slug": "payment",
        "title": "日本付款方式：現金、信用卡、交通IC、PayPay與外國卡",
        "keywords": ["支付", "信用卡", "Suica", "PASMO", "PayPay", "現金"],
        "persona": "短住、長住、跨境看房與日常採買客戶",
        "points": [
            "大城市卡片與電子支付普及，但地方、市場、小店仍可能偏現金。",
            "交通IC可小額支付，適合便利店、車站商店與部分自販機。",
            "海外信用卡可能遇到驗證或拒刷，初期仍建議保留現金備案。",
        ],
    },
    {
        "slug": "secondhand",
        "title": "二手與回收店：Hard-Off、Treasure Factory、Mercari",
        "keywords": ["二手家具", "回收店", "Hard-Off", "Mercari", "中古家電", "搬家"],
        "persona": "低成本入住、出租佈置、留學生與短期長住客",
        "points": [
            "二手家電家具可省預算，但要看保固、配送與清潔狀態。",
            "Mercari價格彈性高，但大型物件配送與安裝要另外評估。",
            "退租或換屋時，回收處理成本也應列入持有成本。",
        ],
    },
    {
        "slug": "delivery",
        "title": "配送與收貨：黑貓宅急便、置き配、再配達與大樓管理",
        "keywords": ["宅配", "配送", "再配達", "置き配", "宅配ボックス", "管理規約"],
        "persona": "遠距屋主、工作繁忙住戶、租客與物業管理需求者",
        "points": [
            "有宅配ボックス的大樓，對單身上班族和短期不在家的人很加分。",
            "大型家具家電需確認搬入路線、電梯、樓梯寬度與管理規約。",
            "看房時可記錄收貨動線，後續出租介紹更具體。",
        ],
    },
    {
        "slug": "outlet-department",
        "title": "百貨、Outlet與商店街：觀光購物和長住生活的差別",
        "keywords": ["百貨", "Outlet", "商店街", "Mitsui Outlet", "Isetan", "Takashimaya"],
        "persona": "觀光看房、品牌消費、家庭休閒與城市生活客戶",
        "points": [
            "百貨與Outlet提升休閒消費吸引力，但不等於日常採買便利。",
            "長住更應看超市、藥妝、診所、銀行與交通。",
            "商店街能反映地方生活密度，對出租與自住都有參考價值。",
        ],
    },
    {
        "slug": "garbage",
        "title": "日本垃圾分類與指定垃圾袋：買房租房都要先問",
        "keywords": ["垃圾分類", "指定垃圾袋", "粗大ゴミ", "搬家垃圾", "管理規約", "生活規則"],
        "persona": "首次赴日居住、長租、購屋自住與出租管理者",
        "points": [
            "各自治體垃圾分類不同，指定袋、回收日與粗大ゴミ申請要先確認。",
            "大樓垃圾置場是否24小時可用，會直接影響居住便利。",
            "出租管理時，把垃圾規則翻成中文可降低客訴與鄰里摩擦。",
        ],
    },
]


BUYER_GUIDES = [
    {
        "slug": "buying-flow",
        "title": "日本買屋流程：從條件整理、內見到重要事項說明",
        "keywords": ["日本買屋", "購屋流程", "內見", "重要事項説明", "売買契約", "引渡し"],
        "persona": "第一次在日本買房、跨境看房與需要中文陪同說明的買方",
        "points": [
            "先確認用途、地區、預算、現金/貸款、可接受築年數與站距，再開始案件篩選。",
            "內見時同時記錄採光、噪音、管理狀態、收納、垃圾置場、周邊超市與車站動線。",
            "重要事項說明要看權利、用途限制、管理費、修繕積立金、借地權與交屋條件。",
        ],
    },
    {
        "slug": "purchase-costs",
        "title": "日本買屋諸費用：仲介手數料、登記、印紙、稅金與保險",
        "keywords": ["諸費用", "仲介手數料", "登記費用", "不動產取得稅", "固定資產稅", "火災保險"],
        "persona": "已經鎖定案件、需要估總成本與現金流的買方",
        "points": [
            "購屋總成本不只成交價，還包含仲介、登記、稅費、保險、清算金與可能的貸款費用。",
            "中古マンション還要確認管理費、修繕積立金、滯納與未來大規模修繕計畫。",
            "投資案要用月租、空室率、管理費、修繕與稅費估實質收益，不只看表面利回り。",
        ],
    },
    {
        "slug": "loan-foreigner",
        "title": "外國人日本房貸：審查資料、在留資格、收入與頭期款",
        "keywords": ["日本房貸", "外國人貸款", "住宅ローン", "頭期款", "在留資格", "收入證明"],
        "persona": "希望用貸款購屋、在日工作者、海外收入或法人持有買方",
        "points": [
            "銀行通常會看在留資格、勤務年數、收入、信用紀錄、頭期款與物件擔保性。",
            "海外收入或非居民買方可行性差異很大，需先做可貸性預審再看總價。",
            "若以現金購屋，仍要準備本人確認、資金來源、匯款與交割時程。",
        ],
    },
    {
        "slug": "one-room-investment",
        "title": "1R/1K 單間買屋：投資出租與自住判斷",
        "keywords": ["單間", "1R", "1K", "ワンルーム", "区分マンション", "投資出租"],
        "persona": "單間投資、留學陪讀、低總價自用或初次入門買方",
        "points": [
            "1R/1K 要優先看駅徒歩、生活機能、管理費修繕積立金、築年數與出租需求。",
            "低總價不等於高收益，需扣掉管理費、修繕、租賃管理、空室與退租修繕。",
            "自住單間要看收納、採光、噪音、洗衣空間、宅配ボックス與夜間採買便利。",
        ],
    },
    {
        "slug": "single-floor-hiraya",
        "title": "單層與平屋買屋：一戶建、低樓層與無障礙需求",
        "keywords": ["單層", "平屋", "一戸建て", "低層", "無障礙", "階段なし"],
        "persona": "退休長住、家庭自住、膝蓋不便、希望低樓層或平屋的買方",
        "points": [
            "平屋/單層住宅要確認土地權利、建蔽率容積率、停車、修繕、隔熱與防災。",
            "マンション若偏好單層生活，可看低樓層、電梯、無障礙、垃圾動線與管理狀態。",
            "一樓案件要額外看採光、防潮、防犯、噪音與災害風險。",
        ],
    },
    {
        "slug": "station-walk-route",
        "title": "路線與徒歩分鐘：買屋搜尋如何看交通真實性",
        "keywords": ["駅徒歩", "徒歩5分", "交通路線", "沿線", "通勤", "乗換"],
        "persona": "通勤、自住、出租投資與重視站距的買方",
        "points": [
            "駅徒歩是搜尋入口，但實際要看坡道、紅綠燈、夜間安全、轉乘與末班車。",
            "出租投資常用徒歩5至10分作強訊號；徒歩15分以上要用價格、格局或商圈補強。",
            "看區域時要把 JR、私鐵、地下鐵、巴士與機場/新幹線距離分開評估。",
        ],
    },
    {
        "slug": "building-age-management",
        "title": "中古マンション管理品質：築年數、新耐震、修繕積立金",
        "keywords": ["中古マンション", "新耐震", "修繕積立金", "管理費", "長期修繕計画", "大規模修繕"],
        "persona": "中古マンション買方、投資客與重視風險控管的人",
        "points": [
            "築年數要搭配耐震基準、修繕履歷、積立金水準與管理組合狀態一起判斷。",
            "管理費過低不一定好，可能代表未來修繕費不足；過高則會壓縮收益。",
            "看房時要問大規模修繕、滯納、借入、管理方式與共用部維護。",
        ],
    },
    {
        "slug": "handover-shopping",
        "title": "買屋交屋後採買：家具家電、網路、水電瓦斯與維修",
        "keywords": ["交屋", "家具家電", "水電瓦斯", "網路", "配送", "入住清單"],
        "persona": "已成交或準備交屋、需要落地入住與出租佈置的買方",
        "points": [
            "交屋前先量家具家電尺寸、搬入路線、電梯與室內插座位置。",
            "自住要安排水電瓦斯、網路、家具家電、保險與鑰匙交接；出租要準備設備清單。",
            "附近 Nitori、家電量販店、百元店、超市與藥妝能縮短入住準備時間。",
        ],
    },
]


FAQ_ROWS = [
    (
        "第一次到日本生活，先買哪些用品？",
        "先分成睡眠、洗浴、廚房、清潔、網路與緊急用品。最先買床墊/被子、毛巾、洗衣用品、延長線、垃圾袋、基本鍋具與常備藥，再補家具。",
        ["入住清單", "生活用品", "Nitori", "百元店", "家電"],
    ),
    (
        "看日本房子時，購物生活圈要看什麼？",
        "至少看三層：最近便利店、步行10分內超市/藥妝、週末可到的大型商場或家電家具店。若是出租投資，這些會影響租客決策。",
        ["生活機能", "徒歩10分", "超市", "藥妝", "出租"],
    ),
    (
        "徒歩5分和徒歩15分，對購物便利差很多嗎？",
        "差異通常很明顯。徒歩5分適合單身、長者與夜間採買；徒歩15分若有腳踏車、巴士或大型商場也可接受，但出租話術要寫清楚。",
        ["徒歩5分", "徒歩15分", "駅近", "生活圈"],
    ),
    (
        "日本藥妝店免稅一定比較便宜嗎？",
        "不一定。免稅要看身分、金額、商品分類與店內折扣。住宅區大型店有時未免稅也比觀光區便宜，建議比較總價。",
        ["免稅", "藥妝", "折扣券", "觀光區"],
    ),
    (
        "大型家電能不能請店家送到家？",
        "多數大型家電店可配送，安裝、回收舊機、搬入吊掛和偏遠地區可能另收費。買房或租房前先量門寬、電梯和放置空間。",
        ["家電配送", "安裝", "搬入", "冷氣", "洗衣機"],
    ),
    (
        "沒有日本手機或地址時能買家具家電嗎？",
        "店面可先買小件；大型配送通常需要地址、聯絡電話和收貨時間。剛落地可先用飯店/臨時住處，正式入住後再安排大件。",
        ["日本手機", "家具", "配送", "地址"],
    ),
    (
        "日本超市晚上會打折嗎？",
        "許多超市熟食、便當、鮮食晚間會折扣，但時間因店而異。這是長住生活成本的小細節，也能反映生活圈成熟度。",
        ["超市折扣", "惣菜", "生活成本"],
    ),
    (
        "想買單間出租，購物知識怎麼用？",
        "單間租客通常重視便利店、超市、藥妝、車站與夜間安全。後台關鍵字可串 1R/1K、駅徒歩、コンビニ、スーパー。",
        ["單間", "1R", "1K", "租客", "便利店"],
    ),
    (
        "日本搬家後不要的家具怎麼處理？",
        "可查自治體粗大ゴミ、回收業者、二手店收購或平台轉售。費用與預約時間要提前安排，不能隨意丟在垃圾置場。",
        ["粗大ゴミ", "二手", "搬家", "回收"],
    ),
    (
        "生活機能可以跟房產案件怎麼關聯？",
        "關聯欄位可用地區、站名、徒歩分鐘、物件類型、交易類型、價格帶和關鍵字。客服可先問用途，再把生活圈匹配到案件。",
        ["案件關聯", "站名", "徒歩", "物件類型", "客服"],
    ),
]


SCENARIOS = [
    {
        "id": "shopping_move_in",
        "label": "日本入住採買",
        "keywords": ["入住", "生活用品", "家具", "家電", "Nitori", "百元店", "搬家"],
        "conclusion": "先把入住必需品拆成睡眠、清潔、廚房、通訊與大型家電，再依交屋/租約日倒排採買。",
        "bullets": ["先量尺寸與搬入路線", "大型家電先約配送", "小物可用百元店補齊"],
        "priority": 190,
    },
    {
        "id": "shopping_tax_free",
        "label": "藥妝與免稅購物",
        "keywords": ["免稅", "藥妝", "tax free", "護照", "Matsukiyo", "Welcia"],
        "conclusion": "先確認身分與商品分類，再比較免稅後總價；不要只用觀光區價格判斷生活成本。",
        "bullets": ["短期停留才常見免稅", "消耗品封袋規則需確認", "住宅區大型店可能更便宜"],
        "priority": 184,
    },
    {
        "id": "shopping_station_walk",
        "label": "駅近徒歩與生活機能",
        "keywords": ["徒歩", "駅近", "超市", "便利店", "生活機能", "1R", "1K"],
        "conclusion": "站距只是第一層，還要看超市、藥妝、便利店和夜間回家動線。",
        "bullets": ["徒歩5至10分最容易溝通", "徒歩15分要補交通或商圈理由", "單間租客尤其在意夜間採買"],
        "priority": 186,
    },
    {
        "id": "shopping_family_life",
        "label": "家庭自住生活圈",
        "keywords": ["家庭", "親子", "超市", "AEON", "公園", "學區", "醫院"],
        "conclusion": "家庭客戶要同時看日常採買、醫療、學校、公園和週末大型商場。",
        "bullets": ["超市與藥妝距離要短", "大件採買靠商場/home center", "垃圾置場與收納也要問"],
        "priority": 178,
    },
]


BUYER_SCENARIOS = [
    {
        "id": "buying_first_consult",
        "label": "日本買屋首次諮詢",
        "keywords": ["日本買屋", "購屋流程", "預算", "地區", "貸款", "現金"],
        "conclusion": "先問用途、預算、地區、現金/貸款、居住或投資，再把案件限制成買屋、物件類型、站距與更新時間。",
        "bullets": ["確認買屋用途", "拆預算與諸費用", "先給 3 至 5 個同條件案件"],
        "priority": 222,
    },
    {
        "id": "buying_one_room",
        "label": "1R/1K 單間買屋",
        "keywords": ["單間", "1R", "1K", "ワンルーム", "区分マンション", "駅徒歩"],
        "conclusion": "單間買屋先看駅徒歩、生活機能、管理費、修繕積立金、築年數與出租需求，避免只看總價低。",
        "bullets": ["扣除管理費與空室風險", "查徒歩與夜間採買", "確認管理狀態"],
        "priority": 218,
    },
    {
        "id": "buying_single_floor",
        "label": "單層/平屋/低樓層需求",
        "keywords": ["單層", "平屋", "一戸建て", "低層", "階段なし", "無障礙"],
        "conclusion": "偏好單層或平屋時，除價格外要看無障礙、災害風險、防潮、防犯、停車與修繕。",
        "bullets": ["辨識平屋/低樓層", "查災害與防犯", "確認生活採買動線"],
        "priority": 216,
    },
    {
        "id": "buying_station_walk",
        "label": "買屋交通路線與徒歩",
        "keywords": ["駅徒歩", "徒歩5分", "沿線", "通勤", "交通路線", "乗換"],
        "conclusion": "買屋搜尋要同時看站距、路線、轉乘、坡道、紅綠燈與夜間動線；徒歩5至10分通常最容易轉換。",
        "bullets": ["記錄路線與徒歩分鐘", "比較通勤時間", "用生活機能補足遠距案件"],
        "priority": 214,
    },
]


SUPPORT_QA = [
    {
        "id": "jp_shopping_move_in_first",
        "label": "剛到日本入住要買什麼",
        "keywords": ["剛到日本", "入住", "生活用品", "買什麼", "家具", "家電"],
        "answer": "可以先用「第一晚能住、第一週能生活、第一月再優化」來排。第一晚：被子/床墊、毛巾、洗浴、延長線、垃圾袋。第一週：洗衣、廚房、常備藥、收納。大型家電先確認尺寸與配送時間，再下單。",
        "priority": 210,
    },
    {
        "id": "jp_shopping_life_function_case",
        "label": "生活機能如何影響案件",
        "keywords": ["生活機能", "超市", "便利店", "藥妝", "徒歩", "案件"],
        "answer": "看案件時我會把生活機能拆成：站距、便利店、超市、藥妝、醫療/銀行、週末大型採買。單間租客通常最重視徒歩和便利店；家庭客更重視超市、學校、公園與收納。",
        "priority": 206,
    },
    {
        "id": "jp_shopping_tax_free",
        "label": "日本免稅與藥妝疑問",
        "keywords": ["免稅", "藥妝", "tax free", "護照", "折扣"],
        "answer": "免稅不是人人適用，也不一定最低價。要看停留資格、店家、金額門檻、商品分類和折扣券。若是長住或買房生活，建議更重視住宅區日常價格與店距。",
        "priority": 204,
    },
    {
        "id": "jp_shopping_appliance_delivery",
        "label": "日本家電配送安裝",
        "keywords": ["家電", "配送", "安裝", "冰箱", "洗衣機", "冷氣"],
        "answer": "大型家電通常可配送，但冷氣安裝、舊機回收、樓梯搬運、偏遠地區可能另收費。買之前先量門寬、電梯、樓梯、放置空間，避免送到現場不能搬入。",
        "priority": 200,
    },
    {
        "id": "jp_shopping_single_room",
        "label": "單間租客的購物生活需求",
        "keywords": ["單間", "1R", "1K", "租客", "便利店", "超市"],
        "answer": "單間/1R/1K 的租客多半重視下班後採買便利、車站距離、便利店、藥妝、洗衣與收貨。找案件時可以把「駅徒歩、スーパー、コンビニ、宅配ボックス」一起納入篩選。",
        "priority": 198,
    },
]


BUYER_SUPPORT_QA = [
    {
        "id": "jp_buying_flow_first",
        "label": "日本買屋流程怎麼開始",
        "keywords": ["日本買屋", "買房流程", "內見", "重要事項説明", "売買契約"],
        "answer": "先確認用途、地區、預算、現金/貸款、物件類型與站距，再看最新案件。基本流程是條件整理、案件篩選、內見、申込、重要事項說明、売買契約、貸款/交割、引渡し。",
        "priority": 230,
    },
    {
        "id": "jp_buying_costs",
        "label": "日本買屋除了房價還有什麼費用",
        "keywords": ["諸費用", "仲介手數料", "登記", "固定資產稅", "不動產取得稅"],
        "answer": "除了成交價，還要估仲介手數料、登記費、印紙稅、固定資產稅清算、不動產取得稅、火災保險、貸款費用，以及交屋後家具家電或修繕費。",
        "priority": 228,
    },
    {
        "id": "jp_buying_one_room_investment",
        "label": "1R/1K 單間買屋適合投資嗎",
        "keywords": ["1R", "1K", "ワンルーム", "單間", "投資", "表面利回り"],
        "answer": "單間適合入門，但要用實質收益看，不只看表面利回り。要扣管理費、修繕積立金、租賃管理費、空室、退租修繕與稅費，並確認駅徒歩與生活機能。",
        "priority": 226,
    },
    {
        "id": "jp_buying_single_floor_hiraya",
        "label": "想買單層或平屋怎麼找",
        "keywords": ["單層", "平屋", "一戸建て", "低樓層", "階段なし"],
        "answer": "可以用平屋、一戸建て、低層、階段なし、エレベーター、無障礙等條件組合。平屋要看土地權利、停車、修繕與災害風險；マンション低樓層要看防潮、防犯與採光。",
        "priority": 224,
    },
    {
        "id": "jp_buying_station_walk_filter",
        "label": "買屋為什麼要看徒歩分鐘和路線",
        "keywords": ["駅徒歩", "徒歩", "沿線", "通勤", "交通"],
        "answer": "徒歩分鐘直接影響自住便利與出租需求，但還要看實際路線、坡道、紅綠燈、夜間安全、轉乘和末班車。買屋搜尋建議先用徒歩5/10/15分分層比較。",
        "priority": 222,
    },
]


def to_hans(text: str) -> str:
    out = text or ""
    for old, new in HANS_REPL:
        out = out.replace(old, new)
    return out


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "item"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_body(title: str, persona: str, keywords: list[str], points: list[str], relation: str) -> str:
    lines = [
        title,
        "",
        f"客戶畫像：{persona}",
        f"常問問題：{title} 怎麼判斷？哪些店、路線與徒歩分鐘會影響自住或出租？",
        "",
        "知識要點：",
    ]
    lines.extend(f"- {p}" for p in points)
    lines.extend(
        [
            "",
            f"關聯案件資料：{relation}",
            f"後台關鍵字：{', '.join(keywords)}",
            "維護策略：此條目作為客服、智慧查詢、SEO 草稿與案件篩選的共用知識，後續可依搜尋紀錄調整權重。",
        ]
    )
    return "\n".join(lines)


def base_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for row in AREA_PROFILES:
        points = [
            f"主要購物與生活據點：{row['chains']}。",
            "看房時同時記錄最近車站、超市、藥妝、便利店與大型採買點。",
            "若案件是出租或單間，徒歩分鐘與夜間採買便利性要寫進推薦理由。",
        ]
        entries.append(
            {
                "slug": f"area-{row['slug']}",
                "title": row["title"],
                "body": build_body(row["title"], row["persona"], row["keywords"], points, row["case_link"]),
                "region": "tw",
                "keyword_type": "howto",
                "intent_target": "日本生活購物",
                "topic_category": "生活機能",
                "tags": ["日本購物", "生活機能", "商圈", row["region"], *row["keywords"]],
            }
        )
    for row in CATEGORY_GUIDES:
        entries.append(
            {
                "slug": f"category-{row['slug']}",
                "title": row["title"],
                "body": build_body(
                    row["title"],
                    row["persona"],
                    row["keywords"],
                    row["points"],
                    "關聯地區、駅徒歩、交易類型、單間/家庭/投資等客戶畫像，並可回填案件 keyword_tags。",
                ),
                "region": "tw",
                "keyword_type": "howto",
                "intent_target": "日本生活購物",
                "topic_category": "日本購物知識",
                "tags": ["日本購物", "常見問答", *row["keywords"]],
            }
        )
    for row in BUYER_GUIDES:
        entries.append(
            {
                "slug": f"buyer-{row['slug']}",
                "title": row["title"],
                "body": build_body(
                    row["title"],
                    row["persona"],
                    row["keywords"],
                    row["points"],
                    "關聯交易類型=買屋、物件類型=マンション/一戸建て、間取り=1R/1K/ワンルーム、階層/平屋、駅徒歩與地區篩選。",
                ),
                "region": "tw",
                "keyword_type": "howto",
                "intent_target": "日本買屋",
                "topic_category": "日本買屋知識",
                "tags": ["日本買屋", "買屋FAQ", "案件篩選", *row["keywords"]],
            }
        )
    for idx, (q, a, kws) in enumerate(FAQ_ROWS, 1):
        body = build_body(
            q,
            "正在考慮赴日看房、入住、投資出租或長住的華人客戶",
            kws,
            [a, "客服回答時先判斷使用者是短期購物、長住生活，還是和房產案件比較有關。", "回答後可引導補充地區、預算、站距與物件類型。"],
            "關聯 support_qa_training、keyword_search_stats、portal_case_search 的 region_hint/keyword/property_types。",
        )
        entries.append(
            {
                "slug": f"faq-{idx:02d}-{slugify(kws[0])}",
                "title": q,
                "body": body,
                "region": "tw",
                "keyword_type": "howto",
                "intent_target": "日本生活購物",
                "topic_category": "常見問答",
                "tags": ["日本購物", "FAQ", *kws],
            }
        )
    return entries


def expanded_entries(target_count: int) -> list[dict[str, Any]]:
    entries = base_entries()
    if len(entries) >= target_count:
        return entries[:target_count]
    cats = [*CATEGORY_GUIDES, *BUYER_GUIDES]
    areas = AREA_PROFILES
    personas = [
        "單身1R/1K租客",
        "家庭自住客",
        "退休長住客",
        "投資出租屋主",
        "短期看房與觀光購物客",
        "留學陪讀家庭",
        "買屋自住客",
        "單間投資買方",
        "平屋/單層偏好買方",
        "重視路線徒歩的通勤買方",
    ]
    idx = 1
    while len(entries) < target_count:
        area = areas[(idx - 1) % len(areas)]
        cat = cats[((idx - 1) // len(areas)) % len(cats)]
        persona = personas[(idx - 1) % len(personas)]
        title = f"{area['region']} x {cat['title']}：{persona}的判斷清單"
        kws = list(dict.fromkeys([area["region"], *area["keywords"][:3], *cat["keywords"][:4], persona]))
        points = [
            f"{area['region']}客戶先看 {area['chains']} 這類可落地的採買點。",
            f"{cat['title']} 對 {persona} 的重點是距離、價格、營業時間與配送/收貨便利。",
            "把徒歩分鐘、站名、超市/藥妝/便利店距離寫入案件摘要，可提升客服命中率。",
        ]
        entries.append(
            {
                "slug": f"matrix-{idx:03d}-{area['slug']}-{cat['slug']}-{slugify(persona)}",
                "title": title,
                "body": build_body(
                    title,
                    persona,
                    kws,
                    points,
                    f"關聯 {area['case_link']}；並把 {cat['slug']} 類關鍵字串到 keyword_search_stats。",
                ),
                "region": "tw",
                "keyword_type": "howto",
                "intent_target": "日本生活購物",
                "topic_category": "購物生活矩陣",
                "tags": ["日本購物", "生活機能", "客戶畫像", *kws],
            }
        )
        idx += 1
    return entries


def upsert_content_item(conn: sqlite3.Connection, entry: dict[str, Any]) -> str:
    ts = now_iso()
    slug = f"jp-shopping-{entry['slug']}"
    item_url = f"{SOURCE_URL}/{slug}"
    tags = ",".join(list(dict.fromkeys(str(x) for x in entry.get("tags", []) if str(x).strip()))[:24])
    title_hant = str(entry["title"]).strip()
    body_hant = str(entry["body"]).strip()
    title_hans = to_hans(title_hant)
    body_hans = to_hans(body_hant)
    schema_json = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title_hant,
            "about": entry.get("topic_category"),
            "keywords": tags,
            "inLanguage": "zh-Hant",
            "dateModified": ts,
            "isPartOf": SOURCE_NAME,
        },
        ensure_ascii=False,
    )
    conn.execute(
        """
        INSERT INTO source_items (
            source_name, source_category, source_url, item_url, title_original, body_original,
            language, published_at, access_status, access_note, last_checked_at,
            image_urls, content_kind
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        ON CONFLICT(item_url) DO UPDATE SET
            source_name = excluded.source_name,
            source_category = excluded.source_category,
            source_url = excluded.source_url,
            title_original = excluded.title_original,
            body_original = excluded.body_original,
            language = excluded.language,
            published_at = excluded.published_at,
            access_status = excluded.access_status,
            access_note = excluded.access_note,
            last_checked_at = CURRENT_TIMESTAMP,
            image_urls = excluded.image_urls,
            content_kind = excluded.content_kind
        """,
        (
            SOURCE_NAME,
            "知識庫",
            SOURCE_URL,
            item_url,
            title_hant,
            body_hant,
            "zh-Hant",
            ts,
            "public",
            "日本購物/生活機能/客戶畫像知識庫種子資料",
            "",
            CONTENT_KIND,
        ),
    )
    sid = int(conn.execute("SELECT id FROM source_items WHERE item_url = ?", (item_url,)).fetchone()["id"])
    exists = conn.execute("SELECT id FROM content_items WHERE source_item_id = ? ORDER BY id LIMIT 1", (sid,)).fetchone()
    seo_title = f"{title_hant}｜日本購物生活知識庫"
    seo_description = body_hant.replace("\n", " ")[:180]
    params = (
        title_hant,
        title_hans,
        body_hant,
        body_hans,
        str(entry.get("region") or "tw"),
        str(entry.get("keyword_type") or "howto"),
        str(entry.get("intent_target") or "日本生活購物"),
        str(entry.get("topic_category") or "日本購物知識"),
        tags[:500],
        slug,
        seo_title[:240],
        seo_description[:260],
        schema_json,
    )
    if exists:
        conn.execute(
            """
            UPDATE content_items
            SET title_zh_hant = ?, title_zh_hans = ?, body_zh_hant = ?, body_zh_hans = ?,
                region_code = ?, keyword_type = ?, intent_target = ?, topic_category = ?, keyword_tags = ?,
                seo_slug = ?, seo_title = ?, seo_description = ?, schema_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*params, int(exists["id"])),
        )
    else:
        conn.execute(
            """
            INSERT INTO content_items (
                source_item_id, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans,
                region_code, keyword_type, intent_target, topic_category, keyword_tags,
                seo_slug, seo_title, seo_description, schema_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (sid, *params),
        )
    return slug


def upsert_scenarios(conn: sqlite3.Connection) -> int:
    n = 0
    for item in [*SCENARIOS, *BUYER_SCENARIOS]:
        conn.execute(
            """
            INSERT INTO offline_support_scenarios (
                scene_id, label, keywords_json, conclusion, bullets_json, enabled, priority, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(scene_id) DO UPDATE SET
                label = excluded.label,
                keywords_json = excluded.keywords_json,
                conclusion = excluded.conclusion,
                bullets_json = excluded.bullets_json,
                enabled = 1,
                priority = excluded.priority,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                item["id"],
                item["label"],
                json.dumps(item["keywords"], ensure_ascii=False),
                item["conclusion"],
                json.dumps(item["bullets"], ensure_ascii=False),
                int(item["priority"]),
            ),
        )
        n += 1
    return n


def upsert_support_qa(conn: sqlite3.Connection) -> int:
    n = 0
    for item in [*SUPPORT_QA, *BUYER_SUPPORT_QA]:
        conn.execute(
            """
            INSERT INTO support_qa_training (qa_id, label, keywords_json, answer_body, enabled, priority, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(qa_id) DO UPDATE SET
                label = excluded.label,
                keywords_json = excluded.keywords_json,
                answer_body = excluded.answer_body,
                enabled = 1,
                priority = excluded.priority,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                item["id"],
                item["label"],
                json.dumps(item["keywords"], ensure_ascii=False),
                item["answer"],
                int(item["priority"]),
            ),
        )
        n += 1
    return n


def upsert_keywords(conn: sqlite3.Connection, entries: list[dict[str, Any]]) -> int:
    keywords: list[str] = []
    seeds = [
        "日本購物",
        "日本生活機能",
        "日本超市",
        "日本藥妝",
        "日本免稅",
        "東京超市 徒歩5分",
        "大阪 1R 生活機能",
        "福岡 移居 購物",
        "家電配送 日本",
        "Nitori 家具 入住",
        "Bic Camera 家電",
        "Daiso 百元店",
        "コンビニ 駅近",
        "ドラッグストア 免税",
        "スーパー 徒歩10分",
        "日本買屋",
        "日本買房流程",
        "中古マンション 購入",
        "新築マンション 購入",
        "一戸建て 購入",
        "1R 買屋",
        "1K ワンルーム 投資",
        "平屋 買屋",
        "単層 住宅",
        "駅徒歩5分 買屋",
        "管理費 修繕積立金",
        "外國人 日本房貸",
        "重要事項説明 中文",
        "表面利回り 實質收益",
    ]
    keywords.extend(seeds)
    for e in entries:
        keywords.extend(str(x) for x in e.get("tags", []) if str(x).strip())
    unique = list(dict.fromkeys(k.strip()[:120] for k in keywords if k.strip()))
    for idx, kw in enumerate(unique[:420], 1):
        channel = "knowledge_seed:japan_shopping"
        filters = {"source": "seed_japan_shopping_knowledge", "rank": idx}
        conn.execute(
            """
            INSERT INTO keyword_search_stats (keyword, channel, search_count, last_filters_json, last_searched_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(keyword, channel) DO UPDATE SET
                search_count = MAX(keyword_search_stats.search_count, excluded.search_count),
                last_filters_json = excluded.last_filters_json,
                last_searched_at = CURRENT_TIMESTAMP
            """,
            (kw, channel, max(3, 30 - min(idx, 27)), json.dumps(filters, ensure_ascii=False)),
        )
        if idx <= 220:
            conn.execute(
                """
                INSERT INTO keyword_search_logs (keyword, channel, filters_json, searched_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (kw, channel, json.dumps(filters, ensure_ascii=False)),
            )
    return len(unique[:420])


def write_report(report: dict[str, Any]) -> Path:
    out = ROOT / "data" / "japan_shopping_knowledge_seed_last.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    stamped = ROOT / "data" / f"japan_shopping_knowledge_seed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    stamped.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return stamped


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SCLAW backend knowledge base with Japan shopping/living FAQ, personas, and keywords.")
    parser.add_argument("--target-count", type=int, default=240)
    parser.add_argument("--retries", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=24)
    parser.add_argument("--skip-init", action="store_true", help="Skip init_db when another long writer is already running.")
    args = parser.parse_args()
    target_count = max(20, min(600, int(args.target_count or 240)))
    entries = expanded_entries(target_count)

    last_exc: Exception | None = None
    for attempt in range(max(1, int(args.retries or 1))):
        try:
            if not args.skip_init:
                init_db()
            with get_conn() as conn:
                batch_size = max(4, min(80, int(args.batch_size or 24)))
                slugs: list[str] = []
                for idx, entry in enumerate(entries, 1):
                    slugs.append(upsert_content_item(conn, entry))
                    if idx % batch_size == 0:
                        conn.commit()
                scenario_count = upsert_scenarios(conn)
                qa_count = upsert_support_qa(conn)
                keyword_count = upsert_keywords(conn, entries)
                conn.commit()
                kb_count = conn.execute(
                    "SELECT COUNT(1) AS c FROM source_items WHERE content_kind = ?",
                    (CONTENT_KIND,),
                ).fetchone()["c"]
                report = {
                    "ok": True,
                    "target_count": target_count,
                    "content_upserted": len(slugs),
                    "kb_total": int(kb_count or 0),
                    "scenarios_upserted": scenario_count,
                    "support_qa_upserted": qa_count,
                    "keywords_upserted": keyword_count,
                    "sample_slugs": slugs[:12],
                    "finished_at": now_iso(),
                }
                stamped = write_report(report)
                print(json.dumps({**report, "report": str(stamped)}, ensure_ascii=False), flush=True)
                return
        except sqlite3.OperationalError as exc:
            last_exc = exc
            time.sleep(0.35 * (2**attempt))
    raise SystemExit(f"failed after retries: {last_exc}")


if __name__ == "__main__":
    main()
