"""All lifecycle mutations are restricted to pytest's isolated task tree."""
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.maintenance import router
from app.core import storage
from app.services import maintenance, policy
from app.services.mask import code
from app.models.schemas import PolicyIn


@pytest.fixture
def owned(tmp_path, monkeypatch):
    tasks = tmp_path / 'data/tasks'
    tasks.mkdir(parents=True)
    monkeypatch.setattr(storage, 'TASKS_DIR', tasks)
    monkeypatch.setattr(policy, 'POLICY_FILE', tmp_path / 'data/policies.json')
    return tasks


def make_task(root, task_id='one', source=None, **extra):
    path = root / task_id
    path.mkdir(parents=True, exist_ok=True)
    if source is None:
        source = root.parent.parent / f'{task_id}-source.txt'
        source.write_text('original data', encoding='utf-8')
    meta = {'id':task_id, 'name':f'Task {task_id}', 'status':'masked', 'revision':3,
            'files':[{'id':'f','abs':str(source),'path':source.name,
                      'source_fingerprint':{'sha256':'b'*64},
                      'original_preview':'original-cache/f/'+'b'*64+'.wav'}],
            'artifacts':{'revision':3,'files':{}}, **extra}
    storage.write_json(path / 'meta.json', meta)
    (path / 'findings.json').write_text('[]', encoding='utf-8')
    return path, meta, source


def test_soft_delete_restore_preserves_sources_and_data(owned):
    path, meta, source = make_task(owned)
    original = source.read_bytes()
    snapshot = (path / 'meta.json').read_bytes()
    result = maintenance.soft_delete(['one'])
    assert result['results'][0]['ok']
    assert not path.exists() and (owned.parent/'trash/one/meta.json').is_file()
    listing = maintenance.storage_info()
    assert listing['tasks'] == [] and listing['trash'][0]['id'] == 'one'
    assert listing['trash'][0]['deleted_at']
    assert listing['logical_bytes'] and listing['totals']['trash_bytes'] > 0
    assert maintenance.restore_tasks(['one'])['results'][0]['ok']
    assert (path/'meta.json').read_bytes() == snapshot
    assert source.read_bytes() == original


def test_purge_requires_confirmation_and_never_removes_source(owned):
    path, meta, source = make_task(owned)
    maintenance.soft_delete(['one'])
    with pytest.raises(HTTPException) as error:
        maintenance.purge_tasks(['one'])
    assert error.value.status_code == 422
    assert maintenance.purge_tasks(['one'], True)['results'][0]['ok']
    assert not (owned.parent/'trash/one').exists()
    assert source.read_text() == 'original data'


def test_invalid_paths_and_metadata_are_rejected(owned):
    outside = owned.parent.parent / 'outside'
    outside.mkdir()
    marker = outside / 'important.txt'
    marker.write_text('preserve')
    for task_id in ['../outside', '..', 'one/../../outside', 'C:\\outside']:
        assert not maintenance.soft_delete([task_id])['results'][0]['ok']
    path, meta, _ = make_task(owned)
    storage.write_json(path/'meta.json', {**meta,'id':'different'})
    assert not maintenance.soft_delete(['one'])['results'][0]['ok']
    assert path.exists() and marker.read_text() == 'preserve'


def test_busy_task_and_restore_collision_are_rejected(owned):
    path, meta, _ = make_task(owned, job={'status':'running'})
    assert not maintenance.soft_delete(['one'])['results'][0]['ok']
    storage.write_json(path/'meta.json', {**meta,'job':{'status':'succeeded'}})
    assert maintenance.soft_delete(['one'])['results'][0]['ok']
    replacement, _, _ = make_task(owned)
    (replacement/'marker').write_text('new task')
    assert not maintenance.restore_tasks(['one'])['results'][0]['ok']
    assert (replacement/'marker').read_text() == 'new task'


def test_generated_cache_used_as_source_blocks_move_and_purge(owned):
    path, _, _ = make_task(owned)
    generated = path/'generated.wav'
    generated.write_bytes(b'owned output')
    make_task(owned, 'two', generated)
    result = maintenance.soft_delete(['one'])['results'][0]
    assert not result['ok'] and 'two' in result['error']
    # After removing only the referring task metadata inside this test fixture,
    # moving succeeds. A newly referenced trash cache then blocks permanent purge.
    (owned/'two/meta.json').unlink()
    assert maintenance.soft_delete(['one'])['results'][0]['ok']
    generated = owned.parent/'trash/one/generated.wav'
    make_task(owned, 'two', generated)
    assert not maintenance.purge_tasks(['one'], True)['results'][0]['ok']
    assert generated.read_bytes() == b'owned output'


def test_cache_cleanup_retains_current_outputs_and_hashes(owned):
    path, meta, source = make_task(owned)
    candidates = ['media-cache/2/old.mp4','media-cache/3/current.mp4',
                  'original-cache/f/'+'a'*64+'.wav','original-cache/f/'+'b'*64+'.wav',
                  'original-audio-cache/f/'+'a'*64+'/track-0.wav',
                  'original-audio-cache/f/'+'b'*64+'/track-0.wav',
                  'unknown/user-file.txt']
    for name in candidates:
        file = path/name
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes(b'123456')
    before = source.read_bytes()
    listing = maintenance.storage_info()['tasks'][0]
    assert listing['stale_cache_bytes'] == 18
    result = maintenance.clean_cache(['one'])['results'][0]
    assert result['ok'] and result['bytes'] == 18 and not result['partial']
    for name in [candidates[1],candidates[3],candidates[5],candidates[6]]:
        assert (path/name).is_file()
    assert source.read_bytes() == before


def test_referenced_stale_cache_is_preserved_and_reported(owned):
    path, _, _ = make_task(owned)
    cached = path/'media-cache/1/old.wav'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'keep')
    make_task(owned,'two',cached)
    result = maintenance.clean_cache(['one'])['results'][0]
    assert result['ok'] and result['partial'] and result['warnings']
    assert cached.read_bytes() == b'keep'


def test_unknown_legacy_hash_and_referenced_artifact_revision_are_retained(owned):
    path, meta, _ = make_task(owned)
    meta['files'][0].pop('source_fingerprint')
    meta['files'][0].pop('original_preview')
    meta['artifacts']['revision'] = 2
    storage.write_json(path / 'meta.json', meta)
    retained = ['media-cache/2/preview.mp4', 'media-cache/3/current.mp4',
                'original-cache/f/' + 'a'*64 + '.wav',
                'original-audio-cache/f/' + 'a'*64 + '/track-0.wav']
    for name in retained:
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'keep')
    result = maintenance.clean_cache(['one'])['results'][0]
    assert result['ok'] and result['bytes'] == 0
    assert all((path / name).read_bytes() == b'keep' for name in retained)


def test_reparse_subtree_is_rejected_without_touching_target(owned):
    path, _, _ = make_task(owned)
    outside = owned.parent.parent/'external'
    outside.mkdir()
    (outside/'marker').write_text('external source')
    link = path/'unsafe-link'
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(outside),str(link))
    else:
        link.symlink_to(outside,target_is_directory=True)
    try:
        assert not maintenance.soft_delete(['one'])['results'][0]['ok']
        assert not maintenance.clean_cache(['one'])['results'][0]['ok']
        assert (outside/'marker').read_text() == 'external source'
    finally:
        if os.name == 'nt': link.rmdir()
        else: link.unlink()


def test_os_failure_is_explicit_not_success(owned,monkeypatch):
    path, _, _ = make_task(owned)
    def blocked(*args,**kwargs):
        raise PermissionError('test lock')
    monkeypatch.setattr(Path,'rename',blocked)
    result = maintenance.soft_delete(['one'])['results'][0]
    assert not result['ok'] and '未完成' in result['error']
    assert path.exists()


def test_maintenance_api_envelope(owned):
    make_task(owned)
    app=FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response=client.get('/api/maintenance/storage')
        assert response.status_code == 200 and response.json()['code'] == 0
        assert response.json()['data']['tasks'][0]['id'] == 'one'
        response=client.post('/api/maintenance/trash',json={'task_ids':['one']})
        assert response.json()['data']['results'][0]['ok']
        assert client.post('/api/maintenance/purge',json={'task_ids':['one']}).status_code == 422
        assert client.post('/api/maintenance/restore',json={'task_ids':['one']}).json()['data']['results'][0]['ok']


def test_encrypt_is_disabled_for_new_and_updated_policy_but_legacy_is_readable(owned):
    legacy={'id':'old','name':'legacy','mode':'encrypt','crypto_key':'existing-key'}
    storage.write_json(policy.POLICY_FILE,[legacy])
    read=policy.get_policy('old')
    assert read['generation_disabled'] and read['crypto_key']=='existing-key'
    for action in [lambda:policy.create_policy(PolicyIn(name='new',mode='encrypt')),
                   lambda:policy.update_policy('old',PolicyIn(name='old',mode='encrypt'))]:
        with pytest.raises(HTTPException) as error: action()
        assert error.value.status_code == 422
    assert storage.read_json(policy.POLICY_FILE)[0] == legacy
    updated=policy.update_policy('old',PolicyIn(name='safe',mode='mask'))
    assert updated['mode']=='mask'


def test_encrypt_generation_cannot_bypass_policy_with_no_hits_or_override(owned):
    for action in [lambda:code.compute({'id':'x','files':[],'policy':{'mode':'encrypt'}},[]),
                   lambda:code.compute({'id':'x','files':[],'policy':{'mode':'mask'}},[{'mode':'encrypt'}]),
                   lambda:code.mask_value('secret','encrypt',0),
                   lambda:code.encrypt_value('secret','key')]:
        with pytest.raises(HTTPException) as error: action()
        assert error.value.status_code == 422
    assert code.mask_value('secret','delete',0)==''
