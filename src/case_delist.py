from __future__ import annotations

import sqlite3
from typing import Any

from src.db import get_conn
from src.portal_case_search import is_likely_agent_portrait_image_url, ordered_listing_image_urls


ENDED_HOMES_NO_TRUSTED_IMAGE_REASON = (
    "來源 HOME'S 物件已顯示掲載終了，且站內沒有與本案編號相符的可信圖片；"
    "已下架，避免空白圖片或推薦物件照片誤導。"
)

_ENDED_TOKENS = (
    "該当物件の掲載は終了しました",
    "この物件の掲載は終了しました",
    "物件の掲載は終了しました",
    "掲載は終了しました",
    "掲載が終了しました",
    "掲載終了",
    "募集は終了しました",
    "已下架",
    "已停止刊登",
    "已結束刊登",
)


def _row_get(row: Any, key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key, default)
        return default


def _is_homes_listing_url(item_url: Any) -> bool:
    u = str(item_url or "").strip().lower()
    return ("homes.co.jp" in u or "homes.jp" in u) and "/b-" in u


def case_listing_body_indicates_ended(row: Any) -> bool:
    blob = "\n".join(
        str(_row_get(row, key, "") or "")
        for key in (
            "title_original",
            "title_zh_hant",
            "title_zh_hans",
            "body_original",
            "body_zh_hant",
            "body_zh_hans",
        )
    )
    if not blob.strip():
        return False
    return any(token in blob for token in _ENDED_TOKENS)


def trusted_property_gallery_urls(row: Any, *, limit: int = 1) -> list[str]:
    try:
        gallery = ordered_listing_image_urls(
            str(_row_get(row, "image_urls", "") or ""),
            str(_row_get(row, "body_original", "") or ""),
            str(_row_get(row, "listing_media_json", "[]") or "[]"),
            item_url=str(_row_get(row, "item_url", "") or ""),
            limit=max(1, int(limit or 1)),
        )
    except Exception:
        gallery = []
    out: list[str] = []
    for url in gallery:
        u = str(url or "").strip()
        if not u or is_likely_agent_portrait_image_url(u):
            continue
        out.append(u)
        if len(out) >= max(1, int(limit or 1)):
            break
    return out


def should_delist_ended_homes_without_trusted_images(row: Any) -> bool:
    if str(_row_get(row, "content_kind", "") or "").strip().lower() != "jp_listing":
        return False
    if not _is_homes_listing_url(_row_get(row, "item_url", "")):
        return False
    if not case_listing_body_indicates_ended(row):
        return False
    return not trusted_property_gallery_urls(row, limit=1)


def _candidate_rows(conn: sqlite3.Connection, *, limit: int, ids: list[int] | None = None) -> list[sqlite3.Row]:
    if ids:
        marks = ",".join("?" for _ in ids)
        return conn.execute(
            f"""
            SELECT
              s.id, s.item_url, s.title_original, s.body_original, s.access_status, s.access_note,
              s.image_urls, s.content_kind,
              COALESCE(c.title_zh_hant,'') AS title_zh_hant,
              COALESCE(c.title_zh_hans,'') AS title_zh_hans,
              COALESCE(c.body_zh_hant,'') AS body_zh_hant,
              COALESCE(c.body_zh_hans,'') AS body_zh_hans,
              COALESCE(c.listing_media_json,'[]') AS listing_media_json
            FROM source_items s
            LEFT JOIN content_items c ON c.source_item_id = s.id
            WHERE s.id IN ({marks})
            ORDER BY s.id DESC
            """,
            ids,
        ).fetchall()
    return conn.execute(
        """
        SELECT
          s.id, s.item_url, s.title_original, s.body_original, s.access_status, s.access_note,
          s.image_urls, s.content_kind,
          COALESCE(c.title_zh_hant,'') AS title_zh_hant,
          COALESCE(c.title_zh_hans,'') AS title_zh_hans,
          COALESCE(c.body_zh_hant,'') AS body_zh_hant,
          COALESCE(c.body_zh_hans,'') AS body_zh_hans,
          COALESCE(c.listing_media_json,'[]') AS listing_media_json
        FROM source_items s
        LEFT JOIN content_items c ON c.source_item_id = s.id
        WHERE COALESCE(s.content_kind,'') = 'jp_listing'
          AND COALESCE(s.access_status,'public') = 'public'
          AND (lower(COALESCE(s.item_url,'')) LIKE '%homes.co.jp%' OR lower(COALESCE(s.item_url,'')) LIKE '%homes.jp%')
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 1), 500000)),),
    ).fetchall()


def delist_ended_homes_without_trusted_images(
    *,
    limit: int = 200000,
    ids: list[int] | None = None,
    dry_run: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns_conn = conn is None
    ctx = get_conn() if owns_conn else None
    db = ctx.__enter__() if ctx is not None else conn
    assert db is not None
    try:
        rows = _candidate_rows(db, limit=limit, ids=ids)
        matches: list[sqlite3.Row] = [row for row in rows if should_delist_ended_homes_without_trusted_images(row)]
        sample = [
            {
                "source_item_id": int(_row_get(row, "id", 0) or 0),
                "title_original": str(_row_get(row, "title_original", "") or "")[:120],
                "item_url": str(_row_get(row, "item_url", "") or ""),
            }
            for row in matches[:30]
        ]
        updated = 0
        if matches and not dry_run:
            updates = [
                (
                    "restricted",
                    ENDED_HOMES_NO_TRUSTED_IMAGE_REASON,
                    int(_row_get(row, "id", 0) or 0),
                )
                for row in matches
                if int(_row_get(row, "id", 0) or 0) > 0
            ]
            cur = db.executemany(
                """
                UPDATE source_items
                SET access_status = ?,
                    access_note = ?,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND COALESCE(access_status,'public') = 'public'
                """,
                updates,
            )
            updated = max(0, int(getattr(cur, "rowcount", 0) or 0))
            db.commit()
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "scanned_rows": len(rows),
            "matched_rows": len(matches),
            "updated_rows": int(updated),
            "reason": ENDED_HOMES_NO_TRUSTED_IMAGE_REASON,
            "sample": sample,
        }
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)
