from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from src.bsoup import soup_from_html
from src.crawler import BROWSER_HEADERS, CrawledItem
from src.pipeline import process_crawled_items

SUUMO_FAQ_URL = "https://suumo.jp/kasu/knowhow/first/"


@dataclass
class FaqEntry:
    title: str
    body: str
    item_url: str


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _extract_faq_entries(html: str, base_url: str = SUUMO_FAQ_URL) -> list[FaqEntry]:
    soup = soup_from_html(html)
    entries: list[FaqEntry] = []

    # Prefer semantic sections with headings + paragraphs / lists.
    sections = soup.select("h1, h2, h3")
    for idx, h in enumerate(sections, 1):
        title = _clean_text(h.get_text(" "))
        if len(title) < 8:
            continue
        body_lines: list[str] = []
        for sib in h.find_all_next(limit=8):
            if sib.name in ("h1", "h2", "h3"):
                break
            if sib.name in ("p", "li"):
                t = _clean_text(sib.get_text(" "))
                if len(t) >= 20:
                    body_lines.append(t)
        if not body_lines:
            continue
        body = " ".join(body_lines)[:1300]
        anchor = h.get("id")
        item_url = f"{base_url}#{anchor}" if anchor else f"{base_url}#faq-{idx}"
        entries.append(FaqEntry(title=title[:180], body=body, item_url=item_url))
        if len(entries) >= 100:
            break
    return entries


def _fallback_entries() -> list[FaqEntry]:
    common = [
        (
            "不再居住的房子，三種選擇是什麼？",
            "持ち家を住み替える時，常見選擇是「出售」「空置維持」「出租」。出售可一次回收資金但可能要清償貸款；空置保留彈性但有維護與稅費成本；出租可有租金但有空置與管理風險。",
        ),
        (
            "出售的主要優缺點",
            "優點：不再承擔維護與固定成本。缺點：若貸款未清可能需補差額，並有仲介與登記等交易費用。",
        ),
        (
            "空置維持的主要優缺點",
            "優點：保有自住回歸彈性與資產。缺點：有防盜、通風、管理、稅費與修繕支出。",
        ),
        (
            "出租的主要優缺點",
            "優點：有機會取得租金收益且保留資產。缺點：可能遇到空室、滯納、修繕與管理成本。",
        ),
    ]
    out: list[FaqEntry] = []
    for i, (q, a) in enumerate(common, 1):
        out.append(FaqEntry(title=q, body=a, item_url=f"{SUUMO_FAQ_URL}#common-{i}"))
    return out


def seed_suumo_faq_knowledge(limit: int = 100) -> dict:
    lim = max(5, min(100, int(limit)))
    entries: list[FaqEntry] = []
    source_status = "live"
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            r = client.get(SUUMO_FAQ_URL)
            r.raise_for_status()
            entries = _extract_faq_entries(r.text, SUUMO_FAQ_URL)
    except Exception:
        source_status = "fallback"
        entries = []

    if not entries:
        entries = _fallback_entries()

    items: list[CrawledItem] = []
    for row in entries[:lim]:
        body = (
            f"{row.title}\n\n"
            f"{row.body}\n\n"
            f"來源網址：{row.item_url}\n"
            "用途：提供華人赴日購屋常見問答之知識庫查詢。"
        )
        items.append(
            CrawledItem(
                source_name="SUUMO FAQ",
                source_category="大型房仲",
                source_url=SUUMO_FAQ_URL,
                item_url=row.item_url,
                title_original=row.title,
                body_original=body,
                language="ja",
                published_at=None,
                access_status="public",
                access_note="",
                image_urls="",
                content_kind="suumo_faq",
            )
        )
    processed = process_crawled_items(items) if items else 0
    return {
        "ok": True,
        "source_status": source_status,
        "fetched_count": len(entries),
        "processed": processed,
        "url": SUUMO_FAQ_URL,
    }
