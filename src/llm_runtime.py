"""Runtime LLM settings: DeepSeek / Gemini via OpenAI-compatible chat/completions."""

from __future__ import annotations

import sqlite3
import time
from typing import Literal
from urllib.parse import urlparse

from src.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    GEMINI_MODEL,
    LLM_ACTIVE_PROVIDER,
    LLM_DOCS_URL_DEFAULT,
)
from src.db import get_conn

ProviderId = Literal["deepseek", "gemini"]


def _kv_get(conn, k: str) -> str | None:
    row = conn.execute("SELECT v FROM app_kv WHERE k = ?", (k,)).fetchone()
    if not row:
        return None
    return str(row[0])


def get_kv(key: str, default: str = "") -> str:
    # 讀取也可能撞鎖（並發寫入時）；短重試避免後台儲存偶發失敗。
    for attempt in range(8):
        try:
            with get_conn() as conn:
                v = _kv_get(conn, key)
            return default if v is None else v
        except sqlite3.OperationalError:
            if attempt < 7:
                time.sleep(0.05 * (2**attempt))
                continue
            raise


def set_kv(key: str, value: str) -> None:
    for attempt in range(8):
        try:
            with get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO app_kv (k, v, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, value),
                )
                conn.commit()
            return
        except sqlite3.OperationalError:
            if attempt < 7:
                time.sleep(0.05 * (2**attempt))
                continue
            raise


def delete_kv(key: str) -> None:
    for attempt in range(8):
        try:
            with get_conn() as conn:
                conn.execute("DELETE FROM app_kv WHERE k = ?", (key,))
                conn.commit()
            return
        except sqlite3.OperationalError:
            if attempt < 7:
                time.sleep(0.05 * (2**attempt))
                continue
            raise


def get_active_provider() -> ProviderId:
    v = get_kv("llm_active_provider", "").strip().lower()
    if v in ("deepseek", "gemini"):
        return v  # type: ignore[return-value]
    ap = (LLM_ACTIVE_PROVIDER or "deepseek").strip().lower()
    if ap in ("deepseek", "gemini"):
        return ap  # type: ignore[return-value]
    return "deepseek"


def resolve_llm_provider(explicit: str | None) -> ProviderId:
    e = (explicit or "").strip().lower()
    if e in ("deepseek", "gemini"):
        return e  # type: ignore[return-value]
    return get_active_provider()


def get_chat_credentials(provider: str) -> tuple[str, str, str]:
    """Return (base_url without trailing slash, api_key, default_model_id)."""
    p = (provider or "").strip().lower()
    if p == "deepseek":
        base = (get_kv("deepseek_base_url") or DEEPSEEK_BASE_URL or "").strip().rstrip("/")
        key = (get_kv("deepseek_api_key") or DEEPSEEK_API_KEY or "").strip()
        model = (get_kv("deepseek_model") or DEEPSEEK_MODEL or "deepseek-v3.2").strip()
        return base, key, model
    base = (get_kv("gemini_base_url") or GEMINI_BASE_URL or "").strip().rstrip("/")
    key = (get_kv("gemini_api_key") or GEMINI_API_KEY or "").strip()
    model = (get_kv("gemini_model") or GEMINI_MODEL or "gemini-3-flash").strip()
    return base, key, model


def is_llm_configured(provider: str | None = None) -> bool:
    p = resolve_llm_provider(provider)
    b, k, _ = get_chat_credentials(p)
    return bool(b and k)


def llm_configuration_hint(provider: str | None = None) -> str:
    """Human-readable zh-Hant hint when base URL or API key is missing."""
    p = resolve_llm_provider(provider)
    b, k, _ = get_chat_credentials(p)
    name_zh = "DeepSeek" if p == "deepseek" else "Gemini（OpenAI 相容代理）"
    missing: list[str] = []
    if not b:
        missing.append("Base URL")
    if not k:
        missing.append("API Key")
    env_pair = (
        "DEEPSEEK_BASE_URL、DEEPSEEK_API_KEY"
        if p == "deepseek"
        else "GEMINI_BASE_URL、GEMINI_API_KEY"
    )
    if not missing:
        # 與 is_llm_configured 理論上不同步時的防禦（仍應幫使用者指向設定處）
        if not (b and k):
            return (
                f"{name_zh}（供應商代碼：{p}）無法建立連線：請檢查後台「AI 供應商」是否已儲存，"
                f"或 .env 是否已設定 {env_pair} 並已重啟服務。"
            )
        return ""
    return (
        f"{name_zh}（供應商代碼：{p}）尚未完成設定：缺少 {'、'.join(missing)}。"
        f"請至後台「AI 供應商」分頁填寫並按「儲存 AI 設定」，或在專案根目錄 .env 設定 {env_pair} 後重啟服務。"
        "若介面選了「Gemini」但只填了 DeepSeek，請改選預設供應商或補齊 Gemini 的網址與金鑰。"
    )


def get_llm_docs_url() -> str:
    return (get_kv("llm_docs_url") or LLM_DOCS_URL_DEFAULT or "").strip()


def base_host(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    try:
        return urlparse(u).netloc or u
    except Exception:
        return ""


def mask_secret(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if len(t) <= 8:
        return "********"
    return f"{t[:4]}…{t[-4:]}"


def admin_llm_settings_snapshot() -> dict:
    ap = get_active_provider()
    d_base, d_key, d_model = get_chat_credentials("deepseek")
    g_base, g_key, g_model = get_chat_credentials("gemini")
    return {
        "active_provider": ap,
        "docs_url": get_llm_docs_url() or LLM_DOCS_URL_DEFAULT,
        "deepseek_base_url": d_base,
        "deepseek_model": d_model,
        "deepseek_api_key_masked": mask_secret(d_key),
        "deepseek_api_key_set": bool(d_key),
        "gemini_base_url": g_base,
        "gemini_model": g_model,
        "gemini_api_key_masked": mask_secret(g_key),
        "gemini_api_key_set": bool(g_key),
    }
