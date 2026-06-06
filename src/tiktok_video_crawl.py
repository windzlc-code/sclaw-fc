"""TikTok video metadata crawler for social-video knowledge ingestion."""

from __future__ import annotations

import html
import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx


TIKTOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
}


def _safe_text(value: Any, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[: max(1, int(limit))]


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk_dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_dicts(v)


def _parse_universal_data(page_html: str) -> dict[str, Any]:
    m = re.search(
        r'<script[^>]+id=["\']__UNIVERSAL_DATA_FOR_REHYDRATION__["\'][^>]*>(.*?)</script>',
        page_html or "",
        re.S | re.I,
    )
    if not m:
        return {}
    try:
        return json.loads(html.unescape(m.group(1)))
    except Exception:
        return {}


def _find_item_struct(data: dict[str, Any], video_id: str = "") -> dict[str, Any]:
    direct = (
        data.get("__DEFAULT_SCOPE__", {})
        .get("webapp.video-detail", {})
        .get("itemInfo", {})
        .get("itemStruct", {})
    )
    if isinstance(direct, dict) and (direct.get("id") or direct.get("desc")):
        return direct
    for d in _walk_dicts(data):
        if not isinstance(d, dict):
            continue
        if video_id and str(d.get("id") or "") == str(video_id) and isinstance(d.get("author"), dict):
            return d
        if d.get("desc") and isinstance(d.get("author"), dict) and isinstance(d.get("video"), dict):
            return d
    return {}


def _fetch_html_with_curl(url: str) -> str:
    try:
        cp = subprocess.run(
            [
                "curl",
                "-L",
                "-s",
                "--max-time",
                "30",
                "-A",
                TIKTOK_HEADERS["User-Agent"],
                "-H",
                f"Accept: {TIKTOK_HEADERS['Accept']}",
                "-H",
                f"Accept-Language: {TIKTOK_HEADERS['Accept-Language']}",
                "-H",
                f"Sec-Fetch-Site: {TIKTOK_HEADERS['Sec-Fetch-Site']}",
                "-H",
                f"Sec-Fetch-Mode: {TIKTOK_HEADERS['Sec-Fetch-Mode']}",
                "-H",
                f"Sec-Fetch-Dest: {TIKTOK_HEADERS['Sec-Fetch-Dest']}",
                str(url),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return ""
    if cp.returncode != 0:
        return ""
    return str(cp.stdout or "")


def _extract_video_id(url: str) -> str:
    m = re.search(r"/video/(\d{8,})", str(url or ""))
    return str(m.group(1) or "") if m else ""


def _vtt_to_plain_text(raw: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for line in str(raw or "").splitlines():
        s = line.strip()
        if not s or s.upper().startswith("WEBVTT"):
            continue
        if "-->" in s:
            continue
        if re.fullmatch(r"\d+", s):
            continue
        s = re.sub(r"<[^>]+>", "", s).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        lines.append(s)
    return "\n".join(lines)


def _fetch_subtitle_text(client: httpx.Client, video: dict[str, Any]) -> tuple[str, str]:
    infos = list(video.get("subtitleInfos") or [])
    if not infos:
        cla = video.get("claInfo") or {}
        infos = list(cla.get("captionInfos") or [])
    if not infos:
        return "", ""

    def rank(info: dict[str, Any]) -> tuple[int, str]:
        lang = str(info.get("LanguageCodeName") or info.get("language") or "").lower()
        if "cmn" in lang or "zh" in lang or "hans" in lang or "hant" in lang:
            return (0, lang)
        if "eng" in lang or "en" in lang:
            return (1, lang)
        return (2, lang)

    for info in sorted((x for x in infos if isinstance(x, dict)), key=rank):
        u = str(info.get("Url") or info.get("url") or "").strip()
        if not u.startswith("http"):
            urls = info.get("Urls") or info.get("urlList") or []
            if isinstance(urls, list):
                u = next((str(x).strip() for x in urls if str(x).strip().startswith("http")), "")
        if not u:
            continue
        try:
            r = client.get(u, timeout=20)
            if r.status_code >= 400:
                continue
            text = _vtt_to_plain_text(r.text)
            if text.strip():
                lang = str(info.get("LanguageCodeName") or info.get("language") or "")
                return text[:12000], lang
        except Exception:
            continue
    return "", ""


def tiktok_host(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    return "tiktok.com" in host


def crawl_tiktok_video_metadata(url: str) -> dict[str, Any]:
    target = str(url or "").strip()
    if not target:
        return {}
    with httpx.Client(timeout=30, follow_redirects=True, headers=TIKTOK_HEADERS) as client:
        resp = client.get(target)
        resp.raise_for_status()
        final_url = str(resp.url)
        data = _parse_universal_data(resp.text)
        if not data:
            # TikTok often gives Python HTTP clients a small login shell while curl receives
            # the SSR hydration JSON. Use curl as a metadata-only fallback.
            data = _parse_universal_data(_fetch_html_with_curl(target))
        item = _find_item_struct(data, _extract_video_id(final_url))
        if not item:
            raise ValueError("TikTok itemStruct not found")
        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        stats = item.get("statsV2") or item.get("stats") or {}
        subtitle_text, subtitle_lang = _fetch_subtitle_text(client, video)

    desc = _safe_text(item.get("desc"), 3000)
    author_handle = _safe_text(author.get("uniqueId"), 160)
    author_name = _safe_text(author.get("nickname"), 160)
    hashtags: list[str] = []
    for ch in item.get("challenges") or []:
        if isinstance(ch, dict):
            tag = _safe_text(ch.get("title"), 80)
            if tag:
                hashtags.append(tag)
    cover = _safe_text(video.get("originCover") or video.get("cover") or video.get("dynamicCover"), 1200)
    play_addr = _safe_text(video.get("playAddr") or video.get("downloadAddr"), 3000)
    duration = _safe_text(video.get("duration"), 40)
    width = _safe_text(video.get("width"), 20)
    height = _safe_text(video.get("height"), 20)
    video_id = _safe_text(item.get("id") or video.get("id") or _extract_video_id(final_url), 80)
    published_at = ""
    try:
        ts = int(str(item.get("createTime") or "0"))
        if ts > 0:
            published_at = datetime.fromtimestamp(ts, timezone.utc).isoformat()
    except Exception:
        published_at = ""
    if not published_at:
        published_at = datetime.now(timezone.utc).isoformat()

    return {
        "source_name": f"TikTok｜@{author_handle}" if author_handle else "TikTok",
        "source_category": "社群影片知識",
        "source_url": f"https://www.tiktok.com/@{author_handle}" if author_handle else "https://www.tiktok.com/",
        "item_url": final_url,
        "title": f"TikTok｜{author_name or author_handle}｜{desc[:80] or video_id}",
        "desc": desc,
        "author_handle": author_handle,
        "author_name": author_name,
        "author_signature": _safe_text(author.get("signature"), 1000),
        "hashtags": list(dict.fromkeys(hashtags))[:24],
        "stats": {str(k): str(v) for k, v in dict(stats or {}).items()},
        "video_id": video_id,
        "duration": duration,
        "width": width,
        "height": height,
        "cover": cover,
        "play_addr": play_addr,
        "subtitle_text": subtitle_text,
        "subtitle_lang": subtitle_lang,
        "published_at": published_at,
        "raw_language": _safe_text(item.get("textLanguage"), 80),
    }


def build_tiktok_knowledge_body(meta: dict[str, Any]) -> str:
    tags = [str(x).strip() for x in (meta.get("hashtags") or []) if str(x).strip()]
    stats = meta.get("stats") if isinstance(meta.get("stats"), dict) else {}
    stat_lines = [f"- {k}: {v}" for k, v in stats.items() if str(v).strip()]
    subtitle = str(meta.get("subtitle_text") or "").strip()
    parts = [
        "TikTok 影片知識來源（日本房地產／海外置業）",
        "",
        "[影片文案]",
        str(meta.get("desc") or "").strip() or "（無文案）",
        "",
        "[作者]",
        f"- 帳號：@{meta.get('author_handle') or ''}",
        f"- 名稱：{meta.get('author_name') or ''}",
        f"- 簡介：{meta.get('author_signature') or ''}",
        "",
        "[主題標籤]",
        "、".join(tags) if tags else "（無）",
        "",
        "[字幕逐字稿]",
        subtitle or "（未取得字幕）",
        "",
        "[互動數據]",
        "\n".join(stat_lines) if stat_lines else "（無）",
        "",
        "[影片資訊]",
        f"- TikTok ID：{meta.get('video_id') or ''}",
        f"- 時長：{meta.get('duration') or ''} 秒",
        f"- 版型：{meta.get('width') or ''}x{meta.get('height') or ''}",
        f"- 語言：{meta.get('raw_language') or ''}",
        f"- 字幕語言：{meta.get('subtitle_lang') or ''}",
        "",
        "[來源網址]",
        str(meta.get("item_url") or ""),
        "",
        "[影片播放位址]",
        str(meta.get("play_addr") or ""),
        "",
        "[封面圖片]",
        str(meta.get("cover") or ""),
        "",
        "用途：摘要搜尋、知識整理、購買日本房地產教育內容提取；只做摘要與分析，不直接複製為發布文。",
    ]
    return "\n".join(parts).strip()
