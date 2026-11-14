import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from ..core.config import MAX_FILES, MAX_FILE_BYTES, SKIP_DIRS, TASKS_DIR
from ..core.storage import (
    load_findings, load_task, new_id, now, read_json, save_findings, save_task, task_dir,
)
from ..models.schemas import DetectIn, MaskIn, RestoreIn, ReviewIn, TaskCreateIn
from ..services.detect.code import _is_text_file, detect_files
from ..services.ingest import count_lines, read_text
from ..services.mask.code import (
    build_export_zip, build_mapping, build_restore_zip, compute, preview_file, restore_info,
)
from ..services.policy import get_policy
from ..services.report import build_report, summarize

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
    text = _is_text_file(abs_path)
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


def _collect(sp: Path, warnings: list[str]) -> list[dict]:
    infos: list[dict] = []
    if sp.is_file():
        if sp.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(400, f"文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB 限制")
        infos.append(_new_file(sp.name, sp))
        return infos

    for root, dirs, names in os.walk(sp):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git")]
        for name in names:
            if len(infos) >= MAX_FILES:
                warnings.append(f"文件数量超过 {MAX_FILES}，仅导入前 {MAX_FILES} 个")
                return infos
            p = Path(root) / name
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    warnings.append(f"{p.relative_to(sp).as_posix()} 超过大小限制，已跳过")
                    continue
                infos.append(_new_file(p.relative_to(sp).as_posix(), p))
            except OSError:
                continue
    infos.sort(key=lambda x: x["path"])
    return infos


@router.post("")
def create_task(body: TaskCreateIn):
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
    task_id = new_id()
    task_dir(task_id).mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    infos = _collect(sp, warnings)
    if not infos:
        raise HTTPException(400, "该路径下没有可检测的文件")

    source = "file" if sp.is_file() else "folder"
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
    }
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
    _get(task_id)
    shutil.rmtree(task_dir(task_id), ignore_errors=True)
    return {"code": 0, "msg": "ok", "data": None}


@router.post("/{task_id}/detect")
def detect(task_id: str, body: DetectIn | None = None):
    task = _get(task_id)
    if body and body.policy_id:
        task["policy"] = get_policy(body.policy_id)
        task["policy_id"] = task["policy"]["id"]
    policy = task.get("policy") or {}
    files = [{"id": f["id"], "path": f["path"], "abs": f["abs"]} for f in task["files"]]
    res = detect_files(files, policy)

    _prune(task_id)
    rel_by_id = {f["id"]: f["path"] for f in task["files"]}
    findings = []
    for i, f in enumerate(res["findings"]):
        findings.append({
            "id": f"f{i + 1:04d}",
            "file_id": f["file_id"],
            "file_path": rel_by_id.get(f["file_id"], f["file_path"]),
            "rule_name": f["rule_name"],
            "series": f["series"],
            "is_mechanical": f["is_mechanical"],
            "value": f["value"],
            "match": f["match"],
            "line_start": f["line_start"],
            "line_end": f["line_end"],
            "col_start": f["col_start"],
            "col_end": f["col_end"],
            "index_start": f["index_start"],
            "index_end": f["index_end"],
            "confidence": f["confidence"],
            "risk": f["risk"],
            "status": "pending",
            "mode": None,
            "type_label": f["type_label"],
        })
    save_findings(task_id, findings)

    counts = {}
    for f in findings:
        counts[f["file_id"]] = counts.get(f["file_id"], 0) + 1
    for fi in task["files"]:
        fi["findings"] = counts.get(fi["id"], 0)
    summary = summarize(findings)
    task["status"] = "detected"
    task["updated_at"] = now()
    task["stats"] = {
        **task.get("stats", {}),
        "total": summary["total"],
        "scanned": res["scanned"],
        "skipped": res["skipped"],
        "warnings": res["warnings"],
        "by_type": summary["by_type"],
        "detected_at": now(),
    }
    save_task(task)
    return {"code": 0, "msg": "ok", "data": {"task": _public(task), "summary": summary, "warnings": res["warnings"]}}


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
    task = _get(task_id)
    findings = load_findings(task_id)
    by_id = {f["id"]: f for f in findings}
    if body.accept_all or body.reject_all:
        target = "accepted" if body.accept_all else "rejected"
        for f in findings:
            if f["status"] == "pending":
                f["status"] = target
    for item in body.items:
        f = by_id.get(item.id)
        if not f:
            continue
        if item.status:
            f["status"] = item.status
        if item.mode:
            f["mode"] = None if item.mode == "default" else item.mode
    save_findings(task_id, findings)
    summary = summarize(findings)
    task["status"] = "reviewed" if summary["pending"] == 0 else "detected"
    task["updated_at"] = now()
    task["stats"] = {**task.get("stats", {}), **{k: summary[k] for k in ("total", "pending", "accepted", "rejected")}}
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
    findings = [f for f in load_findings(task_id) if f["file_id"] == file_id]
    if not fi["is_text"]:
        return {"code": 0, "msg": "ok", "data": {"file": fi, "original": "", "masked": "",
                                                  "applied": 0, "findings": findings, "binary": True}}
    pv = preview_file(task, fi, findings)
    return {"code": 0, "msg": "ok", "data": {"file": fi, "original": pv["text"], "masked": pv["masked"],
                                             "applied": pv["applied"], "findings": findings, "binary": False}}


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
    findings = load_findings(task_id)
    policy = task.get("policy") or {}
    if findings and all(f["status"] == "rejected" for f in findings):
        raise HTTPException(400, "所有结果都被忽略了，请至少接受一条后再脱敏")
    res = compute(task, findings, body.accepted_only)
    stats = res["stats"]
    if stats["findings_applied"] == 0 and findings:
        task["status"] = "detected" if any(f["status"] == "pending" for f in findings) else "reviewed"
        task["updated_at"] = now()
        save_task(task)
        raise HTTPException(400, "没有符合策略阈值的结果，未执行脱敏")
    for f in findings:
        if f["status"] != "rejected" and float(f.get("confidence", 0)) >= float(policy.get("min_confidence", 0) or 0):
            f["status"] = "accepted"
    save_findings(task_id, findings)
    summary = summarize(findings)
    task["status"] = "masked"
    task["updated_at"] = now()
    task["stats"] = {**task.get("stats", {}), "total": summary["total"], **{k: summary[k] for k in ("pending", "accepted", "rejected")},
                     "mask": stats, "masked_at": now()}
    save_task(task)
    return {"code": 0, "msg": "ok", "data": {"stats": stats, "summary": summary, "task": _public(task)}}


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
    if task.get("status") not in ("masked", "exported"):
        raise HTTPException(400, "请先执行脱敏")
    findings = load_findings(task_id)
    mapping = build_mapping(task, findings)
    if not mapping or not mapping.get("items"):
        raise HTTPException(400, "缺少映射表，无法还原")
    path = build_restore_zip(task, findings, mapping)
    return _zip_response(path, f"omnimask_{task_id}_restored.zip", inline)


@router.get("/{task_id}/export")
def export(task_id: str, include_mapping: bool = False, inline: bool = False):
    task = _get(task_id)
    if task.get("status") not in ("masked", "exported"):
        raise HTTPException(400, "请先执行脱敏")
    findings = load_findings(task_id)
    path = build_export_zip(task, findings, include_mapping)
    task["status"] = "exported"
    task["updated_at"] = now()
    save_task(task)
    return _zip_response(path, f"omnimask_{task_id}.zip", inline)
