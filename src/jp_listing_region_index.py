from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from urllib.parse import unquote, urlparse

from src.case_metadata import JP_AREA_FILTER_LABELS
from src.homes_geo import HOMES_KODATE_CHUKO_PREFS


_AREA_LABELS: frozenset[str] = frozenset(str(x or "").strip() for x in JP_AREA_FILTER_LABELS if str(x or "").strip())


@dataclass(frozen=True)
class _PrefInfo:
    key: str
    label: str
    region: str


_PREFS: tuple[_PrefInfo, ...] = tuple(
    _PrefInfo(str(p.key), str(p.label), str(p.region)) for p in (HOMES_KODATE_CHUKO_PREFS or [])
)
_PREF_KEY_SET: frozenset[str] = frozenset(p.key for p in _PREFS if p.key)
_PREF_REGION_BY_KEY: dict[str, str] = {p.key: p.region for p in _PREFS if p.key and p.region}
_PREF_LABEL_MARKERS: tuple[tuple[str, str, str], ...] = tuple(
    (p.label.casefold(), p.key, p.region) for p in _PREFS if p.label and p.key and p.region
)

# Prefecture/city labels that exist in JP_AREA_FILTER_LABELS.
_PREF_KEY_TO_AREA_LABEL: dict[str, str] = {
    "tokyo": "東京",
    "osaka": "大阪",
    "fukuoka": "福岡",
    "kanagawa": "神奈川",
    "saitama": "埼玉",
    "chiba": "千葉",
}

_KANTO_PREF_KEYS: frozenset[str] = frozenset({"ibaraki", "tochigi", "gunma", "saitama", "chiba", "tokyo", "kanagawa"})
_CAPITAL_PREF_KEYS: frozenset[str] = frozenset(set(_KANTO_PREF_KEYS) | {"yamanashi"})

_REGION_SLUG_TO_LABEL: dict[str, str] = {
    "hokkaido": "北海道",
    "tohoku": "東北",
    "kanto": "關東",
    "koshinetsu": "甲信越",
    "hokuriku": "北陸",
    "tokai": "東海",
    "chugoku": "中國地方",
    "shikoku": "四國",
    "kyushu": "九州",
    "okinawa": "沖繩",
    # SUUMO sometimes uses "kinki" or "kansai" as the region landing segment.
    "kansai": "關西",
    "kinki": "關西",
}

_AREA_MARKERS: dict[str, tuple[str, ...]] = {
    "横滨": ("yokohama", "横浜", "橫濱", "横滨"),
    "川崎": ("kawasaki", "川崎"),
    "名古屋": ("nagoya", "名古屋"),
    "京都市": ("kyoto", "京都市", "京都府", "sc_kyoto", "sa_kyoto", "kyoto-city"),
}


def _safe_url_path_segments(url: str) -> tuple[str, ...]:
    raw = str(url or "").strip()
    if not raw:
        return ()
    try:
        parsed = urlparse(raw)
        path = unquote(parsed.path or "")
    except Exception:
        path = raw
    segs = [s.strip().lower() for s in path.replace("\\", "/").split("/") if s.strip()]
    return tuple(segs)


def infer_region_keys(*, item_url: str, title_original: str = "", body_original: str = "") -> set[str]:
    """Infer `JP_AREA_FILTER_LABELS` region keys for one jp_listing row.

    Designed for fast incremental updates; uses URL slugs + prefecture keys + a small set of city markers.
    """
    url = str(item_url or "").strip()
    if not url:
        return set()
    segs = _safe_url_path_segments(url)
    blob = "\n".join([url, str(title_original or ""), str(body_original or "")[:1800]])
    blob_l = blob.casefold()

    matched: set[str] = set()

    # 1) Region landing slugs (e.g. /shikoku/, /tokai/).
    for seg in segs:
        lbl = _REGION_SLUG_TO_LABEL.get(seg)
        if lbl and lbl in _AREA_LABELS:
            matched.add(lbl)

    # 2) Prefecture keys (e.g. /aichi/, /kagawa/) -> broad regions.
    pref_hits = set(segs) & set(_PREF_KEY_SET)
    for pref_key in pref_hits:
        reg = _PREF_REGION_BY_KEY.get(pref_key)
        if reg and reg in _AREA_LABELS:
            matched.add(reg)
        area = _PREF_KEY_TO_AREA_LABEL.get(pref_key)
        if area and area in _AREA_LABELS:
            matched.add(area)

    # 2b) Prefecture labels in text (e.g. 宮城県 / 東京都) -> broad regions.
    # Many portal item URLs don't include the prefecture slug; the page content usually does.
    for pref_label_cf, pref_key, reg in _PREF_LABEL_MARKERS:
        if pref_label_cf in blob_l:
            if reg in _AREA_LABELS:
                matched.add(reg)
            area = _PREF_KEY_TO_AREA_LABEL.get(pref_key)
            if area and area in _AREA_LABELS:
                matched.add(area)
            pref_hits.add(pref_key)

    # 3) Capital area inference.
    if pref_hits & _KANTO_PREF_KEYS:
        matched.add("關東")
    if pref_hits & _CAPITAL_PREF_KEYS:
        matched.add("首都圏")

    # 4) City/area markers that frequently appear in URLs and titles.
    for area_key, markers in _AREA_MARKERS.items():
        if area_key not in _AREA_LABELS:
            continue
        for m in markers:
            if not m:
                continue
            if m.casefold() in blob_l:
                matched.add(area_key)
                break

    # Safety: ensure outputs are within the configured filter labels.
    return {k for k in matched if k in _AREA_LABELS}


def upsert_jp_listing_region_index(
    conn,
    *,
    source_item_id: int,
    region_keys: Iterable[str],
    sort_time: str = "",
) -> int:
    keys = [str(k or "").strip() for k in region_keys if str(k or "").strip()]
    keys = [k for k in keys if k in _AREA_LABELS]
    if not keys:
        return 0
    sid = int(source_item_id or 0)
    if sid <= 0:
        return 0
    st = str(sort_time or "").strip()
    rows = [(k, sid, st) for k in keys]
    conn.executemany(
        """
        INSERT INTO jp_listing_region_index(region_key, source_item_id, sort_time)
        VALUES (?, ?, ?)
        ON CONFLICT(region_key, source_item_id)
        DO UPDATE SET sort_time = excluded.sort_time
        """,
        rows,
    )
    return len(rows)


def ensure_jp_listing_region_index_for_item(
    conn,
    *,
    source_item_id: int,
    item_url: str,
    title_original: str = "",
    body_original: str = "",
    sort_time: str = "",
) -> int:
    keys = infer_region_keys(item_url=item_url, title_original=title_original, body_original=body_original)
    return upsert_jp_listing_region_index(conn, source_item_id=source_item_id, region_keys=sorted(keys), sort_time=sort_time)
