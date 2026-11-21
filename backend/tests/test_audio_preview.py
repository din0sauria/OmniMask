"""Audition cache/queue lifecycle tests; decoding is deterministic here."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core import storage
from app.api import tasks
from app.main import app
from app.services import audio_preview, jobs, workflow


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / 'tasks'
    root.mkdir()
    monkeypatch.setattr(storage, 'TASKS_DIR', root)
    monkeypatch.setattr(tasks, 'TASKS_DIR', root)
    monkeypatch.setattr(jobs, 'TASKS_DIR', root)
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(jobs, '_pool', pool)
    monkeypatch.setattr(jobs, '_events', {})
    monkeypatch.setattr(jobs, '_futures', {})
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'synthetic source')
    task = {'id':'listen', 'status':'masked', 'revision':4, 'stats':{},
            'artifacts':{'revision':4,'files':{}},
            'files':[{'id':'f','path':'video.mp4','abs':str(source),
                      'source_fingerprint':workflow.fingerprint(source)}]}
    storage.save_task(task)
    metadata = {'audio_tracks':[{'track_index':0}], 'duration':1}
    monkeypatch.setattr(audio_preview, 'probe', lambda path: metadata)
    calls = []
    def extract(path, track, output, metadata, context):
        calls.append(track['track_index'])
        context.check_cancelled()
        output.write_bytes(b'valid test wav')
    monkeypatch.setattr(audio_preview, 'extract_track', extract)
    yield root, source, calls
    for event in jobs._events.values():
        event.set()
    pool.shutdown(wait=True, cancel_futures=True)


def test_audio_endpoint_caches_track_and_preserves_masked_output(cache):
    root, source, calls = cache
    before = source.read_bytes()
    with TestClient(app) as client:
        url = '/api/tasks/listen/files/f/audio'
        first = client.get(url)
        second = client.get(url)
        assert first.status_code == second.status_code == 200
        assert first.content == second.content == b'valid test wav'
    assert calls == [0] and source.read_bytes() == before
    task = storage.load_task('listen')
    assert task['status'] == 'masked' and task['revision'] == 4
    assert task['artifacts'] == {'revision':4,'files':{}}
    assert task['job']['operation'] == 'audition' and task['job']['status'] == 'succeeded'


def test_concurrent_same_track_joins_one_extraction(cache, monkeypatch):
    entered, release, joined = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def extract(path, track, output, metadata, context):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        output.write_bytes(b'wav')
    monkeypatch.setattr(audio_preview, 'extract_track', extract)
    with ThreadPoolExecutor(max_workers=2) as clients:
        first = clients.submit(audio_preview.prepare_track, 'listen', 'f', 0)
        assert entered.wait(2)
        work = jobs._futures['listen']
        original_result = work.result
        def shared_result(*args, **kwargs):
            joined.set()
            return original_result(*args, **kwargs)
        monkeypatch.setattr(work, 'result', shared_result)
        second = clients.submit(audio_preview.prepare_track, 'listen', 'f', 0)
        try:
            assert joined.wait(2)  # It joined the active work, not a later cache hit.
        finally:
            release.set()
        assert first.result(timeout=3) == second.result(timeout=3)
    assert calls == [1]


def test_audition_waits_for_other_heavy_work_and_can_cancel_queued(cache):
    root, source, calls = cache
    storage.save_task({'id':'busy','status':'created','stats':{}})
    entered, release = threading.Event(), threading.Event()
    def busy(context):
        entered.set()
        assert release.wait(3)
    jobs.launch('busy', 'detect', busy, True)
    assert entered.wait(2)
    with ThreadPoolExecutor(max_workers=1) as clients:
        # Hold the lock until launch has registered the future so cancellation
        # has a deterministic queued state rather than a timing-based sleep.
        submitted = threading.Event()
        real_submit = jobs._pool.submit
        def submit(*args, **kwargs):
            future = real_submit(*args, **kwargs)
            submitted.set()
            return future
        jobs._pool.submit = submit
        future = clients.submit(audio_preview.prepare_track, 'listen', 'f', 0)
        try:
            assert submitted.wait(2)
            assert calls == []
            assert storage.load_task('listen')['job']['status'] == 'queued'
            assert jobs.cancel('listen')
            with pytest.raises(HTTPException) as exc:
                future.result(timeout=2)
            assert exc.value.status_code == 409
        finally:
            release.set()
    assert calls == []


def test_running_audition_cancels_without_publishing_and_blocks_edits(cache, monkeypatch):
    root, source, calls = cache
    entered = threading.Event()
    def extract(path, track, output, metadata, context):
        output.write_bytes(b'partial')
        entered.set()
        assert context.event.wait(3)
        context.check_cancelled()
    monkeypatch.setattr(audio_preview, 'extract_track', extract)
    with ThreadPoolExecutor(max_workers=1) as clients:
        future = clients.submit(audio_preview.prepare_track, 'listen', 'f', 0)
        assert entered.wait(2)
        with jobs._lock:
            with pytest.raises(HTTPException):
                jobs.ensure_idle(storage.load_task('listen'))
        assert jobs.cancel('listen')
        with pytest.raises(HTTPException) as exc:
            future.result(timeout=3)
        assert exc.value.status_code == 409
    assert not list(root.rglob('track-0.wav'))
    assert storage.load_task('listen')['status'] == 'masked'


def test_changed_source_rejected_even_with_cached_audio(cache):
    root, source, calls = cache
    audio_preview.prepare_track('listen', 'f', 0)
    source.write_bytes(b'changed')
    with pytest.raises(HTTPException) as exc:
        audio_preview.prepare_track('listen', 'f', 0)
    assert exc.value.status_code == 409 and calls == [0]


def test_cache_corruption_rebuilds_and_invalid_track_does_not_publish(cache):
    root, source, calls = cache
    path = audio_preview.prepare_track('listen', 'f', 0)
    path.write_bytes(b'corrupt')
    assert audio_preview.prepare_track('listen', 'f', 0).read_bytes() == b'valid test wav'
    assert calls == [0,0]
    with pytest.raises(HTTPException) as exc:
        audio_preview.prepare_track('listen', 'f', 1)
    assert exc.value.status_code == 404
    assert not list(root.rglob('track-1.wav'))


def test_failed_extraction_never_becomes_listening_cache(cache, monkeypatch):
    root, source, calls = cache
    def fail(path, track, output, metadata, context):
        output.write_bytes(b'partial')
        raise RuntimeError('private path not exposed')
    monkeypatch.setattr(audio_preview, 'extract_track', fail)
    with pytest.raises(HTTPException) as exc:
        audio_preview.prepare_track('listen', 'f', 0)
    assert exc.value.status_code == 422 and 'private path' not in str(exc.value.detail)
    assert not list(root.rglob('track-0.wav'))


def test_source_change_during_extraction_is_not_published(cache, monkeypatch):
    root, source, calls = cache
    def change(path, track, output, metadata, context):
        output.write_bytes(b'completed audio for old source')
        source.write_bytes(b'changed during work')
    monkeypatch.setattr(audio_preview, 'extract_track', change)
    with pytest.raises(HTTPException) as exc:
        audio_preview.prepare_track('listen', 'f', 0)
    assert exc.value.status_code == 409
    assert not list(root.rglob('track-0.wav'))


def test_real_ffmpeg_second_track_endpoint(cache, monkeypatch):
    import io
    import numpy as np
    import soundfile as sf
    from app.services import media_runtime
    root, source, calls = cache
    media_runtime.run_ffmpeg(['-f','lavfi','-i','color=size=64x48:rate=10:duration=1',
                             '-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=1',
                             '-f','lavfi','-i','sine=frequency=880:sample_rate=16000:duration=1',
                             '-map','0:v:0','-map','1:a:0','-map','2:a:0',
                             '-c:v','libx264','-c:a','aac',source])
    task = storage.load_task('listen')
    task['files'][0]['source_fingerprint'] = workflow.fingerprint(source)
    storage.save_task(task)
    before = source.read_bytes()
    monkeypatch.setattr(audio_preview, 'probe', media_runtime.probe)
    monkeypatch.setattr(audio_preview, 'extract_track', media_runtime.extract_track)
    with TestClient(app) as client:
        response = client.get('/api/tasks/listen/files/f/audio?track_index=1')
        assert response.status_code == 200, response.text
        data, rate = sf.read(io.BytesIO(response.content))
        peak = np.argmax(abs(np.fft.rfft(data))) * rate / len(data)
        assert abs(peak - 880) < 4
        assert abs(len(data)/rate - 1) < .05
        assert client.get('/api/tasks/listen/files/f/audio?track_index=1').content == response.content
    assert source.read_bytes() == before


def test_restart_of_audition_preserves_reviewed_artifacts(cache):
    task = storage.load_task('listen')
    task['job'] = {'operation':'audition','status':'running'}
    storage.save_task(task)
    jobs.recover_interrupted()
    after = storage.load_task('listen')
    assert after['job']['status'] == 'interrupted'
    assert after['status'] == 'masked' and after['revision'] == 4
    assert after['artifacts'] == task['artifacts']
