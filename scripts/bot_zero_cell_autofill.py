import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fill_matrix_min10 import (  # noqa: E402
    HOSTS,
    PRIMARY_FOUR,
    REMAINING_THREE,
    _crawl_with_queries,
    _region_queries,
    host_sources,
    matrix,
    matrix_cell,
)
from src.case_metadata import JP_AREA_FILTER_LABELS  # noqa: E402

HOST_LABELS = {
    "suumo.jp": "SUUMO",
    "homes.co.jp": "LIFULL HOME'S",
    "athome.co.jp": "at home",
    "realestate.yahoo.co.jp": "Yahoo!",
    "realestate.rakuten.co.jp": "Rakuten",
    "yes1.co.jp": "Yes!station",
    "oheya-su.jp": "OHEYASU",
}


def _set_fast_defaults(*, enable_playwright: bool) -> None:
    if not enable_playwright:
        os.environ.setdefault("SCLAW_PLAYWRIGHT", "0")
    os.environ.setdefault("SCLAW_PORTAL_HUB_CAP", "8")
    os.environ.setdefault("SCLAW_ATHOME_HUB_CAP", "8")
    os.environ.setdefault("SCLAW_REMAINING_PORTAL_HUB_CAP", "4")
    os.environ.setdefault("SCLAW_ATHOME_CATALOG_MAX_PAGES", "3")
    os.environ.setdefault("SCLAW_HOMES_CATALOG_MAX_PAGES", "3")


def _summary(m: dict[tuple[str, str], int], *, min_count: int, threshold: int) -> dict[str, Any]:
    values = [int(v) for v in m.values()]
    by_host = {h: int(sum(int(m.get((r, h), 0)) for r in JP_AREA_FILTER_LABELS)) for h in HOSTS}
    return {
        "total": int(sum(values)),
        "zero_cells": int(sum(1 for v in values if v == 0)),
        "low_min_cells": int(sum(1 for v in values if v < min_count)),
        "low_threshold_cells": int(sum(1 for v in values if v < threshold)),
        "by_host": by_host,
    }


def _cell_key(region: str, host: str) -> str:
    return f"{region}|{host}"


def _load_state() -> dict[str, Any]:
    state_path = ROOT / "data" / "bot_zero_cell_autofill_state.json"
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    last_path = ROOT / "data" / "bot_zero_cell_autofill_last.json"
    state: dict[str, Any] = {"cells": {}}
    if last_path.is_file():
        try:
            last = json.loads(last_path.read_text(encoding="utf-8"))
            for item in list(last.get("attempts") or []):
                region = str(item.get("region") or "").strip()
                host = str(item.get("host") or "").strip()
                if not region or not host:
                    continue
                delta = int(item.get("delta") or 0)
                status = str(item.get("status") or "").strip()
                key = _cell_key(region, host)
                state["cells"][key] = {
                    "region": region,
                    "host": host,
                    "fail_count": 0 if delta > 0 else 1,
                    "success_count": 1 if delta > 0 else 0,
                    "last_status": status,
                    "last_delta": delta,
                    "last_attempt_at": last.get("run_label") or "",
                }
        except Exception:
            pass
    return state


def _save_state(state: dict[str, Any]) -> None:
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bot_zero_cell_autofill_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _cell_fail_count(state: dict[str, Any], region: str, host: str) -> int:
    cells = state.get("cells") if isinstance(state.get("cells"), dict) else {}
    item = cells.get(_cell_key(region, host)) if isinstance(cells, dict) else None
    if not isinstance(item, dict):
        return 0
    try:
        return max(0, int(item.get("fail_count") or 0))
    except Exception:
        return 0


def _update_state_from_attempts(state: dict[str, Any], attempts: list[dict[str, Any]], *, run_label: str) -> dict[str, Any]:
    cells = state.get("cells") if isinstance(state.get("cells"), dict) else {}
    if not isinstance(cells, dict):
        cells = {}
    for item in attempts:
        region = str(item.get("region") or "").strip()
        host = str(item.get("host") or "").strip()
        if not region or not host:
            continue
        key = _cell_key(region, host)
        prev = cells.get(key) if isinstance(cells.get(key), dict) else {}
        delta = int(item.get("delta") or 0)
        fail_count = 0 if delta > 0 else max(0, int(prev.get("fail_count") or 0)) + 1
        success_count = max(0, int(prev.get("success_count") or 0)) + (1 if delta > 0 else 0)
        cells[key] = {
            "region": region,
            "host": host,
            "fail_count": int(fail_count),
            "success_count": int(success_count),
            "last_status": str(item.get("status") or ""),
            "last_delta": delta,
            "last_crawled": int(item.get("crawled") or 0),
            "last_attempt_at": run_label,
        }
    state["cells"] = cells
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return state


def _region_index(region: str) -> int:
    try:
        return list(JP_AREA_FILTER_LABELS).index(region)
    except ValueError:
        return 999


def _host_index(host: str) -> int:
    try:
        return list(HOSTS).index(host)
    except ValueError:
        return 999


def _target_cells(
    m: dict[tuple[str, str], int],
    *,
    min_count: int,
    max_cells: int,
    scope: str,
    state: dict[str, Any],
    max_fail_per_cell: int,
) -> tuple[str, list[tuple[str, str, int]]]:
    if scope == "primary":
        allowed = set(PRIMARY_FOUR)
    elif scope == "remaining":
        allowed = set(REMAINING_THREE)
    else:
        allowed = set(HOSTS)

    skipped: list[tuple[str, str, int]] = []

    def allowed_by_state(region: str, host: str) -> bool:
        fails = _cell_fail_count(state, region, host)
        if max_fail_per_cell < 0:
            return True
        return fails < max_fail_per_cell

    zero_all = [
        (region, host, int(count))
        for (region, host), count in m.items()
        if host in allowed and int(count) == 0
    ]
    zero = []
    for item in zero_all:
        if allowed_by_state(item[0], item[1]):
            zero.append(item)
        else:
            skipped.append(item)
    zero.sort(key=lambda x: (_region_index(x[0]), _host_index(x[1])))
    if zero:
        return "zero", zero[: max(1, max_cells)]

    low_all = [
        (region, host, int(count))
        for (region, host), count in m.items()
        if host in allowed and int(count) < min_count
    ]
    low = []
    for item in low_all:
        if allowed_by_state(item[0], item[1]):
            low.append(item)
        else:
            skipped.append(item)
    low.sort(key=lambda x: (int(x[2]), _region_index(x[0]), _host_index(x[1])))
    if low:
        return "low", low[: max(1, max_cells)]
    if skipped:
        skipped.sort(key=lambda x: (_cell_fail_count(state, x[0], x[1]), _region_index(x[0]), _host_index(x[1])))
        return "deferred_retry", skipped[: max(1, max_cells)]
    return "none", []


def _run_cell(
    *,
    region: str,
    host: str,
    before_count: int,
    per_source: int,
    age_days: int,
    host_source_take: int,
) -> dict[str, Any]:
    sources = host_sources().get(host, [])[: max(1, host_source_take)]
    qlist = _region_queries(region)
    if not sources:
        return {
            "region": region,
            "host": host,
            "portal": HOST_LABELS.get(host, host),
            "before": int(before_count),
            "after": int(before_count),
            "delta": 0,
            "crawled": 0,
            "status": "no_source",
            "queries": qlist,
            "sources": [],
        }

    got_total = 0
    err = ""
    for url in sources:
        try:
            got_total += int(_crawl_with_queries(url, per_source, qlist) or 0)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {str(exc)[:180]}"
        if got_total >= per_source:
            break
    after_count = int(matrix_cell(region, host, age_days))
    delta = after_count - int(before_count)
    if delta > 0:
        status = "improved"
    elif got_total > 0:
        status = "crawled_not_counted"
    elif err:
        status = "error"
    else:
        status = "no_candidates"
    return {
        "region": region,
        "host": host,
        "portal": HOST_LABELS.get(host, host),
        "before": int(before_count),
        "after": int(after_count),
        "delta": int(delta),
        "crawled": int(got_total),
        "status": status,
        "queries": qlist,
        "sources": sources,
        "error": err,
    }


def _save_report(report: dict[str, Any]) -> None:
    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (out_dir / "bot_zero_cell_autofill_last.json").write_text(payload, encoding="utf-8")
    (out_dir / f"bot_zero_cell_autofill_{stamp}.json").write_text(payload, encoding="utf-8")


def _save_bot_eval(report: dict[str, Any], *, age_days: int, threshold: int, run_label: str) -> None:
    try:
        from app import _build_data_completion_bot_eval, _save_data_completion_bot_eval

        attempts = list(report.get("attempts") or [])
        heal_result = {
            "processed": int(report.get("improved_delta") or 0),
            "crawled_links": int(report.get("crawled") or 0),
            "target_cells": [
                {"region": a.get("region"), "host_key": a.get("host"), "count": a.get("before")}
                for a in attempts
            ],
            "healed_cells": attempts,
            "zero_cell_autofill": {
                "mode": report.get("mode"),
                "before_summary": report.get("before"),
                "after_summary": report.get("after"),
                "improved_cells": report.get("improved_cells"),
            },
        }
        bot_eval = _build_data_completion_bot_eval(
            age_days=age_days,
            threshold_per_cell=threshold,
            source_item_id=0,
            media_fix={"fixed": False, "checked": 0},
            text_fix={"fixed": False, "checked": 0},
            heal_result=heal_result,
        )
        bot_eval["zero_cell_autofill"] = report
        _save_data_completion_bot_eval(run_label=run_label, source_item_id=0, bot_eval=bot_eval)
    except Exception as exc:  # noqa: BLE001
        report["bot_eval_error"] = f"{type(exc).__name__}: {str(exc)[:220]}"


def run(args: argparse.Namespace) -> dict[str, Any]:
    _set_fast_defaults(enable_playwright=bool(args.enable_playwright))
    run_label = str(args.run_label or "zero_cell_autofill").strip()[:120]
    started = time.time()
    state = _load_state()
    only_region = str(getattr(args, "only_region", "") or "").strip()
    only_host = str(getattr(args, "only_host", "") or "").strip().lower()
    if only_region or only_host:
        allowed = set(HOSTS)
        if args.scope == "primary":
            allowed = set(PRIMARY_FOUR)
        elif args.scope == "remaining":
            allowed = set(REMAINING_THREE)
        target_regions = [only_region] if only_region else list(JP_AREA_FILTER_LABELS)
        if only_host:
            target_hosts = [only_host] if only_host in allowed else []
        else:
            target_hosts = [h for h in HOSTS if h in allowed]
        m_before = {
            (region, host): int(matrix_cell(region, host, args.age_days))
            for region in target_regions
            for host in target_hosts
        }
        before_summary = _summary(m_before, min_count=args.min_count, threshold=args.threshold)
        targets = [
            (region, host, int(count))
            for (region, host), count in m_before.items()
        ]
        targets.sort(key=lambda x: (_region_index(x[0]), _host_index(x[1])))
        targets = targets[: max(1, int(args.max_cells))]
        mode = "manual"
    else:
        m_before = matrix(args.age_days)
        before_summary = _summary(m_before, min_count=args.min_count, threshold=args.threshold)
        mode, targets = _target_cells(
            m_before,
            min_count=args.min_count,
            max_cells=args.max_cells,
            scope=args.scope,
            state=state,
            max_fail_per_cell=args.max_fail_per_cell,
        )
    print(
        f"bot_zero_cell_autofill start label={run_label} mode={mode} "
        f"zero={before_summary['zero_cells']} low={before_summary['low_min_cells']} "
        f"targets={len(targets)}",
        flush=True,
    )

    attempts: list[dict[str, Any]] = []
    for idx, (region, host, before_count) in enumerate(targets, start=1):
        print(f"[{idx}/{len(targets)}] {region}/{HOST_LABELS.get(host, host)} before={before_count}", flush=True)
        item = _run_cell(
            region=region,
            host=host,
            before_count=before_count,
            per_source=args.per_source,
            age_days=args.age_days,
            host_source_take=args.host_source_take,
        )
        attempts.append(item)
        print(
            f"  status={item['status']} crawled={item['crawled']} "
            f"after={item['after']} delta={item['delta']}",
            flush=True,
        )
        if args.sleep_sec > 0:
            time.sleep(float(args.sleep_sec))

    if mode == "manual":
        m_after = {
            (str(item.get("region") or ""), str(item.get("host") or "")): int(
                matrix_cell(str(item.get("region") or ""), str(item.get("host") or ""), args.age_days)
            )
            for item in attempts
            if str(item.get("region") or "").strip() and str(item.get("host") or "").strip()
        }
    else:
        m_after = matrix(args.age_days)
    after_summary = _summary(m_after, min_count=args.min_count, threshold=args.threshold)
    improved = [x for x in attempts if int(x.get("delta") or 0) > 0]
    report = {
        "ok": True,
        "run_label": run_label,
        "age_days": int(args.age_days),
        "min_count": int(args.min_count),
        "threshold": int(args.threshold),
        "mode": mode,
        "scope": str(args.scope),
        "elapsed_sec": round(time.time() - started, 1),
        "before": before_summary,
        "after": after_summary,
        "attempted_cells": len(attempts),
        "improved_cells": len(improved),
        "improved_delta": int(sum(max(0, int(x.get("delta") or 0)) for x in attempts)),
        "crawled": int(sum(int(x.get("crawled") or 0) for x in attempts)),
        "max_fail_per_cell": int(args.max_fail_per_cell),
        "attempts": attempts,
    }
    state = _update_state_from_attempts(state, attempts, run_label=run_label)
    report["state"] = {
        "tracked_cells": len((state.get("cells") if isinstance(state.get("cells"), dict) else {}) or {}),
        "updated_at": state.get("updated_at"),
    }
    _save_state(state)
    _save_bot_eval(report, age_days=args.age_days, threshold=args.threshold, run_label=run_label)
    _save_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="BOT zero-cell coverage autofill for SCLAW seven-portal matrix.")
    parser.add_argument("--age-days", type=int, default=180)
    parser.add_argument("--min-count", type=int, default=10)
    parser.add_argument("--threshold", type=int, default=15)
    parser.add_argument("--per-source", type=int, default=12)
    parser.add_argument("--max-cells", type=int, default=8)
    parser.add_argument("--host-source-take", type=int, default=2)
    parser.add_argument("--sleep-sec", type=float, default=0.25)
    parser.add_argument("--scope", choices=("all", "primary", "remaining"), default="all")
    parser.add_argument("--run-label", default="zero_cell_autofill")
    parser.add_argument("--max-fail-per-cell", type=int, default=1)
    parser.add_argument("--only-region", default="", help="Manually retry only this JP_AREA_FILTER_LABELS region.")
    parser.add_argument("--only-host", default="", help="Manually retry only this host key, e.g. realestate.yahoo.co.jp.")
    parser.add_argument("--enable-playwright", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
