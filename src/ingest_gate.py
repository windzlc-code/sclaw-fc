"""
Crawl → DB: optional gate before writing `content_items`.

Env:
  SCLAW_INGEST_GATE_MODE=off|rules|ai
    off   — always write (legacy).
    rules — skip insert/update when heuristics say non–real-estate or too thin.
    ai    — rules must pass, then LLM JSON {admit,score,reason}; if LLM unavailable, rules only.

  SCLAW_INGEST_RULE_MIN_CHARS — min len(zh body) after strip (default 40).
"""

from __future__ import annotations

import os
import re

from src.link_quality import url_is_low_value_for_link_list

# Japanese / Chinese signals for Japan property pages (low false negatives for portal crawls)
_PROPERTY_SIGNALS_JA = (
    "不動産",
    "賃貸",
    "売買",
    "マンション",
    "物件",
    "土地",
    "投資",
    "管理費",
    "共益費",
    "仲介",
    "家賃",
    "契約",
    "元付",
    "客付",
    "利回り",
    "収益",
    "築年",
    "間取り",
    "沿線",
    "駅",
    "坪",
    "㎡",
    "m2",
    "借地",
    "所有権",
)
_PROPERTY_SIGNALS_ZH = (
    "不動產",
    "不动产",
    "房地產",
    "房地产",
    "買房",
    "买房",
    "購屋",
    "租屋",
    "租金",
    "投資",
    "投资",
    "房貸",
    "房贷",
    "稅",
    "税",
    "東京",
    "大阪",
    "京都",
    "福岡",
    "北海道",
    "日本",
    "區域",
    "区域",
    "物件",
    "公寓",
    "一戶建",
    "一户建",
    "中古",
    "新築",
    "塔樓",
    "塔楼",
    "仲介",
    "中介",
)


def ingest_gate_mode() -> str:
    raw = (os.getenv("SCLAW_INGEST_GATE_MODE") or "off").strip().lower()
    if raw in ("off", "rules", "ai"):
        return raw
    return "off"


def ingest_rule_min_chars() -> int:
    try:
        n = int((os.getenv("SCLAW_INGEST_RULE_MIN_CHARS") or "40").strip())
    except ValueError:
        return 40
    return max(20, min(400, n))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def rules_admit_content_item(
    *,
    title_hans: str,
    body_hans: str,
    title_original: str,
    body_original: str,
    content_kind: str,
) -> tuple[bool, str]:
    """Return (admit, reason_code)."""
    ck = (content_kind or "").strip().lower()
    if ck in ("jp_listing", "suumo_faq"):
        return True, "whitelist_content_kind"

    body = _norm(body_hans)
    title = _norm(title_hans)
    if len(body) < ingest_rule_min_chars():
        return False, "body_too_short"

    blob = f"{title}\n{body}\n{_norm(title_original)}\n{_norm(body_original)}"
    blob_lower = blob.lower()
    for tok in _PROPERTY_SIGNALS_JA:
        if tok in blob:
            return True, "signal_ja"
    for tok in _PROPERTY_SIGNALS_ZH:
        if tok in blob or tok.lower() in blob_lower:
            return True, "signal_zh"

    return False, "no_property_keywords"


def ai_admit_content_item(
    *,
    title_hans: str,
    body_hans: str,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[bool, str]:
    """LLM gate; raises on misconfiguration or parse failure — caller should catch."""
    from src.gemini_client import chat_completion, parse_json_object
    from src.llm_runtime import get_chat_credentials, resolve_llm_provider

    use_p = resolve_llm_provider(provider)
    base_url, api_key, _ = get_chat_credentials(use_p)
    if not base_url or not api_key:
        raise RuntimeError("llm_not_configured")

    system = (
        "你是日本不動產站內容審核員。只輸出一個 JSON 物件，鍵名必須為 "
        '{"admit":true或false,"score":0到100的整數,"reason":"20字內簡述"}。'
        "admit=true 僅當文本對「日本不動產：自住／投資／租賃／稅務／貸款／區域市場／交易流程」具備可查閱的實質資訊；"
        "若為明顯無關主題、純導流、重複空泛、或與房地產無關之新聞／廣告，admit=false。"
    )
    user = (
        "請審核以下是否應進入本站知識庫（僅依文本判斷）：\n\n"
        f"【簡中標題】\n{title_hans[:400]}\n\n【簡中正文摘錄】\n{body_hans[:2800]}"
    )
    content = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=0.05,
        timeout_sec=28.0,
        provider=use_p,
        max_tokens=220,
    )
    data = parse_json_object(content)
    if not isinstance(data, dict):
        raise ValueError("invalid_json")
    admit = bool(data.get("admit"))
    reason = str(data.get("reason") or "").strip()[:200]
    score = data.get("score")
    tag = f"ai_score={score}" if score is not None else "ai"
    return admit, f"{tag}:{reason}" if reason else tag


def should_write_content_item(
    *,
    title_hans: str,
    body_hans: str,
    title_original: str,
    body_original: str,
    content_kind: str,
    item_url: str = "",
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> tuple[bool, str]:
    """
    Central gate for pipeline. Returns (write_to_content_items, reason_for_log).
    """
    u = (item_url or "").strip()
    if u and url_is_low_value_for_link_list(u):
        return False, "blocked_url:terms_login_or_non_property_help"

    mode = ingest_gate_mode()
    if mode == "off":
        return True, "gate_off"

    ok, why = rules_admit_content_item(
        title_hans=title_hans,
        body_hans=body_hans,
        title_original=title_original,
        body_original=body_original,
        content_kind=content_kind,
    )
    if not ok:
        return False, f"rules:{why}"

    if mode == "rules":
        return True, f"rules:{why}"

    # mode == "ai"
    prov_e = (os.getenv("SCLAW_INGEST_AI_PROVIDER") or "").strip() or llm_provider
    mod_e = (os.getenv("SCLAW_INGEST_AI_MODEL") or "").strip() or llm_model
    try:
        ok_ai, why_ai = ai_admit_content_item(
            title_hans=title_hans,
            body_hans=body_hans,
            provider=prov_e or None,
            model=mod_e or None,
        )
        if not ok_ai:
            return False, f"ai:{why_ai}"
        return True, f"rules:{why};ai:{why_ai}"
    except Exception as exc:
        # 未設定 API 或逾時：不阻擋入庫，避免爬文全失敗；僅記錄
        return True, f"rules:{why};ai_skipped:{type(exc).__name__}"
