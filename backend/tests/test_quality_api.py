"""Export review and actual artifact verification share the current revision."""
import io
import json
import zipfile

from test_workflow_api import client, create, detect, accept
from app.core import storage
from app.services import jobs


def masked(client, tmp_path, name='sample.txt', reject=False):
    source = tmp_path / name
    source.write_text('phone 13800138000', encoding='utf-8')
    task = create(client, source)
    detect(client, task)
    client.post(f'/api/tasks/{task}/review', json={'reject_all': True} if reject else {'accept_all': True})
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 200
    return task


def test_verification_api_and_export_snapshot(client, tmp_path):
    task = masked(client, tmp_path)
    before = client.get(f'/api/quality/tasks/{task}/preflight').json()['data']
    assert before['verification_status'] == 'not_run'
    assert not before['requires_acknowledgement']
    check = client.post(f'/api/quality/tasks/{task}/verify', json={'background': False})
    assert check.status_code == 200, check.text
    state = storage.load_task(task)['verification']
    assert state['status'] == 'complete' and state['candidate_count'] == 0
    result = client.get(f'/api/tasks/{task}/export')
    assert result.status_code == 200, result.text
    with zipfile.ZipFile(io.BytesIO(result.content)) as z:
        report = json.loads(z.read('report.json'))
        assert report['verification']['status'] == 'complete'
        assert report['export_review']['verification_status'] == 'complete'
        assert '导出复核' in z.read('report.md').decode()


def test_ignored_residual_requires_ack_and_never_leaks_candidate_value(client, tmp_path):
    task = masked(client, tmp_path, reject=True)
    result = client.post(f'/api/quality/tasks/{task}/verify', json={'background': False})
    assert result.status_code == 200, result.text
    state = storage.load_task(task)['verification']
    assert state['candidate_count'] == 1
    assert '13800138000' not in json.dumps(state)
    review = client.get(f'/api/quality/tasks/{task}/preflight').json()['data']
    assert review['requires_acknowledgement']
    assert '13800138000' not in json.dumps(review)
    assert client.get(f'/api/tasks/{task}/export').status_code == 409
    assert client.get(f'/api/tasks/{task}/export?acknowledge_preflight=true').status_code == 200


def test_metadata_is_checked_and_optional_generic_names_remove_it(client, tmp_path):
    task = masked(client, tmp_path, 'phone_13800138000.txt')
    data = storage.load_task(task)
    data.update(operator='test@example.com', purpose='联系 13912345678')
    storage.save_task(data)
    review = client.get(f'/api/quality/tasks/{task}/preflight').json()['data']
    assert {'file.path', 'task.operator', 'task.purpose'} <= {c['field'] for c in review['metadata_candidates']}
    assert '13800138000' not in json.dumps(review)
    assert client.get(f'/api/tasks/{task}/export').status_code == 409
    result = client.get(f'/api/tasks/{task}/export?anonymize_metadata=true')
    assert result.status_code == 200, result.text
    with zipfile.ZipFile(io.BytesIO(result.content)) as z:
        assert 'masked/file_0001.txt' in z.namelist()
        all_text = ''.join(z.read(n).decode() for n in z.namelist())
        for original in ('phone_13800138000', 'test@example.com', '13912345678', str(tmp_path)):
            assert original not in all_text


def test_export_revision_and_tampered_artifact_rejected(client, tmp_path):
    task = masked(client, tmp_path)
    data = storage.load_task(task)
    assert client.get(f'/api/tasks/{task}/export?expected_revision=999').status_code == 409
    from app.services.workflow import artifact_path
    path, _ = artifact_path(data, data['files'][0]['id'])
    path.write_bytes(b'changed')
    assert client.get(f'/api/quality/tasks/{task}/preflight').status_code == 409
    assert client.get(f'/api/tasks/{task}/export').status_code == 409


def test_interrupted_verification_preserves_masked_artifacts(client, tmp_path):
    task = masked(client, tmp_path)
    data = storage.load_task(task)
    manifest, revision = data['artifacts'], data['revision']
    data['job'] = {'operation': 'verify', 'status': 'running', 'id': 'stopped'}
    data['verification'] = {'status': 'running'}
    storage.save_task(data)
    jobs.recover_interrupted()
    data = storage.load_task(task)
    assert data['status'] == 'masked'
    assert data['artifacts'] == manifest and data['revision'] == revision
    assert data['job']['status'] == 'interrupted'
    assert data['verification']['status'] == 'interrupted'


def test_legacy_encrypt_cannot_generate_even_without_hits(client, tmp_path):
    source = tmp_path / 'safe.txt'
    source.write_text('nothing private')
    task = create(client, source)
    detect(client, task)
    data = storage.load_task(task)
    data['policy']['mode'] = 'encrypt'
    storage.save_task(data)
    assert client.post(f'/api/tasks/{task}/mask', json={}).status_code == 422
