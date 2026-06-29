"""Shared portal listing image filters.

The crawler, search cards and case detail pages should agree on what is a
listing-owned image. This module only rejects high-confidence non-listing media:
page chrome, agency/staff photos, placeholders, map/UI assets and known
nearby-facility thumbnails.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlparse

from src.homes_media_filter import is_homes_non_property_media


def deep_unquote(value: str, *, rounds: int = 3) -> str:
    out = str(value or "")
    for _ in range(max(1, rounds)):
        try:
            nxt = unquote(out)
        except Exception:
            break
        if nxt == out:
            break
        out = nxt
    return out


def is_suumo_non_property_image_url(text: str) -> bool:
    """Reject SUUMO page chrome, agency media, thumbnails and nearby-facility photos."""
    s = str(text or "").strip().lower()
    if not s:
        return False
    decoded = deep_unquote(s)
    hay = f"{s} {decoded}"
    if "suumo." not in hay:
        return False
    hard_reject = (
        "jjcommon/img/",
        "/edit/assets/suumo/",
        "front_kaisha",
        "gazo/kaisha",
        "/kaisha/",
        "tantou",
        "staff",
        "statement.gif",
        "bt_header_",
        "tab_bkdt-",
        "btn_bkdt-",
    )
    if any(tok in hay for tok in hard_reject):
        return True
    if re.search(r"/front/gazo/fr/bukken/[^?\s\"']+_s\d+[ot]\.(?:jpe?g|png|webp)", hay):
        return True
    if re.search(r"/front/gazo/fr/bukken/[^?\s\"']+_(?:g|c|r|\d+|s\d+)t\.(?:jpe?g|png|webp)", hay):
        return True
    return False


def is_athome_non_property_image_url(text: str) -> bool:
    s = str(text or "").strip().lower()
    if not s:
        return False
    decoded = deep_unquote(s)
    hay = f"{s} {decoded}"
    if "athome.co.jp" not in hay:
        return False
    if "/image_files/path/" in hay:
        return False
    if any(
        tok in hay
        for tok in (
            "/images/common/",
            "/common/",
            "/assets/",
            "/static/",
            "/shop/",
            "/staff/",
            "/company/",
            "/gyousha/",
            "shop_image",
            "staffphoto",
            "company_image",
            "logo",
            "banner",
            "bnr_",
            "btn_",
            "button",
            "noimage",
            "no_image",
            "loading",
            "placeholder",
        )
    ):
        return True
    return False


def is_yahoo_non_property_image_url(text: str) -> bool:
    s = str(text or "").strip().lower()
    if not s:
        return False
    decoded = deep_unquote(s)
    hay = f"{s} {decoded}"
    if "yimg.jp" not in hay and "yahoo.co.jp" not in hay:
        return False
    try:
        parsed = urlparse(s)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
    except Exception:
        host = ""
        path = ""
    if host.endswith("realestate-pctr.c.yimg.jp") and "/realestate-buy-image/" in path:
        return False
    if any(
        tok in hay
        for tok in (
            "s.yimg.jp/images/realestate/",
            "/common/",
            "/assets/",
            "/logo",
            "/icon",
            "/banner",
            "/btn",
            "button",
            "no_image/noimage",
        )
    ):
        return True
    return False


def is_general_non_property_image_url(text: str) -> bool:
    s = str(text or "").strip().lower()
    if not s:
        return False
    decoded = deep_unquote(s)
    hay = f"{s} {decoded}"
    bad_tokens = (
        "/assets/",
        "/common/",
        "/static/",
        "/map/",
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
        "placeholder",
        "dummy",
        "loading",
        "spacer",
        "pixel",
    )
    if any(tok in hay for tok in bad_tokens):
        return True
    dims = re.findall(r"[?&](?:w|h|width|height)=(\d{1,5})", hay)
    if dims and max(int(x) for x in dims) <= 120:
        return True
    return False


def is_portal_non_property_image_url(url: str, *, item_url: str = "", context: str = "") -> bool:
    """High-confidence portal media rejector used before persisting/displaying images."""
    raw = str(url or "").strip()
    if not raw:
        return True
    try:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
    except Exception:
        host = ""
    bag = f"{raw} {context}"
    if "suumo." in host and is_suumo_non_property_image_url(bag):
        return True
    if ("homes.jp" in host or "homes.co.jp" in host) and is_homes_non_property_media(raw, context):
        return True
    if "athome.co.jp" in host and is_athome_non_property_image_url(bag):
        return True
    if "yimg.jp" in host or "yahoo.co.jp" in host:
        if is_yahoo_non_property_image_url(bag):
            return True
        if host.endswith("realestate-pctr.c.yimg.jp"):
            return False
    if is_general_non_property_image_url(bag):
        return True
    return False


def portal_image_identity(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
        if "suumo." in host and "resizeimage" in path:
            q = dict(parse_qsl(parsed.query, keep_blank_values=True))
            src = deep_unquote(str(q.get("src") or "")).lower()
            if src:
                src = re.sub(r"_(?:g|c|r|\d+|s\d+)t(\.[a-z0-9]+)$", r"_\1", src)
                return f"suumo:{src}"
        if "suumo." in host and "/front/gazo/fr/bukken/" in path:
            norm = re.sub(r"_(?:g|c|r|\d+|s\d+)t(\.[a-z0-9]+)$", r"_\1", path)
            return f"suumo:{norm}"
        if ("homes.jp" in host or "homes.co.jp" in host) and ("image.php" in path or "/smallimg/" in path):
            q = dict(parse_qsl(parsed.query, keep_blank_values=True))
            inner = deep_unquote(str(q.get("file") or q.get("src") or "")).lower()
            if inner:
                return f"homes:{inner}"
        return f"{host}{path}"
    except Exception:
        return raw.lower()


def clean_portal_image_urls(item_url: str, urls: list[str] | tuple[str, ...], *, max_urls: int = 120) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        u = str(raw or "").strip()
        if not u or is_portal_non_property_image_url(u, item_url=item_url):
            continue
        key = portal_image_identity(u) or u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
        if len(out) >= max_urls:
            break
    return out
