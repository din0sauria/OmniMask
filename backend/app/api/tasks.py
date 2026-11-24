import mimetypes
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from ..core.config import SKIP_DIRS, TASKS_DIR
from ..core.storage import (
    load_findings, load_task, new_id, now, read_json, save_findings, save_task, task_dir,
)
from ..models.schemas import DetectIn, MaskIn, RestoreIn, ReviewIn, TaskCreateIn
from ..services.detect.code import _is_text_file
from ..services.ingest import count_lines, read_text
from ..services.mask.code import (
    build_mapping, build_restore_zip, preview_file, restore_info,
)
from ..services.media import kind_of, label_of, partition
from ..services.policy import get_policy
from ..services.report import build_report, summarize
from ..services import jobs, workflow

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _get(task_id: str) -> dict:
    task = load_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


def _public(task: dict) -> dict:
    data = dict(task)
    data.pop("_dir", None)
    return data


def _prune(task_id: str) -> None:
    """Remove artifacts left by older versions that copied files into data/."""
    tdir = task_dir(task_id)
    for name in ("source", "masked", "restored", "exports"):
        shutil.rmtree(tdir / name, ignore_errors=True)
    for name in ("mapping.json", "report.json", "report.md"):
        p = tdir / name
        if p.exists():
            p.unlink()


def _new_file(rel: str, abs_path: Path) -> dict:
    text = kind_of(rel) in ("code", "text") and _is_text_file(abs_path)
    lines = 0
    if text:
        try:
            content, _ = read_text(abs_path)
            lines = count_lines(content)
        except OSError:
            text = False
    return {
        "id": new_id(8),
        "path": rel,
        "abs": str(abs_path.resolve()),
        "name": Path(rel).name,
        "size": abs_path.stat().st_size,
        "is_text": text,
        "lines": lines,
        "findings": 0,
    }


def _collect(sp: Path) -> list[dict]:
    infos: list[dict] = []
    if sp.is_file():
        infos.append(_new_file(sp.name, sp))
        return infos

    for root, dirs, names in os.walk(sp):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in names:
            p = Path(root) / name
            try:
                infos.append(_new_file(p.relative_to(sp).as_posix(), p))
            except OSError:
                continue
    infos.sort(key=lambda x: x["path"])
    return infos


@router.post("")
def create_task(body: TaskCreateIn):
    # Serialize collection and registration with maintenance reference checks.
    with jobs._lock:
        return _create_task(body)


def _create_task(body: TaskCreateIn):
    raw = body.source_path.strip().strip('"').strip("'")
    if not raw:
        raise HTTPException(400, "请填写本地文件或目录路径")
    sp = Path(raw).expanduser()
    try:
        sp = sp.resolve()
    except OSError:
        raise HTTPException(400, f"路径无效：{raw}")
    if not sp.exists():
        raise HTTPException(400, f"路径不存在：{raw}")
    if not (sp.is_file() or sp.is_dir()):
        raise HTTPException(400, f"不是文件或目录：{raw}")

    policy = get_policy(body.policy_id)
    if body.voice_change is not None:
        policy["voice_change"] = body.voice_change
    task_id = new_id()
    task_dir(task_id).mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    infos = _collect(sp)
    if not infos:
        raise HTTPException(400, "该路径下没有可检测的文件")

    source = "file" if sp.is_file() else "folder"
    modality = label_of(partition(infos))
    task = {
        "id": task_id,
        "name": body.name.strip() or sp.name,
        "status": "created",
        "created_at": now(),
        "updated_at": now(),
        "policy_id": policy["id"],
        "policy": policy,
        "operator": body.operator.strip(),
        "purpose": body.purpose.strip(),
        "files": infos,
        "stats": {"file_count": len(infos), "warnings": warnings},
        "source": source,
        "source_path": str(sp),
        "root": str(sp.parent if source == "file" else sp),
        "modality": modality,
    }
    if body.voice_change is not None:
        task["voice_change"] = body.voice_change
    save_task(task)
    return {"code": 0, "msg": "ok", "data": {"task": _public(task), "warnings": warnings}}


@router.get("")
def list_tasks(q: str = ""):
    items = []
    for d in TASKS_DIR.iterdir():
        if not d.is_dir():
            continue
        meta = read_json(d / "meta.json")
        if not meta:
            continue
        if q and q.lower() not in (meta.get("name", "") + " " + meta.get("id", "")).lower():
            continue
        items.append({
            "id": meta["id"], "name": meta["name"], "status": meta["status"],
            "created_at": meta["created_at"], "updated_at": meta["updated_at"],
            "file_count": len(meta.get("files", [])),
            "findings": (meta.get("stats") or {}).get("total", 0),
            "policy": (meta.get("policy") or {}).get("name", ""),
            "source": meta.get("source", "folder"),
            "source_path": meta.get("source_path", ""),
        })
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"code": 0, "msg": "ok", "data": items}


@router.get("/{task_id}")
def get_task(task_id: str):
    task = _get(task_id)
    findings = load_findings(task_id)
    return {"code": 0, "msg": "ok", "data": {"task": _public(task), "summary": summarize(findings)}}


@router.delete("/{task_id}")
def delete_task(task_id: str):
    from ..services.maintenance import soft_delete
    result = soft_delete([task_id])
    if not result['results'][0]['ok']:
        raise HTTPException(409, result['results'][0].get('error', '任务未移入回收站'))
    return {"code": 0, "msg": "ok", "data": result}


@router.post("/{task_id}/detect")
def detect(task_id: str, body: DetectIn | None = None):
    _get(task_id)
    body = body or DetectIn()
    result = jobs.launch(task_id, "detect", lambda ctx: workflow.detect_task(task_id, body.policy_id, ctx), body.background)
    return _operation_response(task_id, result)


def _operation_response(task_id, result=None):
    task = _get(task_id)
    data = {"task": _public(task), "summary": summarize(load_findings(task_id)),
            "warnings": task.get("stats", {}).get("warnings", []), "stats": task.get("stats", {}).get("mask", {})}
    return {"code": 0, "msg": "ok", "data": data}


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str):
    _get(task_id)
    jobs.cancel(task_id)
    return _operation_response(task_id)


@router.get("/{task_id}/findings")
def get_findings(task_id: str, file_id: str = "", status: str = ""):
    _get(task_id)
    findings = load_findings(task_id)
    if file_id:
        findings = [f for f in findings if f["file_id"] == file_id]
    if status:
        findings = [f for f in findings if f["status"] == status]
    return {"code": 0, "msg": "ok", "data": {"findings": findings, "summary": summarize(findings)}}


@router.post("/{task_id}/review")
def review(task_id: str, body: ReviewIn):
    with jobs._lock:
        task = _get(task_id)
        jobs.ensure_idle(task)
        if task.get("detection_version") != workflow.VERSION:
            raise HTTPException(409, "请先重新检测")
        workflow.verify_sources(task)
        if body.accept_all and body.reject_all:
            raise HTTPException(422, "接受全部与忽略全部不能同时选择")
        findings = load_findings(task_id)
        by_id = {f["id"]: f for f in findings}
        files = {f["id"]: f for f in task["files"]}
        if body.accept_all or body.reject_all:
            for finding in findings:
                if finding["status"] == "pending":
                    finding["status"] = "accepted" if body.accept_all else "rejected"
        for item in body.items:
            finding = by_id.get(item.id)
            if not finding:
                raise HTTPException(404, "审核结果不存在")
            changes = item.model_dump(exclude_none=True, exclude={"id"})
            if changes.get('mode') == 'encrypt':
                raise HTTPException(422, '旧实验加密已停用，请选择掩码、删除或文本代号替换')
            location = any(k in changes for k in ("start", "end", "bbox", "track_index"))
            if location and finding.get("media") not in ("image", "audio", "video"):
                raise HTTPException(422, "只有媒体结果支持区域或时间调整")
            if finding.get("media") and changes.get("mode") not in (None, "default", "mask"):
                raise HTTPException(422, "媒体结果仅支持区域遮挡或片段静音")
            finding.update(changes)
            if finding.get("mode") == "default":
                finding["mode"] = None
            if location:
                if "start" in changes or "end" in changes:
                    finding.pop("frame_index", None)
                    finding.pop("frame_pts", None)
                finding.update(source="manual", timing_precision="manual", status="pending", confidence=1.0)
                workflow.validate_location(finding, files[finding["file_id"]])
        for added in body.add:
            fi = files.get(added.file_id)
            if not fi or kind_of(fi['path']) != added.media:
                raise HTTPException(422, "手动标记的文件与模态不匹配")
            finding = added.model_dump(exclude_none=True)
            finding.update(id='manual_' + new_id(), source='manual', timing_precision='manual',
                           file_path=fi['path'], status='pending', mode=None, confidence=1.0,
                           type_label='人工区域' if added.bbox else '人工静音', rule_name='manual', series='manual',
                           risk='high', line_start=0, line_end=0, col_start=0, col_end=0, index_start=0, index_end=0)
            workflow.validate_location(finding, fi)
            findings.append(finding)
        workflow.invalidate(task)
        save_findings(task_id, findings)
        summary = summarize(findings)
        for fi in task['files']:
            fi['findings'] = sum(f['file_id'] == fi['id'] for f in findings)
        task['status'] = 'reviewed' if summary['pending'] == 0 else 'detected'
        task['updated_at'] = now()
        task['stats'].update(summary)
        save_task(task)
        return {"code": 0, "msg": "ok", "data": {"summary": summary, "task": _public(task)}}


@router.get("/{task_id}/files/{file_id}")
def file_preview(task_id: str, file_id: str):
    task = _get(task_id)
    fi = next((f for f in task["files"] if f["id"] == file_id), None)
    if not fi:
        raise HTTPException(404, "文件不存在")
    if not Path(fi["abs"]).exists():
        raise HTTPException(404, "源文件不存在或已被移动")
    if fi.get('source_fingerprint') and workflow.fingerprint(Path(fi['abs'])) != fi['source_fingerprint']:
        raise HTTPException(409, '源文件已改变，请重新检测')
    findings = [f for f in load_findings(task_id) if f["file_id"] == file_id]
    if not fi["is_text"]:
        kind = kind_of(fi["path"])
        if kind in ("image", "audio", "video"):
            base = f"/api/tasks/{task_id}/files/{file_id}/{kind}"
            return {"code": 0, "msg": "ok", "data": {"file": fi, "original": "", "masked": "",
                                                      "applied": 0, "findings": findings, "binary": False,
                                                      "media": kind, f"{kind}_url": base,
                                                      "masked_url": base + f"?masked=1&revision={task.get('revision', 0)}",
                                                      "masked_ready": workflow.cache_valid(task)}}
        return {"code": 0, "msg": "ok", "data": {"file": fi, "original": "", "masked": "",
                                                  "applied": 0, "findings": findings, "binary": True}}
    pv = preview_file(task, fi, findings)
    if workflow.cache_valid(task):
        cached, _ = workflow.artifact_path(task, file_id)
        pv['masked'], _ = read_text(cached)
    return {"code": 0, "msg": "ok", "data": {"file": fi, "original": pv["text"], "masked": pv["masked"],
                                             "applied": pv["applied"], "findings": findings, "binary": False}}


def _source_file(task_id: str, file_id: str) -> FileResponse:
    task = _get(task_id)
    fi = next((f for f in task["files"] if f["id"] == file_id), None)
    if not fi:
        raise HTTPException(404, "文件不存在")
    p = Path(fi["abs"])
    if not p.exists():
        raise HTTPException(404, "源文件不存在或已被移动")
    if fi.get('original_preview'):
        if workflow.fingerprint(p) != fi.get('source_fingerprint'):
            raise HTTPException(409, '源文件已改变，请重新检测')
        preview = task_dir(task_id) / fi['original_preview']
        if preview.is_file():
            return FileResponse(preview)
        raise HTTPException(409, '可播放预览已丢失，请重新检测')
    if kind_of(fi['path']) in ('audio', 'video'):
        raise HTTPException(409, '请先检测以生成可播放的原始预览')
    return FileResponse(p)


@router.get("/{task_id}/files/{file_id}/image")
@router.get("/{task_id}/files/{file_id}/audio")
@router.get("/{task_id}/files/{file_id}/video")
def file_media(task_id: str, file_id: str, request: Request, masked: bool = False, track_index: int = 0):
    task = _get(task_id)
    fi = next((f for f in task['files'] if f['id'] == file_id), None)
    if not fi:
        raise HTTPException(404, '文件不存在')
    if masked:
        workflow.ready(task, load_findings(task_id))
        workflow.verify_sources(task)
        path, entry = workflow.artifact_path(task, file_id)
        return FileResponse(path, media_type=mimetypes.guess_type(entry['path'])[0])
    if kind_of(fi['path']) == 'video' and request.url.path.endswith('/audio'):
        from ..services.audio_preview import prepare_track
        path = prepare_track(task_id, file_id, track_index)
        return FileResponse(path, media_type='audio/wav')
    return _source_file(task_id, file_id)


@router.get("/{task_id}/files/{file_id}/download")
def file_download(task_id: str, file_id: str):
    task = _get(task_id)
    fi = next((f for f in task["files"] if f["id"] == file_id), None)
    if not fi:
        raise HTTPException(404, "文件不存在")
    p = Path(fi["abs"])
    if not p.exists():
        raise HTTPException(404, "源文件不存在或已被移动")
    return FileResponse(p, filename=fi["name"])


@router.post("/{task_id}/mask")
def mask(task_id: str, body: MaskIn = MaskIn()):
    task = _get(task_id)
    workflow.ready(task, load_findings(task_id))
    result = jobs.launch(task_id, "mask", lambda ctx: workflow.mask_task(task_id, ctx), body.background)
    return _operation_response(task_id, result)


@router.get("/{task_id}/report")
def get_report(task_id: str):
    task = _get(task_id)
    return {"code": 0, "msg": "ok", "data": build_report(task, load_findings(task_id))}


@router.get("/{task_id}/mapping")
def get_mapping(task_id: str):
    task = _get(task_id)
    mapping = build_mapping(task, load_findings(task_id))
    if not mapping:
        raise HTTPException(404, "当前策略不是可逆脱敏，没有映射表")
    return JSONResponse(mapping)


@router.post("/{task_id}/restore")
def restore(task_id: str, body: RestoreIn = RestoreIn()):
    task = _get(task_id)
    jobs.ensure_idle(task)
    workflow.ready(task, load_findings(task_id))
    workflow.verify_sources(task)
    if task.get("status") not in ("masked", "exported"):
        raise HTTPException(400, "请先执行脱敏")
    findings = load_findings(task_id)
    mapping = body.mapping or build_mapping(task, findings)
    if not mapping or not mapping.get("items"):
        raise HTTPException(400, "缺少映射表，无法还原")
    info = restore_info(task, findings, mapping)
    return {"code": 0, "msg": "ok", "data": {**info, "download": f"/api/tasks/{task_id}/restore/download"}}


def _zip_response(path: Path, filename: str, inline: bool):
    kwargs = {
        "media_type": "application/octet-stream",
        "background": BackgroundTask(os.unlink, path),
    }
    if inline:
        return FileResponse(path, **kwargs)
    return FileResponse(path, filename=filename, **kwargs)


@router.get("/{task_id}/restore/download")
def restore_download(task_id: str, inline: bool = False):
    task = _get(task_id)
    jobs.ensure_idle(task)
    workflow.ready(task, load_findings(task_id))
    workflow.verify_sources(task)
    if task.get("status") not in ("masked", "exported"):
        raise HTTPException(400, "请先执行脱敏")
    findings = load_findings(task_id)
    mapping = build_mapping(task, findings)
    if not mapping or not mapping.get("items"):
        raise HTTPException(400, "缺少映射表，无法还原")
    path = build_restore_zip(task, findings, mapping)
    return _zip_response(path, f"omnimask_{task_id}_restored.zip", inline)


@router.get("/{task_id}/export")
def export(task_id: str, include_mapping: bool = False, inline: bool = False,
           anonymize_metadata: bool = False, acknowledge_preflight: bool = False,
           expected_revision: int | None = None):
    with jobs._lock:
        task = _get(task_id)
        jobs.ensure_idle(task)
        path = workflow.export_cached(task, include_mapping, anonymize_metadata,
                                      acknowledge_preflight, expected_revision)
        task["status"] = "exported"
        task["updated_at"] = now()
        save_task(task)
    return _zip_response(path, f"omnimask_{task_id}.zip", inline)
