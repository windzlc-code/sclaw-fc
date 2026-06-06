from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2

ROOT = Path(__file__).resolve().parents[1]
TG_ROOT = Path(r"D:\digital_human_tg_bot")
TG_DB = TG_ROOT / "data" / "workbench.db"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(TG_ROOT / "src"))

from complete_social_case_local import thumbnail_from_video  # noqa: E402
from digital_human_tg_bot.config import load_config  # noqa: E402
from digital_human_tg_bot.workflow import DigitalHumanWorkflowRunner, WorkflowRequest  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def task_row(task_id: str) -> dict[str, Any]:
    with sqlite3.connect(str(TG_DB), timeout=8.0) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM workspace_tasks WHERE id = ? LIMIT 1", (task_id,)).fetchone()
    if not row:
        raise SystemExit(f"Task not found: {task_id}")
    return dict(row)


def first_fact(pack: dict[str, Any], *labels: str) -> str:
    text_blobs = [
        str(pack.get("case_intro_script") or ""),
        str(pack.get("video_script") or ""),
        str(pack.get("image_post_copy") or ""),
    ]
    facts = pack.get("facts") if isinstance(pack.get("facts"), dict) else {}
    for label in labels:
        value = facts.get(label)
        if value:
            return str(value)
    for blob in text_blobs:
        for label in labels:
            marker = f"{label}："
            if marker in blob:
                return blob.split(marker, 1)[1].split("、", 1)[0].split("\n", 1)[0].strip(" 。")
    return ""


def lively_script(pack: dict[str, Any], fallback: str) -> str:
    title = str(pack.get("case_title") or "").strip() or "這個日本房產案件"
    title_short = title.replace("[SUUMO] ", "").replace("|新建公寓和公寓房產信息", "").strip()
    price = first_fact(pack, "價格", "總價", "售價") or "價格條件清楚"
    layout = first_fact(pack, "格局", "間取り") or "格局實用"
    area = first_fact(pack, "面積", "專有面積") or ""
    traffic = first_fact(pack, "交通", "車站", "駅") or "交通位置有亮點"
    core = "、".join([x for x in [price, layout, area] if x]) or "價格、格局和生活機能都值得看"
    return (
        f"嗨，今天帶你快速看這間 {title_short}。"
        f"先抓重點：{core}，而且{traffic}。"
        "第一眼看外觀和社區質感，這種畫面很適合先建立客戶信任。"
        "接著看室內，重點放在採光、動線和每個空間實際怎麼用；如果是自住，生活感要夠清楚，如果是置產，也要好出租、好比較。"
        "最後幫你整理判斷：這案適合想找日本市區生活圈、又希望總價和格局一眼看懂的客戶。"
        "想拿完整地址、管理費、更多照片和預約賞屋時間，直接私訊我「日本案件」，我把完整資料整理給你。"
    ) if title_short else (fallback or "")


def backup_outputs(work_dir: Path) -> Path:
    version_dir = work_dir / "versions" / datetime.now().strftime("%Y%m%d_%H%M%S_before_clone_lively")
    version_dir.mkdir(parents=True, exist_ok=True)
    names = [
        "digital_human.mp4",
        "case_intro_with_digital_human_portrait_9x16.mp4",
        "case_intro_with_digital_human_landscape_16x9.mp4",
        "cloned_voice.flac",
        "narration.wav",
        "thumbnail.jpg",
        "materials_manifest.json",
        "success_result.json",
        "case_script.txt",
        "voiceover_script.txt",
    ]
    for name in names:
        src = work_dir / name
        if src.exists():
            shutil.copy2(src, version_dir / name)
    return version_dir


def video_duration_seconds(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return float(frames / fps) if fps else 0.0


def source_item_id(row: dict[str, Any], pack: dict[str, Any]) -> int:
    try:
        return int(pack.get("source_item_id") or 0)
    except Exception:
        pass
    raw = str(row.get("source") or "")
    if raw.startswith("social-case:"):
        try:
            return int(raw.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


def patch_batch_records(task_id: str, source_id: int, work_dir: Path, manifest: dict[str, Any]) -> None:
    path = ROOT / "data" / "social_case_batch_records.json"
    rows = load_json(path, [])
    if not isinstance(rows, list):
        return
    for record in rows:
        if not isinstance(record, dict):
            continue
        changed = False
        for item in record.get("items") or []:
            if not isinstance(item, dict):
                continue
            same_task = str(item.get("task_id") or "") == task_id
            same_source = source_id and int(item.get("source_item_id") or 0) == source_id
            if same_task or same_source:
                result = item.get("result") if isinstance(item.get("result"), dict) else {}
                result.update(
                    {
                        "task_id": task_id,
                        "source_item_id": source_id,
                        "voice_enhanced": True,
                        "voice_enhanced_at": manifest["created_at"],
                        "cloned_audio_path": manifest["outputs"].get("cloned_audio"),
                        "raw_digital_human_path": manifest["outputs"].get("raw_digital_human"),
                        "voice_enhancement_manifest_path": str(work_dir / "voice_enhancement_manifest.json"),
                    }
                )
                item["result"] = result
                item["status"] = "completed"
                item["completed_at"] = manifest["created_at"]
                changed = True
        if changed:
            record["updated_at"] = now_iso()
            counts = record.get("counts") if isinstance(record.get("counts"), dict) else {}
            counts["completed"] = max(int(counts.get("completed") or 0), 1)
            if int(counts.get("queued") or 0) > 0:
                counts["queued"] = max(0, int(counts.get("queued") or 0) - 1)
            record["counts"] = counts
            record["status"] = "partial_completed" if counts.get("queued") else "completed"
            record["summary"] = "已完成影片，並已更新為克隆聲音與更生動的數字人口播版本。"
    save_json(path, rows)


def update_db(task_id: str, result: Any, final_video: Path, raw_dh: Path, duration: float, backup_dir: Path) -> None:
    ts = time.time()
    with sqlite3.connect(str(TG_DB), timeout=8.0) as conn:
        conn.execute(
            """
            UPDATE workspace_tasks
            SET status = 'completed',
                current_stage = ?,
                summary = ?,
                extracted_audio_path = ?,
                cloned_audio_path = ?,
                final_video_path = ?,
                cloned_audio_duration_seconds = ?,
                video_duration_seconds = ?,
                audio_task_id = ?,
                video_task_id = ?,
                error_message = '',
                started_at = COALESCE(started_at, ?),
                finished_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                "克隆聲音與生動數字人口播已完成",
                "已用來源影片克隆聲音，並重新生成更有動態的案件介紹影片。",
                str(result.extracted_audio_path),
                str(result.cloned_audio_path),
                str(final_video),
                int(result.cloned_audio_duration_seconds or duration or 0),
                int(duration or result.video_duration_seconds or 0),
                str(result.audio_task_id or ""),
                str(result.video_task_id or ""),
                ts,
                ts,
                ts,
                task_id,
            ),
        )
        events = [
            ("info", "backup_previous_outputs", f"上一版已保留：{backup_dir}"),
            ("info", "voice_clone_completed", f"克隆聲音已保存：{result.cloned_audio_path}"),
            ("info", "digital_human_lively_completed", f"生動口播影片已保存：{final_video}"),
        ]
        for level, stage, message in events:
            conn.execute(
                "INSERT INTO workspace_task_events (task_id, level, stage, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, level, stage, message, ts),
            )
        conn.commit()


def run(task_id: str) -> dict[str, Any]:
    row = task_row(task_id)
    work_dir = Path(row.get("work_dir") or TG_ROOT / "data" / "jobs" / task_id)
    pack = load_json(work_dir / "social_asset_pack.json", {})
    if not isinstance(pack, dict):
        pack = {}
    script = lively_script(pack, str(row.get("script_text") or ""))
    backup_dir = backup_outputs(work_dir)
    for name in ("case_script.txt", "voiceover_script.txt", "case_script_for_tts.txt"):
        (work_dir / name).write_text(script, encoding="utf-8")
    pack["voiceover_script"] = script
    pack["digital_human_intro_script"] = script
    pack["digital_human_video_script"] = script
    pack["voice_enhancement"] = {"style": "lively", "updated_at": now_iso()}
    save_json(work_dir / "social_asset_pack.json", pack)

    config = load_config(TG_ROOT)
    runner = DigitalHumanWorkflowRunner(config)

    def progress(message: str) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)

    request = WorkflowRequest(
        source_video_path=Path(row.get("source_video_path") or ""),
        avatar_image_path=Path(row.get("avatar_image_path") or ""),
        script_text=script,
        work_dir=work_dir,
        target_duration_seconds=None,
        publish_to_default_paths=False,
    )
    result = runner.run_request(request, progress_callback=progress)

    portrait = work_dir / "case_intro_with_digital_human_portrait_9x16.mp4"
    landscape = work_dir / "case_intro_with_digital_human_landscape_16x9.mp4"
    raw_dh = work_dir / "digital_human.mp4"
    raw_clone = work_dir / "digital_human_clone_raw.mp4"
    if raw_dh.exists():
        shutil.copy2(raw_dh, raw_clone)
    final_video = portrait if portrait.exists() else Path(result.final_video_path)
    if final_video.exists() and final_video != raw_dh:
        shutil.copy2(final_video, raw_dh)
        final_video = raw_dh
    thumb = work_dir / "thumbnail.jpg"
    thumbnail_from_video(final_video, thumb)
    duration = video_duration_seconds(final_video)
    outputs = {
        "final_video": str(final_video),
        "portrait_video": str(portrait),
        "landscape_video": str(landscape),
        "raw_digital_human": str(raw_clone),
        "cloned_audio": str(result.cloned_audio_path),
        "extracted_audio": str(result.extracted_audio_path),
        "thumbnail": str(thumb),
        "script": str(work_dir / "case_script.txt"),
    }
    manifest = {
        "task_id": task_id,
        "source_item_id": source_item_id(row, pack),
        "case_title": str(pack.get("case_title") or ""),
        "created_at": now_iso(),
        "style": "lively_digital_human_with_cloned_voice",
        "duration_seconds": duration,
        "backup_dir": str(backup_dir),
        "script": script,
        "audio_task_id": result.audio_task_id,
        "video_task_id": result.video_task_id,
        "outputs": outputs,
        "query_urls": {
            "final_video": f"/api/social-case-workbench/tg-files/{task_id}/final_video",
            "portrait_video": f"/api/social-case-workbench/tg-files/{task_id}/portrait_video",
            "landscape_video": f"/api/social-case-workbench/tg-files/{task_id}/landscape_video",
            "thumbnail": f"/api/social-case-workbench/tg-files/{task_id}/thumbnail",
            "script": f"/api/social-case-workbench/tg-files/{task_id}/script",
        },
    }
    save_json(work_dir / "voice_enhancement_manifest.json", manifest)
    existing_manifest = load_json(work_dir / "materials_manifest.json", {})
    if isinstance(existing_manifest, dict):
        existing_manifest["voice_enhancement"] = manifest
        existing_manifest["outputs"] = {**(existing_manifest.get("outputs") if isinstance(existing_manifest.get("outputs"), dict) else {}), **outputs}
        save_json(work_dir / "materials_manifest.json", existing_manifest)
        save_json(work_dir / "success_result.json", existing_manifest)
    update_db(task_id, result, final_video, raw_clone, duration, backup_dir)
    patch_batch_records(task_id, manifest["source_item_id"], work_dir, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Rerun one social case with cloned voice and lively digital-human output.")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.task_id), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
