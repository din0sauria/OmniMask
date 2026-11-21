from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import maintenance

router = APIRouter(prefix='/api/maintenance', tags=['maintenance'])


class TaskSelection(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=500)


class PurgeSelection(TaskSelection):
    confirm: bool = False


def _ok(data):
    return {'code': 0, 'msg': 'ok', 'data': data}


@router.get('/storage')
def storage_usage():
    return _ok(maintenance.storage_info())


@router.post('/trash')
def trash_tasks(body: TaskSelection):
    return _ok(maintenance.soft_delete(body.task_ids))


@router.post('/restore')
def restore_tasks(body: TaskSelection):
    return _ok(maintenance.restore_tasks(body.task_ids))


@router.post('/purge')
def purge_tasks(body: PurgeSelection):
    return _ok(maintenance.purge_tasks(body.task_ids, body.confirm))


@router.post('/clean-cache')
def clean_cache(body: TaskSelection):
    return _ok(maintenance.clean_cache(body.task_ids))
