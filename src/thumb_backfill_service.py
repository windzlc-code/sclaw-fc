"""批次補齊 source_items.image_urls（與 fetch_property_detail／live_enrich_eligible_url 一致）。"""

from __future__ import annotations

import contextlib
import multiprocessing as mp
import queue
import signal
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from src.crawler import BROWSER_HEADERS
from src.db import get_conn
from src.live_enrich_urls import live_enrich_eligible_url
from src.portal_property_crawl import fetch_property_detail


class _BackfillTimeout(Exception):
    pass


@contextlib.contextmanager
def _item_timeout(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handle_timeout(_signum, _frame):
        raise _BackfillTimeout(f"backfill item timed out after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _host_key(url: str) -> str:
    u = (url or "").lower()
    for h in (
        "suumo.jp",
        "homes.co.jp",
        "athome.co.jp",
        "realestate.yahoo.co.jp",
        "realestate.rakuten.co.jp",
        "yes1.co.jp",
        "oheya-su.jp",
    ):
        if h in u:
            return h
    return ""


def _looks_like_blocked_detail_response(title: str, body: str) -> bool:
    text = f"{title}\n{body}".lower()
    return any(
        token in text
        for token in (
            "認証中",
            "認證中",
            "认证中",
            "click to verify",
            "captcha",
            "human verification",
            "awswaf",
            "通常のサイト閲覧を超える速度",
        )
    )


def _timeout_for_url(item_url: str) -> float:
    ul = item_url.lower()
    if "realestate.yahoo.co.jp" in ul and ("/land/search/" in ul or "/used/mansion/search/" in ul):
        return 24.0
    if "homes.co.jp" in ul:
        return 22.0
    if "realestate.rakuten.co.jp" in ul or "yes1.co.jp" in ul:
        return 22.0
    return 18.0


def _fetch_detail_worker(item_url: str, timeout_s: float, fallback_context: str, out_q) -> None:
    try:
        with httpx.Client(timeout=float(timeout_s), follow_redirects=True, headers=BROWSER_HEADERS) as client:
            out_q.put(("ok", fetch_property_detail(client, item_url, fallback_context=fallback_context)))
    except Exception as exc:
        out_q.put(("err", f"{type(exc).__name__}: {exc}"))


def _fetch_detail_hard_timeout(
    item_url: str,
    timeout_s: int,
    *,
    fallback_context: str = "",
) -> tuple[str, str, list[str]]:
    if timeout_s <= 0:
        with httpx.Client(timeout=_timeout_for_url(item_url), follow_redirects=True, headers=BROWSER_HEADERS) as client:
            return fetch_property_detail(client, item_url, fallback_context=fallback_context)
    ctx = mp.get_context("fork")
    out_q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_fetch_detail_worker, args=(item_url, float(timeout_s), fallback_context, out_q))
    proc.daemon = True
    proc.start()
    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        if proc.is_alive():
            proc.kill()
            proc.join(2)
        raise _BackfillTimeout(f"backfill item timed out after {timeout_s}s")
    try:
        status, payload = out_q.get_nowait()
    except queue.Empty:
        return "", "", []
    if status == "ok":
        title, body_original, imgs = payload
        return str(title or ""), str(body_original or ""), list(imgs or [])
    raise RuntimeError(str(payload))


def _merge_image_lines(existing: str, new_imgs: list[str], *, item_url: str = "") -> str:
    old = [x.strip() for x in str(existing or "").splitlines() if x.strip()]
    try:
        from src.homes_media_token import merge_homes_listing_image_urls

        merged = merge_homes_listing_image_urls(str(item_url or ""), list(new_imgs or []), old)
    except Exception:
        merged = list(dict.fromkeys([*(new_imgs or []), *old]))
    return "\n".join(merged[:60])


def run_empty_image_backfill(
    *,
    host_filter: frozenset[str] | None,
    limit: int,
    sleep_s: float,
    dry_run: bool,
    force: bool = False,
) -> dict[str, Any]:
    """host_filter 為 None 時含所有支援門戶；否則僅處理該 host key 集合（例：後三站）。"""
    lim = max(1, min(int(limit or 30), 5000))
    now = datetime.now(timezone.utc).isoformat()

    where_extra = ""
    if not force:
        where_extra = " AND (trim(COALESCE(s.image_urls,'')) = '' OR length(trim(COALESCE(s.image_urls,''))) < 8)"

    host_sql = ""
    sql_params: list[Any] = []
    if host_filter:
        clauses = []
        for host in sorted(host_filter):
            clauses.append("lower(s.item_url) LIKE ?")
            sql_params.append(f"%{host}%")
        host_sql = " AND (" + " OR ".join(clauses) + ")"

    sql = f"""
        SELECT s.id, s.item_url,
               COALESCE(s.image_urls,'') AS image_urls,
               COALESCE(s.title_original,'') AS title_original,
               COALESCE(s.body_original,'') AS body_original
         FROM source_items s
         WHERE COALESCE(trim(s.item_url),'') LIKE 'http%'
         AND COALESCE(s.content_kind,'') = 'jp_listing'
         {where_extra}
         {host_sql}
         ORDER BY s.last_checked_at ASC, s.id ASC
         LIMIT ?
     """

    fetch_cap = lim * (12 if host_filter else 5)
    fetch_cap = max(fetch_cap, lim * 3)
    with get_conn() as conn:
        rows = conn.execute(sql, (*sql_params, fetch_cap)).fetchall()

    picked: list[dict[str, Any]] = []
    for r in rows:
        url = str(r["item_url"] or "").strip()
        if not live_enrich_eligible_url(url):
            continue
        hk = _host_key(url)
        if host_filter is not None and hk not in host_filter:
            continue
        picked.append(dict(r))
        if len(picked) >= lim:
            break

    if not picked:
        return {
            "processed_rows": 0,
            "ok": 0,
            "err": 0,
            "skip": 0,
            "dry_run": dry_run,
            "force": force,
            "message": "no rows matched filters",
        }

    ok = err = skip = 0
    total = len(picked)
    for i, row in enumerate(picked):
        sid = int(row["id"])
        item_url = str(row["item_url"] or "").strip()
        if i == 0 or (i + 1) % 25 == 0 or (i + 1) == total:
            print(
                f"backfill_progress={i + 1}/{total} ok={ok} err={err} skip={skip} current_id={sid}",
                flush=True,
            )
        if sleep_s > 0 and i > 0:
            time.sleep(float(sleep_s))
        to = _timeout_for_url(item_url)
        fallback_context = "\n".join(
            [
                str(row.get("title_original") or ""),
                str(row.get("body_original") or ""),
            ]
        )[:12000]
        try:
            title, body_original, imgs = _fetch_detail_hard_timeout(
                item_url,
                int(max(to + 8.0, 30.0)),
                fallback_context=fallback_context,
            )
        except _BackfillTimeout as exc:
            print(f"backfill_timeout id={sid} url={item_url} error={exc}", flush=True)
            err += 1
            continue
        except Exception as exc:
            if err < 10 or (err + 1) % 100 == 0:
                print(
                    f"backfill_error id={sid} host={_host_key(item_url)} "
                    f"error={type(exc).__name__}: {str(exc)[:220]}",
                    flush=True,
                )
            err += 1
            continue
        if not str(body_original or "").strip() and not (imgs or []):
            skip += 1
            continue
        img_lines = _merge_image_lines(row.get("image_urls") or "", list(imgs or []), item_url=item_url)
        blocked_response = _looks_like_blocked_detail_response(str(title or ""), str(body_original or ""))
        if blocked_response:
            skip += 1
            continue
        else:
            title_new = (
                str(title).strip()[:200]
                if title and str(title).strip()
                else str(row.get("title_original") or "")[:200]
            )
            body_new = str(body_original or "").strip()
        if dry_run:
            ok += 1
            continue
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE source_items
                SET title_original = ?,
                    body_original = ?,
                    image_urls = ?,
                    last_checked_at = ?,
                    crawled_at = ?
                WHERE id = ?
                """,
                (
                    title_new,
                    body_new,
                    img_lines,
                    now,
                    now,
                    sid,
                ),
            )
            conn.commit()
        ok += 1

    return {
        "processed_rows": len(picked),
        "ok": ok,
        "err": err,
        "skip": skip,
        "dry_run": dry_run,
        "force": force,
        "hosts": sorted(host_filter) if host_filter else None,
    }


def enrich_single_source_item_by_id(
    source_item_id: int, *, force: bool = True, dry_run: bool = False
) -> dict[str, Any]:
    """單筆案件：自 item_url 呼叫 fetch_property_detail，合併寫回 title／body／image_urls。"""
    sid = int(source_item_id)
    if sid <= 0:
        return {"ok": False, "error": "invalid source_item_id"}

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, item_url,
                   COALESCE(image_urls,'') AS image_urls,
                   COALESCE(title_original,'') AS title_original,
                   COALESCE(body_original,'') AS body_original
            FROM source_items WHERE id = ?
            """,
            (sid,),
        ).fetchone()
    if not row:
        return {"ok": False, "error": "source_item not found"}

    drow = dict(row)
    item_url = str(drow.get("item_url") or "").strip()
    if not item_url.startswith("http"):
        return {"ok": False, "error": "missing or invalid item_url"}

    if not live_enrich_eligible_url(item_url):
        return {"ok": False, "error": "item_url not supported for fetch_property_detail"}

    existing_imgs = str(drow.get("image_urls") or "").strip()
    if not force and len(existing_imgs) >= 8:
        return {
            "ok": False,
            "error": "already has image_urls; pass force=true to merge refetch",
            "skipped": True,
        }

    now = datetime.now(timezone.utc).isoformat()
    to = _timeout_for_url(item_url)
    with httpx.Client(timeout=25.0, follow_redirects=True, headers=BROWSER_HEADERS) as client:
        client.timeout = httpx.Timeout(to)
        try:
            fallback_context = "\n".join(
                [
                    str(drow.get("title_original") or ""),
                    str(drow.get("body_original") or ""),
                ]
            )[:12000]
            title, body_original, imgs = fetch_property_detail(client, item_url, fallback_context=fallback_context)
        except Exception as exc:
            title, body_original, imgs = "", "", []
            primary_err = exc
        else:
            primary_err = None

    # HOMES detail pages are frequently protected by AWS WAF (HTTP 202 + challenge HTML).
    # If static fetch yields no text/images, attempt a Playwright-rendered fetch (best-effort).
    ul = item_url.lower()
    is_homes = ("homes.co.jp" in ul) or ("homes.jp" in ul)
    if is_homes and not str(body_original or "").strip() and not (imgs or []):
        try:
            from src.homes_detail_playwright import fetch_homes_detail_playwright

            pw = fetch_homes_detail_playwright(item_url)
            if pw.get("ok"):
                title = str(pw.get("title") or title or "").strip()
                body_original = str(pw.get("body_original") or body_original or "").strip()
                imgs = list(pw.get("image_urls") or imgs or [])
            else:
                if primary_err is not None:
                    return {"ok": False, "error": f"fetch failed: {primary_err}"[:400], "fallback": pw}
        except Exception as exc:
            if primary_err is not None:
                return {"ok": False, "error": f"fetch failed: {primary_err}"[:400], "fallback_error": str(exc)[:200]}
            # If primary fetch didn't error but returned empty, keep the original empty result.

    if not str(body_original or "").strip() and not (imgs or []):
        return {"ok": False, "error": "empty response from source page"}

    img_lines = _merge_image_lines(drow.get("image_urls") or "", list(imgs or []), item_url=item_url)
    blocked_response = _looks_like_blocked_detail_response(str(title or ""), str(body_original or ""))
    if blocked_response:
        return {
            "ok": False,
            "error": "source returned blocked verification page; data was not overwritten",
            "source_item_id": sid,
            "item_url": item_url,
            "host": _host_key(item_url),
            "blocked_response": True,
            "preserved_existing": True,
        }
    else:
        title_new = (
            str(title).strip()[:200]
            if title and str(title).strip()
            else str(drow.get("title_original") or "")[:200]
        )
        body_new = str(body_original or "").strip()

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "source_item_id": sid,
            "item_url": item_url,
            "host": _host_key(item_url),
            "merged_image_lines": len(img_lines.splitlines()),
            "body_chars": len(body_new),
            "blocked_response": bool(blocked_response),
        }

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE source_items
            SET title_original = ?,
                body_original = ?,
                image_urls = ?,
                last_checked_at = ?,
                crawled_at = ?
            WHERE id = ?
            """,
            (
                title_new,
                body_new,
                img_lines,
                now,
                now,
                sid,
            ),
        )
        conn.commit()

    return {
        "ok": True,
        "source_item_id": sid,
        "item_url": item_url,
        "host": _host_key(item_url),
        "image_urls_lines": len(img_lines.splitlines()),
        "body_chars": len(body_new),
        "blocked_response": bool(blocked_response),
    }
