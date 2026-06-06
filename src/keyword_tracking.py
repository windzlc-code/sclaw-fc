"""Persist user / system keyword usage into keyword_search_* tables."""

from __future__ import annotations

import json
import re
import sqlite3
import time

from src.db import get_conn


def normalize_keyword(keyword: str) -> str:
    cleaned = re.sub(r"\s+", " ", (keyword or "").strip())
    return cleaned[:120]


def keyword_is_tracking_noise(keyword: str) -> bool:
    kw = normalize_keyword(keyword)
    if not kw:
        return True
    if len(kw) <= 1:
        return True
    if kw in {"東", "西", "南", "北", "中", "新", "駅", "站", "租", "買", "卖", "買賣"}:
        return True
    if re.fullmatch(r"[\W_]+", kw):
        return True
    return False


def track_keyword_search(keyword: str, channel: str, filters: dict | None = None) -> None:
    """Best-effort analytics write; must never raise into API handlers."""
    try:
        kw = normalize_keyword(keyword)
        if keyword_is_tracking_noise(kw):
            return
        filters_json = json.dumps(filters or {}, ensure_ascii=False)
        # SQLite 在並發寫入／長讀取時可能短暫 locked；短重試後放棄，不阻斷主流程。
        for attempt in range(8):
            try:
                with get_conn() as conn:
                    conn.execute(
                        """
                        INSERT INTO keyword_search_logs (keyword, channel, filters_json)
                        VALUES (?, ?, ?)
                        """,
                        (kw, channel, filters_json),
                    )
                    conn.execute(
                        """
                        INSERT INTO keyword_search_stats (keyword, channel, search_count, last_filters_json, last_searched_at)
                        VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(keyword, channel)
                        DO UPDATE SET
                            search_count = keyword_search_stats.search_count + 1,
                            last_filters_json = excluded.last_filters_json,
                            last_searched_at = CURRENT_TIMESTAMP
                        """,
                        (kw, channel, filters_json),
                    )
                    conn.commit()
                return
            except sqlite3.OperationalError:
                if attempt < 7:
                    time.sleep(0.05 * (2**attempt))
                    continue
                return
            except sqlite3.DatabaseError:
                return
    except Exception:
        return
