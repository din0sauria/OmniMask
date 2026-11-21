"""Opt-in real-model acceptance on synthetic local fixtures (no downloads).

Run from the repository root with backend/.venv/Scripts/python.exe
backend/scripts/accept_media.py. Speech fixtures are made by make_speech_samples.ps1.
These few examples are integration evidence, not an accuracy benchmark.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.validation'
SAMPLES = WORK / 'acceptance-samples'
EXPORTS = WORK / 'acceptance-exports'
for directory in (WORK, SAMPLES, EXPORTS):
    directory.mkdir(parents=True, exist_ok=True)
os.environ['OMNIMASK_DATA'] = str(WORK / 'acceptance-data')
os.environ.setdefault('OMNIMASK_ASR_MODEL', str(ROOT / 'models/faster-whisper-base'))
for tool in ('ffmpeg', 'ffprobe'):
    os.environ.setdefault('OMNIMASK_' + tool.upper(), str(ROOT / '.tools/ffmpeg' / (tool + '.exe')))
sys.path.insert(0, str(ROOT / 'backend'))

import av
import cv2
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from app.main import app
from app.services.media_runtime import run_ffmpeg, probe, video_frames, extract_track
from app.services.detect.audio import detect_audio_detail


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked(response):
    assert response.status_code == 200, f'{response.status_code}: {response.text[:300]}'
    return response.json()['data']


def generate():
    # QR detection is a real detector; large text also exercises actual OCR.
    qr = cv2.QRCodeEncoder_create().encode('https://example.com/synthetic-privacy-demo')
    qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    qr = cv2.resize(qr, (152, 152), interpolation=cv2.INTER_NEAREST)
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    font = ImageFont.truetype(str(font_path), 40) if font_path.exists() else ImageFont.load_default(size=40)
    def frame(index):
        image = Image.new('RGB', (640, 360), 'white')
        draw = ImageDraw.Draw(image)
        draw.text((22, 25), '13912345678', font=font, fill='black')
        x, y = 28 + index * 7, 150
        image.paste(Image.fromarray(qr).convert('RGB'), (x, y))
        return image
    frame(0).save(SAMPLES / 'privacy-image.png')
    visual = SAMPLES / 'visual-only.mp4'
    with av.open(str(visual), 'w') as container:
        stream = container.add_stream('libx264', rate=3)
        stream.width, stream.height, stream.pix_fmt = 640, 360, 'yuv420p'
        for index in range(31):
            video = av.VideoFrame.from_image(frame(index))
            for packet in stream.encode(video):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    speech = WORK / 'samples/speech_zh_digits.wav'
    english = WORK / 'samples/speech_en.wav'
    assert speech.is_file() and english.is_file(), 'Generate speech fixtures first'
    video = SAMPLES / 'privacy-video.mp4'
    run_ffmpeg(['-i', visual, '-i', speech, '-i', english,
                '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0', '-c:v', 'copy', '-c:a', 'aac', video])
    (SAMPLES / 'sample.txt').write_text('Synthetic phone: 13912345678; email: demo@example.com', encoding='utf-8')
    (SAMPLES / 'sample.py').write_text('demo_api_key = "OMNI_DEMO_1234567890ABCDEFG"\n', encoding='utf-8')
    return speech, video


def flow(client, source, policy='media', expected=None):
    before = sha(source)
    task = checked(client.post('/api/tasks', json={'source_path': str(source), 'policy_id': policy,
                                                  'name': 'Acceptance ' + source.name,
                                                  'operator': 'synthetic acceptance', 'purpose': 'local integration test'}))['task']
    tid = task['id']
    started = time.monotonic()
    detected = checked(client.post(f'/api/tasks/{tid}/detect', json={}))['task']
    findings = checked(client.get(f'/api/tasks/{tid}/findings'))['findings']
    assert all(f['detection']['state'] == 'complete' for f in detected['files']), detected['stats']['warnings']
    if expected:
        assert expected(findings), 'Expected detector result not present'
    file = detected['files'][0]
    checked(client.post(f'/api/tasks/{tid}/review', json={'accept_all': True}))
    masked = checked(client.post(f'/api/tasks/{tid}/mask', json={}))['task']
    preview = checked(client.get(f'/api/tasks/{tid}/files/{file["id"]}'))
    response = client.get(f'/api/tasks/{tid}/export')
    assert response.status_code == 200, response.text[:300]
    export = EXPORTS / (source.name + '.zip')
    export.write_bytes(response.content)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = [n for n in archive.namelist() if n.startswith('masked/')]
        assert len(names) == 1
        data = archive.read(names[0])
        if preview.get('media'):
            result = client.get(preview['masked_url'])
            assert result.status_code == 200
            assert data == result.content, 'Preview/export bytes diverged'
        else:
            assert data.decode() == preview['masked']
        report_text = archive.read('report.json').decode()
        assert str(ROOT) not in report_text and str(ROOT).replace('\\', '\\\\') not in report_text
        for value in ('13912345678', 'demo@example.com', 'OMNI_DEMO_1234567890ABCDEFG'):
            assert value not in report_text, 'Report contains raw sensitive fixture value'
        assert 'mapping.json' not in archive.namelist()
        output = EXPORTS / Path(names[0]).name
        output.write_bytes(data)
    assert sha(source) == before, 'Source file was modified'
    return {'task_id': tid, 'source': source.name, 'findings': findings,
            'detection': file['detection'], 'source_sha256': before,
            'elapsed_seconds': round(time.monotonic() - started, 3), 'output': str(output),
            'export': str(export), 'mask_stats': masked['stats']['mask']}


def validate_audio(result):
    data, rate = sf.read(result['output'], always_2d=True)
    hits = [f for f in result['findings'] if f.get('start') is not None]
    assert hits
    for hit in hits:
        core = data[int(hit['start'] * rate):int(hit['end'] * rate)]
        assert len(core) and np.max(abs(core)) == 0, 'Sensitive audio interval not fully muted'
    return {'target_intervals': len(hits), 'all_channels_muted': True}


def validate_video(result, source):
    metadata = probe(Path(result['output']))
    with av.open(result['output']) as encoded:
        assert encoded.streams.video[0].codec_context.name == 'h264'
        assert all(track.codec_context.name == 'aac' for track in encoded.streams.audio)
    assert len(metadata['audio_tracks']) == 2
    source_meta = probe(source)
    assert abs(metadata['duration'] - source_meta['duration']) <= .1
    count = 0
    by_frame = {}
    for hit in result['findings']:
        if hit.get('bbox'):
            by_frame.setdefault(hit['frame_index'], []).append(hit['bbox'])
    for index, frame, ts, image in video_frames(Path(result['output'])):
        count += 1
        assert index in by_frame, 'Generated visible QR frame was missed'
        for x, y, w, h in by_frame[index]:
            # Avoid encoder boundary ringing while checking the entire region core.
            roi = image[int(y)+3:int(y+h)-3, int(x)+3:int(x+w)-3]
            assert roi.size and np.max(roi) < 30, 'Detected frame region remains visible'
    assert count == 31 and result['detection']['metadata']['frames_scanned'] == count
    decoded = []
    for track in metadata['audio_tracks']:
        target = EXPORTS / f"video-track-{track['track_index']}.wav"
        extract_track(Path(result['output']), track, target, metadata)
        signal, rate = sf.read(target, always_2d=True)
        decoded.append((signal, rate))
    hits = [f for f in result['findings'] if f.get('start') is not None and not f.get('bbox')]
    assert hits, 'Video ASR did not find sensitive speech'
    for hit in hits:
        signal, rate = decoded[hit.get('track_index', 0)]
        assert np.max(abs(signal[int(hit['start']*rate):int(hit['end']*rate)])) < .005
    assert np.sqrt(np.mean(decoded[1][0] ** 2)) > .005, 'Unselected English track was silenced'
    reference = EXPORTS / 'source-second-track.wav'
    extract_track(source, source_meta['audio_tracks'][1], reference, source_meta)
    original, rate = sf.read(reference, always_2d=True)
    assert rate == decoded[1][1]
    original, processed = original[:,0], decoded[1][0][:,0]
    size = min(len(original), len(processed))
    correlation = float(np.corrcoef(original[:size], processed[:size])[0,1])
    assert correlation > .95, 'Unselected audio track differs beyond expected AAC re-encoding'
    return {'decoded_frames': count, 'protected_frames': len(by_frame), 'audio_tracks': 2,
            'second_track_retained': True, 'second_track_correlation': correlation,
            'duration_error_seconds': abs(metadata['duration']-source_meta['duration'])}


def main():
    results = {'synthetic_fixtures': True, 'real_models': True,
               'not_an_accuracy_benchmark': True, 'cases': [], 'limitations': [], 'failures': []}
    try:
        speech, video = generate()
        with TestClient(app) as client:
            cases = [('text', SAMPLES/'sample.txt', 'text-pii'),
                     ('image', SAMPLES/'privacy-image.png', 'media'),
                     ('audio', speech, 'media'), ('video', video, 'media')]
            cases.append(('code_keyleak_baseline', SAMPLES/'sample.py', 'delete'))
            for kind, source, policy in cases:
                print('RUN', kind, flush=True)
                try:
                    result = flow(client, source, policy, expected=lambda hits: len(hits)>0)
                    if kind == 'audio':
                        result['verification'] = validate_audio(result)
                    if kind == 'video':
                        result['verification'] = validate_video(result, source)
                    result.update(case=kind, passed=True)
                    results['cases'].append(result)
                    print('PASS', kind, 'hits', len(result['findings']), 'seconds', result['elapsed_seconds'], flush=True)
                except Exception as exc:
                    results['failures'].append({'case': kind, 'error': str(exc), 'type': type(exc).__name__})
                    print('FAIL', kind, type(exc).__name__, str(exc)[:200], flush=True)
        # Actual repeated-zero ASR result stays visible even if it does not pass.
        zero_source = WORK/'samples/speech_zh.wav'
        hits, detail = detect_audio_detail(zero_source, {'pii_types':['phone']})
        results['limitations'].append({'case': 'repeated_zero_chinese_speech', 'expected': '13800138000',
                                      'detected': bool(hits), 'findings': hits, 'transcript': detail['transcript'],
                                      'note': 'Known base-model digit deletion; must use transcript review/manual interval correction. Do not fabricate missing digits.'})
        english_hits, english_detail = detect_audio_detail(WORK/'samples/speech_en.wav', {'pii_types':['phone']})
        results['cases'].append({'case':'english_transcription_compatibility', 'passed':bool(english_detail['transcript']),
                                 'transcript':english_detail['transcript'], 'findings':english_hits,
                                 'note':'Non-sensitive English fixture; not evidence of English PII recall.'})
    except Exception as exc:
        results['failures'].append({'case':'setup', 'error':str(exc), 'type':type(exc).__name__})
        raise
    finally:
        output = WORK/'acceptance-results.json'
        output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print('RESULTS', output, 'failures',len(results['failures']),flush=True)
    return 1 if results['failures'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
