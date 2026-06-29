from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import BASE_DIR
from src.crawler import BROWSER_HEADERS
from src.db import get_conn, init_db
from src.link_quality import listing_title_or_fallback, title_looks_like_crawl_placeholder
from src.pipeline import generate_content_for_source
from src.portal_property_crawl import fetch_property_detail


VISIBLE_ERROR_PATTERNS: dict[str, re.Pattern[str]] = {
    "js_disabled": re.compile(r"javascript\s*(?:被禁用|已禁用|is disabled|を有効|disabled)", re.I),
    "robot_challenge": re.compile(
        r"(captcha|認証中|認證中|认证中|human verification|verify you are not a robot|"
        r"不是機器人|不是机器人|awswaf|通常のサイト閲覧を超える速度)",
        re.I,
    ),
    "http_error": re.compile(
        r"(?:^|\b)(?:error\s*500|server error|403 forbidden|404 not found|access denied|"
        r"service unavailable|that.?s an error)",
        re.I,
    ),
    "url_as_title": re.compile(r"^https?://\S+(?:\s*｜.*)?$", re.I),
}

TEXT_FIELDS = (
    "title_original",
    "body_original",
    "title_zh_hant",
    "title_zh_hans",
    "body_zh_hant",
    "body_zh_hans",
    "seo_title",
    "seo_description",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _host_key(url: str) -> str:
    u = str(url or "").lower()
    for h in (
        "homes.co.jp",
        "suumo.jp",
        "athome.co.jp",
        "realestate.yahoo.co.jp",
        "realestate.rakuten.co.jp",
        "yes1.co.jp",
        "oheya-su.jp",
    ):
        if h in u:
            return h
    return "other"


def _is_visible_url_title(field: str, value: str) -> bool:
    return field in {"title_original", "title_zh_hant", "title_zh_hans", "seo_title"} and bool(
        VISIBLE_ERROR_PATTERNS["url_as_title"].search(str(value or "").strip())
    )


def _polluted_signals(field: str, value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    out: set[str] = set()
    for name, pat in VISIBLE_ERROR_PATTERNS.items():
        if name == "url_as_title":
            if _is_visible_url_title(field, text):
                out.add(name)
            continue
        if pat.search(text):
            out.add(name)
    return out


def _row_signals(row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for field in TEXT_FIELDS:
        out.update(_polluted_signals(field, str(row.get(field) or "")))
    return out


def _text_is_polluted(text: str) -> bool:
    t = str(text or "")
    return any(p.search(t) for key, p in VISIBLE_ERROR_PATTERNS.items() if key != "url_as_title")


def _source_title(row: dict[str, Any]) -> str:
    item_url = str(row.get("item_url") or "").strip()
    candidates = [
        str(row.get("title_original") or "").strip(),
        str(row.get("title_zh_hant") or "").strip(),
        str(row.get("title_zh_hans") or "").strip(),
    ]
    for title in candidates:
        if not title:
            continue
        if _text_is_polluted(title) or title_looks_like_crawl_placeholder(title):
            continue
        if title.startswith(("http://", "https://")):
            continue
        return title[:240]
    return listing_title_or_fallback(str(row.get("title_original") or ""), item_url)[:240]


def _safe_body(row: dict[str, Any], reason: str) -> str:
    title = _source_title(row)
    item_url = str(row.get("item_url") or "").strip()
    source_name = str(row.get("source_name") or "").strip() or _host_key(item_url)
    return (
        f"{title}\n\n"
        f"來源站目前回傳{reason}，站內已清除錯誤頁文字，避免將驗證頁或錯誤頁誤作房源明細。\n"
        f"資料來源：{source_name}\n"
        f"來源物件頁（請以官方頁面為準）：{item_url}\n"
        "用途：站內摘要、導覽與連結索引；不主張為完整契約內容。"
    ).strip()


def _refetch_timeout(url: str) -> float:
    u = str(url or "").lower()
    if "homes.co.jp" in u or "homes.jp" in u:
        return 22.0
    if "athome.co.jp" in u or "realestate.yahoo.co.jp" in u:
        return 24.0
    return 18.0


def _attempt_refetch(row: dict[str, Any]) -> tuple[bool, str, str, list[str], str]:
    item_url = str(row.get("item_url") or "").strip()
    if not item_url.startswith("http"):
        return False, "", "", [], "invalid_url"
    fallback_context = "\n".join(
        [
            str(row.get("title_original") or ""),
            str(row.get("body_original") or ""),
            str(row.get("title_zh_hant") or ""),
            str(row.get("body_zh_hant") or ""),
        ]
    )[:12000]

    title = ""
    body = ""
    imgs: list[str] = []
    err = ""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(_refetch_timeout(item_url)),
            follow_redirects=True,
            headers=BROWSER_HEADERS,
        ) as client:
            title, body, imgs = fetch_property_detail(client, item_url, fallback_context=fallback_context)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:240]

    is_homes = "homes.co.jp" in item_url.lower() or "homes.jp" in item_url.lower()
    if is_homes and (not str(body or "").strip() or _text_is_polluted(f"{title}\n{body}")):
        try:
            from src.homes_detail_playwright import fetch_homes_detail_playwright

            pw = fetch_homes_detail_playwright(item_url)
            if pw.get("ok"):
                title = str(pw.get("title") or title or "").strip()
                body = str(pw.get("body_original") or body or "").strip()
                imgs = list(pw.get("image_urls") or imgs or [])
                err = ""
            elif not err:
                err = str(pw.get("error") or pw.get("reason") or "playwright_fetch_failed")[:240]
        except Exception as exc:
            if not err:
                err = f"playwright:{type(exc).__name__}: {exc}"[:240]

    if not str(body or "").strip():
        return False, "", "", [], err or "empty_response"
    if _text_is_polluted(f"{title}\n{body}"):
        return False, "", "", [], "source_still_returns_error_or_verification_page"
    return True, str(title or "").strip(), str(body or "").strip(), list(imgs or []), ""


def _load_rows(ids: set[int] | None, limit: int, *, force: bool = False) -> list[dict[str, Any]]:
    where = "COALESCE(s.content_kind,'')='jp_listing'"
    params: list[Any] = []
    if ids:
        marks = ",".join("?" for _ in ids)
        where += f" AND s.id IN ({marks})"
        params.extend(sorted(ids))
    sql = f"""
        SELECT s.id, s.source_name, s.source_url, s.item_url, s.title_original, s.body_original,
               s.image_urls, s.access_status, s.access_note,
               c.title_zh_hant, c.title_zh_hans, c.body_zh_hant, c.body_zh_hans,
               c.seo_title, c.seo_description, c.listing_media_json
        FROM source_items s
        JOIN content_items c ON c.source_item_id = s.id
        WHERE {where}
        ORDER BY s.id
    """
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    matched = rows if force and ids else [r for r in rows if _row_signals(r)]
    if limit > 0:
        return matched[: int(limit)]
    return matched


def _write_backup(rows: list[dict[str, Any]], path: Path | None) -> Path | None:
    if not rows:
        return None
    if path is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = BASE_DIR / "logs" / f"contaminated_case_text_backup_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _repair_one(row: dict[str, Any], *, apply: bool, refetch: bool) -> dict[str, Any]:
    sid = int(row["id"])
    signals = sorted(_row_signals(row))
    reason = "驗證頁" if ("js_disabled" in signals or "robot_challenge" in signals) else "錯誤頁"
    refetch_ok = False
    refetch_error = ""
    title_new = _source_title(row)
    body_new = _safe_body(row, reason)
    images_new = str(row.get("image_urls") or "")

    if refetch:
        ok, title, body, imgs, err = _attempt_refetch(row)
        refetch_ok = bool(ok)
        refetch_error = err
        if ok:
            clean_title = listing_title_or_fallback(title or title_new, str(row.get("item_url") or ""))
            title_new = clean_title[:240]
            body_new = body
            if imgs:
                images_new = "\n".join(dict.fromkeys(str(u).strip() for u in imgs if str(u).strip()))

    if apply:
        now = _now()
        os.environ["SCLAW_FAST_JP_LISTING_CONTENT"] = "1"
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE source_items
                SET title_original = ?,
                    body_original = ?,
                    image_urls = ?,
                    access_note = CASE
                        WHEN COALESCE(access_note, '') LIKE '%contaminated_case_text_repair%' THEN access_note
                        ELSE TRIM(COALESCE(access_note, '') || ' contaminated_case_text_repair')
                    END,
                    last_checked_at = ?,
                    crawled_at = ?
                WHERE id = ?
                """,
                (title_new, body_new, images_new, now, now, sid),
            )
            generate_content_for_source(conn, sid)
            _clean_generated_content_fields(conn, sid)
            conn.commit()

    return {
        "id": sid,
        "host": _host_key(str(row.get("item_url") or "")),
        "signals": signals,
        "refetch_ok": refetch_ok,
        "refetch_error": refetch_error,
        "title": title_new[:120],
    }


def _clean_generated_content_fields(conn, source_item_id: int) -> None:
    row = conn.execute(
        "SELECT body_zh_hant, body_zh_hans FROM content_items WHERE source_item_id = ?",
        (int(source_item_id),),
    ).fetchone()
    if not row:
        return

    def clean(body: str) -> str:
        text = str(body or "")
        text = re.sub(r"(?m)^交通：來源站目前回傳.*$", "交通：—", text)
        text = re.sub(r"(?m)^交通：来源站目前回传.*$", "交通：—", text)
        return text

    body_hant = clean(str(row["body_zh_hant"] or ""))
    body_hans = clean(str(row["body_zh_hans"] or ""))
    if body_hant != str(row["body_zh_hant"] or "") or body_hans != str(row["body_zh_hans"] or ""):
        conn.execute(
            """
            UPDATE content_items
            SET body_zh_hant = ?, body_zh_hans = ?, updated_at = CURRENT_TIMESTAMP
            WHERE source_item_id = ?
            """,
            (body_hant, body_hans, int(source_item_id)),
        )


def _parse_ids(raw: str) -> set[int] | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    out: set[int] = set()
    for part in raw.split(","):
        s = part.strip()
        if not s:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            out.update(range(a, b + 1))
        elif s.isdigit():
            out.add(int(s))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair polluted jp_listing title/body/SEO fields.")
    parser.add_argument("--apply", action="store_true", help="Write updates. Default is dry-run.")
    parser.add_argument("--refetch", action="store_true", help="Try to refetch each affected source URL before fallback repair.")
    parser.add_argument("--refetch-limit", type=int, default=0, help="Only refetch the first N affected rows; fallback-repair the rest.")
    parser.add_argument("--limit", type=int, default=0, help="Only scan first N affected source rows after SQL order.")
    parser.add_argument("--ids", default="", help="Comma/range source_item ids, e.g. 313,342,500-520.")
    parser.add_argument("--force", action="store_true", help="With --ids, process those ids even if current fields are already clean.")
    parser.add_argument("--backup-json", default="", help="Optional backup JSON path for rows to be changed.")
    args = parser.parse_args()

    init_db()
    parsed_ids = _parse_ids(args.ids)
    rows = _load_rows(parsed_ids, int(args.limit or 0), force=bool(args.force))
    backup_path = _write_backup(rows, Path(args.backup_json) if args.backup_json else None) if args.apply else None
    refetch_remaining = max(0, int(args.refetch_limit or 0)) if args.refetch else 0

    results: list[dict[str, Any]] = []
    for row in rows:
        do_refetch = bool(args.refetch and (int(args.refetch_limit or 0) <= 0 or refetch_remaining > 0))
        if args.refetch and int(args.refetch_limit or 0) > 0 and do_refetch:
            refetch_remaining -= 1
        results.append(_repair_one(row, apply=bool(args.apply), refetch=do_refetch))

    summary: dict[str, Any] = {
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "matched_rows": len(rows),
        "changed_rows": len(results) if args.apply else 0,
        "backup_json": str(backup_path) if backup_path else "",
        "refetch_attempted": sum(1 for r in results if r["refetch_ok"] or r["refetch_error"]),
        "refetch_ok": sum(1 for r in results if r["refetch_ok"]),
        "refetch_failed": sum(1 for r in results if r["refetch_error"]),
        "by_host": {},
        "by_signal": {},
        "samples": results[:20],
    }
    by_host: dict[str, int] = {}
    by_signal: dict[str, int] = {}
    for r in results:
        by_host[r["host"]] = by_host.get(r["host"], 0) + 1
        for sig in r["signals"]:
            by_signal[sig] = by_signal.get(sig, 0) + 1
    summary["by_host"] = dict(sorted(by_host.items()))
    summary["by_signal"] = dict(sorted(by_signal.items()))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
