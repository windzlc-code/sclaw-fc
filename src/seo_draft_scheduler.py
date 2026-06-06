import json
import re
from datetime import datetime, timezone

from slugify import slugify

from src.config import SITE_NAME
from src.site_public_config import get_effective_site_url
from src.db import get_conn
from src.text_utils import to_zh_hans


def _normalize_keyword(keyword: str) -> str:
    return re.sub(r"\s+", " ", (keyword or "").strip())[:120]


def _build_title(keyword: str) -> str:
    return f"{keyword}｜日本房地產海外買家完整指南"


def _build_description(keyword: str) -> str:
    base = (
        f"{keyword} 最新重點：提供海外買家日本房地產市場分析、購屋流程、合法合規仲介對接與風險檢核。"
        "含 FAQ 與操作清單，方便快速決策。"
    )
    return base[:170]


def _build_faq(keyword: str) -> list[dict]:
    return [
        {
            "q": f"{keyword} 適合海外買家先看哪些資料？",
            "a": "建議先看區域行情、可貸款條件、稅務與持有成本，再比對物件所在區的租售供需。",
        },
        {
            "q": "購買日本不動產時，如何確認流程合法合規？",
            "a": "確認仲介與專業機構資格、核對契約條款、保存重要文件，並依當地規範完成交易程序。",
        },
        {
            "q": f"{keyword} 的查詢策略如何提高效率？",
            "a": "先用核心關鍵字查趨勢，再用區域與物件類型做二次篩選，最後交叉驗證多來源資訊。",
        },
    ]


def _faq_schema(title: str, faqs: list[dict], slug: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    schema = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Article",
                "headline": title,
                "datePublished": now,
                "dateModified": now,
                "author": {"@type": "Organization", "name": SITE_NAME},
                "publisher": {"@type": "Organization", "name": SITE_NAME},
                "mainEntityOfPage": f"{get_effective_site_url()}/seo-draft/{slug}",
            },
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": row["q"],
                        "acceptedAnswer": {"@type": "Answer", "text": row["a"]},
                    }
                    for row in faqs
                ],
            },
        ],
    }
    return json.dumps(schema, ensure_ascii=False)


def _build_body_hant(keyword: str, channels: str, score: int) -> str:
    lines = [
        f"【熱門關鍵字草稿】{keyword}",
        "",
        f"此主題近期查詢熱度較高（累積查詢：{score}），建議優先規劃成長尾內容。",
        "",
        "## 一、查詢重點",
        f"- 關鍵字：{keyword}",
        f"- 來源通道：{channels or 'main_search'}",
        "- 建議搭配條件：地區、物件型態、貸款可行性、持有成本。",
        "",
        "## 二、海外買家操作流程",
        "1. 先看市場趨勢：區域供需與價格帶。",
        "2. 再看購屋流程：看房、簽約、交割、持有管理。",
        "3. 最後做合規檢核：文件、稅務、專業機構對接。",
        "",
        "## 三、內容佈局建議",
        "- 主文：完整攻略（制度 + 流程 + 風險）。",
        "- 延伸文：按地區與物件類型拆分專文。",
        "- FAQ：整理常見問題，提升自然搜尋點擊率。",
    ]
    return "\n".join(lines)


def _build_body_hans(body_hant: str) -> str:
    return to_zh_hans(body_hant)


def fetch_hot_keywords(limit: int = 10, min_count: int = 2) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              keyword,
              SUM(search_count) AS total_count,
              GROUP_CONCAT(DISTINCT channel) AS channels
            FROM keyword_search_stats
            GROUP BY keyword
            HAVING SUM(search_count) >= ?
            ORDER BY total_count DESC, keyword ASC
            LIMIT ?
            """,
            (min_count, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def generate_seo_drafts(limit: int = 10, min_count: int = 2) -> dict:
    hot_rows = fetch_hot_keywords(limit=limit, min_count=min_count)
    created = 0
    updated = 0
    items = []
    with get_conn() as conn:
        for row in hot_rows:
            keyword = _normalize_keyword(row.get("keyword", ""))
            if not keyword:
                continue
            score = int(row.get("total_count") or 0)
            channels = row.get("channels") or ""
            slug = slugify(f"seo-{keyword}", separator="-")
            title = _build_title(keyword)
            description = _build_description(keyword)
            faq = _build_faq(keyword)
            body_hant = _build_body_hant(keyword=keyword, channels=channels, score=score)
            body_hans = _build_body_hans(body_hant)
            faq_schema_json = _faq_schema(title=title, faqs=faq, slug=slug)

            existing = conn.execute("SELECT id FROM seo_draft_items WHERE keyword = ?", (keyword,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE seo_draft_items
                    SET seo_slug = ?, seo_title = ?, seo_description = ?,
                        body_zh_hant = ?, body_zh_hans = ?, faq_schema_json = ?,
                        source_channels = ?, keyword_score = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE keyword = ?
                    """,
                    (
                        slug,
                        title,
                        description,
                        body_hant,
                        body_hans,
                        faq_schema_json,
                        channels,
                        score,
                        keyword,
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO seo_draft_items (
                        keyword, seo_slug, seo_title, seo_description,
                        body_zh_hant, body_zh_hans, faq_schema_json,
                        source_channels, keyword_score, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
                    """,
                    (
                        keyword,
                        slug,
                        title,
                        description,
                        body_hant,
                        body_hans,
                        faq_schema_json,
                        channels,
                        score,
                    ),
                )
                created += 1
            items.append({"keyword": keyword, "score": score, "slug": slug, "title": title})
        conn.commit()
    return {"count": len(items), "created": created, "updated": updated, "items": items}


def list_seo_drafts(limit: int = 30, status: str = "") -> list[dict]:
    status_clean = (status or "").strip().lower()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              id, keyword, seo_slug, seo_title, seo_description,
              source_channels, keyword_score, status, generated_at, updated_at
            FROM seo_draft_items
            WHERE (? = '' OR status = ?)
            ORDER BY keyword_score DESC, datetime(updated_at) DESC, id DESC
            LIMIT ?
            """,
            (status_clean, status_clean, limit),
        ).fetchall()
    return [dict(r) for r in rows]
