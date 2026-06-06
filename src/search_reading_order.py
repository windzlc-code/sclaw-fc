"""首頁／站內搜尋結果：合併同主題標題、差異化顯示、AI 閱讀順序建議。"""
from __future__ import annotations

import re
from typing import Any

from src.gemini_client import is_llm_configured, search_reading_order_gemini
from src.link_quality import url_is_low_value_for_link_list
from src.llm_runtime import resolve_llm_provider


def _resolve_item_url(row: dict[str, Any]) -> str:
    ext = str(row.get("item_url") or "").strip()
    slug = str(row.get("seo_slug") or "").strip()
    article_path = f"/article/{slug}" if slug else ""
    if ext and not url_is_low_value_for_link_list(ext):
        return ext[:2000]
    if article_path:
        return article_path
    return f"/case/{int(row.get('source_item_id') or 0)}"


def _norm_title(s: str) -> str:
    t = re.sub(r"\s+", "", (s or "").strip().lower())
    t = re.sub(r"[｜|・·．.]", "", t)
    return t[:200]


def _clip(s: str, n: int) -> str:
    x = re.sub(r"\s+", " ", (s or "").strip())
    return (x[:n] + "…") if len(x) > n else x


def offline_search_reading_order(*, query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """無 LLM：依標題相似度合併、用摘要／主題／地區差異化標籤，並以「較具體標題優先」排序。"""
    q = (query or "").strip()[:200]
    rows: list[dict[str, Any]] = []
    for x in items or []:
        if not isinstance(x, dict):
            continue
        r = dict(x)
        if not str(r.get("resolved_url") or "").strip():
            r["resolved_url"] = _resolve_item_url(r)
        rows.append(r)
    # 先依 URL 去重（保留標題較長者）
    by_url: dict[str, dict[str, Any]] = {}
    for r in rows:
        u = str(r.get("resolved_url") or "").strip()
        key = u.split("#")[0].lower()
        prev = by_url.get(key)
        if not prev or len(str(r.get("seo_title") or "")) > len(str(prev.get("seo_title") or "")):
            by_url[key] = r
    deduped = list(by_url.values())

    def base_title(r: dict[str, Any]) -> str:
        return (str(r.get("seo_title") or r.get("title_zh_hant") or r.get("title_zh_hans") or "結果").strip())[:200]

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in deduped:
        u = str(r.get("resolved_url") or "").strip()
        nk = _norm_title(base_title(r))
        gkey = nk if nk else u.split("#")[0].lower()
        groups.setdefault(gkey, []).append(r)

    ordered: list[dict[str, Any]] = []
    rank = 0

    def sort_key(r: dict[str, Any]) -> tuple[int, str]:
        tlen = len(base_title(r))
        upd = str(r.get("updated_at") or "")
        return (-tlen, upd)

    seen_labels: set[str] = set()

    def _unique_label(candidate: str) -> str:
        lab = candidate.strip()[:200]
        nk = _norm_title(lab)
        k = 0
        while nk in seen_labels and k < 8:
            k += 1
            lab = f"{candidate.strip()[:150]}（變體 {k + 1}）"[:200]
            nk = _norm_title(lab)
        seen_labels.add(nk)
        return lab

    group_list = sorted(groups.values(), key=lambda g: -len(base_title(g[0])))
    for grp in group_list:
        grp_sorted = sorted(grp, key=sort_key)
        for i, r in enumerate(grp_sorted):
            u = str(r.get("resolved_url") or "").strip()
            if not u:
                continue
            base = base_title(r)
            body = str(r.get("body_zh_hant") or r.get("body_zh_hans") or r.get("seo_description") or "")
            topic = str(r.get("topic_category") or "").strip()
            region = str(r.get("region_code") or "").strip()
            if i == 0 and len(grp_sorted) == 1:
                raw_lab = base[:160]
                why = "與本次查詢直接相關的站內條目。"
            elif i == 0:
                raw_lab = base[:160]
                why = "同主題中內容較完整或標題較具體者，優先閱讀。"
            else:
                bits = [x for x in (_clip(body, 36), topic, region) if x]
                suffix = " · ".join(bits[:2]) if bits else f"延伸條目 {i + 1}"
                raw_lab = f"{base[:72]}（{suffix}）"[:180]
                why = "與上一筆標題相近但內容不同，已用摘要／分類區分。"
            rank += 1
            ordered.append({"rank": rank, "url": u, "display_label": _unique_label(raw_lab), "why": why})

    for i, row in enumerate(ordered, start=1):
        row["rank"] = i

    intro = (
        f"已將「{q or '本次查詢'}」命中的來源，依標題相似度合併並加上差異化副標；"
        "相同主體字樣者只保留多個連結時會以摘要／主題區分（離線規則）。"
    )
    return {"ok": True, "intro": intro, "ordered": ordered[:12], "source": "offline"}


def run_search_reading_order_ai(
    *,
    query: str,
    items: list[dict[str, Any]],
    gemini_model: str = "",
    llm_provider: str = "",
) -> dict[str, Any]:
    q = (query or "").strip()[:200]
    rows: list[dict[str, Any]] = []
    for x in items or []:
        if not isinstance(x, dict):
            continue
        r = dict(x)
        if not str(r.get("resolved_url") or "").strip():
            r["resolved_url"] = _resolve_item_url(r)
        rows.append(r)
    if not rows:
        return {"ok": True, "intro": "", "ordered": [], "source": "none"}

    allowed_urls: set[str] = set()
    compact: list[dict[str, Any]] = []
    for r in rows[:14]:
        u = str(r.get("resolved_url") or "").strip()
        if not u:
            continue
        allowed_urls.add(u.split("#")[0].lower())
        compact.append(
            {
                "resolved_url": u[:2000],
                "seo_title": _clip(str(r.get("seo_title") or ""), 200),
                "title_zh_hant": _clip(str(r.get("title_zh_hant") or ""), 120),
                "seo_description": _clip(str(r.get("seo_description") or ""), 220),
                "body_excerpt": _clip(str(r.get("body_zh_hant") or r.get("body_zh_hans") or ""), 260),
                "topic_category": _clip(str(r.get("topic_category") or ""), 60),
                "region_code": _clip(str(r.get("region_code") or ""), 24),
                "keyword_tags": _clip(str(r.get("keyword_tags") or ""), 120),
                "updated_at": _clip(str(r.get("updated_at") or ""), 40),
            }
        )

    if not compact:
        return {"ok": True, "intro": "", "ordered": [], "source": "none"}

    model = (gemini_model or "").strip() or None
    prov = resolve_llm_provider((llm_provider or "").strip() or None)
    if is_llm_configured(prov):
        try:
            raw = search_reading_order_gemini(
                query=q,
                items=compact,
                model=model,
                provider=prov,
            )
            url_map = {str(r.get("resolved_url") or "").strip().split("#")[0].lower(): str(r.get("resolved_url") or "").strip() for r in rows[:14]}
            normalized = _normalize_reading_order_response(
                raw, allowed_urls=allowed_urls, url_canonical=url_map
            )
            if normalized.get("ok") and normalized.get("ordered"):
                normalized["source"] = "llm"
                return normalized
        except Exception:
            pass
    return offline_search_reading_order(query=q, items=rows)


def _normalize_reading_order_response(
    raw: dict[str, Any],
    *,
    allowed_urls: set[str],
    url_canonical: dict[str, str] | None = None,
) -> dict[str, Any]:
    intro = str(raw.get("intro") or "").strip()[:500]
    arr = raw.get("ordered")
    out: list[dict[str, Any]] = []
    canon = url_canonical or {}
    if isinstance(arr, list):
        seen_u: set[str] = set()
        for row in arr[:14]:
            if not isinstance(row, dict):
                continue
            u = str(row.get("url") or "").strip()
            if not u:
                continue
            key = u.split("#")[0].lower()
            if key not in allowed_urls or key in seen_u:
                continue
            seen_u.add(key)
            u = canon.get(key, u)
            lab = str(row.get("display_label") or row.get("label") or "").strip()[:200]
            why = str(row.get("why") or row.get("read_note") or "").strip()[:300]
            rk = int(row.get("rank") or len(out) + 1)
            out.append({"rank": rk, "url": u, "display_label": lab or u[:80], "why": why})
    out.sort(key=lambda x: int(x.get("rank") or 999))
    for i, row in enumerate(out, start=1):
        row["rank"] = i
    if not out:
        return {"ok": False, "intro": "", "ordered": [], "source": "llm_empty"}
    seen_lab: set[str] = set()
    for row in out:
        lab = str(row.get("display_label") or "").strip()
        nk = _norm_title(lab)
        if nk in seen_lab:
            row["display_label"] = f"{lab[:120]}（序 {row.get('rank')}）"[:200]
            nk = _norm_title(str(row.get("display_label") or ""))
        seen_lab.add(nk)
    return {"ok": True, "intro": intro or "以下為智能提序後的閱讀順序（已盡量區分同標題來源）。", "ordered": out, "source": "llm"}
