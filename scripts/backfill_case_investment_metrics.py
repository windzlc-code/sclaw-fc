from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402
from src.db import get_conn, init_db  # noqa: E402


def _price_man(fields: dict, row: dict) -> float | None:
    return app._case_price_man_from_text(fields.get("price_text_hant")) or app._case_price_man_from_text(
        "\n".join(str(row.get(k) or "") for k in ("title_original", "body_original", "body_zh_hant", "body_zh_hans"))
    )


def _target_blob(row: dict, fields: dict) -> str:
    return "\n".join(
        str(x or "")
        for x in (
            fields.get("address_line_original_jp"),
            fields.get("address_line_jp"),
            fields.get("address_line_hant"),
            fields.get("access_line_original_jp"),
            fields.get("layout_text_hant"),
            fields.get("area_text_hant"),
            row.get("title_original"),
            row.get("body_original"),
            row.get("body_zh_hant"),
        )
    )


def _norm_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _project_key(row: dict, fields: dict) -> str:
    candidates = [
        fields.get("title_hant"),
        fields.get("title_zh_hant"),
        row.get("title_original"),
        row.get("title_zh_hant"),
    ]
    for raw in candidates:
        s = str(raw or "").strip()
        if not s:
            continue
        s = re.sub(r"^【[^】]+】", "", s)
        s = re.split(r"[｜|]", s, maxsplit=1)[0]
        s = re.sub(r"(?:新築|中古)?(?:マンション|一戸建て|戸建て|土地|公寓|大樓|住宅).*", "", s).strip()
        s = re.sub(r"^(?:在家|ホームズ|suumo|スーモ|athome|アットホーム)[:：\s]*", "", s, flags=re.I).strip()
        if len(_norm_text(s)) >= 5:
            return s
    return ""


def _same_case_public_rows(conn, row: dict, fields: dict, *, limit: int = 8) -> list[dict]:
    key = _project_key(row, fields)
    if len(_norm_text(key)) < 5:
        return []
    like = f"%{key}%"
    current_id = int(row.get("id") or row.get("source_item_id") or 0)
    rows = conn.execute(
        """
        SELECT
            s.*,
            c.title_zh_hant,
            c.title_zh_hans,
            c.body_zh_hant,
            c.body_zh_hans,
            c.seo_title,
            c.seo_description,
            c.case_transaction_override,
            c.case_jp_region_override,
            c.case_transit_override,
            c.listing_media_json
        FROM source_items s
        LEFT JOIN content_items c ON c.source_item_id = s.id
        WHERE COALESCE(s.content_kind, '') = 'jp_listing'
          AND s.id != ?
          AND (
            s.title_original LIKE ?
            OR s.body_original LIKE ?
            OR c.title_zh_hant LIKE ?
            OR c.body_zh_hant LIKE ?
          )
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (current_id, like, like, like, like, max(1, int(limit or 8))),
    ).fetchall()
    target_blob = _norm_text(_target_blob(row, fields))
    target_tokens = set(app._case_location_tokens_for_rent_estimate(_target_blob(row, fields)))
    out: list[dict] = []
    key_norm = _norm_text(key)
    for raw in rows:
        cand = dict(raw)
        cand_title_blob = _norm_text(
            "\n".join(
                str(cand.get(k) or "")
                for k in ("title_original", "title_zh_hant", "title_zh_hans", "seo_title")
            )
        )
        if key_norm not in cand_title_blob:
            continue
        try:
            meta = app.infer_case_metadata(cand)
            cand_fields = app._extract_listing_fields(cand, meta=meta)
        except Exception:
            cand_fields = {}
        cand_blob_raw = _target_blob(cand, cand_fields)
        cand_blob = _norm_text(cand_blob_raw)
        if key_norm not in cand_blob:
            continue
        cand_tokens = set(app._case_location_tokens_for_rent_estimate(cand_blob_raw))
        shared_tokens = {t for t in target_tokens & cand_tokens if not re.search(r"(?:都|道|府|県)$", t)}
        if target_tokens and cand_tokens and not shared_tokens and target_blob:
            continue
        cand["_investment_fields"] = cand_fields
        out.append(cand)
    return out


def _live_source_snapshot(row: dict) -> dict | None:
    url = str(row.get("item_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    try:
        with app.httpx.Client(timeout=app.httpx.Timeout(9.0, connect=3.0), follow_redirects=True, headers=app.BROWSER_HEADERS) as client:
            resp = client.get(url)
        if resp.status_code >= 400 or not resp.text:
            return None
        text = app.soup_from_html(resp.text).get_text(" ", strip=True)
    except Exception:
        return None
    if len(text) < 80:
        return None
    snap = dict(row)
    snap["body_original"] = f"{text}\n\n{row.get('body_original') or ''}"
    return snap


def _public_metrics_from_same_case(
    conn,
    row: dict,
    fields: dict,
    *,
    live_source: bool = False,
) -> tuple[dict | None, list[dict]]:
    candidates = _same_case_public_rows(conn, row, fields)
    checked: list[dict] = []
    search_rows = [row] + candidates
    if live_source:
        live_rows = []
        for cand in search_rows:
            snap = _live_source_snapshot(cand)
            if snap:
                live_rows.append(snap)
        search_rows = live_rows + search_rows
    seen_ids: set[tuple[int, str]] = set()
    for cand in search_rows:
        sid = int(cand.get("id") or cand.get("source_item_id") or 0)
        url = str(cand.get("item_url") or "")
        key = (sid, url)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        cand_fields = cand.get("_investment_fields")
        if not isinstance(cand_fields, dict):
            try:
                meta = app.infer_case_metadata(cand)
                cand_fields = app._extract_listing_fields(cand, meta=meta)
            except Exception:
                cand_fields = {}
        checked.append({
            "source_item_id": sid,
            "source_name": str(cand.get("source_name") or ""),
            "item_url": url,
        })
        metrics = app._case_investment_metrics(
            cand,
            cand_fields,
            use_cache=False,
            allow_live_price=False,
            allow_estimates=False,
        )
        if bool(metrics.get("has_income_data")) and not bool(metrics.get("estimated")):
            metrics["source_url"] = url
            metrics["note"] = "同案公開來源取得租金或收益資料。"
            metrics["comparison_sources"] = checked
            return metrics, checked
    return None, checked


def _build_rent_sample_model(conn) -> dict:
    rows = conn.execute(
        """
        SELECT
            s.*,
            c.title_zh_hant,
            c.title_zh_hans,
            c.body_zh_hant,
            c.body_zh_hans,
            c.seo_title,
            c.seo_description,
            c.case_transaction_override,
            c.case_jp_region_override,
            c.case_transit_override,
            c.listing_media_json
        FROM source_items s
        LEFT JOIN content_items c ON c.source_item_id = s.id
        WHERE
            s.item_url LIKE '%/chintai/%'
            OR s.title_original LIKE '%賃貸%'
            OR s.body_original LIKE '%賃料%'
            OR s.body_original LIKE '%家賃%'
            OR c.body_zh_hant LIKE '%租金%'
        """
    ).fetchall()
    by_city_layout: dict[tuple[str, str], list[dict]] = {}
    by_pref_layout: dict[tuple[str, str], list[dict]] = {}
    by_pref: dict[str, list[dict]] = {}
    for raw in rows:
        row = dict(raw)
        try:
            meta = app.infer_case_metadata(row)
            fields = app._extract_listing_fields(row, meta=meta)
        except Exception:
            fields = {}
        blob = _target_blob(row, fields)
        monthly = app._case_monthly_rent_man_from_text(blob)
        if monthly is None:
            continue
        area = app._case_extract_area_sqm(fields.get("area_text_hant")) or app._case_extract_area_sqm(blob)
        layout = app._case_layout_market_key(str(fields.get("layout_text_hant") or blob))
        tokens = app._case_location_tokens_for_rent_estimate(blob)
        if not tokens:
            continue
        domain = urlparse(str(row.get("item_url") or "")).netloc.lower()
        source_name = str(row.get("source_name") or "").strip()
        source_key = domain or source_name or f"source-{int(row.get('id') or 0)}"
        source_label = source_name or domain or "站內樣本"
        sample = {
            "monthly": float(monthly),
            "area": area,
            "layout": layout,
            "tokens": tokens,
            "source_item_id": int(row.get("id") or 0),
            "source_key": source_key,
            "source_label": source_label,
        }
        pref = next((t for t in tokens if t.endswith(("都", "道", "府", "県"))), tokens[0])
        city = next((t for t in tokens if t.endswith(("市", "区", "町", "村")) and not t.endswith(("都", "道", "府", "県"))), "")
        if layout and city:
            by_city_layout.setdefault((city, layout), []).append(sample)
        if layout and pref:
            by_pref_layout.setdefault((pref, layout), []).append(sample)
        if pref:
            by_pref.setdefault(pref, []).append(sample)
    return {"city_layout": by_city_layout, "pref_layout": by_pref_layout, "pref": by_pref}


def _median(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v and 0 < float(v) < 500]
    return float(statistics.median(vals)) if vals else None


def _mean(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v and 0 < float(v) < 500]
    return float(statistics.fmean(vals)) if vals else None


def _market_monthly_from_samples(samples: list[dict], area: float | None) -> tuple[float | None, dict]:
    by_source: dict[str, list[float]] = {}
    labels: dict[str, str] = {}
    for sample in samples:
        source_key = str(sample.get("source_key") or sample.get("source_item_id") or "sample")
        labels[source_key] = str(sample.get("source_label") or source_key)
        val = None
        if area and sample.get("area"):
            val = float(sample["monthly"]) / float(sample["area"]) * float(area)
        elif sample.get("monthly"):
            val = float(sample["monthly"])
        if val and 1 <= val <= 300:
            by_source.setdefault(source_key, []).append(val)
    source_medians = {key: _median(vals) for key, vals in by_source.items()}
    source_medians = {key: val for key, val in source_medians.items() if val}
    if not source_medians:
        return None, {"source_count": 0, "sample_count": len(samples), "source_labels": []}
    values = list(source_medians.values())
    monthly = _mean(values) if len(values) >= 2 else values[0]
    source_labels = [labels.get(key, key) for key in source_medians.keys()]
    return monthly, {
        "source_count": len(source_medians),
        "sample_count": len(samples),
        "source_labels": source_labels,
    }


def _estimate_from_rent_model(
    row: dict,
    fields: dict,
    model: dict,
    *,
    comparison_sources: list[dict] | None = None,
) -> dict | None:
    price_man = _price_man(fields, row)
    blob = _target_blob(row, fields)
    area = app._case_extract_area_sqm(fields.get("area_text_hant")) or app._case_extract_area_sqm(blob)
    layout = app._case_layout_market_key(str(fields.get("layout_text_hant") or blob))
    tokens = app._case_location_tokens_for_rent_estimate(blob)
    if not price_man or not tokens:
        return None
    pref = next((t for t in tokens if t.endswith(("都", "道", "府", "県"))), tokens[0])
    city = next((t for t in tokens if t.endswith(("市", "区", "町", "村")) and not t.endswith(("都", "道", "府", "県"))), "")
    groups: list[tuple[str, list[dict]]] = []
    if city and layout:
        groups.append((f"{city}/{layout}", model["city_layout"].get((city, layout), [])))
    if pref and layout:
        groups.append((f"{pref}/{layout}", model["pref_layout"].get((pref, layout), [])))
    if pref:
        groups.append((pref, model["pref"].get(pref, [])))
    chosen_label = ""
    chosen: list[dict] = []
    for label, samples in groups:
        if len(samples) >= 3:
            chosen_label = label
            chosen = samples
            break
    if not chosen:
        return None
    monthly, source_info = _market_monthly_from_samples(chosen, area)
    if not monthly or not (1 <= monthly <= 300):
        return None
    annual = monthly * 12
    yield_pct = annual / float(price_man) * 100
    if not (0.3 <= yield_pct <= 25):
        return None
    rows = [
        {"label": "總價（含稅）", "value": str(fields.get("price_text_hant") or f"約 {app._fmt_number_zh(price_man, max_decimals=0)} 萬日圓")},
        {"label": "收益率", "value": f"約 {yield_pct:.2f} %"},
        {"label": "年收入", "value": f"約 {app._fmt_number_zh(annual, max_decimals=1)} 萬日圓"},
    ]
    source_labels = ", ".join(str(x) for x in source_info.get("source_labels") or [] if x)
    checked = comparison_sources or []
    checked_label = ""
    if checked:
        names = []
        for src in checked[:4]:
            name = str(src.get("source_name") or urlparse(str(src.get("item_url") or "")).netloc or "公開來源").strip()
            if name and name not in names:
                names.append(name)
        if names:
            checked_label = f"同案公開來源已檢查：{'、'.join(names)}；"
    note = (
        f"{checked_label}未取得同案公開租金或利回時，按可比租賃樣本估算"
        f"（{chosen_label}，{int(source_info.get('sample_count') or 0)}筆，"
        f"{int(source_info.get('source_count') or 0)}個來源"
        f"{'：' + source_labels if source_labels else ''}）。"
    )
    return {
        "rows": rows,
        "has_income_data": True,
        "estimated": True,
        "source_url": "",
        "data_quality": "db_rent_sample_estimate",
        "note": note,
        "comparison_sources": checked,
    }


def _source_rows(conn, *, only_missing: bool, limit: int) -> list[dict]:
    where = "COALESCE(s.content_kind, '') = 'jp_listing'"
    if only_missing:
        where += " AND m.source_item_id IS NULL"
    sql = f"""
        SELECT
            s.*,
            c.title_zh_hant,
            c.title_zh_hans,
            c.body_zh_hant,
            c.body_zh_hans,
            c.seo_title,
            c.seo_description,
            c.case_transaction_override,
            c.case_jp_region_override,
            c.case_transit_override,
            c.listing_media_json
        FROM source_items s
        JOIN content_items c ON c.source_item_id = s.id
        LEFT JOIN case_investment_metrics m ON m.source_item_id = s.id
        WHERE {where}
        ORDER BY s.id DESC
    """
    if limit > 0:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (limit,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill case investment/rent-yield metrics for jp_listing cases.")
    parser.add_argument("--limit", type=int, default=0, help="Max cases to process; 0 means all.")
    parser.add_argument("--only-missing", action="store_true", help="Only process rows not present in case_investment_metrics.")
    parser.add_argument("--commit-every", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live-price", action="store_true", help="Slow path: fetch source pages when price is missing.")
    parser.add_argument("--live-source", action="store_true", help="Slow path: fetch current/same-case source pages to look for public rent/yield fields.")
    args = parser.parse_args()

    init_db()
    commit_every = max(20, int(args.commit_every or 200))
    counts: dict[str, int] = {}
    processed = 0
    filled = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with get_conn() as conn:
        rent_model = _build_rent_sample_model(conn)
        print(
            "[investment] rent model "
            f"city_layout={len(rent_model['city_layout'])} pref_layout={len(rent_model['pref_layout'])} pref={len(rent_model['pref'])}",
            flush=True,
        )
        rows = _source_rows(conn, only_missing=bool(args.only_missing), limit=max(0, int(args.limit or 0)))
        total = len(rows)
        for idx, row in enumerate(rows, 1):
            sid = int(row.get("id") or 0)
            if sid <= 0:
                continue
            try:
                meta = app.infer_case_metadata(row)
                fields = app._extract_listing_fields(row, meta=meta)
                metrics = app._case_investment_metrics(
                    row,
                    fields,
                    use_cache=False,
                    allow_live_price=bool(args.live_price),
                    allow_estimates=False,
                )
                comparison_sources: list[dict] = []
                if not bool(metrics.get("has_income_data")):
                    public_metrics, comparison_sources = _public_metrics_from_same_case(
                        conn,
                        row,
                        fields,
                        live_source=bool(args.live_source),
                    )
                    if public_metrics:
                        metrics = public_metrics
                if not bool(metrics.get("has_income_data")):
                    modeled = _estimate_from_rent_model(
                        row,
                        fields,
                        rent_model,
                        comparison_sources=comparison_sources,
                    )
                    if modeled:
                        metrics = modeled
            except Exception as exc:
                metrics = {
                    "rows": [
                        {"label": "總價（含稅）", "value": "—"},
                        {"label": "滿租租售比", "value": "—"},
                        {"label": "年收入（滿租時）", "value": "—"},
                        {"label": "現行租售比", "value": "—"},
                        {"label": "年收入（現行）", "value": "—"},
                    ],
                    "has_income_data": False,
                    "estimated": False,
                    "note": f"回填時計算失敗：{type(exc).__name__}",
                    "source_url": "",
                    "data_quality": "error",
                }
            quality = app._case_investment_metrics_quality(metrics)
            metrics["data_quality"] = quality
            source_url = str(metrics.get("source_url") or "")
            source_label = ""
            note = str(metrics.get("note") or "")
            if source_url:
                source_label = "公開家租相場"
            elif quality == "db_rent_sample_estimate":
                source_label = "站內租賃樣本"
            elif quality == "actual_source":
                source_label = "原站公開資料"
            counts[quality] = int(counts.get(quality, 0)) + 1
            if bool(metrics.get("has_income_data")):
                filled += 1
            processed += 1
            if not args.dry_run:
                conn.execute(
                    """
                    INSERT INTO case_investment_metrics (
                        source_item_id, metrics_json, data_quality, source_label, source_url,
                        computed_at, source_last_checked_at, note
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_item_id) DO UPDATE SET
                        metrics_json = excluded.metrics_json,
                        data_quality = excluded.data_quality,
                        source_label = excluded.source_label,
                        source_url = excluded.source_url,
                        computed_at = excluded.computed_at,
                        source_last_checked_at = excluded.source_last_checked_at,
                        note = excluded.note
                    """,
                    (
                        sid,
                        json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
                        quality,
                        source_label,
                        source_url,
                        now,
                        str(row.get("last_checked_at") or ""),
                        note,
                    ),
                )
            if idx % commit_every == 0:
                if not args.dry_run:
                    conn.commit()
                print(f"[investment] {idx}/{total} processed filled={filled} counts={counts}", flush=True)
        if not args.dry_run:
            conn.commit()
    print(f"[investment] done processed={processed} filled={filled} counts={counts}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
