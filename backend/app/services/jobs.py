"""One local worker shared by synchronous and background heavy operations."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, CancelledError

from fastapi import HTTPException

from ..core.config import TASKS_DIR
from ..core.storage import load_task, new_id, now, save_task

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omnimask-media")
_lock = threading.RLock()
_events: dict[str, threading.Event] = {}
_futures: dict[str, Future] = {}
ACTIVE = {"queued", "running"}


def ensure_idle(task: dict) -> None:
    if (task.get("job") or {}).get("status") in ACTIVE:
        raise HTTPException(409, "任务正在执行，请先取消或等待完成")


class Context:
    def __init__(self, task_id: str, event: threading.Event):
        self.task_id, self.event, self.last_update = task_id, event, 0.0

    def check_cancelled(self):
        if self.event.is_set():
            from .media_runtime import MediaCancelled
            raise MediaCancelled("任务已取消")

    def update(self, stage: str, completed: int | float = 0, total: int | float = 0):
        self.check_cancelled()
        tick = time.monotonic()
        if tick - self.last_update < .2 and completed != total:
            return
        self.last_update = tick
        with _lock:
            self.check_cancelled()
            task = load_task(self.task_id)
            if task and _events.get(self.task_id) is self.event:
                task["job"].update(stage=stage, completed=completed, total=total)
                task["updated_at"] = now()
                save_task(task)



def save_current(task: dict):
    """Keep current job progress while atomically committing workflow results."""
    with _lock:
        current = load_task(task["id"])
        if current and current.get("job"):
            task["job"] = current["job"]
        task["updated_at"] = now()
        save_task(task)


def _execute(task_id, operation, callback, event):
    context = Context(task_id, event)
    try:
        with _lock:
            context.check_cancelled()
            task = load_task(task_id)
            label = {'detect': '检测', 'mask': '脱敏', 'verify': '残留复查', 'audition': '音轨试听'}.get(operation, '处理')
            task["job"].update(status="running", stage="准备" + label)
            save_task(task)
        result = callback(context)
        # Returning from callback is the commit point. A late cancel cannot turn
        # already committed usable artifacts into a cancelled job.
        with _lock:
            task = load_task(task_id)
            task["job"].update(status="succeeded", stage="完成", error="")
            task["updated_at"] = now()
            save_task(task)
        return result
    except Exception as exc:
        from .media_runtime import MediaCancelled, MediaError
        cancelled = isinstance(exc, MediaCancelled)
        error = exc.detail if isinstance(exc, HTTPException) else str(exc)
        if not isinstance(exc, (HTTPException, MediaError)):
            error = f"处理失败（{type(exc).__name__}），请检查运行环境并重试"
        with _lock:
            task = load_task(task_id)
            if task and _events.get(task_id) is event:
                task["job"].update(status="cancelled" if cancelled else "failed", stage="已取消" if cancelled else "失败", error=error)
                task["updated_at"] = now()
                save_task(task)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(409 if cancelled else 422, str(error)) from exc
    finally:
        with _lock:
            # A retry may have been queued after terminal status was saved.
            if _events.get(task_id) is event:
                _events.pop(task_id, None)
                _futures.pop(task_id, None)


def launch(task_id: str, operation: str, callback, background: bool, shared_key: str | None = None):
    with _lock:
        task = load_task(task_id)
        if task is None:
            raise HTTPException(404, '任务不存在')
        job = task.get('job') or {}
        # Concurrent requests for the same audition await the same work. Wait
        # outside _lock: the worker needs that lock to persist its progress.
        shared = (shared_key is not None and job.get('status') in ACTIVE
                  and job.get('operation') == operation and job.get('request_key') == shared_key
                  and task_id in _futures)
        if shared:
            future = _futures[task_id]
        else:
            ensure_idle(task)
            event = threading.Event()
            _events[task_id] = event
            task["job"] = {"id": new_id(), "operation": operation, "status": "queued", "stage": "排队中", "completed": 0, "total": 0, "error": ""}
            if shared_key is not None:
                task['job']['request_key'] = shared_key
            save_task(task)
            future = _pool.submit(_execute, task_id, operation, callback, event)
            _futures[task_id] = future
        # Consume background exceptions: the persisted job is the public result.
        if background:
            future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
            return None
    try:
        return future.result()
    except CancelledError as exc:
        raise HTTPException(409, "排队任务已取消") from exc


def cancel(task_id: str):
    with _lock:
        event = _events.get(task_id)
        future = _futures.get(task_id)
        if event is None:
            return False
        event.set()
        if future is not None and future.cancel():
            # A queued future never runs _execute, so persist and clean up here.
            task = load_task(task_id)
            if task:
                task["job"].update(status="cancelled", stage="已取消", error="排队任务已取消")
                task["updated_at"] = now()
                save_task(task)
            if _events.get(task_id) is event:
                _events.pop(task_id, None)
                _futures.pop(task_id, None)
        return True


def recover_interrupted():
    with _lock:
        for path in TASKS_DIR.glob("*/meta.json"):
            task = load_task(path.parent.name)
            if task and (task.get("job") or {}).get("status") in ACTIVE:
                # Audition builds only a source-derived listening cache; it
                # never changes reviewed output or the task's workflow status.
                if task['job']['operation'] not in ('audition', 'verify'):
                    from .workflow import invalidate
                    invalidate(task)
                    task['status'] = 'created' if task['job']['operation'] == 'detect' else 'reviewed'
                elif task['job']['operation'] == 'verify' and task.get('verification', {}).get('status') == 'running':
                    task['verification']['status'] = 'interrupted'
                task["job"].update(status="interrupted", stage="已中断", error="服务已重启，请重新执行任务")
                task["updated_at"] = now()
                save_task(task)
