from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.storage import load_task, load_findings
from ..services import jobs, workflow
from ..services.export_preflight import preflight
from ..services.verification import verify_task, current_verification

router = APIRouter(prefix='/api/quality/tasks', tags=['quality'])


def _task(task_id):
    task = load_task(task_id)
    if task is None:
        raise HTTPException(404, '任务不存在')
    return task


class VerifyIn(BaseModel):
    background: bool = True


@router.post('/{task_id}/verify')
def verify(task_id: str, body: VerifyIn = VerifyIn()):
    task = _task(task_id)
    workflow.ready(task, load_findings(task_id))
    if task.get('status') not in ('masked', 'exported') or not workflow.cache_valid(task):
        raise HTTPException(409, '请先生成当前审核版本的脱敏结果')
    jobs.launch(task_id, 'verify', lambda context: verify_task(task_id, context), body.background)
    task = _task(task_id)
    return {'code': 0, 'msg': 'ok', 'data': {'task': task, 'verification': current_verification(task)}}


@router.get('/{task_id}/preflight')
def export_preflight(task_id: str, anonymize_metadata: bool = False, include_mapping: bool = False):
    with jobs._lock:
        task = _task(task_id)
        jobs.ensure_idle(task)
        return {'code': 0, 'msg': 'ok', 'data': preflight(task, anonymize_metadata, include_mapping)}
