"""站內日本房產門戶案件查詢（多來源）：篩選邏輯對齊案件管理 API（買／賣／租）。"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse, unquote

from src.case_metadata import (
    JP_AREA_FILTER_LABELS,
    _looks_like_site_disclaimer_transit,
    infer_case_metadata,
    transit_line_looks_substantive,
    transaction_sql_clause,
)
from src.coverage_matrix_sql import CASE_INV_FRESH_TS
from src.coverage_matrix_sql import CASE_INV_JP_LISTING_SQL
from src.coverage_matrix_sql import coverage_host_where_sql
from src.coverage_matrix_sql import coverage_region_where_sql
from src.db import get_conn
from src.homes_media_filter import is_homes_non_property_media, media_entry_url_context
from src.jp_listing_region_index import REGION_INDEX_SEARCH_KEYS, normalize_region_index_search_key
from src.portal_media_filter import (
    is_portal_non_property_image_url,
    is_suumo_non_property_image_url as _shared_is_suumo_non_property_image_url,
)

# 與「日本區域」下拉、巡檢矩陣地區列一致，供關鍵字首詞推斷
_JP_AREA_LABEL_SET: frozenset[str] = frozenset(x for x in JP_AREA_FILTER_LABELS if str(x or "").strip())
_SMART_QUERY_GENERIC_GEO_KEYWORDS: frozenset[str] = frozenset(
    {"不動産", "不動產", "不动産", "不动产", "房產", "房产", "房屋", "賃貸", "物件", "住宅", "公寓", "大樓"}
)
_SMART_QUERY_REGION_ALIASES: dict[str, str] = {
    "关东": "關東",
    "关西": "關西",
    "冲绳": "沖繩",
    "中国地方": "中國地方",
    "中國": "中國地方",
    "中国": "中國地方",
    "中國地區": "中國地方",
    "中国地区": "中國地方",
    "東京都": "東京",
    "東京市": "東京",
    "大阪府": "大阪",
    "福岡県": "福岡",
    "福岡縣": "福岡",
    "神奈川県": "神奈川",
    "神奈川縣": "神奈川",
    "埼玉県": "埼玉",
    "埼玉縣": "埼玉",
    "千葉県": "千葉",
    "千葉縣": "千葉",
    "京都府": "京都市",
    "横浜": "横滨",
    "横浜市": "横滨",
    "名古屋市": "名古屋",
}

# 無標點「都道府県＋…区＋町丁」：細地址查詢不可再擴成 /tokyo/ 等路徑 OR，否則整都道府縣全中
_JP_PREF_WARD_TAIL = re.compile(r"^(.+?(?:都|道|府|県))(.+?区)(.+)$")


def _is_jp_address_level_query(kw: str) -> bool:
    t = (kw or "").strip()
    if len(t) < 7:
        return False
    m = _JP_PREF_WARD_TAIL.match(t)
    if not m:
        return False
    a, b, c = m.group(1), m.group(2), m.group(3)
    return min(len(a), len(b), len(c)) >= 1 and min(len(a), len(b)) >= 2


def _normalize_smart_query_geo_label(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if s in _SMART_QUERY_REGION_ALIASES:
        return _SMART_QUERY_REGION_ALIASES[s]
    stripped = re.sub(r"[都道府県縣市]$", "", s)
    if stripped in _SMART_QUERY_REGION_ALIASES:
        return _SMART_QUERY_REGION_ALIASES[stripped]
    if s in _JP_AREA_LABEL_SET:
        return s
    if stripped in _JP_AREA_LABEL_SET:
        return stripped
    return stripped or s


def _normalize_region_index_focus_key(raw: str) -> str:
    key = normalize_region_index_search_key(raw)
    return key if key in REGION_INDEX_SEARCH_KEYS else ""


def _region_hint_index_keys(raw: str) -> list[str]:
    s = str(raw or "").strip()
    if not s:
        return []
    candidates = [s]
    if any(sep in s for sep in ("・", "/", "／", "、", ",")):
        candidates.extend(x.strip() for x in re.split(r"[・/／、,]+", s) if x.strip())
    if s in ("甲信越・北陸", "甲信越/北陸", "甲信越／北陸"):
        candidates.extend(["甲信越", "北陸"])
    out: list[str] = []
    for cand in candidates:
        key = _normalize_region_index_focus_key(cand) or _normalize_smart_query_geo_label(cand)
        if key in REGION_INDEX_SEARCH_KEYS and key not in out:
            out.append(key)
    return out


def _extract_simple_geo_focus_keyword(kw: str) -> str:
    tokens = _portal_keyword_tokens(kw)
    if not tokens:
        return ""
    non_generic = [tok for tok in tokens if tok not in _SMART_QUERY_GENERIC_GEO_KEYWORDS]
    if len(non_generic) != 1:
        return ""
    focus = str(non_generic[0] or "").strip()
    if not focus:
        return ""
    if not all(tok == focus or tok in _SMART_QUERY_GENERIC_GEO_KEYWORDS for tok in tokens):
        return ""
    return focus


def _simple_geo_focus_like_terms(focus: str) -> list[str]:
    raw = str(focus or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _push(term: str) -> None:
        s = str(term or "").strip()
        if not s:
            return
        key = s.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(s)

    _push(raw)
    normalized = _normalize_smart_query_geo_label(raw)
    _push(normalized)
    if normalized and not re.search(r"[都道府県縣市]$", raw):
        _push(f"{normalized}県")
        _push(f"{normalized}縣")
        _push(f"{normalized}市")
    return out[:4]

# AtHome：`/mansion/shinchiku/{id}/`、`/mansion/{id}/`、`/mansion/chuko/…/{id}`、`/kodate/{id}`
_ATHOME_LISTING_DETAIL_URL_RE = re.compile(
    r"(?ix)/(?:mansion/shinchiku/\d{5,}|mansion/\d{5,}|mansion/chuko/.+/\d{5,}|kodate/\d{5,})(?:/|\?|$)"
)


def _is_percent_encoding_valid(text: str) -> bool:
    s = str(text or "")
    # 禁止殘缺百分比編碼（如 img% / %A）
    return re.search(r"%(?:$|[^0-9A-Fa-f]|[0-9A-Fa-f]$)", s) is None


def _clean_snippet_text(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"\[(?:財產調查圖片網址|物件參考圖像\s*URL)\]?\s*", " ", s, flags=re.IGNORECASE)
    # 兼容完整與截斷版（resizeImage / resizeIm / resize...）
    s = re.sub(r"https?://img\d*\.suumo\.com/jj/resize[a-z]*[^\s\]\)'\"]*", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"src\s*=\s*[^\s\]\)'\"]+", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"gazo%2F[^\s\]\)'\"]*", " ", s, flags=re.IGNORECASE)
    s = _clean_translation_noise(s)
    return re.sub(r"\s+", " ", s).strip()


def _clean_translation_noise(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"(?i)\bjavascript\s+is\s+disabled\b", " ", s)
    s = re.sub(r"(?i)\bplease\s+enable\s+javascript\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _portal_host_clause(portal: str) -> tuple[str, list[Any], list[str]]:
    alias_map = {
        "yahoo_realestate": "yahoo",
        "yahoo!": "yahoo",
        "rakuten_realestate": "rakuten",
        "lifull": "homes",
        "liful": "homes",
        "yesstation": "yes1",
        "oheyasu": "oheya_su",
        "oheya-su": "oheya_su",
    }
    rules = {
        "athome": (
            "instr(lower(COALESCE(s.item_url,'')),'athome.co.jp')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'athome')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'at home')>0"
        ),
        "suumo": (
            "instr(lower(COALESCE(s.item_url,'')),'suumo.jp')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'suumo')>0"
        ),
        "homes": (
            "instr(lower(COALESCE(s.item_url,'')),'homes.co.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'rehouse.co.jp')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'homes')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'lifull')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'rehouse')>0"
        ),
        "yahoo": (
            "instr(lower(COALESCE(s.item_url,'')),'realestate.yahoo.co.jp')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'yahoo')>0"
        ),
        "rakuten": (
            "instr(lower(COALESCE(s.item_url,'')),'realestate.rakuten.co.jp')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'rakuten')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'楽天')>0"
        ),
        "yes1": (
            "instr(lower(COALESCE(s.item_url,'')),'yes1.co.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'yes-station.jp')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'yesstation')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'イエステーション')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'yes1')>0"
        ),
        "oheya_su": (
            "instr(lower(COALESCE(s.item_url,'')),'oheya-su.jp')>0 "
            "OR instr(lower(COALESCE(s.item_url,'')),'oheyasuu.com')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'oheya-su')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'oheyasu')>0 "
            "OR instr(lower(COALESCE(s.source_name,'')),'お部屋')>0"
        ),
    }
    all_keys = tuple(rules.keys())
    raw = (portal or "").strip().lower()
    if not raw or raw in ("all", "all7", "all_7", "*"):
        keys = list(all_keys)
    else:
        keys = []
        for tok in re.split(r"[,\s|]+", raw):
            if not tok:
                continue
            key = alias_map.get(tok, tok)
            if key in rules and key not in keys:
                keys.append(key)
        if not keys:
            keys = list(all_keys)
    clause = " OR ".join(f"({rules[k]})" for k in keys)
    return (f"({clause})", [], keys)


def _transaction_clause(transaction: str) -> tuple[str, list[Any]]:
    """buy / sell / rent；空值代表不限（1=1），未知值回退 buy。"""
    t = (transaction or "").strip().lower()
    if t == "":
        return transaction_sql_clause("")
    if t == "buy":
        return (
            "("
            "s.content_kind = 'jp_listing' "
            "AND instr(lower(s.item_url),'chintai')=0 "
            "AND instr(lower(s.item_url),'/chintai')=0"
            ")",
            [],
        )
    if t == "rent":
        # 租賃：以 URL /chintai/（或含 chintai）為主。避免 COALESCE/TRIM 造成索引失效。
        return (
            "("
            "s.content_kind = 'jp_listing' "
            "AND (instr(lower(s.item_url),'chintai')>0 OR instr(lower(s.item_url),'/chintai')>0)"
            ")",
            [],
        )
    if t in ("buy", "sell", "rent"):
        return transaction_sql_clause(t)
    return transaction_sql_clause("buy")


# 區域篩選：除中文／標題欄位外，補上 URL 路徑常見羅馬字（例：suumo …/tokyo/… 無「東京」漢字）
_REGION_HINT_URL_PATH_MARKERS: dict[str, tuple[str, ...]] = {
    # chuko/tokyo：SUUMO 中古マンション等詳情路徑常含此段（略過的 /tokyo/ 易誤中整區列表，由 _KEYWORD_OMIT 擋）
    "東京": ("/tokyo/", "/tokyo_sc", "chuko/tokyo", "東京都"),
    "大阪": ("/osaka/", "/osaka_sc", "chuko/osaka", "大阪府", "大阪市"),
    "名古屋": ("/nagoya/", "/nagoya_sc", "名古屋市"),
    "福岡": ("/fukuoka/", "/fukuoka_sc", "chuko/fukuoka", "福岡市", "福岡県"),
    "北海道": ("/hokkaido/", "/hokkaido_sc", "chuko/hokkaido", "北海道"),
    "京都市": ("/kyoto/", "/kyoto_sc", "京都府", "京都市"),
    "神奈川": ("/kanagawa/", "/kanagawa_sc", "/yokohama/", "/kawasaki/", "神奈川"),
    "埼玉": ("/saitama/", "/saitama_sc", "chuko/saitama", "埼玉", "さいたま"),
    "千葉": ("/chiba/", "/chiba_sc", "chuko/chiba", "千葉", "千葉県", "千葉市"),
    "横滨": ("/yokohama/", "横浜", "横浜市", "横滨"),
    "川崎": ("/kawasaki/", "川崎", "川崎市"),
    # 日語「沖縄」「沖縄県」與繁中「沖繩」字形不同，分鍵皆掃 URL／摘要
    "沖縄": ("/okinawa/", "/okinawa_sc", "沖縄県", "沖縄市", "沖繩", "冲绳"),
    "沖繩": ("/okinawa/", "/okinawa_sc", "沖縄県", "沖縄", "沖繩", "冲绳"),
    "關東": (
        "/kanto/",
        "関東",
        "關東",
        "/tokyo/",
        "/tokyo_sc",
        "/kanagawa/",
        "/saitama/",
        "/chiba/",
        "東京",
        "東京都",
        "神奈川",
        "横浜",
        "横滨",
        "川崎",
        "埼玉",
        "千葉",
        "/yokohama/",
        "/kawasaki/",
        "/tokyo_23ku/",
        "tokyo_23ku",
        "chuko/tokyo",
        "shinchiku/tokyo",
        "23区",
        "２３区",
        "search/03/",
        "/03/11",
        "/03/12",
        "/03/13",
        "/03/14",
    ),
    "關西": ("/kansai/", "関西", "關西"),
    # SUUMO 幾乎不用 /kyushu/ 在物件路徑；多為 /fukuoka/、/kumamoto/ 等都道府縣 slug
    "九州": (
        "/kyushu/",
        "九州",
        "九洲",
        "/fukuoka/",
        "/fukuoka_sc",
        "/kumamoto/",
        "/nagasaki/",
        "/kagoshima/",
        "/oita/",
        "/miyazaki/",
        "/saga/",
    ),
    "首都圏": (
        "/tokyo/",
        "/tokyo_sc",
        "/kanagawa/",
        "/saitama/",
        "/chiba/",
        "首都圏",
        "首都圈",
        "東京",
        "東京都",
        "神奈川",
        "横浜",
        "横滨",
        "川崎",
        "埼玉",
        "千葉",
        "さいたま",
        "/yokohama/",
        "/kawasaki/",
        "/tokyo_23ku/",
        "tokyo_23ku",
        "chuko/tokyo",
        "shinchiku/tokyo",
        "23区",
        "２３区",
        "search/03/",
        "/03/11",
        "/03/12",
        "/03/13",
        "/03/14",
    ),
    # 目黑／西小山等：物件內文常寫全角番地，URL 有 /meguro/、ek_（駅）等；與「東京都…目黒本町」並用
    "目黒": (
        "/meguro/",
        # SUUMO 中古區域 slug（例：…/tokyo/sc_meguro/nc_…）
        "sc_meguro",
        "目黒区",
        "目黒本町",
        # 站內繁中標題有時寫「目黑」
        "目黑",
        "目黑区",
        "目黑本町",
        "西小山",
        "ek_28780",
        "碑文谷",
    ),
}


# 華文音譯／異體 ↔ 日站名、路線、SUUMO 駅 id（站內正文多日文；「波多野臺」≈ はたのだい＝旗の台）
# 觸發字串為使用者可能輸入之子串；擴展供 LIKE 與官網摘要對齊。
_ZH_JP_STATION_KEYWORD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "波多野",
        (
            "旗の台",
            "ek_30650",
            "東急池上線",
            "東急大井町線",
            "池上線",
            "大井町線",
        ),
    ),
    # 東急池上線／大井町線 旗の台駅 — 直接輸入日文站名時須帶 ek_30650／路線（先前僅「波多野」音譯會擴展）
    (
        "旗の台",
        (
            "ek_30650",
            "東急池上線",
            "東急大井町線",
            "池上線",
            "大井町線",
        ),
    ),
    # 東急目黒線 西小山駅 — 華文「西小山站」與日文摘要／ek 列表 id 對齊（SUUMO …/ek_28780/）
    (
        "西小山站",
        (
            "西小山",
            "ek_28780",
            "東急目黒線",
            "目黒線",
            "sc_meguro",
        ),
    ),
    (
        "西小山",
        (
            "ek_28780",
            "東急目黒線",
            "目黒線",
            "sc_meguro",
        ),
    ),
)


# 關鍵字擴展時略過：會大量誤中 SUUMO 區域列表 /ms/chuko/tokyo/…（非物件 id 頁）
# 只略過最易誤中「整區列表」且仍會被 /tokyo/ LIKE 掃到的路徑；縣市級（/fukuoka/ 等）交給 SQL 的 SUUMO /ms/ 守門
_KEYWORD_OMIT_BROAD_PATH_MARKERS: frozenset[str] = frozenset({"/tokyo/", "/kanto/"})

# 區域總稱在前台常當「集合查詢詞」：需展開到都縣名與 URL slug，避免「首都圏」0 筆
# （矩陣已按都縣口徑統計，查詢端需一致）
_REGION_SUPERSET_KEYWORD_ALIASES: dict[str, tuple[str, ...]] = {
    "首都圏": (
        "首都圏",
        "首都圈",
        "東京",
        "東京都",
        "神奈川",
        "横浜",
        "橫濱",
        "埼玉",
        "千葉",
        "/tokyo/",
        "/tokyo_sc",
        "/kanagawa/",
        "/saitama/",
        "/chiba/",
        "23区",
        "２３区",
        "search/03/",
    ),
    "關東": (
        "關東",
        "関東",
        "首都圏",
        "東京",
        "神奈川",
        "埼玉",
        "千葉",
        "/kanto/",
        "/tokyo/",
        "/kanagawa/",
        "/saitama/",
        "/chiba/",
    ),
}

_FW_DIG = "０１２３４５６７８９"
_HW_DIG = "0123456789"
_FW2HW = str.maketrans(_FW_DIG, _HW_DIG)
_HW2FW = str.maketrans(_HW_DIG, _FW_DIG)


def _jp_address_keyword_variants(s: str) -> list[str]:
    """日文物件所在地常混用全形／半形數字；補兩種供 LIKE 命中（例：目黒本町４ vs 目黒本町4）。"""
    t = (s or "").strip()
    if not t:
        return []
    out: list[str] = [t]
    try:
        hw = t.translate(_FW2HW)
        if hw != t:
            out.append(hw)
        fw = t.translate(_HW2FW)
        if fw != t and fw not in out:
            out.append(fw)
    except Exception:
        pass
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        k = x.casefold()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return uniq


def _expand_portal_keyword_search_tokens(kw: str, *, narrow_markers: bool = False) -> list[str]:
    """關鍵字搜尋：補上 URL 常見羅馬字/路徑片（如 suumo …/tokyo/… 不會出現漢字「東京」）。

    narrow_markers=True：細地址（都府県＋区＋町丁）僅保留字面與番地變體／NFKC，不追加 /tokyo/ 等寬鬆路徑，
    避免 _keyword_sql_strict 以 OR 命中整區網址。
    """
    raw = (kw or "").strip()
    if not raw:
        return []
    # 括號內常為補充地名；SQL 嚴格 AND 時「旗の台駅(東京都)」整串極少完整出現在同一欄位 → 以去括號主詞做首要命中
    paren_stripped = re.sub(r"[（(][^）)]*[）)]", "", raw).strip()
    if narrow_markers:
        search_seed = paren_stripped if len(paren_stripped) >= 2 else raw
        out = [search_seed]
        seen = {search_seed.casefold()}
        for s0 in list(out):
            for v in _jp_address_keyword_variants(s0):
                if v == s0:
                    continue
                cfv = v.casefold()
                if cfv in seen or len(v.strip()) < 1:
                    continue
                if len(v) < 2 and not any(ch.isdigit() or ch in _FW_DIG for ch in v):
                    continue
                seen.add(cfv)
                out.append(v)
        for s0 in list(out):
            try:
                nk = unicodedata.normalize("NFKC", s0)
            except Exception:
                continue
            if nk == s0 or len(nk) < 2:
                continue
            cfk = nk.casefold()
            if cfk in seen:
                continue
            seen.add(cfk)
            out.append(nk)
        return out[:32]
    # 單字「東」多指東京；站內 URL 多為 /tokyo/ 無漢字「東」。若仍用 %東% 易命中 /kasu/ 等非物件頁，改只以路徑／全名關聯
    if raw == "東":
        out: list[str] = []
        seen: set[str] = set()
        for t in ("/tokyo/", "東京都"):
            t0 = t.strip()
            if len(t0) < 2:
                continue
            cf = t0.casefold()
            if cf in seen:
                continue
            seen.add(cf)
            out.append(t0)
    else:
        search_seed = paren_stripped if len(paren_stripped) >= 2 else raw
        out = [search_seed]
        seen = {search_seed.casefold()}
    for _key, markers in _REGION_HINT_URL_PATH_MARKERS.items():
        key = str(_key or "")
        if len(key) < 2:
            continue
        if not (raw == key or (key in raw and len(key) >= 2)):
            continue
        for m in markers:
            t = (m or "").strip()
            if len(t) < 2 or t in _KEYWORD_OMIT_BROAD_PATH_MARKERS:
                continue
            cf = t.casefold()
            if cf in seen:
                continue
            seen.add(cf)
            out.append(t)
    # 集合區域詞補強：此類詞預期命中其下都縣；此處允許 /tokyo/、/kanto/ 等 broad marker。
    for rk, alias_terms in _REGION_SUPERSET_KEYWORD_ALIASES.items():
        k0 = str(rk or "").strip()
        if len(k0) < 2:
            continue
        if not (raw == k0 or k0 in raw):
            continue
        for m in alias_terms:
            t = (m or "").strip()
            if len(t) < 2:
                continue
            cf = t.casefold()
            if cf in seen:
                continue
            seen.add(cf)
            out.append(t)
    for trig, alias_markers in _ZH_JP_STATION_KEYWORD_ALIASES:
        if len(trig) < 2 or trig not in raw:
            continue
        for m in alias_markers:
            t = (m or "").strip()
            if len(t) < 2 or t in _KEYWORD_OMIT_BROAD_PATH_MARKERS:
                continue
            cf = t.casefold()
            if cf in seen:
                continue
            seen.add(cf)
            out.append(t)
    # 全形／半形番地變體（不增加 AND 子句數，只擴展同一 OR 內可命中的字串）
    for s0 in list(out):
        for v in _jp_address_keyword_variants(s0):
            if v == s0:
                continue
            cfv = v.casefold()
            if cfv in seen or len(v.strip()) < 1:
                continue
            if len(v) < 2 and not any(ch.isdigit() or ch in _FW_DIG for ch in v):
                continue
            seen.add(cfv)
            out.append(v)
    # 半形／相容片假名：NFKC 與原文並列 OR（例：ｱーバンハイム ↔ アーバンハイム；站內摘要有時混用）
    for s0 in list(out):
        try:
            nk = unicodedata.normalize("NFKC", s0)
        except Exception:
            continue
        if nk == s0 or len(nk) < 2:
            continue
        cfk = nk.casefold()
        if cfk in seen:
            continue
        seen.add(cfk)
        out.append(nk)
    return out[:32]


def _recency_sql(max_age_days: int) -> tuple[str, list[Any]]:
    """依內容或來源列之最近時間（updated_at / last_checked / published / crawled）；0 表示不限制。"""
    n = int(max_age_days or 0)
    if n <= 0:
        return ("1=1", [])
    # 三欄皆空時，舊版 date('') 無法通過「近 N 天」篩選，導致智慧查詢全 0；以當下時間作最後回退
    coalesced = (
        "COALESCE("
        "NULLIF(TRIM(s.published_at), ''), "
        "NULLIF(TRIM(s.last_checked_at), ''), "
        "NULLIF(TRIM(s.crawled_at), ''), "
        "NULLIF(TRIM(c.updated_at), ''), "
        "datetime('now')"
        ")"
    )
    # 已驗證為整數，避免 SQL 注入
    return (f"date({coalesced}) >= date('now', '-{n} days')", [])


# 與 _portal_host_clause rules 鍵一致，供 round-robin 輪詢順序
_PORTAL_BUCKET_ORDER = ("suumo", "homes", "athome", "yahoo", "rakuten", "yes1", "oheya_su", "other")


def _parse_item_ts(it: dict[str, Any]) -> float:
    for k in ("sort_time_at", "data_time_at", "case_time_at", "updated_at", "last_checked_at", "published_at", "crawled_at"):
        raw = str(it.get(k) or "").strip()
        if not raw:
            continue
        raw = unicodedata.normalize("NFKC", raw)
        if raw.endswith("Z") and "T" in raw:
            raw = raw[:-1] + "+00:00"
        m_jp_std = re.search(
            "([0-9]{4})\\s*\\u5e74\\s*([0-9]{1,2})\\s*\\u6708\\s*([0-9]{1,2})\\s*\\u65e5(?:\\s+([0-9]{1,2}):([0-9]{2}))?",
            raw,
        )
        if m_jp_std:
            try:
                hh = int(m_jp_std.group(4) or 0)
                mm = int(m_jp_std.group(5) or 0)
                return datetime(
                    int(m_jp_std.group(1)),
                    int(m_jp_std.group(2)),
                    int(m_jp_std.group(3)),
                    hh,
                    mm,
                ).timestamp()
            except Exception:
                pass
        m_jp = re.search(r"([0-9]{4})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日", raw)
        if m_jp:
            try:
                return datetime(int(m_jp.group(1)), int(m_jp.group(2)), int(m_jp.group(3))).timestamp()
            except Exception:
                pass
        m_slash = re.search(r"([0-9]{4})[/-]([0-9]{1,2})[/-]([0-9]{1,2})(?:\s+([0-9]{1,2}):([0-9]{2}))?", raw)
        if m_slash:
            try:
                hh = int(m_slash.group(4) or 0)
                mm = int(m_slash.group(5) or 0)
                return datetime(int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3)), hh, mm).timestamp()
            except Exception:
                pass
        try:
            if " " in raw and "T" not in raw:
                return datetime.fromisoformat(raw.replace(" ", "T", 1)).timestamp()
            return datetime.fromisoformat(raw).timestamp()
        except Exception:
            continue
    return 0.0


def _item_has_display_image(it: dict[str, Any]) -> bool:
    if str((it or {}).get("thumb_url") or "").strip():
        return True
    gallery = (it or {}).get("gallery_urls")
    return isinstance(gallery, list) and any(str(u or "").strip() for u in gallery)


def _core_listing_signal_count(it: dict[str, Any]) -> int:
    keys = ("price_text_hant", "area_text_hant", "layout_text_hant")
    out = 0
    for k in keys:
        v = str((it or {}).get(k) or "").strip()
        if not v:
            continue
        if v.startswith(("-", "--", "—")):
            continue
        out += 1
    return out


def _item_display_rank(it: dict[str, Any]) -> tuple[int, int, float, int]:
    ts = _parse_item_ts(it)
    try:
        day_bucket = datetime.fromtimestamp(ts).date().toordinal() if ts > 0 else 0
    except Exception:
        day_bucket = 0
    return (
        day_bucket,
        _core_listing_signal_count(it),
        ts,
        1 if _item_has_display_image(it) else 0,
    )


def _prefer_complete_items_for_display(items: list[dict[str, Any]], *, lim: int) -> list[dict[str, Any]]:
    """上線查詢優先顯示有物件圖的完整案件；不足時保留缺圖案件作為備援。"""
    if not items:
        return []
    target = max(1, int(lim or 1))
    complete = [it for it in items if _item_has_display_image(it)]
    if len(complete) >= target:
        return complete
    return items


def _portal_bucket_from_item(it: dict[str, Any]) -> str:
    url = str(it.get("item_url") or "").lower()
    src = str(it.get("source_name") or "").lower()
    if "suumo.jp" in url or "suumo" in src:
        return "suumo"
    if (
        "homes.co.jp" in url
        or "rehouse.co.jp" in url
        or "homes" in src
        or "lifull" in src
        or "rehouse" in src
    ):
        return "homes"
    if "athome.co.jp" in url or "athome" in src or "at home" in src:
        return "athome"
    if "realestate.yahoo.co.jp" in url or "yahoo" in src:
        return "yahoo"
    if "realestate.rakuten.co.jp" in url or "rakuten" in src or "楽天" in src:
        return "rakuten"
    if "yes1.co.jp" in url or "yes-station.jp" in url or "yesstation" in src or "yes1" in src or "イエステーション" in src:
        return "yes1"
    if "oheya-su.jp" in url or "oheyasuu.com" in url or "oheya-su" in src or "oheyasu" in src or "お部屋" in src:
        return "oheya_su"
    return "other"


def _listing_value_present(value: Any) -> bool:
    v = str(value or "").strip()
    if not v:
        return False
    if v in {"—", "-", "–", "−", "―", "－", "--"}:
        return False
    if v.startswith(("—", "－")):
        return False
    return True


_JP_KANA_RE = re.compile(r"[ぁ-んァ-ヴー]")


_PORTAL_CASE_TEXT_HANT_REPL: tuple[tuple[str, str], ...] = (
    ("【アットホーム】", ""),
    ("【ホームズ】", ""),
    ("【SUUMO】", ""),
    ("LIFULL HOME'S", "LIFULL HOME'S"),
    ("アットホーム", "AtHome"),
    ("ホームズ", "LIFULL HOME'S"),
    ("スーモ", "SUUMO"),
    ("ライフルホームズ", "LIFULL HOME'S"),
    ("イエステーション", "YesStation"),
    ("お部屋探す", "OHEYASU"),
    ("ディアナコート", "Diana Court"),
    ("ヴェレーナ", "Verena"),
    ("イノバス", "Innovus"),
    ("ポレスター", "Polestar"),
    ("モンドミオ", "Mond Mio"),
    ("エクセレントシティ", "Excellent City"),
    ("グランゲート", "Grand Gate"),
    ("レーベン", "Leben"),
    ("リビオ", "Livio"),
    ("パークハイツ", "Park Heights"),
    ("パークホームズ", "Park Homes"),
    ("パークタワー", "Park Tower"),
    ("パークコート", "Park Court"),
    ("シティタワー", "City Tower"),
    ("ザ・パークハウス", "The Parkhouse"),
    ("ブランズ", "Branz"),
    ("プラウド", "Proud"),
    ("ザ・マークス", "The Marks"),
    ("グランドパレス", "Grand Palace"),
    ("シーズガーデン", "Seeds Garden"),
    ("サンシャイン", "Sunshine"),
    ("ステーションタワー", "Station Tower"),
    ("タワー", "Tower"),
    ("スクエア", "Square"),
    ("フォート", "Fort"),
    ("コート", "Court"),
    ("ハイツ", "Heights"),
    ("プラザ", "Plaza"),
    ("レジデンス", "Residence"),
    ("ヒルズ", "Hills"),
    ("ガーデン", "Garden"),
    ("ウォークインクロゼット", "步入式衣帽間"),
    ("ウォークイン", "步入式"),
    ("シューズクローク", "鞋櫃收納間"),
    ("クローゼット", "收納衣櫃"),
    ("ワンルーム", "單間套房"),
    ("サービスルーム", "多功能房"),
    ("ルーム", "房間"),
    ("屋上テラス", "屋頂露台"),
    ("テラス", "露台"),
    ("土間収納", "玄關收納"),
    ("パントリー", "食品儲藏室"),
    ("リネン庫", "布草收納"),
    ("畳コーナー", "榻榻米角落"),
    ("こだわり収納", "精選收納"),
    ("小屋裏収納", "閣樓收納"),
    ("収納", "收納"),
    ("スペース", "空間"),
    ("マルチ", "多用途"),
    ("アウトドア", "戶外"),
    ("もちろん", "也可"),
    ("活用できる", "可活用"),
    ("多目的に", "多用途"),
    ("など", "等"),
    ("マンション", "公寓"),
    ("アパート", "公寓"),
    ("コーポ", "公寓"),
    ("メゾン", "公寓"),
    ("新築公寓", "新建公寓"),
    ("新築", "新建"),
    ("分譲", "分售"),
    ("賃貸", "租賃"),
    ("売買", "買賣"),
    ("購入", "購買"),
    ("物件情報", "物件資訊"),
    ("中古", "中古"),
    ("一戸建て", "一戶建"),
    ("戸建て", "戶建"),
    ("戸建", "戶建"),
    ("物件名", "物件名稱"),
    ("価格", "價格"),
    ("所在地", "所在地"),
    ("間取り", "格局"),
    ("専有面積", "專有面積"),
    ("建物階数", "建物層數"),
    ("階数", "層數"),
    ("販売価格", "銷售價格"),
    ("販売開始", "開始銷售"),
    ("価格未定", "價格未定"),
    ("万円", "萬日圓"),
    ("円", "日圓"),
    ("東京メトロ", "東京地鐵"),
    ("大阪メトロ", "大阪地鐵"),
    ("名古屋市営", "名古屋市營"),
    ("北九州モノレール", "北九州單軌電車"),
    ("モノレール", "單軌電車"),
    ("つくばエクスプレス", "筑波快線"),
    ("地下鉄", "地下鐵"),
    ("都営", "都營"),
    ("ＪＲ", "JR"),
    ("私鉄", "私鐵"),
    ("バス停下車", "巴士站下車"),
    ("バス停", "巴士站"),
    ("バス", "巴士"),
    ("徒歩", "步行"),
    ("歩", "步行"),
    ("駅", "站"),
    ("鉄骨鉄筋コンクリート", "鋼骨鋼筋混凝土"),
    ("鉄筋コンクリート", "鋼筋混凝土"),
    ("鉄骨", "鋼骨"),
    ("階建", "層樓建築"),
    ("階部分", "樓層"),
    ("階", "樓"),
    ("築年月", "建築年月"),
    ("築年", "屋齡"),
    ("予定", "預定"),
    ("総戸数", "總戶數"),
    ("総武線", "總武線"),
    ("浅草線", "淺草線"),
    ("大江戸線", "大江戶線"),
    ("東横線", "東橫線"),
    ("山手線", "山手線"),
    ("札沼線", "札沼線"),
    ("室蘭本線", "室蘭本線"),
    ("管理員室", "管理員室"),
    ("集会室", "集會室"),
    ("店舗", "店鋪"),
    ("他に", "另有"),
    ("他、", "另有"),
    ("の一部", "的一部分"),
    ("付の", "附"),
    ("の", "的"),
    ("全", "共"),
    ("邸", "戶"),
    ("戸", "戶"),
    ("枚", "張"),
    ("駐車場", "停車場"),
    ("駐車施設", "停車位"),
    ("設置", "設置"),
    ("現況", "目前狀態"),
    ("予告広告", "預告廣告"),
    ("販売予定時期", "銷售預定時期"),
    ("販売予定", "銷售預定"),
    ("販売", "銷售"),
    ("引渡し", "交屋"),
    ("引渡", "交屋"),
    ("月額使用料", "月租費"),
    ("管理費等", "管理費"),
    ("修繕積立金", "修繕準備金"),
    ("敷地内", "基地內"),
    ("内に", "內設有"),
    ("機械式", "機械式"),
    ("平置き", "平面式"),
    ("来客用", "訪客用"),
    ("車椅子使用者用", "輪椅使用者用"),
    ("号棟", "號棟"),
    ("号", "號"),
    ("県", "縣"),
    ("区", "區"),
    ("ヶ", "之"),
    ("ヶ所", "處"),
    ("ケ所", "處"),
    ("ケ", "之"),
    ("ノ", "之"),
    ("および", "以及"),
    ("下る", "往南"),
    ("地番", "地號"),
    ("収益", "收益"),
    ("浅", "淺"),
    ("蔵", "藏"),
    ("条", "條"),
    ("広", "廣"),
)


_PORTAL_PREFECTURE_REPL: tuple[tuple[str, str], ...] = (
    ("北海道", "北海道"),
    ("青森県", "青森縣"),
    ("岩手県", "岩手縣"),
    ("宮城県", "宮城縣"),
    ("秋田県", "秋田縣"),
    ("山形県", "山形縣"),
    ("福島県", "福島縣"),
    ("茨城県", "茨城縣"),
    ("栃木県", "栃木縣"),
    ("群馬県", "群馬縣"),
    ("埼玉県", "埼玉縣"),
    ("千葉県", "千葉縣"),
    ("東京都", "東京都"),
    ("神奈川県", "神奈川縣"),
    ("新潟県", "新潟縣"),
    ("富山県", "富山縣"),
    ("石川県", "石川縣"),
    ("福井県", "福井縣"),
    ("山梨県", "山梨縣"),
    ("長野県", "長野縣"),
    ("岐阜県", "岐阜縣"),
    ("静岡県", "靜岡縣"),
    ("愛知県", "愛知縣"),
    ("三重県", "三重縣"),
    ("滋賀県", "滋賀縣"),
    ("京都府", "京都府"),
    ("大阪府", "大阪府"),
    ("兵庫県", "兵庫縣"),
    ("奈良県", "奈良縣"),
    ("和歌山県", "和歌山縣"),
    ("鳥取県", "鳥取縣"),
    ("島根県", "島根縣"),
    ("岡山県", "岡山縣"),
    ("広島県", "廣島縣"),
    ("山口県", "山口縣"),
    ("徳島県", "德島縣"),
    ("香川県", "香川縣"),
    ("愛媛県", "愛媛縣"),
    ("高知県", "高知縣"),
    ("福岡県", "福岡縣"),
    ("佐賀県", "佐賀縣"),
    ("長崎県", "長崎縣"),
    ("熊本県", "熊本縣"),
    ("大分県", "大分縣"),
    ("宮崎県", "宮崎縣"),
    ("鹿児島県", "鹿兒島縣"),
    ("沖縄県", "沖繩縣"),
)


def _portal_case_text_hant(raw: Any, *, max_len: int = 260) -> str:
    out = _clean_translation_noise(str(raw or "")).strip()
    if not out:
        return ""
    out = re.sub(r"^\s*日本房[產产]案源[:：]\s*", "", out)
    out = re.sub(r"^\s*日本不動產案件[:：]\s*", "", out)
    out = re.sub(r"^\s*(?:\[在家\]|【在家】|在家)\s*", "", out)
    for jp, zh in _PORTAL_PREFECTURE_REPL:
        out = out.replace(jp, zh)
    for jp, zh in _PORTAL_CASE_TEXT_HANT_REPL:
        out = out.replace(jp, zh)
    out = out.replace("｜", " | ")
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r"\s*([|/・、，,])\s*", r"\1", out)
    out = out.replace("は也可", "也可")
    out = re.sub(r"(^|[|/、，,\s])は(?=$|[|/、，,\s])", r"\1", out)
    out = re.sub(r"\|\s+", "|", out)
    out = re.sub(r"\b(AtHome|LIFULL HOME'S)（\1）", r"\1", out)
    out = out.strip(" ：:-|")
    return out[:max_len].strip()


def _portal_case_kana_safe(raw: Any, *, max_len: int = 260) -> str:
    """Translate common listing Japanese and suppress remaining kana fragments for display fields."""
    out = _portal_case_text_hant(raw, max_len=max_len)
    return "" if _portal_case_has_kana(out) else out


def _portal_case_join_kana_safe(parts: Iterable[Any], *, max_len: int = 260) -> str:
    rows: list[str] = []
    for part in parts:
        text = _portal_case_kana_safe(part, max_len=max_len)
        if text:
            rows.append(text)
    return " ".join(rows)[:max_len].strip()


def _portal_case_clean_access_segments(raw: Any, *, max_len: int = 180) -> str:
    translated = _portal_case_text_hant(raw, max_len=600)
    if not translated:
        return ""
    if not _portal_case_has_kana(translated):
        return translated[:max_len].strip()
    pieces = re.split(r"\s*(?:/|／|(?:\(\d+\))|(?:（\d+）)|[。；;])\s*", translated)
    kept: list[str] = []
    for piece in pieces:
        p = piece.strip(" 、，,")
        if not p or _portal_case_has_kana(p):
            continue
        if re.search(r"(?:站|步行|線|鐵|巴士|JR)", p):
            kept.append(p)
        if len(kept) >= 2:
            break
    return " / ".join(kept)[:max_len].strip()


def _portal_case_source_name_display(raw: Any, item_url: Any = "") -> str:
    text = str(raw or "").strip()
    hay = f"{text} {item_url or ''}".lower()
    if "athome.co.jp" in hay or "athome" in hay or "at home" in hay or "アットホーム" in text:
        return "AtHome"
    if "homes.co.jp" in hay or "lifull" in hay or "home's" in hay or "ホームズ" in text:
        return "LIFULL HOME'S"
    if "suumo.jp" in hay or "suumo" in hay or "スーモ" in text:
        return "SUUMO"
    if "realestate.yahoo.co.jp" in hay or "yahoo" in hay or "不動産" in text:
        return "Yahoo! 不動產"
    if "realestate.rakuten.co.jp" in hay or "rakuten" in hay or "楽天" in text:
        return "樂天不動產"
    if "yes1.co.jp" in hay or "yes-station" in hay or "イエステーション" in text:
        return "YesStation"
    if "oheya-su.jp" in hay or "oheyasu" in hay or "お部屋探す" in text:
        return "OHEYASU"
    return _portal_case_text_hant(text, max_len=80)


def _portal_case_has_kana(raw: Any) -> bool:
    return bool(_JP_KANA_RE.search(str(raw or "")))


def _portal_case_display_line_is_noisy(raw: Any) -> bool:
    t = str(raw or "").strip()
    if not t:
        return False
    if len(t) > 150:
        return True
    noisy_terms = (
        "日本房產案源",
        "日本房产案源",
        "產案源",
        "产案源",
        "資料請求",
        "资料请求",
        "見学予約",
        "問合せ",
        "会社情報",
        "掲載画像",
        "閲覧済",
        "Pick up",
        "災害リスク",
        "構造/樓数",
        "構造/階数",
    )
    return any(term in t for term in noisy_terms)


def _portal_case_display_access(raw: Any, d: dict[str, Any], meta: dict[str, Any]) -> str:
    source_blob = "\n".join([str(d.get("title_original") or ""), str(d.get("body_original") or "")])
    access_raw = str(raw or "").strip()
    segments = _homes_station_segments(access_raw, max_items=2) if access_raw else []
    if segments:
        access_raw = " / ".join(segments)
    if _portal_case_display_line_is_noisy(access_raw):
        access_raw = _extract_jp_access_fallback(source_blob)
        segments = _homes_station_segments(access_raw, max_items=2) if access_raw else []
        if segments:
            access_raw = " / ".join(segments)
    access_hant = _portal_case_text_hant(access_raw or meta.get("transit_line_zh") or "", max_len=180)
    if _portal_case_display_line_is_noisy(access_hant):
        access_hant = _portal_case_text_hant(meta.get("transit_line_zh") or "", max_len=120)
    if _portal_case_has_kana(access_hant):
        access_hant = _portal_case_clean_access_segments(access_raw, max_len=180)
    if _portal_case_has_kana(access_hant):
        access_hant = _portal_case_clean_access_segments(meta.get("transit_line_zh") or "", max_len=120)
    return "" if _portal_case_display_line_is_noisy(access_hant) else access_hant


def _portal_case_display_address(raw: Any, d: dict[str, Any], meta: dict[str, Any]) -> str:
    source_blob = "\n".join([str(d.get("title_original") or ""), str(d.get("body_original") or "")])
    address_raw = str(raw or "").strip()
    if _portal_case_display_line_is_noisy(address_raw) or re.search(r"(?:駅|站|徒歩|步行)\s*\d", address_raw):
        address_raw = _extract_jp_address_fallback(source_blob)
    address_hant = _portal_case_text_hant(address_raw, max_len=160)
    if _portal_case_display_line_is_noisy(address_hant) or re.search(r"(?:站|步行)\s*\d", address_hant):
        address_hant = str(meta.get("jp_region_display_zh") or "").strip()
    if _portal_case_has_kana(address_hant):
        address_hant = str(meta.get("jp_region_display_zh") or "").strip()
    return address_hant


def _portal_case_property_type_hant(d: dict[str, Any], listing_fields: dict[str, Any]) -> str:
    blob = "\n".join(
        [
            str(d.get("title_original") or ""),
            str(d.get("title_zh_hant") or ""),
            str(d.get("item_url") or ""),
            str(listing_fields.get("building_type_zh") or ""),
        ]
    )
    if re.search(r"新築|新建", blob):
        if re.search(r"マンション|公寓|mansion", blob, re.I):
            return "新建公寓"
    if re.search(r"中古", blob) and re.search(r"マンション|公寓|mansion", blob, re.I):
        return "中古公寓"
    if re.search(r"一戸建て|戸建|kodate|ikkodate|透天|一戶建", blob, re.I):
        return "戶建"
    if re.search(r"マンション|公寓|mansion|ms/", blob, re.I):
        return "公寓"
    return str(listing_fields.get("building_type_zh") or "房源").strip() or "房源"


def _portal_case_short_address(address_hant: str, region: str) -> str:
    addr = _portal_case_text_hant(address_hant, max_len=120)
    if not addr:
        return str(region or "").strip()
    addr = re.sub(r"[0-9０-９].*$", "", addr).strip(" -－、，,")
    return addr[:48].strip() or address_hant[:48].strip()


def _portal_case_display_title(
    d: dict[str, Any],
    listing_fields: dict[str, Any],
    *,
    title_hant_clean: str,
    title_original_clean: str,
    meta: dict[str, Any],
) -> str:
    base = _portal_case_text_hant(title_hant_clean or title_original_clean, max_len=120)
    base = re.sub(r"^(?:AtHome|LIFULL HOME'S)\s*", "", base).strip(" ：:-|")
    base = re.sub(r"^\s*(?:\[在家\]|【在家】|在家)\s*", "", base).strip(" ：:-|")
    base = re.sub(r"\s*交通\s*$", "", base).strip()
    address_hant = _portal_case_display_address(listing_fields.get("address_line_jp") or "", d, meta)
    needs_synth = (
        not base
        or _portal_case_has_kana(base)
        or len(base) > 72
        or base.lower().startswith(("http://", "https://"))
    )
    if not needs_synth:
        return base
    region = str(meta.get("jp_region_display_zh") or "")
    short_addr = _portal_case_short_address(address_hant, region)
    layout = _portal_case_text_hant(listing_fields.get("layout_text_hant") or "", max_len=80)
    ptype = _portal_case_property_type_hant(d, listing_fields)
    right = " ".join(x for x in (layout, ptype) if x).strip()
    if short_addr and right:
        return f"{short_addr}｜{right}"[:96]
    if short_addr:
        return f"{short_addr}｜{ptype}"[:96]
    return (right or ptype or "日本不動產案件")[:96]


def _portal_case_display_fields(
    d: dict[str, Any],
    listing_fields: dict[str, Any],
    *,
    meta: dict[str, Any],
    title_hant_clean: str,
    title_hans_clean: str,
    title_original_clean: str,
    body_hant_preview: str,
    body_hans_preview: str,
) -> dict[str, str]:
    title_display_hant = _portal_case_display_title(
        d,
        listing_fields,
        title_hant_clean=title_hant_clean,
        title_original_clean=title_original_clean,
        meta=meta,
    )
    address_hant = _portal_case_display_address(listing_fields.get("address_line_jp") or "", d, meta)
    access_hant = _portal_case_display_access(listing_fields.get("access_line_jp") or "", d, meta)
    preview_source = body_hant_preview or _clean_portal_case_preview(d, listing_fields, script="hant")
    preview_hant = _portal_case_text_hant(preview_source, max_len=320)
    preview_hans = _portal_case_text_hant(body_hans_preview or preview_source, max_len=320)
    if _portal_case_has_kana(preview_hant):
        preview_hant = _clean_portal_case_preview(d, listing_fields, script="hant")
    if _portal_case_has_kana(preview_hans):
        preview_hans = _clean_portal_case_preview(d, listing_fields, script="hans")
    if _portal_case_has_kana(preview_hant):
        preview_hant = _portal_case_join_kana_safe(
            (
                title_display_hant,
                listing_fields.get("price_text_hant"),
                listing_fields.get("layout_text_hant"),
                listing_fields.get("area_text_hant"),
                address_hant,
                access_hant,
            ),
            max_len=320,
        )
    if _portal_case_has_kana(preview_hans):
        preview_hans = preview_hant
    title_display_hans = _portal_case_text_hant(title_hans_clean, max_len=120) if title_hans_clean else title_display_hant
    if not title_display_hans or _portal_case_has_kana(title_display_hans):
        title_display_hans = title_display_hant
    return {
        "title_display_hant": title_display_hant,
        "title_display_hans": title_display_hans,
        "address_line_hant": address_hant,
        "access_line_hant": access_hant,
        "body_display_hant_preview": preview_hant,
        "body_display_hans_preview": preview_hans,
    }


def _preview_text_looks_stale(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return True
    if re.search(r"(?:認証中|認證中|认证中|Click to verify|captcha|human verification|awswaf)", t, re.I):
        return True
    if t.count("：—") >= 4 or t.count(": —") >= 4:
        return True
    return False


def _clean_portal_case_preview(
    d: dict[str, Any],
    listing_fields: dict[str, Any],
    *,
    script: str = "hant",
) -> str:
    """Build a short, user-facing listing preview from current parsed fields.

    Content rows can lag behind source_items after live/detail enrichment. Search
    cards should prefer the freshly parsed source fields over stale WAF/captcha
    text from older generated article bodies.
    """
    labels_hant = {
        "price_text_hant": "價格",
        "layout_text_hant": "格局",
        "area_text_hant": "面積",
        "address_line_jp": "位置",
        "access_line_jp": "交通",
        "built_ym_jp": "築年",
        "floor_text_hant": "樓層",
    }
    labels_hans = {
        "price_text_hant": "价格",
        "layout_text_hant": "格局",
        "area_text_hant": "面积",
        "address_line_jp": "位置",
        "access_line_jp": "交通",
        "built_ym_jp": "建年",
        "floor_text_hant": "楼层",
    }
    label = labels_hans if script == "hans" else labels_hant
    rows: list[str] = []
    title = _portal_case_kana_safe(d.get("title_zh_hant") or d.get("title_original") or "", max_len=120)
    if title:
        rows.append(title)
    for key in (
        "price_text_hant",
        "layout_text_hant",
        "area_text_hant",
        "address_line_jp",
        "access_line_jp",
        "built_ym_jp",
        "floor_text_hant",
    ):
        value = _portal_case_kana_safe(listing_fields.get(key) or "", max_len=220)
        if not _listing_value_present(value):
            continue
        rows.append(f"{label.get(key, key)}：{value}")
    if len(rows) <= 1:
        snippet = _portal_case_kana_safe(_clean_snippet_text(str(d.get("body_original") or "")), max_len=260)
        if snippet and not _preview_text_looks_stale(snippet):
            rows.append(snippet)
    return " ".join(rows)[:320]


def _has_listing_field_signal(it: dict[str, Any]) -> bool:
    fields = (
        "price_text_hant",
        "address_line_jp",
        "access_line_jp",
        "area_text_hant",
        "layout_text_hant",
        "built_ym_jp",
        "floor_text_hant",
        "manage_fee_jp",
        "reserve_fee_jp",
        "total_units_jp",
    )
    for k in fields:
        if not _listing_value_present(it.get(k)):
            continue
        return True
    return False


def _snippet_looks_like_property_excerpt(it: dict[str, Any]) -> bool:
    """正則欄位尚未解析出來時：標題+摘要有長度與日文物件常見片語，當作有效信號。"""
    t = f"{it.get('title_original') or ''} {it.get('snippet_jp') or ''}".strip()
    if len(t) < 16:
        return False
    if re.search(r"[\d０-９]{2,}", t) is None:
        return False
    if any(
        x in t
        for x in (
            "円",
            "万",
            "万円",
            "㎡",
            "m2",
            "LDK",
            "沿線",
            "徒歩",
            "都",
            "県",
            "市",
            "区",
        )
    ):
        return True
    return "物件" in t or "専有" in t or "価格" in t


def _strong_listing_signal_count(it: dict[str, Any]) -> int:
    fields = (
        "price_text_hant",
        "address_line_jp",
        "access_line_jp",
        "area_text_hant",
        "layout_text_hant",
        "built_ym_jp",
        "floor_text_hant",
    )
    n = 0
    for k in fields:
        if not _listing_value_present(it.get(k)):
            continue
        n += 1
    return n


# 官網首頁「エリアから探す」大區一鍵導向（/kyushu/ 等），非物件；關鍵字「九州」+ /kyushu/ 曾誤中
_SUUMO_SINGLE_SEGMENT_REGION_LANDING: frozenset[str] = frozenset(
    {
        "hokkaido",
        "tohoku",
        "kanto",
        "koshinetsu",
        "kinki",
        "tokai",
        "kansai",
        "chugoku",
        "shikoku",
        "kyushu",
        "okinawa",
    }
)


def _suumo_is_regional_landing_url(url: str) -> bool:
    u = (url or "").strip().rstrip("/").lower()
    if "suumo.jp" not in u or "/ms/" in u or "/chintai/" in u:
        return False
    m = re.match(r"^https?://(www\.)?suumo\.jp/([a-z0-9_]+)/?$", u)
    if not m:
        return False
    return m.group(2) in _SUUMO_SINGLE_SEGMENT_REGION_LANDING


def _is_probably_listing_detail_result(it: dict[str, Any]) -> bool:
    """查詢層最後一道保護：排除非物件詳情頁（公告/入口/品牌頁）。"""
    url = str(it.get("item_url") or "").strip().lower()
    if not url:
        return False
    if _suumo_is_regional_landing_url(url):
        return False
    bucket = _portal_bucket_from_item(it)
    if any(
        seg in url
        for seg in (
            "/feature/",
            "/features/",
            "/about",
            "/about/",
            "/inquire/",
            "/lp/",
            "/guide/",
            "/column/",
            "/news/",
        )
    ):
        return False
    if "/contents/" in url and not (bucket == "yes1" and "/contents/detail/" in url):
        return False
    has_signal = _has_listing_field_signal(it)
    strong_n = _strong_listing_signal_count(it)
    has_price = _listing_value_present(it.get("price_text_hant"))
    excerpt_ok = _snippet_looks_like_property_excerpt(it)
    has_any_signal = has_signal or excerpt_ok
    title = str(it.get("title_original") or "").strip()
    body_hint = " ".join(
        str(it.get(k) or "")
        for k in ("snippet_jp", "body_zh_hant_preview", "body_zh_hans_preview")
    )
    title_lite = re.sub(r"\s+", "", title)
    yes1_generic_empty = (
        bucket == "yes1"
        and (
            not title_lite
            or title_lite.startswith("｜中古住宅・マンション・土地の売却・購入なら")
            or title_lite.startswith("全国の不動産売買ならイエステーション")
        )
        and strong_n <= 0
        and "物件番号" not in body_hint
    )
    if yes1_generic_empty:
        return False
    blocked_snapshot = bool(
        re.search(
            r"(?:認証中|認證中|认证中|Click to verify|captcha|human verification|awswaf|通常のサイト閲覧を超える速度)",
            f"{title}\n{body_hint}",
            flags=re.I,
        )
    )
    if blocked_snapshot and not has_signal and not has_price and strong_n <= 0:
        return False
    # 明確的門戶「物件詳情」URL＋有標題：不強依賴正則已切出專有面積等（關鍵字搜尋常僅有 URL 含 /tokyo/）
    if not has_any_signal and len(title) >= 4:
        if bucket == "yahoo" and (
            re.search(r"/used/mansion/detail(?:_corp)?/[a-z0-9]{5,}", url)
            or re.search(r"/land/detail(?:_corp)?/[a-z0-9]{5,}", url)
        ):
            has_any_signal = True
        elif bucket == "homes" and re.search(r"/(?:mansion|kodate)/b-\d{6,}", url):
            has_any_signal = True
        elif bucket == "athome" and _ATHOME_LISTING_DETAIL_URL_RE.search(url):
            has_any_signal = True
        elif bucket == "suumo" and _is_suumo_property_detail_url(url):
            has_any_signal = True
        elif bucket == "rakuten" and (
            re.search(r"/(?:usedmansion|newdetached|useddetached|land)/id-[0-9a-z_-]+/?(?:$|\?)", url)
            or re.search(r"/\d{5,}(?:/|$|\?)", url)
        ):
            has_any_signal = True
        elif bucket == "yes1" and any(seg in url for seg in ("/detail/", "/bukken/", "/estate/")):
            has_any_signal = True
        elif bucket == "oheya_su" and re.search(r"/[0-9]{5,}", url):
            has_any_signal = True
    # 有價格、至少一項結構欄位、或有任一可展示信號（含門戶詳情 URL 規則）
    enough_detail = has_price or strong_n >= 1 or has_any_signal

    if bucket == "suumo":
        # SUUMO 覆蓋矩陣以 jp_listing 計數；前台若再強制信號門檻，會出現「矩陣有、查詢 0」落差。
        # 只要是明確 SUUMO 詳情 URL，且至少有標題或任一信號，即允許展示。
        if not _is_suumo_property_detail_url(url):
            return False
        return bool(title) or has_any_signal or enough_detail
    if bucket == "homes":
        if re.search(r"/(?:mansion|kodate)/b-\d{6,}(?:/|\?|$)", url):
            return has_any_signal and enough_detail
        if "/chintai/room/" in url:
            return has_any_signal and enough_detail
        return False
    if bucket == "athome":
        if _ATHOME_LISTING_DETAIL_URL_RE.search(url):
            return has_any_signal and enough_detail
        return False
    if bucket == "yahoo":
        if re.search(r"/used/mansion/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$)", url):
            return has_any_signal and enough_detail
        if re.search(r"/land/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$)", url):
            return has_any_signal and enough_detail
        return False
    if bucket == "rakuten":
        rakuten_current_detail = re.search(
            r"/(?:usedmansion|newdetached|useddetached|land)/id-[0-9a-z_-]+/?(?:$|\?)",
            url,
        )
        if not rakuten_current_detail and not any(seg in url for seg in ("/mansion/", "/kodate/", "/house/", "/tochi/", "/land/")):
            return False
        if not rakuten_current_detail and re.search(r"/\d{5,}(?:/|$|\?)", url) is None:
            return False
        return has_any_signal and enough_detail
    if bucket == "yes1":
        if "yes1.co.jp" in url and not any(seg in url for seg in ("/detail/", "/bukken/", "/estate/")):
            return False
        if "yes-station.jp" in url and not any(seg in url for seg in ("/buy/", "/sell/", "/bukken/", "/estate/", "/detail/")):
            return False
        return has_any_signal and enough_detail
    if bucket == "oheya_su":
        if not any(seg in url for seg in ("/bukken/", "/detail/", "/chintai/", "/mansion/", "/kodate/")):
            return False
        return has_any_signal and enough_detail
    return has_any_signal and enough_detail


def _merge_multi_portal_items(items: list[dict[str, Any]], *, lim: int) -> list[dict[str, Any]]:
    """多門戶：各站內依時間新→舊，再依來源 round-robin 合併，避免單一站占滿前 N 筆。"""
    if not items:
        return []
    # Release search must show newest source/update time first. The older
    # portal round-robin path could surface stale listings above fresh cases.
    return _dedupe_portal_case_items(items, lim=lim)
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in _PORTAL_BUCKET_ORDER}
    for it in items:
        b = _portal_bucket_from_item(it)
        if b not in buckets:
            buckets[b] = []
        buckets[b].append(it)
    nonempty = [k for k in _PORTAL_BUCKET_ORDER if buckets[k]]
    if len(nonempty) <= 1:
        return sorted(items, key=_item_display_rank, reverse=True)[:lim]
    for k in nonempty:
        buckets[k].sort(key=_item_display_rank, reverse=True)
    out: list[dict[str, Any]] = []
    seen: set[Any] = set()
    seen_dedupe: set[str] = set()
    indices = {k: 0 for k in nonempty}
    while len(out) < lim:
        took = False
        for k in nonempty:
            if len(out) >= lim:
                break
            bk = buckets[k]
            while indices[k] < len(bk):
                it = bk[indices[k]]
                indices[k] += 1
                cid = it.get("content_id")
                if cid in seen:
                    continue
                dkey = _portal_case_dedupe_key(it)
                if dkey and dkey in seen_dedupe:
                    continue
                seen.add(cid)
                if dkey:
                    seen_dedupe.add(dkey)
                out.append(it)
                took = True
                break
        if not took:
            break
    if len(out) < lim:
        for it in sorted(items, key=_item_display_rank, reverse=True):
            if len(out) >= lim:
                break
            cid = it.get("content_id")
            if cid in seen:
                continue
            dkey = _portal_case_dedupe_key(it)
            if dkey and dkey in seen_dedupe:
                continue
            seen.add(cid)
            if dkey:
                seen_dedupe.add(dkey)
            out.append(it)
    return out[:lim]


def _canonical_item_url_for_dedupe(item_url: str) -> str:
    raw = str(item_url or "").strip().split("#", 1)[0]
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        return p._replace(query="", fragment="").geturl().lower()
    except Exception:
        return raw.lower()


def _portal_case_dedupe_key(it: dict[str, Any]) -> str:
    """站內智慧查詢去重鍵：優先用主圖 identity + 價格/面積/格局等摘要，避免跨案照片相同造成結果重複。"""
    if not it or not isinstance(it, dict):
        return ""

    def norm_text(v: Any, *, max_len: int = 220) -> str:
        s = str(v or "")
        if not s:
            return ""
        s = unicodedata.normalize("NFKC", s).strip().lower()
        s = re.sub(r"[\s\u3000]+", "", s)
        return s[:max_len]

    def digits_only(v: Any, *, max_len: int = 64) -> str:
        s = str(v or "")
        if not s:
            return ""
        s = unicodedata.normalize("NFKC", s)
        d = re.sub(r"[^0-9]+", "", s)
        return d[:max_len]

    thumb = str(it.get("thumb_url") or "").strip()
    if not thumb:
        gal = it.get("gallery_urls")
        if isinstance(gal, list) and gal:
            thumb = str(gal[0] or "").strip()
    img_id = _canonical_listing_image_identity(thumb) if thumb else ""

    price_k = digits_only(it.get("price_text_hant") or "")
    area_k = digits_only(it.get("area_text_hant") or "")
    layout_k = digits_only(it.get("layout_text_hant") or "")
    built_k = digits_only(it.get("built_ym_jp") or "")

    if img_id:
        base = f"img:{img_id}|p:{price_k}|a:{area_k}|l:{layout_k}|b:{built_k}"
        return base

    addr = norm_text(it.get("address_line_jp") or it.get("building_name_jp") or it.get("title_original") or "")
    if addr:
        return f"addr:{addr}|p:{price_k}|a:{area_k}|l:{layout_k}|b:{built_k}"

    url_key = _canonical_item_url_for_dedupe(str(it.get("item_url") or ""))
    if url_key:
        return f"url:{url_key}"

    cid = str(it.get("content_id") or "").strip()
    return f"cid:{cid}" if cid else ""


def _dedupe_portal_case_items(items: list[dict[str, Any]], *, lim: int) -> list[dict[str, Any]]:
    if not items:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in sorted(items, key=_item_display_rank, reverse=True):
        if len(out) >= lim:
            break
        key = _portal_case_dedupe_key(it) or f"cid:{it.get('content_id')}"
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out[:lim]


def _resize_wrapper_inner_http_url(u: str) -> str:
    """SUUMO resizeImage 外殼網址內嵌 src 指向實際圖檔；人像路徑常只出現在 src 內。"""
    raw = (u or "").strip()
    lu = raw.lower()
    if "resizeimage" not in lu:
        return ""
    try:
        parsed = urlparse(raw)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        q = {str(k): str(v) for k, v in pairs}
        src = unquote(str(q.get("src") or "").strip())
        if src.startswith("http"):
            return src
        if src.startswith("//"):
            return "https:" + src
        # SUUMO：src 常為 gazo/bukken、gazo/kaisha 等相對路徑（無 http），需還原才能做物件／人像判斷與排序
        if src and "suumo" in lu:
            path = src.replace("\\", "/").strip()
            if path.startswith("/"):
                return "https://img01.suumo.com" + path.split("?", 1)[0]
            lowp = path.lower()
            if lowp.startswith("gazo/"):
                return "https://img01.suumo.com/jj/" + path
    except Exception:
        pass
    return ""


def _agent_portrait_heuristic_on_lower(lu: str) -> bool:
    """是否疑似仲介／店舖人像（lu 已為小寫）。"""
    if not lu.startswith("http"):
        return False
    # 明確為物件內容：永遠不當仲介圖
    if any(
        x in lu
        for x in (
            "/gazo/bukken/",
            "/front/gazo/bukken/",
            "/gazo/chuko/",
            "/gazo/sinchiku/",
            "gazo/bukken",
            "imgchuku",
        )
    ):
        return False
    if re.search(
        r"/(?:gaikan|naikan|madori|kukaku|menseki|shuuhen|shozaichi|chizu|map|madori)[^/]*\.",
        lu,
        re.I,
    ):
        return False
    if any(
        t in lu
        for t in (
            "gazo/kaisha/",
            "/kaisha/",
            "tantou",
            "tantou_",
            "toppage_comment",
            "comment/comment",
            "gazo/comment",
            "gazo/commen",
            "/staff",
            "staff_",
            "eigyou",
            "eigyo",
            "eigy",
            "profile",
            "shain",
            "jisha",
            "mensetsu",
            "fudousantantou",
            "tohohouse",
            "toho_house",
            "toho-house",
            "toho_h",
            "inc_cm_top",
            "inc_kr_top",
            "inc_cm_all",
            "company/used",
            "sales",
            "担当",
            "/logo",
            "companylogo",
            "brand_img",
            "/bnr/",
            "_bnr",
            "kyohan",  # 共販店頭人像常見路徑片段
            "gazo/salesman",
            "salesman",
            "commentator",
            "kyoushi",
        )
    ):
        return True
    if re.search(r"/img/\d{5,}_[0-9]{3}_[0-9]{2}_[0-9]{3}\.(?:jpe?g|png|webp)", lu):
        return True
    # 極小縮圖且不含物件路徑片語時，多為顔写真／小廣告（略保守，避免誤傷地圖／格局小圖）
    m2 = re.findall(r"[?&](?:w|h)=(\d+)", lu)
    if m2 and max(int(x) for x in m2) <= 180:
        if "bukken" not in lu and all(
            x not in lu for x in ("madori", "gaikan", "naikan", "chizu", "shuuhen", "kukaku", "map", "menseki", "plan")
        ):
            return True
    return False


def is_likely_agent_portrait_image_url(u: str) -> bool:
    """是否疑似仲介／店舖人像、品牌宣傳圖（非建物、格局、地圖等案源內容）。"""
    raw = (u or "").strip()
    if not raw:
        return False
    lu = raw.lower()
    # 字面即含業者相簿路徑（未解碼 query；避免 urlparse 邊界漏判）
    if "gazo%2fkaisha" in lu or "gazo%252fkaisha" in lu:
        return True
    if "%2fkaisha%2f" in lu and "gazo" in lu:
        return True
    # SUUMO resize：src 解碼後為 gazo/kaisha（業者／担当），外層網址不含 kaisha 字樣
    if "resizeimage" in lu and "suumo" in lu:
        try:
            pq = urlparse(raw)
            qm = {str(k): str(v) for k, v in parse_qsl(pq.query, keep_blank_values=True)}
            dec = unquote(str(qm.get("src") or "")).strip().lower()
            if "%" in dec:
                dec = unquote(dec).strip().lower()
            if dec and ("gazo/kaisha" in dec or "/kaisha/" in dec):
                return True
        except Exception:
            pass
    if _agent_portrait_heuristic_on_lower(lu):
        return True
    inner = _resize_wrapper_inner_http_url(raw)
    if inner:
        return _agent_portrait_heuristic_on_lower(inner.lower())
    return False


def sort_property_image_urls_for_hero(urls: list[str]) -> list[str]:
    """物件主相簿排序：照片優先；同類圖保留原站相簿順序。"""
    if not urls:
        return []

    def rank(idx_url: tuple[int, str]) -> tuple[int, int]:
        idx, u = idx_url
        lu = (u or "").lower()
        eff = _resize_wrapper_inner_http_url(u) or u
        el = eff.lower()
        # 內層路徑一併參考（resize 外殼常無 gaikan 字樣）
        bag = lu + " " + el
        if any(k in bag for k in ("gaikan", "appearance", "building", "gaikan_")):
            return (0, idx)
        if any(k in bag for k in ("naikan", "interior", "living", "ldk", "naikan_")):
            return (1, idx)
        if "bukken" in bag or "chuko" in bag or "mansion" in bag:
            return (2, idx)
        if any(k in bag for k in ("madori", "floorplan", "layout", "plan", "間取")):
            return (3, idx)
        if any(k in bag for k in ("map", "chizu", "location", "access", "station", "route", "地図")):
            return (4, idx)
        return (5, idx)

    return [u for _, u in sorted(enumerate(urls), key=rank)]


def is_non_image_portal_page_url(u: str) -> bool:
    """非圖片之門戶『物件詳情頁』網址（常從正文誤掃入），不應當作掲載写真候選。"""
    s = (u or "").strip()
    if not s.startswith("http"):
        return False
    low = s.lower()
    path0 = low.split("?", 1)[0]
    if any(path0.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp")):
        return False
    if "resizeimage" in low or "resizeimg" in low:
        return False
    try:
        parsed = urlparse(s)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
    except Exception:
        host = ""
        path = ""
    if "suumo.jp" in host:
        return True
    if any(
        h in host
        for h in (
            "homes.co.jp",
            "homes.jp",
            "rehouse.co.jp",
            "athome.co.jp",
            "realestate.yahoo.co.jp",
            "realestate.rakuten.co.jp",
            "yes1.co.jp",
            "yes-station.jp",
            "oheya-su.jp",
            "oheyasuu.com",
        )
    ):
        if not any(
            tok in path
            for tok in (
                "/image/",
                "/images/",
                "/smallimg/",
                "image.php",
                "/image_files/",
                "/img/",
                "/photo/",
                "/thumbnail/",
            )
        ):
            return True
    return False


def _is_non_listing_asset_url(text: str) -> bool:
    s = str(text or "").strip().lower()
    if not s:
        return False
    if _shared_is_suumo_non_property_image_url(s):
        return True
    if _is_yahoo_listing_image_url(s):
        return False
    try:
        decoded = unquote(s)
    except Exception:
        decoded = s
    for _ in range(2):
        try:
            nxt = unquote(decoded)
        except Exception:
            break
        if nxt == decoded:
            break
        decoded = nxt
    hay = f"{s} {decoded}"
    noisy_tokens = (
        "/tmpl/images/common/",
        "/special/feature/",
        "/company/used/rankstore/",
        "/static_app_contents/",
        "/assets/",
        "/common/",
        "/map/",
        "/bnr/",
        "/campaign/",
        "/event/",
        "/lp/",
        "ogp_estate",
        "comptopimpact",
        "q_sp",
        "sumai_74",
        "satei_74",
        "lineoa_special",
        "used_report",
        "sell_contract",
        "used_online",
        "suumo.jp/article/",
        "/article/oyakudachi/",
        "/shop_image/",
        "chara_good",
        "loading_white.gif",
        "s.yimg.jp/images/realestate/",
        "/edit/assets/suumo/img/include/",
        "/edit/assets/suumo/img/pagetop",
        "inc_cm_top_",
        "inc_kr_top_",
        "inc_kr_detail_",
        "inc_cm_all_",
        "jjcommon/img/btn",
        "mailmaga",
        "melmaga",
        "merumaga",
        "mail_magazine",
        "menuclose_soba",
        "barcode",
        "gomezbaibai",
        "hazard_map",
        "jukatsu",
        "simulation",
        "crrecruit",
        "/icon/",
        "/logo/",
        "/sprite/",
        "/banner/",
        "/bnr/",
        "/button",
        "_button",
        "btn_",
        "/btn",
        "button_",
        "/gyousha/",
        "/gyousha/image/",
        "/staff/",
        "/shop/",
        "/tenant/",
        "/spot/",
        "/ispot/",
        "shopimage",
        "shop_image",
        "staffphoto",
        "company_image",
        "company-photo",
        "close_button",
        "bt_to_pagetop",
        "pagetop",
        "bukken_baloon",
        "mapfiles/transparent",
        "marker",
        "balloon",
        "station",
        "train",
        "route",
        "access",
        "no_image",
        "noimage",
        "dummy",
        "placeholder",
        "text_points_",
        "points_silver",
        "points_blue",
        "realestate.rakuten.co.jp/img/text_",
        "realestate.rakuten.co.jp/img/result/",
        "twitter.png",
        "hatenablog.png",
        "mixi.png",
        "clear.gif",
        "yamarever",
        "ヤマレバー",
        "閲覧履歴",
        "ログイン",
    )
    if any(t in hay for t in noisy_tokens):
        return True
    dims = re.findall(r"[?&](?:w|h|width|height)=(\d{1,5})", hay)
    if dims and max(int(x) for x in dims) <= 180:
        return True
    return False


def is_suumo_non_property_image_url(text: str) -> bool:
    """Return True for SUUMO page chrome, agency media, thumbnails and nearby-facility photos."""
    return _shared_is_suumo_non_property_image_url(text)


def _is_yahoo_listing_image_url(text: str) -> bool:
    s = str(text or "").strip()
    if not s or not s.startswith("http"):
        return False
    try:
        parsed = urlparse(s)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return host.endswith("realestate-pctr.c.yimg.jp") and "/realestate-buy-image/bld_image/" in path


def _yahoo_listing_image_path_token(item_url: str) -> str:
    """Yahoo detail ids map to image paths: b0022522314 -> /00/2252/2314/."""
    raw = str(item_url or "").strip()
    if "realestate.yahoo.co.jp" not in raw.lower():
        return ""
    m = re.search(r"/detail[^/]*/b(\d{10})(?:/|$)", raw, re.I)
    if not m:
        return ""
    digits = m.group(1)
    return f"/{digits[:2]}/{digits[2:6]}/{digits[6:10]}/".lower()


def _is_truncated_listing_image_url(text: str) -> bool:
    s = str(text or "").strip()
    if not s or not s.startswith("http"):
        return False
    try:
        parsed = urlparse(s)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    query = (parsed.query or "").lower()
    if host.endswith("realestate-pctr.c.yimg.jp") and re.search(r"(?:^|&)nf_(?:$|&)", query):
        return True
    return False


def _is_unfetchable_athome_thumbnail_url(text: str) -> bool:
    """Reject AtHome new-mansion thumbnail endpoints that commonly 404 via proxy.

    AtHome sometimes exposes ``/cimages/.../thm/<id>`` entries without a real
    image extension. They look like gallery images in scraped text but fail when
    requested directly, so keeping them in card galleries creates fallback cards.
    """
    s = str(text or "").strip()
    if not s or not s.startswith("http"):
        return False
    try:
        parsed = urlparse(s)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if "athome.co.jp" not in host:
        return False
    if "/mansion/shinchiku/cimages/" not in path or "/thm/" not in path:
        return False
    return not any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))


def _is_unfetchable_listing_image_url(text: str) -> bool:
    s = str(text or "").strip()
    if not s or not s.startswith("http"):
        return False
    if _is_unfetchable_athome_thumbnail_url(s):
        return True
    try:
        parsed = urlparse(s)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if host.endswith("realestate-pctr.c.yimg.jp") and "/realestate-buy-image/" in path:
        return not any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))
    if "suumo." in host and "resizeimage" in path:
        try:
            q = dict(parse_qsl(parsed.query, keep_blank_values=True))
            src = unquote(str(q.get("src") or "")).lower()
        except Exception:
            src = ""
        return bool(src) and not any(src.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))
    if "athome.co.jp" in host and "/mansion/shinchiku/cimages/guidance/" in path:
        # Guidance images are often access/guide assets and several direct
        # original URLs 404. They are not suitable as fast-loading card photos.
        return True
    return False


_LM_URL_KEYS = (
    "url",
    "src",
    "image",
    "image_url",
    "imageUrl",
    "largeUrl",
    "large_url",
    "photo",
    "thumb",
    "thumbnail",
    "original",
    "path",
    "href",
)

_RE_BODY_LISTING_IMG_EXT = re.compile(
    r"https?://[^\s\]\)\"'<>]+\.(?:jpe?g|png|webp|gif)(?:\?[^\s\]\)\"'<>]*)?",
    re.I,
)
_RE_BODY_SUUMO_RESIZE = re.compile(
    r"https?://[^\s\]\)\"'<>]*(?:resizeimage|resizeimg)[^\s\]\)\"'<>]+",
    re.I,
)
_RE_BODY_MAJOR_LISTING_CDN = re.compile(
    r"https?://(?:(?:img\d*\.)?(?:suumo\.jp|suumo\.com|athome\.co\.jp|homes\.co\.jp|rehouse\.co\.jp|"
    r"yahoo\.co\.jp|mansion-market\.com|rakuten\.co\.jp)|(?:image\d*|img)\.homes\.jp)[^\s\]\)\"'<>]+",
    re.I,
)
_RE_BODY_ANY_HOST_HINT = re.compile(
    r"https?://[^\s\]\)\"'<>]*(?:century21|c21\.co|rehouse|homes\.co\.jp|athome|sumifu)[^\s\]\)\"'<>]+",
    re.I,
)


def _extract_listing_body_image_urls(body: str, *, limit: int = 48) -> list[str]:
    """從全文擷取物件刊登圖（含 SUUMO resize、主流門戶 CDN、常見副檔名），長正文不限前 4000 字。"""
    raw = str(body or "")
    if not raw:
        return []
    if len(raw) > 240000:
        raw = raw[:240000]
    out: list[str] = []
    seen: set[str] = set()

    def take(m: str) -> None:
        u = m.strip().rstrip(").,;\"'")
        if not u or u in seen:
            return
        if not u.startswith("http"):
            return
        if is_non_image_portal_page_url(u):
            return
        seen.add(u)
        out.append(u[:800])

    for rx in (
        _RE_BODY_LISTING_IMG_EXT,
        _RE_BODY_SUUMO_RESIZE,
        _RE_BODY_MAJOR_LISTING_CDN,
        _RE_BODY_ANY_HOST_HINT,
    ):
        for m in rx.finditer(raw):
            take(m.group(0))
            if len(out) >= limit:
                return out[:limit]
    return out[:limit]


def _score_listing_image_url(u: str) -> int:
    lu = str(u or "").lower()
    if not lu.startswith("http"):
        return -999
    if not _is_percent_encoding_valid(lu):
        return -999
    if _is_truncated_listing_image_url(lu):
        return -999
    if _is_unfetchable_listing_image_url(lu):
        return -999
    if is_non_image_portal_page_url(u):
        return -999
    if _is_non_listing_asset_url(lu):
        return -999
    if is_likely_agent_portrait_image_url(u):
        return -420
    score = 0
    if any(k in lu for k in ("logo", "icon", "sprite", "blank", "avatar", "banner", "ad", "qr", "download")):
        score -= 180
    if any(
        k in lu
        for k in (
            "bukken",
            "mansion",
            "apartment",
            "house",
            "building",
            "gaikan",
            "naikan",
            "room",
            "living",
            "century21",
            "c21.",
            "sumifu",
            "mansion-market",
            "image_files/path",
        )
    ):
        score += 110
    if any(k in lu for k in ("map", "chizu", "location", "access", "station", "route", "train", "地図", "周辺")):
        score -= 180
    if any(k in lu for k in ("madori", "floorplan", "間取", "layout", "plan")):
        score -= 80
    if "suumo." in lu and "resize" in lu:
        score += 20
    if "suumo." in lu and "/jj/" in lu and "kaisha" not in lu and "tantou" not in lu:
        score += 48
    if ("homes.jp" in lu or "homes.co.jp" in lu) and any(k in lu for k in ("/smallimg/", "image.php")):
        score += 86
    if _is_yahoo_listing_image_url(lu):
        score += 86
    if "%2fsale%2f" in lu or "/sale/" in lu:
        score += 32
    dims = re.findall(r"[?&](?:w|h|width|height)=(\d{2,5})", lu)
    if dims:
        max_dim = max(int(x) for x in dims)
        if max_dim >= 640:
            score += 18
        elif max_dim <= 200:
            score -= 25
    return score


def _canonical_listing_image_identity(u: str) -> str:
    """同一張圖不同縮圖參數／query 時的去重鍵；盡量對齊原站相簿順序的第一張身份。"""
    s = str(u or "").strip().rstrip(").,;\"'")
    if not s or not s.startswith("http"):
        return ""
    try:
        p = urlparse(s)
        host = (p.netloc or "").lower()
        path = (p.path or "").lower()
        if "suumo." in host and "resizeimage" in path:
            q = dict(parse_qsl(p.query, keep_blank_values=True))
            src = str(q.get("src") or "").strip().lower()
            if src:
                return f"suumo_src:{src}"
        if "realestate.yahoo.co.jp" in host:
            return f"path:{host}{path}"
        if "athome.co.jp" in host:
            norm_path = path.replace("/cimages/", "/images/")
            return f"path:{host}{norm_path}"
        if any(h in host for h in ("homes.co.jp", "homes.jp", "rehouse.co.jp", "sumifu.co.jp")):
            if "homes.jp" in host:
                q = dict(parse_qsl(p.query, keep_blank_values=True))
                inner = str(q.get("file") or q.get("src") or "").strip().lower()
                if inner:
                    return f"homes_file:{unquote(inner)}"
                if "image.php" in path or "/smallimg/" in path:
                    return f"path_query:{host}{path}?{p.query}".lower()
            return f"path:{host}{path}"
        return f"path:{host}{path}"
    except Exception:
        return s[:500].lower()


def _homes_listing_image_tokens(item_url: str) -> tuple[str, ...]:
    from src.homes_media_token import homes_listing_image_tokens

    return homes_listing_image_tokens(item_url)


def _homes_is_canonical_listing_image_candidate(url: str) -> bool:
    from src.homes_media_token import homes_is_canonical_listing_image_candidate

    return homes_is_canonical_listing_image_candidate(url)


def _homes_leading_ielove_group_urls(urls: list[str]) -> list[str]:
    from src.homes_media_token import homes_leading_ielove_group_urls

    return homes_leading_ielove_group_urls(urls)


def _homes_listing_image_token(item_url: str) -> str:
    """相容舊呼叫點：回傳第一個 token（完整過濾請用 _homes_listing_image_tokens）。"""
    toks = _homes_listing_image_tokens(item_url)
    return toks[0] if toks else ""


def _is_athome_detail_gallery_compatible_image(url: str) -> bool:
    """Allow a narrow set of AtHome project-detail images in case lightbox playback.

    The crawler-level pollution filter still rejects these groups before
    persistence; this exception only prevents already-sourced detail galleries
    from becoming empty.
    """
    s = str(url or "").strip().lower()
    if "athome.co.jp" not in s:
        return False
    return bool(
        re.search(
            r"/mansion/shinchiku/(?:cimages|images)/(?:project_detail_slide|guidance)/",
            s,
        )
    )


def _listing_image_candidates_scored(
    image_urls: str,
    body_original: str,
    listing_media_json: str,
    *,
    item_url: str = "",
    max_candidates: int = 0,
    body_url_limit: int = 48,
) -> list[tuple[str, int]]:
    candidates: list[str] = []
    seen_id: set[str] = set()
    from_lm_keys: set[str] = set()
    primary_keys: set[str] = set()
    homes_tokens = _homes_listing_image_tokens(item_url)
    homes_matched: set[str] = set()
    homes_canonical_candidates: set[str] = set()
    homes_canonical_primary_candidates: set[str] = set()
    yahoo_path_token = _yahoo_listing_image_path_token(item_url)
    max_c = max(0, int(max_candidates or 0))
    # HOMES token 過濾需要看見「本物件」圖片才能生效；給更高上限避免太早截斷導致空縮圖。
    if homes_tokens and max_c > 0:
        max_c = max(80, max_c * 3)

    def _at_cap() -> bool:
        return max_c > 0 and len(candidates) >= max_c

    def _push(u: str, *, from_lm: bool = False, from_body: bool = False, context: str = "") -> None:
        s = str(u or "").strip().rstrip(").,;\"'")
        if not s:
            return
        if not s.startswith("http"):
            return
        athome_detail_gallery_compatible = _is_athome_detail_gallery_compatible_image(s)
        if not athome_detail_gallery_compatible and is_portal_non_property_image_url(s, item_url=item_url, context=context):
            return
        if not _is_percent_encoding_valid(s):
            return
        if _is_truncated_listing_image_url(s):
            return
        if _is_unfetchable_listing_image_url(s):
            return
        if not athome_detail_gallery_compatible and _is_non_listing_asset_url(s):
            return
        if is_non_image_portal_page_url(s):
            return
        if yahoo_path_token and _is_yahoo_listing_image_url(s):
            try:
                y_path = urlparse(s).path.lower()
            except Exception:
                y_path = s.lower()
            if yahoo_path_token not in unquote(y_path):
                return
        cid = _canonical_listing_image_identity(s)
        if not cid or cid in seen_id:
            return
        seen_id.add(cid)
        store = s if len(s) <= 4096 else s[:4096]
        candidates.append(store)
        if from_lm:
            from_lm_keys.add(store)
        if not from_body:
            primary_keys.add(store)
        if homes_tokens:
            try:
                decoded = unquote(store).lower()
            except Exception:
                decoded = store.lower()
            if any(tok in decoded for tok in homes_tokens):
                homes_matched.add(store)
            if _homes_is_canonical_listing_image_candidate(store):
                homes_canonical_candidates.add(store)
                if not from_body:
                    homes_canonical_primary_candidates.add(store)

    # 1) listing_media_json：銷售物件相簿（優先）
    # 先解析 JSON entry，連同 alt/caption/category 判斷；避免把 HOME'S 的
    # 「ログイン」「部屋情報」「周辺」等站內 UI 縮圖當作本案相簿。
    lm_text = str(listing_media_json or "")
    if lm_text:
        parsed_lm: Any = None
        try:
            parsed_lm = json.loads(lm_text)
        except Exception:
            parsed_lm = None
        if isinstance(parsed_lm, list):
            for entry in parsed_lm:
                if _at_cap():
                    break
                u, ctx = media_entry_url_context(entry)
                if not u:
                    continue
                u_lc = str(u or "").lower()
                if ("homes.co.jp" in u_lc or "homes.jp" in u_lc) and is_homes_non_property_media(u, ctx):
                    continue
                _push(u, from_lm=True, context=ctx)
        else:
            i = 0
            n = len(lm_text)
            while i < n and not _at_cap():
                j = lm_text.find("http", i)
                if j < 0:
                    break
                k = lm_text.find('"', j)
                if k < 0:
                    k = j
                    while k < n and (not lm_text[k].isspace()) and lm_text[k] not in ",]})":
                        k += 1
                if k <= j:
                    i = j + 4
                    continue
                _push(lm_text[j:k], from_lm=True)
                i = k + 1

    # 2) image_urls 欄
    for line in str(image_urls or "").splitlines():
        if _at_cap():
            break
        _push(line.strip(), from_lm=False)

    # 3) 正文含圖網址（全文掃描，勿限前 4000 字）
    if not _at_cap():
        b_lim = max(0, min(int(body_url_limit), 96))
    else:
        b_lim = 0
    if b_lim > 0:
        for u in _extract_listing_body_image_urls(str(body_original or ""), limit=b_lim):
            if _at_cap():
                break
            _push(u, from_lm=False, from_body=True)

    if not candidates:
        return []

    # HOMES：若可找到與該物件 b-id 對應的圖片，就只保留該組，避免「推薦物件」縮圖覆蓋主圖。
    if homes_tokens:
        if homes_matched:
            candidates = [u for u in candidates if u in homes_matched]
            from_lm_keys = {u for u in from_lm_keys if u in homes_matched}
        else:
            # b-id token 存在但完全無 canonical 命中時，先防止推薦物件回流。
            # 若 DOM 已混到其他 HOME'S canonical 圖，直接拒絕；若只有 HOME'S
            # ielove 代理圖，保留第一個連續主圖群，這是部分有效刊登頁的真相簿。
            if homes_canonical_primary_candidates:
                return []
            # 正文常含「推薦物件」或列表殘留的圖片 URL；若主要素材欄
            # （listing_media_json / image_urls）已有 coherent ielove 主圖群，
            # 不讓正文 canonical 混圖把本案真圖整組否決。
            primary_candidates = [u for u in candidates if u in primary_keys]
            ielove_urls = set(_homes_leading_ielove_group_urls(primary_candidates or candidates))
            if not ielove_urls and homes_canonical_candidates:
                return []
            if not ielove_urls:
                return []
            candidates = [u for u in candidates if u in ielove_urls]
            from_lm_keys = {u for u in from_lm_keys if u in ielove_urls}

    # 原站 listing_media 順序優先（與門戶主相簿一致），其餘再以分數排序補齊
    lm_ordered = [u for u in candidates if u in from_lm_keys]
    non_lm = [u for u in candidates if u not in from_lm_keys]
    non_lm_scored = sorted(
        ((u, _score_listing_image_url(u)) for u in non_lm),
        key=lambda x: x[1],
        reverse=True,
    )
    tail: list[tuple[str, int]] = []
    for u, sc in non_lm_scored:
        if sc > -120 or is_likely_agent_portrait_image_url(u):
            tail.append((u, sc))
        elif sc > -350:
            tail.append((u, sc))
    if not tail and non_lm_scored:
        tail = [(u, sc) for u, sc in non_lm_scored if sc > -999]
    out_pairs: list[tuple[str, int]] = []
    for u in lm_ordered:
        sc = _score_listing_image_url(u) + 92
        out_pairs.append((u, sc))
    out_pairs.extend(tail)
    if not out_pairs and candidates:
        for u in candidates:
            out_pairs.append((u, _score_listing_image_url(u)))
    return out_pairs


def _first_thumb(image_urls: str, body_original: str, listing_media_json: str, *, item_url: str = "") -> str:
    scored = _listing_image_candidates_scored(
        image_urls,
        body_original,
        listing_media_json,
        item_url=item_url,
        max_candidates=24,
        body_url_limit=24,
    )
    return scored[0][0] if scored else ""


def split_listing_image_urls_property_vs_agent(
    image_urls: str,
    body_original: str,
    listing_media_json: str,
    *,
    item_url: str = "",
    prop_limit: int = 10,
    agent_limit: int = 6,
) -> tuple[list[str], list[str]]:
    """物件內容圖（建物／地圖／格局等）與仲介人像分離；人像列於最後一組供 UI 顯示。"""
    plim = max(1, min(int(prop_limit or 8), 80))
    alim = max(0, min(int(agent_limit or 6), 12))
    max_cand = max(24, (plim + alim) * 4)
    scored = _listing_image_candidates_scored(
        image_urls,
        body_original,
        listing_media_json,
        item_url=item_url,
        max_candidates=max_cand,
        body_url_limit=48,
    )
    prop: list[str] = []
    ag: list[str] = []
    for u, _sc in scored:
        if is_likely_agent_portrait_image_url(u):
            if u not in ag and len(ag) < alim:
                ag.append(u)
        else:
            if u not in prop and len(prop) < plim:
                prop.append(u)
    # 二次：listing_media 順序可能讓誤判人像留在物件組，改列仲介組
    spill = [u for u in prop if is_likely_agent_portrait_image_url(u)]
    if spill:
        spill_set = set(spill)
        prop = [u for u in prop if u not in spill_set]
        for u in spill:
            if u not in ag and len(ag) < alim:
                ag.append(u)
    prop = sort_property_image_urls_for_hero(prop)
    return prop, ag


def ordered_listing_image_urls(
    image_urls: str,
    body_original: str,
    listing_media_json: str,
    *,
    item_url: str = "",
    limit: int = 6,
) -> list[str]:
    """SUUMO 式列表：主圖 + 小圖；物件內容優先，仲介人像一律置後。"""
    lim = max(1, min(int(limit or 6), 80))
    prop, ag = split_listing_image_urls_property_vs_agent(
        image_urls,
        body_original,
        listing_media_json,
        item_url=item_url,
        prop_limit=lim,
        agent_limit=min(6, lim),
    )
    return (prop + ag)[:lim]


def _thumb_kind_label(u: str) -> str:
    lu = str(u or "").lower()
    if any(k in lu for k in ("map", "chizu", "location", "access", "station", "route", "地図")):
        return "地圖"
    if any(k in lu for k in ("madori", "floorplan", "floor_plan", "間取", "layout", "plan")):
        return "格局"
    return "主圖"


_FW_DIGITS_TRANS = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
_RE_HAS_FULLWIDTH_NUM = re.compile(r"[０-９．，]")


def _to_half_width_num(text: str) -> str:
    s = str(text or "")
    if not s:
        return ""
    if _RE_HAS_FULLWIDTH_NUM.search(s) is None:
        return s
    return s.translate(_FW_DIGITS_TRANS)


def _normalize_price_currency_tokens(text: str) -> str:
    """將常見全形／繁體價格單位統一，利於萬円 regex 命中。"""
    s = str(text or "")
    s = s.replace("萬日圓", "万円").replace("萬日元", "万円").replace("萬円", "万円")
    s = s.replace("万日圓", "万円").replace("万日元", "万円")
    return s


def _is_suumo_property_detail_url(url: str) -> bool:
    """是否為 SUUMO 單一物件詳情（排除 /ms/chuko/tokyo/ 等區域匯總頁）。"""
    u = (url or "").strip().lower().split("#", 1)[0].split("?", 1)[0]
    if "suumo.jp" not in u:
        return False
    if "/jj/bukken/shosai/" in u:
        return True
    if "/chintai/jnc_" in u or "/chintai/nc_" in u:
        return True
    if re.search(r"/(?:nc_|jnc_)[0-9a-z_]+", u, flags=re.I):
        return True
    return False


def _num_from_text(s: str) -> float | None:
    t = str(s or "").strip()
    if not t:
        return None
    t = _to_half_width_num(t).replace(",", "").replace("，", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except Exception:
        return None


def _fmt_number_zh(n: float | None, *, max_decimals: int = 2) -> str:
    if n is None:
        return ""
    if abs(n - int(n)) < 1e-9:
        return f"{int(n):,}"
    q = f"{n:,.{max_decimals}f}".rstrip("0").rstrip(".")
    return q


_GENERATED_LISTING_CACHE_PREFIXES = ("日本房產案源", "日本房产案源")
_LISTING_VALUE_STOP_LABELS = (
    "物件名",
    "價格",
    "价格",
    "販売価格",
    "価格",
    "格局",
    "間取り",
    "專有面積",
    "专有面积",
    "専有面積",
    "所在地",
    "住所",
    "交通",
    "沿線",
    "沿線・駅",
    "築年月",
    "完成時期",
    "樓層",
    "楼层",
    "所在階",
    "總戶數",
    "总户数",
    "総戸数",
    "建物構造",
    "構造",
    "管理費",
    "修繕積立金",
    "修繕金",
    "停車場",
    "駐車場",
    "現況",
    "引渡",
    "來源",
    "来源",
)
_LISTING_VALUE_STOP_RE = re.compile(
    r"\s+(?:" + "|".join(re.escape(x) for x in _LISTING_VALUE_STOP_LABELS) + r")\s*[:：]",
    re.I,
)


def _listing_text_is_generated_cache(text: Any) -> bool:
    s = str(text or "").lstrip()
    return any(s.startswith(prefix) for prefix in _GENERATED_LISTING_CACHE_PREFIXES)


def _compact_listing_value(value: Any, *, max_len: int = 240) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n：:-")
    if not s or s in {"—", "-", "－", "ー", "None", "null"}:
        return ""
    m = _LISTING_VALUE_STOP_RE.search(s)
    if m and m.start() > 0:
        s = s[: m.start()].strip(" \t\r\n：:-")
    return s[:max_len].strip()


def _clean_listing_building_name(value: Any) -> str:
    s = _compact_listing_value(value, max_len=140)
    if not s:
        return ""
    s = re.sub(r"^\s*【ホームズ】\s*", "", s).strip()
    s = re.sub(r"\s*[｜|]\s*(?:新築|中古)?マンションの物件情報.*$", "", s).strip()
    s = re.sub(r"\s*[｜|]\s*.+?の中古マンション.*$", "", s).strip()
    s = re.sub(r"\s+\d[\d,]{0,12}\s*万(?:円)?\s*$", "", s).strip()
    if _is_likely_non_property_building_name(s):
        return ""
    return s[:120]


def _clean_listing_address_line(value: Any) -> str:
    s = _compact_listing_value(value, max_len=260)
    if not s:
        return ""
    for marker in ("[ 地図を確認 ]", "[地図を確認]", "[ 地図を見る ]", "[地図を見る]", "地図を見る", "地図を確認"):
        idx = s.find(marker)
        if idx > 0:
            s = s[:idx].strip()
            break
    s = re.split(r"\s+(?:交通|沿線・駅|沿線駅)\s*[:：]", s, maxsplit=1)[0].strip()
    s = re.sub(r"\s*\[\s*$", "", s).strip()
    if _is_seo_mansion_address_line(s):
        return ""
    return s[:160]


def _clean_listing_access_line(value: Any) -> str:
    s = _compact_listing_value(value, max_len=360)
    if not s:
        return ""
    s = re.sub(r"^(?:交通|沿線・駅|沿線駅)\s*[:：]\s*", "", s).strip()
    s = s.replace("地図を見る", "").replace("地図を確認", "").strip()
    m = re.search(r"\s+(?:所在地|住所|専有面積|間取り|築年月|完成時期|販売価格|価格)\s*[:：]", s)
    if m and m.start() > 0:
        s = s[: m.start()].strip()
    if _looks_like_site_disclaimer_transit(s):
        return ""
    return s[:260]


def _clean_listing_fee_value(value: Any) -> str:
    s = _compact_listing_value(value, max_len=80)
    if not s or s in {"—", "-"}:
        return ""
    if not re.search(r"[0-9０-９]", s):
        return ""
    if not re.search(r"(?:円|元|月|無料|無)", s):
        return ""
    return s[:80]


def _clean_listing_built_value(value: Any) -> str:
    s = _compact_listing_value(value, max_len=180)
    if not s:
        return ""
    compact = re.sub(r"\s+", "", s)
    patterns = (
        r"\d{4}\s*年\s*\d{1,2}\s*月(?:\s*(?:上旬|中旬|下旬))?(?:\s*(?:完成予定|竣工予定|築))?",
        r"\d{4}\s*[./／]\s*\d{1,2}(?:\s*(?:完成予定|竣工予定))?",
        r"令和\s*[0-9元]{1,2}\s*(?:年|[./／])\s*\d{1,2}\s*月?(?:\s*(?:上旬|中旬|下旬))?(?:\s*(?:完成予定|竣工予定))?",
        r"(?:新築|築浅)",
    )
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            return re.sub(r"\s+", "", m.group(0)).strip()
    if re.search(r"(?:現地写真|撮影風景|お電話|お気軽|Yahoo!?不動産|物件詳細|住宅|來源|来源|引き渡し|引渡|土地状況|地目|用途地域|土地権利)", s, re.I):
        return ""
    if len(compact) > 48:
        return ""
    return s[:80]


def _clean_listing_parking_value(value: Any) -> str:
    s = _compact_listing_value(value, max_len=160)
    if not s:
        return ""
    s = re.sub(r"^\s*ヒント\s*[:：]\s*", "", s).strip()
    if re.search(r"(?:会社概要|仲介|宅地建物取引業|不動産協会)", s):
        return ""
    if re.search(r"(?:玄関|バルコニー|収納|設備|リフォーム|土間|その他現地|スロープ|キッチン|庭|クローク)", s):
        m_explicit = re.search(r"(?:[0-9０-９]+\s*台(?:以上|可|分|有)?|駐車場\s*(?:有|無|なし|空有|近隣|要確認|相談|無料)|(?:空有|近隣|要確認|相談|無料))", s)
        return re.sub(r"\s+", "", m_explicit.group(0)).strip("、。・/／ ")[:40] if m_explicit else ""
    patterns = (
        r"(?:駐車場\s*)?(?:有|無|なし|空有|近隣|要確認|相談|無料|[0-9０-９]+\s*台(?:以上|可|分|有)?)",
        r"(?:駐車場\s*)?(?:[0-9０-９]+\s*台(?:以上|可|分|有)?)",
    )
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            out = re.sub(r"\s+", "", m.group(0)).strip("、。・/／ ")
            if out and out not in {"駐車場"}:
                return out
    if re.search(r"(?:情報更新日|情報掲載開始日|資料公開|下次更新)", s):
        return ""
    if re.search(r"(?:新築一戸建て|Yahoo!?不動産|物件欄位摘要|情報掲載|価格|間取り|所在地|交通)", s, re.I):
        return ""
    if len(s) > 60:
        return ""
    return s[:80]


def _clean_listing_handover_value(value: Any) -> str:
    s = _compact_listing_value(value, max_len=180)
    if not s or s in {"—", "-"}:
        return ""
    s = re.sub(r"^\s*ヒント\s*[:：]\s*", "", s).strip()
    if not s:
        return ""
    simple = re.match(
        r"^(?:相談|即引渡可|即時|即入居可|[0-9０-９]{4}\s*年\s*[0-9０-９]{1,2}\s*月(?:上旬|中旬|下旬)?(?:予定|可)?)",
        s,
    )
    if simple:
        return re.sub(r"\s+", "", simple.group(0)).strip()
    if re.search(r"(?:会社概要|仲介|宅地建物取引業|不動産協会|沿線駅|乗り換え案)", s):
        return ""
    if len(s) > 140:
        s = s[:140].rstrip("、。，. ")
    return s


def _clean_listing_structure_value(value: Any) -> str:
    s = _compact_listing_value(value, max_len=140).strip("—- ")
    if not s:
        return ""
    structure_match = re.search(
        r"(?:SRC|RC|S造|鉄骨造|鉄筋コンクリート|鉄骨鉄筋コンクリート|木造|軽量鉄骨)"
        r"(?:[0-9０-９]{1,3}\s*階建)?(?:一部(?:RC|SRC|S造|木造))?",
        s,
        flags=re.I,
    )
    if "ヒント" in s or "沿線駅" in s or "乗り換え案" in s or "完成予定" in s:
        return re.sub(r"\s+", "", structure_match.group(0)).strip()[:60] if structure_match else ""
    if re.search(r"(?:仕様|現地外観写真|玄関|耐震性|シロアリ|徒歩|分譲地|キッチン|標準装備|システム)", s):
        return re.sub(r"\s+", "", structure_match.group(0)).strip()[:60] if structure_match else ""
    if _LISTING_VALUE_STOP_RE.search(" " + s):
        s = _LISTING_VALUE_STOP_RE.split(" " + s, maxsplit=1)[0].strip()
    if len(s) > 90:
        return re.sub(r"\s+", "", structure_match.group(0)).strip()[:60] if structure_match else ""
    if re.search(r"(?:來源|来源|管理費|修繕|物件名|価格|價格|格局|所在地|交通)", s):
        return ""
    return s[:90]


def _extract_listing_price_text(
    blob: str, *, item_url: str = "", blob_hw: str | None = None
) -> tuple[float | None, str]:
    b = _normalize_price_currency_tokens(blob_hw if blob_hw is not None else _to_half_width_num(blob))
    # 優先：緊鄰「販売／価格／家賃」等標籤的萬円（避免先命中頁尾雜數）
    m0 = re.search(
        r"(?:販売(?:価格)?|販賣價格|(?:参考)?価格|売出(?:価格)?|家賃|賃料|購入(?:価格)?|價格|价格|售價)\s*[:：]?\s*"
        r"([0-9０-９][0-9０-９,，]{0,8}(?:\.[0-9]+)?)\s*万円",
        b,
        flags=re.I,
    )
    if m0:
        man = _num_from_text(m0.group(1))
        if man is not None and 0 < man <= 500000:
            return man, f"{_fmt_number_zh(man, max_decimals=1)}萬日圓"
    m0b = re.search(
        r"(?:販売(?:価格)?|(?:参考)?価格|家賃|賃料|價格|价格)\s*[:：]\s*"
        r"([0-9０-９][0-9０-９,，]{0,8}(?:\.[0-9]+)?)\s*万(?:円)?",
        b,
        flags=re.I,
    )
    if m0b:
        man = _num_from_text(m0b.group(1))
        if man is not None and 0 < man <= 500000:
            return man, f"{_fmt_number_zh(man, max_decimals=1)}萬日圓"
    # SUUMO 區域／列表匯總頁常出現多個「○○万円」，勿取全文第一個數字
    suumo_non_detail = "suumo.jp" in (item_url or "").lower() and not _is_suumo_property_detail_url(item_url)
    man_yen_hits = len(re.findall(r"[0-9][0-9,]{0,8}(?:\.[0-9]+)?\s*万円", b[:4200], flags=re.I))
    skip_loose_man_yen = suumo_non_detail and man_yen_hits >= 2
    m1 = re.search(r"([0-9][0-9,]{0,8}(?:\.[0-9]+)?)\s*万円", b, flags=re.I)
    if not skip_loose_man_yen and m1:
        man = _num_from_text(m1.group(1))
        if man is not None and man > 0:
            return man, f"{_fmt_number_zh(man, max_decimals=1)}萬日圓"
    m1b = re.search(r"(?<![0-9０-９])([0-9０-９][0-9０-９,，]{0,8})\s*万円(?![0-9０-９])", b)
    if m1b:
        man = _num_from_text(m1b.group(1))
        if man is not None and man > 0:
            return man, f"{_fmt_number_zh(man, max_decimals=1)}萬日圓"
    m2 = re.search(r"([0-9][0-9,]{0,8}(?:\.[0-9]+)?)\s*萬(?:日圓|日元|円)?", b, flags=re.I)
    if m2:
        man = _num_from_text(m2.group(1))
        if man is not None and man > 0:
            return man, f"{_fmt_number_zh(man, max_decimals=1)}萬日圓"
    m3 = re.search(r"([0-9][0-9,]{3,})\s*円", b, flags=re.I)
    if m3:
        yen = _num_from_text(m3.group(1))
        if yen is not None and yen > 0:
            man = yen / 10000.0
            return man, f"{_fmt_number_zh(man, max_decimals=1)}萬日圓"
    # 摘要前段僅出現一處合理「萬円」時採用（詳情頁較可靠；匯總頁避免多價亂取）
    head = b[:4200]
    hits = [
        _num_from_text(mm.group(1))
        for mm in re.finditer(r"([0-9][0-9,]{0,8}(?:\.[0-9]+)?)\s*万円", head, flags=re.I)
    ]
    reasonable = [x for x in hits if x is not None and 30 <= x <= 200000]
    if reasonable:
        if _is_suumo_property_detail_url(item_url) or len(reasonable) == 1:
            man = reasonable[0]
            return man, f"{_fmt_number_zh(man, max_decimals=1)}萬日圓"
    if re.search(
        r"(?:販売(?:価格)?|価格|販賣價格|價格|价格|售價|家賃|賃料)\s*[:：]?\s*(?:価格)?未定",
        b,
        flags=re.I,
    ):
        return None, "價格未定"
    return None, ""


def _parse_man_from_price_text(text: str) -> float | None:
    """從已格式化的中文價格字串還原「萬円」數值（供換算）。"""
    s = _to_half_width_num(str(text or "")).replace(",", "").strip()
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*萬", s)
    if not m:
        return None
    return _num_from_text(m.group(1))


# 約略牌告：1 台幣 ≈ 4.55 日圓、1 人民幣 ≈ 20.5 日圓（僅供參考，非即時匯率）
_JPY_PER_TWD_APPROX = 4.55
_JPY_PER_CNY_APPROX = 20.5


def _listing_price_fx_hant(price_man: float | None, price_text: str) -> str:
    """萬日圓價格旁顯示：約略萬台幣、萬人民幣。"""
    man: float | None = None
    if price_man is not None:
        try:
            v = float(price_man)
            man = v if v > 0 else None
        except Exception:
            man = None
    if man is None or man <= 0:
        man = _parse_man_from_price_text(price_text)
    if man is None or man <= 0:
        return ""
    jpy_total = float(man) * 10000.0
    twd = jpy_total / _JPY_PER_TWD_APPROX
    cny = jpy_total / _JPY_PER_CNY_APPROX
    twd_wan = twd / 10000.0
    cny_wan = cny / 10000.0

    def _wan_txt(x: float) -> str:
        if x >= 100:
            return f"{x:.0f}"
        t = f"{x:.2f}".rstrip("0").rstrip(".")
        return t if t else "0"

    return (
        f"約 {_wan_txt(twd_wan)} 萬台幣｜約 {_wan_txt(cny_wan)} 萬人民幣（匯率約略，僅供參考）"
    )


def _layout_jp_to_zh(raw: str) -> str:
    s = unicodedata.normalize("NFKC", _to_half_width_num(raw)).strip().upper().replace(" ", "")
    m = re.match(r"^(\d+)(S?)(LDK|LK|DK|K|R)$", s)
    if not m:
        return s
    n = int(m.group(1))
    has_s = m.group(2) == "S"
    core = m.group(3)
    if core == "R":
        out = f"{n}房（套房）"
    elif core == "K":
        out = f"{n}房 + 廚房"
    elif core == "DK":
        out = f"{n}房 + 餐廚"
    elif core == "LK":
        out = f"{n}房 + 客廚"
    else:
        out = f"{n}房 + 客餐廚"
    if has_s:
        out += " + 儲藏"
    return out


def _extract_layout_text(blob: str, *, blob_hw: str | None = None) -> str:
    b = unicodedata.normalize("NFKC", blob_hw if blob_hw is not None else _to_half_width_num(blob))
    labeled = re.search(
        r"(?:格局|間取り|間取)\s*[:：]?\s*([0-9]\s*S?(?:LDK|LK|DK|K|R)(?:\s*[～〜~\-－―]\s*[0-9]\s*S?(?:LDK|LK|DK|K|R))?)",
        b,
        flags=re.I,
    )
    if labeled:
        raw = re.sub(r"\s+", "", labeled.group(1)).upper()
        if re.search(r"[～〜~\-－―]", raw):
            return raw
        return _layout_jp_to_zh(raw)
    m_jp = None
    for mm in re.finditer(r"(?<![0-9A-Z])(\d+\s*S?(?:LDK|LK|DK|K|R))(?![0-9A-Z])", b, flags=re.I):
        tail = b[mm.end() : mm.end() + 4]
        if re.match(r"\s*(?:以下|以上)", tail):
            continue
        m_jp = mm
        break
    if m_jp:
        raw = re.sub(r"\s+", "", m_jp.group(1))
        return _layout_jp_to_zh(raw)
    m_zh = re.search(r"(\d+)\s*房(?:\s*(\d+)\s*廳)?(?:\s*(\d+)\s*衛)?", b)
    if m_zh:
        r = int(m_zh.group(1))
        h = int(m_zh.group(2) or 0)
        w = int(m_zh.group(3) or 0)
        out = f"{r}房"
        if h > 0:
            out += f" {h}廳"
        if w > 0:
            out += f" {w}衛"
        return out
    return ""


def _extract_room_count_from_layout_text(text: str) -> int | None:
    s = _to_half_width_num(str(text or "")).strip()
    if not s:
        return None
    if re.search(r"(?:套房|ワンルーム|\b1\s*R\b)", s, flags=re.I):
        return 0
    m_zh = re.search(r"(\d+)\s*房", s)
    if m_zh:
        try:
            return int(m_zh.group(1))
        except Exception:
            return None
    m_jp = re.search(r"\b(\d+)\s*S?(?:LDK|DK|K|R)\b", s, flags=re.I)
    if m_jp:
        try:
            return int(m_jp.group(1))
        except Exception:
            return None
    return None


def _extract_area_text(blob: str, *, blob_hw: str | None = None) -> tuple[float | None, float | None, str]:
    b = blob_hw if blob_hw is not None else _to_half_width_num(blob)
    tsubo = None
    sqm = None
    m_tsubo = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*坪", b, flags=re.I)
    if m_tsubo:
        tsubo = _num_from_text(m_tsubo.group(1))
        if tsubo and tsubo > 0:
            sqm = round(tsubo * 3.305785, 2)
    m_sqm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:㎡|m2|m²)", b, flags=re.I)
    if m_sqm:
        sqm = _num_from_text(m_sqm.group(1))
        if sqm and sqm > 0 and (tsubo is None):
            tsubo = round(sqm / 3.305785, 2)
    label = ""
    if tsubo is not None and sqm is not None:
        label = f"{_fmt_number_zh(tsubo)}坪（{_fmt_number_zh(sqm)}㎡）"
    elif tsubo is not None:
        label = f"{_fmt_number_zh(tsubo)}坪"
    elif sqm is not None:
        label = f"{_fmt_number_zh(sqm)}㎡"
    return tsubo, sqm, label


def _extract_age_text(blob: str, *, blob_hw: str | None = None) -> tuple[float | None, str]:
    b = blob_hw if blob_hw is not None else _to_half_width_num(blob)
    m = re.search(r"築年数\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*年", b, flags=re.I)
    if not m:
        m = re.search(r"(?:築|屋齡)\s*([0-9]+(?:\.[0-9]+)?)\s*年", b, flags=re.I)
    if not m:
        return None, ""
    y = _num_from_text(m.group(1))
    if y is None or y < 0:
        return None, ""
    return y, f"屋齡 {_fmt_number_zh(y, max_decimals=1)} 年"


def _extract_floor_text(blob: str, *, blob_hw: str | None = None) -> str:
    b = blob_hw if blob_hw is not None else _to_half_width_num(blob)
    m_sf = re.search(r"(?:所在階|地上階)\s*[:：]?\s*(?:地上)?\s*(\d{1,2})\s*階", b, flags=re.I)
    if m_sf:
        return f"{m_sf.group(1)}樓"
    m_y = re.search(r"(\d{1,2})\s*階\s*/\s*(?:地上階数)?(\d{1,3})\s*階", b, flags=re.I)
    if m_y:
        return f"{m_y.group(1)}/{m_y.group(2)}樓"
    m_y2 = re.search(r"所在階\s*[:：]?\s*(\d{1,2})\s*階", b, flags=re.I)
    if m_y2:
        return f"{m_y2.group(1)}樓"
    m = re.search(r"(\d{1,2})\s*階\s*/\s*(\d{1,2})\s*階建", b, flags=re.I)
    if m:
        return f"{m.group(1)}/{m.group(2)}樓"
    # 避免誤把「地上7階建」建物總層數當成住戶所在階；僅在無「所在階」線索時才用略式匹配
    if "所在階" not in b and "階部分" not in b:
        m2 = re.search(r"(\d{1,2})\s*階建", b, flags=re.I)
        if m2:
            return f"{m2.group(1)}樓建物"
    m3 = re.search(r"(\d{1,2})\s*樓", b, flags=re.I)
    if m3:
        return f"{m3.group(1)}樓"
    return ""


def _extract_building_type(blob: str) -> str:
    b = (blob or "").lower()
    pairs = [
        ("公寓大樓", ("マンション", "公寓", "大樓", "apartment")),
        ("透天/一戶建", ("一戶建", "戸建", "透天", "detached", "house")),
        ("土地", ("土地", "tochi")),
        ("車位", ("車位", "駐車", "parking")),
        ("店面", ("店面", "店舗", "shop")),
        ("辦公", ("辦公", "office")),
    ]
    for label, kws in pairs:
        if any(k.lower() in b for k in kws):
            return label
    return ""


_SMART_PROPERTY_TYPE_ORDER = (
    "公寓",
    "大樓",
    "華廈",
    "套房",
    "別墅/透天",
    "辦公",
    "倉庫",
    "店面",
    "廠房",
    "土地",
    "單售車位",
    "其他",
)
_SMART_PROPERTY_TYPE_SET = set(_SMART_PROPERTY_TYPE_ORDER)
_SMART_STUDIO_TYPE_TOKENS = (
    "套房",
    "ワンルーム",
    "studio",
    "1room",
    "1 room",
    "one room",
    "コンパクト",
    "単身",
    "單身",
    "单身",
    "小戶型",
    "小户型",
    "シングル",
)
_SMART_STUDIO_LAYOUT_RE = re.compile(
    r"(?<![0-9a-z])1\s*(?:s?ldk|ldk\+s|dk|k|r|room)(?![0-9a-z])",
    re.I,
)


def _normalize_smart_property_types(values: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        v = str(raw or "").strip()
        if v in ("別墅透天", "透天", "一戶建", "一戸建"):
            v = "別墅/透天"
        elif v in ("車位", "停車位", "單售停車位"):
            v = "單售車位"
        if v in _SMART_PROPERTY_TYPE_SET and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _smart_type_text_bag(item: dict[str, Any]) -> str:
    parts = [
        item.get("building_type_zh"),
        item.get("layout_text_hant"),
        item.get("layout_line_jp"),
        item.get("title_zh_hant"),
        item.get("title_zh_hans"),
        item.get("title_original"),
        item.get("snippet_jp"),
        item.get("body_zh_hant_preview"),
        item.get("body_zh_hans_preview"),
        item.get("topic_category"),
        item.get("item_url"),
    ]
    return unicodedata.normalize("NFKC", " ".join(str(x or "") for x in parts)).lower()


def _smart_type_focused_text_bag(item: dict[str, Any]) -> str:
    parts = [
        item.get("building_type_zh"),
        item.get("title_zh_hant"),
        item.get("title_zh_hans"),
        item.get("title_original"),
        item.get("topic_category"),
        item.get("item_url"),
    ]
    return unicodedata.normalize("NFKC", " ".join(str(x or "") for x in parts)).lower()


def _smart_type_layout_text_bag(item: dict[str, Any]) -> str:
    parts = [
        item.get("building_type_zh"),
        item.get("layout_text_hant"),
        item.get("layout_line_jp"),
        item.get("title_zh_hant"),
        item.get("title_zh_hans"),
        item.get("title_original"),
        item.get("topic_category"),
        item.get("item_url"),
    ]
    return unicodedata.normalize("NFKC", " ".join(str(x or "") for x in parts)).lower()


def _smart_text_has_studio_like(bag: str) -> bool:
    if any(t.lower() in bag for t in _SMART_STUDIO_TYPE_TOKENS):
        return True
    return bool(_SMART_STUDIO_LAYOUT_RE.search(bag))


def _smart_probe_has_studio_like_layout(probe: dict[str, Any]) -> bool:
    text = unicodedata.normalize(
        "NFKC",
        " ".join(
            str(probe.get(k) or "")
            for k in (
                "title_zh_hant",
                "title_zh_hans",
                "title_original",
                "snippet_jp",
                "body_zh_hant_preview",
                "body_zh_hans_preview",
            )
        ),
    ).lower()
    if any(t.lower() in text for t in ("套房", "ワンルーム", "studio", "1room", "1 room", "コンパクト", "単身", "單身", "单身")):
        return True
    layout_markers = ("格局", "間取り", "間取", "layout", "房型")
    for marker in layout_markers:
        start = 0
        while True:
            idx = text.find(marker.lower(), start)
            if idx < 0:
                break
            window = text[idx : idx + 90]
            if _SMART_STUDIO_LAYOUT_RE.search(window):
                return True
            start = idx + len(marker)
    return False


def _smart_property_type_hits(item: dict[str, Any]) -> set[str]:
    focused_bag = _smart_type_focused_text_bag(item)
    layout_bag = _smart_type_layout_text_bag(item)
    bag = _smart_type_text_bag(item)
    hits: set[str] = set()
    apartment_tokens = (
        "公寓",
        "大樓",
        "華廈",
        "マンション",
        "mansion",
        "apartment",
        "中古マンション",
        "新築マンション",
    )
    if any(t.lower() in focused_bag for t in apartment_tokens):
        hits.update({"公寓", "大樓", "華廈"})
    if _smart_text_has_studio_like(layout_bag):
        hits.add("套房")
    if any(
        t.lower() in focused_bag
        for t in ("別墅", "透天", "一戶建", "一戸建", "戸建", "detached", "villa", "中古一戸建て", "新築一戸建て")
    ):
        hits.add("別墅/透天")
    if any(t.lower() in focused_bag for t in ("辦公", "事務所", "オフィス", "office", "/office/")):
        hits.add("辦公")
    if any(t.lower() in focused_bag for t in ("倉庫", "warehouse", "souko", "/warehouse/")):
        hits.add("倉庫")
    if any(t.lower() in focused_bag for t in ("店面", "店舗", "テナント", "shop", "store", "/shop/", "/store/")):
        hits.add("店面")
    if any(t.lower() in focused_bag for t in ("廠房", "工場", "factory", "plant", "/factory/")):
        hits.add("廠房")
    if any(t.lower() in focused_bag for t in ("土地", "売地", "tochi", "/land/")):
        hits.add("土地")
    if any(t.lower() in focused_bag for t in ("單售車位", "車位", "駐車", "駐車場", "parking", "garage", "/parking/")):
        hits.add("單售車位")
    return hits


def _row_to_smart_type_probe(row: Any) -> dict[str, Any]:
    def g(key: str) -> Any:
        try:
            return row[key]
        except Exception:
            return ""

    return {
        "building_type_zh": " ".join(str(g(k) or "") for k in ("keyword_type", "case_transaction_override", "topic_category")),
        "layout_text_hant": " ".join(str(g(k) or "") for k in ("body_zh_hant", "body_zh_hans")),
        "layout_line_jp": g("body_original"),
        "title_zh_hant": g("title_zh_hant"),
        "title_zh_hans": g("title_zh_hans"),
        "title_original": g("title_original"),
        "snippet_jp": g("body_original"),
        "body_zh_hant_preview": g("body_zh_hant"),
        "body_zh_hans_preview": g("body_zh_hans"),
        "topic_category": g("topic_category"),
        "item_url": g("item_url"),
    }


def _smart_type_probe_is_listing_detail(row: Any, probe: dict[str, Any]) -> bool:
    try:
        if str(row["content_kind"] or "") != "jp_listing":
            return False
    except Exception:
        pass
    url = str(probe.get("item_url") or "").strip()
    title = " ".join(
        str(probe.get(k) or "").strip()
        for k in ("title_zh_hant", "title_zh_hans", "title_original")
        if str(probe.get(k) or "").strip()
    )
    if not url or not title:
        return False
    if re.search(r"(?:javascript\s*(?:is disabled|被禁用|已禁用)|需要\s*javascript|浏览器.{0,8}不支持)", title, re.I):
        return False
    return True


def _smart_property_type_candidate_sql(property_types: list[str]) -> tuple[str, list[Any]]:
    selected = _normalize_smart_property_types(property_types)
    if not selected or "其他" in selected:
        return "", []
    focused_fields = (
        "c.title_zh_hant",
        "c.title_zh_hans",
        "c.seo_title",
        "s.title_original",
        "s.item_url",
    )
    broad_fields = (*focused_fields, "c.body_zh_hant", "c.body_zh_hans", "s.body_original")
    terms_by_type: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "公寓": (("公寓", "マンション", "mansion", "apartment", "中古マンション", "新築マンション"), broad_fields),
        "大樓": (("大樓", "大楼", "マンション", "mansion", "中古マンション", "新築マンション"), broad_fields),
        "華廈": (("華廈", "华厦", "マンション", "mansion", "中古マンション", "新築マンション"), broad_fields),
        "套房": (
            ("套房", "ワンルーム", "1R", "1K", "1DK", "1LDK", "1ROOM", "STUDIO", "コンパクト", "単身", "單身", "单身"),
            broad_fields,
        ),
        "別墅/透天": (("別墅", "透天", "一戶建", "一戸建", "戸建", "中古一戸建て", "新築一戸建て", "detached", "villa"), focused_fields),
        "辦公": (("辦公", "办公", "事務所", "オフィス", "office"), focused_fields),
        "倉庫": (("倉庫", "warehouse", "souko"), focused_fields),
        "店面": (("店面", "店舗", "テナント", "shop", "store"), focused_fields),
        "廠房": (("廠房", "工場", "factory", "plant"), focused_fields),
        "土地": (("土地", "売地", "tochi", "/land/"), focused_fields),
        "單售車位": (("單售車位", "車位", "駐車場", "parking", "garage"), focused_fields),
    }
    clauses: list[str] = []
    params: list[Any] = []
    for ptype in selected:
        spec = terms_by_type.get(ptype)
        if not spec:
            continue
        terms, fields = spec
        for term in terms:
            like = f"%{term}%"
            clauses.append("(" + " OR ".join(f"COALESCE({field}, '') LIKE ?" for field in fields) + ")")
            params.extend([like] * len(fields))
    if not clauses:
        return "", []
    return " AND (" + " OR ".join(clauses) + ")", params


def _smart_item_matches_property_types(item: dict[str, Any], property_types: list[str]) -> bool:
    selected = _normalize_smart_property_types(property_types)
    if not selected:
        return True
    hits = _smart_property_type_hits(item)
    if "其他" in selected and not hits:
        return True
    return any(t != "其他" and t in hits for t in selected)


def _smart_item_price_man(item: dict[str, Any]) -> float | None:
    raw = item.get("price_man")
    try:
        val = float(raw)
        if val > 0:
            return val
    except Exception:
        pass
    blob = " ".join(
        str(x or "")
        for x in [
            item.get("price_text_hant"),
            item.get("title_original"),
            item.get("title_zh_hant"),
            item.get("snippet_jp"),
            item.get("body_zh_hant_preview"),
            item.get("body_zh_hans_preview"),
        ]
    )
    parsed = _parse_man_from_price_text(blob)
    if parsed is not None and parsed > 0:
        return float(parsed)
    return None


def _apply_smart_structured_filters(
    items: list[dict[str, Any]],
    *,
    property_types: list[str],
    price_min_man: int,
    price_max_man: int,
    layout_min_rooms: int = 0,
    layout_max_rooms: int = 0,
    layout_exact_zero: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    types = _normalize_smart_property_types(property_types)
    pmin = max(0, int(price_min_man or 0))
    pmax = max(0, int(price_max_man or 0))
    if pmin > 0 and pmax > 0 and pmax < pmin:
        pmin, pmax = pmax, pmin
    rmin = max(0, int(layout_min_rooms or 0))
    rmax = max(0, int(layout_max_rooms or 0))
    if rmin > 0 and rmax > 0 and rmax < rmin:
        rmin, rmax = rmax, rmin
    price_filter_requested = pmin > 0 or pmax > 0
    # 智慧查询价格只作为展示条件记录，不参与结果排除：
    # 用户希望无论高价、低价、无价格都可展示，避免价格缺失造成有效案件消失。
    has_price = False
    exact_zero = bool(layout_exact_zero)
    has_layout = exact_zero or rmin > 0 or rmax > 0
    if not types and not price_filter_requested and not has_layout:
        return items, {
            "property_types": [],
            "price_min_man": 0,
            "price_max_man": 0,
            "layout_min_rooms": 0,
            "layout_max_rooms": 0,
            "layout_exact_zero": False,
            "before_count": len(items),
            "after_count": len(items),
            "price_unknown_excluded": 0,
            "price_unknown_included": 0,
            "price_filter_ignored": False,
            "price_filter_requested": False,
            "layout_unknown_excluded": 0,
        }

    out: list[dict[str, Any]] = []
    price_unknown_excluded = 0
    price_unknown_included = 0
    layout_unknown_excluded = 0
    for it in items:
        if types and not _smart_item_matches_property_types(it, types):
            continue
        if has_price:
            price = _smart_item_price_man(it)
            if price is None or price <= 0:
                price_unknown_included += 1
            else:
                if pmin > 0 and price < pmin:
                    continue
                if pmax > 0 and price > pmax:
                    continue
        if has_layout:
            rooms = _extract_room_count_from_layout_text(
                " ".join(
                    str(x or "")
                    for x in (
                        it.get("layout_text_hant"),
                        it.get("layout_line_jp"),
                        it.get("title_zh_hant"),
                        it.get("title_original"),
                        it.get("snippet_jp"),
                        it.get("body_zh_hant_preview"),
                        it.get("body_zh_hans_preview"),
                    )
                    if str(x or "").strip()
                )
            )
            if rooms is None:
                layout_unknown_excluded += 1
                continue
            if exact_zero and rooms != 0:
                continue
            if rmin > 0 and rooms < rmin:
                continue
            if rmax > 0 and rooms > rmax:
                continue
        out.append(it)
    return out, {
        "property_types": types,
        "price_min_man": pmin,
        "price_max_man": pmax,
        "layout_min_rooms": rmin,
        "layout_max_rooms": rmax,
        "layout_exact_zero": exact_zero,
        "before_count": len(items),
        "after_count": len(out),
        "price_unknown_excluded": price_unknown_excluded,
        "price_unknown_included": price_unknown_included,
        "price_filter_ignored": bool(price_filter_requested),
        "price_filter_requested": bool(price_filter_requested),
        "layout_unknown_excluded": layout_unknown_excluded,
    }


def _building_name_from_title(title_original: str) -> str:
    t = str(title_original or "").strip()
    t = re.sub(r"^\s*【[^】]{1,32}】\s*", "", t)
    t = re.sub(r"\s+[0-9０-９,，]{1,12}\s*万円.*$", "", t, flags=re.I)
    t = re.sub(r"\s*（[^）]{1,40}）\s*$", "", t)
    out = t.strip()[:120]
    if _is_likely_non_property_building_name(out):
        return ""
    return out


def _building_from_title_price_line(title_original: str) -> str:
    """SUUMO 物件名常見於「◯◯マンション 3990万円」標題前段。"""
    t = _to_half_width_num(str(title_original or "").strip())
    t = re.sub(r"^\s*(\[[^\]]{1,40}\]|【[^】]{1,40}】)\s*", "", t)
    m = re.match(r"^(.{2,48}?)\s+\d[\d,]{0,12}\s*万", t)
    if not m:
        return ""
    cand = m.group(1).strip()[:120]
    if _is_likely_non_property_building_name(cand):
        return ""
    return cand


def _is_likely_non_property_building_name(s: str) -> bool:
    """排除導覽／城市搜尋等非單一物件名（避免塞滿規格表「物件名」欄）。"""
    t = str(s or "").strip()
    if len(t) > 56:
        return True
    if re.search(r"(?:購入情報|エリア情報|の中古マンション|中古マンション購入|二手房|購買信息|購屋信息)", t):
        return True
    if "駅" in t and ("の中古" in t or "購入" in t or "周辺" in t):
        return True
    if "【SUUMO" in t or "スーモ)" in t or "SUUMO(" in t:
        return True
    noise = (
        "網站",
        "网站",
        "服務",
        "服务",
        "搜索",
        "搜尋",
        "回答問題",
        "即可找到",
        "小鎮",
        "小鎮！",
        "我想居住",
        "渡環",
        "在城市",
        "全新的城市",
        "僅限東京都",
        "僅限",
        "為您",
        "おすすめ",
        "一覧",
        "検索結果",
        "information",
        "service",
    )
    tl = t.lower()
    return any(k in t for k in noise) or any(k in tl for k in ("http://", "https://", "www."))


def _is_seo_mansion_address_line(s: str) -> bool:
    """SUUMO 區域／車站導覽頁常見長句 SEO，非門牌所在地。"""
    t = str(s or "").strip()
    if len(t) < 18:
        return False
    if "の中古マンション購入情報" in t or "中古マンション】" in t or "【SUUMO" in t:
        return True
    if re.match(r"^東京都の中古マンション", t):
        return True
    if "周辺で" in t and "購入情報" in t:
        return True
    return False


def _extract_jp_address_fallback(blob: str) -> str:
    """正文無標準『所在地：』時，自都道府縣＋市區町村段落猜出門牌級片段。"""
    b = _to_half_width_num(str(blob or ""))
    m = re.search(
        r"((?:東京都|北海道|(?:京都|大阪)府|.{2,3}県)[^\s。\n\r]{1,56}?[区市町村島][^\s。\n\r]{1,48}?)",
        b,
    )
    if m:
        cand = re.sub(r"\s+", " ", m.group(1)).strip()
        if len(cand) >= 6 and not _is_seo_mansion_address_line(cand):
            return cand[:120]
    return ""


def _extract_jp_access_fallback(blob: str) -> str:
    """自日文『◯◯線「駅」徒歩◯分』句式抽交通（避開本站免責文案）。"""
    b = _to_half_width_num(str(blob or ""))
    mx = re.search(
        r"([^\s。\n\r]{1,40}?(?:JR|ＪＲ|西武|東急|小田急|京王|都営|東京メトロ|京急|相鉄|阪急|名鉄)?[^\s。\n\r]{0,8}?線[^\s。\n\r]{0,36}?「[^」]{1,18}」[^\s。\n\r]{0,22}?(?:徒歩|歩)\s*\d{1,3}\s*分)",
        b,
    )
    if mx:
        s = re.sub(r"\s+", " ", mx.group(1)).strip()
        if s and not _looks_like_site_disclaimer_transit(s):
            return s[:200]
    return ""


def _hint_access_line_from_titles(row: dict[str, Any]) -> str:
    """自門戶中文標題還原「站＋城市」提示（非正式沿線欄位，優於整句 SEO）。"""
    for key in ("title_zh_hant", "title_zh_hans", "title_original"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        m = re.search(r"\]\s*(.+?)\s*的二手", raw)
        if m:
            s = re.sub(r"\s+", " ", m.group(1)).strip()
            if 4 <= len(s) <= 72:
                return s[:200]
        m2 = re.search(r"(.+?駅(?:\([^)]{1,24}\))?)の中古マンション", raw)
        if m2:
            s = re.sub(r"\s+", " ", m2.group(1)).strip()
            if 4 <= len(s) <= 72:
                return s[:200]
    return ""


def _access_line_is_portal_headline_noise(access: str, row: dict[str, Any]) -> bool:
    """沿線欄若等於整句中文標題或含「購買信息」等，視為導覽噪音。"""
    a = str(access or "").strip()
    if not a:
        return False
    if "二手" in a and ("信息" in a or "資訊" in a):
        return True
    for key in ("title_zh_hant", "title_zh_hans"):
        t = str(row.get(key) or "").strip()
        if t and a == t:
            return True
    return False


def _suumo_digest_kv_section(blob: str) -> str:
    """僅解析抓取流程寫入的 [SUUMO 詳細欄位] 區塊，避免表單欄位／短鍵污染。"""
    s = str(blob or "")
    i = s.find("[SUUMO 詳細欄位]")
    if i < 0:
        return s
    rest = s[i:]
    ends = ("\n[物件參考圖像 URL]", "\n[物件参考圖像", "\n[物件欄位摘要]")
    cut = len(rest)
    for em in ends:
        j = rest.find(em, 1)
        if j > 10:
            cut = min(cut, j)
    return rest[:cut]


def _extract_suumo_style_detail(blob: str, *, blob_hw: str | None = None) -> dict[str, str]:
    """從日文／摘要 blob 擷取 SUUMO 物件表常見欄位（啟發式）。"""
    b = blob_hw if blob_hw is not None else _to_half_width_num(str(blob or ""))
    b1 = re.sub(r"\s+", " ", b).strip()
    kv: dict[str, str] = {}
    for ln in _suumo_digest_kv_section(blob).splitlines():
        t = ln.strip()
        m = re.match(r"^[-－*•・]\s*([^:：]{1,120})\s*[:：]\s*(.+)$", t)
        if not m:
            continue
        raw_k = str(m.group(1) or "").strip()
        k = re.sub(r"(?:\s*ヒント)\s*$", "", raw_k).strip()
        k = re.sub(r"\s*[（(]\s*(?:予定|予告|税込|税別)\s*[）)]\s*$", "", k).strip()
        if not k:
            continue
        val = str(m.group(2) or "").strip()
        for noise in ("[ SUUMO", "[■", "[ □"):
            ji = val.find(noise)
            if ji > 8:
                val = val[:ji].strip()
        val = val[:920]
        prev = kv.get(k)
        if prev:
            prev_has_range = bool(re.search(r"[～〜~\-－―]", prev))
            val_has_range = bool(re.search(r"[～〜~\-－―]", val))
            if k in {"価格", "専有面積", "間取り", "間取"}:
                if prev_has_range and not val_has_range and "未定" not in prev:
                    continue
                if "未定" in prev and re.search(r"\d", val) and "未定" not in val:
                    kv[k] = val
                elif val_has_range or "未定" not in val:
                    kv[k] = val
                continue
        kv[k] = val

    def _one(pat: str) -> str:
        m = re.search(pat, b1, flags=re.I)
        return (m.group(1) or "").strip()[:200] if m else ""

    building = kv.get("物件名") or ""
    if building and _is_likely_non_property_building_name(building):
        building = ""
    if not building:
        building = _one(r"物件名\s*[:：]?\s*(.+?)(?=\s*(?:販売価格|価格|所在地|沿線))")
    if building and _is_likely_non_property_building_name(building):
        building = ""
    if not building:
        building = _one(r"建物名\s*[:：]?\s*(.+?)(?=\s*(?:販売|価格|所在地|沿線))")
    if building and _is_likely_non_property_building_name(building):
        building = ""
    addr = kv.get("住所") or kv.get("所在地") or _one(
        r"所在地\s*[:：]?\s*(.+?)(?=\s*(?:沿線|販売価格|価格|交通|専有面積|間取))"
    )
    if not addr:
        addr = _one(
            r"住所\s*[:：]?\s*(.+?)(?=\s*(?:交通|沿線|販売価格|価格|専有面積|間取|完成時期|$))"
        )
    if not addr:
        maddr = re.search(r"(東京都[^\n\r]{2,58}?)(?=\s*(?:\n|沿線|販売|専有|間取|バルコニー|築年|情報提供))", b1)
        if maddr:
            addr = maddr.group(1).strip()[:200]
    access = (
        kv.get("交通")
        or kv.get("沿線駅")
        or kv.get("沿線・駅")
        or ""
    )
    handover_from_access = ""
    if access and ("引渡" in access or "引き渡し" in access or "入居時期" in access):
        mh_acc = re.search(
            r"(?:引渡可能時期|引き渡し可能時期|引渡時期|入居時期)\s*[:：]?\s*"
            r"(.{1,90}?)(?=\s*(?:価格|専有面積|間取り|販売|所在地|交通|総戸数|完成時期|$))",
            access,
        )
        if mh_acc:
            handover_from_access = (mh_acc.group(1) or "").strip()
        access = re.split(
            r"\s*(?:引渡可能時期|引き渡し可能時期|引渡時期|入居時期)\s*",
            access,
            maxsplit=1,
        )[0].strip()
    if not access:
        access = _one(r"沿線・駅\s*[:：]?\s*(.+?)(?=\s*(?:専有面積|建物面積|間取り|バルコニー|築年月|掲載))")
    if not access:
        access = _one(r"沿線\s*・\s*駅\s*[:：]?\s*(.+?)(?=\s*(?:専有面積|間取り|バルコニー|築年月))")
    if not access:
        mx = re.search(
            r"((?:JR|ＪＲ|地下鉄|東急|西武|小田急|京王|都営|東京メトロ|つくばエクスプレス|京急|相鉄|阪急|名鉄|鹿児島本線|日豊本線)[^\n]{0,36}?"
            r"線[^\n]{0,14}?「[^」]{1,14}」[^\n]*?(?:徒歩|歩|バス)\s*\d{1,3}\s*分)",
            b1,
        )
        if mx:
            access = mx.group(1).strip()[:200]
    if not access:
        access = _one(r"交通\s*[:：]?\s*(.+?)(?=\s*(?:土地面積|建物面積|所在地|間取り|築年月|価格|専有面積|$))")
    other_area_line = (kv.get("その他面積") or "").strip()
    balcony = kv.get("陽台") or _one(r"バルコニー(?:面積)?\s*[:：]?\s*([0-9.～\-]+\s*m2?|[0-9.]+\s*㎡)")
    if (not balcony) and other_area_line and "バルコニー" in other_area_line:
        mb = re.search(r"バルコニー[^：:0-9]{0,6}[:：]?\s*([0-9.]+)\s*m", other_area_line)
        if mb:
            balcony = f"{mb.group(1).strip()}㎡"
    area_line = (
        kv.get("専有面積")
        or _one(r"専有面積\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?\s*(?:m2|m²|㎡)[^|。\n\r]{0,48})")
    )
    if area_line:
        area_line = re.sub(r"m\s+2\b", "㎡", str(area_line), flags=re.I).replace("m2", "㎡")
    layout_line = kv.get("間取り") or kv.get("間取") or _one(r"(?:間取り|間取)\s*[:：]?\s*([0-9]+\s*S?LDK|[0-9]+\s*DK|[0-9]+\s*K|[0-9]+\s*R)")
    floor_structure_line = (
        kv.get("所在階/構造・階建")
        or kv.get("構造・階建て")
        or kv.get("構造・階建")
        or ""
    ).strip()
    floor_line = (
        floor_structure_line
        or kv.get("所在階")
        or _one(r"所在階(?:/構造・階建)?\s*[:：]?\s*([0-9]{1,2}\s*階[^|。\n\r]{0,48})")
    )
    built = (
        kv.get("完成時期（築年月）")
        or kv.get("完成時期(築年月)")
        or kv.get("完成時期")
        or kv.get("築年月")
        or kv.get("建築年月")
        or kv.get("竣工年月")
        or kv.get("竣工時期")
        or kv.get("完成予定")
        or kv.get("入居時期")
        or _one(r"築年月\s*[:：]?\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月|[0-9]{4}/[0-9]{1,2})")
    )
    if not built:
        mb2 = re.search(r"(?:築年月|築)\s*[:：]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月", b1)
        if mb2:
            built = f"{mb2.group(1)}年{mb2.group(2)}月"
    if not built:
        mb3 = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*築", b1)
        if mb3:
            built = f"{mb3.group(1)}年{mb3.group(2)}月"
    if not built:
        mb4 = re.search(
            r"(?:完成時期|建築年月|竣工年月)\s*[:：]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月",
            b1,
            flags=re.I,
        )
        if mb4:
            built = f"{mb4.group(1)}年{mb4.group(2)}月"
    if not built:
        mb5 = re.search(r"(?:完成時期|築年月)\s*[:：]?\s*(\d{4})\s*[./／]\s*(\d{1,2})\b", b1, flags=re.I)
        if mb5:
            built = f"{mb5.group(1)}年{mb5.group(2)}月"
    manage_fee = (
        kv.get("管理費")
        or kv.get("管理費等")
        or _one(r"(?:管理費|管理費等)\s*[:：]?\s*([0-9,]+\s*円(?:\s*/\s*月|/月|月額)?)")
        or _one(r"(?:管理費|管理費等)\s*[:：]?\s*([0-9,]+\s*元(?:/月|月))")
    )
    reserve_fee = (
        kv.get("修繕積立金")
        or _one(r"修繕積立金\s*[:：]?\s*([0-9,]+\s*円(?:\s*/\s*月|/月|月額)?)")
        or _one(r"修繕積立金\s*[:：]?\s*([0-9,]+\s*元(?:/月|月))")
    )
    total_units = kv.get("総戸数") or _one(r"総戸数\s*[:：]?\s*([0-9０-９]{1,6}\s*戸?)")
    structure = (
        kv.get("建物構造")
        or kv.get("構造・階建て")
        or kv.get("構造・階建")
        or kv.get("構造・工法")
        or _one(r"(?:建物構造|構造・工法|構造・階建て|構造・階建|構造)\s*[:：]?\s*(.+?)(?=\s*(?:土地権利|管理形態|管理費|駐車場|施設|用途地域|$))")
    )
    parking = (
        kv.get("駐車場")
        or kv.get("駐車場・車庫")
        or _one(r"(?:駐車場|駐車場・車庫)\s*[:：]?\s*(.+?)(?=\s*(?:駐輪場|バイク置場|土地権利|管理|$))")
    )
    if not parking:
        facilities = str(kv.get("施設") or "")
        mp = re.search(r"(駐車場[／/].{1,160}?)(?=\s*(?:駐輪場|バイク|ミニバイク|$))", facilities)
        if mp:
            parking = mp.group(1).strip()
        else:
            mp = re.search(r"駐車場\s*([／/].{1,160}?)(?=\s*(?:駐輪場|バイク|ミニバイク|$))", facilities)
            if mp:
                parking = ("駐車場" + mp.group(1)).strip()
    if str(parking or "").strip().startswith(("／", "/")):
        parking = "駐車場" + str(parking or "").strip()
    status = kv.get("現況") or kv.get("建物現況") or _one(r"(?:現況|建物現況)\s*[:：]?\s*([^\s]{1,24})")
    if not status:
        sale_info = str(kv.get("販売情報") or kv.get("販売スケジュール") or "").strip()
        if sale_info:
            status = re.split(r"\s*(?:※|●|。)", sale_info, maxsplit=1)[0].strip()
    handover = (
        kv.get("引渡可能時期")
        or kv.get("引き渡し可能時期")
        or kv.get("引渡時期")
        or kv.get("入居時期")
        or handover_from_access
        or _one(r"(?:引渡可能時期|引き渡し可能時期|引渡時期|入居時期)\s*[:：]?\s*(.{1,70}?)(?=\s*(?:価格|専有面積|間取り|販売|所在地|交通|総戸数|完成時期|$))")
    )
    property_no = (
        kv.get("物件番号")
        or kv.get("物件管理番号")
        or kv.get("掲載会社 管理番号")
        or _one(r"(?:物件番号|物件管理番号|掲載会社\s*管理番号)\s*[:：]?\s*([A-Za-z0-9\-]{4,50})")
    )
    info_open = (
        kv.get("情報提供日")
        or kv.get("情報公開日")
        or kv.get("情報掲載開始日")
        or _one(
            r"(?:情報提供日|情報公開日|情報掲載開始日)\s*[:：]?\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2})"
        )
    )
    next_update = (
        kv.get("次回更新予定日")
        or kv.get("次回更新予定")
        or _one(
            r"(?:次回更新予定日|次回更新予定)\s*[:：]?\s*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2})"
        )
    )
    sales_units = (kv.get("販売戸数") or "").strip()
    rel = (kv.get("関連リンク") or "").strip()[:520]
    cg = (kv.get("不動産会社ガイド") or "").strip()[:320]
    stf = (kv.get("担当者より") or "").strip()[:360]
    inq = (kv.get("お問い合せ先") or "").strip()[:360]
    return {
        "building_name_jp": _clean_listing_building_name(building),
        "address_line_jp": _clean_listing_address_line(addr),
        "access_line_jp": _clean_listing_access_line(access),
        "layout_line_jp": layout_line,
        "area_line_jp": area_line,
        "floor_line_jp": floor_line,
        "floor_structure_line_jp": floor_structure_line,
        "balcony_line_jp": balcony,
        "other_area_line_jp": other_area_line,
        "sales_units_line_jp": sales_units,
        "built_ym_jp": _clean_listing_built_value(built),
        "manage_fee_jp": _clean_listing_fee_value(manage_fee),
        "reserve_fee_jp": _clean_listing_fee_value(reserve_fee),
        "total_units_jp": total_units,
        "structure_jp": _clean_listing_structure_value(structure),
        "parking_jp": _clean_listing_parking_value(parking),
        "status_jp": _compact_listing_value(status, max_len=80),
        "handover_jp": _clean_listing_handover_value(handover),
        "property_no_jp": property_no,
        "info_open_jp": info_open,
        "next_update_jp": next_update,
        "related_links_jp": rel,
        "company_guide_jp": cg,
        "staff_message_jp": stf,
        "inquiry_contact_jp": inq,
    }


def _homes_site_trail_jp_from_blob(blob: str) -> str:
    """自抓取本文還原 LIFULL HOME'S 本站階層（對齊 HOMES 麵包屑編排）。"""
    b = str(blob or "")
    i = b.find("[HOMES 階層導覽]")
    if i >= 0:
        rest = b[i + len("[HOMES 階層導覽]") :].lstrip("\n\r")
        line = rest.split("\n")[0].strip()
        if line:
            return line[:960]
    m = re.search(r"HOMESサイト階層(?:（パンくず）)?[:：]\s*([^\n\r]+)", b)
    if m:
        t = (m.group(1) or "").strip()
        if t:
            return t[:960]
    return ""


def _extract_feature_tags(blob: str) -> list[str]:
    b = (blob or "").lower()
    tag_rules: list[tuple[str, tuple[str, ...]]] = [
        ("近車站", ("徒歩", "步行", "駅", "車站", "站")),
        ("有電梯", ("エレベーター", "電梯")),
        ("可停車", ("駐車", "車位", "停车", "停車")),
        ("近超市", ("スーパー", "超市", "便利店", "便利商店")),
        ("有陽台", ("バルコニー", "陽台")),
        ("可養寵", ("ペット", "寵物", "宠物")),
        ("新成屋", ("新築", "新建")),
        ("中古屋", ("中古", "二手")),
        ("投資向", ("利回り", "投資", "收益")),
    ]
    out: list[str] = []
    for label, kws in tag_rules:
        if any(k.lower() in b for k in kws):
            out.append(label)
        if len(out) >= 6:
            break
    return out


def _homes_clean_value(value: str, *, max_len: int = 220) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" ：:-")
    value = value.replace(" 地図を見る", "").replace("地図を見る", "").strip()
    return value[:max_len].strip()


def _homes_station_segments(value: str, *, max_items: int = 3) -> list[str]:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    if not s:
        return []
    segment_re = re.compile(
        r"((?:(?:JR|ＪＲ)?[\u3040-\u9FFFァ-ヴA-Za-z0-9・]+線)\s+"
        r"[\u3040-\u9FFFァ-ヴA-Za-z0-9・]+駅\s*(?:徒歩|歩|バス)\s*\d{1,3}\s*分)",
        flags=re.I,
    )
    seen: set[str] = set()
    out: list[str] = []
    for mseg in segment_re.finditer(s):
        seg = re.sub(r"\s+", " ", (mseg.group(1) or "")).strip()
        if seg and seg not in seen:
            seen.add(seg)
            out.append(seg)
        if len(out) >= max_items:
            break
    return out


def _extract_listing_fields(row: dict[str, Any], *, meta: dict[str, Any]) -> dict[str, Any]:
    body_zh_hant = str(row.get("body_zh_hant") or "")
    body_zh_hans = str(row.get("body_zh_hans") or "")
    if _listing_text_is_generated_cache(body_zh_hant):
        body_zh_hant = ""
    if _listing_text_is_generated_cache(body_zh_hans):
        body_zh_hans = ""
    blob = "\n".join(
        [
            str(row.get("title_zh_hant") or ""),
            str(row.get("title_zh_hans") or ""),
            str(row.get("title_original") or ""),
            body_zh_hant,
            body_zh_hans,
            str(row.get("body_original") or ""),
        ]
    )
    item_url = str(row.get("item_url") or "")
    blob_hw = _to_half_width_num(blob)
    price_man, price_text = _extract_listing_price_text(blob, item_url=item_url, blob_hw=blob_hw)
    price_fx_hant = _listing_price_fx_hant(price_man, price_text)
    layout_text = _extract_layout_text(blob, blob_hw=blob_hw)
    area_tsubo, area_sqm, area_text = _extract_area_text(blob, blob_hw=blob_hw)
    age_years, age_text = _extract_age_text(blob, blob_hw=blob_hw)
    floor_text = _extract_floor_text(blob, blob_hw=blob_hw)
    building_type = _extract_building_type(blob)
    tags = _extract_feature_tags(blob)
    region = str(meta.get("jp_region_display_zh") or "")
    transit = str(meta.get("transit_line_zh") or "")
    suumo = _extract_suumo_style_detail(blob, blob_hw=blob_hw)
    layout_line_jp = str(suumo.get("layout_line_jp") or "").strip()
    if layout_line_jp:
        layout_text = _layout_jp_to_zh(re.sub(r"\s+", "", layout_line_jp))
    area_line_jp = str(suumo.get("area_line_jp") or "").strip()
    if area_line_jp and (not area_text):
        _ts2, _sqm2, _area2 = _extract_area_text(area_line_jp)
        if _area2:
            area_tsubo, area_sqm, area_text = _ts2, _sqm2, _area2
    floor_line_jp = str(suumo.get("floor_line_jp") or "").strip()
    if floor_line_jp and (not floor_text):
        mfl = re.search(r"(\d{1,2})\s*階(?:\s*/\s*[^0-9]{0,8}(\d{1,3})\s*階)", floor_line_jp)
        if mfl:
            floor_text = f"{mfl.group(1)}/{mfl.group(2)}樓"
        else:
            mfl2 = re.search(r"(\d{1,2})\s*階", floor_line_jp)
            if mfl2:
                floor_text = f"{mfl2.group(1)}樓"
    bname = _clean_listing_building_name(suumo.get("building_name_jp") or "")
    if bname and _is_likely_non_property_building_name(bname):
        bname = ""
    if not bname:
        bname = _clean_listing_building_name(_building_from_title_price_line(str(row.get("title_original") or "")))
    if not bname:
        bname = _clean_listing_building_name(_building_name_from_title(str(row.get("title_original") or "")))
    homes_site_trail_jp = ""
    if "homes.co.jp" in (item_url or "").lower():
        homes_site_trail_jp = _homes_site_trail_jp_from_blob(blob)
    if not bname and homes_site_trail_jp:
        segs = [x.strip() for x in homes_site_trail_jp.replace("／", ">").split(">") if x.strip()]
        if segs:
            tail = segs[-1].strip()[:140]
            if tail and not tail.endswith("駅") and not _is_likely_non_property_building_name(tail):
                stripped = re.sub(r"\s+\d{1,2}階\s*$", "", tail).strip()
                if stripped and len(stripped) >= 2:
                    bname = _clean_listing_building_name(stripped)
    access_jp = _clean_listing_access_line(suumo.get("access_line_jp") or "")
    if _looks_like_site_disclaimer_transit(access_jp):
        access_jp = ""
    if not access_jp and transit and not _looks_like_site_disclaimer_transit(transit):
        access_jp = transit[:160]
    addr_jp = _clean_listing_address_line(suumo.get("address_line_jp") or "")
    if _is_seo_mansion_address_line(addr_jp):
        addr_jp = ""
    if not addr_jp:
        addr_jp = _clean_listing_address_line(_extract_jp_address_fallback(blob).strip())
    if _is_likely_non_property_building_name(bname):
        bname = ""
    if _access_line_is_portal_headline_noise(access_jp, row):
        access_jp = ""
    if _looks_like_site_disclaimer_transit(access_jp):
        access_jp = ""
    if not access_jp:
        access_jp = _clean_listing_access_line(_extract_jp_access_fallback(blob))
    if not access_jp:
        access_jp = _hint_access_line_from_titles(row) or access_jp
    if "suumo.jp" in (item_url or "").lower():
        for _tok in (" [ ■", " [ □"):
            if addr_jp and _tok in addr_jp:
                addr_jp = addr_jp.split(_tok, 1)[0].strip()
    layout_line_jp_out = str(suumo.get("layout_line_jp") or "").strip()
    exclusive_area_jp = str(suumo.get("area_line_jp") or "").strip()
    built_ym_eff = _clean_listing_built_value(suumo.get("built_ym_jp") or "")
    if built_ym_eff:
        age_years = None
        age_text_hant = ""
    floor_structure_jp = str(suumo.get("floor_structure_line_jp") or "").strip()
    if not floor_structure_jp:
        floor_structure_jp = str(suumo.get("floor_line_jp") or "").strip()
    sales_units_jp = str(suumo.get("sales_units_line_jp") or "").strip()
    other_area_jp = str(suumo.get("other_area_line_jp") or "").strip()
    if other_area_jp:
        other_area_jp = re.sub(r"m\s+2\b", "㎡", other_area_jp, flags=re.I)
    related_links_jp = str(suumo.get("related_links_jp") or "").strip()
    company_guide_jp = str(suumo.get("company_guide_jp") or "").strip()
    staff_message_jp = str(suumo.get("staff_message_jp") or "").strip()
    inquiry_contact_jp = str(suumo.get("inquiry_contact_jp") or "").strip()
    total_units_jp = str(suumo.get("total_units_jp") or "").strip()
    structure_jp = _clean_listing_structure_value(suumo.get("structure_jp") or "")
    parking_jp = _clean_listing_parking_value(suumo.get("parking_jp") or "")
    status_jp = str(suumo.get("status_jp") or "").strip()
    handover_jp = str(suumo.get("handover_jp") or "").strip()
    if "homes.co.jp" in (item_url or "").lower() and "/mansion/b-" in (item_url or "").lower():
        homes_source_blob = "\n".join(
            [
                str(row.get("title_original") or ""),
                str(row.get("body_original") or ""),
            ]
        )
        compact_blob = re.sub(r"\s+", " ", _to_half_width_num(homes_source_blob) or blob_hw)
        homes_detail_blob = compact_blob
        m_homes_title = re.search(r"【ホームズ】\s*([^｜|]+)", str(row.get("title_original") or ""))
        if m_homes_title:
            bname = _clean_listing_building_name(m_homes_title.group(1).strip())
        if bname and bname in homes_detail_blob:
            homes_detail_blob = homes_detail_blob[homes_detail_blob.find(bname) :]
        homes_start_candidates = [
            homes_detail_blob.find(token)
            for token in ("物件の特徴 販売概要", "販売概要 ポイント", "新築マンション 新規分譲")
            if homes_detail_blob.find(token) >= 0
        ]
        if homes_start_candidates:
            homes_detail_blob = homes_detail_blob[min(homes_start_candidates) :]
        homes_cut_candidates = [
            homes_detail_blob.find(token)
            for token in ("お問合せ", "モデルルーム", "登録する お気に入り")
            if homes_detail_blob.find(token) > 0
        ]
        if homes_cut_candidates:
            homes_detail_blob = homes_detail_blob[: min(homes_cut_candidates)]

        m_addr_access = re.search(
            r"所在地\s*(.{4,140}?)\s*(?:地図を見る\s*)?交通\s*(.{6,320}?)(?=\s*引き渡し)",
            homes_detail_blob,
        )
        if m_addr_access:
            addr_jp = _homes_clean_value(m_addr_access.group(1), max_len=140)
            access_jp = _homes_clean_value(m_addr_access.group(2), max_len=280)
        elif "東京都" in homes_detail_blob:
            m_addr = re.search(r"(東京都.{4,80}?)(?:\s*地図を見る|\s*交通)", homes_detail_blob)
            if m_addr:
                addr_jp = _homes_clean_value(m_addr.group(1), max_len=140)
        m_price_range = re.search(
            r"([0-9][0-9,]*)\s*万円\s*[～〜~\-－―]\s*([0-9][0-9,]*)\s*万円",
            homes_detail_blob,
        )
        if m_price_range:
            low_price = m_price_range.group(1)
            high_price = m_price_range.group(2)
            price_text = f"{low_price}萬～{high_price}萬日圓"
            try:
                price_man = float(low_price.replace(",", ""))
            except Exception:
                pass
            price_fx_hant = _listing_price_fx_hant(price_man, price_text)
        m_layout_range = re.search(r"間取り\s*([^。]{1,90}?)(?=\s*専有面積)", homes_detail_blob)
        if m_layout_range:
            layout_line_jp_out = re.sub(r"\s+", " ", m_layout_range.group(1)).strip(" ：:-")
            if layout_line_jp_out:
                layout_text = layout_line_jp_out
        m_area_range = re.search(
            r"専有面積\s*([0-9.]+\s*(?:㎡|m2|m²)\s*[～〜~\-－―]\s*[0-9.]+\s*(?:㎡|m2|m²))",
            homes_detail_blob,
            flags=re.I,
        )
        if m_area_range:
            exclusive_area_jp = m_area_range.group(1).replace("m2", "㎡").replace("m²", "㎡")
            area_text = exclusive_area_jp
        if "新築マンション 新規分譲" in compact_blob:
            status_jp = "新築マンション 新規分譲"
        elif ("分譲中" in compact_blob) and (not status_jp or "表すもの" in status_jp):
            status_jp = "分譲中"
        m_handover = re.search(
            r"引き渡し\s*(.{1,48}?)(?=\s+(?:ﾊｲ|ハイ|タワー|複数|販売概要|物件概要|ポイント))",
            homes_detail_blob,
        )
        if m_handover:
            handover_jp = _homes_clean_value(m_handover.group(1), max_len=80)
        m_total_units = re.search(r"(?:全|総戸数)\s*([0-9,]+)\s*(?:邸|戸)", compact_blob)
        if m_total_units:
            total_units_jp = f"全{m_total_units.group(1)}邸"
        if structure_jp and ("省エネ性能" in structure_jp or len(structure_jp) > 80):
            structure_jp = ""
        if parking_jp and len(parking_jp) > 80:
            parking_jp = ""
        if "中古マンション" in compact_blob and "物件概要" in compact_blob:
            overview_idx = compact_blob.find("物件概要 価格")
            if overview_idx < 0:
                overview_idx = compact_blob.find("物件概要")
            homes_overview_blob = compact_blob[overview_idx:] if overview_idx >= 0 else homes_detail_blob
            similar_idx = homes_overview_blob.find("条件が似ている物件")
            if similar_idx > 0:
                homes_overview_blob = homes_overview_blob[:similar_idx]
            m_used_price = re.search(r"価格\s*([0-9][0-9,]*)\s*万円", homes_overview_blob)
            if m_used_price:
                low_price = m_used_price.group(1)
                price_text = f"{low_price}萬日圓"
                try:
                    price_man = float(low_price.replace(",", ""))
                except Exception:
                    pass
                price_fx_hant = _listing_price_fx_hant(price_man, price_text)
            m_used_layout = re.search(r"間取り\s*([0-9A-Za-zＳＬＤＫＲ＋+]+)", homes_overview_blob)
            if m_used_layout:
                layout_line_jp_out = _homes_clean_value(m_used_layout.group(1), max_len=40)
                layout_text = _layout_jp_to_zh(re.sub(r"\s+", "", layout_line_jp_out)) or layout_line_jp_out
            m_used_area = re.search(
                r"専有面積\s*([0-9.]+\s*㎡(?:（[^）]{1,24}）|\([^)]{1,24}\))?)",
                homes_overview_blob,
                flags=re.I,
            )
            if m_used_area:
                exclusive_area_jp = m_used_area.group(1).replace("m2", "㎡").replace("m²", "㎡")
                _ts2, _sqm2, _area2 = _extract_area_text(exclusive_area_jp)
                if _area2:
                    area_tsubo, area_sqm, area_text = _ts2, _sqm2, _area2
            m_used_balcony = re.search(r"バルコニー面積\s*([0-9.]+\s*㎡)", homes_overview_blob)
            if m_used_balcony:
                suumo["balcony_line_jp"] = _homes_clean_value(m_used_balcony.group(1), max_len=40)
            m_used_parking = re.search(r"駐車場\s*(.{1,32}?)(?=\s*築年月)", homes_overview_blob)
            if m_used_parking:
                parking_jp = _homes_clean_value(m_used_parking.group(1), max_len=40)
            m_used_built = re.search(r"築年月\s*([0-9]{4}年\s*[0-9]{1,2}月(?:（築[0-9]+年）)?)", homes_overview_blob)
            if m_used_built:
                built_ym_eff = _homes_clean_value(m_used_built.group(1), max_len=60)
                suumo["built_ym_jp"] = built_ym_eff
            m_used_addr_access = re.search(
                r"所在地\s*(.{4,120}?)\s*交通\s*(.{6,260}?)(?=\s*所在階\s*/\s*階数)",
                homes_overview_blob,
            )
            if m_used_addr_access:
                addr_jp = _homes_clean_value(m_used_addr_access.group(1), max_len=120)
                access_jp = _homes_clean_value(m_used_addr_access.group(2), max_len=240)
            m_used_floor = re.search(r"所在階\s*/\s*階数\s*([0-9]+)\s*階\s*/\s*([0-9]+)\s*階建", homes_overview_blob)
            if m_used_floor:
                floor_structure_jp = f"{m_used_floor.group(1)}階 / {m_used_floor.group(2)}階建"
                floor_text = f"{m_used_floor.group(1)}/{m_used_floor.group(2)}樓"
            m_used_total = re.search(r"総戸数\s*([0-9,]+)\s*戸", homes_overview_blob)
            if m_used_total:
                total_units_jp = f"{m_used_total.group(1)}戸"
            m_used_structure = re.search(r"建物構造\s*(.{1,40}?)(?=\s*土地権利)", homes_overview_blob)
            if m_used_structure:
                structure_jp = _homes_clean_value(m_used_structure.group(1), max_len=60)
            m_manage = re.search(r"管理費等\s*([0-9,]+円)", homes_overview_blob)
            if m_manage:
                suumo["manage_fee_jp"] = m_manage.group(1)
            m_reserve = re.search(r"修繕積立金\s*([0-9,]+円)", homes_overview_blob)
            if m_reserve:
                suumo["reserve_fee_jp"] = m_reserve.group(1)
            m_status = re.search(r"現況\s*(.{1,24}?)(?=\s*実際に見てみたい|\s*引渡し)", homes_overview_blob)
            if m_status:
                status_jp = _homes_clean_value(m_status.group(1), max_len=40)
            m_handover_used = re.search(r"引渡し\s*(.{1,24}?)(?=\s*取引態様)", homes_overview_blob)
            if m_handover_used:
                handover_jp = _homes_clean_value(m_handover_used.group(1), max_len=40)
            m_prop_no = re.search(r"LIFULL HOME'S\s*物件番号\s*([0-9-]+)", homes_overview_blob)
            if m_prop_no:
                suumo["property_no_jp"] = m_prop_no.group(1)
    if "homes.co.jp" in (item_url or "").lower() and "/kodate/b-" in (item_url or "").lower():
        homes_source_blob = "\n".join(
            [
                str(row.get("title_original") or ""),
                str(row.get("body_original") or ""),
            ]
        )
        compact_blob = re.sub(r"\s+", " ", _to_half_width_num(homes_source_blob) or blob_hw).strip()
        m_homes_title = re.search(r"【ホームズ】\s*([^｜|]+)", str(row.get("title_original") or ""))
        if m_homes_title:
            bname = _clean_listing_building_name(m_homes_title.group(1).strip())

        overview_idx = compact_blob.find("物件概要 価格")
        if overview_idx < 0:
            overview_idx = compact_blob.find("物件概要")
        homes_overview_blob = compact_blob[overview_idx:] if overview_idx >= 0 else compact_blob
        stop_candidates = [
            homes_overview_blob.find(token)
            for token in ("支払い目安 月々", "ローン・購入", "会社情報", "条件が似ている物件")
            if homes_overview_blob.find(token) > 0
        ]
        if stop_candidates:
            homes_overview_blob = homes_overview_blob[: min(stop_candidates)]

        m_used_price = re.search(r"価格\s*([0-9][0-9,]*)\s*万円", homes_overview_blob)
        if m_used_price:
            low_price = m_used_price.group(1)
            price_text = f"{low_price}萬日圓"
            try:
                price_man = float(low_price.replace(",", ""))
            except Exception:
                pass
            price_fx_hant = _listing_price_fx_hant(price_man, price_text)

        m_used_layout = re.search(
            r"間取り\s*([0-9A-Za-zＳＬＤＫＲ＋+\s]+?)(?=\s*[（(]|\s*建物面積|\s*土地面積|\s*駐車場)",
            homes_overview_blob,
            flags=re.I,
        )
        if m_used_layout:
            layout_line_jp_out = _homes_clean_value(m_used_layout.group(1), max_len=40)
            layout_text = _layout_jp_to_zh(re.sub(r"\s+", "", layout_line_jp_out)) or layout_line_jp_out

        m_building_area = re.search(
            r"建物面積\s*([0-9.]+\s*(?:㎡|m2|m²)(?:（[^）]{1,32}）|\([^)]{1,32}\))?)",
            homes_overview_blob,
            flags=re.I,
        )
        m_land_area = re.search(
            r"土地面積\s*([0-9.]+\s*(?:㎡|m2|m²)(?:（[^）]{1,32}）|\([^)]{1,32}\))?)",
            homes_overview_blob,
            flags=re.I,
        )
        building_area_jp = ""
        land_area_jp = ""
        if m_building_area:
            building_area_jp = _homes_clean_value(m_building_area.group(1), max_len=60).replace("m2", "㎡").replace("m²", "㎡")
            _ts2, _sqm2, _area2 = _extract_area_text(building_area_jp)
            if _area2:
                area_tsubo, area_sqm, area_text = _ts2, _sqm2, _area2
        if m_land_area:
            land_area_jp = _homes_clean_value(m_land_area.group(1), max_len=60).replace("m2", "㎡").replace("m²", "㎡")
        if building_area_jp and land_area_jp:
            exclusive_area_jp = f"建物 {building_area_jp} / 土地 {land_area_jp}"
            other_area_jp = f"土地 {land_area_jp}"
        elif building_area_jp:
            exclusive_area_jp = f"建物 {building_area_jp}"
        elif land_area_jp:
            exclusive_area_jp = f"土地 {land_area_jp}"
            other_area_jp = f"土地 {land_area_jp}"

        m_used_parking = re.search(
            r"駐車場\s*(.{1,60}?)(?=\s*(?:築年月|所在地|交通|主要採光面|建物構造|接道状況|土地権利|現況|引渡し|取引態様|備考|$))",
            homes_overview_blob,
        )
        if m_used_parking:
            parking_jp = _homes_clean_value(m_used_parking.group(1), max_len=60)

        m_used_built = re.search(r"築年月\s*([0-9]{4}年\s*[0-9]{1,2}月(?:（築[0-9]+年）)?)", homes_overview_blob)
        if m_used_built:
            built_ym_eff = _homes_clean_value(m_used_built.group(1), max_len=60)
            suumo["built_ym_jp"] = built_ym_eff

        m_used_addr_access = re.search(
            r"所在地\s*(.{4,120}?)\s*交通\s*(.{6,260}?)(?=\s*(?:主要採光面|建物構造|接道状況|土地権利|現況|引渡し|取引態様|備考|建築確認番号|LIFULL HOME'S|情報公開日|$))",
            homes_overview_blob,
        )
        if m_used_addr_access:
            addr_jp = _homes_clean_value(m_used_addr_access.group(1), max_len=120)
            access_raw = _homes_clean_value(m_used_addr_access.group(2), max_len=260)
            station_segments = _homes_station_segments(access_raw)
            access_jp = " / ".join(station_segments) if station_segments else access_raw

        m_structure = re.search(
            r"建物構造\s*(.{1,40}?)(?=\s*(?:接道状況|私道負担|土地権利|現況|引渡し|取引態様|用途地域|建築確認番号|$))",
            homes_overview_blob,
        )
        if m_structure:
            structure_jp = _clean_listing_structure_value(_homes_clean_value(m_structure.group(1), max_len=60))
            m_floor_total = re.search(r"/\s*([0-9]{1,3})\s*階建", structure_jp)
            if m_floor_total:
                floor_structure_jp = f"{m_floor_total.group(1)}階建"

        m_status = re.search(r"現況\s*(.{1,24}?)(?=\s*(?:実際に見てみたい|引渡し|取引態様|備考|$))", homes_overview_blob)
        if m_status:
            status_jp = _homes_clean_value(m_status.group(1), max_len=40)
        m_handover = re.search(r"引渡し\s*(.{1,24}?)(?=\s*(?:取引態様|備考|建築確認番号|$))", homes_overview_blob)
        if m_handover:
            handover_jp = _homes_clean_value(m_handover.group(1), max_len=40)
        m_prop_no = re.search(r"LIFULL HOME'S\s*物件番号\s*([0-9-]+)", homes_overview_blob)
        if m_prop_no:
            suumo["property_no_jp"] = m_prop_no.group(1)
        m_info_open = re.search(r"情報公開日[:：]\s*([0-9]{4}/[0-9]{1,2}/[0-9]{1,2})", homes_overview_blob)
        if m_info_open:
            suumo["info_open_jp"] = m_info_open.group(1)
        latest = ""
        m_latest = re.search(r"最新情報提供日[:：]\s*([0-9]{4}/[0-9]{1,2}/[0-9]{1,2})", homes_overview_blob)
        m_valid = re.search(r"情報有効期限[:：]\s*([0-9]{4}/[0-9]{1,2}/[0-9]{1,2})", homes_overview_blob)
        if m_latest:
            latest = f"最新情報提供日：{m_latest.group(1)}"
        if m_valid:
            latest = f"{latest} / 情報有効期限：{m_valid.group(1)}" if latest else f"情報有効期限：{m_valid.group(1)}"
        if latest:
            suumo["next_update_jp"] = latest
        building_type = "透天/一戶建"
    if access_jp and not transit_line_looks_substantive(access_jp):
        access_jp = ""
    bname = _clean_listing_building_name(bname)
    addr_jp = _clean_listing_address_line(addr_jp)
    access_jp = _clean_listing_access_line(access_jp)
    structure_jp = _clean_listing_structure_value(structure_jp)
    parking_jp = _clean_listing_parking_value(parking_jp)
    handover_jp = _clean_listing_handover_value(handover_jp)
    item_url_lc = (item_url or "").lower()
    if "/kodate/" in item_url_lc or "/ikkodate/" in item_url_lc:
        building_type = "透天/一戶建"
    elif "/mansion/" in item_url_lc or "/ms/" in item_url_lc:
        building_type = "公寓大樓"
    return {
        "price_man": price_man,
        "price_text_hant": price_text,
        "price_fx_hant": price_fx_hant,
        "layout_text_hant": layout_text,
        "layout_line_jp": layout_line_jp_out,
        "exclusive_area_jp": exclusive_area_jp,
        "area_tsubo": area_tsubo,
        "area_sqm": area_sqm,
        "area_text_hant": area_text,
        "age_years": age_years,
        "age_text_hant": age_text,
        "floor_text_hant": floor_text,
        "building_type_zh": building_type,
        "feature_tags_hant": tags,
        "address_hint_zh": "｜".join([x for x in [region, access_jp or transit] if x]),
        "building_name_jp": bname,
        "address_line_jp": addr_jp,
        "access_line_jp": access_jp,
        "balcony_line_jp": suumo.get("balcony_line_jp") or "",
        "built_ym_jp": suumo.get("built_ym_jp") or "",
        "manage_fee_jp": _clean_listing_fee_value(suumo.get("manage_fee_jp") or ""),
        "reserve_fee_jp": _clean_listing_fee_value(suumo.get("reserve_fee_jp") or ""),
        "total_units_jp": total_units_jp,
        "structure_jp": structure_jp,
        "parking_jp": parking_jp,
        "status_jp": status_jp,
        "handover_jp": handover_jp,
        "property_no_jp": suumo.get("property_no_jp") or "",
        "info_open_jp": suumo.get("info_open_jp") or "",
        "next_update_jp": suumo.get("next_update_jp") or "",
        "floor_structure_jp": floor_structure_jp,
        "sales_units_jp": sales_units_jp,
        "other_area_jp": other_area_jp,
        "related_links_jp": related_links_jp,
        "company_guide_jp": company_guide_jp,
        "staff_message_jp": staff_message_jp,
        "inquiry_contact_jp": inquiry_contact_jp,
        "homes_site_trail_jp": homes_site_trail_jp,
    }


_CASE_TIME_RE = re.compile(
    r"(?:情報更新日|情報提供日|情報公開日|情報掲載開始日|掲載情報更新日|掲載日|更新日|公開日)"
    r"\s*[:：]?\s*"
    r"([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2})",
    re.I,
)
_SOURCE_UPDATE_TIME_RE = re.compile(
    r"(?:情報更新日|掲載情報更新日|更新日)"
    r"\s*[:：]?\s*"
    r"([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2})",
    re.I,
)
_SOURCE_OPEN_TIME_RE = re.compile(
    r"(?:情報提供日|情報公開日|情報掲載開始日|掲載日|公開日)"
    r"\s*[:：]?\s*"
    r"([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日|[0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2})",
    re.I,
)


def _compact_case_time_text(v: Any) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip()
    if not s or s.lower() in {"none", "null", "nat"}:
        return ""
    if "T" in s:
        s = s.replace("T", " ")
    s = re.sub(r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})$", "", s).strip()
    m = re.match(r"^([0-9]{4})/([0-9]{1,2})/([0-9]{1,2})(.*)$", s)
    if m:
        s = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}{m.group(4)}"
    m2 = re.match(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})\s+([0-9]{2}:[0-9]{2})(?::[0-9]{2})?$", s)
    if m2:
        return f"{m2.group(1)} {m2.group(2)}"
    return s[:80]


def _source_listing_time_hint(row: dict[str, Any], listing_fields: dict[str, Any]) -> str:
    blob = "\n".join(
        str(row.get(k) or "")
        for k in ("body_original", "body_zh_hant", "body_zh_hans", "title_original")
        if str(row.get(k) or "").strip()
    )
    for rx in (_SOURCE_UPDATE_TIME_RE, _SOURCE_OPEN_TIME_RE, _CASE_TIME_RE):
        m = rx.search(blob)
        if m:
            return _compact_case_time_text(m.group(1))
    direct = _compact_case_time_text(listing_fields.get("info_open_jp"))
    return direct


def _case_time_fields(row: dict[str, Any], listing_fields: dict[str, Any]) -> dict[str, str]:
    case_time = (
        _source_listing_time_hint(row, listing_fields)
        or _compact_case_time_text(row.get("published_at"))
        or _compact_case_time_text(row.get("last_checked_at"))
        or _compact_case_time_text(row.get("crawled_at"))
    )
    data_time = (
        _compact_case_time_text(row.get("last_checked_at"))
        or _compact_case_time_text(row.get("crawled_at"))
        or _compact_case_time_text(row.get("published_at"))
        or _compact_case_time_text(row.get("updated_at"))
    )
    next_update = _compact_case_time_text(listing_fields.get("next_update_jp"))
    parts: list[str] = []
    if case_time:
        parts.append(f"來源時間：{case_time}")
    if data_time and data_time != case_time:
        parts.append(f"站內更新：{data_time}")
    if next_update:
        parts.append(f"下次更新：{next_update}")
    return {
        "case_time_at": case_time,
        "data_time_at": data_time,
        "sort_time_at": case_time or data_time,
        "source_listing_time_jp": case_time,
        "case_time_label_hant": "｜".join(parts),
    }


def _jp_split_pref_ward_tail(s: str) -> list[str] | None:
    """無標點的『都道府県＋…区＋下町名』拆成三段，讓多 token AND 後備能觸發（例：東京都目黒区目黒本町）。"""
    t = (s or "").strip()
    if len(t) < 7:
        return None
    m = _JP_PREF_WARD_TAIL.match(t)
    if not m:
        return None
    a, b, c = m.group(1), m.group(2), m.group(3)
    if min(len(a), len(b), len(c)) < 2:
        return None
    return [a, b, c]


def _fts5_quote_term(term: str) -> str:
    t = re.sub(r"\s+", " ", str(term or "").strip())
    if len(t) < 2:
        return ""
    if any(ch in t for ch in ('/', "\\", "?", "&", "=", "%", "#", ":")):
        return ""
    return '"' + t.replace('"', '""') + '"'


def _fts5_or_query(terms: list[str] | tuple[str, ...]) -> str:
    seen: set[str] = set()
    quoted: list[str] = []
    for term in terms:
        q = _fts5_quote_term(str(term or ""))
        if not q:
            continue
        k = q.casefold()
        if k in seen:
            continue
        seen.add(k)
        quoted.append(q)
        if len(quoted) >= 24:
            break
    return " OR ".join(quoted)


def _content_or_source_fts_sql(
    fts_query: str,
    *,
    content_rowid_floor: int = 0,
    content_rowid_ceiling: int = 0,
    source_rowid_floor: int = 0,
    source_rowid_ceiling: int = 0,
    candidate_limit: int = 0,
) -> tuple[str, list[Any]]:
    q = str(fts_query or "").strip()
    if not q:
        return "", []
    clauses: list[str] = []
    params: list[Any] = []

    content_sql = "c.id IN (SELECT rowid FROM content_fts WHERE content_fts MATCH ?"
    content_params: list[Any] = [q]
    if int(content_rowid_floor or 0) > 0:
        content_sql += " AND rowid >= ?"
        content_params.append(int(content_rowid_floor))
    if int(content_rowid_ceiling or 0) > 0:
        content_sql += " AND rowid <= ?"
        content_params.append(int(content_rowid_ceiling))
    if int(candidate_limit or 0) > 0:
        content_sql += " ORDER BY rowid DESC LIMIT ?"
        content_params.append(int(candidate_limit))
    content_sql += ")"
    clauses.append(content_sql)
    params.extend(content_params)

    source_sql = "s.id IN (SELECT rowid FROM source_fts WHERE source_fts MATCH ?"
    source_params: list[Any] = [q]
    if int(source_rowid_floor or 0) > 0:
        source_sql += " AND rowid >= ?"
        source_params.append(int(source_rowid_floor))
    if int(source_rowid_ceiling or 0) > 0:
        source_sql += " AND rowid <= ?"
        source_params.append(int(source_rowid_ceiling))
    if int(candidate_limit or 0) > 0:
        source_sql += " ORDER BY rowid DESC LIMIT ?"
        source_params.append(int(candidate_limit))
    source_sql += ")"
    clauses.append(source_sql)
    params.extend(source_params)

    if not clauses:
        return "", []
    if len(clauses) == 1:
        return clauses[0], params
    return "(" + " OR ".join(clauses) + ")", params


def _fts_source_id_cte_sql(
    fts_query: str,
    *,
    content_rowid_floor: int = 0,
    content_rowid_ceiling: int = 0,
    source_rowid_floor: int = 0,
    source_rowid_ceiling: int = 0,
    candidate_limit: int = 0,
) -> tuple[str, list[Any]]:
    """Build a WITH-CTE that yields a limited set of matching ``source_items.id``.

    Keyword searches on large datasets can be slow if we drive from ``source_items``
    and scan in recency order until LIMIT is satisfied (worst-case: scan the whole
    table when matches are rare). This helper allows driving the join from FTS hits.
    """
    q = str(fts_query or "").strip()
    lim = int(candidate_limit or 0)
    if not q or lim <= 0:
        return "", []
    lim = max(1, min(lim, 60000))

    content_where = ["content_fts MATCH ?"]
    content_params: list[Any] = [q]
    if int(content_rowid_floor or 0) > 0:
        content_where.append("content_fts.rowid >= ?")
        content_params.append(int(content_rowid_floor))
    if int(content_rowid_ceiling or 0) > 0:
        content_where.append("content_fts.rowid <= ?")
        content_params.append(int(content_rowid_ceiling))
    content_params.append(lim)
    content_sql = (
        "SELECT c.source_item_id AS sid\n"
        "  FROM content_fts\n"
        "  JOIN content_items c ON c.id = content_fts.rowid\n"
        f" WHERE {' AND '.join(content_where)}\n"
        " ORDER BY content_fts.rowid DESC\n"
        " LIMIT ?"
    )

    source_where = ["source_fts MATCH ?"]
    source_params: list[Any] = [q]
    if int(source_rowid_floor or 0) > 0:
        source_where.append("source_fts.rowid >= ?")
        source_params.append(int(source_rowid_floor))
    if int(source_rowid_ceiling or 0) > 0:
        source_where.append("source_fts.rowid <= ?")
        source_params.append(int(source_rowid_ceiling))
    source_params.append(lim)
    source_sql = (
        "SELECT source_fts.rowid AS sid\n"
        "  FROM source_fts\n"
        f" WHERE {' AND '.join(source_where)}\n"
        " ORDER BY source_fts.rowid DESC\n"
        " LIMIT ?"
    )

    sql = (
        "WITH\n"
        "content_hits AS (\n"
        f"{content_sql}\n"
        "),\n"
        "source_hits AS (\n"
        f"{source_sql}\n"
        "),\n"
        "fts_source_ids AS (\n"
        "  SELECT sid FROM content_hits\n"
        "  UNION\n"
        "  SELECT sid FROM source_hits\n"
        ")\n"
    )
    return sql, [*content_params, *source_params]


def _portal_keyword_tokens(kw: str) -> list[str]:
    """長句拆成 2 字以上片段，供寬鬆 AND 比對（略過開頭 [SUUMO] 等標籤）。"""
    raw = re.sub(r"^\s*\[[^\]]{1,24}\]\s*", "", (kw or "").strip())
    parts = re.split(r"[\s（）()、，。！!？?；：\[\]【】]+", raw)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        t = p.strip()
        if len(t) < 2:
            continue
        k = t.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    if len(out) == 1:
        trip = _jp_split_pref_ward_tail(out[0])
        if trip:
            out = trip
    return out[:12]


def _keyword_sql_strict(
    kw: str,
    *,
    narrow_markers: bool = False,
    fts_content_rowid_floor: int = 0,
    fts_content_rowid_ceiling: int = 0,
    fts_source_rowid_floor: int = 0,
    fts_source_rowid_ceiling: int = 0,
    fts_candidate_limit: int = 0,
) -> tuple[str, list[Any]]:
    terms = _expand_portal_keyword_search_tokens(kw, narrow_markers=narrow_markers)
    if not terms:
        return "", []
    fts_q = _fts5_or_query(terms)
    if fts_q:
        fts_sql, fts_params = _content_or_source_fts_sql(
            fts_q,
            content_rowid_floor=int(fts_content_rowid_floor or 0),
            content_rowid_ceiling=int(fts_content_rowid_ceiling or 0),
            source_rowid_floor=int(fts_source_rowid_floor or 0),
            source_rowid_ceiling=int(fts_source_rowid_ceiling or 0),
            candidate_limit=int(fts_candidate_limit or 0),
        )
        if fts_sql:
            return f" AND {fts_sql}", fts_params
    or_groups: list[str] = []
    params: list[Any] = []
    for t in terms:
        like = f"%{t}%"
        g = (
            "(c.title_zh_hant LIKE ? OR c.title_zh_hans LIKE ? OR c.seo_title LIKE ? "
            "OR c.body_zh_hant LIKE ? OR c.body_zh_hans LIKE ? "
            "OR s.title_original LIKE ? OR s.body_original LIKE ? OR s.item_url LIKE ? "
            "OR s.source_name LIKE ?)"
        )
        or_groups.append(g)
        params.extend([like] * 9)
    kw_sql = " AND (" + " OR ".join(or_groups) + ")"
    return kw_sql, params


def _row_to_portal_case_item(r: Any) -> dict[str, Any]:
    d = dict(r)
    meta = infer_case_metadata(d)
    listing_fields = _extract_listing_fields(d, meta=meta)
    host = ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(str(d.get("item_url") or "")).netloc or "")[:120]
    except Exception:
        host = ""
    image_urls_s = str(d.get("image_urls") or "")
    listing_media_json_s = str(d.get("listing_media_json") or "")
    body_original_s = str(d.get("body_original") or "")
    item_url_s = str(d.get("item_url") or "")
    gallery = ordered_listing_image_urls(image_urls_s, "", listing_media_json_s, item_url=item_url_s, limit=6)
    if not gallery and body_original_s:
        gallery = ordered_listing_image_urls("", body_original_s, "", item_url=item_url_s, limit=6)
    thumb = gallery[0] if gallery else ""
    title_original_clean = _clean_translation_noise(str(d.get("title_original") or ""))
    title_hant_clean = _clean_translation_noise(str(d.get("title_zh_hant") or "")) or title_original_clean
    title_hans_clean = _clean_translation_noise(str(d.get("title_zh_hans") or "")) or title_hant_clean
    body_hant_preview = _clean_translation_noise(str(d.get("body_zh_hant") or ""))[:320]
    body_hans_preview = _clean_translation_noise(str(d.get("body_zh_hans") or ""))[:320]
    clean_hant_preview = _clean_portal_case_preview(d, listing_fields, script="hant")
    clean_hans_preview = _clean_portal_case_preview(d, listing_fields, script="hans")
    if clean_hant_preview and _preview_text_looks_stale(body_hant_preview):
        body_hant_preview = clean_hant_preview
    if clean_hans_preview and _preview_text_looks_stale(body_hans_preview):
        body_hans_preview = clean_hans_preview
    display_fields = _portal_case_display_fields(
        d,
        listing_fields,
        meta=meta,
        title_hant_clean=title_hant_clean,
        title_hans_clean=title_hans_clean,
        title_original_clean=title_original_clean,
        body_hant_preview=body_hant_preview,
        body_hans_preview=body_hans_preview,
    )
    legacy_title_hant = str(display_fields.get("title_display_hant") or "").strip() or _portal_case_kana_safe(title_hant_clean, max_len=120)
    legacy_title_hans = str(display_fields.get("title_display_hans") or "").strip() or legacy_title_hant
    legacy_body_hant = str(display_fields.get("body_display_hant_preview") or "").strip() or _portal_case_kana_safe(body_hant_preview, max_len=320)
    legacy_body_hans = str(display_fields.get("body_display_hans_preview") or "").strip() or legacy_body_hant
    return {
        "content_id": d.get("id"),
        "source_item_id": d.get("source_item_id"),
        "seo_slug": d.get("seo_slug") or "",
        "title_zh_hant": legacy_title_hant,
        "title_zh_hans": legacy_title_hans,
        "region_code": d.get("region_code") or "",
        "keyword_type": d.get("keyword_type") or "",
        "topic_category": d.get("topic_category") or "",
        "case_transaction_override": str(d.get("case_transaction_override") or ""),
        "case_jp_region_override": str(d.get("case_jp_region_override") or ""),
        "case_transit_override": str(d.get("case_transit_override") or ""),
        "featured_weight": int(d.get("featured_weight") or 0),
        "source_name": d.get("source_name") or "",
        "source_name_display": _portal_case_source_name_display(d.get("source_name") or "", d.get("item_url") or ""),
        "item_url": d.get("item_url") or "",
        "title_original": title_original_clean,
        "snippet_jp": _clean_snippet_text(str(d.get("body_original") or ""))[:260],
        "body_zh_hant_preview": legacy_body_hant,
        "body_zh_hans_preview": legacy_body_hans,
        "updated_at": str(d.get("updated_at") or ""),
        "published_at": str(d.get("published_at") or ""),
        "crawled_at": str(d.get("crawled_at") or ""),
        "last_checked_at": str(d.get("last_checked_at") or ""),
        "thumb_url": thumb,
        "gallery_urls": gallery,
        "thumb_kind": _thumb_kind_label(thumb) if thumb else "",
        "transaction_side": meta.get("transaction_side"),
        "transaction_label_zh": meta.get("transaction_label_zh"),
        "jp_region_display_zh": meta.get("jp_region_display_zh") or "",
        "transit_line_zh": meta.get("transit_line_zh") or "",
        "jp_station_id": int(d.get("jp_station_id") or 0),
        "walk_min": int(d.get("walk_min") or 0),
        "portal_host": host,
        **_case_time_fields(d, listing_fields),
        **listing_fields,
        **display_fields,
    }


def _row_to_portal_case_item_filter_fast(r: Any) -> dict[str, Any]:
    """Ultra-light conversion for paged search ranking.

    Keep only the fields required by:
    - `_is_probably_listing_detail_result`
    - `_item_display_rank`
    - later page hydration by `source_item_id`

    Avoid `infer_case_metadata()` and `_extract_listing_fields()` here; they are
    too expensive to run over thousands of rows when the UI only needs one page.
    """
    d = dict(r)
    listing_media_json_s = str(d.get("listing_media_json") or "")
    image_urls_s = str(d.get("image_urls") or "")
    thumb = _fast_first_media_url(listing_media_json_s) or _fast_first_image_url(image_urls_s)
    title_original_clean = _clean_translation_noise(str(d.get("title_original") or ""))
    title_hant_clean = _clean_translation_noise(str(d.get("title_zh_hant") or "")) or title_original_clean
    title_hans_clean = _clean_translation_noise(str(d.get("title_zh_hans") or "")) or title_hant_clean
    body_hant_preview = _clean_translation_noise(str(d.get("body_zh_hant") or ""))[:320]
    body_hans_preview = _clean_translation_noise(str(d.get("body_zh_hans") or ""))[:320]
    display_title = _portal_case_text_hant(title_hant_clean or title_original_clean, max_len=120)
    if _portal_case_has_kana(display_title):
        region_hint = _portal_case_text_hant(d.get("case_jp_region_override") or "", max_len=40)
        tx_hint = _portal_case_text_hant(d.get("case_transaction_override") or "", max_len=40)
        display_title = "｜".join(x for x in (region_hint, tx_hint, "日本不動產案件") if x).strip("｜")[:96]
    display_preview_hant = _portal_case_text_hant(body_hant_preview, max_len=320)
    display_preview_hans = _portal_case_text_hant(body_hans_preview or body_hant_preview, max_len=320)
    if _portal_case_has_kana(display_preview_hant):
        display_preview_hant = display_title
    if _portal_case_has_kana(display_preview_hans):
        display_preview_hans = display_preview_hant
    gallery = [thumb] if thumb else []
    return {
        "content_id": d.get("id"),
        "source_item_id": d.get("source_item_id"),
        "seo_slug": d.get("seo_slug") or "",
        "title_zh_hant": display_title,
        "title_zh_hans": display_title,
        "title_display_hant": display_title,
        "title_display_hans": display_title,
        "region_code": d.get("region_code") or "",
        "keyword_type": d.get("keyword_type") or "",
        "topic_category": d.get("topic_category") or "",
        "case_transaction_override": str(d.get("case_transaction_override") or ""),
        "case_jp_region_override": str(d.get("case_jp_region_override") or ""),
        "case_transit_override": str(d.get("case_transit_override") or ""),
        "featured_weight": int(d.get("featured_weight") or 0),
        "source_name": d.get("source_name") or "",
        "source_name_display": _portal_case_source_name_display(d.get("source_name") or "", d.get("item_url") or ""),
        "item_url": d.get("item_url") or "",
        "title_original": title_original_clean,
        "snippet_jp": _clean_snippet_text(str(d.get("body_original") or ""))[:260],
        "body_zh_hant_preview": display_preview_hant,
        "body_zh_hans_preview": display_preview_hans,
        "body_display_hant_preview": display_preview_hant,
        "body_display_hans_preview": display_preview_hans,
        "updated_at": str(d.get("updated_at") or ""),
        "published_at": str(d.get("published_at") or ""),
        "crawled_at": str(d.get("crawled_at") or ""),
        "last_checked_at": str(d.get("last_checked_at") or ""),
        "thumb_url": thumb,
        "gallery_urls": gallery,
        "thumb_kind": _thumb_kind_label(thumb) if thumb else "",
        "transaction_side": "",
        "transaction_label_zh": "",
        "jp_region_display_zh": str(d.get("case_jp_region_override") or ""),
        "transit_line_zh": str(d.get("case_transit_override") or ""),
        "jp_station_id": int(d.get("jp_station_id") or 0),
        "walk_min": int(d.get("walk_min") or 0),
        "case_time_at": "",
        "data_time_at": "",
        "sort_time_at": (
            str(d.get("published_at") or "")
            or str(d.get("last_checked_at") or "")
            or str(d.get("crawled_at") or "")
            or str(d.get("updated_at") or "")
        )[:80],
        "source_listing_time_jp": "",
        "case_time_label_hant": "",
        "price_text_hant": "",
        "address_line_jp": "",
        "access_line_jp": "",
        "address_line_hant": "",
        "access_line_hant": "",
        "area_text_hant": "",
        "layout_text_hant": "",
        "built_ym_jp": "",
        "floor_text_hant": "",
        "manage_fee_jp": "",
        "reserve_fee_jp": "",
        "total_units_jp": "",
    }


def _fast_first_media_url(listing_media_json: str) -> str:
    s = str(listing_media_json or "").strip()
    if not s or s in ("[]", "{}", "null", "None"):
        return ""
    i = 0
    while i < len(s):
        i = s.find('\"url\"', i)
        if i < 0:
            break
        i = s.find(":", i)
        if i < 0:
            break
        i += 1
        while i < len(s) and s[i] in (" ", "\t", "\r", "\n"):
            i += 1
        if i >= len(s) or s[i] != '\"':
            continue
        j = s.find('\"', i + 1)
        if j < 0:
            break
        url = s[i + 1 : j].strip()
        i = j + 1
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        if not _is_fast_listing_image_url_usable(url):
            continue
        return url[:2048]
    return ""


def _fast_first_image_url(image_urls: str) -> str:
    s = str(image_urls or "").strip()
    if not s:
        return ""
    for part in s.replace("\r", "").replace("\n", " ").split():
        u = part.strip().strip(",")
        if (u.startswith("http://") or u.startswith("https://")) and _is_fast_listing_image_url_usable(u):
            return u[:2048]
    return ""


def _is_fast_listing_image_url_usable(url: str) -> bool:
    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    if not _is_percent_encoding_valid(u):
        return False
    if _is_truncated_listing_image_url(u):
        return False
    if _is_non_listing_asset_url(u):
        return False
    if is_non_image_portal_page_url(u):
        return False
    if is_likely_agent_portrait_image_url(u):
        return False
    return True


def _row_to_portal_case_item_matrix_fast(r: Any) -> dict[str, Any]:
    """Fast conversion for coverage-matrix aligned mode.

    Avoid heavy regex parsing (infer_case_metadata / listing field extraction / image scoring).
    """
    d = dict(r)
    listing_media_json_s = str(d.get("listing_media_json") or "")
    image_urls_s = str(d.get("image_urls") or "")
    thumb = _fast_first_media_url(listing_media_json_s) or _fast_first_image_url(image_urls_s)
    gallery = [thumb] if thumb else []
    tx_label = str(d.get("case_transaction_override") or "").strip()
    jp_region = str(d.get("case_jp_region_override") or "").strip()
    transit = str(d.get("case_transit_override") or "").strip()
    updated_at = str(d.get("updated_at") or "")
    published_at = str(d.get("published_at") or "")
    crawled_at = str(d.get("crawled_at") or "")
    last_checked_at = str(d.get("last_checked_at") or "")
    case_time_at = published_at or last_checked_at or crawled_at or updated_at
    data_time_at = last_checked_at or crawled_at or updated_at or published_at
    sort_time_at = case_time_at or data_time_at
    title_original = str(d.get("title_original") or "").strip()
    title_hant = str(d.get("title_zh_hant") or "").strip() or title_original
    title_hans = str(d.get("title_zh_hans") or "").strip() or title_hant
    title_display = _portal_case_text_hant(title_hant or title_original, max_len=120)
    if _portal_case_has_kana(title_display):
        title_display = "｜".join(x for x in (jp_region, tx_label, "日本不動產案件") if x).strip("｜")[:96]
    return {
        "content_id": d.get("id"),
        "source_item_id": d.get("source_item_id"),
        "seo_slug": d.get("seo_slug") or "",
        "title_zh_hant": title_display,
        "title_zh_hans": title_display,
        "title_display_hant": title_display,
        "title_display_hans": title_display,
        "region_code": d.get("region_code") or "",
        "keyword_type": d.get("keyword_type") or "",
        "topic_category": d.get("topic_category") or "",
        "case_transaction_override": str(d.get("case_transaction_override") or ""),
        "case_jp_region_override": str(d.get("case_jp_region_override") or ""),
        "case_transit_override": str(d.get("case_transit_override") or ""),
        "featured_weight": int(d.get("featured_weight") or 0),
        "source_name": d.get("source_name") or "",
        "source_name_display": _portal_case_source_name_display(d.get("source_name") or "", d.get("item_url") or ""),
        "item_url": d.get("item_url") or "",
        "title_original": title_original,
        "updated_at": updated_at,
        "published_at": published_at,
        "crawled_at": crawled_at,
        "last_checked_at": last_checked_at,
        "thumb_url": thumb,
        "gallery_urls": gallery,
        "thumb_kind": _thumb_kind_label(thumb) if thumb else "",
        "transaction_label_zh": tx_label,
        "jp_region_display_zh": jp_region,
        "transit_line_zh": transit,
        "jp_station_id": int(d.get("jp_station_id") or 0),
        "walk_min": int(d.get("walk_min") or 0),
        "case_time_at": case_time_at[:80],
        "data_time_at": data_time_at[:80],
        "sort_time_at": sort_time_at[:80],
        "source_listing_time_jp": case_time_at[:80],
        "case_time_label_hant": "",
    }


def _row_dicts_to_portal_case_items(rows: list[Any]) -> list[dict[str, Any]]:
    """將 SQL 查回之列轉成智慧查詢案源 item（與 search_portal_cases 欄位一致）。"""
    return [_row_to_portal_case_item(r) for r in rows]


def _paged_portal_case_items_from_rows_fast(
    rows: list[Any],
    *,
    lim: int,
    offset: int = 0,
    page_size: int = 0,
    multi_portal: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Interactive fast path: rank/filter all rows cheaply, then fully hydrate only the requested page."""
    if not rows:
        return [], 0
    fast_items = [_row_to_portal_case_item_filter_fast(r) for r in rows]
    fast_items = _prefer_complete_items_for_display(fast_items, lim=lim)
    fast_items = [it for it in fast_items if _is_probably_listing_detail_result(it)]
    if multi_portal:
        ranked_items = _merge_multi_portal_items(fast_items, lim=lim)
    else:
        ranked_items = _dedupe_portal_case_items(fast_items, lim=lim)
    total_count = len(ranked_items)
    if page_size > 0:
        start = max(0, int(offset or 0))
        page_items = ranked_items[start : start + max(1, int(page_size or 1))]
    else:
        page_items = ranked_items
    row_by_sid: dict[int, Any] = {}
    for row in rows:
        try:
            sid = int(dict(row).get("source_item_id") or 0)
        except Exception:
            sid = 0
        if sid > 0 and sid not in row_by_sid:
            row_by_sid[sid] = row
    hydrated: list[dict[str, Any]] = []
    for item in page_items:
        sid = int(item.get("source_item_id") or 0)
        row = row_by_sid.get(sid)
        hydrated.append(_row_to_portal_case_item(row) if row is not None else item)
    return hydrated, total_count


_COVERAGE_HOST_SOURCE_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "suumo.jp": ("SUUMO", "SUUMO（スーモ）"),
    "homes.co.jp": ("LIFULL HOME'S", "LIFULL HOME'S（ライフルホームズ）"),
    "athome.co.jp": ("AtHome", "at home", "アットホーム（AtHome）"),
    "realestate.yahoo.co.jp": ("Yahoo!不動産",),
    "realestate.rakuten.co.jp": ("楽天不動産",),
    "yes1.co.jp": ("イエステーション", "イエステーション YesStation"),
    "oheya-su.jp": ("OHEYASU", "OHEYASU（お部屋探す）"),
}


def _portal_keys_to_coverage_host_or_clause(portal_keys: list[str]) -> tuple[str, list[Any], list[str], dict[str, str]]:
    key_to_host = {
        "suumo": "suumo.jp",
        "homes": "homes.co.jp",
        "athome": "athome.co.jp",
        "yahoo": "realestate.yahoo.co.jp",
        "rakuten": "realestate.rakuten.co.jp",
        "yes1": "yes1.co.jp",
        "oheya_su": "oheya-su.jp",
    }
    seen: set[str] = set()
    hosts: list[str] = []
    for k in portal_keys:
        h = key_to_host.get(k)
        if h and h not in seen:
            seen.add(h)
            hosts.append(h)
    if not hosts:
        hosts = list(key_to_host.values())
    src_seen: set[str] = set()
    src_names: list[str] = []
    src_to_host: dict[str, str] = {}
    for h in hosts:
        for nm in _COVERAGE_HOST_SOURCE_NAME_ALIASES.get(h, ()):
            t = str(nm or "").strip()
            if not t or t in src_seen:
                continue
            src_seen.add(t)
            src_names.append(t)
            src_to_host[t] = h
    if src_names:
        ph = ",".join("?" for _ in src_names)
        return f"(s.source_name IN ({ph}))", list(src_names), hosts, src_to_host
    parts: list[str] = []
    params: list[Any] = []
    for h in hosts:
        hs, hp = coverage_host_where_sql(h)
        parts.append(f"({hs})")
        params.extend(hp)
    return " OR ".join(parts), params, hosts, {}


def _matrix_mode_keyword_sql(kw: str) -> tuple[str, list[Any], str]:
    """在矩陣同口徑 WHERE 上追加關鍵字（與一般智慧查詢相同嚴格／分詞邏輯）。"""
    ki = (kw or "").strip()
    if not ki:
        return "", [], ""
    nmark = _is_jp_address_level_query(ki)
    ksql, kpar = _keyword_sql_strict(ki, narrow_markers=nmark)
    if ksql:
        return ksql, kpar, "strict"
    toks = _portal_keyword_tokens(ki)
    if len(toks) >= 2:
        ksql2, kpar2 = _keyword_sql_tokens_and(toks, narrow_markers=nmark)
        if ksql2:
            return ksql2, kpar2, "token_and"
    return "", [], ""


def _search_portal_cases_coverage_matrix_mode(
    *,
    portal: str,
    region_hint: str,
    max_age_days: int,
    limit: int,
    offset: int = 0,
    page_size: int = 0,
    keyword_input: str = "",
    region_inferred_from_keyword: bool = False,
) -> dict[str, Any]:
    """與 /api/cases/coverage-matrix 同口徑：jp_listing、矩陣新鮮度欄位（無日期時不回退 now）、站點 host、區域條件。"""
    lim = max(1, min(int(limit or 60), 100000))
    page_offset = max(0, int(offset or 0))
    requested_page_size = max(0, min(int(page_size or 0), 240))
    _, _, portal_keys = _portal_host_clause(portal)
    multi_portal = len(portal_keys) > 1
    if requested_page_size > 0:
        fetch_lim = requested_page_size
    else:
        fetch_lim = min(12000, max(lim, lim * 4, 1000)) if multi_portal else lim
    region_st = (region_hint or "").strip()
    region_keys = ["甲信越", "北陸"] if region_st in {"甲信越・北陸", "甲信越/北陸", "甲信越 北陸"} else ([region_st] if region_st else [])
    region_placeholders = ",".join("?" for _ in region_keys)
    host_sql, host_params, resolved_hosts, src_name_to_host = _portal_keys_to_coverage_host_or_clause(list(portal_keys))
    n = int(max_age_days or 0)
    if n > 0:
        n = min(max(n, 1), 366)

    order_ts_sql = (
        "COALESCE("
        "NULLIF(TRIM(s.published_at), ''), "
        "NULLIF(TRIM(s.last_checked_at), ''), "
        "NULLIF(TRIM(s.crawled_at), ''), "
        "NULLIF(TRIM(c.updated_at), ''), "
        "datetime('now')"
        ")"
    )
    row_fetch_count = 0
    coverage_matrix_sql_total = 0
    cell_by_host: dict[str, int] = {}
    mk_sql, mk_params, mk_how = _matrix_mode_keyword_sql(keyword_input)
    with get_conn() as conn:
        has_region = bool(region_keys)
        count_needs_content = bool(mk_sql)
        # `source_items` and `content_items` are kept one-to-one in the production
        # listing DB. Avoid a per-row correlated "latest content" lookup here; it
        # makes cold regional searches (especially smaller buckets like 北陸) pay
        # hundreds of index probes before the first page can render.
        latest_content_join = "LEFT JOIN content_items c ON c.source_item_id = s.id"
        count_content_join = f"\n        {latest_content_join}" if count_needs_content else ""
        # jp_listing_region_index.sort_time historically可能為空（舊批次僅建立 key，不填時間）。
        # 為避免「全數歸零」，矩陣同口徑查詢一律以 source_items.last_checked_at 作為新鮮度與排序基準。
        fresh_sql = f"s.last_checked_at >= datetime('now', '-{n} days')" if n > 0 else "1=1"
        if has_region:
            from_sql_count = (
                "FROM jp_listing_region_index rix INDEXED BY idx_jp_listing_region_sort\n"
                "        CROSS JOIN source_items s ON s.id = rix.source_item_id"
                f"{count_content_join}"
            )
            count_region_params: list[Any] = list(region_keys)
            count_region_where = f"rix.region_key IN ({region_placeholders})"
            from_sql_fetch = (
                "FROM jp_listing_region_index rix INDEXED BY idx_jp_listing_region_sort\n"
                "        CROSS JOIN source_items s ON s.id = rix.source_item_id\n"
                f"        {latest_content_join}"
            )
            fetch_region_params: list[Any] = list(region_keys)
            fetch_region_where = f"rix.region_key IN ({region_placeholders})"
            order_by_sql = "rix.sort_time DESC, rix.source_item_id DESC"
        else:
            from_sql_count = (
                "FROM source_items s INDEXED BY idx_source_items_content_kind_last_checked"
                f"{count_content_join}"
            )
            count_region_params = []
            count_region_where = "1=1"
            from_sql_fetch = (
                "FROM source_items s INDEXED BY idx_source_items_content_kind_last_checked\n"
                f"        {latest_content_join}"
            )
            fetch_region_params = []
            fetch_region_where = "1=1"
            order_by_sql = "s.last_checked_at DESC, s.id DESC"

        # 首屏分页只需要一个总数来稳定分页。默认全来源、无关键词的大区/县查询
        # 走快速 COUNT，避免按来源分组扫描把地图点击卡住。
        can_fast_count_page = bool(
            requested_page_size > 0
            and has_region
            and not mk_sql
            and len(resolved_hosts) >= len(_PORTAL_BUCKET_ORDER) - 1
        )
        if can_fast_count_page:
            count_sql = f"""
            SELECT COUNT(*) AS c
            FROM jp_listing_region_index rix INDEXED BY idx_jp_listing_region_sort
            WHERE ({count_region_where})
            """
            count_row = conn.execute(count_sql, count_region_params).fetchone()
            coverage_matrix_sql_total = int((count_row["c"] if count_row else 0) or 0)
            for hk in resolved_hosts:
                cell_by_host[hk] = 0
        else:
            # 與 coverage-matrix 同口徑 COUNT：以 source_name 分組，再映射回 host_key（避免 N+1 COUNT）
            for hk in resolved_hosts:
                cell_by_host[hk] = 0
            count_sql = f"""
            SELECT s.source_name AS source_name, COUNT(DISTINCT s.id) AS c
            {from_sql_count}
            WHERE ({CASE_INV_JP_LISTING_SQL})
              AND ({fresh_sql})
              AND ({count_region_where})
              AND ({host_sql})
              {mk_sql}
            GROUP BY s.source_name
            """
            count_params = [*count_region_params, *host_params, *mk_params]
            count_rows = conn.execute(count_sql, count_params).fetchall()
            for r in count_rows:
                sn = str(r["source_name"] or "")
                hk = src_name_to_host.get(sn)
                if hk:
                    cell_by_host[hk] = int(cell_by_host.get(hk, 0) or 0) + int(r["c"] or 0)
            coverage_matrix_sql_total = int(sum(int(cell_by_host.get(hk, 0) or 0) for hk in resolved_hosts))

        page_sql = "LIMIT ?" if requested_page_size > 0 else "LIMIT ?"
        page_query_limit = fetch_lim
        if requested_page_size > 0:
            # Some regional buckets (notably 北陸 / small prefectures) contain many
            # index hits whose source pages are landing/captcha/list-like records.
            # Offset must be applied after the cheap "real listing" filter; applying
            # SQL OFFSET first makes page 2/3 randomly empty when the raw rows around
            # that offset are mostly list pages. Pull a bounded window from the front,
            # rank/filter cheaply, then hydrate only the requested page.
            visible_target = max(1, int(page_offset or 0) + int(requested_page_size or 1))
            candidate_cap = max(720, min(lim, 12000))
            page_query_limit = min(candidate_cap, max(fetch_lim, visible_target * 80))
        sql = f"""
        SELECT
          COALESCE(c.id, 0) AS id,
          COALESCE(c.seo_slug, '') AS seo_slug,
          COALESCE(c.title_zh_hant, '') AS title_zh_hant,
          COALESCE(c.title_zh_hans, '') AS title_zh_hans,
          substr(COALESCE(c.body_zh_hant,''),1,900) AS body_zh_hant,
          substr(COALESCE(c.body_zh_hans,''),1,900) AS body_zh_hans,
          COALESCE(c.region_code, '') AS region_code,
          COALESCE(c.keyword_type, '') AS keyword_type,
          COALESCE(c.topic_category, '') AS topic_category,
          COALESCE(c.case_transaction_override, '') AS case_transaction_override,
          COALESCE(c.case_jp_region_override, '') AS case_jp_region_override,
          COALESCE(c.case_transit_override, '') AS case_transit_override,
          COALESCE(c.jp_station_id, 0) AS jp_station_id,
          COALESCE(c.walk_min, 0) AS walk_min,
          COALESCE(c.featured_weight, 0) AS featured_weight,
          COALESCE(c.listing_media_json, '[]') AS listing_media_json,
          COALESCE(c.updated_at, '') AS updated_at,
          s.id AS source_item_id,
          s.source_name,
          s.item_url,
          s.title_original,
          substr(COALESCE(s.body_original,''),1,9000) AS body_original,
          substr(COALESCE(s.image_urls,''),1,8000) AS image_urls,
          s.published_at,
          s.crawled_at,
          s.last_checked_at,
          COALESCE(s.content_kind, '') AS content_kind
        {from_sql_fetch}
        WHERE ({CASE_INV_JP_LISTING_SQL})
          AND ({fresh_sql})
          AND ({fetch_region_where})
          AND ({host_sql})
          {mk_sql}
        ORDER BY {order_by_sql}
        {page_sql}
        """
        params = [*fetch_region_params, *host_params, *mk_params, page_query_limit]
        rows = conn.execute(sql, params).fetchall()
        row_fetch_count = len(rows)
    if requested_page_size > 0:
        # The matrix query keeps COUNT/WHERE aligned with the coverage dashboard, but
        # displayed cards must use the full extractor; otherwise price/area/layout are
        # intentionally absent from the fast matrix shape and the UI shows dashes.
        items, _display_total_count = _paged_portal_case_items_from_rows_fast(
            rows,
            lim=max(int(requested_page_size or 1), int(page_offset or 0) + int(requested_page_size or 1)),
            offset=page_offset,
            page_size=requested_page_size,
            multi_portal=multi_portal,
        )
    else:
        items = [_row_to_portal_case_item_matrix_fast(r) for r in rows]
        items = _prefer_complete_items_for_display(items, lim=lim)
        if multi_portal:
            items = _merge_multi_portal_items(items, lim=lim)
        else:
            items = sorted(items, key=_item_display_rank, reverse=True)[:lim]
    max_age_out = int(max_age_days) if int(max_age_days or 0) > 0 else 0
    mat_note = (
        "與「庫存巡檢矩陣」同 WHERE：jp_listing ＋ date(新鮮欄位) 區間 ＋ 站點 host ＋ 區域別關鍵；"
        "不含買賣租／交通／SUUMO 列表守門。"
    )
    if mk_how:
        mat_note += f" 已依關鍵字篩選（{mk_how}）。"
    if not region_st:
        mat_note += " 未指定地區時為日本全域，與矩陣「單一地區列」不同；細地址請輸入關鍵字並確認已套用關鍵字篩選，或改選區域下拉。"
    if multi_portal and len(resolved_hosts) > 1:
        mat_note += " 多站併查時「SQL 合計」對齊矩陣該列合計；要比對單一格子（如首都圏×SUUMO）請只勾選一個來源。"
    if region_inferred_from_keyword:
        mat_note += " 已從關鍵字首詞推斷地區，與矩陣地區欄一致。"
    return {
        "ok": True,
        "transaction": "all",
        "portal": (portal or "").strip().lower(),
        "region_hint": region_st,
        "keyword": (keyword_input or "").strip(),
        "region_inferred_from_keyword": bool(region_inferred_from_keyword),
        "jp_line_id": 0,
        "jp_station_id": 0,
        "walk_max": 0,
        "max_age_days": max_age_out,
        "limit": lim,
        "fetch_limit": fetch_lim,
        "portal_keys": portal_keys,
        "portal_keys_resolved": portal_keys,
        "portal_merge_mode": "sql_time_desc_page" if requested_page_size > 0 else ("global_time_desc" if multi_portal else "sql_time_desc"),
        "coverage_matrix_aligned": True,
        "coverage_matrix_sql_total": int(coverage_matrix_sql_total),
        "coverage_matrix_cell_by_host": cell_by_host,
        "coverage_matrix_note_zh": mat_note,
        "count": int(coverage_matrix_sql_total) if requested_page_size > 0 else len(items),
        "count_exact": (False if can_fast_count_page else True) if requested_page_size > 0 else bool(row_fetch_count < fetch_lim),
        "truncation_note": False if requested_page_size > 0 else row_fetch_count >= fetch_lim,
        "page_offset": page_offset if requested_page_size > 0 else 0,
        "page_size": requested_page_size if requested_page_size > 0 else 0,
        "items": items,
    }


def _keyword_sql_tokens_and(
    tokens: list[str],
    *,
    narrow_markers: bool = False,
    fts_content_rowid_floor: int = 0,
    fts_content_rowid_ceiling: int = 0,
    fts_source_rowid_floor: int = 0,
    fts_source_rowid_ceiling: int = 0,
) -> tuple[str, list[Any]]:
    """每個片段至少在標題／摘要／內文／網址其一命中（多片段 AND）。"""
    if len(tokens) < 2:
        return "", []
    fts_groups: list[str] = []
    for tok in tokens:
        exp = _expand_portal_keyword_search_tokens(tok, narrow_markers=narrow_markers) or [tok]
        q = _fts5_or_query(exp)
        if not q:
            fts_groups = []
            break
        # FTS5: parentheses do NOT participate in implicit-AND insertion.
        # Use explicit AND between groups; only parenthesize OR-groups.
        qn = q.strip()
        fts_groups.append(f"({qn})" if " OR " in qn else qn)
    if fts_groups:
        fts_sql, fts_params = _content_or_source_fts_sql(
            " AND ".join(fts_groups),
            content_rowid_floor=int(fts_content_rowid_floor or 0),
            content_rowid_ceiling=int(fts_content_rowid_ceiling or 0),
            source_rowid_floor=int(fts_source_rowid_floor or 0),
            source_rowid_ceiling=int(fts_source_rowid_ceiling or 0),
        )
        if fts_sql:
            return f" AND {fts_sql}", fts_params
    fields = [
        "c.title_zh_hant",
        "c.title_zh_hans",
        "c.seo_title",
        "c.body_zh_hant",
        "c.body_zh_hans",
        "s.title_original",
        "s.body_original",
        "s.item_url",
        "s.source_name",
    ]
    groups: list[str] = []
    params: list[Any] = []
    for tok in tokens:
        exp = _expand_portal_keyword_search_tokens(tok, narrow_markers=narrow_markers) or [tok]
        inners: list[str] = []
        for t in exp:
            like = f"%{t}%"
            ors = " OR ".join(f"{f} LIKE ?" for f in fields)
            inners.append(f"({ors})")
            params.extend([like] * len(fields))
        groups.append("(" + " OR ".join(inners) + ")")
    return " AND " + " AND ".join(groups), params


def search_portal_cases(
    *,
    transaction: str,
    portal: str,
    region_hint: str,
    keyword: str,
    max_age_days: int,
    limit: int,
    offset: int = 0,
    page_size: int = 0,
    property_types: list[str] | None = None,
    price_min_man: int = 0,
    price_max_man: int = 0,
    layout_min_rooms: int = 0,
    layout_max_rooms: int = 0,
    layout_exact_zero: bool = False,
    jp_line_id: int = 0,
    jp_station_id: int = 0,
    walk_max: int = 0,
    coverage_matrix_aligned: bool = False,
) -> dict[str, Any]:
    """transaction: buy|sell|rent；max_age_days 0=不限；limit 上限 100000。

    jp_line_id / jp_station_id：與 jp_trans_* 種子表對應，用於查詢關鍵字加權（並保留徒步上限篩選）。
    """
    lim = max(1, min(int(limit or 60), 100000))
    page_offset = max(0, int(offset or 0))
    requested_page_size = max(0, min(int(page_size or 0), 240))
    tx_sql, tx_params = _transaction_clause(transaction)
    tx_key = (transaction or "").strip().lower() or "buy"
    if tx_key not in ("buy", "sell", "rent"):
        tx_key = "buy"
    portal_sql, portal_params, portal_keys = _portal_host_clause(portal)
    if len(portal_keys) >= len(_PORTAL_BUCKET_ORDER) - 1:
        portal_sql, portal_params = "1=1", []
    rec_sql, rec_params = _recency_sql(max_age_days)
    smart_property_types = _normalize_smart_property_types(property_types)
    smart_price_min = max(0, min(999_999, int(price_min_man or 0)))
    smart_price_max = max(0, min(999_999, int(price_max_man or 0)))
    if smart_price_min > 0 and smart_price_max > 0 and smart_price_max < smart_price_min:
        smart_price_min, smart_price_max = smart_price_max, smart_price_min
    smart_layout_min = max(0, min(99, int(layout_min_rooms or 0)))
    smart_layout_max = max(0, min(99, int(layout_max_rooms or 0)))
    smart_layout_exact_zero = bool(layout_exact_zero)
    if smart_layout_exact_zero:
        smart_layout_min = 0
        smart_layout_max = 0
    if smart_layout_min > 0 and smart_layout_max > 0 and smart_layout_max < smart_layout_min:
        smart_layout_min, smart_layout_max = smart_layout_max, smart_layout_min
    has_smart_structured_filters = bool(
        smart_property_types
        or smart_price_min > 0
        or smart_price_max > 0
        or smart_layout_min > 0
        or smart_layout_max > 0
        or smart_layout_exact_zero
    )
    original_keyword = (keyword or "").strip()
    kw_base = original_keyword
    region_hint_was_empty = not (region_hint or "").strip()
    keyword_was_empty = not original_keyword
    # 區域／關鍵字皆空、且未用交通加權：視為「庫存瀏覽」，勿與矩陣單區列強行比對，但应避免套用詳情過濾導致筆數暴降
    broad_inventory_browse = (
        region_hint_was_empty
        and keyword_was_empty
        and int(jp_station_id or 0) <= 0
        and int(jp_line_id or 0) <= 0
    )
    # 使用者常把「首都圏/關東」放在 keyword；整段等於地區鍵時轉為 region_hint（與巡檢口徑一致）
    if not (region_hint or "").strip() and kw_base in _REGION_HINT_URL_PATH_MARKERS:
        region_hint = kw_base
        kw_base = ""
    # 「關東 不動産」等：視為地區瀏覽（避免關鍵字掃描拖慢互動查詢）。
    if not (region_hint or "").strip() and kw_base:
        toks = kw_base.split()
        tok0 = toks[0] if toks else ""
        tok0_norm = tok0
        if tok0 in ("関東", "关东"):
            tok0_norm = "關東"
        elif tok0 in ("関西", "关西"):
            tok0_norm = "關西"
        elif tok0 in ("沖縄", "冲绳"):
            tok0_norm = "沖繩"
        if tok0_norm and tok0_norm in _JP_AREA_LABEL_SET and len(toks) >= 2:
            generic = {
                "不動産",
                "物件",
                "住宅",
                "不動產",
                "不动産",
                "不动产",
                "房產",
                "房产",
                "房屋",
                "マンション",
                "公寓",
                "大樓",
                "一戸建",
                "賃貸",
                "売買",
                "購入",
            }
            rest = [t for t in toks[1:] if t]
            if rest and all(t in generic for t in rest):
                region_hint = tok0_norm
                kw_base = ""
        if not (region_hint or "").strip() and len(toks) >= 2:
            generic = {
                "不動産",
                "物件",
                "住宅",
                "不動產",
                "不动産",
                "不动产",
                "マンション",
                "一戸建",
                "賃貸",
                "売買",
                "購入",
            }
            rest = [t for t in toks[1:] if t]
            tok0_index = _normalize_region_index_focus_key(tok0_norm)
            if tok0_index and rest and all(t in generic for t in rest):
                region_hint = tok0_index
                kw_base = ""
    elif (region_hint or "").strip() and kw_base:
        # The UI can carry a generic default keyword such as "関東 不動産" while
        # the user actually clicked another map region. Treat those defaults as
        # noise so they do not override the explicit region or force an FTS scan.
        toks = [t for t in kw_base.split() if t]
        if len(toks) >= 2:
            generic = {
                "不動産",
                "不動產",
                "不动産",
                "不动产",
                "房產",
                "房产",
                "房屋",
                "物件",
                "住宅",
                "マンション",
                "公寓",
                "大樓",
                "一戸建",
                "賃貸",
                "売買",
                "購入",
                "買屋",
                "買房",
            }
            first = toks[0]
            first_norm = first
            if first in ("関東", "关东"):
                first_norm = "關東"
            elif first in ("関西", "关西"):
                first_norm = "關西"
            elif first in ("沖縄", "冲绳"):
                first_norm = "沖繩"
            first_index = _normalize_region_index_focus_key(first_norm)
            if (first_norm in _JP_AREA_LABEL_SET or first_index) and all(t in generic for t in toks[1:]):
                kw_base = ""
    # 已選大區（如 關東/首都圏）但 keyword 又是「東京」等更細地區：視為地區精煉，避免再走關鍵字掃描。
    broad_regions = {
        "首都圏",
        "首都圈",
        "關東",
        "関東",
        "關西",
        "関西",
        "北海道",
        "東北",
        "甲信越",
        "北陸",
        "東海",
        "中國地方",
        "中国地方",
        "四國",
        "四国",
        "九州",
        "沖縄",
        "沖繩",
    }
    if (region_hint or "").strip() in broad_regions and kw_base and kw_base in _REGION_HINT_URL_PATH_MARKERS:
        if kw_base not in broad_regions and kw_base != region_hint:
            region_hint = kw_base
            kw_base = ""

    region_hint_norm = _normalize_smart_query_geo_label(region_hint)
    simple_geo_focus = _extract_simple_geo_focus_keyword(kw_base)
    simple_geo_focus_norm = _normalize_smart_query_geo_label(simple_geo_focus)
    simple_geo_focus_index_key = _normalize_region_index_focus_key(simple_geo_focus)
    region_hint_index_key = _normalize_region_index_focus_key(region_hint)
    if (
        region_hint
        and region_hint_norm
        and region_hint_norm != region_hint
        and (region_hint_norm in REGION_INDEX_SEARCH_KEYS or region_hint_norm in _JP_AREA_LABEL_SET)
    ):
        region_hint = region_hint_norm
        region_hint_index_key = _normalize_region_index_focus_key(region_hint)
        region_hint_norm = _normalize_smart_query_geo_label(region_hint)
    if region_hint and region_hint_index_key and region_hint_index_key != region_hint:
        region_hint = region_hint_index_key
        region_hint_norm = _normalize_smart_query_geo_label(region_hint)
    if region_hint_norm and simple_geo_focus_norm and simple_geo_focus_norm == region_hint_norm:
        kw_base = ""
        simple_geo_focus = ""
        simple_geo_focus_norm = ""
        simple_geo_focus_index_key = ""
    elif (
        (region_hint or "").strip() in broad_regions
        and simple_geo_focus_index_key
        and simple_geo_focus_index_key != region_hint_index_key
    ):
        region_hint = simple_geo_focus_index_key
        region_hint_norm = _normalize_smart_query_geo_label(region_hint)
        region_hint_index_key = simple_geo_focus_index_key
        kw_base = ""
        simple_geo_focus = ""
        simple_geo_focus_norm = ""
        simple_geo_focus_index_key = ""

    region_hint = (region_hint or "").strip()
    region_inferred_for_matrix = False
    # 「首都圏 不動産」等：整段不為地區鍵；矩陣同口徑時改取首詞為地區列
    if coverage_matrix_aligned and not region_hint and kw_base:
        toks = kw_base.split()
        if toks and toks[0] in _JP_AREA_LABEL_SET:
            region_hint = toks[0]
            region_inferred_for_matrix = True
    region_hint = (region_hint or "").strip()
    if coverage_matrix_aligned:
        return _search_portal_cases_coverage_matrix_mode(
            portal=portal,
            region_hint=region_hint,
            max_age_days=max_age_days,
            limit=lim,
            offset=page_offset,
            page_size=requested_page_size,
            keyword_input=kw_base,
            region_inferred_from_keyword=region_inferred_for_matrix,
        )
    region_join_sql = ""
    region_join_params: list[Any] = []
    region_sql = ""
    region_params: list[Any] = []
    region_index_keys = _region_hint_index_keys(region_hint)
    if region_hint:
        if region_index_keys:
            region_placeholders = ",".join("?" for _ in region_index_keys)
            region_join_sql = (
                "JOIN jp_listing_region_index rix ON rix.source_item_id = s.id "
                f"AND rix.region_key IN ({region_placeholders})"
            )
            region_join_params = list(region_index_keys)
        else:
            region_join_sql = "JOIN jp_listing_region_index rix ON rix.source_item_id = s.id AND rix.region_key = ?"
            region_join_params = [region_hint]

    sid = max(0, int(jp_station_id or 0))
    lid = max(0, int(jp_line_id or 0))
    wmax = max(0, int(walk_max or 0))
    has_transit_filter = sid > 0 or lid > 0 or wmax > 0
    region_scoped_geo_focus_mode = bool(
        region_hint
        and simple_geo_focus
        and not has_transit_filter
        and not has_smart_structured_filters
    )
    drive_from_region_index = bool(
        region_hint
        and not has_transit_filter
        and not has_smart_structured_filters
        and (not kw_base or region_scoped_geo_focus_mode)
    )
    fine_grained_region_index_probe = bool(
        drive_from_region_index
        and region_hint
        and (region_hint or "").strip() not in broad_regions
    )
    if drive_from_region_index:
        # Rare-region browse queries are fastest when driven by the prebuilt region/time index.
        region_join_sql = ""
        region_join_params = []
        if region_index_keys:
            region_placeholders = ",".join("?" for _ in region_index_keys)
            region_sql = f" AND rix.region_key IN ({region_placeholders})"
            region_params = list(region_index_keys)
        else:
            region_sql = " AND rix.region_key = ?"
            region_params = [region_hint]
    multi_portal = len(portal_keys) > 1
    if has_transit_filter:
        # 通勤・駅搜尋需要快：交通條件本身已高度限縮，避免過度取樣造成延遲。
        # （若環境資料稀疏導致不足，前端可再調高 limit 或取消交通分層。）
        fetch_lim = min(800, max(lim, int(lim * 1.5), 80))
    else:
        if broad_inventory_browse and not has_smart_structured_filters:
            fetch_lim = lim
        elif (region_hint or kw_base) and not has_smart_structured_filters:
            fetch_lim = lim
        elif has_smart_structured_filters:
            fetch_lim = min(12000, max(lim, lim * 8, 1800))
        elif multi_portal:
            fetch_lim = min(6000, max(lim, lim * 4, 1000))
        else:
            fetch_lim = lim
    has_only_type_filter = bool(smart_property_types) and not (
        smart_price_min > 0
        or smart_price_max > 0
        or smart_layout_min > 0
        or smart_layout_max > 0
        or smart_layout_exact_zero
        or has_transit_filter
        or (region_hint or "").strip()
        or (kw_base or "").strip()
    )
    if requested_page_size > 0 and has_smart_structured_filters:
        visible_target = max(1, page_offset + requested_page_size)
        if has_only_type_filter:
            fetch_lim = min(2400, max(fetch_lim, 2400, visible_target * 400))
        else:
            fetch_lim = min(fetch_lim, max(360, visible_target * 80))
    if fine_grained_region_index_probe and requested_page_size > 0 and not has_smart_structured_filters:
        fetch_lim = max(fetch_lim, min(480, max(int(lim) * 3, 240)))
    if requested_page_size > 0 and not has_smart_structured_filters:
        # The exact total count is now resolved asynchronously by a background endpoint.
        # For the interactive first-page response, only fetch a bounded recent window
        # instead of walking the full region inventory every time.
        visible_target = max(1, page_offset + requested_page_size)
        paged_probe_cap = max(
            720,
            min(
                6000,
                visible_target * (28 if multi_portal else 20),
            ),
        )
        if has_transit_filter:
            paged_probe_cap = max(paged_probe_cap, 1200)
        elif fine_grained_region_index_probe:
            paged_probe_cap = max(paged_probe_cap, 480)
        elif region_hint and (not kw_base or region_scoped_geo_focus_mode):
            paged_probe_cap = max(paged_probe_cap, 960)
        fetch_lim = min(fetch_lim, paged_probe_cap)
    walk_sql = ""
    walk_params: list[Any] = []
    if wmax > 0:
        walk_sql = " AND (COALESCE(c.walk_min,0) = 0 OR COALESCE(c.walk_min,0) <= ?)"
        walk_params = [wmax]

    order_ts_sql = (
        "COALESCE("
        "NULLIF(TRIM(s.published_at), ''), "
        "NULLIF(TRIM(s.last_checked_at), ''), "
        "NULLIF(TRIM(s.crawled_at), ''), "
        "NULLIF(TRIM(c.updated_at), ''), "
        "datetime('now')"
        ")"
    )
    fast_browse_mode = bool(
        broad_inventory_browse
        and not region_hint
        and not kw_base
        and not has_transit_filter
        and not has_smart_structured_filters
    )
    # Fast path: avoid ORDER BY datetime(COALESCE(...)) which forces full scans + sorts.
    # Use source_items.last_checked_at index-order and stop early via LIMIT.
    # Keyword-only searches can still be expensive if we sort by datetime(COALESCE(...)).
    # Prefer source-driven order (last_checked_at index) to keep interactive queries under 0.5s.
    has_transit_identity = bool(sid > 0 or lid > 0)
    drive_from_source = bool(
        fast_browse_mode
        or (region_hint and not drive_from_region_index)
        or has_smart_structured_filters
        or bool(kw_base)
        or (has_transit_filter and not has_transit_identity)
    )
    # For explicit line/station filters, drive FROM content_items so SQLite can use idx_content_jp_station.
    # (Driving from source_items scans many rows before finding matches, especially for line filters.)
    if has_transit_identity:
        drive_from_source = False
    source_index_hint = ""
    if drive_from_source:
        order_clause = "s.last_checked_at DESC, s.id DESC"
        # Use (content_kind, last_checked_at) only when content_kind is constrained.
        if tx_key in ("buy", "rent") and "s.content_kind" in tx_sql:
            source_index_hint = "INDEXED BY idx_source_items_content_kind_last_checked"
        else:
            source_index_hint = "INDEXED BY idx_source_items_last_checked"
        if int(max_age_days or 0) > 0:
            # Prefer a sargable time filter on the same indexed column.
            rec_sql = f"s.last_checked_at >= datetime('now', '-{int(max_age_days)} days')"
            rec_params = []
    elif drive_from_region_index:
        order_clause = "rix.sort_time DESC, rix.source_item_id DESC"
    else:
        order_clause = f"datetime({order_ts_sql}) DESC, c.id DESC"
    # Transit identity filters (line/station) can be highly selective; keep ordering on source recency
    # even when we choose to drive FROM content_items.
    if has_transit_identity:
        order_clause = "s.last_checked_at DESC, s.id DESC"

    transit_sql = ""
    transit_params: list[Any] = []
    streamed_items: list[dict[str, Any]] | None = None
    streamed_filter_meta: dict[str, Any] | None = None
    strict_transit_bound = False
    transit_keyword_relaxed = False
    transit_keyword_before_relax = ""

    # 排除 SUUMO /ms/ 區域匯整頁（無 nc_/jnc_ 物件 id），避免「東京」等關鍵字全洗成列表 hub
    _suumo_ms_guard = (
        "NOT ("
        "instr(lower(COALESCE(s.item_url,'')),'suumo.jp')>0 "
        "AND instr(COALESCE(s.item_url,''),'/ms/')>0 "
        "AND instr(COALESCE(s.item_url,''),'nc_')=0 "
        "AND instr(COALESCE(s.item_url,''),'jnc_')=0"
        ")"
    )

    def _select_sql(extra_kw: str, extra_kw_params: list[Any]) -> tuple[str, list[Any]]:
        if drive_from_region_index:
            from_clause = (
                "FROM jp_listing_region_index rix INDEXED BY idx_jp_listing_region_sort\n"
                "        CROSS JOIN source_items s ON s.id = rix.source_item_id\n"
                "        CROSS JOIN content_items c ON c.source_item_id = s.id"
            )
        elif drive_from_source:
            from_clause = f"FROM source_items s {source_index_hint}\n        CROSS JOIN content_items c ON c.source_item_id = s.id"
        else:
            # When transit identity (line/station) is present, force `content_items` as the outer loop so
            # the jp_station_id index can be used (SQLite would otherwise prefer scanning source_items
            # by last_checked_at and filtering, which is slow for selective transit filters).
            if has_transit_identity:
                from_clause = "FROM content_items c\n        CROSS JOIN source_items s ON s.id = c.source_item_id"
            else:
                from_clause = "FROM content_items c\n        JOIN source_items s ON s.id = c.source_item_id"
        return (
            f"""
        SELECT
          c.id,
          c.seo_slug,
          c.title_zh_hant,
          c.title_zh_hans,
          substr(COALESCE(c.body_zh_hant,''),1,420) AS body_zh_hant,
          substr(COALESCE(c.body_zh_hans,''),1,420) AS body_zh_hans,
          c.region_code,
          c.keyword_type,
          c.topic_category,
          COALESCE(c.case_transaction_override, '') AS case_transaction_override,
          COALESCE(c.case_jp_region_override, '') AS case_jp_region_override,
          COALESCE(c.case_transit_override, '') AS case_transit_override,
          COALESCE(c.jp_station_id, 0) AS jp_station_id,
          COALESCE(c.walk_min, 0) AS walk_min,
          COALESCE(jst.station_name, '') AS jp_bind_station_name,
          COALESCE(jln.line_name, '') AS jp_bind_line_name,
          COALESCE(c.featured_weight, 0) AS featured_weight,
          COALESCE(c.listing_media_json, '[]') AS listing_media_json,
          c.updated_at,
          s.id AS source_item_id,
          s.source_name,
          s.item_url,
          s.title_original,
          substr(COALESCE(s.body_original,''),1,3200) AS body_original,
          substr(COALESCE(s.image_urls,''),1,8000) AS image_urls,
          s.published_at,
          s.crawled_at,
          s.last_checked_at,
          COALESCE(s.content_kind, '') AS content_kind
        {from_clause}
        {region_join_sql}
        LEFT JOIN jp_trans_station jst ON jst.station_id = c.jp_station_id
        LEFT JOIN jp_trans_line jln ON jln.line_id = jst.line_id
        WHERE ({tx_sql})
          AND ({portal_sql})
          AND ({rec_sql})
          AND ({_suumo_ms_guard})
          {region_sql}
          {transit_sql}
          {walk_sql}
          {extra_kw}
        ORDER BY {order_clause}
        LIMIT ?
    """,
            [
                *region_join_params,
                *tx_params,
                *portal_params,
                *rec_params,
                *region_params,
                *transit_params,
                *walk_params,
                *extra_kw_params,
                fetch_lim,
            ],
        )

    def _select_sql_from_fts_source_ids(fts_with_sql: str, fts_with_params: list[Any]) -> tuple[str, list[Any]]:
        from_clause = (
            "FROM fts_source_ids fts\n"
            "        CROSS JOIN source_items s ON s.id = fts.sid\n"
            "        CROSS JOIN content_items c ON c.source_item_id = s.id"
        )
        return (
            f"""
        {fts_with_sql}
        SELECT
          c.id,
          c.seo_slug,
          c.title_zh_hant,
          c.title_zh_hans,
          substr(COALESCE(c.body_zh_hant,''),1,420) AS body_zh_hant,
          substr(COALESCE(c.body_zh_hans,''),1,420) AS body_zh_hans,
          c.region_code,
          c.keyword_type,
          c.topic_category,
          COALESCE(c.case_transaction_override, '') AS case_transaction_override,
          COALESCE(c.case_jp_region_override, '') AS case_jp_region_override,
          COALESCE(c.case_transit_override, '') AS case_transit_override,
          COALESCE(c.jp_station_id, 0) AS jp_station_id,
          COALESCE(c.walk_min, 0) AS walk_min,
          COALESCE(jst.station_name, '') AS jp_bind_station_name,
          COALESCE(jln.line_name, '') AS jp_bind_line_name,
          COALESCE(c.featured_weight, 0) AS featured_weight,
          COALESCE(c.listing_media_json, '[]') AS listing_media_json,
          c.updated_at,
          s.id AS source_item_id,
          s.source_name,
          s.item_url,
          s.title_original,
          substr(COALESCE(s.body_original,''),1,3200) AS body_original,
          substr(COALESCE(s.image_urls,''),1,8000) AS image_urls,
          s.published_at,
          s.crawled_at,
          s.last_checked_at,
          COALESCE(s.content_kind, '') AS content_kind
        {from_clause}
        {region_join_sql}
        LEFT JOIN jp_trans_station jst ON jst.station_id = c.jp_station_id
        LEFT JOIN jp_trans_line jln ON jln.line_id = jst.line_id
        WHERE ({tx_sql})
          AND ({portal_sql})
          AND ({rec_sql})
          AND ({_suumo_ms_guard})
          {region_sql}
          {transit_sql}
          {walk_sql}
        ORDER BY {order_clause}
        LIMIT ?
    """,
            [
                *fts_with_params,
                *region_join_params,
                *tx_params,
                *portal_params,
                *rec_params,
                *region_params,
                *transit_params,
                *walk_params,
                fetch_lim,
            ],
        )

    exact_region_index_page_mode = bool(
        drive_from_region_index
        and requested_page_size > 0
        and not has_smart_structured_filters
        and not kw_base
        and not has_transit_filter
    )

    def _fetch_region_index_page_exact(conn: Any) -> tuple[list[dict[str, Any]], int, bool]:
        region_rec_sql = rec_sql
        region_rec_params = list(rec_params)
        if int(max_age_days or 0) > 0:
            n_days = int(max_age_days or 0)
            source_time_sql = (
                "COALESCE("
                "NULLIF(TRIM(s.published_at), ''), "
                "NULLIF(TRIM(s.last_checked_at), ''), "
                "NULLIF(TRIM(s.crawled_at), ''), "
                "datetime('now')"
                ")"
            )
            region_rec_sql = f"date({source_time_sql}) >= date('now', '-{n_days} days')"
            region_rec_params = []
        count_keys = region_index_keys or [region_hint]
        count_placeholders = ",".join("?" for _ in count_keys)
        fast_count_sql = (
            "SELECT COUNT(*) FROM jp_listing_region_index INDEXED BY idx_jp_listing_region_sort "
            f"WHERE region_key IN ({count_placeholders})"
        )
        fast_count_params: list[Any] = list(count_keys)
        if int(max_age_days or 0) > 0:
            fast_count_sql += " AND sort_time >= datetime('now', ?)"
            fast_count_params.append(f"-{int(max_age_days or 0)} days")
        base_where_page = (
            "FROM jp_listing_region_index rix INDEXED BY idx_jp_listing_region_sort\n"
            "        JOIN source_items s ON s.id = rix.source_item_id\n"
            "        WHERE ({tx_sql})\n"
            "          AND ({portal_sql})\n"
            "          AND ({rec_sql})\n"
            "          AND ({suumo_guard})\n"
            "          {region_sql}"
        ).format(
            tx_sql=tx_sql,
            portal_sql=portal_sql,
            rec_sql=region_rec_sql,
            suumo_guard=_suumo_ms_guard,
            region_sql=region_sql,
        )
        base_params: list[Any] = [
            *tx_params,
            *portal_params,
            *region_rec_params,
            *region_params,
        ]
        total_row = conn.execute(fast_count_sql, fast_count_params).fetchone()
        total_count = int((total_row[0] if total_row else 0) or 0)
        page_sid_sql = f"""
            SELECT rix.source_item_id
            {base_where_page}
            ORDER BY rix.sort_time DESC, rix.source_item_id DESC
            LIMIT ? OFFSET ?
            """
        sid_rows = conn.execute(
            page_sid_sql,
            [*base_params, requested_page_size, page_offset],
        ).fetchall()
        source_ids = [
            int((row[0] if not isinstance(row, dict) else row.get("source_item_id")) or 0)
            for row in sid_rows
        ]
        source_ids = [sid for sid in source_ids if sid > 0]
        if not source_ids:
            return [], total_count, False
        sid_placeholders = ",".join("?" for _ in source_ids)
        order_case = "CASE s.id " + " ".join(
            f"WHEN ? THEN {idx}" for idx, _ in enumerate(source_ids)
        ) + " END"
        rows = conn.execute(
            f"""
            SELECT
              COALESCE(c.id, 0) AS id,
              COALESCE(c.seo_slug, '') AS seo_slug,
              substr(COALESCE(c.title_zh_hant,''),1,420) AS title_zh_hant,
              substr(COALESCE(c.title_zh_hans,''),1,420) AS title_zh_hans,
              substr(COALESCE(c.body_zh_hant,''),1,420) AS body_zh_hant,
              substr(COALESCE(c.body_zh_hans,''),1,420) AS body_zh_hans,
              COALESCE(c.region_code, '') AS region_code,
              COALESCE(c.keyword_type, '') AS keyword_type,
              COALESCE(c.topic_category, '') AS topic_category,
              COALESCE(c.case_transaction_override, '') AS case_transaction_override,
              COALESCE(c.case_jp_region_override, '') AS case_jp_region_override,
              COALESCE(c.case_transit_override, '') AS case_transit_override,
              COALESCE(c.jp_station_id, 0) AS jp_station_id,
              COALESCE(c.walk_min, 0) AS walk_min,
              COALESCE(jst.station_name, '') AS jp_bind_station_name,
              COALESCE(jln.line_name, '') AS jp_bind_line_name,
              COALESCE(c.featured_weight, 0) AS featured_weight,
              COALESCE(c.listing_media_json, '[]') AS listing_media_json,
              COALESCE(c.updated_at, '') AS updated_at,
              s.id AS source_item_id,
              COALESCE(s.source_name, '') AS source_name,
              COALESCE(s.item_url, '') AS item_url,
              COALESCE(s.title_original, '') AS title_original,
              substr(COALESCE(s.body_original,''),1,3200) AS body_original,
              substr(COALESCE(s.image_urls,''),1,8000) AS image_urls,
              COALESCE(s.published_at, '') AS published_at,
              COALESCE(s.crawled_at, '') AS crawled_at,
              COALESCE(s.last_checked_at, '') AS last_checked_at,
              COALESCE(s.content_kind, '') AS content_kind
            FROM source_items s
            LEFT JOIN content_items c ON c.id = (
                SELECT c2.id
                FROM content_items c2
                WHERE c2.source_item_id = s.id
                ORDER BY c2.id DESC
                LIMIT 1
            )
            LEFT JOIN jp_trans_station jst ON jst.station_id = COALESCE(c.jp_station_id, 0)
            LEFT JOIN jp_trans_line jln ON jln.line_id = jst.line_id
            WHERE s.id IN ({sid_placeholders})
            ORDER BY {order_case}
            """,
            [*source_ids, *source_ids],
        ).fetchall()
        return [_row_to_portal_case_item(row) for row in rows], total_count, False

    with get_conn() as conn:
        if exact_region_index_page_mode:
            items, total_count_override, total_count_exact = _fetch_region_index_page_exact(conn)
            tx_out = (transaction or "").strip().lower()
            if tx_out not in ("buy", "sell", "rent"):
                tx_out = "buy"
            return {
                "ok": True,
                "transaction": tx_out,
                "portal": (portal or "").strip().lower(),
                "region_hint": region_hint,
                "keyword": kw_base,
                "jp_line_id": lid,
                "jp_station_id": sid,
                "walk_max": wmax,
                "property_types": smart_property_types,
                "price_min_man": smart_price_min,
                "price_max_man": smart_price_max,
                "layout_min_rooms": smart_layout_min,
                "layout_max_rooms": smart_layout_max,
                "layout_exact_zero": smart_layout_exact_zero,
                "structured_filter_meta": {
                    "property_types": smart_property_types,
                    "price_min_man": smart_price_min,
                    "price_max_man": smart_price_max,
                    "layout_min_rooms": smart_layout_min,
                    "layout_max_rooms": smart_layout_max,
                    "layout_exact_zero": smart_layout_exact_zero,
                },
                "max_age_days": int(max_age_days) if int(max_age_days or 0) > 0 else 0,
                "limit": lim,
                "fetch_limit": requested_page_size,
                "portal_keys": portal_keys,
                "portal_keys_resolved": portal_keys,
                "coverage_matrix_aligned": False,
                "portal_merge_mode": "region_index_exact_page",
                "transit_filter_meta": {
                    "strict_bound": False,
                    "keyword_relaxed": False,
                    "keyword_before_relax": "",
                },
                "count": int(total_count_override or 0),
                "count_exact": bool(total_count_exact),
                "count_provisional": not bool(total_count_exact),
                "truncation_note": False,
                "search_scope_note_zh": "目前使用區域索引快速分頁；總數先用區域索引快速估算，案件頁面按頁即時載入，避免首屏被全量計數拖慢。",
                "broad_inventory_browse": bool(broad_inventory_browse),
                "page_offset": page_offset,
                "page_size": requested_page_size,
                "items": items,
            }

        from src.jp_transit_model import keyword_boost_for_line, keyword_boost_for_station

        # Transit identity filters: prefer strict binding (jp_station_id) when we have any bound inventory.
        # If inventory is still sparse (0 hits), fall back to "keyword boost only" so users still see results.
        if has_transit_identity:
            try:
                if sid > 0:
                    hit = conn.execute(
                        "SELECT 1 FROM content_items WHERE jp_station_id = ? LIMIT 1",
                        (sid,),
                    ).fetchone()
                    if hit:
                        transit_sql = " AND c.jp_station_id = ?"
                        transit_params = [sid]
                elif lid > 0:
                    hit = conn.execute(
                        "SELECT 1 FROM jp_trans_station s JOIN content_items c ON c.jp_station_id = s.station_id WHERE s.line_id = ? LIMIT 1",
                        (lid,),
                    ).fetchone()
                    if hit:
                        transit_sql = " AND jst.line_id = ?"
                        transit_params = [lid]
            except Exception:
                transit_sql = ""
                transit_params = []

        strict_transit_bound = bool(transit_sql)
        transit_boost_text = ""
        try:
            if sid > 0:
                transit_boost_text = keyword_boost_for_station(conn, sid)
            elif lid > 0:
                transit_boost_text = keyword_boost_for_line(conn, lid)
        except Exception:
            transit_boost_text = ""
        merged_kw = kw_base
        try:
            # When we can strictly filter by bound station/line, avoid appending extra boost tokens
            # (FTS implicit-AND makes multi-token "boost" strings overly restrictive).
            if strict_transit_bound and kw_base and transit_boost_text:
                kw_tokens = {str(x or "").strip().lower() for x in _portal_keyword_tokens(kw_base) if str(x or "").strip()}
                boost_tokens = {str(x or "").strip().lower() for x in _portal_keyword_tokens(transit_boost_text) if str(x or "").strip()}
                if kw_tokens and kw_tokens.issubset(boost_tokens):
                    transit_keyword_before_relax = kw_base
                    merged_kw = ""
                    transit_keyword_relaxed = True
            elif not strict_transit_bound and transit_boost_text:
                merged_kw = f"{merged_kw} {transit_boost_text}".strip()
        except Exception:
            merged_kw = kw_base

        kw = merged_kw
        kw_display = kw
        kw_sql = ""
        kw_params: list[Any] = []
        if region_scoped_geo_focus_mode:
            kw = ""
            focus_terms = _simple_geo_focus_like_terms(simple_geo_focus or kw_display)
            if focus_terms:
                fields = (
                    "c.title_zh_hant",
                    "c.title_zh_hans",
                    "c.seo_title",
                    "s.title_original",
                    "s.body_original",
                    "s.item_url",
                )
                clauses: list[str] = []
                for term in focus_terms:
                    like = f"%{term}%"
                    ors = " OR ".join(f"{field} LIKE ?" for field in fields)
                    clauses.append(f"({ors})")
                    kw_params.extend([like] * len(fields))
                if clauses:
                    kw_sql = " AND (" + " OR ".join(clauses) + ")"
        type_candidate_sql, type_candidate_params = _smart_property_type_candidate_sql(smart_property_types)
        if type_candidate_sql and not kw:
            if kw_sql:
                kw_sql = f"{kw_sql} {type_candidate_sql}"
                kw_params.extend(type_candidate_params)
            else:
                kw_sql = type_candidate_sql
                kw_params = list(type_candidate_params)
        if has_only_type_filter:
            try:
                from src.jp_listing_property_type_index import (
                    PROPERTY_TYPE_INDEX_TABLE,
                    ensure_jp_listing_property_type_index_schema,
                    property_type_index_ready,
                )

                ensure_jp_listing_property_type_index_schema(conn)
                if property_type_index_ready(conn):
                    selected_types = _normalize_smart_property_types(smart_property_types)
                    type_marks = ",".join("?" for _ in selected_types)
                    page_lim = requested_page_size if requested_page_size > 0 else lim
                    page_off = page_offset if requested_page_size > 0 else 0
                    index_rec_sql = rec_sql
                    index_rec_params = list(rec_params)
                    if int(max_age_days or 0) > 0:
                        index_rec_sql = (
                            "date(COALESCE("
                            "NULLIF(TRIM(s.published_at), ''), "
                            "NULLIF(TRIM(s.last_checked_at), ''), "
                            "NULLIF(TRIM(s.crawled_at), ''), "
                            "datetime('now')"
                            ")) >= date('now', ?)"
                        )
                        index_rec_params = [f"-{int(max_age_days or 0)} days"]
                    index_where = f"""
                      pti.property_type IN ({type_marks})
                      AND ({tx_sql})
                      AND ({portal_sql})
                      AND ({index_rec_sql})
                      AND ({_suumo_ms_guard})
                    """
                    if requested_page_size > 0:
                        index_where += """
                          AND (
                            TRIM(COALESCE(s.image_urls, '')) <> ''
                            OR TRIM(COALESCE(s.thumbnail_url, '')) <> ''
                            OR EXISTS (
                              SELECT 1
                              FROM content_items cmi
                              WHERE cmi.source_item_id = s.id
                                AND TRIM(COALESCE(cmi.listing_media_json, '')) NOT IN ('', '[]', '{}', 'null', 'None')
                              LIMIT 1
                            )
                          )
                        """
                    index_base_params = [*selected_types, *tx_params, *portal_params, *index_rec_params]
                    total_count = 0
                    total_count_exact = True
                    fetch_page_lim = int(page_lim)
                    source_page_off = int(page_off)
                    if requested_page_size > 0:
                        # For type-only homepage/workbench entry points, the first
                        # page should be instant. Counting tens of thousands of
                        # indexed rows is slower than fetching the visible cards, so
                        # return a provisional window count and let the existing
                        # async count endpoint fill the exact total if needed.
                        total_count_exact = False
                        # Fetch from the start of the indexed media stream through
                        # the requested window, then slice after real gallery
                        # extraction. Some rows have media metadata that later
                        # filters out as non-displayable; slicing raw ids first can
                        # put blank fallback cards on the page or duplicate cards
                        # across page turns.
                        source_page_off = 0
                        fetch_page_lim = max(int(page_lim) + 1, int(page_off) * 20 + int(page_lim) * 30 + 1)
                    else:
                        count_row = conn.execute(
                            f"""
                            SELECT COUNT(DISTINCT pti.source_item_id) AS c
                            FROM {PROPERTY_TYPE_INDEX_TABLE} pti INDEXED BY idx_jp_listing_type_type_time
                            JOIN source_items s ON s.id = pti.source_item_id
                            WHERE {index_where}
                            """,
                            index_base_params,
                        ).fetchone()
                        total_count = int((count_row["c"] if count_row else 0) or 0)
                    if len(selected_types) == 1:
                        source_id_sql = f"""
                        SELECT pti.source_item_id
                        FROM {PROPERTY_TYPE_INDEX_TABLE} pti INDEXED BY idx_jp_listing_type_type_time
                        JOIN source_items s ON s.id = pti.source_item_id
                        WHERE {index_where}
                        ORDER BY pti.source_last_checked_at DESC, pti.source_item_id DESC
                        LIMIT ? OFFSET ?
                        """
                    else:
                        source_id_sql = f"""
                        SELECT pti.source_item_id
                        FROM {PROPERTY_TYPE_INDEX_TABLE} pti INDEXED BY idx_jp_listing_type_type_time
                        JOIN source_items s ON s.id = pti.source_item_id
                        WHERE {index_where}
                        GROUP BY pti.source_item_id
                        ORDER BY MAX(pti.source_last_checked_at) DESC, pti.source_item_id DESC
                        LIMIT ? OFFSET ?
                        """
                    source_id_rows = conn.execute(
                        source_id_sql,
                        [*index_base_params, int(fetch_page_lim), int(source_page_off)],
                    ).fetchall()
                    raw_source_ids = [int(row["source_item_id"] or 0) for row in source_id_rows]
                    source_ids = []
                    seen_source_ids: set[int] = set()
                    for sid in raw_source_ids:
                        if sid <= 0 or sid in seen_source_ids:
                            continue
                        seen_source_ids.add(sid)
                        source_ids.append(sid)
                    has_next_page = requested_page_size > 0 and len(source_ids) > int(page_off) + int(page_lim)
                    index_rows = []
                    if source_ids:
                        sid_marks = ",".join("?" for _ in source_ids)
                        order_case = "CASE s.id " + " ".join(f"WHEN ? THEN {idx}" for idx, _ in enumerate(source_ids)) + " END"
                        index_rows = conn.execute(
                            f"""
                        SELECT
                          c.id,
                          c.seo_slug,
                          c.title_zh_hant,
                          c.title_zh_hans,
                          substr(COALESCE(c.body_zh_hant,''),1,420) AS body_zh_hant,
                          substr(COALESCE(c.body_zh_hans,''),1,420) AS body_zh_hans,
                          c.region_code,
                          c.keyword_type,
                          c.topic_category,
                          COALESCE(c.case_transaction_override, '') AS case_transaction_override,
                          COALESCE(c.case_jp_region_override, '') AS case_jp_region_override,
                          COALESCE(c.case_transit_override, '') AS case_transit_override,
                          COALESCE(c.jp_station_id, 0) AS jp_station_id,
                          COALESCE(c.walk_min, 0) AS walk_min,
                          COALESCE(jst.station_name, '') AS jp_bind_station_name,
                          COALESCE(jln.line_name, '') AS jp_bind_line_name,
                          COALESCE(c.featured_weight, 0) AS featured_weight,
                          COALESCE(c.listing_media_json, '[]') AS listing_media_json,
                          c.updated_at,
                          s.id AS source_item_id,
                          s.source_name,
                          s.item_url,
                          s.title_original,
                          substr(COALESCE(s.body_original,''),1,3200) AS body_original,
                          substr(COALESCE(s.image_urls,''),1,8000) AS image_urls,
                          s.published_at,
                          s.crawled_at,
                          s.last_checked_at,
                          COALESCE(s.content_kind, '') AS content_kind
                        FROM source_items s
                        JOIN content_items c ON c.source_item_id = s.id
                        LEFT JOIN jp_trans_station jst ON jst.station_id = c.jp_station_id
                        LEFT JOIN jp_trans_line jln ON jln.line_id = jst.line_id
                        WHERE s.id IN ({sid_marks})
                        GROUP BY s.id
                        ORDER BY {order_case}
                        """,
                            [*source_ids, *source_ids],
                        ).fetchall()
                    index_limit = int(fetch_page_lim) if requested_page_size > 0 else int(page_lim)
                    if requested_page_size > 0:
                        fast_items = [_row_to_portal_case_item_filter_fast(row) for row in index_rows]
                        if multi_portal:
                            fast_items = _merge_multi_portal_items(fast_items, lim=index_limit)
                        else:
                            fast_items = _dedupe_portal_case_items(fast_items, lim=index_limit)
                        display_items = [item for item in fast_items if _item_has_display_image(item)]
                        slice_source = display_items if display_items else fast_items
                        candidate_ids = [
                            int(item.get("source_item_id") or 0)
                            for item in slice_source[int(page_off) :]
                            if int(item.get("source_item_id") or 0) > 0
                        ]
                        rows_by_sid = {
                            int(dict(row).get("source_item_id") or 0): row
                            for row in index_rows
                            if int(dict(row).get("source_item_id") or 0) > 0
                        }
                        page_items: list[dict[str, Any]] = []
                        fallback_items: list[dict[str, Any]] = []
                        seen_page_keys: set[str] = set()
                        for sid in candidate_ids:
                            row = rows_by_sid.get(sid)
                            if row is None:
                                continue
                            item = _row_to_portal_case_item(row)
                            key = _portal_case_dedupe_key(item) or f"cid:{item.get('content_id')}"
                            if key in seen_page_keys:
                                continue
                            seen_page_keys.add(key)
                            if _item_has_display_image(item):
                                page_items.append(item)
                            else:
                                fallback_items.append(item)
                            if len(page_items) >= int(page_lim):
                                break
                        visible_count = len(slice_source)
                        has_next_page = has_next_page or visible_count > int(page_off) + len(page_items)
                        total_count = int(page_off) + len(page_items) + (1 if has_next_page else 0)
                        index_items = page_items
                    else:
                        index_items = _row_dicts_to_portal_case_items(index_rows)
                        if multi_portal:
                            index_items = _merge_multi_portal_items(index_items, lim=index_limit)
                        else:
                            index_items = _dedupe_portal_case_items(index_items, lim=index_limit)
                        index_items = _prefer_complete_items_for_display(index_items, lim=int(page_lim))
                    return {
                        "ok": True,
                        "transaction": tx_key,
                        "portal": portal,
                        "region_hint": region_hint,
                        "keyword": kw_display,
                        "property_types": smart_property_types,
                        "price_min_man": smart_price_min,
                        "price_max_man": smart_price_max,
                        "layout_min_rooms": smart_layout_min,
                        "layout_max_rooms": smart_layout_max,
                        "layout_exact_zero": smart_layout_exact_zero,
                        "structured_filter_meta": {
                            "property_types": selected_types,
                            "price_min_man": 0,
                            "price_max_man": 0,
                            "layout_min_rooms": 0,
                            "layout_max_rooms": 0,
                            "layout_exact_zero": False,
                            "before_count": total_count,
                            "after_count": total_count,
                            "property_type_index_used": True,
                            "candidate_window_truncated": False,
                        },
                        "max_age_days": int(max_age_days) if int(max_age_days or 0) > 0 else 0,
                        "limit": lim,
                        "fetch_limit": int(page_lim),
                        "portal_keys": portal_keys,
                        "portal_keys_resolved": portal_keys,
                        "coverage_matrix_aligned": False,
                        "portal_merge_mode": "property_type_index",
                        "transit_filter_meta": {
                            "strict_bound": bool(strict_transit_bound),
                            "keyword_relaxed": False,
                            "keyword_before_relax": "",
                        },
                        "count": total_count,
                        "count_exact": bool(total_count_exact),
                        "count_provisional": not bool(total_count_exact),
                        "truncation_note": False,
                        "search_scope_note_zh": (
                            "已使用物件類型索引表，先回傳本頁候選，精確總數背景校準。"
                            if not total_count_exact
                            else "已使用物件類型索引表，數量為全庫類型索引口徑。"
                        ),
                        "broad_inventory_browse": bool(broad_inventory_browse),
                        "page_offset": page_offset if requested_page_size > 0 else 0,
                        "page_size": requested_page_size if requested_page_size > 0 else 0,
                        "items": index_items,
                    }
            except Exception:
                pass
        fts_content_rowid_floor = 0
        fts_source_rowid_floor = 0
        try:
            # Restrict FTS scan to a recent rowid window for interactive queries.
            # Rowid roughly tracks insertion order and is a good-enough proxy for recency.
            if kw:
                mx = conn.execute("SELECT MAX(id) FROM content_items").fetchone()
                max_content_rowid = int((mx[0] if mx else 0) or 0)
                mx = conn.execute("SELECT MAX(id) FROM source_items").fetchone()
                max_source_rowid = int((mx[0] if mx else 0) or 0)
                if max_content_rowid > 0 or max_source_rowid > 0:
                    if has_transit_filter:
                        # Transit boosts can still be broad (e.g., major lines). Keep queries interactive by
                        # clipping to a recent rowid window instead of scanning the full FTS index.
                        window = 65000
                    elif region_hint:
                        window = 48000
                    else:
                        window = 42000
                    if int(window) > 0:
                        # For larger fetch limits, widen the window (but never beyond full table).
                        window = max(window, min(65000, int(fetch_lim) * 12))
                        if max_content_rowid > 0:
                            fts_content_rowid_floor = max(0, max_content_rowid - int(window))
                        if max_source_rowid > 0:
                            fts_source_rowid_floor = max(0, max_source_rowid - int(window))
        except Exception:
            fts_content_rowid_floor = 0
            fts_source_rowid_floor = 0
        if kw:
            fts_candidate_limit = min(20000, max(2400, int(fetch_lim) * 80))
            nmark = _is_jp_address_level_query(kw)
            fts_with_sql = ""
            fts_with_params: list[Any] = []
            try:
                terms = _expand_portal_keyword_search_tokens(kw, narrow_markers=nmark)
                fts_q = _fts5_or_query(terms)
                if fts_q:
                    fts_with_sql, fts_with_params = _fts_source_id_cte_sql(
                        fts_q,
                        content_rowid_floor=fts_content_rowid_floor,
                        source_rowid_floor=fts_source_rowid_floor,
                        candidate_limit=fts_candidate_limit,
                    )
            except Exception:
                fts_with_sql = ""
                fts_with_params = []
            if fts_with_sql:
                sql, params = _select_sql_from_fts_source_ids(fts_with_sql, fts_with_params)
            else:
                kw_sql, kw_params = _keyword_sql_strict(
                    kw,
                    narrow_markers=nmark,
                    fts_content_rowid_floor=fts_content_rowid_floor,
                    fts_source_rowid_floor=fts_source_rowid_floor,
                    fts_candidate_limit=fts_candidate_limit,
                )
                sql, params = _select_sql(kw_sql, kw_params)
        else:
            sql, params = _select_sql(kw_sql, kw_params)
        if has_smart_structured_filters:
            cur = conn.execute(sql, params)
            cur.arraysize = 200
            scanned_rows = 0
            scanned_after_detail = 0
            price_unknown_excluded = 0
            price_unknown_included = 0
            layout_unknown_excluded = 0

            types = _normalize_smart_property_types(smart_property_types)
            pmin = max(0, int(smart_price_min or 0))
            pmax = max(0, int(smart_price_max or 0))
            if pmin > 0 and pmax > 0 and pmax < pmin:
                pmin, pmax = pmax, pmin
            rmin = max(0, int(smart_layout_min or 0))
            rmax = max(0, int(smart_layout_max or 0))
            if rmin > 0 and rmax > 0 and rmax < rmin:
                rmin, rmax = rmax, rmin
            exact_zero = bool(smart_layout_exact_zero)
            price_filter_requested = pmin > 0 or pmax > 0
            # 价格条件不再限制智慧查询结果：仅保留在 meta 中，所有价格/无价格案件均可展示。
            has_price = False
            has_layout = exact_zero or rmin > 0 or rmax > 0
            target_pass = max(lim, min(fetch_lim, max(int(lim * 1.25), 70)))
            type_only_scan = bool(types) and not has_price and not has_layout
            # Type-only searches feed the homepage category cards. Scan the whole
            # bounded candidate window so counts do not collapse to the first few
            # hundred fresh rows; mixed price/layout filters keep the short budget.
            deadline = None if type_only_scan else time.perf_counter() + 0.42
            collect_target = max(lim, page_offset + (requested_page_size if requested_page_size > 0 else lim))

            collected: list[dict[str, Any]] = []
            matched_count = 0
            hit_deadline = False
            while True:
                if deadline is not None and time.perf_counter() >= deadline:
                    hit_deadline = True
                    break
                batch = cur.fetchmany()
                if not batch:
                    break
                scanned_rows += len(batch)
                for rr in batch:
                    if deadline is not None and time.perf_counter() >= deadline:
                        hit_deadline = True
                        break
                    if type_only_scan:
                        probe = _row_to_smart_type_probe(rr)
                        if not _smart_type_probe_is_listing_detail(rr, probe):
                            continue
                        scanned_after_detail += 1
                        if types == ["套房"]:
                            type_matched = _smart_probe_has_studio_like_layout(probe)
                        else:
                            type_matched = _smart_item_matches_property_types(probe, types)
                        if not type_matched:
                            continue
                        if len(collected) < collect_target:
                            it = _row_to_portal_case_item(rr)
                            if not _is_probably_listing_detail_result(it):
                                continue
                            if types == ["套房"] and not _smart_item_matches_property_types(it, types):
                                continue
                            matched_count += 1
                            if type_matched:
                                collected.append(it)
                        else:
                            matched_count += 1
                        continue
                    it = _row_to_portal_case_item(rr)
                    if not _is_probably_listing_detail_result(it):
                        continue
                    scanned_after_detail += 1
                    if types and not _smart_item_matches_property_types(it, types):
                        continue
                    if has_price:
                        price = _smart_item_price_man(it)
                        if price is None or price <= 0:
                            price_unknown_included += 1
                        else:
                            if pmin > 0 and price < pmin:
                                continue
                            if pmax > 0 and price > pmax:
                                continue
                    if has_layout:
                        rooms = _extract_room_count_from_layout_text(
                            " ".join(
                                str(x or "")
                                for x in (
                                    it.get("layout_text_hant"),
                                    it.get("layout_line_jp"),
                                    it.get("title_zh_hant"),
                                    it.get("title_original"),
                                    it.get("snippet_jp"),
                                    it.get("body_zh_hant_preview"),
                                    it.get("body_zh_hans_preview"),
                                )
                                if str(x or "").strip()
                            )
                        )
                        if rooms is None:
                            layout_unknown_excluded += 1
                            continue
                        if exact_zero and rooms != 0:
                            continue
                        if rmin > 0 and rooms < rmin:
                            continue
                        if rmax > 0 and rooms > rmax:
                            continue
                    collected.append(it)
                    if len(collected) >= target_pass:
                        break
                if (not type_only_scan) and len(collected) >= target_pass:
                    break
            streamed_items = collected
            streamed_filter_meta = {
                "property_types": types,
                "price_min_man": pmin,
                "price_max_man": pmax,
                "layout_min_rooms": rmin,
                "layout_max_rooms": rmax,
                "layout_exact_zero": exact_zero,
                "before_count": scanned_after_detail,
                "after_count": matched_count if type_only_scan else len(collected),
                "price_unknown_excluded": price_unknown_excluded,
                "price_unknown_included": price_unknown_included,
                "price_filter_ignored": bool(price_filter_requested),
                "price_filter_requested": bool(price_filter_requested),
                "layout_unknown_excluded": layout_unknown_excluded,
                "scanned_rows": scanned_rows,
                "scanned_after_detail": scanned_after_detail,
                "early_stop": hit_deadline,
                "candidate_window_truncated": scanned_rows >= int(fetch_lim),
            }
            rows = []
            row_fetch_count = int(scanned_rows)
        else:
            rows = conn.execute(sql, params).fetchall()
            row_fetch_count = len(rows)
        if not rows and kw and strict_transit_bound:
            # Some portals omit the exact railway line name in detail text even when the
            # item is already bound to a station/line. Keep the transit identity filter
            # and relax only the redundant keyword so a valid station search does not
            # collapse to zero.
            sql_relaxed, params_relaxed = _select_sql("", [])
            rows = conn.execute(sql_relaxed, params_relaxed).fetchall()
            row_fetch_count = len(rows)
            transit_keyword_relaxed = bool(rows)
            if rows and not transit_keyword_before_relax:
                transit_keyword_before_relax = kw
        if not rows and kw:
            nmark = _is_jp_address_level_query(kw)
            tokens = _portal_keyword_tokens(kw)
            fts_candidate_limit_fb = min(20000, max(2400, int(fetch_lim) * 80))

            def _fts_and_query(toks: list[str]) -> str:
                groups: list[str] = []
                for tok in toks:
                    exp = _expand_portal_keyword_search_tokens(tok, narrow_markers=nmark) or [tok]
                    q = _fts5_or_query(exp)
                    if not q:
                        return ""
                    groups.append(f"({q})" if " OR " in q else q)
                return " AND ".join(groups) if groups else ""
            if len(tokens) >= 2:
                fts_q2 = _fts_and_query(tokens)
                if fts_q2:
                    cte2, cpar2 = _fts_source_id_cte_sql(
                        fts_q2,
                        content_rowid_floor=fts_content_rowid_floor,
                        source_rowid_floor=fts_source_rowid_floor,
                        candidate_limit=fts_candidate_limit_fb,
                    )
                    if cte2:
                        sql2, params2 = _select_sql_from_fts_source_ids(cte2, cpar2)
                        rows = conn.execute(sql2, params2).fetchall()
                        row_fetch_count = len(rows)
                if not rows:
                    kw_sql2, kw_params2 = _keyword_sql_tokens_and(
                        tokens,
                        narrow_markers=nmark,
                        fts_content_rowid_floor=fts_content_rowid_floor,
                        fts_source_rowid_floor=fts_source_rowid_floor,
                    )
                    if kw_sql2:
                        sql2, params2 = _select_sql(kw_sql2, kw_params2)
                        rows = conn.execute(sql2, params2).fetchall()
                        row_fetch_count = len(rows)
            # 仍無資料：逐步放寬（去掉尾端交通／細節 token），避免「區域 + 不動產 + 徒歩10分」全 AND 過嚴
            if not rows and len(tokens) > 2:
                for keep in range(len(tokens) - 1, 1, -1):
                    sub = tokens[:keep]
                    fts_q3 = _fts_and_query(sub)
                    if fts_q3:
                        cte3, cpar3 = _fts_source_id_cte_sql(
                            fts_q3,
                            content_rowid_floor=fts_content_rowid_floor,
                            source_rowid_floor=fts_source_rowid_floor,
                            candidate_limit=fts_candidate_limit_fb,
                        )
                        if cte3:
                            sql3, params3 = _select_sql_from_fts_source_ids(cte3, cpar3)
                            rows = conn.execute(sql3, params3).fetchall()
                            row_fetch_count = len(rows)
                            if rows:
                                break
                    kw_sql3, kw_params3 = _keyword_sql_tokens_and(
                        sub,
                        narrow_markers=nmark,
                        fts_content_rowid_floor=fts_content_rowid_floor,
                        fts_source_rowid_floor=fts_source_rowid_floor,
                    )
                    if not kw_sql3:
                        continue
                    sql3, params3 = _select_sql(kw_sql3, kw_params3)
                    rows = conn.execute(sql3, params3).fetchall()
                    row_fetch_count = len(rows)
                    if rows:
                        break

    tx_out = (transaction or "").strip().lower()
    if tx_out not in ("buy", "sell", "rent"):
        tx_out = "buy"

    used_paged_fast_path = False
    total_count_override: int | None = None
    if streamed_items is None:
        if requested_page_size > 0 and not has_smart_structured_filters:
            items, total_count_override = _paged_portal_case_items_from_rows_fast(
                rows,
                lim=lim,
                offset=page_offset,
                page_size=requested_page_size,
                multi_portal=multi_portal,
            )
            used_paged_fast_path = True
        else:
            items = _row_dicts_to_portal_case_items(rows)
    else:
        if requested_page_size > 0:
            try:
                total_count_override = int((streamed_filter_meta or {}).get("after_count") or len(streamed_items))
            except Exception:
                total_count_override = len(streamed_items)
            items = streamed_items[page_offset : page_offset + requested_page_size]
            used_paged_fast_path = True
        else:
            items = streamed_items
    items = _prefer_complete_items_for_display(items, lim=lim)

    # 區域總覽詞（首都圏/關東等）仍走同一套物件詳情保護，避免入口頁／搜尋頁被套成單一物件卡。
    _region_inventory_terms = {
        "首都圏",
        "首都圈",
        "關東",
        "関東",
        "關西",
        "関西",
        "東京",
        "神奈川",
        "埼玉",
        "千葉",
        "横浜",
        "横滨",
        "川崎",
        "大阪",
        "名古屋",
        "福岡",
    }
    region_inventory_mode = (
        ((region_hint or "").strip() in _region_inventory_terms)
        or ((kw_base or "").strip() in _region_inventory_terms)
        or broad_inventory_browse
    )
    if streamed_items is None and not used_paged_fast_path:
        items = [it for it in items if _is_probably_listing_detail_result(it)]
        items, smart_filter_meta = _apply_smart_structured_filters(
            items,
            property_types=smart_property_types,
            price_min_man=smart_price_min,
            price_max_man=smart_price_max,
            layout_min_rooms=smart_layout_min,
            layout_max_rooms=smart_layout_max,
            layout_exact_zero=smart_layout_exact_zero,
        )
    elif streamed_items is None:
        smart_filter_meta = {
            "property_types": smart_property_types,
            "price_min_man": smart_price_min,
            "price_max_man": smart_price_max,
            "layout_min_rooms": smart_layout_min,
            "layout_max_rooms": smart_layout_max,
            "layout_exact_zero": smart_layout_exact_zero,
        }
    else:
        smart_filter_meta = dict(streamed_filter_meta or {})

    if not used_paged_fast_path and multi_portal:
        items = _merge_multi_portal_items(items, lim=lim)
    elif not used_paged_fast_path:
        items = _dedupe_portal_case_items(items, lim=lim)

    scope_note_zh = ""
    if broad_inventory_browse:
        scope_note_zh = (
            "目前為「日本全域／無關鍵字／未用交通」瀏覽：已略過物件詳情信號過濾，結果筆數接近庫存口徑；"
            "仍套用買賣租與新鮮度。案件管理矩陣未套用買賣租，且「首都圏 562」僅為該區列加總，與全域筆數不是同一比較對象。"
        )
    else:
        scope_note_zh = (
            "已套用物件詳情信號過濾，入口頁／搜尋頁不會套成單一物件卡；列出筆數通常低於後台矩陣。"
            "若要對齊矩陣請勾選「矩陣同口徑」查看庫存口徑。"
        )

    return {
        "ok": True,
        "transaction": tx_out,
        "portal": (portal or "").strip().lower(),
        "region_hint": region_hint,
        "keyword": kw_display,
        "jp_line_id": lid,
        "jp_station_id": sid,
        "walk_max": wmax,
        "property_types": smart_property_types,
        "price_min_man": smart_price_min,
        "price_max_man": smart_price_max,
        "layout_min_rooms": smart_layout_min,
        "layout_max_rooms": smart_layout_max,
        "layout_exact_zero": smart_layout_exact_zero,
        "structured_filter_meta": smart_filter_meta,
        "max_age_days": int(max_age_days) if int(max_age_days or 0) > 0 else 0,
        "limit": lim,
        "fetch_limit": fetch_lim,
        "portal_keys": portal_keys,
        "portal_keys_resolved": portal_keys,
        "coverage_matrix_aligned": False,
        "portal_merge_mode": "global_time_desc" if multi_portal else "sql_time_desc",
        "transit_filter_meta": {
            "strict_bound": bool(strict_transit_bound),
            "keyword_relaxed": bool(transit_keyword_relaxed),
            "keyword_before_relax": transit_keyword_before_relax if transit_keyword_relaxed else "",
        },
        "count": int(total_count_override if total_count_override is not None else len(items)),
        "count_exact": bool(row_fetch_count < fetch_lim),
        "truncation_note": row_fetch_count >= fetch_lim,
        "search_scope_note_zh": scope_note_zh,
        "broad_inventory_browse": bool(broad_inventory_browse),
        "page_offset": page_offset if requested_page_size > 0 else 0,
        "page_size": requested_page_size if requested_page_size > 0 else 0,
        "items": items,
    }
