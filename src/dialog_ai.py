"""Dialog query summary: Gemini + local fallback."""

from __future__ import annotations

import re
from typing import Any

from src.gemini_client import (
    is_llm_configured,
    smart_nav_knowledge_graph_gemini,
    summarize_dialog_query_gemini,
)
from src.smart_nav_intel import dimension_label as smart_nav_dimension_label
from src.link_quality import dedupe_links_by_url, sanitize_dialog_link_label, url_is_low_value_for_link_list
from src.llm_runtime import resolve_llm_provider
from src.jp_real_estate_guidance import guidance_dialog_payload
from src.knowledge_service import knowledge_source_meta, pick_article_title_for_ui


def _rank_search_rows_by_query(query: str, rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Put rows whose title/description overlap query tokens first (offline convenience)."""
    if not rows:
        return []
    q = (query or "").strip().lower()
    toks = [t for t in re.split(r"[\s,，。.;；、/／]+", q) if len(t) >= 2][:10]
    if not toks:
        return list(rows[:limit])

    def score(row: dict[str, Any]) -> int:
        blob = " ".join(
            [
                str(row.get("title_zh_hans") or ""),
                str(row.get("title_zh_hant") or ""),
                str(row.get("seo_title") or ""),
                str(row.get("seo_description") or ""),
                str(row.get("topic_category") or ""),
                str(row.get("intent_target") or ""),
            ]
        ).lower()
        return sum(2 for t in toks if t in blob)

    ranked = sorted(rows, key=score, reverse=True)
    return ranked[:limit]


def _search_row_title_zh(r: dict[str, Any], prefer: str = "hans") -> str:
    hans = (r.get("title_zh_hans") or "").strip()
    hant = (r.get("title_zh_hant") or "").strip()
    seo = (r.get("seo_title") or "").strip()
    p = (prefer or "hans").strip().lower()
    if p == "hant":
        return hant or hans or seo
    if p == "both":
        if hans and hant and hans != hant:
            return f"{hans}（繁：{hant}）"
        return hans or hant or seo
    return hans or hant or seo


def _kb_row_excerpt_zh(r: dict[str, Any], prefer: str = "hans") -> str:
    hans = str(r.get("body_zh_hans_excerpt") or r.get("body_zh_hans") or "").strip()
    hant = str(r.get("body_zh_hant_excerpt") or r.get("body_zh_hant") or "").strip()
    p = (prefer or "hans").strip().lower()
    body = (hant or hans) if p == "hant" else (hans or hant)
    body = re.sub(r"\s+", " ", body)
    return body[:120]


def _append_knowledge_rows(
    *,
    rows: list[dict[str, Any]],
    bullets: list[str],
    links: list[dict[str, str]],
    prefer: str,
    max_rows: int = 4,
) -> None:
    for r in list(rows or [])[:max_rows]:
        meta = knowledge_source_meta(r)
        logo = str(meta.get("platform_logo") or meta.get("platform_label") or "知識").strip()
        mode = str(meta.get("reading_mode") or "").strip()
        badge = str(meta.get("content_badge") or "").strip()
        title = sanitize_dialog_link_label(pick_article_title_for_ui(r, prefer), url=str(r.get("item_url") or ""))
        if not title:
            continue
        prefix_bits = [logo]
        if mode:
            prefix_bits.append(mode)
        if badge:
            prefix_bits.append(badge)
        excerpt = _kb_row_excerpt_zh(r, prefer)
        line = f"【{'｜'.join(prefix_bits)}】{title}"
        if excerpt:
            line += f" — {excerpt}"
        if line and line not in bullets:
            bullets.append(line)
        u = str(r.get("item_url") or r.get("article_path") or "").strip()
        if u and not url_is_low_value_for_link_list(u) and len(links) < 8:
            links.append({"label": f"{logo}｜{title}"[:120], "url": u})


def build_local_dialog_summary(
    query: str,
    search_rows: list[dict[str, Any]],
    kb_rows: list[dict[str, Any]],
    knowledge_zh_variant: str = "hans",
) -> dict[str, Any]:
    bullets: list[str] = []
    links: list[dict[str, str]] = []
    guidance = guidance_dialog_payload(query)
    if guidance:
        bullets.extend([str(x).strip() for x in (guidance.get("bullets") or []) if str(x).strip()][:6])
        links.extend([dict(x) for x in (guidance.get("links") or []) if isinstance(x, dict)][:4])
    pref = (knowledge_zh_variant or "hans").strip().lower()
    if pref not in ("hans", "hant", "both"):
        pref = "hans"
    if kb_rows:
        _append_knowledge_rows(rows=kb_rows, bullets=bullets, links=links, prefer=pref, max_rows=4)
    ranked = _rank_search_rows_by_query(query, list(search_rows or []), limit=8)
    for r in ranked:
        u = (r.get("item_url") or "").strip()
        if u and url_is_low_value_for_link_list(u):
            continue
        t = sanitize_dialog_link_label(_search_row_title_zh(r, pref), url=u)
        desc = (r.get("seo_description") or "").strip()[:100]
        line = (f"{t}" + (f" — {desc}" if desc else "")).strip()
        if line and line not in bullets:
            bullets.append(line)
        if u and len(links) < 8:
            links.append({"label": (t or "来源链接")[:120], "url": u})
    if kb_rows and len(bullets) < 8:
        bullets.append(f"【知识库】近半月另有 {len(kb_rows)} 条摘录；请点下方链接或关键字导览查看与「{query[:24]}」相关的条目。")
    n = len(ranked)
    if guidance:
        title = str(guidance.get("title") or f"「{query}」要點摘要")
        voice = str(guidance.get("voice_script") or "").strip()
        if not voice:
            voice = "。".join(bullets[:4])
        voice += f" 已按相关度补充 {n} 条站内索引；详情见画面链接。"
    else:
        title = f"「{query}」要点摘要（离线）"
        voice = (
            f"关于「{query}」：已按相关度优先列出 {n} 条站内索引。"
            "以下为与检索词较相关的要点，无关条目已略；详情见画面链接。"
        )
    if bullets:
        voice += "重点：" + "；".join(bullets[:4]) + "。"
    return {
        "title": title,
        "bullets": bullets[:8],
        "links": dedupe_links_by_url(links, max_items=8),
        "voice_script": voice[:800],
    }


def run_dialog_ai_summary(
    *,
    query: str,
    search_rows: list[dict[str, Any]],
    kb_rows: list[dict[str, Any]],
    gemini_model: str = "",
    llm_provider: str = "",
    knowledge_zh_variant: str = "hans",
) -> dict[str, Any]:
    model = (gemini_model or "").strip() or None
    prov = resolve_llm_provider((llm_provider or "").strip() or None)
    if is_llm_configured(prov):
        try:
            data = summarize_dialog_query_gemini(
                query=query,
                search_rows=search_rows,
                knowledge_rows=kb_rows,
                model=model,
                provider=prov,
                knowledge_zh_variant=knowledge_zh_variant,
            )
            return _normalize_dialog_payload(
                data, query, search_rows, knowledge_rows=kb_rows, knowledge_zh_variant=knowledge_zh_variant
            )
        except Exception:
            pass
    return build_local_dialog_summary(query, search_rows, kb_rows, knowledge_zh_variant=knowledge_zh_variant)


def _normalize_dialog_payload(
    raw: dict[str, Any],
    query: str,
    search_rows: list[dict[str, Any]],
    *,
    knowledge_rows: list[dict[str, Any]] | None = None,
    knowledge_zh_variant: str = "hans",
) -> dict[str, Any]:
    title = str(raw.get("title") or f"「{query}」智慧摘要").strip()[:200]
    bullets = raw.get("bullets")
    if not isinstance(bullets, list):
        bullets = []
    bullets = [str(x).strip() for x in bullets if str(x).strip()][:8]
    links_out: list[dict[str, str]] = []
    links = raw.get("links")
    if isinstance(links, list):
        for row in links:
            if not isinstance(row, dict):
                continue
            u = str(row.get("url") or "").strip()
            if not u or url_is_low_value_for_link_list(u):
                continue
            lab = sanitize_dialog_link_label(str(row.get("label") or "連結"), url=u)
            links_out.append({"label": lab[:120], "url": u[:2000]})
    pref = (knowledge_zh_variant or "hans").strip().lower()
    if pref not in ("hans", "hant", "both"):
        pref = "hans"
    if not links_out:
        for r in search_rows[:6]:
            u = (r.get("item_url") or "").strip()
            if not u or url_is_low_value_for_link_list(u):
                continue
            lab = sanitize_dialog_link_label(
                (_search_row_title_zh(r, pref) or (r.get("seo_title") or "来源")).strip(),
                url=u,
            )[:120]
            links_out.append({"label": lab, "url": u})
    if len(links_out) < 4 and knowledge_rows:
        extra_bullets: list[str] = []
        _append_knowledge_rows(
            rows=list(knowledge_rows or []),
            bullets=extra_bullets,
            links=links_out,
            prefer=pref,
            max_rows=4,
        )
    voice = str(raw.get("voice_script") or "").strip()
    if not voice:
        voice = "。".join(bullets[:4])[:800] if bullets else f"查詢「{query}」的整理摘要已顯示於畫面。"
    return {
        "title": title,
        "bullets": bullets,
        "links": dedupe_links_by_url(links_out, max_items=8),
        "voice_script": voice[:1200],
    }


def _normalize_smart_nav_graph(
    raw: dict[str, Any],
    *,
    query: str,
    dimension_key: str,
) -> dict[str, Any]:
    one = str(raw.get("one_liner") or "").strip()[:400]
    buyer = str(raw.get("buyer_profile_guess") or "").strip()[:500]
    chain_in = raw.get("conclusion_chain")
    chain: list[dict[str, Any]] = []
    if isinstance(chain_in, list):
        for i, row in enumerate(chain_in[:6]):
            if not isinstance(row, dict):
                continue
            chain.append(
                {
                    "step": int(row.get("step") or i + 1),
                    "heading": str(row.get("heading") or f"第{i + 1}段")[:120],
                    "body": str(row.get("body") or "")[:900],
                    "bridge_next": str(row.get("bridge_next") or "")[:200],
                }
            )
    dg = raw.get("decision_graph") if isinstance(raw.get("decision_graph"), dict) else {}
    nodes_in = dg.get("nodes") if isinstance(dg.get("nodes"), list) else []
    edges_in = dg.get("edges") if isinstance(dg.get("edges"), list) else []
    node_ids: set[str] = set()
    nodes: list[dict[str, str]] = []
    for row in nodes_in[:14]:
        if not isinstance(row, dict):
            continue
        nid = str(row.get("id") or "").strip()[:24]
        lab = str(row.get("label") or "").strip()[:80]
        kind = str(row.get("kind") or "factor").strip()[:24]
        if not nid or not lab:
            continue
        node_ids.add(nid)
        nodes.append({"id": nid, "label": lab, "kind": kind})
    edges: list[dict[str, str]] = []
    for row in edges_in[:16]:
        if not isinstance(row, dict):
            continue
        a = str(row.get("from") or "").strip()[:24]
        b = str(row.get("to") or "").strip()[:24]
        if not a or not b or a not in node_ids or b not in node_ids:
            continue
        edges.append(
            {
                "from": a,
                "to": b,
                "relation": str(row.get("relation") or "supports").strip()[:24],
            }
        )
    rp_in = raw.get("ranked_paths")
    ranked: list[dict[str, Any]] = []
    if isinstance(rp_in, list):
        for row in rp_in[:6]:
            if not isinstance(row, dict):
                continue
            caveats = row.get("caveats")
            clist: list[str] = []
            if isinstance(caveats, list):
                for c in caveats[:4]:
                    if isinstance(c, str) and c.strip():
                        clist.append(c.strip()[:200])
            ranked.append(
                {
                    "rank": int(row.get("rank") or len(ranked) + 1),
                    "title": str(row.get("title") or "").strip()[:120],
                    "score_10": max(1, min(10, int(row.get("score_10") or 5))),
                    "why": str(row.get("why") or "").strip()[:500],
                    "caveats": clist,
                }
            )
    nf = raw.get("noise_filtered")
    noise: list[str] = []
    if isinstance(nf, list):
        for x in nf[:8]:
            if isinstance(x, str) and x.strip():
                noise.append(x.strip()[:200])
    nq = raw.get("next_questions")
    nxt: list[str] = []
    if isinstance(nq, list):
        for x in nq[:5]:
            if isinstance(x, str) and x.strip():
                nxt.append(x.strip()[:160])
    return {
        "ok": True,
        "query": (query or "").strip()[:200],
        "dimension_key": (dimension_key or "").strip()[:40],
        "dimension_label_zh": smart_nav_dimension_label(dimension_key),
        "one_liner": one or f"關於「{query[:40]}」：建議先釐清預算、地區與持有目的，再比對稅費與貸款條件。",
        "buyer_profile_guess": buyer or "資訊不足，建議補充預算區間、預計持有年限與是否自住／出租。",
        "conclusion_chain": chain
        or [
            {
                "step": 1,
                "heading": "先釐清需求",
                "body": "在證據有限下，先確認購屋目的（自住、收租或資產配置）與可承受匯率波動，再進入區域與物件類型比較。",
                "bridge_next": "接著可對照官方稅制與融資條件。",
            }
        ],
        "decision_graph": {"nodes": nodes, "edges": edges},
        "ranked_paths": ranked
        or [
            {
                "rank": 1,
                "title": "保守觀望並補齊資料",
                "score_10": 6,
                "why": "離線模式下證據不足，建議先蒐集貸款與稅費假設再決策。",
                "caveats": ["非法律或稅務建議"],
            }
        ],
        "noise_filtered": noise,
        "next_questions": nxt or ["預算與頭期款比例？", "預計持有幾年？", "是否需申請當地貸款？"],
        "source": "llm",
    }


def build_local_smart_nav_graph(
    query: str,
    *,
    dimension_key: str,
    search_rows: list[dict[str, Any]],
    kb_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    titles = []
    for r in (search_rows or [])[:5]:
        t = str(r.get("seo_title") or r.get("title_zh_hans") or r.get("title_zh_hant") or "").strip()
        if t:
            titles.append(t[:100])
    kb_n = len(kb_rows or [])
    chain = [
        {
            "step": 1,
            "heading": "查詢與證據",
            "body": f"目前以「{query[:60]}」檢索，站內主索引命中 {len(search_rows or [])} 條、近半月知識摘錄約 {kb_n} 條；離線模式無法推論個別財務可行性。",
            "bridge_next": "建議依下列主題逐段核對。",
        },
        {
            "step": 2,
            "heading": "可優先閱讀",
            "body": ("；".join(titles[:3]) if titles else "尚無足夠標題可摘；請擴大關鍵字或先執行知識補齊。"),
            "bridge_next": "比對後再進入風險與費用假設。",
        },
        {
            "step": 3,
            "heading": "決策提醒",
            "body": "日本不動產決策高度依個案契約、稅籍與融資審核；請以官方資料與專業顧問為準。",
            "bridge_next": "",
        },
    ]
    nodes = [
        {"id": "goal", "label": "購屋目標", "kind": "goal"},
        {"id": "tax", "label": "稅費與持有成本", "kind": "factor"},
        {"id": "loan", "label": "融資可行性", "kind": "factor"},
        {"id": "risk", "label": "匯率與空置風險", "kind": "risk"},
    ]
    edges = [
        {"from": "goal", "to": "tax", "relation": "prereq"},
        {"from": "goal", "to": "loan", "relation": "prereq"},
        {"from": "loan", "to": "risk", "relation": "tradeoff"},
    ]
    ranked = [
        {
            "rank": 1,
            "title": "先補資料再比選",
            "score_10": 7,
            "why": "在無 LLM 時以安全預設排序，避免過度推斷。",
            "caveats": ["離線摘要"],
        },
        {
            "rank": 2,
            "title": "同步查官方稅務與登記流程",
            "score_10": 6,
            "why": "可建立可驗證的決策基準。",
            "caveats": ["需自行比對最新公告"],
        },
    ]
    raw = {
        "one_liner": f"「{query[:40]}」：建議以稅費、貸款與區域流動性三軸交叉檢視。",
        "buyer_profile_guess": "離線模式無法細分購屋者畫像；請補充預算與地區。",
        "conclusion_chain": chain,
        "decision_graph": {"nodes": nodes, "edges": edges},
        "ranked_paths": ranked,
        "noise_filtered": ["與查詢無直接關聯的泛用行銷語、重複 SEO 尾綴"],
        "next_questions": ["預算上限？", "東京或大阪優先？", "是否需以租金覆蓋月供？"],
    }
    out = _normalize_smart_nav_graph(raw, query=query, dimension_key=dimension_key)
    out["source"] = "offline"
    return out


def run_smart_nav_graph_ai(
    *,
    query: str,
    dimension_key: str,
    search_rows: list[dict[str, Any]],
    kb_rows: list[dict[str, Any]],
    gemini_model: str = "",
    llm_provider: str = "",
    knowledge_zh_variant: str = "hans",
) -> dict[str, Any]:
    q = (query or "").strip()
    dk = (dimension_key or "").strip()[:40]
    model = (gemini_model or "").strip() or None
    prov = resolve_llm_provider((llm_provider or "").strip() or None)
    dim_zh = smart_nav_dimension_label(dk)
    if is_llm_configured(prov):
        try:
            raw = smart_nav_knowledge_graph_gemini(
                query=q,
                dimension_key=dk,
                dimension_label_zh=dim_zh,
                search_rows=search_rows,
                knowledge_rows=kb_rows,
                model=model,
                provider=prov,
                knowledge_zh_variant=knowledge_zh_variant,
            )
            if isinstance(raw, dict):
                out = _normalize_smart_nav_graph(raw, query=q, dimension_key=dk)
                out["source"] = "llm"
                return out
        except Exception:
            pass
    return build_local_smart_nav_graph(q, dimension_key=dk, search_rows=search_rows, kb_rows=kb_rows)
