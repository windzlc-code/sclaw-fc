from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure project root is importable when running `python scripts/...` directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.homes_geo import HOMES_KODATE_CHUKO_PREFS  # noqa: E402
from src.homes_transit_sync import sync_homes_kodate_chuko_transit_pref  # noqa: E402
from src.suumo_transit_sync import sync_suumo_chukoikkodate_transit_pref  # noqa: E402

STATE_PATH = ROOT / "data" / "bot_transit_sync_state.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe(v: object) -> str:
    try:
        return str(v)
    except Exception:
        return ""


def _load_state() -> dict[str, Any]:
    if STATE_PATH.is_file():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"homes_cursor": 0, "suumo_cursor": 0, "updated_at": ""}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Best-effort; do not fail the scheduled task on write issues.
        pass


def _pref_keys() -> list[str]:
    keys = [str(p.key or "").strip().lower() for p in (HOMES_KODATE_CHUKO_PREFS or []) if getattr(p, "key", None)]
    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _pick_batch(keys: list[str], cursor: int, batch: int) -> tuple[list[str], int]:
    if not keys:
        return [], 0
    n = len(keys)
    cur = int(cursor or 0) % n
    b = max(0, int(batch or 0))
    if b <= 0:
        return [], cur
    picked = [keys[(cur + i) % n] for i in range(b)]
    next_cur = (cur + b) % n
    return picked, next_cur


def _run_homes(pref_key: str, *, include_stations: bool, max_lines: int | None, force_refresh: bool) -> dict[str, Any]:
    started = time.time()
    try:
        res = sync_homes_kodate_chuko_transit_pref(
            pref_key,
            force_refresh_lines=bool(force_refresh),
            include_stations=bool(include_stations),
            max_lines=max_lines,
        )
        return {
            "portal": "homes",
            "pref": _safe(res.pref_key),
            "city_area": _safe(res.city_area),
            "lines_seen": int(res.lines_seen),
            "lines_upserted": int(res.lines_upserted),
            "stations_seen": int(res.stations_seen),
            "stations_upserted": int(res.stations_upserted),
            "elapsed_sec": round(float(res.elapsed_sec), 2),
            "ok": True,
        }
    except Exception as exc:
        return {
            "portal": "homes",
            "pref": _safe(pref_key),
            "ok": False,
            "error": _safe(exc)[:220],
            "elapsed_sec": round(time.time() - started, 2),
        }


def _run_suumo(pref_key: str, *, include_stations: bool, max_lines: int | None, include_empty: bool) -> dict[str, Any]:
    started = time.time()
    try:
        res = sync_suumo_chukoikkodate_transit_pref(
            pref_key,
            include_stations=bool(include_stations),
            max_lines=max_lines,
            only_enabled_lines=not bool(include_empty),
        )
        return {
            "portal": "suumo",
            "pref": _safe(res.pref_key),
            "city_area": _safe(res.city_area),
            "lines_seen": int(res.lines_seen),
            "lines_upserted": int(res.lines_upserted),
            "stations_seen": int(res.stations_seen),
            "stations_upserted": int(res.stations_upserted),
            "elapsed_sec": round(float(res.elapsed_sec), 2),
            "ok": True,
        }
    except Exception as exc:
        return {
            "portal": "suumo",
            "pref": _safe(pref_key),
            "ok": False,
            "error": _safe(exc)[:220],
            "elapsed_sec": round(time.time() - started, 2),
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rotate-sync jp_trans_* lines/stations from HOMES + SUUMO prefecture pages.")
    ap.add_argument("--homes-batch", type=int, default=1, help="How many prefectures to sync from HOMES per run.")
    ap.add_argument("--suumo-batch", type=int, default=2, help="How many prefectures to sync from SUUMO per run.")
    ap.add_argument("--max-lines", type=int, default=0, help="Limit enabled lines per pref (0 = all).")
    ap.add_argument("--no-stations", action="store_true", help="Only sync lines (skip station pages).")
    ap.add_argument("--homes-no-refresh", action="store_true", help="Use HOMES cache; do not force-refresh line list.")
    ap.add_argument("--suumo-include-empty", action="store_true", help="Include SUUMO lines with 0 listings.")
    args = ap.parse_args(argv)

    keys = _pref_keys()
    if not keys:
        print(json.dumps({"ok": False, "error": "no prefecture keys found"}, ensure_ascii=True), flush=True)
        return 1

    max_lines = int(args.max_lines or 0)
    max_lines_arg = None if max_lines <= 0 else max_lines

    state = _load_state()
    homes_cursor = int(state.get("homes_cursor") or 0)
    suumo_cursor = int(state.get("suumo_cursor") or 0)

    homes_prefs, homes_next = _pick_batch(keys, homes_cursor, int(args.homes_batch or 0))
    suumo_prefs, suumo_next = _pick_batch(keys, suumo_cursor, int(args.suumo_batch or 0))

    include_stations = not bool(args.no_stations)
    force_refresh = not bool(args.homes_no_refresh)
    include_empty = bool(args.suumo_include_empty)

    attempts: list[dict[str, Any]] = []
    for pref in homes_prefs:
        attempts.append(
            _run_homes(pref, include_stations=include_stations, max_lines=max_lines_arg, force_refresh=force_refresh)
        )
    for pref in suumo_prefs:
        attempts.append(_run_suumo(pref, include_stations=include_stations, max_lines=max_lines_arg, include_empty=include_empty))

    # Persist cursors even if some prefectures fail (scheduled task should keep rotating).
    state["homes_cursor"] = int(homes_next)
    state["suumo_cursor"] = int(suumo_next)
    state["last_run_at"] = _now_iso()
    _save_state(state)

    ok = all(bool(a.get("ok")) for a in attempts) if attempts else True
    payload = {
        "ok": bool(ok),
        "homes_prefs": homes_prefs,
        "suumo_prefs": suumo_prefs,
        "state": {"homes_cursor": int(homes_next), "suumo_cursor": int(suumo_next), "path": str(STATE_PATH)},
        "attempts": attempts,
        "ran_at": state.get("last_run_at") or "",
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

