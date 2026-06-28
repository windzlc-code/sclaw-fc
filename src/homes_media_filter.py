"""HOME'S listing media filters shared by crawlers, search cards and detail pages."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

URL_KEYS = ("url", "src", "image", "img", "image_url", "imageUrl", "largeUrl", "large_url", "thumbnail", "thumb", "original", "href")
TEXT_KEYS = ("alt", "title", "caption", "label", "name", "kind", "type", "category", "note", "source", "cls")


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


def media_entry_url_context(entry: Any) -> tuple[str, str]:
    """Return the first media URL and its human context from a listing_media_json entry."""
    if isinstance(entry, str):
        s = entry.strip()
        return (s if s.startswith("http") else ""), ""
    if not isinstance(entry, dict):
        return "", ""
    url = ""
    parts: list[str] = []
    for key in URL_KEYS:
        val = entry.get(key)
        if val and str(val).strip().startswith("http"):
            url = str(val).strip()
            break
    for key in TEXT_KEYS:
        val = entry.get(key)
        if val:
            parts.append(str(val))
    return url, " ".join(parts)


def is_homes_non_property_asset_url(url: str) -> bool:
    """Reject HOME'S page chrome, action buttons, placeholder images and POI UI assets."""
    raw = str(url or "").strip()
    if not raw:
        return False
    decoded = deep_unquote(raw).lower()
    hay = f"{raw.lower()} {decoded}"
    bad_tokens = (
        "/hrw/assets/",
        "/assets/",
        "/common/",
        "/static/",
        "/pc/img/",
        "/sp/img/",
        "/map/",
        "/icon/",
        "/logo",
        "/sprite",
        "/banner",
        "/bnr/",
        "/button",
        "/btn",
        "btn_",
        "button_",
        "favorite/dialog",
        "dialog-header",
        "certification-badge",
        "homeskun",
        "homes_kun",
        "now_printing",
        "nowprinting",
        "no_photo",
        "noimage",
        "no_image",
        "nophoto",
        "placeholder",
        "dummy",
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
        "ヤマレバー",
        "閲覧履歴",
        "ログイン",
        "部屋情報",
        "お部屋情報",
        "イベント",
    )
    if any(token in hay for token in bad_tokens):
        return True
    dims = re.findall(r"[?&](?:w|h|width|height)=(\d{1,5})", hay)
    if dims and max(int(x) for x in dims) <= 180:
        return True
    return False


def is_homes_non_property_context(context: str) -> bool:
    compact = re.sub(r"\s+", "", str(context or ""))
    if not compact:
        return False
    exact = {
        "部屋情報",
        "お部屋情報",
        "周辺",
        "周邊",
        "ログイン",
        "NowPrinting",
        "ＮｏｗＰｒｉｎｔｉｎｇ",
        "イベント",
        "閲覧履歴",
        "お気に入り",
        "最近見た物件",
        "この物件にお問合せ",
    }
    if compact in exact:
        return True
    bad_tokens = (
        "ヤマレバー",
        "スタッフ写真",
        "店内の様子",
        "店舗の外観",
        "不動産会社情報",
        "株式会社",
        "有限会社",
        "部屋情報",
        "お部屋情報",
        "ログイン",
        "閲覧履歴",
        "Now Printing",
        "NowPrinting",
    )
    return any(token in compact for token in bad_tokens)


def is_homes_non_property_media(url: str, context: str = "") -> bool:
    return is_homes_non_property_asset_url(url) or is_homes_non_property_context(context)


def clean_homes_listing_media_entries(raw: str, *, max_entries: int = 80) -> list[Any]:
    """Parse listing_media_json and keep only entries that are plausible property media."""
    try:
        data = json.loads(str(raw or "[]"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[Any] = []
    seen: set[str] = set()
    for entry in data:
        url, context = media_entry_url_context(entry)
        if not url or is_homes_non_property_media(url, context):
            continue
        key = deep_unquote(url).lower().split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
        if len(out) >= max_entries:
            break
    return out
