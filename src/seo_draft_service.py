import hashlib
import json
import re
from typing import Any

from slugify import slugify

from src.db import get_conn
from src.gemini_client import (
    PERSONA_CATEGORY_LABELS,
    PERSONA_REGION_LABELS,
    generate_seo_draft_via_gemini,
    is_llm_configured,
)
from src.llm_runtime import resolve_llm_provider
from src.text_utils import to_zh_hans


def _normalize_keyword(keyword: str) -> str:
    return re.sub(r"\s+", " ", (keyword or "").strip())[:120]


def _build_slug(keyword: str, used_slugs: set[str]) -> str:
    base_slug = slugify(keyword, separator="-")
    if not base_slug:
        digest = hashlib.sha1(keyword.encode("utf-8")).hexdigest()[:10]
        base_slug = f"kw-{digest}"
    slug = base_slug
    idx = 2
    while slug in used_slugs:
        slug = f"{base_slug}-{idx}"
        idx += 1
    used_slugs.add(slug)
    return slug


def _build_seo_title(keyword: str) -> str:
    return f"{keyword} | Japan property guide for overseas buyers"


def _build_seo_description(keyword: str) -> str:
    text = (
        f"{keyword}: practical market checks, buying process, tax notes, and risk controls "
        "for cross-border Japan property decisions."
    )
    return text[:170]


def _build_body_zh_hant(keyword: str, channels: str, score: int) -> str:
    lines = [
        f"# {keyword} 實務內容草稿",
        "",
        f"此關鍵字目前累積搜尋量為 {score}，可優先作為內容佈局主題。",
        f"資料來源通道：{channels or 'main_search'}",
        "",
        "## 一、快速判讀重點",
        f"- 核心關鍵字：{keyword}",
        "- 先確認區域供需、價格帶與租售流動性。",
        "- 對照外國買家常見疑問：貸款、稅務、持有成本、法規。",
        "",
        "## 二、實務操作流程",
        "1. 定義搜尋意圖：自住、投資、資產配置。",
        "2. 建立比較表：區域、物件型態、總持有成本。",
        "3. 交叉比對公開來源資料，整理可驗證結論。",
        "4. 與專業顧問確認契約、稅務與交割節點。",
        "",
        "## 三、可直接發布的段落建議",
        "- 段落 A：市場背景與需求變化。",
        "- 段落 B：購買流程與時程安排。",
        "- 段落 C：常見風險與避坑清單。",
        "- 段落 D：FAQ 與行動建議。",
        "",
        "## 四、行動清單",
        "- 準備 3 個可比較區域，建立同格式資料表。",
        "- 蒐集至少 2 個官方資料來源與 2 個平台來源。",
        "- 為每個結論附上可追溯來源連結。",
    ]
    return "\n".join(lines)


def _build_faq_items(keyword: str) -> list[dict[str, str]]:
    return [
        {
            "question": f"{keyword} 最先要看哪些數據？",
            "answer": "先看區域價格帶、成交流動性、租金報酬區間，再看持有成本與稅務。",
        },
        {
            "question": "海外買家如何降低資訊落差？",
            "answer": "以官方資料為基礎，搭配平台數據交叉驗證，並保留來源與更新日期。",
        },
        {
            "question": f"{keyword} 內容要如何規劃才有 SEO 效益？",
            "answer": "主文聚焦完整流程，子文拆分地區、貸款、稅務與案例，並建立 FAQ 區塊。",
        },
        {
            "question": "發布前要做哪些合規檢查？",
            "answer": "確認資料來源可公開引用、避免未授權全文轉載，並清楚標示出處與更新時間。",
        },
    ]


def _build_faq_schema_json(keyword: str) -> str:
    faqs = _build_faq_items(keyword)
    return _faq_schema_json_from_pairs(faqs)


def _faq_schema_json_from_pairs(pairs: list[dict[str, str]]) -> str:
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq["question"],
                "acceptedAnswer": {"@type": "Answer", "text": faq["answer"]},
            }
            for faq in pairs
        ],
    }
    return json.dumps(schema, ensure_ascii=False)


def _sanitize_persona_region(code: str) -> str:
    c = (code or "tw").strip().lower()
    return c if c in PERSONA_REGION_LABELS else "tw"


def _sanitize_persona_category(code: str) -> str:
    c = (code or "finance_workplace").strip().lower()
    return c if c in PERSONA_CATEGORY_LABELS else "finance_workplace"


def fetch_top_keyword_stats(limit: int = 12, min_count: int = 1) -> list[dict[str, Any]]:
    safe_limit = max(1, min(200, int(limit)))
    safe_min_count = max(1, int(min_count))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              keyword,
              SUM(search_count) AS total_count,
              GROUP_CONCAT(DISTINCT channel) AS channels,
              MAX(last_searched_at) AS last_searched_at
            FROM keyword_search_stats
            GROUP BY keyword
            HAVING SUM(search_count) >= ?
            ORDER BY total_count DESC, datetime(last_searched_at) DESC, keyword ASC
            LIMIT ?
            """,
            (safe_min_count, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def generate_seo_drafts(
    limit: int = 12,
    min_count: int = 1,
    *,
    use_gemini: bool = False,
    persona_region: str = "tw",
    persona_category: str = "finance_workplace",
    gemini_model: str = "",
    llm_provider: str = "",
) -> dict[str, Any]:
    rp = resolve_llm_provider((llm_provider or "").strip() or None)
    if use_gemini and not is_llm_configured(rp):
        return {
            "ok": False,
            "error": "llm_not_configured",
            "generated": 0,
            "count": 0,
            "created": 0,
            "updated": 0,
            "items": [],
            "gemini_errors": [],
            "message": f"已勾選使用 AI，但供應商「{rp}」未設定 base URL 與 API Key。請至後台「AI 供應商」或 .env 設定後重啟。",
        }

    region_code = _sanitize_persona_region(persona_region)
    category_code = _sanitize_persona_category(persona_category)
    model_override = (gemini_model or "").strip() or None

    keyword_rows = fetch_top_keyword_stats(limit=limit, min_count=min_count)
    if not keyword_rows:
        return {
            "generated": 0,
            "count": 0,
            "created": 0,
            "updated": 0,
            "items": [],
            "gemini_errors": [],
            "use_gemini": use_gemini,
            "message": "No qualifying keywords found.",
        }

    items: list[dict[str, Any]] = []
    created = 0
    updated = 0
    gemini_errors: list[str] = []
    with get_conn() as conn:
        existing_rows = conn.execute("SELECT keyword, seo_slug FROM seo_draft_items").fetchall()
        existing_keyword_to_slug = {str(r["keyword"]): str(r["seo_slug"]) for r in existing_rows}
        used_slugs = {str(r["seo_slug"]) for r in existing_rows if r["seo_slug"]}

        for row in keyword_rows:
            keyword = _normalize_keyword(str(row.get("keyword") or ""))
            if not keyword:
                continue
            score = int(row.get("total_count") or 0)
            channels = str(row.get("channels") or "")
            existing_slug = existing_keyword_to_slug.get(keyword, "")
            if existing_slug:
                slug = existing_slug
                updated += 1
            else:
                slug = _build_slug(keyword=keyword, used_slugs=used_slugs)
                created += 1

            writer = "template"
            if use_gemini and is_llm_configured(rp):
                try:
                    g = generate_seo_draft_via_gemini(
                        keyword=keyword,
                        score=score,
                        channels=channels,
                        persona_region=region_code,
                        persona_category=category_code,
                        model=model_override,
                        provider=rp,
                    )
                    seo_title = g["seo_title"]
                    seo_description = g["seo_description"]
                    body_zh_hant = g["body_zh_hant"]
                    body_zh_hans = to_zh_hans(body_zh_hant)
                    faq_schema_json = _faq_schema_json_from_pairs(g["faq"])
                    writer = "ai"
                except Exception as exc:  # noqa: BLE001 — per-keyword fallback
                    gemini_errors.append(f"{keyword}: {exc}")
                    seo_title = _build_seo_title(keyword)
                    seo_description = _build_seo_description(keyword)
                    body_zh_hant = _build_body_zh_hant(keyword=keyword, channels=channels, score=score)
                    body_zh_hans = to_zh_hans(body_zh_hant)
                    faq_schema_json = _build_faq_schema_json(keyword)
                    writer = "template_fallback"
            else:
                seo_title = _build_seo_title(keyword)
                seo_description = _build_seo_description(keyword)
                body_zh_hant = _build_body_zh_hant(keyword=keyword, channels=channels, score=score)
                body_zh_hans = to_zh_hans(body_zh_hant)
                faq_schema_json = _build_faq_schema_json(keyword)
            status = "draft"
            conn.execute(
                """
                INSERT INTO seo_draft_items (
                    keyword, seo_slug, seo_title, seo_description, body_zh_hant, body_zh_hans,
                    faq_schema_json, source_channels, keyword_score, status, generated_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(keyword)
                DO UPDATE SET
                    seo_slug = excluded.seo_slug,
                    seo_title = excluded.seo_title,
                    seo_description = excluded.seo_description,
                    body_zh_hant = excluded.body_zh_hant,
                    body_zh_hans = excluded.body_zh_hans,
                    faq_schema_json = excluded.faq_schema_json,
                    source_channels = excluded.source_channels,
                    keyword_score = excluded.keyword_score,
                    status = excluded.status,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    keyword,
                    slug,
                    seo_title,
                    seo_description,
                    body_zh_hant,
                    body_zh_hans,
                    faq_schema_json,
                    channels,
                    score,
                    status,
                ),
            )
            items.append(
                {
                    "keyword": keyword,
                    "seo_slug": slug,
                    "seo_title": seo_title,
                    "keyword_score": score,
                    "source_channels": channels,
                    "writer": writer,
                }
            )
        conn.commit()

    total = len(items)
    msg = f"Generated or updated {total} SEO draft items (new {created}, refreshed {updated})."
    if use_gemini and gemini_errors:
        msg += f" AI 產文失敗 {len(gemini_errors)} 筆，已改用範本草稿。"
    return {
        "generated": total,
        "count": total,
        "created": created,
        "updated": updated,
        "items": items,
        "use_gemini": use_gemini,
        "persona_region": region_code,
        "persona_category": category_code,
        "gemini_errors": gemini_errors,
        "message": msg,
    }


def list_seo_drafts(limit: int = 30, status: str = "") -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit)))
    status_clean = (status or "").strip().lower()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                id, keyword, seo_slug, seo_title, seo_description,
                source_channels, keyword_score, status, generated_at, updated_at
            FROM seo_draft_items
            WHERE (? = '' OR lower(status) = ?)
            ORDER BY keyword_score DESC, datetime(updated_at) DESC, id DESC
            LIMIT ?
            """,
            (status_clean, status_clean, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_seo_draft(slug: str) -> dict[str, Any] | None:
    slug_clean = (slug or "").strip()
    if not slug_clean:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                id, keyword, seo_slug, seo_title, seo_description,
                body_zh_hant, body_zh_hans, faq_schema_json,
                source_channels, keyword_score, status, generated_at, updated_at
            FROM seo_draft_items
            WHERE seo_slug = ?
            """,
            (slug_clean,),
        ).fetchone()
    if not row:
        return None
    return dict(row)
