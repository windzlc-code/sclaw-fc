"""日本不動產案件：買／賣／租取向、日本區域、交通位置等顯示用推論（啟發式，非契約認定）。"""
from __future__ import annotations

import re
from typing import Any

# 日本廣域／常見區域關鍵字（標題／內文掃描）
_JP_REGION_PATTERNS: list[tuple[str, str]] = [
    ("首都圏", r"首都圏|首都圈"),
    ("關東", r"関東|關東|Kanto|kanto"),
    ("關西", r"関西|關西|Kansai|kansai"),
    ("北海道", r"北海道|Hokkaido|hokkaido"),
    ("東北", r"東北|东北|Tohoku|tohoku"),
    ("甲信越", r"甲信越"),
    ("北陸", r"北陸|北陆|Hokuriku|hokuriku"),
    ("東海", r"東海|东海|Tokai|tokai"),
    ("中國地方", r"中國地方|中国地方|Chugoku|chugoku"),
    ("四國", r"四國|四国|Shikoku|shikoku"),
    ("九州", r"九州|Kyushu|kyushu"),
    ("沖繩", r"沖繩|冲绳|沖縄|Okinawa|okinawa"),
    ("東京", r"東京|东京|Tokyo|tokyo|２３区|23区"),
    ("大阪", r"大阪|Osaka|osaka"),
    ("名古屋", r"名古屋|Nagoya|nagoya"),
    ("福岡", r"福岡|福冈|Fukuoka|fukuoka"),
    ("神奈川", r"神奈川|神奈川县|Kanagawa|kanagawa"),
    ("埼玉", r"埼玉|さいたま|Saitama|saitama"),
    ("千葉", r"千葉|千叶|Chiba|chiba"),
    ("横滨", r"横浜|横滨|Yokohama|yokohama"),
    ("川崎", r"川崎|Kawasaki|kawasaki"),
    ("京都市", r"京都市|京都府|Kyoto|kyoto"),
]

# 前端「日本區域」下拉選單標籤（與 _JP_REGION_PATTERNS 對齊）
JP_AREA_FILTER_LABELS: list[str] = [label for label, _ in _JP_REGION_PATTERNS]


_GENERATED_LISTING_CACHE_PREFIXES = ("日本房產案源", "日本房产案源")


def _skip_generated_listing_cache(value: Any) -> str:
    s = str(value or "")
    return "" if any(s.lstrip().startswith(prefix) for prefix in _GENERATED_LISTING_CACHE_PREFIXES) else s


def _blob(row: dict[str, Any]) -> str:
    bo = str(row.get("body_original") or "")
    parts = [
        str(row.get("title_zh_hant") or ""),
        str(row.get("title_zh_hans") or ""),
        str(row.get("seo_title") or ""),
        _skip_generated_listing_cache(row.get("body_zh_hant"))[:800],
        _skip_generated_listing_cache(row.get("body_zh_hans"))[:800],
        bo[:1500],
        str(row.get("title_original") or ""),
        str(row.get("item_url") or ""),
    ]
    return "\n".join(parts)


_URL_REGION_HINTS: list[tuple[str, str]] = [
    (r"tokyo", "東京"),
    (r"osaka", "大阪"),
    (r"nagoya", "名古屋"),
    (r"fukuoka", "福岡"),
    (r"yokohama", "横滨"),
    (r"kawasaki", "川崎"),
    (r"saitama", "埼玉"),
    (r"chiba", "千葉"),
    (r"kanagawa", "神奈川"),
    (r"hokkaido", "北海道"),
    (r"kyoto", "京都市"),
    (r"kobe", "關西"),
    (r"nara", "關西"),
]


def _regions_from_url(url: str) -> list[str]:
    u = (url or "").lower()
    out: list[str] = []
    for pat, label in _URL_REGION_HINTS:
        if re.search(pat, u, re.I):
            if label not in out:
                out.append(label)
    return out[:4]


def _transit_blob_strip_boilerplate(blob: str) -> str:
    """移除本站摘要／免責模板行，避免誤匹配為交通說明。"""
    out_lines: list[str] = []
    for ln in str(blob or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if "用途：" in s and ("摘要" in s or "索引" in s or "导览" in s or "導覽" in s):
            continue
        if "站內摘要" in s or "站内摘要" in s or "連結索引" in s or "链接索引" in s:
            continue
        if "不主張為完整" in s or "不主张为完整" in s:
            continue
        out_lines.append(s)
    return "\n".join(out_lines)


def _looks_like_site_disclaimer_transit(s: str) -> bool:
    t = str(s or "").strip()
    if not t:
        return False
    if "用途：" in t or "站內摘要" in t or "站内摘要" in t:
        return True
    if "連結索引" in t or "链接索引" in t or "不主張" in t or "不主张" in t:
        return True
    if "契約內容" in t or "契约内容" in t:
        return True
    if "綜合信息" in t or "综合信息" in t or "資訊網站" in t or "信息网站" in t or "信息網站" in t:
        return True
    if "來源網站" in t or "来源网站" in t:
        return True
    if ("不動産" in t or "不动产" in t) and ("住宅" in t) and ("情報" in t or "信息" in t):
        return True
    return False


def transit_line_looks_substantive(s: str) -> bool:
    """交通一行須像真實路線／駅／徒步分，排除門戶行銷語、導覽句。"""
    t = str(s or "").strip()
    if not t or _looks_like_site_disclaimer_transit(t):
        return False
    if "駅" in t or "站" in t:
        return True
    if re.search(r"(?:徒歩|步行)\s*\d", t):
        return True
    if re.search(r"(?:JR|ＪＲ|メトロ|地下鉄|地下铁|地鐵|地铁|京急|東急|小田急|西武|阪急|名古屋)", t):
        return True
    if "線" in t and re.search(r"\d{1,3}\s*分", t):
        return True
    return False


def gloss_jp_property_line_for_zh(s: str) -> str:
    """
    將 SUUMO／門戶常見日文交通、設施字串轉成繁中讀者友善助讀（非機器全文翻譯）。
    駅名仍多為漢字與假名並列，與台灣旅客閱讀習慣較接近。
    """
    t = str(s or "").strip()
    if not t:
        return ""
    out = t
    repl = [
        ("東京メトロ", "東京地鐵"),
        ("大阪メトロ", "大阪地鐵"),
        ("名古屋市営", "名古屋市營"),
        ("都営", "都營"),
        ("地下鉄", "地下鐵"),
        ("地下鐵", "地下鐵"),
        ("ＪＲ", "JR"),
        ("バス", "巴士"),
        ("徒歩", "步行"),
        ("階建", "層樓建築"),
        ("階部分", "樓層"),
        ("専有面積", "專有面積"),
        ("万円", "萬日圓"),
    ]
    for a, b in repl:
        out = out.replace(a, b)
    out = out.replace("区", "區")
    out = re.sub(r"([\u3040-\u9FFF・「」ー々〆ゞ゛゜]+)駅", r"\1站", out)
    out = re.sub(r"駅(\s*[，、])", r"站\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out[:260]


def infer_transit_line_zh(row: dict[str, Any]) -> str:
    """擷取一行交通相關描述（駅・路線・徒歩分）。"""
    text = _transit_blob_strip_boilerplate(_blob(row))
    m = re.search(r"[^\n。]{0,12}(?:駅|站|徒歩\d+分|步行\d+分|線)[^\n。]{0,40}", text)
    if m:
        cand = m.group(0).strip()[:80]
        if transit_line_looks_substantive(cand):
            return cand
    m1b = re.search(r"[\u3040-\u9FFF]{1,8}駅(?:\s|　)*(?:徒歩|步行)?\d{1,3}分?", text)
    if m1b:
        cand = m1b.group(0).strip()[:80]
        if transit_line_looks_substantive(cand):
            return cand
    m1c = re.search(r"(?:徒歩|步行)\d{1,3}分", text)
    if m1c:
        cand = m1c.group(0).strip()[:80]
        if transit_line_looks_substantive(cand):
            return cand
    m2 = re.search(r"(?:JR|地下鐵|地铁|地鐵|京急|東急|小田急|西武|阪急)[^\n。]{0,30}", text)
    if m2:
        cand = m2.group(0).strip()[:80]
        if transit_line_looks_substantive(cand):
            return cand
    m3 = re.search(
        r"(.{1,36}?線[^\n。]{0,36}?「[^」]{1,16}」[^\n。]{0,18}?(?:徒歩|歩)\s*\d{1,3}\s*分)",
        text,
    )
    if m3:
        cand = m3.group(1).strip()[:80]
        if transit_line_looks_substantive(cand):
            return cand
    return ""


def infer_jp_region_labels(row: dict[str, Any]) -> list[str]:
    blob = _blob(row)
    out: list[str] = []
    seen: set[str] = set()
    for label, pat in _JP_REGION_PATTERNS:
        if label in seen:
            continue
        if re.search(pat, blob, re.I):
            seen.add(label)
            out.append(label)
    return out[:6]


def infer_transaction_side(row: dict[str, Any]) -> str:
    """
    傳回 buy | sell | rent | unknown
    以 URL 路徑與中日標題／內文關鍵字粗分（列表頁多為租賃或買賣物件入口）。
    """
    url = str(row.get("item_url") or "").lower()
    blob = _blob(row).lower()

    if "chintai" not in url and (
        "/mansion/b-" in url
        or "homes.co.jp/mansion" in url
        or "athome.co.jp/mansion" in url
        or "suumo.jp/ms/" in url
        or "/shinchiku/" in url
        or "/kodate/" in url
    ):
        return "buy"
    if "chintai" in url or "賃貸" in blob or "租赁" in blob or "租屋" in blob or "rent" in blob:
        return "rent"
    if "baibai" in url or "/ms/" in url or "shinchiku" in url or "ikkodate" in url:
        if "chintai" in url:
            return "rent"
        return "buy"
    if "/chintai" in url or "suumo.jp/chintai" in url or "athome.co.jp/chintai" in url:
        return "rent"
    if "売却" in blob or "賣出" in blob or "賣方" in blob or "让渡" in blob or "出售" in blob:
        return "sell"
    if "購入" in blob or "買屋" in blob or "買房" in blob or "買賣" in blob or "買う" in blob:
        return "buy"
    ck = str(row.get("content_kind") or "").lower()
    if ck == "jp_listing":
        if "chintai" in url or "/chintai" in url:
            return "rent"
        return "buy"
    return "unknown"


def transaction_label_zh(side: str) -> str:
    return {
        "buy": "買賣／購屋",
        "sell": "賣出／釋出",
        "rent": "租屋（賃貸：賃=租借/租用，貸=借出/出租）",
        "unknown": "未標示",
    }.get((side or "").strip(), "未標示")


def infer_case_metadata(row: dict[str, Any]) -> dict[str, Any]:
    otx = str(row.get("case_transaction_override") or "").strip().lower()
    if otx in ("buy", "sell", "rent"):
        side = otx
    else:
        side = infer_transaction_side(row)
    oreg = str(row.get("case_jp_region_override") or "").strip()
    if oreg:
        regions = [x.strip() for x in re.split(r"[,，、]", oreg) if x.strip()][:8]
    else:
        regions = infer_jp_region_labels(row)
    otr = str(row.get("case_transit_override") or "").strip()
    bind_sid = int(row.get("jp_station_id") or 0)
    bind_walk = int(row.get("walk_min") or 0)
    bln = str(row.get("jp_bind_line_name") or "").strip()
    bsn = str(row.get("jp_bind_station_name") or "").strip()
    if bind_sid > 0 and bln and bsn:
        transit = f"{bln} {bsn}駅"
        if bind_walk > 0:
            transit += f" 徒步{bind_walk}分"
        transit = transit[:200]
    elif otr:
        transit = otr[:200]
    else:
        transit = infer_transit_line_zh(row)
    if _looks_like_site_disclaimer_transit(transit):
        transit = ""
    if transit and not transit_line_looks_substantive(transit):
        transit = ""
    rc = str(row.get("region_code") or "").strip()
    return {
        "transaction_side": side,
        "transaction_label_zh": transaction_label_zh(side),
        "jp_region_labels": regions,
        "jp_region_display_zh": "、".join(regions) if regions else "",
        "transit_line_zh": transit,
        "region_code": rc,
    }


def transaction_sql_clause(side: str) -> tuple[str, list[Any]]:
    """回傳 (SQL 片段, 參數列)；side 為 buy|sell|rent|''。"""
    s = (side or "").strip().lower()
    if s == "rent":
        return (
            "("
            "instr(lower(COALESCE(s.item_url,'')),'chintai')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'/chintai')>0 "
            "OR c.title_zh_hant LIKE ? OR c.title_zh_hans LIKE ? OR c.body_zh_hant LIKE ?"
            ")",
            ["%賃貸%", "%租%", "%賃貸%"],
        )
    if s == "buy":
        # 買屋：保留 URL 關鍵字與中文標題；並納入七大門戶常見買賣網域（排除 chintai 路徑＝排除租賃主站）。
        # 先前僅依 baibai/chuko//ms/ 等，會漏掉 HOMES／AtHome／Yahoo／楽天 等僅含 mansion 路徑的買賣物件。
        return (
            "("
            "instr(lower(COALESCE(s.item_url,'')),'baibai')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')) ,'/ms/')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'toushi')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'chuko')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'suumo.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'homes.co.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'rehouse.co.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'athome.co.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'realestate.yahoo.co.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'realestate.rakuten.co.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'yes1.co.jp')>0 "
            "OR (instr(lower(COALESCE(s.item_url,'')),'oheya-su.jp')>0 "
            "     AND instr(lower(COALESCE(s.item_url,'')),'chintai')=0) "
            "OR instr(COALESCE(s.title_original,''),'中古')>0 "
            "OR instr(COALESCE(s.title_original,''),'売買')>0 "
            "OR instr(COALESCE(s.title_original,''),'購入')>0 "
            "OR c.title_zh_hant LIKE ? OR c.title_zh_hant LIKE ? OR c.body_zh_hant LIKE ? "
            "OR c.title_zh_hant LIKE ? OR c.title_zh_hans LIKE ? "
            "OR c.title_zh_hant LIKE ? OR c.title_zh_hans LIKE ?"
            ") AND instr(lower(COALESCE(s.item_url,'')),'chintai')=0 "
            "AND instr(lower(COALESCE(s.item_url,'')),'/chintai')=0",
            [
                "%買賣%",
                "%購入%",
                "%中古%",
                "%購買%",
                "%購買%",
                "%二手%",
                "%二手%",
            ],
        )
    if s == "sell":
        return (
            "(c.title_zh_hant LIKE ? OR c.title_zh_hans LIKE ? OR c.body_zh_hant LIKE ?)",
            ["%売却%", "%賣出%", "%売却%"],
        )
    return ("1=1", [])
