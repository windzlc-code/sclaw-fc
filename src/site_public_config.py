"""公開站台網址（DNS／canonical）：優先 app_kv，其次環境變數 SITE_URL。

後台可設定「主要網址」與「別名」（apex/www），用於：
- 所有對外絕對連結（SEO、sitemap、結構化資料、客服 prompt 等）
- CORS 允許來源（與 SCLAW_CORS_ORIGINS、環境 SITE_URL 合併）
"""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlparse

from src.config import SITE_URL
from src.llm_runtime import get_kv, set_kv

_CACHE: dict[str, object] = {"t": 0.0, "primary": "", "aliases": []}
_TTL_SEC = 15.0


def _norm_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def invalidate_site_public_cache() -> None:
    _CACHE["t"] = 0.0


def _parse_aliases_raw(raw: str) -> list[str]:
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            j = json.loads(s)
            if isinstance(j, list):
                return [_norm_base(str(x)) for x in j if _norm_base(str(x))]
        except Exception:
            return []
    out: list[str] = []
    for line in s.splitlines():
        x = _norm_base(line)
        if x:
            out.append(x)
    return out


def _read_cache_unlocked() -> dict[str, object]:
    now = time.monotonic()
    last = float(_CACHE.get("t") or 0)
    if now - last < _TTL_SEC and "primary" in _CACHE:
        return _CACHE
    primary = _norm_base(get_kv("public_site_url") or "")
    aliases = _parse_aliases_raw(get_kv("public_site_url_aliases") or "")
    if not primary:
        primary = _norm_base(SITE_URL)
    _CACHE.clear()
    _CACHE.update(t=now, primary=primary, aliases=aliases)
    return _CACHE


def get_effective_site_url() -> str:
    """Canonical 公開根網址（無尾隨斜線）。用於產生站內絕對連結。"""
    data = _read_cache_unlocked()
    p = _norm_base(str(data.get("primary") or ""))
    return p or "https://www.manuvip.com"


def get_support_avatar_url() -> str:
    """智能客服／站內顧問頭像 URL（/api/assets/support-avatar；?v= 由後台上傳後更新）。"""
    ver = (get_kv("support_avatar_ver") or "").strip() or "1"
    return f"/api/assets/support-avatar?v={ver}"


def get_public_site_aliases() -> list[str]:
    data = _read_cache_unlocked()
    return list(data.get("aliases") or [])


def _www_apex_variants(origin: str) -> list[str]:
    out: list[str] = []
    try:
        p = urlparse(origin)
        h = (p.hostname or "").lower()
        sch = p.scheme
        if not h or sch not in ("http", "https"):
            return out
        if "example.com" in h:
            return out
        root = f"{sch}://{h}"
        if root not in out:
            out.append(root)
        if h.startswith("www.") and len(h) > 4:
            apex = f"{sch}://{h[4:]}"
            if apex not in out:
                out.append(apex)
        elif not h.startswith("www."):
            www = f"{sch}://www.{h}"
            if www not in out:
                out.append(www)
    except Exception:
        pass
    return out


def get_site_public_cors_origins() -> list[str]:
    """瀏覽器跨域請求允許的 Origin 清單（完整 scheme://host，無路徑）。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        x = _norm_base(u)
        if not x or x in seen:
            return
        seen.add(x)
        out.append(x)

    add(get_effective_site_url())
    for a in get_public_site_aliases():
        add(a)
    add(_norm_base(SITE_URL))

    extra: list[str] = []
    for o in list(out):
        extra.extend(_www_apex_variants(o))
    for e in extra:
        add(e)

    raw = os.getenv("SCLAW_CORS_ORIGINS", "").strip()
    if raw:
        for x in raw.split(","):
            add(x.strip())

    return out


def admin_site_dns_snapshot() -> dict[str, str]:
    invalidate_site_public_cache()
    primary_kv = (get_kv("public_site_url") or "").strip()
    aliases_kv = (get_kv("public_site_url_aliases") or "").strip()
    return {
        "public_site_url": primary_kv,
        "public_site_url_aliases": aliases_kv,
        "effective_site_url": get_effective_site_url(),
        "env_fallback_site_url": _norm_base(SITE_URL),
    }


def apply_site_dns_settings(*, public_site_url: str, public_site_url_aliases: str) -> None:
    primary = (public_site_url or "").strip()
    aliases = (public_site_url_aliases or "").strip()
    set_kv("public_site_url", primary)
    set_kv("public_site_url_aliases", aliases)
    invalidate_site_public_cache()
