"""API acceptance: real local files, reviewed artifacts, no fail-open routes."""
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.core import storage
from app.api import tasks
from app.services import jobs, policy, workflow
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    task_root = tmp_path / 'tasks'
    task_root.mkdir()
    monkeypatch.setattr(storage, 'TASKS_DIR', task_root)
    monkeypatch.setattr(tasks, 'TASKS_DIR', task_root)
    monkeypatch.setattr(jobs, 'TASKS_DIR', task_root)
    monkeypatch.setattr(policy, 'POLICY_FILE', tmp_path / 'policies.json')
    with TestClient(app) as client:
        yield client


def create(client, path, policy_id='text-pii'):
    result = client.post('/api/tasks', json={'source_path': str(path), 'policy_id': policy_id})
    assert result.status_code == 200, result.text
    return result.json()['data']['task']['id']


def detect(client, task):
    result = client.post(f'/api/tasks/{task}/detect', json={})
    assert result.status_code == 200, result.text
    return result.json()['data']['task']


def accept(client, task):
    assert client.post(f'/api/tasks/{task}/review', json={'accept_all': True}).status_code == 200


def archive(client, task):
    result = client.get(f'/api/tasks/{task}/export')
    assert result.status_code == 200, result.text
    return zipfile.ZipFile(io.BytesIO(result.content))


def test_text_review_mask_export_and_source_hash(client, tmp_path):
    source = tmp_path / 'sample.txt'
    original = '测试手机号：13800138000；邮箱 test@example.com。'
    source.write_text(original, encoding='utf-8')
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    task = create(client, source)
    info = detect(client, task)
    file = info['files'][0]['id']
    assert info['files'][0]['detection']['state'] == 'complete'
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 409
    assert storage.load_findings(task)[0]['status'] == 'pending'
    accept(client, task)
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 200
    preview = client.get(f'/api/tasks/{task}/files/{file}').json()['data']
    assert '13800138000' not in preview['masked']
    with archive(client, task) as z:
        assert z.read('masked/sample.txt').decode() == preview['masked']
        report = z.read('report.json').decode()
        assert '13800138000' not in report and str(tmp_path) not in report
        assert 'mapping.json' not in z.namelist()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_review_invalidates_output_and_threshold_consistent(client, tmp_path):
    source = tmp_path / 'sample.txt'
    source.write_text('phone 13800138000', encoding='utf-8')
    task = create(client, source)
    info = detect(client, task)
    data = storage.load_task(task)
    data['policy']['min_confidence'] = 1.0  # accepted 0.95 hit excluded by an explicit threshold
    storage.save_task(data)
    accept(client, task)
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 200
    fid = info['files'][0]['id']
    pv = client.get(f'/api/tasks/{task}/files/{fid}').json()['data']
    with archive(client, task) as z:
        assert z.read('masked/sample.txt').decode() == pv['masked'] == source.read_text()
    finding = storage.load_findings(task)[0]
    assert client.post(f'/api/tasks/{task}/review', json={'items': [{'id': finding['id'], 'status': 'rejected'}]}).status_code == 200
    assert client.get(f'/api/tasks/{task}/export').status_code == 409


def test_changed_source_blocks_export(client, tmp_path):
    path = tmp_path / 'plain.txt'
    path.write_text('nothing sensitive')
    task = create(client, path)
    detect(client, task)
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 200
    path.write_text('new sensitive content')
    assert client.get(f'/api/tasks/{task}/export').status_code == 409


def test_media_failures_never_return_original_as_masked(client, tmp_path, monkeypatch):
    from app.services.detect import audio
    path = tmp_path / 'tone.wav'
    sf.write(path, np.zeros(1600), 16000)
    monkeypatch.delenv('OMNIMASK_ASR_MODEL', raising=False)
    task = create(client, path, 'media')
    info = detect(client, task)
    fid = info['files'][0]['id']
    assert info['files'][0]['detection']['state'] == 'failed'
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 409
    result = client.get(f'/api/tasks/{task}/files/{fid}/audio?masked=1')
    assert result.status_code == 409 and result.headers['content-type'].startswith('application/json')


def mock_audio(monkeypatch):
    from app.services.detect import audio
    def fake(files, policy, context=None):
        """Only isolates the API. Real recognition is verified separately."""
        fi = files[0]
        return {'findings': [], 'scanned': 1, 'skipped': 0, 'warnings': [], 'files': {
            fi['id']: {'state': 'complete', 'warnings': [], 'transcript': [], 'metadata': {
                'duration': 1.0, 'audio_tracks': [{'track_index': 0}]}}}}
    monkeypatch.setattr(audio, 'detect_audio_files', fake)


def test_manual_audio_review_cache_reuse_and_immutable_source(client, tmp_path, monkeypatch):
    mock_audio(monkeypatch)
    path = tmp_path / 'tone.wav'
    t = np.arange(16000) / 16000
    sf.write(path, np.stack([np.sin(2 * np.pi * 440 * t)] * 2, axis=1), 16000)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    task = create(client, path, 'media')
    info = detect(client, task)
    fid = info['files'][0]['id']
    added = client.post(f'/api/tasks/{task}/review', json={'add': [
        {'file_id': fid, 'media': 'audio', 'start': .3, 'end': .5, 'track_index': 0}]})
    assert added.status_code == 200, added.text
    assert added.json()['data']['summary']['pending'] == 1
    accept(client, task)
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 200
    from app.services.mask import media
    monkeypatch.setattr(media, 'mask_audio_bytes', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must reuse cache')))
    result = client.get(f'/api/tasks/{task}/files/{fid}/audio?masked=1')
    assert result.status_code == 200
    signal, sr = sf.read(io.BytesIO(result.content))
    assert signal.shape == (16000, 2)
    assert np.max(abs(signal[int(.2 * sr):int(.6 * sr)])) == 0
    with archive(client, task) as z:
        assert z.read('masked/tone.wav') == result.content
    hit = storage.load_findings(task)[0]
    changed = client.post(f'/api/tasks/{task}/review', json={'items': [{'id': hit['id'], 'start': .4, 'end': .6}]})
    assert changed.status_code == 200 and changed.json()['data']['summary']['pending'] == 1
    assert client.get(f'/api/tasks/{task}/files/{fid}/audio?masked=1').status_code == 409
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_invalid_manual_locations_are_atomic(client, tmp_path, monkeypatch):
    mock_audio(monkeypatch)
    path = tmp_path / 'tone.wav'
    sf.write(path, np.zeros(16000), 16000)
    task = create(client, path)
    info = detect(client, task)
    fid = info['files'][0]['id']
    for start, end in [(1, .2), (0, 2), (-1, .2)]:
        r = client.post(f'/api/tasks/{task}/review', json={'add': [{'file_id': fid, 'media': 'audio', 'start': start, 'end': end}]})
        assert r.status_code == 422
        assert not storage.load_findings(task)


def test_background_completion_and_restart_legacy(client, tmp_path):
    path = tmp_path / 'plain.txt'
    path.write_text('plain text')
    task = create(client, path)
    result = client.post(f'/api/tasks/{task}/detect', json={'background': True})
    assert result.status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        info = client.get(f'/api/tasks/{task}').json()['data']['task']
        if info['job']['status'] not in ('running', 'queued'):
            break
        time.sleep(.01)
    assert info['job']['status'] == 'succeeded'
    old = storage.load_task(task)
    old.pop('detection_version')
    storage.save_task(old)
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 409


def test_country_code_phone_source_span():
    from app.services.detect.text import scan_pii
    source = '联系 +86 13800138000'
    hit = scan_pii(source, {'pii_types': ['phone']})[0]
    assert source[hit['index_start']:hit['index_end']] == hit['value'] == '+86 13800138000'


def test_reversible_multiple_files_preview_uses_exact_cached_tokens(client, tmp_path):
    folder = tmp_path / 'multi'
    folder.mkdir()
    (folder / 'a.txt').write_text('phone 13912345678')
    (folder / 'b.txt').write_text('email second@example.com')
    task = create(client, folder, 'reversible')
    info = detect(client, task)
    accept(client, task)
    counts = [f['findings'] for f in info['files']]
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 200
    with archive(client, task) as z:
        for fi in info['files']:
            pv = client.get(f"/api/tasks/{task}/files/{fi['id']}").json()['data']
            assert z.read('masked/' + fi['path']).decode() == pv['masked']
    assert [f['findings'] for f in storage.load_task(task)['files']] == counts


def test_oversize_is_skipped_before_hash_or_transcode(client, tmp_path, monkeypatch):
    source = tmp_path / 'oversize.wav'
    source.write_bytes(b'x' * 20)
    task = create(client, source)
    monkeypatch.setattr(workflow, 'MAX_MEDIA_BYTES', 10)
    monkeypatch.setattr(workflow, 'fingerprint', lambda *a: (_ for _ in ()).throw(AssertionError('must not hash oversized file')))
    info = detect(client, task)
    assert info['files'][0]['detection']['state'] == 'skipped'
    assert info['stats']['skipped'] == 1


def test_policy_crud_and_reversible_restore_regression(client, tmp_path):
    payload = {'name': 'Regression policy', 'mode': 'replace', 'reversible': True, 'pii_types': ['phone']}
    created = client.post('/api/policies', json=payload)
    assert created.status_code == 200
    pid = created.json()['data']['id']
    updated = client.put('/api/policies/' + pid, json={**payload, 'name': 'Updated policy'})
    assert updated.status_code == 200
    source = tmp_path / 'restore.txt'
    source.write_text('测试 13912345678', encoding='utf-8')
    task = create(client, source, pid)
    detect(client, task)
    accept(client, task)
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 200
    mapping = client.get(f'/api/tasks/{task}/mapping')
    assert mapping.status_code == 200 and mapping.json()['items']
    restored = client.post(f'/api/tasks/{task}/restore', json={})
    assert restored.status_code == 200
    downloaded = client.get(f'/api/tasks/{task}/restore/download')
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as z:
        assert z.read('restored/restore.txt') == source.read_bytes()
    source.write_text('changed', encoding='utf-8')
    assert client.get(f'/api/tasks/{task}/restore/download').status_code == 409
    assert client.delete('/api/policies/' + pid).status_code == 200


def test_cancelled_redetection_clears_previous_counts(client, tmp_path, monkeypatch):
    from app.services.detect import audio
    from app.services.media_runtime import MediaCancelled
    mock_audio(monkeypatch)
    source = tmp_path / 'tone.wav'
    sf.write(source, np.zeros(16000), 16000)
    task = create(client, source)
    info = detect(client, task)
    fid = info['files'][0]['id']
    client.post(f'/api/tasks/{task}/review', json={'add': [{'file_id': fid, 'media': 'audio', 'start': 0, 'end': .5}]})
    def cancel_in_detector(*args):
        raise MediaCancelled('test cancellation')
    monkeypatch.setattr(audio, 'detect_audio_files', cancel_in_detector)
    result = client.post(f'/api/tasks/{task}/detect', json={})
    assert result.status_code == 409
    info = client.get(f'/api/tasks/{task}').json()['data']['task']
    assert info['job']['status'] == 'cancelled'
    assert info['files'][0]['findings'] == 0 and info['stats']['scanned'] == 0
    assert not storage.load_findings(task)
