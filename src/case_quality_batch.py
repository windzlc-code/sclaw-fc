from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote

from src.config import DATA_DIR
from src.crawler import crawl_item_url
from src.case_delist import (
    case_listing_body_indicates_ended,
    delist_ended_homes_without_trusted_images,
    trusted_property_gallery_urls,
)
from src.db import get_conn
from src.homes_media_token import (
    homes_ielove_image_group_key,
    homes_is_canonical_listing_image_candidate,
    homes_listing_image_tokens,
)
from src.live_enrich_urls import live_enrich_eligible_url
from src.pipeline import process_crawled_items
from src.source_registry import REMAINING_THREE_PORTAL_HOSTS
from src.thumb_backfill_service import run_empty_image_backfill


_STATE_LOCK = threading.Lock()
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
_STATE_PATH = DATA_DIR / "case_quality_batch_state.json"
_LOCK_PATH = DATA_DIR / "case_quality_batch.lock"
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+)", re.I)
_QC_KEYS = ("所在地", "住所", "沿線・駅", "交通", "専有面積", "間取り", "築年月", "所在階")
_IMAGE_RESTRICTED_NOTE_RE = re.compile(
    r"(?:可信圖片|未取得本案可信圖片|無法抓取|空白圖片|推薦物件照片|掲載終了|已下架)",
    re.I,
)

_STATE: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "status": "idle",
    "message": "每日案件品質批次尚未啟動",
    "last_started_at": "",
    "last_finished_at": "",
    "next_run_at": "",
    "next_run_at_taipei": "",
    "last_report": {},
}


class _RecrawlTimeout(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _taipei_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _mask(value: Any) -> str:
    return _SECRET_RE.sub("***", str(value or ""))[:700]


def _env_truthy(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip() or default)
    except Exception:
        value = int(default)
    return max(low, min(high, value))


def _env_float(name: str, default: float, *, low: float, high: float) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip() or default)
    except Exception:
        value = float(default)
    return max(low, min(high, value))


def _parse_taipei_slot(raw: str | None, default: str = "04:30") -> tuple[int, int]:
    text = str(raw or default).strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", text)
    if not m:
        text = default
        m = re.fullmatch(r"(\d{1,2})(?::(\d{1,2}))?", text)
    hour = int(m.group(1)) if m else 4
    minute = int(m.group(2) or 0) if m else 30
    return max(0, min(23, hour)), max(0, min(59, minute))


def _next_taipei_run(slot: str | None = None) -> tuple[datetime, datetime, float]:
    hour, minute = _parse_taipei_slot(slot or os.getenv("SCLAW_DAILY_CASE_QUALITY_AT") or "04:30")
    now_tw = _taipei_now()
    next_tw = now_tw.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_tw <= now_tw:
        next_tw += timedelta(days=1)
    next_utc = next_tw.astimezone(timezone.utc)
    return next_utc, next_tw, max(30.0, (next_tw - now_tw).total_seconds())


def _write_state() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(_STATE, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_state_from_disk() -> None:
    try:
        if not _STATE_PATH.is_file():
            return
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _STATE.update(data)
    except Exception:
        pass


def _set_state(**patch: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(patch)
        _write_state()


def case_quality_batch_status() -> dict[str, Any]:
    with _STATE_LOCK:
        if not _STATE.get("last_started_at") and _STATE_PATH.is_file():
            _load_state_from_disk()
        out = dict(_STATE)
        out["last_report"] = dict(out.get("last_report") or {})
        return out


def _acquire_file_lock(stale_after_sec: int) -> int | None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if _LOCK_PATH.exists():
            age = time.time() - _LOCK_PATH.stat().st_mtime
            if age > max(1800, int(stale_after_sec or 0)):
                _LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        fd = os.open(str(_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps({"pid": os.getpid(), "started_at": _now_iso()}).encode("utf-8"))
        return fd
    except FileExistsError:
        return None
    except Exception:
        return None


def _release_file_lock(fd: int | None) -> None:
    try:
        if fd is not None:
            os.close(fd)
    except Exception:
        pass
    try:
        _LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _split_image_lines(raw: Any) -> list[str]:
    return [x.strip() for x in str(raw or "").splitlines() if x.strip()]


def _row_needs_quality_recrawl(row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    imgs = _split_image_lines(row.get("image_urls"))
    body = str(row.get("body_original") or "")
    missing = sum(1 for key in _QC_KEYS if key not in body)
    bad_image_tokens = ("gazo%2fkaisha", "/kaisha/", "tantou", "staff", "portrait", "avatar")
    image_bad = (not imgs) or all(any(token in img.lower() for token in bad_image_tokens) for img in imgs[:3])
    body_too_short = len(body.strip()) < 120
    needs = bool(image_bad or missing >= 6 or body_too_short)
    return needs, {
        "image_count": len(imgs),
        "image_bad": bool(image_bad),
        "missing_field_count": int(missing),
        "body_too_short": bool(body_too_short),
    }


def pick_quality_recrawl_candidates(*, limit: int = 40, scan_limit: int = 8000) -> list[dict[str, Any]]:
    lim = max(0, min(int(limit or 0), 500))
    if lim <= 0:
        return []
    scan = max(500, min(int(scan_limit or 8000), 50000))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.item_url,
                   COALESCE(s.image_urls,'') AS image_urls,
                   COALESCE(s.body_original,'') AS body_original,
                   COALESCE(s.title_original,'') AS title_original
            FROM source_items s
            LEFT JOIN content_items c ON c.source_item_id = s.id
            WHERE COALESCE(s.content_kind,'') = 'jp_listing'
              AND COALESCE(trim(s.item_url),'') LIKE 'http%'
            ORDER BY COALESCE(s.last_checked_at, s.crawled_at, '') ASC, s.id ASC
            LIMIT ?
            """,
            (scan,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        url = str(d.get("item_url") or "").strip()
        if not live_enrich_eligible_url(url):
            continue
        needs, reason = _row_needs_quality_recrawl(d)
        if not needs:
            continue
        out.append(
            {
                "id": int(d.get("id") or 0),
                "item_url": url,
                "title_original": str(d.get("title_original") or "")[:180],
                **reason,
            }
        )
        if len(out) >= lim:
            break
    return out


def _crawl_worker(url: str, out_q) -> None:
    try:
        out_q.put(("ok", crawl_item_url(url)))
    except Exception as exc:
        out_q.put(("err", f"{type(exc).__name__}: {exc}"))


def _crawl_item_url_hard_timeout(url: str, timeout_s: int):
    if timeout_s <= 0:
        return crawl_item_url(url)
    ctx = mp.get_context("fork")
    out_q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_crawl_worker, args=(url, out_q))
    proc.daemon = True
    proc.start()
    proc.join(timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(2)
        if proc.is_alive():
            proc.kill()
            proc.join(2)
        raise _RecrawlTimeout(f"recrawl item timed out after {timeout_s}s")
    try:
        status, payload = out_q.get_nowait()
    except queue.Empty:
        return []
    if status == "ok":
        return payload or []
    raise RuntimeError(str(payload))


def recrawl_quality_candidates(
    candidates: list[dict[str, Any]],
    *,
    timeout_s: int = 45,
    process_batch_size: int = 12,
) -> dict[str, Any]:
    pending_items: list[Any] = []
    pending_ids: list[int] = []
    touched: list[int] = []
    ok = err = skip = 0

    def flush_pending() -> None:
        nonlocal ok, pending_items, pending_ids
        if not pending_items:
            return
        try:
            processed = int(process_crawled_items(pending_items) or 0)
        except Exception as exc:
            print(
                f"quality_recrawl_batch_process_error size={len(pending_items)} "
                f"error={type(exc).__name__}: {str(exc)[:220]}",
                flush=True,
            )
        else:
            ok += processed
            touched.extend(pending_ids[:processed])
        pending_items = []
        pending_ids = []

    for idx, row in enumerate(candidates, start=1):
        sid = int(row.get("id") or 0)
        url = str(row.get("item_url") or "").strip()
        if not url:
            skip += 1
            continue
        if idx == 1 or idx % 20 == 0 or idx == len(candidates):
            print(f"quality_recrawl_progress={idx}/{len(candidates)} ok={ok} err={err} skip={skip} id={sid}", flush=True)
        try:
            items = _crawl_item_url_hard_timeout(url, max(0, int(timeout_s or 0)))
        except _RecrawlTimeout as exc:
            print(f"quality_recrawl_timeout id={sid} error={exc}", flush=True)
            err += 1
            continue
        except Exception as exc:
            if err < 10:
                print(f"quality_recrawl_error id={sid} error={type(exc).__name__}: {str(exc)[:220]}", flush=True)
            err += 1
            continue
        if not items:
            skip += 1
            continue
        pending_items.extend(items)
        pending_ids.append(sid)
        if len(pending_items) >= max(1, int(process_batch_size or 1)):
            flush_pending()
    flush_pending()
    return {
        "candidate_count": len(candidates),
        "ok": int(ok),
        "err": int(err),
        "skip": int(skip),
        "touched_ids": touched[:200],
    }


def _parse_listing_media_count(raw: Any) -> int:
    try:
        data = json.loads(str(raw or "[]"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def _listing_media_urls_from_json(raw: Any) -> list[str]:
    try:
        data = json.loads(str(raw or "[]"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    keys = ("url", "src", "image", "image_url", "imageUrl", "largeUrl", "large_url", "photo", "thumb", "original")
    out: list[str] = []
    for item in data:
        u = ""
        if isinstance(item, str):
            u = item.strip()
        elif isinstance(item, dict):
            for key in keys:
                v = str(item.get(key) or "").strip()
                if v.startswith("http"):
                    u = v
                    break
        if u.startswith("http") and u not in out:
            out.append(u)
    return out


def _homes_media_has_mixed_ownership(item_url: Any, urls: list[str]) -> bool:
    u = str(item_url or "").lower()
    if "homes.co.jp" not in u and "homes.jp" not in u:
        return False
    groups = {homes_ielove_image_group_key(x) for x in urls if homes_ielove_image_group_key(x)}
    if len(groups) > 1:
        return True
    tokens = homes_listing_image_tokens(str(item_url or ""))
    if not tokens:
        return False
    for raw in urls:
        if not homes_is_canonical_listing_image_candidate(raw):
            continue
        try:
            decoded = unquote(str(raw or "")).lower()
        except Exception:
            decoded = str(raw or "").lower()
        if not any(tok in decoded for tok in tokens):
            return True
    return False


def _listing_media_json_from_gallery(urls: list[str]) -> str:
    return json.dumps(
        [
            {
                "type": "image",
                "url": u,
                "source": "case_quality_verified_gallery_restore",
                "note": "verified_property_gallery",
            }
            for u in urls
            if str(u or "").strip()
        ],
        ensure_ascii=False,
    )


def _cached_listing_body_needs_rebuild(body_hant: str, body_hans: str) -> bool:
    hant = str(body_hant or "").strip()
    hans = str(body_hans or "").strip()
    generated_cache = hant.lstrip().startswith("日本房產案源") or hans.lstrip().startswith("日本房产案源")
    polluted_cache = bool(
        generated_cache
        and re.search(
            r"(?:所在地：.*(?:交通|地図を見る|地図を確認)|建物構造：.{60,}|物件名：.*(?:價格|价格|格局|專有面積|专有面积))",
            hant,
            flags=re.S,
        )
    )
    return len(hant) < 80 or len(hans) < 80 or generated_cache or polluted_cache


def restore_restricted_cases_with_verified_gallery(
    *,
    limit: int = 50000,
    min_gallery: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Restore restricted listing rows that now have a verified property gallery.

    This closes the loop after image-token rules or backfills improve: rows that
    were restricted only because no trusted gallery existed can become public
    again, while ended or still-incomplete rows remain protected.
    """
    lim = max(1, min(int(limit or 1), 500000))
    min_g = max(1, min(int(min_gallery or 1), 12))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              s.id AS source_item_id,
              s.id,
              s.source_name,
              s.item_url,
              s.title_original,
              COALESCE(s.body_original,'') AS body_original,
              COALESCE(s.image_urls,'') AS image_urls,
              COALESCE(s.content_kind,'') AS content_kind,
              COALESCE(s.access_status,'public') AS access_status,
              COALESCE(s.access_note,'') AS access_note,
              COALESCE(s.last_checked_at,'') AS last_checked_at,
              COALESCE(c.id, 0) AS content_id,
              COALESCE(c.title_zh_hant,'') AS title_zh_hant,
              COALESCE(c.title_zh_hans,'') AS title_zh_hans,
              COALESCE(c.body_zh_hant,'') AS body_zh_hant,
              COALESCE(c.body_zh_hans,'') AS body_zh_hans,
              COALESCE(c.listing_media_json,'[]') AS listing_media_json
            FROM source_items s
            LEFT JOIN content_items c ON c.source_item_id = s.id
            WHERE COALESCE(s.content_kind,'') = 'jp_listing'
              AND COALESCE(s.access_status,'public') != 'public'
            ORDER BY s.id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()

    restore_ids: list[int] = []
    media_updates: list[tuple[str, int]] = []
    body_updates: list[tuple[str, str, int]] = []
    sample: list[dict[str, Any]] = []
    skipped_ended = skipped_note = skipped_gallery = skipped_content = 0

    try:
        from src.pipeline import _build_listing_zh_fallback
    except Exception:
        _build_listing_zh_fallback = None  # type: ignore[assignment]

    for row in rows:
        d = dict(row)
        sid = int(d.get("source_item_id") or d.get("id") or 0)
        note = str(d.get("access_note") or "")
        if not _IMAGE_RESTRICTED_NOTE_RE.search(note):
            skipped_note += 1
            continue
        if case_listing_body_indicates_ended(d):
            skipped_ended += 1
            continue
        gallery = trusted_property_gallery_urls(d, limit=24)
        if len(gallery) < min_g:
            skipped_gallery += 1
            continue
        restore_ids.append(sid)
        if int(d.get("content_id") or 0) <= 0:
            skipped_content += 1
        else:
            current_media_count = _parse_listing_media_count(d.get("listing_media_json"))
            current_media_urls = _listing_media_urls_from_json(d.get("listing_media_json"))
            if (
                current_media_count <= 0
                or len(gallery) >= current_media_count
                or _homes_media_has_mixed_ownership(d.get("item_url"), current_media_urls)
            ):
                media_updates.append((_listing_media_json_from_gallery(gallery), sid))
            if _build_listing_zh_fallback and _cached_listing_body_needs_rebuild(
                str(d.get("body_zh_hant") or ""),
                str(d.get("body_zh_hans") or ""),
            ):
                try:
                    fb_hant, fb_hans = _build_listing_zh_fallback(d)
                except Exception:
                    fb_hant = fb_hans = ""
                if fb_hant and fb_hans:
                    body_updates.append((fb_hant, fb_hans, sid))
        if len(sample) < 40:
            sample.append(
                {
                    "source_item_id": sid,
                    "source_name": str(d.get("source_name") or ""),
                    "title_original": str(d.get("title_original") or "")[:140],
                    "gallery_count": len(gallery),
                    "item_url": str(d.get("item_url") or ""),
                }
            )

    restored = media_fixed = body_fixed = 0
    if restore_ids and not dry_run:
        with get_conn() as conn:
            cur = conn.executemany(
                """
                UPDATE source_items
                SET access_status = 'public',
                    access_note = '',
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                [(sid,) for sid in restore_ids if sid > 0],
            )
            restored = max(0, int(getattr(cur, "rowcount", 0) or 0))
            if media_updates:
                cur = conn.executemany(
                    """
                    UPDATE content_items
                    SET listing_media_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE source_item_id = ?
                    """,
                    media_updates,
                )
                media_fixed = max(0, int(getattr(cur, "rowcount", 0) or 0))
            if body_updates:
                cur = conn.executemany(
                    """
                    UPDATE content_items
                    SET body_zh_hant = ?, body_zh_hans = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE source_item_id = ?
                    """,
                    body_updates,
                )
                body_fixed = max(0, int(getattr(cur, "rowcount", 0) or 0))
            conn.commit()

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "scanned_rows": len(rows),
        "matched_rows": len(restore_ids),
        "updated_rows": int(restored if not dry_run else 0),
        "media_fixed_rows": int(media_fixed if not dry_run else 0),
        "body_fixed_rows": int(body_fixed if not dry_run else 0),
        "would_update_rows": int(len(restore_ids)) if dry_run else 0,
        "skipped_note_rows": int(skipped_note),
        "skipped_ended_rows": int(skipped_ended),
        "skipped_gallery_rows": int(skipped_gallery),
        "skipped_missing_content_rows": int(skipped_content),
        "min_gallery": min_g,
        "sample": sample,
    }


def _run_public_no_verified_gallery_guard(
    *,
    limit: int,
    dry_run: bool,
    timeout_s: int = 900,
) -> dict[str, Any]:
    script = DATA_DIR.parent / "scripts" / "delist_public_no_verified_gallery_cases.py"
    if not script.is_file():
        return {"ok": False, "error": f"missing script: {script}"}
    args = [sys.executable, str(script), "--limit", str(max(1, min(int(limit or 1), 500000)))]
    if dry_run:
        args.append("--dry-run")
    try:
        proc = subprocess.run(
            args,
            cwd=str(DATA_DIR.parent),
            capture_output=True,
            text=True,
            timeout=max(60, int(timeout_s or 900)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"public no-image guard timed out after {timeout_s}s",
            "stdout_tail": _mask((exc.stdout or "")[-1200:]),
            "stderr_tail": _mask((exc.stderr or "")[-1200:]),
        }
    stdout = str(proc.stdout or "").strip()
    report: dict[str, Any]
    try:
        report = json.loads(stdout or "{}")
    except Exception:
        report = {"ok": False, "error": "could not parse guard JSON output", "stdout_tail": _mask(stdout[-1600:])}
    report["returncode"] = int(proc.returncode)
    if proc.stderr:
        report["stderr_tail"] = _mask(proc.stderr[-1600:])
    if proc.returncode != 0:
        report["ok"] = False
        report.setdefault("error", f"public no-image guard exited with {proc.returncode}")
    return report


def run_case_quality_batch_once(
    *,
    reason: str = "manual",
    dry_run: bool = False,
    backfill_limit: int | None = None,
    remaining_three_limit: int | None = None,
    force_refresh_limit: int | None = None,
    recrawl_limit: int | None = None,
    scan_limit: int | None = None,
    sleep_s: float | None = None,
    invalidate_caches: Callable[[], None] | None = None,
) -> dict[str, Any]:
    bf_limit = _env_int("SCLAW_DAILY_CASE_QUALITY_BACKFILL_LIMIT", 120, low=0, high=5000) if backfill_limit is None else max(0, min(int(backfill_limit), 5000))
    rem_limit = _env_int("SCLAW_DAILY_CASE_QUALITY_REMAINING_LIMIT", 60, low=0, high=1000) if remaining_three_limit is None else max(0, min(int(remaining_three_limit), 1000))
    force_limit = _env_int("SCLAW_DAILY_CASE_QUALITY_FORCE_LIMIT", 12, low=0, high=500) if force_refresh_limit is None else max(0, min(int(force_refresh_limit), 500))
    rc_limit = _env_int("SCLAW_DAILY_CASE_QUALITY_RECRAWL_LIMIT", 40, low=0, high=500) if recrawl_limit is None else max(0, min(int(recrawl_limit), 500))
    delist_limit = _env_int("SCLAW_DAILY_CASE_QUALITY_DELIST_LIMIT", 200000, low=0, high=500000)
    public_image_guard_limit = _env_int("SCLAW_DAILY_CASE_QUALITY_PUBLIC_IMAGE_GUARD_LIMIT", 200000, low=0, high=500000)
    scan = _env_int("SCLAW_DAILY_CASE_QUALITY_SCAN_LIMIT", 8000, low=500, high=50000) if scan_limit is None else max(500, min(int(scan_limit), 50000))
    sleep = _env_float("SCLAW_DAILY_CASE_QUALITY_SLEEP", 0.35, low=0.0, high=5.0) if sleep_s is None else max(0.0, min(float(sleep_s), 5.0))

    fd = _acquire_file_lock(stale_after_sec=8 * 60 * 60)
    if fd is None:
        skipped = {"ok": True, "skipped": True, "reason": "another quality batch is running", "finished_at": _now_iso()}
        _set_state(running=False, status="skipped", message="已有另一個案件品質批次正在執行，本輪先略過。", last_report=skipped)
        return skipped

    started_at = _now_iso()
    _set_state(
        enabled=True,
        running=True,
        status="running",
        message="每日案件品質批次執行中：補圖、刷新低品質圖文、重抓缺欄案件。",
        last_started_at=started_at,
    )
    report: dict[str, Any] = {
        "ok": False,
        "reason": reason,
        "dry_run": bool(dry_run),
        "started_at": started_at,
        "limits": {
            "backfill_limit": bf_limit,
            "remaining_three_limit": rem_limit,
            "force_refresh_limit": force_limit,
            "recrawl_limit": rc_limit,
            "delist_limit": delist_limit,
            "public_image_guard_limit": public_image_guard_limit,
            "scan_limit": scan,
        },
    }
    try:
        if bf_limit > 0:
            report["image_backfill"] = run_empty_image_backfill(
                host_filter=None,
                limit=bf_limit,
                sleep_s=sleep,
                dry_run=bool(dry_run),
                force=False,
            )
        else:
            report["image_backfill"] = {"processed_rows": 0, "ok": 0, "err": 0, "skip": 0, "disabled": True}

        if rem_limit > 0:
            report["remaining_three_image_backfill"] = run_empty_image_backfill(
                host_filter=frozenset(REMAINING_THREE_PORTAL_HOSTS),
                limit=rem_limit,
                sleep_s=sleep,
                dry_run=bool(dry_run),
                force=False,
            )
        else:
            report["remaining_three_image_backfill"] = {"processed_rows": 0, "ok": 0, "err": 0, "skip": 0, "disabled": True}

        if force_limit > 0:
            report["image_refresh"] = run_empty_image_backfill(
                host_filter=None,
                limit=force_limit,
                sleep_s=sleep,
                dry_run=bool(dry_run),
                force=True,
            )
        else:
            report["image_refresh"] = {"processed_rows": 0, "ok": 0, "err": 0, "skip": 0, "disabled": True}

        candidates = pick_quality_recrawl_candidates(limit=rc_limit, scan_limit=scan)
        report["quality_recrawl_candidates"] = candidates[:80]
        if dry_run or rc_limit <= 0:
            report["quality_recrawl"] = {"candidate_count": len(candidates), "ok": 0, "err": 0, "skip": 0, "dry_run": bool(dry_run)}
        else:
            report["quality_recrawl"] = recrawl_quality_candidates(candidates)

        report["restricted_verified_gallery_restore"] = restore_restricted_cases_with_verified_gallery(
            limit=delist_limit or 50000,
            min_gallery=3,
            dry_run=bool(dry_run),
        )

        if delist_limit > 0:
            report["ended_homes_delist"] = delist_ended_homes_without_trusted_images(
                limit=delist_limit,
                dry_run=bool(dry_run),
            )
        else:
            report["ended_homes_delist"] = {"ok": True, "disabled": True, "matched_rows": 0, "updated_rows": 0}

        if public_image_guard_limit > 0:
            report["public_no_verified_gallery_guard"] = _run_public_no_verified_gallery_guard(
                limit=public_image_guard_limit,
                dry_run=bool(dry_run),
            )
        else:
            report["public_no_verified_gallery_guard"] = {
                "ok": True,
                "disabled": True,
                "matched_rows": 0,
                "updated_rows": 0,
            }

        if invalidate_caches and not dry_run:
            try:
                invalidate_caches()
            except Exception as exc:
                report["cache_invalidate_error"] = _mask(exc)
        report["ok"] = True
        report["finished_at"] = _now_iso()
        fixed = sum(int((report.get(key) or {}).get("ok") or 0) for key in ("image_backfill", "remaining_three_image_backfill", "image_refresh", "quality_recrawl"))
        message = f"每日案件品質批次完成：補圖/刷新/重抓成功 {fixed} 筆，候選重抓 {len(candidates)} 筆。"
        _set_state(running=False, status="done", message=message, last_finished_at=report["finished_at"], last_report=report)
        return report
    except Exception as exc:
        report["ok"] = False
        report["error"] = _mask(exc)
        report["finished_at"] = _now_iso()
        _set_state(
            running=False,
            status="failed",
            message=f"每日案件品質批次失敗：{_mask(exc)}",
            last_finished_at=report["finished_at"],
            last_report=report,
        )
        return report
    finally:
        _release_file_lock(fd)


def _worker_loop(invalidate_caches: Callable[[], None] | None) -> None:
    while True:
        next_utc, next_tw, sleep_seconds = _next_taipei_run()
        _set_state(
            enabled=True,
            running=False,
            next_run_at=next_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            next_run_at_taipei=next_tw.replace(microsecond=0).isoformat(),
            message="每日案件品質批次已啟動，等待固定時間執行。",
        )
        time.sleep(sleep_seconds)
        run_case_quality_batch_once(reason="daily_scheduled", invalidate_caches=invalidate_caches)


def start_case_quality_batch_worker(*, invalidate_caches: Callable[[], None] | None = None) -> bool:
    global _WORKER_STARTED
    if not _env_truthy("SCLAW_ENABLE_DAILY_CASE_QUALITY_BATCH", True):
        _set_state(enabled=False, running=False, status="disabled", message="每日案件品質批次已停用")
        return False
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return True
        _load_state_from_disk()
        t = threading.Thread(
            target=_worker_loop,
            args=(invalidate_caches,),
            daemon=True,
            name="case-quality-daily-batch",
        )
        t.start()
        _WORKER_STARTED = True
    return True
