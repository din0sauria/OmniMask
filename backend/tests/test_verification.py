"""Residual recheck is scoped evidence, bound to immutable reviewed artifacts."""
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException

from app.core import storage
from app.services import verification, workflow
from app.services.media_runtime import MediaCancelled


class Context:
    cancelled = False

    def check_cancelled(self):
        if self.cancelled:
            raise MediaCancelled('cancelled')

    def update(self, *args):
        self.check_cancelled()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path / 'tasks'
    root.mkdir()
    monkeypatch.setattr(storage, 'TASKS_DIR', root)

    def make(source_content=b'phone 13800138000', output_content=b'nothing sensitive', suffix='.txt'):
        source = tmp_path / ('source' + suffix)
        source.write_bytes(source_content)
        task_id = 'verify-test'
        output = root / task_id / 'media-cache' / '2' / ('f' + suffix)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(output_content)
        fi = {'id':'f', 'path':'source'+suffix, 'abs':str(source), 'size':len(source_content),
              'source_fingerprint':workflow.fingerprint(source), 'is_text':suffix=='.txt',
              'detection':{'state':'complete'}}
        task = {'id':task_id,'status':'masked','revision':2,'detection_version':workflow.VERSION,
                'policy':{'pii_types':['phone','email'],'use_model':False},'files':[fi],
                'artifacts':{'revision':2,'files':{'f':{'cache_name':'f'+suffix,'path':'source'+suffix,
                             'sha256':workflow.fingerprint(output)['sha256'],'size':len(output_content)}}}}
        storage.save_task(task)
        storage.save_findings(task_id, [])
        return task, source, output

    return make


def test_text_residual_is_found_without_plaintext_or_paths(fixture):
    task, source, output = fixture(output_content=b'phone 13800138000; email test@example.com')
    before = source.read_bytes()
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'complete' and result['candidate_count'] == 2
    serialized = json.dumps(result)
    assert '13800138000' not in serialized and 'test@example.com' not in serialized
    assert str(source.parent) not in serialized
    assert all('value' not in hit and 'match' not in hit for hit in result['files'][0]['candidates'])
    assert source.read_bytes() == before
    assert storage.load_task(task['id'])['status'] == 'masked'
    assert verification.current_verification(storage.load_task(task['id']), True)['current'] is True


def test_no_candidate_does_not_claim_safety(fixture):
    task, _, _ = fixture()
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'complete' and result['candidate_count'] == 0
    assert any('不等于' in text for text in result['limitations'])
    assert 'safe' not in result and 'privacy_passed' not in result


def test_missing_or_changed_artifact_rejects_verification(fixture):
    task, _, output = fixture()
    output.write_bytes(b'changed')
    with pytest.raises(HTTPException, match='409'):
        verification.verify_task(task['id'], Context())
    assert storage.load_task(task['id'])['verification']['status'] == 'failed'


def test_manifest_and_content_changes_invalidate_snapshot(fixture):
    task, _, output = fixture()
    verification.verify_task(task['id'], Context())
    saved = storage.load_task(task['id'])
    output.write_bytes(b'tampered')
    assert verification.current_verification(saved, check_hashes=True)['status'] == 'stale'
    saved['revision'] += 1
    assert verification.current_verification(saved)['status'] == 'stale'


def test_cancelled_recheck_is_not_success(fixture, monkeypatch):
    task, _, _ = fixture()
    context = Context()
    def cancelled(*args):
        context.cancelled = True
        context.check_cancelled()
    monkeypatch.setattr(verification, '_detect', cancelled)
    with pytest.raises(MediaCancelled):
        verification.verify_task(task['id'], context)
    result = storage.load_task(task['id'])['verification']
    assert result['status'] == 'cancelled'
    assert result['files'][0]['status'] == 'not_checked'


def test_partial_detector_cannot_be_reported_complete(fixture, monkeypatch):
    task, _, _ = fixture()
    monkeypatch.setattr(verification, '_detect', lambda *a: ([], {'state':'partial'}))
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'partial'
    assert result['files'][0]['warnings']


def test_detector_exception_does_not_leak_message(fixture, monkeypatch):
    task, _, _ = fixture()
    def broken(*args):
        raise RuntimeError('private value 13800138000 at C:/private/secret.wav')
    monkeypatch.setattr(verification, '_detect', broken)
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'failed'
    assert '13800138000' not in json.dumps(result) and 'C:/private' not in json.dumps(result)


def test_new_review_revision_is_not_overwritten_by_old_recheck(fixture, monkeypatch):
    task, _, _ = fixture()
    def newer(*args):
        current = storage.load_task(task['id'])
        current['revision'] = 3
        current['verification'] = {'status':'newer'}
        storage.save_task(current)
        return [], {'state':'complete'}
    monkeypatch.setattr(verification, '_detect', newer)
    with pytest.raises(HTTPException):
        verification.verify_task(task['id'], Context())
    assert storage.load_task(task['id'])['verification'] == {'status':'newer'}


def test_actual_missing_asr_model_is_failed_not_zero_hit_success(fixture, monkeypatch):
    import io
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(16000), 16000, format='WAV')
    data = buffer.getvalue()
    task, _, _ = fixture(data, data, '.wav')
    monkeypatch.delenv('OMNIMASK_ASR_MODEL', raising=False)
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'failed' and result['files'][0]['status'] == 'failed'


def test_missing_text_model_cannot_silently_claim_complete(fixture, monkeypatch):
    from app.services.detect import text
    monkeypatch.setattr(text, 'text_llm', lambda: None)
    task, _, _ = fixture()
    task['policy']['use_model'] = True
    storage.save_task(task)
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'partial'
    assert result['files'][0]['warnings']


def test_coverage_ignores_metadata_strings_and_source_paths(fixture, monkeypatch):
    task, _, _ = fixture()
    monkeypatch.setattr(verification, '_detect', lambda *a: ([], {'state':'complete', 'metadata':{
        'duration':3.0, 'transcript':'private 13800138000', 'source':'C:/private/secret',
        'frame_check':'C:/private/path', 'audio_tracks':[{'source':'C:/private/audio'}]}}))
    result = verification.verify_task(task['id'], Context())
    coverage = result['files'][0]['coverage']
    assert coverage['duration'] == 3.0 and coverage['audio_track_count'] == 1
    assert 'private' not in json.dumps(result) and '13800138000' not in json.dumps(result)


@pytest.mark.parametrize('content, expected', [('{}', 'complete'), ('not JSON', 'partial'),
    ('{"电话号码":"13800138000"}', 'partial'), ('{"error":"failed"}', 'partial'),
    ('{"电话号码":[13800138000]}', 'partial')])
def test_enabled_text_model_success_and_bad_output_are_distinct(fixture, monkeypatch, content, expected):
    from app.services.detect import text
    class LocalModel:
        def create_chat_completion(self, **kwargs):
            return {'choices': [{'message': {'content': content}}]}
    monkeypatch.setattr(text, 'text_llm', lambda: LocalModel())
    task, _, _ = fixture()
    task['policy']['use_model'] = True
    storage.save_task(task)
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == expected
    assert result['candidate_count'] == 0


def test_actual_qr_artifact_recheck_finds_residual_without_payload(fixture, tmp_path):
    import cv2
    payload = 'https://example.com/verification-synthetic-target'
    qr = cv2.QRCodeEncoder_create().encode(payload)
    qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    qr = cv2.resize(qr, (272,272), interpolation=cv2.INTER_NEAREST)
    image = np.full((350,350),255,np.uint8)
    image[39:311,39:311] = qr
    ok, data = cv2.imencode('.png', image)
    assert ok
    task, _, _ = fixture(data.tobytes(), data.tobytes(), '.png')
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'complete'
    assert any(h['rule_name'] == 'qr_code' for h in result['files'][0]['candidates'])
    assert payload not in json.dumps(result)


def test_code_candidate_limit_is_partial_not_complete(fixture, monkeypatch):
    from app.services.detect import code
    # Exercise real span generation and truncation without unrelated KeyLeak filters.
    monkeypatch.setattr(code, '_state', {'rules': [], 'check_multi': lambda hits: hits,
        'combine_substr': lambda hits: (hits, None, None), 'filter_strings': lambda path,hits: hits})
    content = b' '.join(f'KEY{i:04d}'.encode() for i in range(3001))
    task, _, _ = fixture(content, content, '.py')
    task['policy']['custom_regex'] = [{'name':'fixture_key', 'regex':r'KEY\d+'}]
    storage.save_task(task)
    result = verification.verify_task(task['id'], Context())
    assert result['status'] == 'partial'
    assert result['candidate_count'] == 3000
    assert result['files'][0]['candidates_truncated'] is True
