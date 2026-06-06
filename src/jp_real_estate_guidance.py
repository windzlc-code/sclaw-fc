# -*- coding: utf-8 -*-
"""Built-in guided FAQ answers for Japanese real-estate support/search."""

from __future__ import annotations

from typing import Any


OFFICIAL_REFERENCE_LINKS: list[dict[str, str]] = [
    {
        "label": "國稅廳：登錄免許稅稅額表",
        "url": "https://www.nta.go.jp/taxes/shiraberu/taxanswer/inshi/7191.htm",
    },
    {
        "label": "東京都主稅局：不動產取得稅",
        "url": "https://www.tax.metro.tokyo.lg.jp/kazei/real_estate/fudosan/syutokuzei",
    },
    {
        "label": "東京都主稅局：固定資產稅・都市計畫稅",
        "url": "https://www.tax.metro.tokyo.lg.jp/shitsumon/real_estate/o",
    },
]


JP_REAL_ESTATE_GUIDANCE_FAQS: list[dict[str, Any]] = [
    {
        "qa_id": "jp_property_tax_cost_overview",
        "label": "日本買房稅金與交易成本",
        "priority": 320,
        "keywords": [
            "稅金",
            "税金",
            "稅費",
            "税费",
            "費用",
            "费用",
            "持有成本",
            "不動產取得稅",
            "不动产取得税",
            "固定資產稅",
            "固定资产税",
            "都市計畫稅",
            "都市计划税",
            "登錄免許稅",
            "登録免許税",
            "印花稅",
            "印纸税",
            "仲介費",
            "中介费",
        ],
        "answer_body": (
            "日本買房稅金不能只用成交價抓固定百分比，因為很多稅是看「固定資產稅評價額」。\n"
            "先抓大方向：購入時有印花稅、登錄免許稅、不動產取得稅、司法書士/登記費與仲介費；持有中有固定資產稅、都市計畫稅、管理費與修繕積立金。\n"
            "粗估預算可先用「物件價格＋約 6%～10% 購入雜費」抓保守值，正式金額再由司法書士或稅理士依所在地與評價額試算。\n\n"
            "官方參考：\n"
            "- 國稅廳 登錄免許稅：https://www.nta.go.jp/taxes/shiraberu/taxanswer/inshi/7191.htm\n"
            "- 東京都 不動產取得稅：https://www.tax.metro.tokyo.lg.jp/kazei/real_estate/fudosan/syutokuzei\n"
            "- 東京都 固定資產稅/都市計畫稅：https://www.tax.metro.tokyo.lg.jp/shitsumon/real_estate/o\n\n"
            "{links}"
        ),
    },
    {
        "qa_id": "jp_property_buying_process",
        "label": "日本房地產購買流程",
        "priority": 300,
        "keywords": [
            "購買流程",
            "购买流程",
            "買房流程",
            "买房流程",
            "購屋流程",
            "如何購買",
            "如何购买",
            "怎麼買",
            "怎么买",
            "怎樣買",
            "採購",
            "采购",
            "下斡旋",
            "斡旋",
            "簽約",
            "签约",
            "過戶",
            "过户",
            "交割",
        ],
        "answer_body": (
            "日本房地產購買建議先拆流程，不要一開始就只看物件連結。\n"
            "基本順序是：先確認用途與預算，再做物件初篩與成本試算，接著看屋/調查，最後才是出價斡旋、簽約、匯款與司法書士登記。\n"
            "每一步都要一起看稅費、管理費、修繕積立金、貸款可行性與交屋後管理，這樣比較不會只被售價吸引。\n\n"
            "{links}"
        ),
    },
    {
        "qa_id": "jp_property_foreigner_rules",
        "label": "外國人購買日本房地產資格",
        "priority": 285,
        "keywords": [
            "外國人",
            "外国人",
            "海外人士",
            "台灣人",
            "台湾人",
            "香港人",
            "新加坡人",
            "中國人",
            "中国人",
            "需要簽證",
            "需要签证",
            "永久產權",
            "永久产权",
            "限制",
            "可以買嗎",
            "能买吗",
            "買日本房",
        ],
        "answer_body": (
            "一般來說，外國人可以在日本購買不動產，常見住宅或公寓不會只因國籍被禁止購買。\n"
            "但要分開看：能不能貸款、能不能合法出租、是否涉及短租限制、以及稅務/管理責任，這些不會因為買得到房就自動解決。\n"
            "現金購買通常較單純；若要貸款，就要看居留資格、收入文件、信用與銀行政策。\n\n"
            "{links}"
        ),
    },
    {
        "qa_id": "jp_property_loan_guidance",
        "label": "日本房貸與海外買家貸款",
        "priority": 270,
        "keywords": [
            "貸款",
            "贷款",
            "房貸",
            "房贷",
            "按揭",
            "利率",
            "頭期款",
            "头期款",
            "自備款",
            "自备款",
            "銀行",
            "银行",
            "可貸",
            "可贷",
            "月付",
        ],
        "answer_body": (
            "日本房貸要先看買方身份與收入文件，海外買家不一定能直接套用日本本地一般住宅貸款條件。\n"
            "實務上會看居留資格、收入來源、自備款比例、物件地區/屋齡，以及銀行對海外買家的政策。\n"
            "若貸款不確定，建議先用保守現金流估算，把匯率、利率、稅費、管理費與修繕一起放進月支出。\n\n"
            "{links}"
        ),
    },
    {
        "qa_id": "jp_property_holding_cost_risk",
        "label": "日本房地產持有成本與風險",
        "priority": 255,
        "keywords": [
            "持有成本",
            "風險",
            "风险",
            "管理費",
            "管理费",
            "修繕",
            "修缮",
            "空置",
            "屋況",
            "屋况",
            "避坑",
            "陷阱",
            "租金回報",
            "租金回报",
            "報酬率",
            "报酬率",
            "roi",
        ],
        "answer_body": (
            "日本房地產不能只看表面租金或總價，要用「淨收益」和「日後好不好轉手」一起判斷。\n"
            "常見持有成本包含固定資產稅/都市計畫稅、管理費、修繕積立金、室內修繕、租賃管理費、保險、空置與換租成本。\n"
            "風險先看三件事：站距與租客需求、屋齡/耐震/管理組合、租金是否用保守行情估算。\n\n"
            "{links}"
        ),
    },
]


def _norm(s: str) -> str:
    return str(s or "").strip().lower().replace(" ", "")


def match_jp_real_estate_guidance(query: str) -> dict[str, Any] | None:
    text = _norm(query)
    if not text:
        return None
    best: tuple[int, int, dict[str, Any] | None] = (0, 0, None)
    for row in JP_REAL_ESTATE_GUIDANCE_FAQS:
        hits = 0
        for kw in row.get("keywords") or []:
            k = _norm(str(kw))
            if k and k in text:
                hits += 1
        if hits <= 0:
            continue
        score = hits * 100 + int(row.get("priority") or 0)
        if score > best[0]:
            best = (score, hits, row)
    return best[2]


def guidance_block_for_prompt(query: str, *, max_chars: int = 4200) -> str:
    row = match_jp_real_estate_guidance(query)
    if not row:
        return ""
    body = str(row.get("answer_body") or "").replace("{links}", "").strip()
    text = (
        "【內建日本房地產常見問答引導】\n"
        f"命中主題：{row.get('label') or ''}\n"
        "使用方式：請先直接回答使用者問題，內容保持短版引導式；最後最多只問 1 個下一步問題，"
        "若站內知識庫有摘錄，請融合摘錄，不要只說「請輸入人工」。\n\n"
        f"{body}"
    )
    return text[:max_chars]


def guidance_dialog_payload(query: str) -> dict[str, Any] | None:
    row = match_jp_real_estate_guidance(query)
    if not row:
        return None
    body = str(row.get("answer_body") or "").replace("{links}", "").strip()
    paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
    bullets: list[str] = []
    for p in paragraphs:
        if p.startswith("官方參考"):
            break
        if p.startswith("- "):
            continue
        bullets.append(p[:160])
        if len(bullets) >= 4:
            break
    return {
        "title": str(row.get("label") or "日本房地產常見問題"),
        "bullets": bullets,
        "links": OFFICIAL_REFERENCE_LINKS if row.get("qa_id") == "jp_property_tax_cost_overview" else [],
        "voice_script": "。".join(bullets[:3])[:900],
        "matched_qa_id": str(row.get("qa_id") or ""),
    }
