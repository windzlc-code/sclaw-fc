import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

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

from src.config import DB_PATH  # noqa: E402
from src.portal_case_search import (  # noqa: E402
    is_likely_agent_portrait_image_url,
    sort_property_image_urls_for_hero,
    split_listing_image_urls_property_vs_agent,
)

TARGET_W = 640
TARGET_H = 480
MEDIA_URL_KEYS = (
    "url",
    "src",
    "image_url",
    "imageUrl",
    "thumbnail",
    "thumbnail_url",
    "thumb_url",
)
FLOORPLAN_TOKENS = ("madori", "floorplan", "floor_plan", "layout", "間取")
MAP_TOKENS = ("map", "chizu", "location", "access", "station", "route", "地図")
PHOTO_TOKENS = (
    "gaikan",
    "appearance",
    "building",
    "naikan",
    "interior",
    "living",
    "ldk",
    "bukken",
    "chuko",
    "mansion",
)


def _is_suumo_resize_url(u: str) -> bool:
    try:
        p = urlparse(str(u or "").strip())
    except Exception:
        return False
    return "suumo." in (p.netloc or "").lower() and "resizeimage" in (p.path or "").lower()


def _resize_dims(u: str) -> tuple[int, int]:
    try:
        q = dict(parse_qsl(urlparse(str(u or "")).query, keep_blank_values=True))
        return int(q.get("w") or 0), int(q.get("h") or 0)
    except Exception:
        return 0, 0


def normalize_suumo_display_url(u: str, *, w: int = TARGET_W, h: int = TARGET_H) -> tuple[str, bool]:
    raw = str(u or "").strip()
    if not raw:
        return "", False
    try:
        p = urlparse(raw)
    except Exception:
        return raw, False
    host = (p.netloc or "").lower()
    path = (p.path or "").lower()
    if "suumo." in host and "/gazo/bukken/" in path and path.endswith((".jpg", ".jpeg", ".png", ".webp")):
        raw_path = p.path or ""
        idx = raw_path.lower().find("/gazo/")
        if idx >= 0:
            src = raw_path[idx + 1 :].lstrip("/")
            out = "https://img01.suumo.com/jj/resizeImage?" + urlencode(
                {"src": src, "w": str(int(w)), "h": str(int(h))}
            )
            return out, out != raw
    if "suumo." not in host or "resizeimage" not in path:
        return raw, False
    try:
        pairs = parse_qsl(p.query, keep_blank_values=True)
    except Exception:
        return raw, False
    q: dict[str, str] = {}
    for k, v in pairs:
        q[str(k)] = str(v)
    if not str(q.get("src") or "").strip():
        return raw, False
    if str(q.get("w") or "") == str(int(w)) and str(q.get("h") or "") == str(int(h)):
        return raw, False
    q["w"] = str(int(w))
    q["h"] = str(int(h))
    out = p._replace(query=urlencode(list(q.items()), doseq=True)).geturl()
    return out, out != raw


def _normalize_image_lines(raw: str) -> tuple[str, int]:
    changed = 0
    out: list[str] = []
    seen: set[str] = set()
    for line in str(raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        ns, did = normalize_suumo_display_url(s)
        if did:
            changed += 1
        if ns and ns not in seen:
            seen.add(ns)
            out.append(ns)
    return "\n".join(out), changed


def _normalize_listing_media_json(raw: str) -> tuple[str, int]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return raw or "[]", 0
    if not isinstance(data, list):
        return raw or "[]", 0
    changed = 0
    out: list[Any] = []
    for entry in data:
        if isinstance(entry, str):
            ns, did = normalize_suumo_display_url(entry)
            changed += int(did)
            out.append(ns or entry)
            continue
        if isinstance(entry, dict):
            copied = dict(entry)
            for key in MEDIA_URL_KEYS:
                val = copied.get(key)
                if isinstance(val, str) and val.strip().startswith("http"):
                    ns, did = normalize_suumo_display_url(val)
                    if did:
                        copied[key] = ns
                        changed += 1
                    break
            out.append(copied)
            continue
        out.append(entry)
    if changed <= 0:
        return raw or "[]", 0
    return json.dumps(out, ensure_ascii=False, separators=(",", ":")), changed


def _url_has_any(u: str, tokens: tuple[str, ...]) -> bool:
    lu = str(u or "").lower()
    return any(t in lu for t in tokens)


def _is_floorplan_url(u: str) -> bool:
    return _url_has_any(u, FLOORPLAN_TOKENS)


def _is_map_url(u: str) -> bool:
    return _url_has_any(u, MAP_TOKENS)


def _is_photoish_url(u: str) -> bool:
    lu = str(u or "").lower()
    if is_likely_agent_portrait_image_url(u) or _is_map_url(lu) or _is_floorplan_url(lu):
        return False
    return _url_has_any(lu, PHOTO_TOKENS) or bool(re.search(r"/img/\d{1,4}/\d{6,}", lu))


def _content_ratio(url: str, *, timeout: float = 8.0) -> float | None:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        req = urllib.request.Request(str(url), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            blob = res.read(4_000_000)
        import io

        im = Image.open(io.BytesIO(blob)).convert("RGB")
        im.thumbnail((180, 135))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            pixels = list(im.getdata())
        if not pixels:
            return None
        non_blank = 0
        for r, g, b in pixels:
            if min(r, g, b) < 238 or (max(r, g, b) - min(r, g, b)) > 18:
                non_blank += 1
        return round(non_blank / len(pixels), 4)
    except Exception:
        return None


def _row_findings(
    row: sqlite3.Row,
    *,
    http_check: bool,
    max_http_left: int,
    min_content_ratio: float,
) -> tuple[dict[str, Any], int]:
    image_urls = str(row["image_urls"] or "")
    listing_media_json = str(row["listing_media_json"] or "[]")
    norm_image_urls, image_changes = _normalize_image_lines(image_urls)
    norm_listing_media_json, media_changes = _normalize_listing_media_json(listing_media_json)

    prop, agent = split_listing_image_urls_property_vs_agent(
        norm_image_urls or image_urls,
        str(row["body_original"] or ""),
        norm_listing_media_json or listing_media_json,
        item_url=str(row["item_url"] or ""),
        prop_limit=14,
        agent_limit=8,
    )
    prop = sort_property_image_urls_for_hero(prop)
    hero = prop[0] if prop else ""

    oversized_count = 0
    for u in [x.strip() for x in image_urls.splitlines() if x.strip()]:
        if _is_suumo_resize_url(u):
            cw, ch = _resize_dims(u)
            if cw > TARGET_W or ch > TARGET_H:
                oversized_count += 1
    try:
        lm = json.loads(listing_media_json or "[]")
    except Exception:
        lm = []
    if isinstance(lm, list):
        for entry in lm:
            vals: list[str] = []
            if isinstance(entry, str):
                vals = [entry]
            elif isinstance(entry, dict):
                vals = [str(entry.get(k) or "") for k in MEDIA_URL_KEYS]
            for u in vals:
                if _is_suumo_resize_url(u):
                    cw, ch = _resize_dims(u)
                    if cw > TARGET_W or ch > TARGET_H:
                        oversized_count += 1
                        break

    reasons: list[str] = []
    if not prop:
        reasons.append("missing_property_images")
    if hero and is_likely_agent_portrait_image_url(hero):
        reasons.append("agent_image_as_hero")
    if hero and _is_floorplan_url(hero) and any(_is_photoish_url(u) for u in prop[1:]):
        reasons.append("floorplan_before_photos")
    if oversized_count:
        reasons.append("oversized_suumo_resize_padding_risk")

    http_used = 0
    hero_content_ratio: float | None = None
    if http_check and hero and max_http_left > 0:
        hero_display, _ = normalize_suumo_display_url(hero)
        hero_content_ratio = _content_ratio(hero_display or hero)
        http_used = 1
        if hero_content_ratio is not None and hero_content_ratio < min_content_ratio:
            reasons.append("hero_low_content_ratio")

    finding = {
        "source_item_id": int(row["id"]),
        "item_url": row["item_url"],
        "title_original": row["title_original"],
        "source_name": row["source_name"],
        "hero_url": hero,
        "property_images": len(prop),
        "agent_images": len(agent),
        "oversized_suumo_resize_urls": int(oversized_count),
        "image_url_rewrites": int(image_changes),
        "listing_media_rewrites": int(media_changes),
        "hero_content_ratio": hero_content_ratio,
        "reasons": reasons,
    }
    return finding, http_used


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=60000")
    except sqlite3.Error:
        pass
    where = "WHERE (si.content_kind = 'jp_listing' OR coalesce(ci.listing_media_json, '[]') != '[]' OR coalesce(si.image_urls, '') != '')"
    params: list[Any] = []
    if args.case_id:
        where += " AND si.id = ?"
        params.append(int(args.case_id))
    sql = f"""
        SELECT si.id, si.source_name, si.item_url, si.title_original, si.body_original,
               si.image_urls, si.content_kind, ci.listing_media_json
        FROM source_items si
        JOIN content_items ci ON ci.source_item_id = si.id
        {where}
        ORDER BY si.last_checked_at DESC, si.id DESC
        LIMIT ?
    """
    params.append(max(1, int(args.limit or 1)))
    rows = conn.execute(sql, params).fetchall()

    findings: list[dict[str, Any]] = []
    fix_rows: list[tuple[int, str, str, int, int]] = []
    http_left = max(0, int(args.max_http or 0))
    for row in rows:
        finding, used = _row_findings(
            row,
            http_check=bool(args.http_check),
            max_http_left=http_left,
            min_content_ratio=float(args.min_content_ratio),
        )
        http_left -= used
        if finding["reasons"]:
            findings.append(finding)
        if args.fix and (finding["image_url_rewrites"] or finding["listing_media_rewrites"]):
            new_image_urls, _ = _normalize_image_lines(str(row["image_urls"] or ""))
            new_media_json, _ = _normalize_listing_media_json(str(row["listing_media_json"] or "[]"))
            fix_rows.append(
                (
                    int(row["id"]),
                    new_image_urls,
                    new_media_json,
                    int(finding["image_url_rewrites"]),
                    int(finding["listing_media_rewrites"]),
                )
            )

    fixed_image_urls = 0
    fixed_media_urls = 0
    if fix_rows:
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            for sid, new_image_urls, new_media_json, image_n, media_n in fix_rows:
                conn.execute(
                    """
                    UPDATE source_items
                    SET image_urls = ?, last_checked_at = ?
                    WHERE id = ?
                    """,
                    (new_image_urls, now, sid),
                )
                conn.execute(
                    """
                    UPDATE content_items
                    SET listing_media_json = ?, updated_at = ?
                    WHERE source_item_id = ?
                    """,
                    (new_media_json, now, sid),
                )
                fixed_image_urls += image_n
                fixed_media_urls += media_n

    report = {
        "ok": True,
        "mode": "fix" if args.fix else "audit",
        "db_path": str(DB_PATH),
        "target_size": {"w": TARGET_W, "h": TARGET_H},
        "scanned_rows": len(rows),
        "finding_rows": len(findings),
        "fixed_rows": len(fix_rows),
        "fixed_image_urls": int(fixed_image_urls),
        "fixed_listing_media_urls": int(fixed_media_urls),
        "http_checked": int(max(0, int(args.max_http or 0)) - http_left),
        "elapsed_sec": round(time.time() - started, 2),
        "findings": findings[: max(0, int(args.report_examples or 30))],
    }
    if args.write_report:
        out_path = Path(args.write_report)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["report_path"] = str(out_path)
    conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if args.fail_on_findings and findings:
        raise SystemExit(2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and fix SCLAW case media blank-space risks.")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--case-id", type=int, default=0)
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--http-check", action="store_true")
    parser.add_argument("--max-http", type=int, default=20)
    parser.add_argument("--min-content-ratio", type=float, default=0.55)
    parser.add_argument("--write-report", default="logs/case_media_quality_audit.json")
    parser.add_argument("--report-examples", type=int, default=30)
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
