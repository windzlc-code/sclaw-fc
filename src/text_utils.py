import html
import json
import re
from datetime import datetime, timezone

from deep_translator import GoogleTranslator
from opencc import OpenCC
from slugify import slugify

from src.config import SITE_NAME
from src.site_public_config import get_effective_site_url

_cc_s2t = OpenCC("s2t")


def looks_japanese(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9faf]", text or ""))


_RE_KANA = re.compile(r"[\u3041-\u3096\u30a1-\u30fc]")


def is_japanese_primary_plaintext(text: str) -> bool:
    """
    True when text is mostly untranslated Japanese (hiragana/katakana present)
    and lacks enough zh-Hans/zh-Hant editorial markers — e.g. JP title pasted into body_zh_*.
    """
    t = (text or "").strip()
    if len(t) < 8:
        return False
    kana_n = len(_RE_KANA.findall(t))
    if kana_n < 2:
        return False
    # Strong zh editorial / UI fragments → treat as bilingual stub, not "JP only"
    if any(
        x in t
        for x in (
            "用途：",
            "僅做",
            "仅做",
            "资讯摘要",
            "資訊摘要",
            "制度導覽",
            "制度导览",
            "台灣買日本",
            "台湾买日本",
            "資料重整",
            "资料重整",
            "指南",
            "繁體",
            "简体",
            "簡體",
        )
    ):
        return False
    zh_markers = re.findall(
        r"[的了在是不與或為這樣個該請說讓您擬僅觀諮詢複製資訊簡體臺灣台湾仅说还过进对]",
        t,
    )
    if len(zh_markers) >= 4:
        return False
    return True


def body_zh_field_is_corrupt_jp_placeholder(body: str) -> bool:
    """
    True when body_zh_hant/body_zh_hans is still mostly Japanese (possibly with a zh disclaimer tail),
    so article「結論／AI 解說」不應直接從該欄抽句。
    """
    b = sanitize_article_display_body(body or "")
    if not b:
        return True
    if is_japanese_primary_plaintext(b):
        return True
    head = b[:56]
    if len(_RE_KANA.findall(head)) >= 2 and re.search(r"[仅资讯摘要制度导览资料复制]", b):
        return True
    return False


def to_zh_hans(text: str) -> str:
    if not text:
        return ""
    try:
        return GoogleTranslator(source="auto", target="zh-CN").translate(text)
    except Exception:
        return text


def to_zh_hant(text: str) -> str:
    return _cc_s2t.convert(text or "")


def dual_translate(text: str) -> tuple[str, str]:
    zh_hans = to_zh_hans(text)
    zh_hant = to_zh_hant(zh_hans)
    return zh_hant, zh_hans


def rewrite_for_originality(body_zh_hant: str, body_zh_hans: str, source_name: str) -> tuple[str, str]:
    """
    舊版曾在正文前綴長段「資料重整聲明」，導致文章頁「結論／AI 解說」整段被聲明占滿。
    改為不注入正文；免責與出處改由 article 模板底部一行＋來源按鈕呈現。
    """
    _ = source_name
    return (body_zh_hant or "").strip(), (body_zh_hans or "").strip()


_DISCLAIMER_MARKERS = (
    "【資料重整聲明】",
    "【资料重整声明】",
    "資料重整聲明",
    "资料重整声明",
    "僅保留事實與制度重點",
    "仅保留事实与制度重点",
    "原始資料來源：",
    "原始资料来源：",
    "來源鏈接：",
    "来源链接：",
    "來源網站：",
    "来源网站：",
    "用途：僅做資訊摘要",
    "用途：仅做信息摘要",
)


def is_disclaimer_or_template_noise(sentence: str) -> bool:
    t = (sentence or "").strip()
    if len(t) < 10:
        return True
    for m in _DISCLAIMER_MARKERS:
        if m in t:
            return True
    return False


# 聲明常與後文同一行（僅以句號分隔），僅刪「行」無法清掉
_RE_DISCLAIMER_HANT_SENT = re.compile(r"【資料重整聲明】[^。]*。")
_RE_DISCLAIMER_HANS_SENT = re.compile(r"【资料重整声明】[^。]*。")


def strip_disclaimer_noise_for_keypoints(text: str) -> str:
    """移除整行聲明／用途模板，供 build_key_points 與既有庫存文章使用。"""
    out_lines: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if is_disclaimer_or_template_noise(s):
            continue
        out_lines.append(line)
    return "\n".join(out_lines).strip()


_RE_SUUMO_RESIZE_FULL = re.compile(
    r"https://img\d*\.suumo\.com/jj/resizeImage\?[^\s\]\)\"']+",
    flags=re.IGNORECASE,
)
_RE_SUUMO_RESIZE_SPLIT = re.compile(
    r"(https://img\d*\.suumo\.com/jj/resizeImage)(?:\s*\n\s*|\s+)(src=[^\s\]\)\"']+)",
    flags=re.IGNORECASE,
)
_RE_PUBLIC_DISPLAY_URL = re.compile(r"https?://[^\s\]\)\"'<>]+", flags=re.IGNORECASE)
_RE_PUBLIC_LINK_NOISE_LABEL = re.compile(
    r"^\s*(?:[-－*•・]\s*)?"
    r"(?:"
    r"來源|来源|來源URL|来源URL|來源網址|来源网址|來源連結|来源链接|"
    r"圖片網址|图片网址|圖片頁|图片页|物件參考圖像\s*URL|image\s*urls?|source\s*url"
    r")\s*[:：]?",
    flags=re.IGNORECASE,
)
_RE_PUBLIC_IMAGE_URL_FRAGMENT = re.compile(
    r"(?:resizeImage\?|/jj/resizeImage|src=|gazo%2F|image\.php|img\d*\.suumo\.(?:com|jp)|/image_files/path/)",
    flags=re.IGNORECASE,
)
_RE_PUBLIC_HTML_TAG = re.compile(r"<[^>\n]{1,240}>")


def strip_public_link_noise(text: str) -> str:
    """Remove raw source/image URL fragments from public article and case narrative text."""
    raw = html.unescape(str(text or ""))
    if not raw.strip():
        return ""
    out_lines: list[str] = []
    skip_url_block = False
    for raw_line in raw.splitlines():
        line = _RE_PUBLIC_HTML_TAG.sub("", str(raw_line or "")).strip()
        if not line:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            continue
        has_label = bool(_RE_PUBLIC_LINK_NOISE_LABEL.match(line))
        has_url = bool(_RE_PUBLIC_DISPLAY_URL.search(line))
        has_img_fragment = bool(_RE_PUBLIC_IMAGE_URL_FRAGMENT.search(line))
        if has_label and (has_url or has_img_fragment or len(line) <= 48):
            skip_url_block = True
            continue
        if skip_url_block and (has_url or has_img_fragment):
            continue
        skip_url_block = False
        if _RE_PUBLIC_DISPLAY_URL.fullmatch(line):
            continue
        if has_url:
            if has_img_fragment:
                continue
            line = _RE_PUBLIC_DISPLAY_URL.sub("", line).strip(" \t　｜|:：,，;；。()（）[]【】")
            if not line or _RE_PUBLIC_LINK_NOISE_LABEL.match(line):
                continue
        if _RE_PUBLIC_IMAGE_URL_FRAGMENT.search(line):
            continue
        out_lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()


def _suumo_resize_url_is_safe(url: str) -> bool:
    u = (url or "").strip()
    if not u.startswith("https://"):
        return False
    low = u.lower()
    if "suumo.com/jj/resizeimage?" not in low:
        return False
    if "src=" not in low:
        return False
    if any(bad in low for bad in ("<", ">", '"', "'", "`", "\n", "\r", "javascript:")):
        return False
    return True


def format_ai_pack_line_html(text: str) -> str:
    """
    文章頁「結論／AI 解說」用：將 SUUMO 縮圖網址（含 LLM 常見的斷行 `resizeImage` + `src=…`）轉成 <img>，
    其餘字元一律跳脫，避免把模型輸出的文字當成 HTML 執行。
    """
    raw = str(text or "")
    if not raw:
        return ""
    spans: list[tuple[int, int, str]] = []
    for m in _RE_SUUMO_RESIZE_FULL.finditer(raw):
        u = m.group(0).strip()
        if _suumo_resize_url_is_safe(u):
            spans.append((m.start(), m.end(), u))
    for m in _RE_SUUMO_RESIZE_SPLIT.finditer(raw):
        u = (m.group(1) + "?" + m.group(2)).strip()
        if not _suumo_resize_url_is_safe(u):
            continue
        s, e = m.start(), m.end()
        if any(not (e <= os or s >= oe) for os, oe, _ in spans):
            continue
        spans.append((s, e, u))
    spans.sort(key=lambda x: x[0])
    merged: list[tuple[int, int, str]] = []
    for s, e, u in spans:
        if merged and s < merged[-1][1]:
            continue
        merged.append((s, e, u))
    parts: list[str] = []
    last = 0
    for s, e, u in merged:
        parts.append(html.escape(raw[last:s]))
        eu = html.escape(u, quote=True)
        parts.append(
            '<span class="article-inline-img-wrap">'
            f'<a href="{eu}" target="_blank" rel="nofollow noopener noreferrer">'
            f'<img src="{eu}" alt="" loading="lazy" referrerpolicy="no-referrer" '
            'class="article-inline-suumo-thumb"></a></span>'
        )
        last = e
    parts.append(html.escape(raw[last:]))
    return "".join(parts)


def sanitize_article_display_body(text: str) -> str:
    """
    文章頁／結論用：先以正則剝除與正文黏在一起的「資料重整聲明」句，再整行過濾模板句。
    """
    t = (text or "").strip()
    if not t:
        return ""
    t = t.replace("\ufffd", "")
    t = re.sub(r"ヒント\s*[:：]\s*", "", t)
    if re.search(r"(?:需授權或無法抽吸|需授权或无法抽吸)\s+JavaScript\s*被禁用", t):
        return ""
    t = re.sub(
        r"(?is)\[物件欄位摘要\].*?(?=\n\s*\[[^\]]+\]|\Z)",
        "\n",
        t,
    )
    prev = None
    while prev != t:
        prev = t
        t = _RE_DISCLAIMER_HANT_SENT.sub("", t)
        t = _RE_DISCLAIMER_HANS_SENT.sub("", t)
    # 常見誤植：「衝縄」應為沖繩一帶的「沖縄」
    t = t.replace("衝縄", "沖縄")
    t = strip_public_link_noise(t)
    t = re.sub(r"(?im)^\s*\[(?:來源網址|来源网址|圖片網址|图片网址|來源連結|来源链接)\]\s*$", "", t)
    t = strip_disclaimer_noise_for_keypoints(t)
    t = re.sub(r"[ \t\u3000]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def build_slug(region_code: str, keyword_type: str, title_zh_hans: str) -> str:
    t = (title_zh_hans or "").strip()
    if len(t) > 72:
        t = t[:72].rstrip(" -—，、")
    raw = f"{region_code}-{keyword_type}-{t}"
    s = slugify(raw, separator="-")
    if len(s) > 96:
        s = s[:96].rstrip("-")
    return s


def build_seo_title(title_zh_hant: str, region_name: str) -> str:
    return f"{title_zh_hant}｜{region_name}買日本房地產指南"


def build_seo_description(title_zh_hant: str, source_name: str) -> str:
    _ = source_name
    return (
        f"{title_zh_hant}：整合日本官方與主流平台資訊，提供制度、稅務、流程與地區趨勢。"
    )[:160]


def build_schema_json(
    slug: str,
    seo_title: str,
    seo_description: str,
    region_name: str,
    body_zh_hant: str,
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": seo_title,
        "description": seo_description,
        "inLanguage": "zh-Hant",
        "isAccessibleForFree": True,
        "datePublished": now,
        "dateModified": now,
        "author": {"@type": "Organization", "name": SITE_NAME},
        "publisher": {"@type": "Organization", "name": SITE_NAME},
        "mainEntityOfPage": f"{get_effective_site_url()}/article/{slug}",
        "articleSection": [region_name, "日本房地產"],
        "about": (sanitize_article_display_body(body_zh_hant) or seo_description)[:150],
    }
    return json.dumps(schema, ensure_ascii=False)
