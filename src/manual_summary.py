import re
from urllib.parse import urlparse

import httpx

from src.bsoup import soup_from_html
from src.text_utils import dual_translate


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _split_sentences(text: str) -> list[str]:
    chunks = re.split(r"[。！？!?]\s*|\.\s+", text)
    return [x.strip() for x in chunks if x.strip()]


def _extract_page(url: str) -> dict:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("網址格式不正確，請使用 http/https 完整網址")

    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "SCLAWBot/1.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw = resp.content

    # Japanese sites often use cp932/euc-jp; fallback decoding avoids mojibake.
    text = ""
    for enc in [resp.encoding, "utf-8", "cp932", "euc_jp", "shift_jis"]:
        if not enc:
            continue
        try:
            text = raw.decode(enc, errors="strict")
            break
        except Exception:
            continue
    if not text:
        text = raw.decode("utf-8", errors="ignore")

    soup = soup_from_html(text)

    title = _clean_text(soup.title.get_text(" ")) if soup.title else "未命名來源"
    paragraphs = []
    for p in soup.select("p"):
        txt = _clean_text(p.get_text(" "))
        if len(txt) >= 25:
            paragraphs.append(txt)
        if len(paragraphs) >= 12:
            break

    body = "\n".join(paragraphs) if paragraphs else title
    return {"title": title[:180], "body": body[:4000]}


def _build_key_points(text: str, limit: int = 5) -> list[str]:
    sentences = _split_sentences(text)
    return sentences[:limit] if sentences else ["此來源可用內容較少，建議補充更多公開資料交叉比對。"]


def build_manual_summary(url: str) -> dict:
    try:
        data = _extract_page(url)
        title_hant, title_hans = dual_translate(data["title"])
        body_hant, body_hans = dual_translate(data["body"])
        points_hant = _build_key_points(body_hant, limit=5)
        points_hans = _build_key_points(body_hans, limit=5)
        return {
            "ok": True,
            "source_url": url,
            "title_original": data["title"],
            "title_zh_hant": title_hant,
            "title_zh_hans": title_hans,
            "summary_zh_hant": body_hant[:1200],
            "summary_zh_hans": body_hans[:1200],
            "key_points_zh_hant": points_hant,
            "key_points_zh_hans": points_hans,
            "status": "public",
            "note": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "source_url": url,
            "title_original": "需授權或無法抓取",
            "title_zh_hant": "需授權或無法抓取",
            "title_zh_hans": "需授权或无法抓取",
            "summary_zh_hant": "此來源目前無法直接抓取，請改為手動貼上可公開文字，再進行翻譯與重點整理。",
            "summary_zh_hans": "该来源目前无法直接抓取，请改为手动贴上可公开文字，再进行翻译与重点整理。",
            "key_points_zh_hant": [
                "確認來源是否需登入或授權。",
                "改抓官方可公開頁面（NTA、SUUMO、HOMES、AtHome）。",
                "整理後再發布，避免直接複製原文。",
            ],
            "key_points_zh_hans": [
                "确认来源是否需登录或授权。",
                "改抓官方可公开页面（NTA、SUUMO、HOMES、AtHome）。",
                "整理后再发布，避免直接复制原文。",
            ],
            "status": "restricted",
            "note": str(exc),
        }
