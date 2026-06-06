from __future__ import annotations

import logging
import time
import unicodedata
from dataclasses import dataclass

from src.db import get_conn
from src.homes_geo import HOMES_KODATE_CHUKO_PREFS
from src.jp_transit_model import ensure_jp_transit_schema_and_seed
from src.suumo_transit import SuumoTransitLine, suumo_chukoikkodate_ensen_lines, suumo_chukoikkodate_line_stations

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SuumoTransitSyncResult:
    pref_key: str
    city_area: str
    lines_seen: int
    lines_upserted: int
    stations_seen: int
    stations_upserted: int
    elapsed_sec: float


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def _pref_label(pref_key: str) -> str:
    key = str(pref_key or "").strip().lower()
    pref = next((p for p in HOMES_KODATE_CHUKO_PREFS if p.key == key), None)
    if not pref:
        raise ValueError("unknown pref")
    return str(pref.label or "").strip()


def _line_match_key(line_name: str) -> str:
    return _norm(line_name).replace(" ", "")


def _safe_line_id_from_rn(conn, rn: int, *, city_area: str, line_name: str) -> int:
    """
    Prefer SUUMO `rn` as our `jp_trans_line.line_id` when safe:
    - rn is within (1000..9999]
    - no existing conflicting line_id row
    """
    rid = int(rn or 0)
    if rid < 1000 or rid > 9999:
        return 0
    row = conn.execute("SELECT city_area, line_name FROM jp_trans_line WHERE line_id = ? LIMIT 1", (rid,)).fetchone()
    if not row:
        return rid
    if _norm(str(row["city_area"] or "")) == _norm(city_area) and _line_match_key(str(row["line_name"] or "")) == _line_match_key(line_name):
        return rid
    return 0


def _safe_station_id_from_code(conn, code: int, *, line_id: int, station_name: str) -> int:
    """
    Prefer SUUMO `ek_` station code as `jp_trans_station.station_id` when safe:
    - code is within (1..9_999_999]
    - no existing conflicting station_id row
    """
    sid = int(code or 0)
    if sid <= 0 or sid > 9_999_999:
        return 0
    row = conn.execute(
        "SELECT line_id, station_name FROM jp_trans_station WHERE station_id = ? LIMIT 1",
        (sid,),
    ).fetchone()
    if not row:
        return sid
    if int(row["line_id"] or 0) == int(line_id or 0) and _norm(str(row["station_name"] or "")) == _norm(station_name):
        return sid
    return 0


def sync_suumo_chukoikkodate_transit_pref(
    pref_key: str,
    *,
    include_stations: bool = True,
    max_lines: int | None = None,
    only_enabled_lines: bool = True,
) -> SuumoTransitSyncResult:
    """
    Scrape SUUMO chuko-ikkodate (中古一戸建て) transit lines/stations into `jp_trans_*`.

    Data source:
      - Lines:   https://suumo.jp/chukoikkodate/{pref}/ensen/
      - Stations: each line's `en_...` page (accordion "◯◯県にある駅から探す")
    """
    start = time.time()
    pref = str(pref_key or "").strip().lower()
    if not pref:
        raise ValueError("pref_key is required")
    city_area = _pref_label(pref)

    lines_payload = suumo_chukoikkodate_ensen_lines(pref)
    if only_enabled_lines:
        lines_payload = [ln for ln in lines_payload if int(ln.count or 0) > 0 and str(ln.url or "").strip()]
    if max_lines is not None:
        lines_payload = lines_payload[: max(0, int(max_lines))]

    lines_seen = len(lines_payload)
    lines_upserted = 0
    stations_seen = 0
    stations_upserted = 0

    with get_conn() as conn:
        ensure_jp_transit_schema_and_seed(conn)

        # Existing line-name -> line_id map (same prefecture).
        existing_rows = conn.execute(
            "SELECT line_id, line_name FROM jp_trans_line WHERE city_area = ?",
            (city_area,),
        ).fetchall()
        by_key: dict[str, int] = {}
        for r in existing_rows or []:
            lid = int(r["line_id"] or 0)
            k = _line_match_key(str(r["line_name"] or ""))
            if k and lid > 0:
                by_key.setdefault(k, lid)

        mx_row = conn.execute("SELECT MAX(line_id) AS mx FROM jp_trans_line").fetchone()
        next_line_id = int((mx_row["mx"] if mx_row and mx_row["mx"] is not None else 0) or 0) + 1

        def _ensure_line_row(ln: SuumoTransitLine) -> int:
            nonlocal next_line_id, lines_upserted
            name = str(ln.line_name or "").strip()
            trans_type = str(ln.trans_type or "").strip() or "その他"
            key = _line_match_key(name)
            if not key:
                return 0
            if key in by_key:
                lid = int(by_key[key] or 0)
                if lid > 0:
                    try:
                        conn.execute(
                            "UPDATE jp_trans_line SET trans_type = ?, line_name = ? WHERE line_id = ?",
                            (trans_type, name, lid),
                        )
                    except Exception:
                        pass
                    return lid

            # Prefer SUUMO rn when safe (IDs remain stable across sync runs).
            lid = _safe_line_id_from_rn(conn, int(ln.rn or 0), city_area=city_area, line_name=name)
            if not lid:
                lid = int(next_line_id)
                if lid <= 0 or lid > 9999:
                    raise RuntimeError("jp_trans_line id capacity exceeded (need <= 9999)")
                next_line_id += 1
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO jp_trans_line (line_id, city_area, trans_type, line_name, line_color, main_ward)
                    VALUES (?, ?, ?, ?, '', '')
                    """,
                    (int(lid), city_area, trans_type, name),
                )
                # Align on latest name/type in case of existing row.
                conn.execute(
                    "UPDATE jp_trans_line SET city_area = ?, trans_type = ?, line_name = ? WHERE line_id = ?",
                    (city_area, trans_type, name, int(lid)),
                )
            except Exception:
                pass
            by_key.setdefault(key, int(lid))
            lines_upserted += 1
            return int(lid)

        # Ensure line rows exist first.
        line_id_by_rn: dict[int, int] = {}
        for ln in lines_payload:
            lid = _ensure_line_row(ln)
            if lid > 0:
                line_id_by_rn[int(ln.rn or 0)] = lid

        if not include_stations:
            conn.commit()
            return SuumoTransitSyncResult(
                pref_key=pref,
                city_area=city_area,
                lines_seen=lines_seen,
                lines_upserted=lines_upserted,
                stations_seen=0,
                stations_upserted=0,
                elapsed_sec=time.time() - start,
            )

        # Stations: per line page.
        for ln in lines_payload:
            lid = line_id_by_rn.get(int(ln.rn or 0), 0)
            if lid <= 0:
                continue
            stations = suumo_chukoikkodate_line_stations(pref, line_url=str(ln.url or ""), rn=int(ln.rn or 0), line_name=str(ln.line_name or ""))
            stations_seen += len(stations)
            for st in stations:
                code = int(st.station_code or 0)
                name = str(st.station_name or "").strip()
                if code <= 0 or not name:
                    continue
                wanted_sid = _safe_station_id_from_code(conn, code, line_id=lid, station_name=name)
                if wanted_sid:
                    # Insert/update with stable SUUMO station ID.
                    try:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO jp_trans_station
                            (station_id, line_id, station_name, prefecture, city, ward, full_address)
                            VALUES (?, ?, ?, ?, ?, '', ?)
                            """,
                            (int(wanted_sid), int(lid), name, city_area, city_area, name),
                        )
                        conn.execute(
                            """
                            UPDATE jp_trans_station
                            SET line_id = ?, station_name = ?, prefecture = ?, city = ?, full_address = ?
                            WHERE station_id = ?
                            """,
                            (int(lid), name, city_area, city_area, name, int(wanted_sid)),
                        )
                        stations_upserted += 1
                    except Exception:
                        pass
                    continue

                # Fallback: allocate station_id in our local scheme.
                try:
                    from src.pipeline import _ensure_jp_transit_station_row
                except Exception:
                    _ensure_jp_transit_station_row = None  # type: ignore
                if _ensure_jp_transit_station_row is None:
                    continue
                sid2 = int(
                    _ensure_jp_transit_station_row(
                        conn,
                        line_id=int(lid),
                        station_name=name,
                        pref_hint=city_area,
                        addr_hint=name,
                    )
                    or 0
                )
                if sid2 > 0:
                    stations_upserted += 1

        conn.commit()

    return SuumoTransitSyncResult(
        pref_key=pref,
        city_area=city_area,
        lines_seen=lines_seen,
        lines_upserted=lines_upserted,
        stations_seen=stations_seen,
        stations_upserted=stations_upserted,
        elapsed_sec=time.time() - start,
    )

