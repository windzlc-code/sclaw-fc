import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = BASE_DIR / "exports"
DB_PATH = DATA_DIR / "jp_real_estate.sqlite3"
CONFIG_DIR = BASE_DIR / "config"
SOURCE_REGISTRY_PATH = CONFIG_DIR / "source_registry.json"
CRAWL_SETTINGS_PATH = CONFIG_DIR / "crawl_settings.json"

_DEFAULT_SITE_NAME = "日本不動產全球指定搜尋網"
SEO_PHRASE_ENTRY_DEFAULT = "日本不動產指定查詢入口"
# 首頁橫幅內上方小標（pill）；與 SEO_PHRASE_ENTRY 可分開，避免與副標完全重複。環境變數 SEO_PHRASE_HERO_KICKER 可覆寫。
SEO_PHRASE_HERO_KICKER_DEFAULT = "日本不動產・全球智慧查詢"

SITE_NAME = (os.getenv("SITE_NAME", "") or _DEFAULT_SITE_NAME).strip() or _DEFAULT_SITE_NAME
SITE_URL = os.getenv("SITE_URL", "https://www.manuvip.com")

# 全站 SEO：各頁 meta／頁尾涵蓋此二片語（亦可分別以環境變數覆寫）
SEO_PHRASE_BRAND = (os.getenv("SEO_PHRASE_BRAND", "") or _DEFAULT_SITE_NAME).strip() or _DEFAULT_SITE_NAME
SEO_PHRASE_ENTRY = (os.getenv("SEO_PHRASE_ENTRY", "") or SEO_PHRASE_ENTRY_DEFAULT).strip() or SEO_PHRASE_ENTRY_DEFAULT
SEO_PHRASE_HERO_KICKER = (
    (os.getenv("SEO_PHRASE_HERO_KICKER", "") or SEO_PHRASE_HERO_KICKER_DEFAULT).strip() or SEO_PHRASE_HERO_KICKER_DEFAULT
)
SEO_META_KEYWORDS_BASE = f"{SEO_PHRASE_BRAND},{SEO_PHRASE_ENTRY}"
SEO_DESCRIPTION_SUFFIX = f"{SEO_PHRASE_BRAND}｜{SEO_PHRASE_ENTRY}"


def merge_seo_meta_keywords(*extra: str | None) -> str:
    """合併全站固定關鍵字與頁面額外關鍵字（逗號分隔、去重）。"""
    parts: list[str] = [SEO_META_KEYWORDS_BASE]
    for raw in extra:
        if not raw:
            continue
        s = str(raw).strip()
        if s:
            parts.append(s)
    seen: set[str] = set()
    out: list[str] = []
    for block in parts:
        for tok in block.split(","):
            t = tok.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return ",".join(out)
BRAND_NAME = os.getenv("BRAND_NAME", "")

# OpenAI-compatible Gemini proxy (e.g. one-api / new-api). Set in .env — do not commit secrets.
GEMINI_BASE_URL = (os.getenv("GEMINI_BASE_URL", "") or "").strip().rstrip("/")
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY", "") or "").strip()
GEMINI_MODEL = (os.getenv("GEMINI_MODEL", "gemini-3-flash") or "gemini-3-flash").strip()

# DeepSeek / 兔子等 OpenAI 相容网关（chat/completions）
DEEPSEEK_BASE_URL = (os.getenv("DEEPSEEK_BASE_URL", "https://api.tu-zi.com") or "https://api.tu-zi.com").strip().rstrip("/")
DEEPSEEK_API_KEY = (os.getenv("DEEPSEEK_API_KEY", "") or "").strip()
DEEPSEEK_MODEL = (os.getenv("DEEPSEEK_MODEL", "deepseek-v3.2") or "deepseek-v3.2").strip()

# 預設走哪個供應商：deepseek | gemini（後台 app_kv 可覆寫）
_llm_ap = (os.getenv("LLM_ACTIVE_PROVIDER", "deepseek") or "deepseek").strip().lower()
LLM_ACTIVE_PROVIDER = _llm_ap if _llm_ap in ("deepseek", "gemini") else "deepseek"

# 供應商文件（前端／後台顯示用，可後台覆寫 llm_docs_url）
LLM_DOCS_URL_DEFAULT = (os.getenv("LLM_DOCS_URL", "https://tuzi-api.apifox.cn/343647063e0") or "").strip()

# Google Programmable Search Engine (Custom Search JSON API)
GOOGLE_CSE_API_KEY = (os.getenv("GOOGLE_CSE_API_KEY", "") or "").strip()
GOOGLE_CSE_CX = (os.getenv("GOOGLE_CSE_CX", "") or "").strip()

TARGET_REGIONS = [
    {"code": "tw", "name_zh_hant": "台灣", "name_zh_hans": "台湾"},
    {"code": "hk", "name_zh_hant": "香港", "name_zh_hans": "香港"},
    {"code": "cn", "name_zh_hant": "中國", "name_zh_hans": "中国"},
    {"code": "sg", "name_zh_hant": "新加坡", "name_zh_hans": "新加坡"},
    {"code": "my", "name_zh_hant": "馬來西亞", "name_zh_hans": "马来西亚"},
    {"code": "th", "name_zh_hant": "泰國", "name_zh_hans": "泰国"},
]

DEFAULT_SOURCES = [
    {
        "name": "日本國稅廳",
        "category": "官方資料",
        "url": "https://www.nta.go.jp",
        "note": "不動產稅務、資本利得、租金稅",
        "enabled": True,
    },
    {
        "name": "SUUMO",
        "category": "大型房仲",
        "url": "https://suumo.jp",
        "note": "市場趨勢與租金行情（僅整理摘要與連結）",
        "enabled": True,
    },
    {
        "name": "LIFULL HOME'S",
        "category": "大型房仲",
        "url": "https://www.homes.co.jp",
        "note": "地區分析與物件概況（僅整理摘要與連結）",
        "enabled": True,
    },
    {
        "name": "at home",
        "category": "大型房仲",
        "url": "https://www.athome.co.jp",
        "note": "價格帶與區域觀察（僅整理摘要與連結）",
        "enabled": True,
    },
]

COMPANY_PROFILE = {
    "name": "日本不動產指定查詢入口",
    "service_name": "了解日本不動產",
    "summary": (
        "提供海外買家日本不動產購買支援，包含市場分析、購屋流程說明、"
        "合法合規仲介轉介與專業流程對接。"
    ),
    "service_steps": [
        "需求諮詢：確認預算、需求、投資目標",
        "資訊提供與分析：提供地區行情與風險提醒",
        "媒合日本合法仲介：安排看屋與遠端溝通",
        "專業支援：協助對接流程與注意事項",
    ],
    "address": "東京都豐島區北大塚2-17-4 大塚Career大樓 B1",
    "contact": "mokushin.tokyo@gmail.com",
    "reference_url": "https://www.moon-bears.com/manus/app/views/services.php",
}

COMPANY_TABS = [
    {
        "id": "about",
        "label": "關於本站",
        "title": "服務定位",
        "summary": "結合日本不動產資訊整理與顧問式導引，秉持專業與透明，提供可核對的查詢服務。",
        "bullets": [
            "服務對象：海外買家與資產配置需求者",
            "服務方向：不動產購買支援、流程導引、跨國資訊整理",
            "核心價值：合規、透明、可追溯來源",
        ],
    },
    {
        "id": "real-estate",
        "label": "日本不動產",
        "title": "了解日本不動產",
        "summary": "提供全方位日本不動產購買支援，從市場分析到交易完成，全程專業協助。",
        "bullets": [
            "熱門地區行情分析與風險提醒",
            "購屋流程解說與文件準備建議",
            "合法合規仲介轉介與專業流程對接",
            "代辦節點與時程管理",
        ],
        "steps": [
            "需求諮詢：了解預算、需求與投資目標，提供初步評估。",
            "資訊提供與分析：依需求提供市場資訊與區域分析。",
            "媒合日本在地仲介：安排看屋或遠端了解物件。",
            "專業支援：提供流程與注意事項，協助對接專業機構。",
            "交易完成追蹤：交屋與後續管理重點提醒。",
        ],
    },
    {
        "id": "partners",
        "label": "品牌夥伴",
        "title": "品牌夥伴與合作生態",
        "summary": "整合日本在地專業資源，協助海外客戶以穩健方式完成資訊判讀與購屋決策。",
        "bullets": [
            "日本在地合法仲介與顧問網絡",
            "稅務、法務、貸款資訊對接",
            "跨來源比對機制（官方資料 + 市場平台）",
        ],
    },
    {
        "id": "program",
        "label": "夥伴計畫",
        "title": "夥伴計畫",
        "summary": "提供合作方導流與共同服務模式，建立可長期運作的跨境不動產資訊服務。",
        "bullets": [
            "合作入口：品牌方、顧問方、資訊渠道方",
            "內容協作：主題共編、地區資訊更新、案例共建",
            "流程規範：以公開資料與合規導引為原則",
        ],
    },
    {
        "id": "contact",
        "label": "聯絡我們",
        "title": "聯絡資訊",
        "summary": "歡迎洽詢海外買家日本不動產需求、合作提案與夥伴交流。",
        "bullets": [
            "地址：東京都豐島區北大塚2-17-4 大塚Career大樓 B1",
            "Email：mokushin.tokyo@gmail.com",
            "官方參考頁：https://www.moon-bears.com/manus/app/views/services.php",
        ],
    },
]

KNOWLEDGE_SOURCES = {
    "官方資料": [
        {"name": "日本國稅廳", "url": "https://www.nta.go.jp", "use": "不動產稅務、資本利得稅、租金稅"},
    ],
    "大型房仲網站": [
        {"name": "SUUMO", "url": "https://suumo.jp", "use": "市場趨勢、租金行情、地區分析"},
        {"name": "HOMES", "url": "https://www.homes.co.jp", "use": "地區與物件趨勢"},
        {"name": "AtHome", "url": "https://www.athome.co.jp", "use": "區域行情與價格觀察"},
    ],
    "投資與金融網站": [
        {"name": "樂天不動產", "url": "https://realestate.rakuten.co.jp", "use": "投資與市場觀察"},
        {"name": "野村不動產研究", "url": "https://www.nri.com/jp", "use": "產業研究與趨勢"},
        {"name": "三井不動產報告", "url": "https://www.mitsuifudosan.co.jp", "use": "區域發展與商圈分析"},
    ],
}

LIGHT_KNOWLEDGE_KEYWORDS = [
    "日本 不動產 投資",
    "日本房貸 外國人",
    "Tokyo real estate investment",
]

SEO_ARTICLE_TW = {
    "title": "台灣人買日本房完整流程（2026 最新版）",
    "slug": "tw-buy-japan-property-full-guide",
    "sections": [
        "一、先確認購屋目的：自住、出租、或資產配置。",
        "二、查官方政策：先看公開政策與稅務制度規範。",
        "三、查稅務：國稅廳確認不動產稅、租金稅與資本利得稅。",
        "四、查市場行情：以 SUUMO / HOMES / AtHome 交叉比對區域價格與租金。",
        "五、資金規劃：依外國人條件確認貸款可行性與利率區間。",
        "六、仲介與法務：透過日本合法仲介簽約，並做風險審閱。",
        "七、交屋後管理：租賃管理、報稅節奏、年度收益檢視。",
    ],
}
