"""Translate remaining non-listing public cache fields from Japanese to Chinese.

``source_items`` remains untouched.  This only updates the public Chinese,
SEO and schema fields for non-``jp_listing`` records after the structured
listing cache has been rebuilt separately.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from opencc import OpenCC


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import DB_PATH
from src.db import get_conn
from src.text_utils import build_schema_json, build_seo_description, build_seo_title


_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")
_T2S = OpenCC("t2s")
_S2T = OpenCC("s2t")
_MAX_BODY_TRANSLATE = 1500
_TRANSLATE_TIMEOUT = (3, 8)


def _default_backup_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path("/tmp") / f"sclaw-public-cache-before-translation-{stamp}.sqlite3"


def _snapshot_database(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination}")
    source = sqlite3.connect(str(DB_PATH))
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target, pages=2048)
    finally:
        target.close()
        source.close()


def _translate_hans(text: str) -> str:
    value = str(text or "")
    if not _KANA.search(value):
        return _T2S.convert(value)
    response = requests.get(
        "https://translate.google.com/m",
        # Cached Chinese fields often have a Chinese disclaimer wrapped around
        # Japanese source text. Auto detection would leave that Japanese
        # untouched; this path is used only when kana is present, so force JA.
        params={"sl": "ja", "tl": "zh-CN", "q": value},
        timeout=_TRANSLATE_TIMEOUT,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    node = soup.find("div", {"class": "result-container"}) or soup.find("div", {"class": "t0"})
    translated = node.get_text(strip=True) if node else ""
    response.close()
    if not translated:
        raise ValueError("translation response is empty")
    return translated.strip()


def _translate_pair(text: str) -> tuple[str, str]:
    hans = _translate_hans(text)
    return _S2T.convert(hans), hans


def _translate_pair_for_display(text: str, *, fallback: str, body: bool = False) -> tuple[str, str]:
    value = str(text or "").strip()
    short_value = value
    truncated = False
    if body and len(short_value) > _MAX_BODY_TRANSLATE:
        cut = max(short_value.rfind("\n", 0, _MAX_BODY_TRANSLATE), short_value.rfind("。", 0, _MAX_BODY_TRANSLATE))
        short_value = short_value[: cut if cut >= 300 else _MAX_BODY_TRANSLATE]
        truncated = True
    try:
        hant, hans = _translate_pair(short_value)
        if not hant or _KANA.search(hant + hans):
            raise ValueError("translation still contains Japanese kana")
    except Exception:
        hant = fallback
        hans = _T2S.convert(fallback)
        truncated = False
    if truncated:
        hant = f"{hant}\n\n本文內容較長，已先提供中文重點摘要；完整資料可向顧問索取中文說明。"
        hans = f"{hans}\n\n正文内容较长，已先提供中文重点摘要；完整资料可向顾问索取中文说明。"
    return hant, hans


def _local_chinese_fallback(text: str, *, fallback: str, body: bool = False) -> tuple[str, str]:
    """Remove Japanese-only fragments while retaining any existing Chinese/English text.

    This is used only when an external translator is rate-limited.  The raw
    source remains in ``source_items``; the public cache receives a Chinese
    reading summary rather than exposing untranslated text.
    """
    kept = [line.strip() for line in str(text or "").splitlines() if line.strip() and not _KANA.search(line)]
    hant = "\n".join(kept).strip()
    if not hant or (body and len(hant) < 24):
        hant = fallback
    if body:
        hant = f"{hant}\n\n此條目已保留原始公開資料，站內目前提供中文摘要與閱讀入口；完整中文說明可向顧問索取。"
    return hant, _T2S.convert(hant)


def _has_visible_kana(row: sqlite3.Row) -> bool:
    return any(
        _KANA.search(str(row[key] or ""))
        for key in ("title_zh_hant", "title_zh_hans", "seo_title", "seo_description", "body_zh_hant", "body_zh_hans", "schema_json", "keyword_tags")
    )


def run(*, apply: bool, limit: int, batch_size: int, delay: float, backup: Path | None, no_backup: bool, offline_fallback: bool) -> dict[str, Any]:
    if apply and not no_backup:
        _snapshot_database(backup or _default_backup_path())
    safe_limit = max(0, int(limit or 0))
    safe_batch = max(1, min(500, int(batch_size or 50)))
    scanned = translated = failures = 0
    failure_ids: list[int] = []
    with get_conn() as conn:
        sql = """
            SELECT c.*, COALESCE(s.content_kind, '') AS source_content_kind, COALESCE(s.source_name, '') AS source_name
            FROM content_items c
            JOIN source_items s ON s.id = c.source_item_id
            WHERE COALESCE(s.content_kind, '') <> 'jp_listing'
            ORDER BY c.id ASC
        """
        if safe_limit:
            sql += " LIMIT ?"
            rows = conn.execute(sql, (safe_limit,))
        else:
            rows = conn.execute(sql)
        for row in rows:
            scanned += 1
            if not _has_visible_kana(row):
                continue
            d = dict(row)
            try:
                title_source = str(d.get("title_zh_hant") or d.get("title_zh_hans") or "")
                body_source = str(d.get("body_zh_hant") or d.get("body_zh_hans") or "")
                tags_source = str(d.get("keyword_tags") or "")
                translate = _local_chinese_fallback if offline_fallback else _translate_pair_for_display
                title_hant, title_hans = translate(title_source, fallback="日本資料中文摘要")
                body_hant, body_hans = translate(
                    body_source,
                    fallback="此條目已整理為中文閱讀入口；完整資料可向顧問索取中文說明。",
                    body=True,
                )
                tags_hant, _ = translate(tags_source, fallback="日本資料,中文摘要")
                if _KANA.search("\n".join((title_hant, title_hans, body_hant, body_hans, tags_hant))):
                    raise ValueError("translation still contains Japanese kana")
                slug = str(d.get("seo_slug") or "").strip()
                region = str(d.get("region_code") or "全球華人")
                seo_title = build_seo_title(title_hant, region)
                seo_description = build_seo_description(title_hant, str(d.get("source_name") or ""))
                schema_json = build_schema_json(slug, seo_title, seo_description, region, body_hant)
                if _KANA.search("\n".join((seo_title, seo_description, schema_json))):
                    raise ValueError("rebuilt SEO still contains Japanese kana")
                if apply:
                    conn.execute(
                        """
                        UPDATE content_items
                        SET title_zh_hant = ?, title_zh_hans = ?,
                            body_zh_hant = ?, body_zh_hans = ?,
                            seo_title = ?, seo_description = ?, schema_json = ?,
                            keyword_tags = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (title_hant, title_hans, body_hant, body_hans, seo_title, seo_description, schema_json, tags_hant, int(d["id"])),
                    )
                translated += 1
                if delay > 0:
                    time.sleep(delay)
            except Exception:
                failures += 1
                if len(failure_ids) < 20:
                    failure_ids.append(int(d.get("id") or 0))
            if apply and translated and translated % safe_batch == 0:
                conn.commit()
            if scanned % 100 == 0:
                print(f"[public-cache-translation] scanned={scanned} translated={translated} failures={failures}", file=sys.stderr, flush=True)
        if apply:
            conn.commit()
    return {
        "ok": failures == 0,
        "apply": bool(apply),
        "scanned_rows": scanned,
        "translated_rows": translated,
        "failures": failures,
        "failure_content_ids": failure_ids,
        "offline_fallback": bool(offline_fallback),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--delay", type=float, default=0.05, help="seconds between external translation calls")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--no-backup", action="store_true", help="only when a fresh whole-DB snapshot already exists")
    parser.add_argument("--offline-fallback", action="store_true", help="avoid external translation when it is rate-limited")
    args = parser.parse_args()
    report = run(
        apply=bool(args.apply),
        limit=int(args.limit or 0),
        batch_size=int(args.batch_size or 50),
        delay=max(0.0, float(args.delay or 0.0)),
        backup=args.backup,
        no_backup=bool(args.no_backup),
        offline_fallback=bool(args.offline_fallback),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
