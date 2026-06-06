from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.homes_transit_sync import sync_homes_kodate_chuko_transit_pref  # noqa: E402


def _safe(s: str) -> str:
    try:
        return str(s)
    except Exception:
        return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync HOMES /kodate/chuko/{pref}/line/ into jp_trans_* tables.")
    p.add_argument("--pref", action="append", default=[], help="Pref key (e.g., kanagawa, saitama). Repeatable.")
    p.add_argument("--max-lines", type=int, default=0, help="Limit enabled lines (0=all).")
    p.add_argument("--no-stations", action="store_true", help="Only sync line rows (skip station pages).")
    p.add_argument("--no-refresh", action="store_true", help="Do not force-refresh HOMES line page (use cache).")
    args = p.parse_args(argv)

    prefs = [str(x).strip().lower() for x in (args.pref or []) if str(x).strip()]
    if not prefs:
        prefs = ["kanagawa", "saitama"]

    max_lines = int(args.max_lines or 0)
    max_lines_arg = None if max_lines <= 0 else max_lines

    results = []
    for pref_key in prefs:
        res = sync_homes_kodate_chuko_transit_pref(
            pref_key,
            force_refresh_lines=not bool(args.no_refresh),
            include_stations=not bool(args.no_stations),
            max_lines=max_lines_arg,
        )
        results.append(res)
        # Print as ASCII-safe JSON (avoid UnicodeEncodeError in cp950 consoles).
        print(
            json.dumps(
                {
                    "pref": _safe(res.pref_key),
                    "city_area": _safe(res.city_area).encode("unicode_escape").decode(),
                    "lines_seen": res.lines_seen,
                    "lines_upserted": res.lines_upserted,
                    "stations_seen": res.stations_seen,
                    "stations_upserted": res.stations_upserted,
                    "elapsed_sec": round(float(res.elapsed_sec), 2),
                },
                ensure_ascii=True,
            )
        , flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
