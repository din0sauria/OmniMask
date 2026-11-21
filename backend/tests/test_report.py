"""Successful-output processing records, restoration scope and safe export fields."""
import json
from copy import deepcopy

import numpy as np
import pytest
import soundfile as sf

from app.services.mask.media import compute_media
from app.services.processing_record import processing_record
from app.services.report import build_report, render_markdown
from app.services.media_runtime import probe, run_ffmpeg


def task(files, policy=None):
    return {'id': 'report-test', 'name': 'synthetic', 'status': 'masked',
            'created_at': 'test', 'updated_at': 'test', 'files': files,
            'policy': {'id': 'test', 'name': 'test', 'mode': 'replace', 'reversible': True,
                       **(policy or {})}, 'revision': 2,
            'artifacts': {'revision': 2, 'files': {}}}


def fi(path='sample.txt', id='f1'):
    return {'id': id, 'path': path, 'size': 100, 'is_text': path.endswith('.txt'),
            'detection': {'state': 'complete', 'metadata': {}}}


def finding(file_id='f1', **extra):
    return {'file_id': file_id, 'file_path': 'sample.txt', 'status': 'accepted',
            'confidence': 1, 'value': 'SYNTHETIC_PRIVATE_VALUE', 'match': 'SYNTHETIC_PRIVATE_VALUE',
            'rule_name': 'test', 'type_label': 'synthetic type', **extra}


def save_record(t, file, record):
    t['artifacts']['files'][file['id']] = {'processing': record}


def test_audio_report_records_actual_pcm_intervals_and_output_codec(tmp_path):
    source = tmp_path / 'source.wav'
    sf.write(source, np.ones((16000, 2)) * .2, 16000)
    file = fi('audio.wav')
    file['abs'] = str(source)
    file['detection']['metadata'] = {'duration': 1, 'audio_tracks': [{'track_index': 0}]}
    hits = [finding(media='audio', start=.05, end=.3),
            finding(media='audio', start=.7, end=.8, confidence=.1),
            finding(media='audio', start=.7, end=.8, status='rejected')]
    policy = {'mode': 'replace', 'reversible': True, 'min_confidence': .8,
              'voice_change': 'robot', 'crypto_key': 'SYNTHETIC_PRIVATE_KEY'}
    result = compute_media({'files': [file], 'policy': policy}, hits)
    output = tmp_path / 'output.wav'
    output.write_bytes(result['outputs']['f1'][0])
    record = processing_record(file, hits, policy, output, media_audit=result['audits']['f1'])
    track = record['audio_tracks'][0]
    assert record['filter']['eligible_findings'] == 1
    assert record['filter']['excluded_findings'] == 2
    assert track['mute_intervals_seconds'][0] == pytest.approx([0, .45])
    assert track['duration_seconds'] == 1 and track['channels'] == 2
    assert track['interval_basis'] == 'output_pcm'
    assert record['mute_padding_seconds'] == .15
    assert record['voice_change'] == {'mode': 'robot', 'carrier_hz': 400}
    assert record['output']['audio_tracks'][0]['codec'] == 'pcm_s16le'
    t = task([file], policy)
    save_record(t, file, record)
    report = build_report(t, hits)
    assert report['policy']['reversible'] is False
    assert report['reversibility']['status'] == 'none'
    rendered = render_markdown(report)
    assert '媒体' in rendered and '不提供逆向恢复' in rendered
    serialized = json.dumps(report, ensure_ascii=False)
    assert 'SYNTHETIC_PRIVATE_VALUE' not in serialized
    assert 'SYNTHETIC_PRIVATE_KEY' not in serialized
    assert str(tmp_path) not in serialized


def test_text_operations_use_actual_masker_results_not_eligible_counts():
    file = fi()
    hits = [finding(), finding(), finding()]
    t = task([file])
    # Three eligible overlapping detections resulted in only one actual replacement.
    record = processing_record(file, hits, t['policy'], 'output.txt',
                               text_operations=[{'mode': 'replace', 'start': 10, 'end': 20}])
    save_record(t, file, record)
    report = build_report(t, hits)
    assert record['filter']['eligible_findings'] == 3
    assert record['applied_operations'] == 1
    assert record['mapping_reversible_operations'] == 1
    assert report['reversibility']['text_mapping_operations'] == 1
    assert report['reversibility']['status'] == 'text_only'


@pytest.mark.parametrize('mode', ['encrypt', 'offset', 'mask', 'delete'])
def test_other_text_modes_are_not_reported_as_mapping_reversible(mode):
    file = fi()
    t = task([file], {'mode': mode})
    record = processing_record(file, [finding()], t['policy'], 'output.txt',
                               text_operations=[{'mode': mode, 'start': 0, 'end': 5}])
    save_record(t, file, record)
    report = build_report(t, [finding()])
    assert report['policy']['reversible_requested'] is True
    assert report['policy']['reversible'] is False
    assert report['reversibility']['status'] == 'none'


def test_mixed_report_limits_mapping_to_actual_text_operations():
    text, video = fi(), fi('sample.mp4', 'f2')
    t = task([text, video])
    save_record(t, text, processing_record(text, [finding()], t['policy'], 'output.txt',
                                          text_operations=[{'mode': 'replace', 'start': 0, 'end': 5}]))
    save_record(t, video, {'recorded': True, 'media': 'video', 'mapping_reversible_operations': 0})
    report = build_report(t, [finding()])
    assert report['reversibility']['status'] == 'text_only'
    assert report['reversibility']['text_files'] == ['sample.txt']
    assert report['reversibility']['media_restoration_supported'] is False
    assert '仅实际执行的文本代号替换' in render_markdown(report)


def test_old_or_invalidated_artifacts_are_unknown_not_reconstructed():
    file = fi()
    t = task([file])
    report = build_report(t, [finding()])
    assert report['reversibility']['status'] == 'unknown'
    assert report['files'][0]['processing']['recorded'] is False
    assert '未记录' in render_markdown(report)
    save_record(t, file, {'recorded': True, 'mapping_reversible_operations': 1})
    t['revision'] = 3
    assert build_report(t, [finding()])['reversibility']['status'] == 'unknown'


def test_missing_text_operation_audit_is_unknown():
    file = fi()
    t = task([file])
    save_record(t, file, processing_record(file, [finding()], t['policy'], 'output.txt'))
    report = build_report(t, [finding()])
    assert report['files'][0]['processing']['operations'] is None
    assert report['reversibility']['status'] == 'unknown'


def test_report_does_not_serialize_internal_snapshot_fields():
    file = fi()
    t = task([file])
    save_record(t, file, {'recorded': True, 'mapping_reversible_operations': 0,
                         'value': 'PRIVATE_VALUE', 'crypto_key': 'PRIVATE_KEY',
                         'output': {'format': 'text', 'source_path': 'C:/PRIVATE_SOURCE'},
                         'operations': [{'mode': 'mask', 'start': 0, 'end': 1, 'value': 'PRIVATE_VALUE'}]})
    encoded = json.dumps(build_report(t, [finding()]))
    assert 'PRIVATE_VALUE' not in encoded
    assert 'PRIVATE_KEY' not in encoded
    assert 'PRIVATE_SOURCE' not in encoded


def test_processing_snapshot_is_not_recomputed_from_changed_policy():
    file = fi()
    t = task([file])
    record = processing_record(file, [finding()], t['policy'], 'output.txt',
                               text_operations=[{'mode': 'replace', 'start': 0, 'end': 5}])
    save_record(t, file, record)
    before = deepcopy(record)
    t['policy'].update(mode='delete', reversible=False)
    report = build_report(t, [finding()])
    assert report['files'][0]['processing'] == before
    assert report['reversibility']['text_mapping_operations'] == 1


def test_video_report_uses_short_track_pcm_audit_not_video_duration(tmp_path):
    source, output = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    run_ffmpeg(['-f', 'lavfi', '-i', 'color=c=white:s=64x48:r=10:d=1',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=0.4',
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', source])
    file = fi('sample.mp4')
    file['abs'] = str(source)
    file['detection']['metadata'] = probe(source)
    hits = [finding(media='video', start=.7, end=.9, track_index=0)]
    policy = {'mode': 'replace', 'reversible': True}
    result = compute_media({'files': [file], 'policy': policy}, hits)
    output.write_bytes(result['outputs']['f1'][0])
    record = processing_record(file, hits, policy, output, media_audit=result['audits']['f1'])
    track = record['audio_tracks'][0]
    assert track['duration_seconds'] < .55
    assert track['mute_intervals_seconds'] == []
    assert track['interval_basis'] == 'processed_pcm_before_aac'
    assert record['frames_processed'] == 10
    assert record['output']['video_codec'] == 'h264'
    assert record['output']['audio_tracks'][0]['codec'] == 'aac'
    assert record['mapping_reversible_operations'] == 0
