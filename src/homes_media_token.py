"""Helpers for deriving listing-specific media keys for LIFULL HOME'S (homes.co.jp).

HOMES の物件 URL には `b-<digits>` が含まれます。これは「物件番号（7桁-7桁）」の
ハイフンを取り除いた上で、先頭の 0 を落とした表現です（長さは可変）。

本モジュールは、同一ページ内に混在しがちな「おすすめ物件」縮図を除去するために、
物件番号から当該物件に紐付く画像 URL のキー（token）を導出します。
"""

from __future__ import annotations

import re
from urllib.parse import unquote

_HOMES_LISTING_BID_RE = re.compile(r"(?i)/b-([0-9]{6,})(?:/|\?|$)")
_HOMES_IELOVE_GROUP_RE = re.compile(
    r"cdn-lambda-img\.cloud\.ielove\.jp/image/sale/[^/]+/([0-9]{2,})_([0-9]{2,})_[0-9]{1,3}_",
    re.I,
)


def _deep_unquote(value: str) -> str:
    out = str(value or "")
    for _ in range(3):
        nxt = unquote(out)
        if nxt == out:
            break
        out = nxt
    return out


def homes_ielove_image_group_key(url: str) -> str:
    """Return the ielove photo-group key used by some HOME'S wrapped images."""
    decoded = _deep_unquote(url).lower()
    m = _HOMES_IELOVE_GROUP_RE.search(decoded)
    if not m:
        return ""
    return f"{m.group(1)}_{m.group(2)}"


def homes_is_canonical_listing_image_candidate(url: str) -> bool:
    """Whether URL uses HOME'S canonical image ownership paths."""
    decoded = _deep_unquote(url).lower()
    if "img.homes.jp/" in decoded and "/sale/" in decoded:
        return True
    return bool(re.search(r"/data/[0-9]+/sale/image/[0-9]{7}-", decoded))


def homes_leading_ielove_group_urls(urls: list[str] | tuple[str, ...]) -> list[str]:
    """Keep only the first coherent ielove image group from a HOME'S listing scrape.

    Some HOME'S pages wrap the target listing photos via ielove instead of the
    canonical `img.homes.jp/{company}/sale/{id}` path. The same DOM can also
    include recommended listings. We therefore only trust the first repeated
    group, or a single-image page with no competing ielove group.
    """
    raw_urls = [str(u or "").strip() for u in (urls or []) if str(u or "").strip()]
    first_key = ""
    groups: dict[str, list[str]] = {}
    for u in raw_urls:
        key = homes_ielove_image_group_key(u)
        if not key:
            continue
        if not first_key:
            first_key = key
        groups.setdefault(key, []).append(u)
    if not first_key:
        return []
    first_urls = list(dict.fromkeys(groups.get(first_key) or []))
    competing_count = sum(len(v) for k, v in groups.items() if k != first_key)
    if len(first_urls) < 2 and competing_count:
        return []
    return first_urls


def homes_listing_image_tokens(item_url: str) -> tuple[str, ...]:
    """Return token substrings identifying images belonging to the HOMES listing.

    Returns an empty tuple when the URL is not a HOMES `b-...` listing.
    """
    u = str(item_url or "").strip()
    if not u:
        return ()
    lu = u.lower()
    if "homes.co.jp" not in lu and "homes.jp" not in lu:
        return ()
    m = _HOMES_LISTING_BID_RE.search(lu)
    if not m:
        return ()
    bid = (m.group(1) or "").strip()
    if not bid.isdigit():
        return ()
    if len(bid) > 14:
        bid = bid[-14:]
    if len(bid) < 8:
        return ()
    bid14 = bid.zfill(14)
    pref7 = bid14[:7]
    suf7 = bid14[7:]
    try:
        pref = str(int(pref7))
        suf = str(int(suf7))
    except Exception:
        return ()
    if not pref or not suf:
        return ()

    tokens: list[str] = []
    tokens.append(f"img.homes.jp/{pref}/sale/{suf}/")
    tokens.append(f"/data/{pref}/sale/image/{suf7}-")
    return tuple(dict.fromkeys(tokens))


def filter_homes_listing_image_urls(item_url: str, urls: list[str] | tuple[str, ...]) -> list[str]:
    """Return HOME'S image URLs that match the listing b-id token.

    For non-HOME'S URLs, or HOME'S URLs without a parseable b-id, this only
    performs stable de-duplication. For HOME'S b-id listing URLs, unmatched
    images are treated as related/recommended listing media and removed.
    """
    raw_urls = [str(u or "").strip() for u in (urls or []) if str(u or "").strip()]
    tokens = homes_listing_image_tokens(item_url)
    if not tokens:
        return list(dict.fromkeys(raw_urls))

    out: list[str] = []
    for u in raw_urls:
        hay = _deep_unquote(u).lower()
        if any(tok in hay for tok in tokens):
            out.append(u)
    if out:
        return list(dict.fromkeys(out))

    # No canonical b-id match. If canonical HOME'S images are present, they
    # belong to another listing and must stay rejected. Otherwise allow the
    # first coherent ielove group, which HOME'S uses for some legitimate pages.
    if any(homes_is_canonical_listing_image_candidate(u) for u in raw_urls):
        return []
    return homes_leading_ielove_group_urls(raw_urls)


def merge_homes_listing_image_urls(item_url: str, new_urls: list[str], existing_urls: list[str]) -> list[str]:
    """Merge image URLs while enforcing HOME'S b-id ownership when available."""
    merged = list(dict.fromkeys([*(new_urls or []), *(existing_urls or [])]))
    tokens = homes_listing_image_tokens(item_url)
    if not tokens:
        return merged
    return filter_homes_listing_image_urls(item_url, merged)
