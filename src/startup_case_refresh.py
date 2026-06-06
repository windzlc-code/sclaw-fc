from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.config import DATA_DIR
from src.crawler import crawl_one_source
from src.pipeline import process_crawled_items
from src.source_registry import (
    ensure_seven_jp_portal_sources,
    load_crawl_settings,
    ordered_seven_jp_portal_sources_for_crawl,
)
from src.thumb_backfill_service import run_empty_image_backfill


_STATE_LOCK = threading.Lock()
_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
_STATE_PATH = DATA_DIR / "case_auto_refresh_state.json"
_LOCK_PATH = DATA_DIR / "case_auto_refresh.lock"
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+)", re.I)

_STATE: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "status": "idle",
    "message": "案件自動更新尚未啟動",
    "last_started_at": "",
    "last_finished_at": "",
    "next_run_at": "",
    "last_report": {},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _write_state() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(_STATE, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _set_state(**patch: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(patch)
        _write_state()


def case_auto_refresh_status() -> dict[str, Any]:
    with _STATE_LOCK:
        out = dict(_STATE)
        out["last_report"] = dict(out.get("last_report") or {})
        return out


def _apply_crawl_quality_defaults() -> None:
    defaults = {
        "SCLAW_FAST_JP_LISTING_CONTENT": "1",
        "SCLAW_PORTAL_HUB_CAP": "12",
        "SCLAW_ATHOME_HUB_CAP": "24",
        "SCLAW_REMAINING_PORTAL_HUB_CAP": "10",
        "SCLAW_SUUMO_REQUEST_INTERVAL_SEC": "1.2",
        "SCLAW_SUUMO_HUB_CAP": "10",
        "SCLAW_SUUMO_BUKKEN_MAX_PAGES": "4",
        "SCLAW_SUUMO_CITY_ICHIRAN_CAP": "8",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _acquire_file_lock(stale_after_sec: int) -> int | None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if _LOCK_PATH.exists():
            age = time.time() - _LOCK_PATH.stat().st_mtime
            if age > max(900, int(stale_after_sec or 0)):
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


def _crawl_latest_cases(per_source_limit: int) -> dict[str, Any]:
    ensure_seven_jp_portal_sources()
    sources = ordered_seven_jp_portal_sources_for_crawl()
    source_reports: list[dict[str, Any]] = []
    crawled_total = 0
    processed_total = 0
    for source in sources:
        name = str(source.get("name") or source.get("url") or "source")
        url = str(source.get("url") or "").strip()
        if not url:
            continue
        try:
            items = crawl_one_source(url, per_source_limit=per_source_limit, search_query="")
            crawled = len(items or [])
            processed = int(process_crawled_items(items or [])) if items else 0
            source_reports.append({"source": name, "url": url, "crawled": crawled, "processed": processed})
            crawled_total += crawled
            processed_total += processed
        except Exception as exc:
            source_reports.append({"source": name, "url": url, "crawled": 0, "processed": 0, "error": _mask(exc)})
    return {
        "sources": source_reports,
        "crawled": crawled_total,
        "processed": processed_total,
        "source_count": len(source_reports),
    }


def run_case_auto_refresh_once(
    *,
    reason: str = "startup",
    invalidate_caches: Callable[[], None] | None = None,
) -> dict[str, Any]:
    settings = load_crawl_settings()
    configured_limit = int(settings.get("per_source_limit") or 12)
    per_source = _env_int(
        "SCLAW_CASE_REFRESH_PER_SOURCE_LIMIT",
        min(max(configured_limit, 2), 4),
        low=1,
        high=60,
    )
    backfill_limit = _env_int("SCLAW_CASE_REFRESH_BACKFILL_LIMIT", 30, low=0, high=500)
    force_image_limit = _env_int("SCLAW_CASE_REFRESH_FORCE_IMAGE_LIMIT", 8, low=0, high=120)
    stale_after = max(3600, _case_refresh_interval_seconds() * 2)
    fd = _acquire_file_lock(stale_after)
    if fd is None:
        skipped = {
            "ok": True,
            "skipped": True,
            "reason": "another refresh is running",
            "finished_at": _now_iso(),
        }
        _set_state(
            running=False,
            status="skipped",
            message="已有另一個案件更新正在執行，本輪先略過。",
            last_finished_at=skipped["finished_at"],
            last_report=skipped,
        )
        return skipped

    started_at = _now_iso()
    _set_state(
        enabled=True,
        running=True,
        status="running",
        message="正在抓取最新案件並整理原站高清圖文。",
        last_started_at=started_at,
    )
    report: dict[str, Any] = {
        "ok": False,
        "reason": reason,
        "started_at": started_at,
        "per_source_limit": per_source,
    }
    try:
        _apply_crawl_quality_defaults()
        crawl_report = _crawl_latest_cases(per_source)
        report["crawl"] = crawl_report

        backfill_report: dict[str, Any] = {}
        if backfill_limit > 0:
            backfill_report = run_empty_image_backfill(
                host_filter=None,
                limit=backfill_limit,
                sleep_s=0.25,
                dry_run=False,
                force=False,
            )
        force_report: dict[str, Any] = {}
        if force_image_limit > 0:
            force_report = run_empty_image_backfill(
                host_filter=None,
                limit=force_image_limit,
                sleep_s=0.35,
                dry_run=False,
                force=True,
            )
        report["image_backfill"] = backfill_report
        report["image_refresh"] = force_report
        report["ok"] = True
        report["finished_at"] = _now_iso()
        if invalidate_caches:
            try:
                invalidate_caches()
            except Exception as exc:
                report["cache_invalidate_error"] = _mask(exc)
        message = (
            f"最新案件整理完成：抓取 {int(crawl_report.get('crawled') or 0)} 筆，"
            f"入庫/更新 {int(crawl_report.get('processed') or 0)} 筆，"
            f"補圖 {int(backfill_report.get('ok') or 0) + int(force_report.get('ok') or 0)} 筆。"
        )
        _set_state(
            running=False,
            status="done",
            message=message,
            last_finished_at=report["finished_at"],
            last_report=report,
        )
        return report
    except Exception as exc:
        report["ok"] = False
        report["error"] = _mask(exc)
        report["finished_at"] = _now_iso()
        _set_state(
            running=False,
            status="failed",
            message=f"案件自動更新失敗：{_mask(exc)}",
            last_finished_at=report["finished_at"],
            last_report=report,
        )
        return report
    finally:
        _release_file_lock(fd)


def _case_refresh_interval_seconds() -> int:
    settings = load_crawl_settings()
    default_minutes = max(30, int(settings.get("interval_hours") or 1) * 60)
    minutes = _env_int("SCLAW_CASE_REFRESH_EVERY_MINUTES", default_minutes, low=15, high=24 * 60)
    return int(minutes * 60)


def _worker_loop(invalidate_caches: Callable[[], None] | None, delay_seconds: int) -> None:
    delay = max(0, int(delay_seconds or 0))
    if delay:
        time.sleep(delay)
    while True:
        interval = _case_refresh_interval_seconds()
        next_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
        _set_state(next_run_at=next_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        run_case_auto_refresh_once(reason="startup" if not case_auto_refresh_status().get("last_finished_at") else "scheduled", invalidate_caches=invalidate_caches)
        interval = _case_refresh_interval_seconds()
        next_at = datetime.now(timezone.utc) + timedelta(seconds=interval)
        _set_state(next_run_at=next_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        time.sleep(interval)


def start_case_auto_refresh_worker(*, invalidate_caches: Callable[[], None] | None = None) -> bool:
    global _WORKER_STARTED
    if not _env_truthy("SCLAW_ENABLE_CASE_AUTO_REFRESH", True):
        _set_state(enabled=False, running=False, status="disabled", message="案件自動更新已停用")
        return False
    delay = _env_int("SCLAW_CASE_REFRESH_START_DELAY_SECONDS", 6, low=0, high=600)
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return True
        _set_state(enabled=True, running=False, status="idle", message="案件自動更新已啟動，等待第一輪抓取。")
        t = threading.Thread(
            target=_worker_loop,
            args=(invalidate_caches, delay),
            daemon=True,
            name="case-auto-refresh",
        )
        t.start()
        _WORKER_STARTED = True
    return True
