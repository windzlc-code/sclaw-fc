import argparse
import sqlite3
import sys
import time

# Windows 主控台預設 cp950，印出「首都圏」等字會 UnicodeEncodeError；強制 UTF-8 輸出。
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
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.case_metadata import JP_AREA_FILTER_LABELS
from src.coverage_matrix_sql import (
    CASE_INV_FRESH_TS,
    CASE_INV_JP_LISTING_SQL,
    COVERAGE_HEAL_REGION_QUERY_ALIASES,
    coverage_host_where_sql,
    coverage_region_where_sql,
)
from src.config import DB_PATH
from src.crawler import crawl_one_source
from src.pipeline import process_crawled_items
from src.source_registry import ensure_seven_jp_portal_sources, get_enabled_sources

DB = DB_PATH
HOSTS = (
    "suumo.jp",
    "homes.co.jp",
    "athome.co.jp",
    "realestate.yahoo.co.jp",
    "realestate.rakuten.co.jp",
    "yes1.co.jp",
    "oheya-su.jp",
)
PRIMARY_FOUR = ("suumo.jp", "homes.co.jp", "athome.co.jp", "realestate.yahoo.co.jp")
REMAINING_THREE = ("realestate.rakuten.co.jp", "yes1.co.jp", "oheya-su.jp")


def _norm_host(raw_url: str) -> str:
    h = (urlparse(str(raw_url or "")).netloc or "").lower()
    h = h[4:] if h.startswith("www.") else h
    if h in {"oheyago.jp", "oheyasuu.com"}:
        return "oheya-su.jp"
    return h


def _region_queries(region: str) -> list[str]:
    out: list[str] = []
    r = str(region or "").strip()
    if r:
        out.append(r)
    for q in COVERAGE_HEAL_REGION_QUERY_ALIASES.get(r, ()):
        sq = str(q or "").strip()
        if sq and sq not in out:
            out.append(sq)
    return out[:6]


def matrix_cell(region: str, host: str, age_days: int) -> int:
    """單格：與 /api/cases/coverage-matrix 同口徑（jp_listing＋新鮮度＋host＋地區）。"""
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    fresh_sql = f"date({CASE_INV_FRESH_TS}) >= date('now', '-{age_days} days')"
    region_sql, region_params = coverage_region_where_sql(region)
    host_sql, host_params = coverage_host_where_sql(host)
    c = int(
        conn.execute(
            f"""
            SELECT COUNT(1) AS c
            FROM content_items c
            JOIN source_items s ON s.id = c.source_item_id
            WHERE {CASE_INV_JP_LISTING_SQL}
              AND {fresh_sql}
              AND {host_sql}
              AND {region_sql}
            """,
            [*host_params, *region_params],
        ).fetchone()["c"]
    )
    conn.close()
    return c


def matrix(age_days: int) -> dict[tuple[str, str], int]:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    fresh_sql = f"date({CASE_INV_FRESH_TS}) >= date('now', '-{age_days} days')"
    out: dict[tuple[str, str], int] = {}
    for region in JP_AREA_FILTER_LABELS:
        region_sql, region_params = coverage_region_where_sql(region)
        for host in HOSTS:
            host_sql, host_params = coverage_host_where_sql(host)
            c = int(
                conn.execute(
                    f"""
                    SELECT COUNT(1) AS c
                    FROM content_items c
                    JOIN source_items s ON s.id = c.source_item_id
                    WHERE {CASE_INV_JP_LISTING_SQL}
                      AND {fresh_sql}
                      AND {host_sql}
                      AND {region_sql}
                    """,
                    [*host_params, *region_params],
                ).fetchone()["c"]
            )
            out[(region, host)] = c
    conn.close()
    return out


def _primary_four_row_line(region: str, age_days: int) -> str:
    vals = [matrix_cell(region, hk, age_days) for hk in PRIMARY_FOUR]
    return "\t".join([region, *[str(v) for v in vals], str(sum(vals))])


def run_sequential_primary_four(
    *,
    age_days: int,
    min_count: int,
    per_source: int,
    host_source_take: int,
    passes_per_cell: int,
    sleep_sec: float,
    skip_regions: frozenset[str],
    hosts: tuple[str, ...],
    verify_capital_kanto: bool,
) -> None:
    """
    依 JP_AREA_FILTER_LABELS 順序，逐地區、逐站（預設前四站）補抓；與 coverage-matrix／heal 同關鍵字策略。
    預設略過首都圏、關東（已由使用者對照飽和），其餘地區依序緊急補齊。
    """
    hs = host_sources()
    print(
        f"sequential_primary_four: DB={DB} age_days={age_days} min_count={min_count} "
        f"passes_per_cell={passes_per_cell} skip={sorted(skip_regions)} hosts={hosts}",
        flush=True,
    )
    if verify_capital_kanto:
        print("【確認】前四站筆數（首都圏）", flush=True)
        print("\t".join(["地區", "SUUMO", "LIFULL HOME'S", "at home", "Yahoo!", "列計"]), flush=True)
        print(_primary_four_row_line("首都圏", age_days), flush=True)
        print("【確認】前四站筆數（關東）", flush=True)
        print("\t".join(["地區", "SUUMO", "LIFULL HOME'S", "at home", "Yahoo!", "列計"]), flush=True)
        print(_primary_four_row_line("關東", age_days), flush=True)

    regions_work = [r for r in JP_AREA_FILTER_LABELS if str(r).strip() not in skip_regions]
    print(f"【補齊順序】共 {len(regions_work)} 區（已略過 {sorted(skip_regions)}）", flush=True)

    for region in regions_work:
        print(f"\n======== 地區：{region} ========", flush=True)
        print("\t".join(["地區", "SUUMO", "LIFULL HOME'S", "at home", "Yahoo!", "列計"]), flush=True)
        before_line = _primary_four_row_line(region, age_days)
        print(f"補前\t{before_line}", flush=True)

        for host in hosts:
            if not hs.get(host):
                print(f"skip {region}/{host}（無啟用來源 URL）", flush=True)
                continue
            for _attempt in range(max(1, passes_per_cell)):
                cnt = matrix_cell(region, host, age_days)
                if cnt >= min_count:
                    break
                _run_phase(
                    phase_name=f"SEQ-{region}",
                    lows=[(region, host, cnt)],
                    hs=hs,
                    per_source=per_source,
                    max_cells=1,
                    host_source_take=host_source_take,
                )
                if sleep_sec > 0:
                    time.sleep(sleep_sec)

        after_line = _primary_four_row_line(region, age_days)
        print(f"補後\t{after_line}", flush=True)

    print("\n【全區前四站總覽】（略過區仍可在全表出現—此表僅列本次工作區）", flush=True)
    print("\t".join(["地區", "SUUMO", "LIFULL HOME'S", "at home", "Yahoo!", "列計"]), flush=True)
    for region in regions_work:
        print(_primary_four_row_line(region, age_days), flush=True)


def host_sources() -> dict[str, list[str]]:
    ensure_seven_jp_portal_sources()
    out: dict[str, list[str]] = {h: [] for h in HOSTS}
    for s in get_enabled_sources():
        u = str(s.get("url") or "").strip()
        h = _norm_host(u)
        if h in out and u not in out[h]:
            out[h].append(u)
    return out


def _select_low_cells(
    m: dict[tuple[str, str], int],
    *,
    hosts: tuple[str, ...],
    min_count: int,
    zero_only: bool,
) -> list[tuple[str, str, int]]:
    lows = []
    for (region, host), c in m.items():
        if host not in hosts:
            continue
        if zero_only and c == 0:
            lows.append((region, host, c))
        elif not zero_only and c < min_count:
            lows.append((region, host, c))
    lows.sort(key=lambda x: x[2])
    return lows


def _crawl_with_queries(source_url: str, per_source: int, qlist: list[str]) -> int:
    got_total = 0
    for q in qlist:
        # 兩次嘗試，避免單次 503 直接放棄
        for attempt in range(2):
            try:
                got = crawl_one_source(source_url, per_source_limit=per_source, search_query=q)
                if got:
                    got_total += len(got)
                    process_crawled_items(got)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    print(f"warn crawl failed source={source_url} q={q} err={str(e)[:120]}", flush=True)
                else:
                    time.sleep(1.2)
        if got_total >= per_source:
            break
    return got_total


def _run_phase(
    *,
    phase_name: str,
    lows: list[tuple[str, str, int]],
    hs: dict[str, list[str]],
    per_source: int,
    max_cells: int,
    host_source_take: int,
) -> int:
    did = 0
    for region, host, cnt in lows[:max_cells]:
        srcs = hs.get(host, [])[: max(1, host_source_take)]
        if not srcs:
            print(f"skip {phase_name} {region}/{host} no-source", flush=True)
            continue
        qlist = _region_queries(region)
        got_total = 0
        for u in srcs:
            got_total += _crawl_with_queries(u, per_source, qlist)
            if got_total >= per_source:
                break
        did += 1
        print(f"{phase_name} fill {region}/{host} from {cnt} got={got_total}", flush=True)
    return did


def run(
    rounds: int,
    min_count: int,
    per_source: int,
    max_cells: int,
    age_days: int,
    *,
    sleep_sec: float,
    host_source_take: int,
    continuous: bool,
) -> None:
    print(f"fill_matrix_min10: DB={DB} rounds={rounds} min_count={min_count} max_cells={max_cells}", flush=True)
    hs = host_sources()
    rnd = 0
    while True:
        rnd += 1
        if not continuous and rnd > rounds:
            break
        m = matrix(age_days)
        zero_all = [(reg, host, c) for (reg, host), c in m.items() if c == 0]
        low_all = [(reg, host, c) for (reg, host), c in m.items() if c < min_count]
        print(f"[round {rnd}] zero_cells={len(zero_all)} low_cells={len(low_all)}", flush=True)
        if not low_all:
            print("all cells reached target", flush=True)
            return

        did = 0
        # Phase 1: 前四站先清零格
        p1 = _select_low_cells(m, hosts=PRIMARY_FOUR, min_count=min_count, zero_only=True)
        did += _run_phase(
            phase_name="P1-primary-zero",
            lows=p1,
            hs=hs,
            per_source=per_source,
            max_cells=max_cells,
            host_source_take=host_source_take,
        )
        m = matrix(age_days)
        # Phase 2: 後三站清零格
        p2 = _select_low_cells(m, hosts=REMAINING_THREE, min_count=min_count, zero_only=True)
        did += _run_phase(
            phase_name="P2-remaining-zero",
            lows=p2,
            hs=hs,
            per_source=per_source,
            max_cells=max_cells,
            host_source_take=host_source_take,
        )
        m = matrix(age_days)
        # Phase 3: 前四站拉升到 min_count
        p3 = _select_low_cells(m, hosts=PRIMARY_FOUR, min_count=min_count, zero_only=False)
        did += _run_phase(
            phase_name="P3-primary-min",
            lows=p3,
            hs=hs,
            per_source=per_source,
            max_cells=max_cells,
            host_source_take=host_source_take,
        )
        m = matrix(age_days)
        # Phase 4: 後三站拉升到 min_count
        p4 = _select_low_cells(m, hosts=REMAINING_THREE, min_count=min_count, zero_only=False)
        did += _run_phase(
            phase_name="P4-remaining-min",
            lows=p4,
            hs=hs,
            per_source=per_source,
            max_cells=max_cells,
            host_source_take=host_source_take,
        )

        if did == 0:
            print("no progress in this round", flush=True)
            return
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    mf = matrix(age_days)
    remain_zero = sum(1 for v in mf.values() if v == 0)
    remain_low = sum(1 for v in mf.values() if v < min_count)
    completed_rounds = max(0, rnd - 1) if not continuous else rnd
    print(
        f"done completed_rounds={completed_rounds}, remaining_zero_cells={remain_zero}, remaining_low_cells={remain_low}",
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="矩陣補洞：預設全矩陣多輪；加 --sequential-primary-four 則依地區順序只補前四站。",
    )
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--min-count", type=int, default=10)
    p.add_argument("--per-source", type=int, default=180)
    p.add_argument("--max-cells", type=int, default=60)
    p.add_argument("--age-days", type=int, default=180)
    p.add_argument("--sleep-sec", type=float, default=1.0)
    p.add_argument("--host-source-take", type=int, default=2)
    p.add_argument("--continuous", action="store_true")
    p.add_argument(
        "--sequential-primary-four",
        action="store_true",
        help="依 JP_AREA_FILTER_LABELS 順序逐區、逐站補 SUUMO／HOMES／at home／Yahoo（與 coverage-matrix 同口徑）。",
    )
    p.add_argument(
        "--skip-regions",
        default="首都圏,關東",
        help="逗號分隔；預設略過首都圏、關東，其餘地區依序補。傳空字串則不略過。",
    )
    p.add_argument(
        "--no-verify-capital-kanto",
        action="store_true",
        help="與 --sequential-primary-four 併用：不先印首都圏／關東四站確認列。",
    )
    p.add_argument(
        "--passes-per-cell",
        type=int,
        default=2,
        help="與 --sequential-primary-four 併用：每格未達 min-count 時最多再爬幾輪。",
    )
    p.add_argument(
        "--hosts",
        default="",
        help="與 --sequential-primary-four 併用：逗號分隔 host，留空=四站全跑。例：homes.co.jp,athome.co.jp",
    )
    a = p.parse_args()
    if a.sequential_primary_four:
        raw_skip = str(a.skip_regions or "").strip()
        skip_set = frozenset(x.strip() for x in raw_skip.split(",") if x.strip()) if raw_skip else frozenset()
        host_tuple = PRIMARY_FOUR
        hs_raw = str(a.hosts or "").strip().lower()
        if hs_raw:
            wanted = {x.strip().lower() for x in hs_raw.split(",") if x.strip()}
            host_tuple = tuple(h for h in PRIMARY_FOUR if h in wanted) or PRIMARY_FOUR
        run_sequential_primary_four(
            age_days=a.age_days,
            min_count=max(1, int(a.min_count)),
            per_source=max(5, int(a.per_source)),
            host_source_take=max(1, int(a.host_source_take)),
            passes_per_cell=max(1, int(a.passes_per_cell)),
            sleep_sec=float(a.sleep_sec or 0),
            skip_regions=skip_set,
            hosts=host_tuple,
            verify_capital_kanto=not bool(a.no_verify_capital_kanto),
        )
    else:
        run(
            a.rounds,
            a.min_count,
            a.per_source,
            a.max_cells,
            a.age_days,
            sleep_sec=a.sleep_sec,
            host_source_take=a.host_source_take,
            continuous=a.continuous,
        )
