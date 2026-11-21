"""Local queue lifecycle regression tests; no models or media downloads."""
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from app.core import storage
from app.services import jobs


@pytest.fixture
def queue(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "TASKS_DIR", tmp_path)
    monkeypatch.setattr(jobs, "TASKS_DIR", tmp_path)
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(jobs, "_pool", pool)
    monkeypatch.setattr(jobs, "_events", {})
    monkeypatch.setattr(jobs, "_futures", {})
    yield tmp_path
    for event in jobs._events.values():
        event.set()
    pool.shutdown(wait=True, cancel_futures=True)


def task(task_id):
    data = {"id":task_id, "status":"created", "revision":0, "stats":{}}
    storage.save_task(data)
    return data


def test_queued_cancel_immediate_and_retry(queue):
    task("first"); task("second")
    entered, release = threading.Event(), threading.Event()
    def blocking(ctx):
        entered.set()
        assert release.wait(3)
    jobs.launch("first", "detect", blocking, True)
    assert entered.wait(2)
    never = threading.Event()
    jobs.launch("second", "detect", lambda ctx: never.set(), True)
    old = jobs._futures["second"]
    assert jobs.cancel("second")
    assert old.cancelled()
    assert storage.load_task("second")["job"]["status"] == "cancelled"
    assert "second" not in jobs._events
    retry = threading.Event()
    jobs.launch("second", "detect", lambda ctx: retry.set(), True)
    future = jobs._futures["second"]
    release.set()
    future.result(timeout=3)
    assert retry.is_set() and not never.is_set()
    assert storage.load_task("second")["job"]["status"] == "succeeded"


def test_duplicate_rejected_and_running_cancel(queue):
    task("work")
    entered = threading.Event()
    def work(ctx):
        entered.set()
        assert ctx.event.wait(3)
        ctx.check_cancelled()
    jobs.launch("work", "detect", work, True)
    future = jobs._futures["work"]
    assert entered.wait(2)
    with pytest.raises(HTTPException) as exc:
        jobs.launch("work", "detect", lambda ctx: None, True)
    assert exc.value.status_code == 409
    assert jobs.cancel("work")
    with pytest.raises(HTTPException): future.result(timeout=3)
    assert storage.load_task("work")["job"]["status"] == "cancelled"


def test_callback_return_is_success_commit_point(queue):
    task("mask")
    def committed(ctx):
        current = storage.load_task("mask")
        current.update(status="masked", artifacts={"revision":1})
        jobs.save_current(current)
        ctx.event.set()  # cancellation arrives after callback has committed
        return "done"
    assert jobs.launch("mask", "mask", committed, False) == "done"
    current = storage.load_task("mask")
    assert current["job"]["status"] == "succeeded"
    assert current["status"] == "masked"
    assert current["artifacts"]


def test_old_cleanup_does_not_remove_retry_event(queue, monkeypatch):
    task("same")
    old, replacement = threading.Event(), threading.Event()
    jobs._events["same"] = old
    current = storage.load_task("same")
    current["job"] = {"status":"queued"}
    storage.save_task(current)
    original = jobs.save_task
    def save_then_retry(data):
        original(data)
        if data["job"]["status"] == "succeeded":
            jobs._events["same"] = replacement
    monkeypatch.setattr(jobs, "save_task", save_then_retry)
    jobs._execute("same", "detect", lambda ctx: None, old)
    assert jobs._events["same"] is replacement


def test_restart_invalidates_interrupted_artifacts(queue):
    current = task("restart")
    cache = queue / "restart" / "media-cache"
    cache.mkdir(); (cache / "result.wav").write_bytes(b"incomplete")
    current.update(status="masked", artifacts={"revision":0}, job={"operation":"mask", "status":"running"})
    current["stats"]["mask"] = {"media_changed":1}
    storage.save_task(current)
    jobs.recover_interrupted()
    after = storage.load_task("restart")
    assert after["job"]["status"] == "interrupted"
    assert after["status"] == "reviewed"
    assert "artifacts" not in after and "mask" not in after["stats"]
    # Immutable files may remain for in-flight FileResponse, but no longer have
    # a trusted manifest and cannot be served/exported as a completed result.
    assert "artifacts" not in after


def test_background_failure_persisted_and_sanitized(queue):
    task("bad")
    def fail(ctx): raise ValueError("raw secret / private path")
    with jobs._lock:
        jobs.launch("bad", "mask", fail, True)
        future = jobs._futures["bad"]
    with pytest.raises(HTTPException): future.result(timeout=3)
    error = storage.load_task("bad")["job"]["error"]
    assert "ValueError" in error and "raw secret" not in error


def test_synchronous_queued_cancel_returns_conflict(queue, monkeypatch):
    task("blocking"); task("sync")
    entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
    def blocker(ctx):
        entered.set()
        assert release.wait(3)
    jobs.launch("blocking", "detect", blocker, True)
    assert entered.wait(2)
    real_submit = jobs._pool.submit
    def submit(*args, **kwargs):
        future = real_submit(*args, **kwargs)
        submitted.set()
        return future
    monkeypatch.setattr(jobs._pool, "submit", submit)
    errors = []
    def caller():
        try: jobs.launch("sync", "detect", lambda ctx: None, False)
        except HTTPException as exc: errors.append(exc.status_code)
    thread = threading.Thread(target=caller)
    thread.start()
    assert submitted.wait(2)
    assert jobs.cancel("sync")
    thread.join(2)
    release.set()
    assert not thread.is_alive() and errors == [409]
