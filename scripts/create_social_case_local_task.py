from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4


TG_ROOT = Path(r"D:\digital_human_tg_bot")
TG_DB = TG_ROOT / "data" / "workbench.db"
JOBS_DIR = TG_ROOT / "data" / "jobs"
DEFAULT_TEMPLATE_DIR = JOBS_DIR / "case_20260525_192758_de5fc78f"
DEFAULT_AUDIO = DEFAULT_TEMPLATE_DIR / "local_preview_voice.m4a"
DEFAULT_VOICE_VIDEO = TG_ROOT / "data" / "voice_sources" / "wechat_voice_20260525_122432_151.mp4"
DEFAULT_AVATAR = Path("static/uploads/social-case-workbench/avatar_20260524_090249_0cd946f6.jpg")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def first_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def copy_template_assets(template_dir: Path, work_dir: Path, source_item_id: int) -> dict:
    pack = load_json(template_dir / "social_asset_pack.json", {})
    if not isinstance(pack, dict):
        pack = {}
    pack["source_item_id"] = int(source_item_id or pack.get("source_item_id") or 0)
    pack.setdefault("duration_seconds", 50)
    pack.setdefault("output_formats", ["portrait_9x16", "landscape_16x9"])
    pack.setdefault("segment_mode", "core")
    pack["avatar_image_path"] = str(DEFAULT_AVATAR.resolve()) if DEFAULT_AVATAR.exists() else str(pack.get("avatar_image_path") or "")
    if DEFAULT_VOICE_VIDEO.exists():
        pack["digital_human_source_video_path"] = str(DEFAULT_VOICE_VIDEO)
        pack["source_video_path"] = str(DEFAULT_VOICE_VIDEO)

    image_src_dir = template_dir / "case_images"
    image_dst_dir = work_dir / "case_images"
    image_paths: list[Path] = []
    if image_src_dir.exists():
        image_dst_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(image_src_dir.glob("case_*.jpg"))[:12]:
            dst = image_dst_dir / src.name
            shutil.copy2(src, dst)
            image_paths.append(dst)

    old_segments = pack.get("selected_segments")
    if not isinstance(old_segments, list) or not old_segments:
        old_segments = load_json(template_dir / "selected_segments.json", [])
    segments: list[dict] = []
    for idx, image_path in enumerate(image_paths, start=1):
        old = old_segments[idx - 1] if idx - 1 < len(old_segments) and isinstance(old_segments[idx - 1], dict) else {}
        segments.append(
            {
                **old,
                "number": idx,
                "url": str(image_path),
                "role": first_text(old.get("role"), "property scene"),
                "copy_hint": first_text(old.get("copy_hint"), pack.get("case_title"), "Case visual"),
                "visual_type": first_text(old.get("visual_type"), "case_image"),
            }
        )
    if segments:
        pack["selected_segments"] = segments
        pack["sales_image_plan"] = segments
        pack["image_urls"] = [str(x["url"]) for x in segments]
        pack["selected_image_urls"] = [str(x["url"]) for x in segments]

    script = first_text(
        pack.get("voiceover_script"),
        pack.get("digital_human_intro_script"),
        pack.get("case_intro_script"),
        "Hello, this is a local closed-loop preview for the selected Japan property case.",
    )
    pack["voiceover_script"] = script
    pack["digital_human_intro_script"] = script
    pack["digital_human_video_script"] = script
    save_json(work_dir / "social_asset_pack.json", pack)
    save_json(work_dir / "selected_segments.json", pack.get("selected_segments") or [])
    save_json(work_dir / "selected_image_urls.json", pack.get("selected_image_urls") or pack.get("image_urls") or [])
    save_json(work_dir / "video_layout.json", pack.get("video_layout") or {"output_formats": pack.get("output_formats")})
    (work_dir / "voiceover_script.txt").write_text(script, encoding="utf-8")
    (work_dir / "subtitles.txt").write_text(str(pack.get("subtitles") or ""), encoding="utf-8")
    return pack


def insert_task(task_id: str, work_dir: Path, pack: dict, source_item_id: int) -> None:
    if not TG_DB.exists():
        raise SystemExit(f"workbench db not found: {TG_DB}")
    now = time.time()
    title = first_text(pack.get("case_title"), pack.get("title"), f"social case {source_item_id}")
    script = first_text(pack.get("voiceover_script"), pack.get("digital_human_intro_script"))
    with sqlite3.connect(str(TG_DB), timeout=12.0) as conn:
        conn.execute(
            """
            INSERT INTO workspace_tasks (
                id, submitter_chat_id, submitter_label, source, source_video_path,
                avatar_image_path, extracted_audio_path, cloned_audio_path, final_video_path,
                work_dir, script_text, target_duration_seconds, cloned_audio_duration_seconds,
                video_duration_seconds, audio_task_id, video_task_id, status, current_stage,
                summary, error_message, is_default_assets, created_at, started_at, finished_at, updated_at
            ) VALUES (
                ?, 0, ?, ?, ?, ?, '', '', '', ?, ?, ?, NULL, NULL, NULL, NULL,
                'queued', 'local_preview_queued', ?, '', 0, ?, NULL, NULL, ?
            )
            """,
            (
                task_id,
                "Codex local closed-loop preview",
                f"social-case:{int(source_item_id)}",
                str(DEFAULT_VOICE_VIDEO) if DEFAULT_VOICE_VIDEO.exists() else "",
                str(DEFAULT_AVATAR.resolve()) if DEFAULT_AVATAR.exists() else "",
                str(work_dir),
                script,
                int(pack.get("duration_seconds") or 50),
                f"Local closed-loop preview queued: {title}",
                now,
                now,
            ),
        )
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a clean local social-case video task without calling external engines.")
    parser.add_argument("--source-item-id", type=int, default=97587)
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    args = parser.parse_args()

    template_dir = Path(args.template_dir)
    if not template_dir.exists():
        raise SystemExit(f"template dir not found: {template_dir}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    task_id = f"case_{stamp}_{uuid4().hex[:8]}"
    work_dir = JOBS_DIR / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    pack = copy_template_assets(template_dir, work_dir, int(args.source_item_id))
    insert_task(task_id, work_dir, pack, int(args.source_item_id))
    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task_id,
                "source_item_id": int(args.source_item_id),
                "work_dir": str(work_dir),
                "audio_path": str(DEFAULT_AUDIO) if DEFAULT_AUDIO.exists() else "",
                "title": first_text(pack.get("case_title"), pack.get("title")),
                "image_count": len(pack.get("selected_segments") or []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
