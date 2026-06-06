from collections import OrderedDict


TAG_MAP: "OrderedDict[str, list[str]]" = OrderedDict(
    [
        ("房地產", ["不動產", "房地產", "物件", "マンション", "住宅", "租金", "區域", "地價"]),
        ("投資", ["投資", "收益", "報酬", "利回", "回報", "資產配置", "資本利得"]),
        ("稅務", ["稅", "稅務", "國稅", "資本利得稅", "印花", "所得稅"]),
        ("貸款", ["貸款", "房貸", "融資", "利率", "審核", "按揭"]),
        ("政策", ["政策", "規範", "法規", "國土交通省", "外國人", "許可"]),
    ]
)


def classify_content(title: str, body: str, source_category: str) -> dict[str, str]:
    text = f"{title} {body} {source_category}".lower()
    score: dict[str, int] = {k: 0 for k in TAG_MAP}
    matched_tags: list[str] = []

    for category, keywords in TAG_MAP.items():
        for kw in keywords:
            if kw.lower() in text:
                score[category] += 1
                matched_tags.append(kw)

    intent_target = max(score, key=score.get)
    if score[intent_target] == 0:
        intent_target = "房地產"

    if intent_target == "政策":
        topic_category = "官方制度"
    elif intent_target == "投資":
        topic_category = "投資分析"
    elif intent_target == "稅務":
        topic_category = "稅務法規"
    elif intent_target == "貸款":
        topic_category = "貸款流程"
    else:
        topic_category = "市場資訊"

    keyword_type = "forecast" if intent_target == "投資" else "howto"
    dedup_tags = list(dict.fromkeys(matched_tags))
    keyword_tags = ",".join(dedup_tags[:8]) if dedup_tags else "日本房地產,海外購屋"
    return {
        "intent_target": intent_target,
        "topic_category": topic_category,
        "keyword_type": keyword_type,
        "keyword_tags": keyword_tags,
    }


def infer_region_code(title: str, body: str) -> str:
    text = f"{title} {body}"
    if any(x in text for x in ["香港", "hong kong", "hk"]):
        return "hk"
    if any(x in text for x in ["中國", "中国", "china", "cn"]):
        return "cn"
    if any(x in text for x in ["新加坡", "馬來西亞", "马来西亚", "泰國", "泰国", "東南亞", "东南亚"]):
        return "sg"
    return "tw"
