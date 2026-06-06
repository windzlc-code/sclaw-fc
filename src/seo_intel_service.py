"""Google CSE + Gemini: real-estate / finance / loan intel for SEO keyword board."""

from __future__ import annotations

import re
from typing import Any

from src.gemini_client import (
    intel_reading_list_from_google_hits,
    is_llm_configured,
)
from src.llm_runtime import resolve_llm_provider
from src.google_cse_client import is_google_cse_configured, search_cse
from src.keyword_tracking import track_keyword_search

# Seed queries: Japan real estate + finance + loans (JP / zh / en mix for broader hits)
DEFAULT_INTEL_QUERIES: list[str] = [
    "日本 不動産 住宅ローン 金利",
    "外国人 日本 不動産 融資 審査",
    "不動産投資 ローン 返済",
    "日本 不動產 貸款 外國人",
    "日本 房貸 利率 不動產",
    "住宅ローン 変動金利 固定金利 2026",
    "不動産担保融資 個人事業主",
    "Japan mortgage foreign buyer property",
    "不動産 金融機関 融資 条件",
    "投資用不動産 ローン 自己資金",
    "リフォーム ローン 不動産担保",
    "不動産取得税 住宅ローン控除",
]


def _parse_custom_queries(raw: list[Any] | None) -> list[str]:
    out: list[str] = []
    seen_lower: set[str] = set()
    for x in raw or []:
        q = re.sub(r"\s+", " ", str(x).strip())[:200]
        if not q:
            continue
        key = q.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        out.append(q)
    return out


def _merge_seed_queries(
    custom: list[str],
    *,
    include_default_queries: bool,
    max_total: int,
) -> tuple[list[str], set[str]]:
    """Returns (queries to run, set of strings that came from user list)."""
    merged: list[str] = []
    seen_lower: set[str] = set()
    custom_set: set[str] = set()

    for q in custom:
        key = q.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        merged.append(q)
        custom_set.add(q)

    if include_default_queries:
        for d in DEFAULT_INTEL_QUERIES:
            key = d.lower()
            if key in seen_lower:
                continue
            seen_lower.add(key)
            merged.append(d)

    cap = max(1, min(24, int(max_total)))
    return merged[:cap], custom_set


def _dedupe_hits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        link = (row.get("link") or "").strip()
        key = link or f"t:{row.get('title','')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def run_google_finance_property_intel(
    *,
    max_seed_queries: int = 8,
    results_per_query: int = 10,
    ingest_seed_queries: bool = True,
    run_gemini: bool = True,
    persona_region: str = "tw",
    gemini_model: str = "",
    llm_provider: str = "",
    custom_queries: list[str] | None = None,
    include_default_queries: bool = True,
) -> dict[str, Any]:
    if not is_google_cse_configured():
        return {
            "ok": False,
            "error": "google_cse_not_configured",
            "message": "未設定 Google 可程式化搜尋：請在 .env 設定 GOOGLE_CSE_API_KEY 與 GOOGLE_CSE_CX（Search engine ID）。",
        }

    results_per_query = max(1, min(10, int(results_per_query)))

    custom_parsed = _parse_custom_queries(custom_queries)
    if not custom_parsed and not include_default_queries:
        return {
            "ok": False,
            "error": "no_queries",
            "message": "請在「自訂查詢清單」至少輸入一組關鍵字，或勾選「併用內建種子查詢」。",
        }

    queries, custom_set = _merge_seed_queries(
        custom_parsed,
        include_default_queries=include_default_queries,
        max_total=max_seed_queries,
    )
    if not queries:
        return {
            "ok": False,
            "error": "no_queries",
            "message": "沒有可執行的查詢字串，請檢查自訂清單或內建種子設定。",
        }

    aggregated: list[dict[str, Any]] = []

    for q in queries:
        if ingest_seed_queries:
            seed_src = "user_list" if q in custom_set else "default_seeds"
            track_keyword_search(
                q,
                "google_cse_seed",
                {"topic": "real_estate_finance_loan", "seed_source": seed_src},
            )
        try:
            batch = search_cse(q, num=results_per_query, start=1)
        except Exception as exc:  # noqa: BLE001
            aggregated.append(
                {
                    "title": f"[CSE 錯誤] {q}",
                    "link": "",
                    "snippet": str(exc),
                    "displayLink": "",
                    "query_used": q,
                    "error": True,
                }
            )
            continue
        for it in batch:
            aggregated.append({**it, "query_used": q, "error": False})

    hits = _dedupe_hits([h for h in aggregated if not h.get("error")])
    errors = [h for h in aggregated if h.get("error")]

    gemini_analysis: dict[str, Any] | None = None
    gemini_skipped: str | None = None
    keywords_ingested = 0
    rp = resolve_llm_provider((llm_provider or "").strip() or None)

    if run_gemini:
        if not is_llm_configured(rp):
            gemini_skipped = f"AI（{rp}）未設定 API，已略過分析。請於後台「AI 供應商」或 .env 設定。"
        elif not hits:
            gemini_skipped = "沒有有效搜尋結果可分析。"
        else:
            model_use = (gemini_model or "").strip() or None
            try:
                gemini_analysis = intel_reading_list_from_google_hits(
                    hits=hits[:36],
                    persona_region=persona_region,
                    model=model_use,
                    provider=rp,
                )
            except Exception as exc:  # noqa: BLE001
                gemini_analysis = {"error": str(exc), "reading_list": [], "suggested_seo_keywords": []}
                gemini_skipped = f"AI（{rp}）分析失敗：{exc}"
            else:
                seen_kw: set[str] = set()

                def _ingest_kw(k: str, topic: str) -> None:
                    nonlocal keywords_ingested
                    k = k.strip()
                    if not k or k in seen_kw:
                        return
                    seen_kw.add(k)
                    track_keyword_search(k, "google_gemini_intel", {"topic": topic})
                    keywords_ingested += 1

                raw_kws = gemini_analysis.get("suggested_seo_keywords") or []
                if isinstance(raw_kws, list):
                    for kw in raw_kws:
                        if isinstance(kw, str):
                            _ingest_kw(kw, "real_estate_finance_loan")

                rl = gemini_analysis.get("reading_list") or []
                if isinstance(rl, list):
                    for item in rl:
                        if not isinstance(item, dict):
                            continue
                        for kw in item.get("keywords_zh") or []:
                            if isinstance(kw, str):
                                _ingest_kw(kw, "reading_list")

    lite_hits = [
        {k: v for k, v in h.items() if k in ("title", "link", "snippet", "displayLink", "query_used")}
        for h in hits[:50]
    ]

    return {
        "ok": True,
        "queries_used": queries,
        "custom_queries_used": [q for q in queries if q in custom_set],
        "include_default_queries": include_default_queries,
        "google_hits_count": len(hits),
        "google_items": lite_hits,
        "cse_errors": [{"query": e.get("query_used"), "snippet": (e.get("snippet") or "")[:500]} for e in errors],
        "gemini_analysis": gemini_analysis,
        "gemini_skipped": gemini_skipped,
        "keywords_ingested": keywords_ingested,
        "message": "Google 擷取完成"
        + (f"；已寫入約 {keywords_ingested} 組關鍵字到熱門統計。" if keywords_ingested else "。"),
    }
