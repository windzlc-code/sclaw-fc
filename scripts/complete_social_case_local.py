from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sqlite3
import subprocess
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import imageio_ffmpeg
import numpy as np
import requests
from mutagen import File as MutagenFile
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
BATCH_RECORDS_PATH = ROOT / "data" / "social_case_batch_records.json"
TG_ROOT = Path(r"D:\digital_human_tg_bot")
TG_DB = TG_ROOT / "data" / "workbench.db"
DEFAULT_DIGITAL_HUMAN_VIDEO = TG_ROOT / "data" / "voice_sources" / "wechat_voice_20260525_122432_151.mp4"

FPS = 18
PORTRAIT_SIZE = (720, 1280)
LANDSCAPE_SIZE = (1280, 720)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) social-case-local-render/1.0"
WORKBENCH_API = "http://127.0.0.1:8013/api/social-case-workbench/package"

COMMON_ZH_TOKENS = (
    "日本",
    "房產",
    "案件",
    "交通",
    "價格",
    "格局",
    "面積",
    "室內",
    "室外",
    "外觀",
    "顧問",
    "資料",
    "完整",
    "私訊",
    "步行",
    "車站",
    "銷售",
    "亮點",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\NotoSansTC-VF.ttf",
        r"C:\Windows\Fonts\msjhbd.ttc" if bold else r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\Microsoft JhengHei UI Bold.ttf" if bold else r"C:\Windows\Fonts\Microsoft JhengHei UI.ttf",
        r"C:\Windows\Fonts\meiryob.ttc" if bold else r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for item in candidates:
        path = Path(item)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                pass
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    fnt: ImageFont.ImageFont,
    max_width: int,
    max_lines: int | None = None,
) -> list[str]:
    raw = str(text or "").replace("\r", "").strip()
    if not raw:
        return []
    lines: list[str] = []
    for para in raw.split("\n"):
        current = ""
        for char in para:
            trial = current + char
            if text_size(draw, trial, fnt)[0] <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = char
                if max_lines and len(lines) >= max_lines:
                    clipped = lines[-1]
                    while text_size(draw, clipped + "...", fnt)[0] > max_width and clipped:
                        clipped = clipped[:-1]
                    lines[-1] = clipped + "..."
                    return lines
        if current:
            lines.append(current)
            if max_lines and len(lines) >= max_lines:
                return lines
    return lines


def draw_round_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int, int] | tuple[int, int, int],
    outline: tuple[int, int, int, int] | tuple[int, int, int] | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def fit_cover(img: Image.Image, size: tuple[int, int], zoom: float = 1.0) -> Image.Image:
    w, h = size
    src = ImageOps.exif_transpose(img.convert("RGB"))
    if zoom > 1.0:
        cw = max(1, int(src.width / zoom))
        ch = max(1, int(src.height / zoom))
        left = (src.width - cw) // 2
        top = (src.height - ch) // 2
        src = src.crop((left, top, left + cw, top + ch))
    scale = max(w / src.width, h / src.height)
    nw, nh = int(src.width * scale + 0.5), int(src.height * scale + 0.5)
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


def image_quality_metrics(img: Image.Image) -> dict[str, Any]:
    src = ImageOps.exif_transpose(img.convert("RGB"))
    sample = src.resize((240, 180), Image.Resampling.LANCZOS)
    arr = np.array(sample)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    white_ratio = float(((arr[:, :, 0] > 220) & (arr[:, :, 1] > 220) & (arr[:, :, 2] > 220)).mean())
    low_sat_ratio = float((hsv[:, :, 1] < 35).mean())
    edges = cv2.Canny(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY), 50, 150)
    edge_ratio = float(edges.mean() / 255.0)
    return {
        "width": int(src.width),
        "height": int(src.height),
        "white_ratio": round(white_ratio, 4),
        "low_saturation_ratio": round(low_sat_ratio, 4),
        "edge_ratio": round(edge_ratio, 4),
    }


def needs_full_image_display(scene: dict[str, Any], img: Image.Image, metrics: dict[str, Any] | None = None) -> bool:
    metrics = metrics or image_quality_metrics(img)
    role = str(scene.get("role") or "")
    category = str(scene.get("category") or "")
    url = str(scene.get("url") or "")
    marker_text = f"{role} {category} {url}".lower()
    info_marker = any(k in marker_text for k in ("格局圖", "間取り", "madori", "floor", "layout", "plan", "路線", "交通路線圖卡", "route"))
    diagram_like = (
        float(metrics.get("white_ratio") or 0) >= 0.52
        and float(metrics.get("low_saturation_ratio") or 0) >= 0.72
        and float(metrics.get("edge_ratio") or 0) <= 0.14
    )
    return bool(info_marker or diagram_like)


def make_contained_background(img: Image.Image, size: tuple[int, int], landscape: bool) -> Image.Image:
    w, h = size
    canvas = Image.new("RGB", size, (239, 244, 244))
    draw = ImageDraw.Draw(canvas)
    if landscape:
        box = (44, 132, w - 356, h - 168)
    else:
        box = (34, 286, w - 34, h - 496)
    max_w = max(160, box[2] - box[0])
    max_h = max(160, box[3] - box[1])
    src = ImageOps.exif_transpose(img.convert("RGB"))
    scale = min(max_w / src.width, max_h / src.height)
    nw, nh = max(1, int(src.width * scale + 0.5)), max(1, int(src.height * scale + 0.5))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    x = box[0] + (max_w - nw) // 2
    y = box[1] + (max_h - nh) // 2
    draw_round_rect(draw, (x - 12, y - 12, x + nw + 12, y + nh + 12), 18, (255, 255, 255), (188, 206, 208), 2)
    canvas.paste(resized, (x, y))
    return canvas


def make_portrait_object_background(img: Image.Image, size: tuple[int, int], scene: dict[str, Any]) -> Image.Image:
    w, h = size
    src = ImageOps.exif_transpose(img.convert("RGB"))
    blurred = fit_cover(src, size).filter(ImageFilter.GaussianBlur(18))
    tint = Image.new("RGB", size, (12, 28, 34))
    canvas = Image.blend(blurred, tint, 0.22)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, 0, w, 260), fill=(0, 0, 0, 82))
    od.rectangle((0, 900, w, h), fill=(0, 0, 0, 92))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)

    display_mode = str(scene.get("display_mode") or "")
    category = str(scene.get("category") or "")
    if display_mode == "contain_full_image":
        box = (34, 292, w - 34, 902)
    elif category in {"cover", "exterior", "traffic"}:
        box = (22, 268, w - 22, 948)
    else:
        box = (30, 286, w - 30, 930)
    max_w = max(240, box[2] - box[0])
    max_h = max(240, box[3] - box[1])
    scale = min(max_w / src.width, max_h / src.height)
    nw, nh = max(1, int(src.width * scale + 0.5)), max(1, int(src.height * scale + 0.5))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    x = box[0] + (max_w - nw) // 2
    y = box[1] + (max_h - nh) // 2

    panel = Image.new("RGBA", size, (0, 0, 0, 0))
    pd = ImageDraw.Draw(panel)
    shadow = (x - 12, y - 12, x + nw + 12, y + nh + 12)
    pd.rounded_rectangle(shadow, radius=28, fill=(0, 0, 0, 80))
    pd.rounded_rectangle((x - 8, y - 8, x + nw + 8, y + nh + 8), radius=24, fill=(255, 255, 255, 230))
    mask = Image.new("L", (nw, nh), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, nw, nh), radius=18, fill=255)
    photo = resized.convert("RGBA")
    photo.putalpha(mask)
    panel.alpha_composite(photo, (x, y))
    return Image.alpha_composite(canvas, panel).convert("RGB")


def apply_camera_motion(
    background: Image.Image,
    size: tuple[int, int],
    progress: float,
    scene_index: int,
    *,
    landscape: bool,
    contained: bool = False,
) -> Image.Image:
    w, h = size
    p = max(0.0, min(1.0, float(progress or 0.0)))
    ease = 0.5 - 0.5 * math.cos(math.pi * p)
    if scene_index >= 200:
        max_zoom = 0.072 if landscape else 0.088
    else:
        max_zoom = 0.036 if contained else (0.052 if landscape else 0.046)
    if scene_index % 5 in {1, 4}:
        zoom = 1.0 + max_zoom * (1.0 - ease)
        travel = 1.0 - ease
    else:
        zoom = 1.0 + max_zoom * ease
        travel = ease
    src = background.convert("RGB")
    zw = max(w + 2, int(w * zoom + 0.5))
    zh = max(h + 2, int(h * zoom + 0.5))
    layer = src.resize((zw, zh), Image.Resampling.LANCZOS)
    max_x = max(0, zw - w)
    max_y = max(0, zh - h)
    mode = scene_index % 4
    if mode == 0:
        left = int(max_x * travel)
        top = max_y // 2
    elif mode == 1:
        left = int(max_x * (1.0 - travel))
        top = max_y // 2
    elif mode == 2:
        left = max_x // 2
        top = int(max_y * travel)
    else:
        left = int(max_x * travel)
        top = int(max_y * (1.0 - travel))
    return layer.crop((left, top, left + w, top + h))


def make_host_spotlight_background(avatar_source_path: str, avatar: Image.Image, size: tuple[int, int]) -> Image.Image:
    source = Path(str(avatar_source_path or ""))
    if source.exists():
        try:
            with Image.open(source) as raw:
                return fit_cover(raw, size)
        except Exception:
            pass
    canvas = Image.new("RGB", size, (24, 44, 52))
    w, h = size
    av = avatar.resize((min(w, 560), min(h, 740)), Image.Resampling.LANCZOS)
    x = (w - av.width) // 2
    y = max(0, (h - av.height) // 2)
    canvas.paste(Image.new("RGB", size, (24, 44, 52)))
    canvas.paste(av.convert("RGB"), (x, y), av if av.mode == "RGBA" else None)
    return canvas


def placeholder_image(title: str, size: tuple[int, int] = (960, 720)) -> Image.Image:
    img = Image.new("RGB", size, (242, 246, 246))
    draw = ImageDraw.Draw(img)
    w, h = size
    draw.rectangle((0, 0, w, h), fill=(233, 241, 240))
    for i in range(9):
        bx = 70 + i * 95
        bh = 130 + (i % 4) * 35
        draw.rectangle((bx, h - 260 - bh, bx + 58, h - 260), fill=(76, 92, 108))
        for y in range(h - 260 - bh + 18, h - 275, 34):
            draw.rectangle((bx + 12, y, bx + 25, y + 14), fill=(248, 205, 103))
            draw.rectangle((bx + 35, y, bx + 48, y + 14), fill=(248, 205, 103))
    draw_round_rect(draw, (80, h - 220, w - 80, h - 70), 22, (255, 255, 255), (191, 210, 215), 3)
    title_lines = wrap_text(draw, title, font(36, True), w - 180, 2)
    yy = h - 190
    for line in title_lines:
        draw.text((110, yy), line, font=font(36, True), fill=(26, 45, 60))
        yy += 46
    draw.text((110, yy + 8), "日本房產案件素材", font=font(28), fill=(76, 100, 112))
    return img


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def task_row(conn: sqlite3.Connection, task_id: str | None) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    if task_id:
        row = conn.execute("SELECT * FROM workspace_tasks WHERE id = ? LIMIT 1", (task_id,)).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM workspace_tasks
            WHERE source LIKE 'social-case:%' AND status IN ('queued', 'processing', 'failed')
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        raise SystemExit(f"Cannot find social-case task: {task_id or '(latest queued)'}")
    return dict(row)


def source_item_id_from_task(row: dict[str, Any], asset_pack: dict[str, Any]) -> int:
    if isinstance(asset_pack.get("source_item_id"), int):
        return int(asset_pack["source_item_id"])
    raw = str(row.get("source") or "")
    if raw.startswith("social-case:"):
        try:
            return int(raw.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


def text_quality(text: str) -> int:
    score = 0
    for ch in text:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            score += 2
        if 0x3040 <= o <= 0x30FF:
            score += 1
        if 0xE000 <= o <= 0xF8FF or 0x80 <= o <= 0x9F:
            score -= 8
        if ch in "�ÃÂåçæäèéïð":
            score -= 3
    for token in COMMON_ZH_TOKENS:
        if token in text:
            score += 8
    score -= text.count("?") * 2
    return score


def repair_text(text: Any) -> Any:
    if not isinstance(text, str) or not text:
        return text
    candidates = [text]
    for enc in ("latin1", "cp1252", "cp950", "big5"):
        try:
            candidates.append(text.encode(enc).decode("utf-8"))
        except Exception:
            pass
    best = max(candidates, key=text_quality)
    return best


def repair_obj(value: Any) -> Any:
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [repair_obj(x) for x in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            out[str(repair_text(k)) if isinstance(k, str) else k] = repair_obj(v)
        return out
    return value


def sentence_fit(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    parts = [x.strip() for x in re.split(r"(?<=[。！？!?；;])\s*|\n+", text) if x.strip()]
    out = ""
    for part in parts:
        if len(out + part) > max_chars:
            break
        out += part
    if not out:
        out = text[:max_chars]
    return re.sub(r"[，,、；;：:。！？!?\s]+$", "。", out).strip()


def clean_script(script: str, title: str) -> str:
    text = " ".join(str(repair_text(script) or "").split())
    text = re.sub(r"大家好，?這支先用\d+個重點片段，?快速帶你看[^。！？!?]*[。！？!?]", "", text)
    text = re.sub(r"第[一二三四五六七八九十\d]+段", "接著", text)
    text = text.replace("用畫面說明", "說明").replace("畫面要讓人很快看懂", "讓客戶快速看懂")
    text = text.replace("先別滑走，", "").strip()
    if text:
        return sentence_fit(text, 360)
    return (
        f"你好，我先帶你看{title}。"
        "我會用外觀、室內、格局和交通幾個畫面，幫你快速判斷這個案件值不值得深入看。"
        "如果想拿完整資料、費用明細或預約賞屋，可以直接私訊我。"
    )


def short_lines_from_subtitles(pack: dict[str, Any], script: str, title: str) -> list[str]:
    subtitles = str(pack.get("subtitles") or "").replace("\r", "").strip()
    rows = []
    if subtitles:
        for line in subtitles.split("\n"):
            line = line.strip()
            if line:
                rows.append(line)
    if rows:
        return rows[:8]
    parts = [x.strip() for x in script.replace("。", "。\n").replace("！", "！\n").splitlines() if x.strip()]
    return (parts[:8] or [title, "完整資料、費用明細與預約賞屋，歡迎私訊。"])


def asset_segments(pack: dict[str, Any], work_dir: Path) -> list[dict[str, Any]]:
    rows = pack.get("selected_segments")
    if isinstance(rows, list) and rows:
        return [dict(x) for x in rows if isinstance(x, dict)]
    rows = load_json(work_dir / "selected_segments.json", [])
    if isinstance(rows, list) and rows:
        return [dict(x) for x in rows if isinstance(x, dict)]
    urls = pack.get("image_urls") if isinstance(pack.get("image_urls"), list) else []
    return [{"number": idx + 1, "url": url, "role": "案件畫面", "copy_hint": ""} for idx, url in enumerate(urls[:8])]


def fetch_workbench_item(source_item_id: int) -> dict[str, Any]:
    if source_item_id <= 0:
        return {}
    try:
        resp = requests.get(
            WORKBENCH_API,
            params={"source_item_id": source_item_id},
            headers={"User-Agent": USER_AGENT},
            timeout=8,
        )
        resp.raise_for_status()
        data = json.loads(resp.content.decode("utf-8"))
        item = data.get("item") if isinstance(data, dict) else {}
        return repair_obj(item) if isinstance(item, dict) else {}
    except Exception:
        return {}


def fact_value(pack: dict[str, Any], key: str) -> str:
    facts = pack.get("facts") if isinstance(pack.get("facts"), dict) else {}
    return str(facts.get(key) or "").strip()


def unique_urls(rows: list[Any], limit: int = 16) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        url = str(row.get("url") if isinstance(row, dict) else row or "").strip()
        if not url:
            continue
        key = url.lower().split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def role_category(role: str, idx: int) -> str:
    role_l = str(role or "").lower()
    if idx == 0 or "封面" in role_l:
        return "cover"
    if any(k in role_l for k in ("室內", "格局", "採光", "動線", "interior", "layout")):
        return "indoor"
    if any(k in role_l for k in ("交通", "周邊", "生活", "車站", "route")):
        return "traffic"
    if any(k in role_l for k in ("外觀", "建物", "社區", "管理", "exterior")):
        return "exterior"
    if idx in (1, 2, 3):
        return "indoor"
    if idx in (4, 5):
        return "exterior"
    return "traffic"


def category_label(category: str) -> str:
    return {
        "cover": "封面主賣點",
        "indoor": "室內/格局",
        "exterior": "室外/建物",
        "traffic": "交通/生活圈",
    }.get(category, "銷售畫面")


def image_sales_angle(category: str, facts: dict[str, str]) -> str:
    price = facts.get("價格") or "價格待確認"
    layout = facts.get("格局") or facts.get("面積") or "空間條件"
    traffic = facts.get("交通") or facts.get("區域") or "交通與生活機能"
    if category == "cover":
        return f"開場先打 {traffic}，再帶出 {price}，建立第一眼詢問理由。"
    if category == "indoor":
        return f"室內段主講採光、動線與 {layout}，讓客戶判斷自住或出租使用情境。"
    if category == "exterior":
        return "室外段補建物外觀、社區質感與管理印象，降低客戶對遠距看房的不確定感。"
    if category == "traffic":
        return f"交通段說清楚 {traffic}，把通勤、生活圈與保值性連在一起。"
    return "補充案件價值，讓客戶有理由私訊索取完整資料。"


def lead_clue_for_category(category: str, facts: dict[str, str]) -> str:
    if category == "indoor":
        return "追問：是否要完整室內照片、格局圖、採光方向或可入住/出租時程。"
    if category == "exterior":
        return "追問：是否在意管理品質、社區規模、屋齡、總戶數與修繕積立。"
    if category == "traffic":
        return "追問：通勤目的地、是否需要車站步行距離、附近生活機能與路線圖。"
    return "追問：預算、用途、付款方式、看房時間與是否要費用明細。"


def enrich_pack_from_item(pack: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    if not item:
        return pack
    for key in ("title", "source_item_id", "item_url", "case_url", "score", "score_reasons", "facts", "selling_points", "platform", "property_images", "body_excerpt"):
        if item.get(key) not in (None, "", []):
            pack[key] = item.get(key)
    content_pack = item.get("content_pack")
    if isinstance(content_pack, dict):
        pack["content_pack"] = content_pack
    sales_plan = item.get("sales_image_plan") or content_pack.get("sales_image_plan") if isinstance(content_pack, dict) else item.get("sales_image_plan")
    if isinstance(sales_plan, list) and sales_plan:
        pack["sales_image_plan"] = sales_plan
    if isinstance(item.get("property_images"), list) and item.get("property_images"):
        pack["image_urls"] = list(item.get("property_images") or [])
    return pack


def build_sales_segments(pack: dict[str, Any], work_dir: Path, max_images: int = 16) -> list[dict[str, Any]]:
    existing = asset_segments(pack, work_dir)
    content_pack = pack.get("content_pack") if isinstance(pack.get("content_pack"), dict) else {}
    plans: list[dict[str, Any]] = []
    for rows in (content_pack.get("sales_image_plan"), pack.get("sales_image_plan"), pack.get("selected_segments"), existing):
        if isinstance(rows, list):
            plans.extend([dict(x) for x in rows if isinstance(x, dict)])
    by_url = {str(x.get("url") or ""): x for x in plans if str(x.get("url") or "").strip()}
    urls = unique_urls(plans + list(pack.get("property_images") or []) + list(pack.get("image_urls") or []), limit=max_images)
    if not urls:
        urls = unique_urls(existing, limit=max_images)
    facts = dict(pack.get("facts") or {})
    out: list[dict[str, Any]] = []
    for idx, url in enumerate(urls):
        base = dict(by_url.get(url) or {})
        role = str(base.get("role") or "").strip()
        category = role_category(role, idx)
        if not role:
            role = category_label(category)
        copy_hint = str(base.get("copy_hint") or "").strip()
        if not copy_hint:
            copy_hint = image_sales_angle(category, facts)
        out.append(
            {
                **base,
                "number": idx + 1,
                "order": idx + 1,
                "url": url,
                "role": role,
                "category": category,
                "category_label": category_label(category),
                "copy_hint": copy_hint,
                "sales_angle": image_sales_angle(category, facts),
                "lead_clue": lead_clue_for_category(category, facts),
            }
        )
    return out


def create_traffic_route_card(work_dir: Path, title: str, pack: dict[str, Any]) -> Path:
    out_dir = work_dir / "materials"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "traffic_route_card.jpg"
    facts = dict(pack.get("facts") or {})
    traffic = facts.get("交通") or "交通路線待顧問確認"
    region = facts.get("區域") or ""
    price = facts.get("價格") or ""
    img = Image.new("RGB", (960, 720), (242, 246, 246))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 960, 720), fill=(239, 245, 244))
    draw_round_rect(draw, (58, 54, 902, 666), 26, (255, 255, 255), (189, 211, 211), 3)
    draw.text((92, 92), "交通路線與銷售線索", font=font(42, True), fill=(20, 85, 88))
    y = 158
    for line in wrap_text(draw, title, font(28, True), 760, 2):
        draw.text((92, y), line, font=font(28, True), fill=(36, 53, 65))
        y += 38
    y += 16
    route_lines = [traffic]
    if region:
        route_lines.append(f"區域：{region}")
    if price:
        route_lines.append(f"價格：{price}")
    for text in route_lines:
        draw_round_rect(draw, (92, y, 868, y + 54), 18, (227, 241, 240), (180, 212, 211), 2)
        draw.text((116, y + 12), text, font=font(24, True), fill=(26, 79, 82))
        y += 68
    line_y = min(520, y + 24)
    draw.line((140, line_y, 820, line_y), fill=(22, 141, 137), width=10)
    for x, label in ((160, "物件"), (480, "車站"), (800, "生活圈")):
        draw.ellipse((x - 25, line_y - 25, x + 25, line_y + 25), fill=(22, 141, 137), outline=(255, 255, 255), width=5)
        tw, _ = text_size(draw, label, font(23, True))
        draw.text((x - tw // 2, line_y + 38), label, font=font(23, True), fill=(36, 55, 66))
    draw.text((92, 604), "顧問跟進：問通勤目的地、預算、完整費用明細、更多室內外照片與預約賞屋時間。", font=font(22), fill=(62, 80, 91))
    img.save(path, quality=92)
    return path


def add_traffic_card_segment(segments: list[dict[str, Any]], work_dir: Path, title: str, pack: dict[str, Any]) -> list[dict[str, Any]]:
    if not fact_value(pack, "交通") and not fact_value(pack, "區域"):
        return segments
    segments = [
        dict(seg)
        for seg in segments
        if str(seg.get("role") or "") != "交通路線圖卡"
        and not str(seg.get("url") or "").lower().endswith("traffic_route_card.jpg")
    ]
    card = create_traffic_route_card(work_dir, title, pack)
    card_seg = {
        "url": str(card),
        "role": "交通路線圖卡",
        "category": "traffic",
        "category_label": "交通/生活圈",
        "copy_hint": image_sales_angle("traffic", dict(pack.get("facts") or {})),
        "sales_angle": image_sales_angle("traffic", dict(pack.get("facts") or {})),
        "lead_clue": lead_clue_for_category("traffic", dict(pack.get("facts") or {})),
    }
    insert_at = min(4, len(segments))
    out = [*segments[:insert_at], card_seg, *segments[insert_at:]]
    for idx, seg in enumerate(out, start=1):
        seg["number"] = idx
        seg["order"] = idx
    return out


def sales_video_subtitles(title: str, pack: dict[str, Any], segments: list[dict[str, Any]], script: str) -> list[str]:
    facts = dict(pack.get("facts") or {})
    facts_line = "｜".join([x for x in (facts.get("交通"), facts.get("價格"), facts.get("格局"), facts.get("面積")) if x])
    subtitles: list[str] = []
    if facts_line:
        subtitles.append(f"先看核心條件：{facts_line}")
    for seg in segments:
        category = str(seg.get("category") or "")
        if category == "indoor":
            subtitles.append(str(seg.get("sales_angle") or "室內重點看採光、格局、動線與可使用空間。"))
        elif category == "exterior":
            subtitles.append(str(seg.get("sales_angle") or "外觀重點看建物質感、管理與周邊第一印象。"))
        elif category == "traffic":
            subtitles.append(str(seg.get("sales_angle") or "交通重點看步行距離、生活圈與通勤便利。"))
        elif category == "cover":
            subtitles.append(str(seg.get("sales_angle") or f"{title}，先用最強賣點建立詢問理由。"))
    subtitles.append("想看完整地址、費用明細、更多室內外照片與預約賞屋，直接私訊顧問。")
    out: list[str] = []
    seen: set[str] = set()
    for line in subtitles:
        clean = " ".join(str(line or "").split())
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out[: max(5, min(14, len(segments) + 2))] or short_lines_from_subtitles(pack, script, title)


def build_sales_brief(title: str, source_id: int, pack: dict[str, Any], assets: list[dict[str, Any]], subtitles: list[str]) -> dict[str, Any]:
    facts = dict(pack.get("facts") or {})
    points = [str(x) for x in (pack.get("selling_points") or []) if str(x).strip()]
    highlights: list[dict[str, Any]] = []
    for weight, label, detail in (
        (98, "交通動線", facts.get("交通") or facts.get("區域") or ""),
        (92, "價格入口", facts.get("價格") or ""),
        (88, "格局面積", " / ".join([x for x in (facts.get("格局"), facts.get("面積")) if x])),
        (84, "室內外圖庫", f"{len([x for x in assets if x.get('downloaded')])} 張已保存素材"),
    ):
        if detail:
            highlights.append(
                {
                    "weight": weight,
                    "label": label,
                    "detail": detail,
                    "sales_use": "優先放在開場、字幕、私訊回覆與顧問跟進話術。",
                }
            )
    for idx, point in enumerate(points[:5], start=1):
        highlights.append({"weight": max(70, 86 - idx * 3), "label": f"銷售點 {idx}", "detail": point, "sales_use": "可拆成圖文輪播標題或短影音字幕。"})
    image_storyboard = []
    for asset in assets:
        image_storyboard.append(
            {
                "number": asset.get("number"),
                "category": asset.get("category_label") or category_label(str(asset.get("category") or "")),
                "role": asset.get("role"),
                "sales_angle": asset.get("sales_angle") or asset.get("copy_hint"),
                "lead_clue": asset.get("lead_clue"),
                "display_mode": asset.get("display_mode") or "",
                "source_resolution": asset.get("source_resolution") or {},
                "needs_visual_verification": bool(asset.get("needs_visual_verification")),
                "verification_note": asset.get("verification_note") or "",
                "url": asset.get("url"),
                "local_path": asset.get("local_path"),
                "downloaded": bool(asset.get("downloaded")),
            }
        )
    key_verification: list[dict[str, Any]] = [
        {
            "weight": 100,
            "item": "格局圖/交通圖完整性",
            "issue": "部分 SUUMO 圖片是平台縮圖，若用直式封面裁切會看不到完整格局或路線。",
            "action": "資訊型圖片一律用完整置入；發布前以原圖校證房間標示、方位、交通文字與尺寸。",
        },
        {
            "weight": 96,
            "item": "價格/格局/面積/交通一致性",
            "issue": "短影音字幕、口播與銷售表必須和來源站欄位一致。",
            "action": "顧問跟進前再次核對價格、2LDK、18.15坪與 JR高徳線 栗林公園北口駅 徒步8分。",
        },
    ]
    for asset in assets:
        if asset.get("needs_visual_verification"):
            res = asset.get("source_resolution") if isinstance(asset.get("source_resolution"), dict) else {}
            key_verification.append(
                {
                    "weight": 94 if asset.get("display_mode") == "contain_full_image" else 86,
                    "item": f"圖 {asset.get('number')}｜{asset.get('role')}",
                    "issue": asset.get("verification_note") or "素材需打開原圖確認是否被壓縮或裁切。",
                    "action": "用原圖/素材檔校證後再做圖文發布；資訊型圖片不要再用 cover 裁切。",
                    "local_path": asset.get("local_path"),
                    "url": asset.get("url"),
                    "resolution": f"{res.get('width') or '?'}x{res.get('height') or '?'}",
                    "display_mode": asset.get("display_mode") or "",
                }
            )
    traffic = facts.get("交通") or "交通待確認"
    advisor_questions = [
        "用途是自住、出租、置產保值，還是短期轉售？",
        "預算、付款方式與希望看的總費用明細範圍？",
        "是否要完整室內、室外、格局圖與交通路線圖？",
        "是否要顧問直接安排線上說明或預約賞屋？",
    ]
    intro = (
        f"您好，這個案件我先幫您整理重點：{traffic}。"
        "如果您要比較價格、格局、費用明細或完整照片，我可以直接把資料包給您，也能請顧問協助評估。"
    )
    return {
        "case": {
            "title": title,
            "source_item_id": source_id,
            "source_url": pack.get("item_url") or "",
            "case_url": pack.get("case_url") or "",
            "score": pack.get("score") or 0,
            "facts": facts,
        },
        "sales_highlights": sorted(highlights, key=lambda x: int(x.get("weight") or 0), reverse=True),
        "traffic_route": {
            "summary": traffic,
            "route_card": next((x.get("local_path") for x in assets if str(x.get("role") or "") == "交通路線圖卡"), ""),
            "follow_up": "把通勤目的地、步行距離與生活機能問出來，再引導索取完整資料與預約賞屋。",
        },
        "key_verification": key_verification,
        "image_storyboard": image_storyboard,
        "customer_lead_clues": [
            {"weight": 95, "signal": "問完整地址、費用明細、格局圖", "action": "立即轉顧問，提供完整資料包與預約時段。"},
            {"weight": 90, "signal": "問交通、步行距離、附近生活圈", "action": "回覆交通路線卡，追問通勤目的地與使用情境。"},
            {"weight": 86, "signal": "問室內照片、採光、是否可出租", "action": "傳室內外素材與租售/自住判斷表。"},
            {"weight": 82, "signal": "問價格、管理費、修繕積立金", "action": "提供費用拆解，邀請顧問做購買成本說明。"},
        ],
        "advisor_handoff": {
            "intro_message": intro,
            "questions": advisor_questions,
            "cta": "私訊「日本案件」取得完整地址、費用明細、更多室內外照片與預約賞屋。",
        },
        "video_subtitles": subtitles,
        "generated_at": now_iso(),
    }


def sales_brief_markdown(brief: dict[str, Any]) -> str:
    case = brief.get("case") if isinstance(brief.get("case"), dict) else {}
    lines = [
        f"# {case.get('title') or '日本房產案件銷售亮點'}",
        "",
        "## 基本條件",
    ]
    facts = case.get("facts") if isinstance(case.get("facts"), dict) else {}
    for k in ("價格", "格局", "面積", "交通", "區域"):
        if facts.get(k):
            lines.append(f"- {k}：{facts[k]}")
    if case.get("source_url"):
        lines.append(f"- 來源：{case.get('source_url')}")
    lines.extend(["", "## 銷售亮點"])
    for row in brief.get("sales_highlights") or []:
        lines.append(f"- [{row.get('weight')}] {row.get('label')}：{row.get('detail')}｜{row.get('sales_use')}")
    lines.extend(["", "## 重點校證內容"])
    for row in brief.get("key_verification") or []:
        lines.append(f"- [{row.get('weight')}] {row.get('item')}：{row.get('issue')}｜處理：{row.get('action')}")
        if row.get("resolution") or row.get("display_mode"):
            lines.append(f"  顯示/解析度：{row.get('display_mode') or '待確認'}｜{row.get('resolution') or ''}")
        if row.get("local_path"):
            lines.append(f"  原圖素材：{row.get('local_path')}")
    lines.extend(["", "## 圖文素材分鏡"])
    for row in brief.get("image_storyboard") or []:
        lines.append(f"- {row.get('number')}. {row.get('category')}｜{row.get('role')}：{row.get('sales_angle')}")
        if row.get("needs_visual_verification"):
            res = row.get("source_resolution") if isinstance(row.get("source_resolution"), dict) else {}
            lines.append(f"  校證：{row.get('verification_note')}｜顯示模式：{row.get('display_mode')}｜解析度：{res.get('width') or '?'}x{res.get('height') or '?'}")
        if row.get("lead_clue"):
            lines.append(f"  跟進：{row.get('lead_clue')}")
        if row.get("local_path"):
            lines.append(f"  素材：{row.get('local_path')}")
    lines.extend(["", "## 客戶線索與顧問話術"])
    for row in brief.get("customer_lead_clues") or []:
        lines.append(f"- [{row.get('weight')}] {row.get('signal')}：{row.get('action')}")
    handoff = brief.get("advisor_handoff") if isinstance(brief.get("advisor_handoff"), dict) else {}
    if handoff.get("intro_message"):
        lines.extend(["", "## 私訊開場", handoff["intro_message"]])
    return "\n".join(lines).strip() + "\n"


def download_case_images(segments: list[dict[str, Any]], work_dir: Path, title: str) -> list[dict[str, Any]]:
    out_dir = work_dir / "materials"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments[:16], start=1):
        url = str(seg.get("url") or "").strip()
        local = out_dir / f"source_{idx:02d}.jpg"
        ok = False
        if url.startswith(("http://", "https://")):
            try:
                resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=18)
                resp.raise_for_status()
                tmp = out_dir / f"source_{idx:02d}.download"
                tmp.write_bytes(resp.content)
                with Image.open(tmp) as im:
                    ImageOps.exif_transpose(im).convert("RGB").save(local, quality=92)
                tmp.unlink(missing_ok=True)
                ok = True
            except Exception:
                ok = False
        elif url:
            p = Path(url)
            if p.exists():
                try:
                    with Image.open(p) as im:
                        ImageOps.exif_transpose(im).convert("RGB").save(local, quality=92)
                    ok = True
                except Exception:
                    ok = False
        if not ok:
            placeholder_image(title).save(local, quality=92)
        metrics: dict[str, Any] = {}
        display_mode = "cover_crop"
        needs_verify = False
        verification_note = "可作情境畫面；仍需以原始素材確認重點文字與物件條件。"
        try:
            with Image.open(local) as meta_im:
                metrics = image_quality_metrics(meta_im)
                full_display = needs_full_image_display(seg, meta_im, metrics)
                low_res = int(metrics.get("width") or 0) <= 720 or int(metrics.get("height") or 0) <= 540
                display_mode = "contain_full_image" if full_display else "cover_crop"
                needs_verify = bool(full_display or low_res)
                if full_display:
                    verification_note = "資訊型圖片/格局或交通圖卡不可裁切；影片已改用完整置入，發布前仍要打開原圖校證文字、方位與尺寸。"
                elif low_res:
                    verification_note = "來源為平台縮圖尺寸，短影音可用，但圖文發布與顧問交接要打開原圖確認是否被壓縮到看不清。"
        except Exception:
            metrics = {}
        saved.append(
            {
                **seg,
                "local_path": str(local),
                "downloaded": ok,
                "source_resolution": {"width": metrics.get("width"), "height": metrics.get("height")} if metrics else {},
                "visual_metrics": metrics,
                "display_mode": display_mode,
                "needs_visual_verification": needs_verify,
                "verification_note": verification_note,
            }
        )
    if not saved:
        local = out_dir / "source_01.jpg"
        placeholder_image(title).save(local, quality=92)
        saved.append({"number": 1, "role": "案件畫面", "copy_hint": "", "url": "", "local_path": str(local), "downloaded": False, "display_mode": "contain_full_image", "needs_visual_verification": True, "verification_note": "占位素材，必須回原始案件補圖後再發布。"})
    return saved


def create_silent_wav(path: Path, seconds: float) -> None:
    framerate = 22050
    frames = int(max(1.0, seconds) * framerate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * frames)


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def media_duration(path: Path) -> float:
    try:
        media = MutagenFile(str(path))
        if media and media.info and getattr(media.info, "length", None):
            return float(media.info.length)
    except Exception:
        pass
    return wav_duration(path)


def render_tts(script: str, work_dir: Path, target_seconds: int) -> tuple[Path, float]:
    text_path = work_dir / "case_script_for_tts.txt"
    ps1_path = work_dir / "render_tts.ps1"
    audio_path = work_dir / "narration.wav"
    text_path.write_text(script, encoding="utf-8")
    ps1_path.write_text(
        "\n".join(
            [
                "Add-Type -AssemblyName System.Speech",
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
                "$s.Rate = 0",
                "$s.Volume = 100",
                "$voice = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh-*' } | Select-Object -First 1",
                "if ($voice) { $s.SelectVoice($voice.VoiceInfo.Name) }",
                f"$text = Get-Content -LiteralPath '{text_path}' -Raw -Encoding UTF8",
                f"$s.SetOutputToWaveFile('{audio_path}')",
                "$s.Speak($text)",
                "$s.Dispose()",
            ]
        ),
        encoding="utf-8-sig",
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
            cwd=str(work_dir),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )
    except Exception:
        create_silent_wav(audio_path, max(20.0, min(55.0, float(target_seconds or 40))))
    duration = wav_duration(audio_path)
    if duration < 2:
        create_silent_wav(audio_path, max(20.0, min(55.0, float(target_seconds or 40))))
        duration = wav_duration(audio_path)
    return audio_path, duration


def avatar_pip(avatar_path: str, size: tuple[int, int]) -> Image.Image:
    w, h = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw_round_rect(draw, (0, 0, w, h), 28, (255, 255, 255, 235), (215, 226, 229, 255), 2)
    img_path = Path(avatar_path) if avatar_path else Path()
    if img_path.exists():
        try:
            with Image.open(img_path) as raw:
                face = fit_cover(raw, (w - 28, h - 72))
            mask = Image.new("L", face.size, 0)
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle((0, 0, face.width, face.height), radius=24, fill=255)
            face_rgba = face.convert("RGBA")
            face_rgba.putalpha(mask)
            canvas.alpha_composite(face_rgba, (14, 14))
        except Exception:
            draw_avatar_illustration(draw, w // 2, 38, min(w, h) / 330)
    else:
        draw_avatar_illustration(draw, w // 2, 38, min(w, h) / 330)
    draw_round_rect(draw, (18, h - 48, w - 18, h - 12), 18, (26, 96, 96, 245))
    label = "日本房產顧問"
    tw, th = text_size(draw, label, font(19, True))
    draw.text(((w - tw) // 2, h - 41), label, font=font(19, True), fill=(255, 255, 255, 255))
    return canvas


def avatar_pip_from_frame(frame_rgb: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw_round_rect(draw, (0, 0, w, h), 28, (255, 255, 255, 238), (33, 185, 174, 255), 3)
    face = fit_cover(frame_rgb, (w - 28, h - 72))
    mask = Image.new("L", face.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, face.width, face.height), radius=24, fill=255)
    face_rgba = face.convert("RGBA")
    face_rgba.putalpha(mask)
    canvas.alpha_composite(face_rgba, (14, 14))
    draw_round_rect(draw, (18, h - 48, w - 18, h - 12), 18, (26, 96, 96, 245))
    label = "動態數字人"
    tw, _ = text_size(draw, label, font(19, True))
    draw.text(((w - tw) // 2, h - 41), label, font=font(19, True), fill=(255, 255, 255, 255))
    return canvas


def resolve_digital_human_video(pack: dict[str, Any], row: dict[str, Any]) -> Path | None:
    if pack.get("force_static_avatar"):
        return None
    candidates = [
        pack.get("source_video_path"),
        pack.get("digital_human_source_video_path"),
        DEFAULT_DIGITAL_HUMAN_VIDEO,
        row.get("source_video_path"),
    ]
    for raw in candidates:
        if not raw:
            continue
        p = Path(str(raw))
        if p.exists() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}:
            return p
    return None


def load_avatar_video_frames(video_path: Path | None, size: tuple[int, int], *, max_frames: int = 360) -> tuple[list[Image.Image], float, str]:
    if not video_path or not video_path.exists():
        return [], 0.0, ""
    cap = cv2.VideoCapture(str(video_path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, math.ceil(total / max_frames)) if total else 1
    frames: list[Image.Image] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(avatar_pip_from_frame(Image.fromarray(rgb), size))
            if len(frames) >= max_frames:
                break
        idx += 1
    cap.release()
    return frames, fps / step if frames else 0.0, str(video_path)


def draw_avatar_illustration(draw: ImageDraw.ImageDraw, cx: int, y: int, scale: float) -> None:
    cy = y + int(130 * scale)
    draw_round_rect(draw, (cx - int(92 * scale), cy + int(95 * scale), cx + int(92 * scale), cy + int(260 * scale)), int(34 * scale), (31, 48, 70, 255))
    draw.polygon(
        [
            (cx - int(44 * scale), cy + int(108 * scale)),
            (cx, cy + int(185 * scale)),
            (cx + int(44 * scale), cy + int(108 * scale)),
        ],
        fill=(242, 246, 248, 255),
    )
    draw.ellipse((cx - int(80 * scale), cy - int(82 * scale), cx + int(80 * scale), cy + int(96 * scale)), fill=(242, 196, 166, 255))
    draw.pieslice((cx - int(88 * scale), cy - int(94 * scale), cx + int(88 * scale), cy + int(52 * scale)), 180, 360, fill=(41, 38, 42, 255))
    draw.ellipse((cx - int(44 * scale), cy - int(8 * scale), cx - int(24 * scale), cy + int(12 * scale)), fill=(42, 38, 40, 255))
    draw.ellipse((cx + int(24 * scale), cy - int(8 * scale), cx + int(44 * scale), cy + int(12 * scale)), fill=(42, 38, 40, 255))
    draw.line((cx - int(22 * scale), cy + int(48 * scale), cx + int(22 * scale), cy + int(48 * scale)), fill=(126, 47, 62, 255), width=max(2, int(5 * scale)))


def frame_canvas_portrait(
    size: tuple[int, int],
    title: str,
    subtitle: str,
    scene: dict[str, Any],
    background: Image.Image,
    avatar: Image.Image,
    t: float,
    duration: float,
    avatar_is_video: bool = False,
) -> Image.Image:
    w, h = size
    img = background.copy().convert("RGBA")
    draw = ImageDraw.Draw(img)
    pad = 34

    badge = "日本房產案件介紹"
    draw_round_rect(draw, (pad, 38, pad + 250, 82), 22, (255, 255, 255, 232))
    draw.text((pad + 18, 47), badge, font=font(22, True), fill=(20, 88, 92, 255))

    title_font = font(35, True)
    yy = 104
    for line in wrap_text(draw, title, title_font, w - pad * 2, 2):
        draw.text((pad, yy), line, font=title_font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 150))
        yy += 44

    role = str(scene.get("role") or "案件重點")
    hint = str(scene.get("copy_hint") or "").strip()
    pill_y = min(228, yy + 8)
    draw_round_rect(draw, (pad, pill_y, w - pad, pill_y + 40), 20, (16, 119, 118, 232))
    draw.text((pad + 18, pill_y + 8), role[:22], font=font(20, True), fill=(255, 255, 255, 255))
    if hint:
        hint_y = pill_y + 52
        for i, line in enumerate(wrap_text(draw, hint, font(20), w - pad * 2, 2)):
            draw.text((pad, hint_y + i * 28), line, font=font(20), fill=(255, 255, 255, 236), stroke_width=1, stroke_fill=(0, 0, 0, 120))

    sub_font = font(26, True)
    sub_left = pad
    sub_right = w - pad
    sub_top = h - 210
    sub_bottom = h - 44
    draw_round_rect(draw, (sub_left, sub_top, sub_right, sub_bottom), 24, (255, 255, 255, 232))
    subtitle_text = subtitle.replace("｜", " ")
    sy = sub_top + 22
    for line in wrap_text(draw, subtitle_text, sub_font, sub_right - sub_left - 34, 3):
        draw.text((sub_left + 17, sy), line, font=sub_font, fill=(18, 42, 54, 255))
        sy += 34

    av_w = 166
    av_h = 222
    pulse = 0.5 + 0.5 * math.sin(t * 7.5)
    bob = int(math.sin(t * 2.6) * 5)
    av = avatar.resize((av_w, av_h), Image.Resampling.LANCZOS)
    ax = w - pad - av_w
    ay = sub_top - av_h - 58 + bob
    ring_pad = int(5 + pulse * 4)
    draw.rounded_rectangle(
        (ax - ring_pad, ay - ring_pad, ax + av_w + ring_pad, ay + av_h + ring_pad),
        radius=28,
        outline=(33, 202, 185, int(135 + pulse * 90)),
        width=3,
    )
    img.alpha_composite(av, (ax, ay))
    voice_badge = "動態數字人口播" if avatar_is_video else "數字人口播"
    draw_round_rect(draw, (ax, ay - 36, ax + av_w, ay - 8), 14, (10, 98, 94, 232))
    tw, _ = text_size(draw, voice_badge, font(15, True))
    draw.text((ax + (av_w - tw) // 2, ay - 32), voice_badge, font=font(15, True), fill=(255, 255, 255, 255))
    if not avatar_is_video:
        mouth_w = int(22 + pulse * 18)
        mouth_h = int(4 + pulse * 10)
        mx = ax + av_w // 2
        my = ay + int(av_h * 0.50)
        draw.rounded_rectangle(
            (mx - mouth_w // 2, my - mouth_h // 2, mx + mouth_w // 2, my + mouth_h // 2),
            radius=max(3, mouth_h // 2),
            fill=(112, 35, 48, 185),
        )
    bar_x = ax + 16
    bar_y = ay + av_h + 10
    for i in range(7):
        amp = int((9 + 18 * (0.5 + 0.5 * math.sin(t * 5.2 + i * 0.8))) * (0.7 + pulse * 0.3))
        draw_round_rect(draw, (bar_x + i * 13, bar_y - amp, bar_x + i * 13 + 7, bar_y), 4, (33, 169, 158, 220))

    pct = max(0.0, min(1.0, t / max(duration, 0.1)))
    draw_round_rect(draw, (pad, h - 24, w - pad, h - 18), 3, (255, 255, 255, 120))
    draw_round_rect(draw, (pad, h - 24, pad + int((w - pad * 2) * pct), h - 18), 3, (33, 169, 158, 255))
    return img.convert("RGB")


def draw_host_talking_overlays(draw: ImageDraw.ImageDraw, size: tuple[int, int], t: float, landscape: bool) -> None:
    w, h = size
    pulse = 0.5 + 0.5 * math.sin(t * 9.5)
    quick = 0.5 + 0.5 * math.sin(t * 17.0)
    if landscape:
        mx, my = int(w * 0.505), int(h * 0.423)
        mouth_w = int(54 + pulse * 28)
        mouth_h = int(5 + quick * 13)
        eye_y = int(h * 0.286)
        eyes = [(int(w * 0.445), eye_y), (int(w * 0.557), eye_y)]
        eye_w, eye_h = 54, 7
    else:
        mx, my = int(w * 0.512), int(h * 0.414)
        mouth_w = int(48 + pulse * 26)
        mouth_h = int(5 + quick * 15)
        eye_y = int(h * 0.292)
        eyes = [(int(w * 0.423), eye_y), (int(w * 0.590), eye_y)]
        eye_w, eye_h = 48, 8
    draw.rounded_rectangle(
        (mx - mouth_w // 2, my - mouth_h // 2, mx + mouth_w // 2, my + mouth_h // 2),
        radius=max(3, mouth_h // 2),
        fill=(112, 35, 48, int(112 + quick * 70)),
    )
    draw.line((mx - mouth_w // 2 + 6, my, mx + mouth_w // 2 - 6, my), fill=(82, 28, 36, 150), width=2)

    blink_phase = t % 3.9
    blink = blink_phase < 0.11 or 2.05 < blink_phase < 2.15
    if blink:
        for ex, ey in eyes:
            draw.rounded_rectangle(
                (ex - eye_w // 2, ey - eye_h // 2, ex + eye_w // 2, ey + eye_h // 2),
                radius=max(2, eye_h // 2),
                fill=(38, 34, 32, 132),
            )
    else:
        glint_alpha = int(78 + pulse * 72)
        for ex, ey in eyes:
            draw.ellipse((ex - 8, ey - 8, ex - 2, ey - 2), fill=(255, 255, 255, glint_alpha))


def frame_canvas_host_spotlight(
    size: tuple[int, int],
    title: str,
    subtitle: str,
    scene: dict[str, Any],
    background: Image.Image,
    t: float,
    duration: float,
    landscape: bool,
) -> Image.Image:
    w, h = size
    img = background.copy().convert("RGBA")
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    if landscape:
        od.rectangle((0, 0, int(w * 0.54), h), fill=(0, 0, 0, 118))
        od.rectangle((0, int(h * 0.68), w, h), fill=(0, 0, 0, 70))
        pad = 46
        title_max = int(w * 0.50)
        panel_bottom = h - 54
    else:
        od.rectangle((0, 0, w, 270), fill=(0, 0, 0, 92))
        od.rectangle((0, int(h * 0.58), w, h), fill=(0, 0, 0, 132))
        pad = 36
        title_max = w - pad * 2
        panel_bottom = h - 70
    img.alpha_composite(overlay)
    draw = ImageDraw.Draw(img)
    draw_host_talking_overlays(draw, size, t, landscape)

    stage = str(scene.get("_host_stage") or "intro")
    badge = "日本房產顧問帶看" if stage == "intro" else "預約看屋提醒"
    draw_round_rect(draw, (pad, pad, pad + (260 if landscape else 250), pad + 44), 22, (255, 255, 255, 232))
    draw.text((pad + 18, pad + 9), badge, font=font(22, True), fill=(18, 86, 90, 255))

    headline = "先帶你看這個案件" if stage == "intro" else "想看完整資料，直接約顧問"
    h_font = font(42 if landscape else 38, True)
    yy = pad + 68
    for line in wrap_text(draw, headline, h_font, title_max, 2):
        draw.text((pad, yy), line, font=h_font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 140))
        yy += 50 if landscape else 46

    title_font = font(30 if landscape else 28, True)
    for line in wrap_text(draw, title, title_font, title_max, 2):
        draw.text((pad, yy + 10), line, font=title_font, fill=(238, 252, 250, 255), stroke_width=1, stroke_fill=(0, 0, 0, 130))
        yy += 38 if landscape else 34

    sub_font = font(27 if landscape else 28, True)
    if landscape:
        box = (pad, panel_bottom - 116, int(w * 0.73), panel_bottom)
    else:
        box = (pad, panel_bottom - 160, w - pad, panel_bottom)
    draw_round_rect(draw, box, 24, (255, 255, 255, 232))
    sy = box[1] + 20
    cta = subtitle if stage == "intro" else "想看完整地址、費用明細、更多室內外照片與預約賞屋，直接私訊顧問。"
    for line in wrap_text(draw, cta.replace("｜", " "), sub_font, box[2] - box[0] - 36, 3):
        draw.text((box[0] + 18, sy), line, font=sub_font, fill=(18, 42, 54, 255))
        sy += 36

    pulse = 0.5 + 0.5 * math.sin(t * 7.5)
    wave_y = box[1] - 24
    wave_x = box[0] + 12
    for i in range(10):
        amp = int((10 + 22 * (0.5 + 0.5 * math.sin(t * 5.4 + i * 0.65))) * (0.8 + pulse * 0.2))
        draw_round_rect(draw, (wave_x + i * 15, wave_y - amp, wave_x + i * 15 + 8, wave_y), 4, (33, 202, 185, 230))

    pct = max(0.0, min(1.0, t / max(duration, 0.1)))
    draw_round_rect(draw, (pad, h - 16, w - pad, h - 10), 3, (255, 255, 255, 120))
    draw_round_rect(draw, (pad, h - 16, pad + int((w - pad * 2) * pct), h - 10), 3, (33, 169, 158, 255))
    return img.convert("RGB")


def frame_canvas(
    size: tuple[int, int],
    title: str,
    subtitle: str,
    scene: dict[str, Any],
    background: Image.Image,
    avatar: Image.Image,
    t: float,
    duration: float,
    landscape: bool,
    avatar_is_video: bool = False,
) -> Image.Image:
    if scene.get("_host_stage"):
        return frame_canvas_host_spotlight(
            size=size,
            title=title,
            subtitle=subtitle,
            scene=scene,
            background=background,
            t=t,
            duration=duration,
            landscape=landscape,
        )
    if not landscape:
        return frame_canvas_portrait(
            size=size,
            title=title,
            subtitle=subtitle,
            scene=scene,
            background=background,
            avatar=avatar,
            t=t,
            duration=duration,
            avatar_is_video=avatar_is_video,
        )
    w, h = size
    bg = background.copy()
    info_mode = str(scene.get("display_mode") or "") == "contain_full_image"
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    if info_mode:
        od.rectangle((0, 0, w, 250 if landscape else 276), fill=(0, 0, 0, 24))
        od.rectangle((0, h - (168 if landscape else 206), w, h), fill=(0, 0, 0, 32))
    else:
        od.rectangle((0, int(h * 0.54), w, h), fill=(0, 0, 0, 108))
        od.rectangle((0, 0, w, int(h * 0.24)), fill=(0, 0, 0, 58))
    img = bg.convert("RGBA")
    img.alpha_composite(overlay)
    draw = ImageDraw.Draw(img)

    pad = 42 if landscape else 36
    badge = "日本房產案件介紹"
    draw_round_rect(draw, (pad, pad, pad + (270 if landscape else 250), pad + 44), 22, (255, 255, 255, 230))
    draw.text((pad + 20, pad + 9), badge, font=font(22, True), fill=(20, 88, 92, 255))

    title_font = font(42 if landscape else 39, True)
    title_max = int(w * (0.68 if landscape else 0.86))
    yy = pad + 65
    for line in wrap_text(draw, title, title_font, title_max, 2):
        draw.text((pad, yy), line, font=title_font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 130))
        yy += 52 if landscape else 50

    role = str(scene.get("role") or "案件畫面")
    hint = str(scene.get("copy_hint") or "").strip()
    pill_y = yy + 6
    draw_round_rect(draw, (pad, pill_y, min(w - pad, pad + 360), pill_y + 42), 21, (16, 119, 118, 230))
    draw.text((pad + 18, pill_y + 8), role[:18], font=font(21, True), fill=(255, 255, 255, 255))
    if hint:
        for i, line in enumerate(wrap_text(draw, hint, font(22), int(w * (0.58 if landscape else 0.78)), 2)):
            fill = (26, 54, 63, 245) if info_mode else (255, 255, 255, 235)
            stroke = (255, 255, 255, 150) if info_mode else (0, 0, 0, 110)
            draw.text((pad, pill_y + 58 + i * 30), line, font=font(22), fill=fill, stroke_width=1, stroke_fill=stroke)

    sub_font = font(26 if landscape else 28, True)
    sub_box_h = 116 if landscape else 152
    sub_left = pad
    sub_right = w - pad - (260 if landscape else 0)
    sub_top = h - sub_box_h - 38
    draw_round_rect(draw, (sub_left, sub_top, sub_right, h - 28), 24, (255, 255, 255, 226))
    subtitle_text = subtitle.replace("｜", " ")
    sy = sub_top + 20
    for line in wrap_text(draw, subtitle_text, sub_font, sub_right - sub_left - 36, 3):
        draw.text((sub_left + 18, sy), line, font=sub_font, fill=(18, 42, 54, 255))
        sy += 36

    av_w = 220 if landscape else 204
    av_h = 292 if landscape else 276
    pulse = 0.5 + 0.5 * math.sin(t * 7.5)
    bob = int(math.sin(t * 2.6) * (5 if landscape else 7))
    av = avatar.resize((av_w, av_h), Image.Resampling.LANCZOS)
    ax = w - av_w - pad
    ay = h - av_h - (42 if landscape else 206) + bob
    ring_pad = int(6 + pulse * 5)
    draw.rounded_rectangle(
        (ax - ring_pad, ay - ring_pad, ax + av_w + ring_pad, ay + av_h + ring_pad),
        radius=30,
        outline=(33, 202, 185, int(135 + pulse * 90)),
        width=3,
    )
    img.alpha_composite(av, (ax, ay))
    voice_badge = "動態數字人口播" if avatar_is_video else "數字人口播"
    draw_round_rect(draw, (ax, ay - 42, ax + av_w, ay - 10), 16, (10, 98, 94, 232))
    tw, _ = text_size(draw, voice_badge, font(17, True))
    draw.text((ax + (av_w - tw) // 2, ay - 37), voice_badge, font=font(17, True), fill=(255, 255, 255, 255))
    if not avatar_is_video:
        mouth_w = int(30 + pulse * 24)
        mouth_h = int(5 + pulse * 13)
        mx = ax + av_w // 2
        my = ay + int(av_h * 0.50)
        draw.rounded_rectangle(
            (mx - mouth_w // 2, my - mouth_h // 2, mx + mouth_w // 2, my + mouth_h // 2),
            radius=max(3, mouth_h // 2),
            fill=(112, 35, 48, 185),
        )
    bar_x = ax + 18
    bar_y = ay + av_h + 12
    for i in range(8):
        amp = int((10 + 22 * (0.5 + 0.5 * math.sin(t * 5.2 + i * 0.8))) * (0.7 + pulse * 0.3))
        draw_round_rect(
            draw,
            (bar_x + i * 14, bar_y - amp, bar_x + i * 14 + 8, bar_y),
            4,
            (33, 169, 158, 220),
        )

    pct = max(0.0, min(1.0, t / max(duration, 0.1)))
    draw_round_rect(draw, (pad, h - 12, w - pad, h - 6), 3, (255, 255, 255, 110))
    draw_round_rect(draw, (pad, h - 12, pad + int((w - pad * 2) * pct), h - 6), 3, (33, 169, 158, 255))
    return img.convert("RGB")


def write_video(
    out_path: Path,
    size: tuple[int, int],
    title: str,
    subtitles: list[str],
    assets: list[dict[str, Any]],
    avatar: Image.Image,
    avatar_video_frames: list[Image.Image] | None,
    avatar_video_fps: float,
    duration: float,
    landscape: bool,
    avatar_source_path: str = "",
) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    silent_path = out_path.with_name(out_path.stem + ".silent.mp4")
    writer = cv2.VideoWriter(str(silent_path), fourcc, FPS, size)
    total_frames = max(FPS * 8, int(duration * FPS))
    host_background = make_host_spotlight_background(avatar_source_path, avatar, size) if str(avatar_source_path or "").strip() else None
    host_intro_seconds = min(4.8, max(3.2, duration * 0.07)) if host_background else 0.0
    host_outro_seconds = min(5.2, max(3.4, duration * 0.075)) if host_background else 0.0
    content_duration = max(1.0, duration - host_intro_seconds - host_outro_seconds)
    scene_seconds = max(3.2, content_duration / max(1, len(assets)))
    backgrounds: list[Image.Image] = []
    for scene in assets:
        try:
            with Image.open(Path(scene["local_path"])) as raw:
                if not landscape:
                    backgrounds.append(make_portrait_object_background(raw, size, scene))
                elif str(scene.get("display_mode") or "") == "contain_full_image":
                    backgrounds.append(make_contained_background(raw, size, landscape))
                else:
                    backgrounds.append(fit_cover(raw, size))
        except Exception:
            backgrounds.append(placeholder_image(title, size=size))
    for frame_idx in range(total_frames):
        t = frame_idx / FPS
        if host_background and t < host_intro_seconds:
            scene_index = 0
            scene = {"_host_stage": "intro", "role": "顧問開場"}
            scene_progress = t / max(host_intro_seconds, 0.1)
            background = apply_camera_motion(host_background, size, scene_progress, 200, landscape=landscape)
            subtitle = subtitles[0] if subtitles else title
        elif host_background and t >= duration - host_outro_seconds:
            scene_index = max(0, len(assets) - 1)
            scene = {"_host_stage": "outro", "role": "預約看屋"}
            scene_progress = (t - (duration - host_outro_seconds)) / max(host_outro_seconds, 0.1)
            background = apply_camera_motion(host_background, size, scene_progress, 201, landscape=landscape)
            subtitle = subtitles[-1] if subtitles else title
        else:
            content_t = max(0.0, t - host_intro_seconds)
            scene_index = min(len(assets) - 1, int(content_t / scene_seconds))
            scene = assets[scene_index]
            scene_start = scene_index * scene_seconds
            scene_progress = (content_t - scene_start) / max(scene_seconds, 0.1)
            background = apply_camera_motion(
                backgrounds[scene_index],
                size,
                scene_progress,
                scene_index,
                landscape=landscape,
                contained=str(scene.get("display_mode") or "") == "contain_full_image",
            )
            subtitle = subtitles[min(len(subtitles) - 1, scene_index)] if subtitles else title
        avatar_frame = avatar
        avatar_is_video = bool(avatar_video_frames)
        if avatar_video_frames:
            avatar_frame = avatar_video_frames[int(t * max(1.0, avatar_video_fps)) % len(avatar_video_frames)]
        frame = frame_canvas(
            size=size,
            title=title,
            subtitle=subtitle,
            scene=scene,
            background=background,
            avatar=avatar_frame,
            t=t,
            duration=duration,
            landscape=landscape,
            avatar_is_video=avatar_is_video,
        )
        writer.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))
    writer.release()
    if not silent_path.is_file() or silent_path.stat().st_size <= 0:
        raise RuntimeError(f"video writer did not create a usable file: {silent_path}")

    # Browser playback is unreliable for OpenCV's default mp4v stream on Chrome.
    # Transcode the silent render to H.264/yuv420p before audio muxing.
    encoded_path = out_path.with_name(out_path.stem + ".h264.mp4")
    encoded_path.unlink(missing_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(silent_path),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(encoded_path),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if not encoded_path.is_file() or encoded_path.stat().st_size <= 0:
            raise RuntimeError(f"H.264 transcode produced an empty file: {encoded_path}")
        out_path.unlink(missing_ok=True)
        shutil.move(str(encoded_path), str(out_path))
    finally:
        silent_path.unlink(missing_ok=True)
        encoded_path.unlink(missing_ok=True)


def mux_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = out_path.with_name(out_path.stem + ".mux.mp4")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(tmp),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out_path.unlink(missing_ok=True)
    shutil.move(str(tmp), str(out_path))


def thumbnail_from_video(video_path: Path, thumb_path: Path) -> None:
    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, min(total - 1, max(0, total // 5)))
    ok, frame = cap.read()
    cap.release()
    if ok:
        cv2.imwrite(str(thumb_path), frame)


def update_task_db(
    conn: sqlite3.Connection,
    task_id: str,
    final_video: Path,
    audio_path: Path,
    duration: float,
    *,
    stage: str = "本地閉環完成：口播、案件影片與素材包已保存",
    summary: str = "已完成，可播放、下載並查詢素材。",
    audio_task_id: str | None = None,
    video_task_id: str | None = None,
) -> None:
    ts = time.time()
    audio_id = audio_task_id or f"local_tts_{int(ts)}"
    video_id = video_task_id or f"local_render_{int(ts)}"
    conn.execute(
        """
        UPDATE workspace_tasks
        SET status = 'completed',
            current_stage = ?,
            summary = ?,
            final_video_path = ?,
            extracted_audio_path = ?,
            cloned_audio_path = ?,
            video_duration_seconds = ?,
            cloned_audio_duration_seconds = ?,
            audio_task_id = ?,
            video_task_id = ?,
            error_message = '',
            started_at = COALESCE(started_at, ?),
            finished_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            stage,
            summary,
            str(final_video),
            str(audio_path),
            str(audio_path),
            float(duration),
            float(duration),
            audio_id,
            video_id,
            ts,
            ts,
            ts,
            task_id,
        ),
    )
    events = [
        ("info", "local_started", "開始本地完成 social-case 影片閉環"),
        ("info", "generated_audio", f"口播音訊已保存：{audio_path}"),
        ("info", "rendered_video", f"案件影片已保存：{final_video}"),
        ("info", "completed", "任務已標記完成，工作台可查詢影片與素材"),
    ]
    for level, stage, msg in events:
        conn.execute(
            "INSERT INTO workspace_task_events (task_id, level, stage, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, level, stage, msg, ts),
        )
    conn.commit()


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"material": 0, "queued": 0, "completed": 0, "preview": 0, "failed": 0, "fallback": 0}
    for item in items:
        status = str(item.get("status") or "").lower()
        if status in counts:
            counts[status] += 1
        if status not in {"failed", ""}:
            counts["material"] += 1
        if item.get("fallback"):
            counts["fallback"] += 1
    return counts


def update_batch_record(task_id: str, source_item_id: int, title: str, work_dir: Path, outputs: dict[str, str]) -> dict[str, Any]:
    rows = load_json(BATCH_RECORDS_PATH, [])
    if not isinstance(rows, list):
        rows = []
    updated_record: dict[str, Any] = {}
    result = {
        "task_id": task_id,
        "source_item_id": source_item_id,
        "title": title,
        "completed_at": now_iso(),
        "work_dir": str(work_dir),
        "final_video_path": outputs["final_video"],
        "portrait_video_path": outputs["portrait_video"],
        "landscape_video_path": outputs["landscape_video"],
        "cloned_audio_path": outputs.get("cloned_audio", ""),
        "thumbnail_path": outputs["thumbnail"],
        "script_path": outputs["script"],
        "assets_manifest_path": outputs["manifest"],
        "sales_brief_path": outputs.get("sales_brief_md", ""),
        "sales_brief_json_path": outputs.get("sales_brief_json", ""),
        "final_video_url": f"/api/social-case-workbench/tg-files/{task_id}/final_video",
        "portrait_video_url": f"/api/social-case-workbench/tg-files/{task_id}/portrait_video",
        "landscape_video_url": f"/api/social-case-workbench/tg-files/{task_id}/landscape_video",
        "thumbnail_url": f"/api/social-case-workbench/tg-files/{task_id}/thumbnail",
        "assets_manifest_url": f"/api/social-case-workbench/tg-files/{task_id}/assets_manifest",
        "script_url": f"/api/social-case-workbench/tg-files/{task_id}/script",
        "sales_brief_url": f"/api/social-case-workbench/tg-files/{task_id}/sales_brief",
        "sales_brief_json_url": f"/api/social-case-workbench/tg-files/{task_id}/sales_brief_json",
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        items = row.get("items") if isinstance(row.get("items"), list) else []
        matched = False
        for item in items:
            if not isinstance(item, dict):
                continue
            same_task = str(item.get("task_id") or "") == task_id
            same_source = source_item_id and int(item.get("source_item_id") or 0) == source_item_id
            if same_task or same_source:
                item["task_id"] = task_id
                item["status"] = "completed"
                item["result"] = result
                item["completed_at"] = result["completed_at"]
                matched = True
        if matched:
            row["items"] = items
            row.setdefault("task_ids", [])
            if task_id and task_id not in row["task_ids"]:
                row["task_ids"].append(task_id)
            counts = status_counts([x for x in items if isinstance(x, dict)])
            row["counts"] = {**(row.get("counts") if isinstance(row.get("counts"), dict) else {}), **counts}
            total = len(items)
            done = counts["completed"]
            row["status"] = "completed" if total and done >= total else "partial_completed"
            row["summary"] = f"已完成 {done}/{total} 支影片；成功結果已保存，可查詢影片與素材。"
            row["updated_at"] = now_iso()
            row.setdefault("results", [])
            existing_results = [x for x in row["results"] if isinstance(x, dict) and x.get("task_id") != task_id]
            row["results"] = [result, *existing_results][:30]
            updated_record = row
    save_json(BATCH_RECORDS_PATH, rows)
    return updated_record


def complete_task(
    task_id: str | None,
    audio_override_path: Path | None = None,
    script_override_path: Path | None = None,
) -> dict[str, Any]:
    with sqlite3.connect(str(TG_DB), timeout=8.0) as conn:
        row = task_row(conn, task_id)
    real_task_id = str(row["id"])
    work_dir = Path(row.get("work_dir") or TG_ROOT / "data" / "jobs" / real_task_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    pack = repair_obj(load_json(work_dir / "social_asset_pack.json", {}))
    if not isinstance(pack, dict):
        pack = {}
    source_id = source_item_id_from_task(row, pack)
    item = fetch_workbench_item(source_id)
    pack = enrich_pack_from_item(pack, item)
    title = str(repair_text(pack.get("title") or pack.get("case_title") or row.get("source") or real_task_id))
    pack["case_title"] = title
    pack["source_item_id"] = source_id
    script_override = ""
    if script_override_path and script_override_path.exists():
        script_override = script_override_path.read_text(encoding="utf-8", errors="ignore")
    script = clean_script(str(script_override or pack.get("voiceover_script") or row.get("script_text") or ""), title)
    (work_dir / "case_script.txt").write_text(script, encoding="utf-8")
    (work_dir / "voiceover_script.txt").write_text(script, encoding="utf-8")
    (work_dir / "case_script_for_tts.txt").write_text(script, encoding="utf-8")
    pack["voiceover_script"] = script
    pack["digital_human_intro_script"] = script
    pack["digital_human_video_script"] = script

    segments = build_sales_segments(pack, work_dir, max_images=16)
    segments = add_traffic_card_segment(segments, work_dir, title, pack)
    pack["selected_segments"] = segments
    pack["sales_image_plan"] = segments
    pack["image_urls"] = [str(x.get("url") or "") for x in segments if str(x.get("url") or "").startswith(("http://", "https://"))]
    save_json(work_dir / "social_asset_pack.json", pack)

    assets = download_case_images(segments, work_dir, title)
    subtitles = sales_video_subtitles(title, pack, segments, script)
    target_seconds = int(row.get("target_duration_seconds") or pack.get("duration_seconds") or 45)
    audio_mode = "local_tts"
    source_audio_note = ""
    if audio_override_path and audio_override_path.exists():
        suffix = audio_override_path.suffix.lower() or ".flac"
        audio_path = work_dir / f"cloned_voice{suffix}"
        if audio_override_path.resolve() != audio_path.resolve():
            shutil.copy2(audio_override_path, audio_path)
        audio_seconds = media_duration(audio_path)
        audio_mode = "existing_cloned_voice"
        source_audio_note = str(audio_override_path)
    else:
        audio_path, audio_seconds = render_tts(script, work_dir, target_seconds)
    render_seconds = max(18.0, min(75.0, audio_seconds or target_seconds or 40.0))

    avatar_source_path = str(pack.get("avatar_image_path") or row.get("avatar_image_path") or "")
    avatar = avatar_pip(avatar_source_path, (240, 320))
    digital_human_video = resolve_digital_human_video(pack, row)
    avatar_video_frames, avatar_video_fps, avatar_video_source = load_avatar_video_frames(digital_human_video, (240, 320))
    pack["digital_human_motion"] = {
        "mode": "source_video_loop" if avatar_video_frames else "static_avatar_animation",
        "source_video_path": avatar_video_source,
        "frame_count": len(avatar_video_frames),
        "fps": avatar_video_fps,
    }
    save_json(work_dir / "social_asset_pack.json", pack)
    portrait_silent = work_dir / "case_intro_with_digital_human_portrait_9x16.video.mp4"
    landscape_silent = work_dir / "case_intro_with_digital_human_landscape_16x9.video.mp4"
    portrait = work_dir / "case_intro_with_digital_human_portrait_9x16.mp4"
    landscape = work_dir / "case_intro_with_digital_human_landscape_16x9.mp4"
    final_video = work_dir / "digital_human.mp4"
    thumb = work_dir / "thumbnail.jpg"
    manifest = work_dir / "materials_manifest.json"
    sales_brief_json = work_dir / "sales_brief.json"
    sales_brief_md = work_dir / "sales_brief.md"

    write_video(portrait_silent, PORTRAIT_SIZE, title, subtitles, assets, avatar, avatar_video_frames, avatar_video_fps, render_seconds, landscape=False, avatar_source_path=avatar_source_path)
    write_video(landscape_silent, LANDSCAPE_SIZE, title, subtitles, assets, avatar, avatar_video_frames, avatar_video_fps, render_seconds, landscape=True, avatar_source_path=avatar_source_path)
    mux_audio(portrait_silent, audio_path, portrait)
    mux_audio(landscape_silent, audio_path, landscape)
    shutil.copy2(portrait, final_video)
    thumbnail_from_video(portrait, thumb)
    for tmp in (portrait_silent, landscape_silent):
        tmp.unlink(missing_ok=True)

    outputs = {
        "final_video": str(final_video),
        "portrait_video": str(portrait),
        "landscape_video": str(landscape),
        "cloned_audio": str(audio_path),
        "thumbnail": str(thumb),
        "script": str(work_dir / "case_script.txt"),
        "manifest": str(manifest),
        "sales_brief_json": str(sales_brief_json),
        "sales_brief_md": str(sales_brief_md),
    }
    sales_brief = build_sales_brief(title, source_id, pack, assets, subtitles)
    save_json(sales_brief_json, sales_brief)
    sales_brief_md.write_text(sales_brief_markdown(sales_brief), encoding="utf-8")
    manifest_data = {
        "task_id": real_task_id,
        "source_item_id": source_id,
        "case_title": title,
        "created_at": now_iso(),
        "duration_seconds": render_seconds,
        "audio_mode": audio_mode,
        "digital_human_motion": pack.get("digital_human_motion") or {},
        "source_audio_path": source_audio_note,
        "work_dir": str(work_dir),
        "script": script,
        "outputs": outputs,
        "query_urls": {
            "final_video": f"/api/social-case-workbench/tg-files/{real_task_id}/final_video",
            "portrait_video": f"/api/social-case-workbench/tg-files/{real_task_id}/portrait_video",
            "landscape_video": f"/api/social-case-workbench/tg-files/{real_task_id}/landscape_video",
            "thumbnail": f"/api/social-case-workbench/tg-files/{real_task_id}/thumbnail",
            "assets_manifest": f"/api/social-case-workbench/tg-files/{real_task_id}/assets_manifest",
            "script": f"/api/social-case-workbench/tg-files/{real_task_id}/script",
            "sales_brief": f"/api/social-case-workbench/tg-files/{real_task_id}/sales_brief",
            "sales_brief_json": f"/api/social-case-workbench/tg-files/{real_task_id}/sales_brief_json",
        },
        "assets": assets,
        "sales_brief": sales_brief,
        "social_asset_pack": pack,
    }
    save_json(manifest, manifest_data)
    save_json(work_dir / "success_result.json", manifest_data)

    with sqlite3.connect(str(TG_DB), timeout=8.0) as conn:
        update_task_db(
            conn,
            real_task_id,
            final_video,
            audio_path,
            render_seconds,
            stage="動態數字人、克隆聲音、完整圖文素材與銷售亮點表已完成" if audio_mode == "existing_cloned_voice" else "本地閉環完成：動態數字人口播、案件影片、素材包與銷售亮點表已保存",
            summary="已套用克隆聲音與來源影片動態數字人，完成口播影片、完整室內外/交通素材與顧問銷售亮點表。" if audio_mode == "existing_cloned_voice" else "已完成，可播放、下載並查詢動態數字人口播影片、素材與銷售亮點表。",
            audio_task_id=f"existing_clone_reuse_{int(time.time())}" if audio_mode == "existing_cloned_voice" else None,
            video_task_id=f"local_lively_render_{int(time.time())}" if audio_mode == "existing_cloned_voice" else None,
        )

    batch_record = update_batch_record(real_task_id, source_id, title, work_dir, outputs)
    return {
        "task_id": real_task_id,
        "source_item_id": source_id,
        "title": title,
        "duration_seconds": render_seconds,
        "audio_mode": audio_mode,
        "outputs": outputs,
        "batch_id": batch_record.get("batch_id") if batch_record else "",
        "urls": manifest_data["query_urls"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Complete one queued social-case task with local retained video/assets.")
    parser.add_argument("--task-id", default="", help="workspace_tasks.id to complete; defaults to newest queued social-case task")
    parser.add_argument("--audio-path", default="", help="optional existing cloned voice audio to use instead of local TTS")
    parser.add_argument("--script-path", default="", help="optional script text file matching the supplied audio")
    args = parser.parse_args()
    audio_path = Path(args.audio_path).expanduser() if str(args.audio_path or "").strip() else None
    script_path = Path(args.script_path).expanduser() if str(args.script_path or "").strip() else None
    result = complete_task(args.task_id.strip() or None, audio_override_path=audio_path, script_override_path=script_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
