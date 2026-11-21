"""API exports hide descriptive metadata and package the exact checked bytes."""
import io
import json
import zipfile
from pathlib import Path

from test_workflow_api import client, create, detect, accept
from app.core import storage
from app.services.export_preflight import aliases, anonymize_report
from app.services.workflow import artifact_path


def generate(client, source, policy='standard'):
    task_id = create(client, source, policy)
    detect(client, task_id)
    accept(client, task_id)
    response = client.post(f'/api/tasks/{task_id}/mask', json={})
    assert response.status_code == 200, response.text
    return task_id


def test_unknown_sensitive_extension_is_not_exported_in_name_or_format(client, tmp_path):
    source = tmp_path / 'notes.13800138000'
    source.write_text('nothing sensitive', encoding='utf-8')
    task_id = generate(client, source)
    preflight = client.get(f'/api/quality/tasks/{task_id}/preflight?anonymize_metadata=true').json()['data']
    assert any(item['field'] == 'file.path' and item['omitted_from_export']
               for item in preflight['metadata_candidates'])
    response = client.get(f'/api/tasks/{task_id}/export?anonymize_metadata=true')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert 'masked/file_0001.txt' in archive.namelist()
        assert all('13800138000' not in name for name in archive.namelist())
        report_text = archive.read('report.json').decode()
        assert '13800138000' not in report_text
        assert '13800138000' not in archive.read('report.md').decode()
        report = json.loads(report_text)
        assert report['files'][0]['processing']['output']['format'] == 'txt'
        assert archive.read('masked/file_0001.txt') == source.read_bytes()


def test_known_extensions_preserved_and_unknown_nontext_uses_bin():
    task = {'files': [{'id': 'a'}, {'id': 'b'}], 'artifacts': {'files': {
        'a': {'path': 'private.MP4', 'media': 'video'},
        'b': {'path': 'private.secret-payload', 'media': 'unsupported'},
    }}}
    assert aliases(task) == {'a': 'file_0001.mp4', 'b': 'file_0002.bin'}


def test_custom_rule_name_is_checked_then_hidden_in_report_and_mapping(client, tmp_path):
    rule_name = 'owner_test@example.com'
    secret = 'SYNTHETIC_PRIVATE_92Q'
    response = client.post('/api/policies', json={
        'name': 'custom policy', 'mode': 'replace', 'reversible': True,
        'custom_regex': [{'name': rule_name, 'regex': secret, 'confidence': .99}],
    })
    assert response.status_code == 200, response.text
    policy_id = response.json()['data']['id']
    source = tmp_path / 'credentials.py'
    source.write_text(f'credential = "{secret}"', encoding='utf-8')
    task_id = generate(client, source, policy_id)
    assert any(hit['rule_name'] == rule_name for hit in storage.load_findings(task_id))
    preflight = client.get(f'/api/quality/tasks/{task_id}/preflight').json()['data']
    assert any(item['field'] == 'policy.custom_regex[0].name' for item in preflight['metadata_candidates'])
    assert preflight['requires_acknowledgement']
    assert rule_name not in json.dumps(preflight)
    assert client.get(f'/api/tasks/{task_id}/export').status_code == 409
    response = client.get(f'/api/tasks/{task_id}/export?anonymize_metadata=true&include_mapping=true')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for name in ('report.json', 'report.md', 'mapping.json'):
            assert rule_name not in archive.read(name).decode()
        mapping = json.loads(archive.read('mapping.json'))
        item = next(item for item in mapping['items'] if item['value'] == secret)
        assert item['file_path'] == 'file_0001.py'
        assert item['rule_name'] == '自定义规则 1'
        assert item['type_label'] == '自定义规则'
        masked = archive.read('masked/file_0001.py').decode()
        assert secret not in masked
        assert masked.replace(item['token'], item['value']) == source.read_text(encoding='utf-8')


def test_historical_type_labels_matching_custom_rule_are_hidden():
    label = 'owner_13800138000'
    task = {'policy': {'custom_regex': [{'name': label}]}, 'files': [{'id': 'f', 'path': 'private.py'}],
            'artifacts': {'files': {'f': {'path': 'private.py', 'media': 'code'}}}}
    report = {'task': {}, 'policy': {}, 'files': [{'path': 'private.py'}],
              'summary': {'by_rule': [{'name': label}], 'by_type': [{'name': label}]},
              'reversibility': {'text_files': []}}
    result = anonymize_report(report, task)
    assert label not in json.dumps(result)
    assert report['summary']['by_type'][0]['name'] == label


def test_export_packages_checked_snapshot_even_if_cache_changes_after_read(client, tmp_path, monkeypatch):
    source = tmp_path / 'safe.txt'
    source.write_text('safe cached content', encoding='utf-8')
    task_id = generate(client, source)
    task = storage.load_task(task_id)
    cached, _ = artifact_path(task, task['files'][0]['id'])
    original_read = Path.read_bytes
    expected = original_read(cached)
    replaced = []

    def replace_after_read(path):
        data = original_read(path)
        if path == cached:
            path.write_bytes(b'UNREVIEWED replacement content')
            replaced.append(True)
        return data

    monkeypatch.setattr(Path, 'read_bytes', replace_after_read)
    response = client.get(f'/api/tasks/{task_id}/export?anonymize_metadata=true')
    assert response.status_code == 200, response.text
    assert replaced == [True]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read('masked/file_0001.txt') == expected
    # A later export must reject the now modified cache instead of using it.
    assert client.get(f'/api/tasks/{task_id}/export?anonymize_metadata=true').status_code == 409
