"""Actual masker measurements must be the records exported by the workflow."""
import io
import json
import zipfile

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.api import tasks
from app.core import storage
from app.main import app
from app.services import jobs, policy
from app.services.detect import audio
from app.services.mask.code import compute


def test_text_record_counts_only_operations_actually_applied(tmp_path):
    path = tmp_path / 'note.txt'
    path.write_text('13800138000', encoding='utf-8')
    task = {'id':'t', 'policy':{'mode':'mask'},
            'files':[{'id':'f', 'path':'note.txt', 'abs':str(path), 'is_text':True}]}
    hit = {'file_id':'f', 'file_path':'note.txt', 'status':'accepted', 'confidence':1,
           'value':'13800138000', 'index_start':0, 'index_end':11}
    result = compute(task, [dict(hit), dict(hit)])
    assert result['stats']['findings_applied'] == 1
    assert result['operations']['f'] == [{'mode':'mask', 'start':0, 'end':11}]
    assert '13800138000' not in json.dumps(result['operations'])


def test_mixed_export_uses_measured_audio_and_text_snapshots(tmp_path, monkeypatch):
    task_root = tmp_path / 'tasks'
    task_root.mkdir()
    monkeypatch.setattr(storage, 'TASKS_DIR', task_root)
    monkeypatch.setattr(tasks, 'TASKS_DIR', task_root)
    monkeypatch.setattr(jobs, 'TASKS_DIR', task_root)
    monkeypatch.setattr(policy, 'POLICY_FILE', tmp_path / 'policies.json')
    # Deterministic workflow test only. Real ASR is tested by accept_special.py.
    monkeypatch.setattr(audio, '_transcribe', lambda *args: [])
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'note.txt').write_text('phone 13800138000', encoding='utf-8')
    sf.write(source / 'tone.wav', np.full((32000, 2), .2), 16000)
    with TestClient(app) as client:
        response = client.post('/api/policies', json={'name':'mixed audit', 'mode':'replace',
                               'reversible':True, 'pii_types':['phone'], 'voice_change':'pitch'})
        assert response.status_code == 200, response.text
        pid = response.json()['data']['id']
        created = client.post('/api/tasks', json={'source_path':str(source),'policy_id':pid})
        tid = created.json()['data']['task']['id']
        detected = client.post(f'/api/tasks/{tid}/detect', json={})
        assert detected.status_code == 200, detected.text
        files = detected.json()['data']['task']['files']
        fid = next(f['id'] for f in files if f['name'] == 'tone.wav')
        review = client.post(f'/api/tasks/{tid}/review', json={'add':[
            {'file_id':fid,'media':'audio','start':.25,'end':.5}]})
        assert review.status_code == 200, review.text
        client.post(f'/api/tasks/{tid}/review', json={'accept_all':True}).raise_for_status()
        masked = client.post(f'/api/tasks/{tid}/mask', json={})
        assert masked.status_code == 200, masked.text
        response = client.get(f'/api/tasks/{tid}/export')
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            report = json.loads(archive.read('report.json'))
            markdown = archive.read('report.md').decode()
            assert 'mapping.json' not in archive.namelist()
        assert report['reversibility']['status'] == 'text_only'
        assert report['reversibility']['text_files'] == ['note.txt']
        assert report['reversibility']['media_restoration_supported'] is False
        records = {f['path']:f['processing'] for f in report['files']}
        assert records['note.txt']['modes'] == {'replace':1}
        actual = records['tone.wav']
        assert actual['voice_change'] == {'mode':'pitch','semitones':4,'duration_compensated':True}
        assert actual['mute_padding_seconds'] == .15
        track = actual['audio_tracks'][0]
        assert track['duration_seconds'] == 2
        assert track['sample_rate'] == 16000 and track['channels'] == 2
        assert track['interval_basis'] == 'output_pcm'
        assert track['mute_intervals_seconds'][0] == pytest.approx([.1,.65])
        assert actual['output']['audio_tracks'][0]['codec'] == 'pcm_s16le'
        assert '13800138000' not in json.dumps(report)
        assert str(source) not in json.dumps(report)
        assert '仅实际执行的文本代号替换' in markdown
        saved = storage.load_task(tid)['artifacts']['files'][fid]['processing']
        assert saved == actual


@pytest.mark.parametrize('start,end,target', [(.02,.03,0), (.32,.33,3)])
def test_subframe_manual_region_masks_overlapping_display_frame(tmp_path, start, end, target):
    import av
    from app.services.mask.media import compute_media
    from app.services.media_runtime import run_ffmpeg, probe
    from app.services.processing_record import processing_record
    source = tmp_path / 'short.mp4'
    run_ffmpeg(['-f','lavfi','-i','color=white:size=64x48:rate=10:duration=0.4',
                '-c:v','libx264','-pix_fmt','yuv420p', source])
    file = {'id':'v','path':'short.mp4','abs':str(source),
            'detection':{'metadata':probe(source)}}
    finding = {'file_id':'v','status':'accepted','confidence':1,'media':'video',
               'source':'manual','start':start,'end':end,'bbox':[8,8,16,16]}
    result = compute_media({'files':[file],'policy':{}},[finding])
    output = tmp_path / 'masked.mp4'
    output.write_bytes(result['outputs']['v'][0])
    with av.open(str(output)) as container:
        frames = [f.to_ndarray(format='bgr24') for f in container.decode(video=0)]
    assert len(frames) == 4
    assert frames[target][12:20,12:20].max() < 12
    assert all(f[12:20,12:20].mean() > 200 for i,f in enumerate(frames) if i != target)
    record = processing_record(file,[finding],{},output,media_audit=result['audits']['v'])
    assert record['masked_frames'] == 1
    assert record['regions'][0]['frames_applied'] == 1
