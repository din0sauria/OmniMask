"""Small real-tool/model edge-case acceptance, no model download or training.

Run from repo root: backend/.venv/Scripts/python.exe backend/scripts/accept_special.py
Synthetic fixtures are not an accuracy/robustness benchmark.
"""
import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
for tool in ('ffmpeg', 'ffprobe'):
    local = ROOT / '.tools/ffmpeg' / (tool + '.exe')
    if local.exists():
        os.environ.setdefault('OMNIMASK_' + tool.upper(), str(local))

import av
import cv2
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

from app.services.media_runtime import probe, run_ffmpeg, video_frames
from app.services.mask.media import mask_video_bytes


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_sync_fixture(directory, mode):
    """Flash and 2 audio pulses share an event, despite nonzero stream starts."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rate = Fraction(30000, 1001) if mode == 'ntsc' else Fraction(25)
    times = [Fraction(i, 1) / rate for i in range(60)] if mode == 'ntsc' else [Fraction(str(x)) for x in (0,.04,.16,.2,.36,.60,.96,1.08,1.4,1.64)]
    flash_index = 30 if mode == 'ntsc' else 6
    event = float(times[flash_index])
    duration = float(times[-1] + 1 / rate)
    visual = directory / f'{mode}-visual.mp4'
    with av.open(str(visual), 'w') as container:
        stream = container.add_stream('libx264', rate=rate)
        stream.width, stream.height, stream.pix_fmt = 96, 64, 'yuv420p'
        stream.codec_context.max_b_frames = 0
        stream.codec_context.time_base = Fraction(1, 90000)
        stream.time_base = Fraction(1, 90000)
        for i, ts in enumerate(times):
            pixels = np.full((64, 96, 3), 24, np.uint8)
            if i == flash_index:
                pixels[24:48, 36:60] = 255
            frame = av.VideoFrame.from_ndarray(pixels, format='bgr24')
            frame.pts, frame.time_base = round(ts * 90000), Fraction(1, 90000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    # The original visual timeline starts at 2s; independent audio starts later.
    origin = 2.0
    offsets = (.25, .10)
    args = ['-copyts', '-itsoffset', str(origin), '-i', visual]
    for track, offset in enumerate(offsets):
        sr = 48000
        samples = np.zeros(round((duration - offset) * sr), np.float32)
        start = round((event - offset) * sr)
        pulse = np.sin(2*np.pi*(1000 + track*600)*np.arange(round(.025*sr))/sr) * .7
        samples[start:start+len(pulse)] = pulse
        wav = directory / f'{mode}-pulse-{track}.wav'
        sf.write(wav, samples, sr, subtype='PCM_16')
        args += ['-itsoffset', str(origin + offset), '-i', wav]
    source = directory / f'{mode}-offsets.mp4'
    run_ffmpeg([*args, '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0',
                '-c:v', 'copy', '-c:a', 'aac', '-avoid_negative_ts', 'disabled', source])
    return source, {'mode': mode, 'expected_event_seconds': event,
                    'expected_frames': len(times), 'nominal_rate': str(rate),
                    'requested_video_origin': origin, 'audio_start_offsets': list(offsets)}


def measure_sync(path, directory):
    """Measure event onset from decoded flash pixels and audible sample PTS."""
    flashes = []
    frame_times = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        origin = None
        for frame in container.decode(stream):
            absolute = float(frame.pts * frame.time_base)
            origin = absolute if origin is None else origin
            frame_times.append(absolute - origin)
            pixels = frame.to_ndarray(format='bgr24')
            if pixels[28:44,40:56].mean() > 180:
                flashes.append(absolute - origin)
    if len(flashes) != 1:
        raise AssertionError(f'Expected one flash, got {flashes}')
    pulses = []
    metadata = probe(path)
    for track in metadata['audio_tracks']:
        with av.open(str(path)) as container:
            stream = container.streams.audio[track['track_index']]
            onset = None
            for frame in container.decode(stream):
                samples = frame.to_ndarray()
                # planar or packed mono, both yield one channel here.
                above = np.flatnonzero(np.abs(samples).reshape(-1) > .12)
                if len(above):
                    onset = float(frame.pts * frame.time_base) + int(above[0]) / frame.sample_rate - origin
                    break
            if onset is None:
                raise AssertionError('No decoded audio pulse')
            pulses.append(onset)
    return {'flash_seconds': flashes[0], 'pulse_seconds_by_track': pulses,
            'av_offset_seconds_by_track': [x-flashes[0] for x in pulses],
            'frame_times': frame_times, 'frames': len(frame_times),
            'video_origin': origin, 'metadata': metadata}


def sync_case(directory, mode):
    source, spec = make_sync_fixture(directory, mode)
    before = sha(source)
    original = measure_sync(source, directory)
    output = Path(directory) / f'{mode}-masked.mp4'
    # Apply a real corner mask while leaving the independent flash marker intact.
    hit = {'status':'accepted', 'confidence':1, 'source':'manual', 'bbox':[0,0,12,12],
           'start':0, 'end':probe(source)['duration'], 'media':'video'}
    output.write_bytes(mask_video_bytes(source, [hit], {}))
    processed = measure_sync(output, directory)
    assert processed['frames'] == spec['expected_frames']
    assert len(processed['pulse_seconds_by_track']) == 2
    assert all(abs(x) <= .1 for x in original['av_offset_seconds_by_track'])
    assert all(abs(x) <= .1 for x in processed['av_offset_seconds_by_track'])
    assert np.allclose(original['frame_times'], processed['frame_times'], atol=.001)
    assert sha(source) == before
    return {'spec':spec, 'source_sha256':before, 'output_sha256':sha(output),
            'source_measurement':original, 'processed_measurement':processed,
            'passed':True}


def make_visual_fixture(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    qr = cv2.QRCodeEncoder_create().encode('https://example.com/synthetic-transient')
    qr = cv2.copyMakeBorder(qr,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
    qr = cv2.resize(qr,(136,136),interpolation=cv2.INTER_NEAREST)
    font_path = Path('C:/Windows/Fonts/arial.ttf')
    font = ImageFont.truetype(str(font_path),36) if font_path.exists() else ImageFont.load_default(size=36)
    expected = {i:[] for i in range(10)}
    source = directory / 'transient-moving.mp4'
    with av.open(str(source),'w') as container:
        stream = container.add_stream('libx264',rate=5)
        stream.width,stream.height,stream.pix_fmt = 640,360,'yuv420p'
        stream.codec_context.max_b_frames = 0
        for i in range(10):
            image = Image.new('RGB',(640,360),'white')
            draw = ImageDraw.Draw(image)
            if i in (0,4,9):
                draw.text((20+i*8,25),'13912345678',font=font,fill='black')
                expected[i].append('phone')
            if i in (0,3,4,9):
                image.paste(Image.fromarray(qr).convert('RGB'),(25+i*35,170))
                expected[i].append('qr_code')
            frame = av.VideoFrame.from_image(image)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return source, expected


def visual_case(directory):
    from app.services.detect.video import detect_video_detail
    source, expected = make_visual_fixture(directory)
    before = sha(source)
    hits, detail = detect_video_detail(source, {'pii_types':['phone'], 'use_model':False})
    per_frame = []
    missed = []
    for frame, targets in expected.items():
        found = sorted({h['rule_name'] for h in hits if h.get('frame_index') == frame})
        missing = sorted(set(targets) - set(found))
        missed.extend({'frame_index':frame, 'rule_name':rule} for rule in missing)
        per_frame.append({'frame_index':frame,'expected':targets,'detected_rules':found,'missed':missing})
    for hit in hits:
        hit['status'] = 'accepted'
    output = Path(directory) / 'transient-moving-masked.mp4'
    output.write_bytes(mask_video_bytes(source,hits,{}))
    residual, residual_detail = detect_video_detail(output, {'pii_types':['phone'], 'use_model':False})
    residual = [{'frame_index':h.get('frame_index'),'rule_name':h['rule_name']} for h in residual
                if h['rule_name'] in ('phone','qr_code')]
    result = {'source_sha256':before,'source_unchanged':sha(source)==before,
              'expected_target_instances':sum(map(len,expected.values())),
              'detected_target_instances':sum(len(set(p['expected']) & set(p['detected_rules'])) for p in per_frame),
              'per_frame':per_frame,'missed':missed,'residual_detector_hits':residual,
              'coverage':detail['state'],'frames_scanned':detail['metadata']['frames_scanned'],
              'output_coverage':residual_detail['state'],
              'passed':not missed and not residual and sha(source)==before and detail['state']=='complete'}
    # Keep automatic misses visible, then separately prove the manual recovery.
    supplements = []
    for missing in missed:
        i = missing['frame_index']
        if missing['rule_name'] == 'qr_code':
            supplements.append({'status':'accepted','confidence':1,'source':'manual','media':'video',
                                'bbox':[25+i*35,170,136,136],'start':i/5,'end':(i+1)/5})
    if supplements:
        corrected = Path(directory) / 'transient-moving-manual-corrected.mp4'
        corrected.write_bytes(mask_video_bytes(source,[*hits,*supplements],{}))
        remaining, corrected_detail = detect_video_detail(corrected,{'pii_types':['phone'],'use_model':False})
        remaining = [{'frame_index':h.get('frame_index'),'rule_name':h['rule_name']} for h in remaining
                     if h['rule_name'] in ('phone','qr_code')]
        result['manual_recovery'] = {'supplemented_regions':len(supplements),
                                     'residual_detector_hits':remaining,
                                     'passed':not remaining and corrected_detail['state']=='complete',
                                     'note':'Known synthetic coordinates simulate an explicit reviewer addition; does not repair automatic recall.'}
    return result


def audio_cases(directory):
    from app.services.detect.audio import detect_audio_detail
    from fastapi.testclient import TestClient
    from app.main import app
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    for kind in ('silence','non_speech_tone'):
        source = directory / f'{kind}.wav'
        sr = 16000
        samples = np.zeros(sr*3,np.float32) if kind=='silence' else .2*np.sin(2*np.pi*440*np.arange(sr*3)/sr)
        sf.write(source,samples,sr,subtype='PCM_16')
        before = sha(source)
        hits, detail = detect_audio_detail(source, {'pii_types':['phone','email']})
        results.append({'case':kind,'passed':not hits and not detail['transcript'] and detail['state']=='complete',
                        'source_sha256':before,'source_unchanged':sha(source)==before,
                        'findings':len(hits),'transcript':detail['transcript'],'state':detail['state']})
    source = directory / 'corrupt.wav'
    source.write_bytes(b'RIFF\x00\x00\x00\x00WAVEcorrupt-not-valid-audio')
    before = sha(source)
    with TestClient(app) as client:
        created = client.post('/api/tasks',json={'source_path':str(source),'policy_id':'media'})
        created.raise_for_status()
        task = created.json()['data']['task']['id']
        detected = client.post(f'/api/tasks/{task}/detect',json={})
        detected.raise_for_status()
        info = detected.json()['data']['task']
        state = info['files'][0]['detection']
        mask = client.post(f'/api/tasks/{task}/mask',json={})
        export = client.get(f'/api/tasks/{task}/export')
        results.append({'case':'corrupt_audio_full_api_flow','state':state,
                        'mask_status':mask.status_code,'export_status':export.status_code,
                        'source_sha256':before,'source_unchanged':sha(source)==before,
                        'passed':state['state']=='failed' and mask.status_code==409 and export.status_code==409,
                        'note':'Rejected during media preparation before ASR; invalid audio is not counted as no PII.'})
    return results


def main():
    work = ROOT / '.validation/special-samples'
    work.mkdir(parents=True,exist_ok=True)
    os.environ.setdefault('OMNIMASK_ASR_MODEL',str(ROOT/'models/faster-whisper-base'))
    # Set isolated task storage before importing app modules that initialize config.
    from app.core import config, storage
    from app.api import tasks
    from app.services import jobs, policy
    data = ROOT / '.validation/special-data'
    task_root = data / 'tasks'
    task_root.mkdir(parents=True,exist_ok=True)
    config.TASKS_DIR = storage.TASKS_DIR = tasks.TASKS_DIR = jobs.TASKS_DIR = task_root
    policy.POLICY_FILE = data / 'policies.json'
    results = {'synthetic_fixtures':True,'real_components':True,'not_accuracy_benchmark':True,
               'cases':[],'failures':[],'first_pass_failures':[],'known_limitations':[],
               'limitations':['No natural/noisy/accented speech benchmark.','Known repeated-zero ASR deletion remains in prior acceptance evidence.','Detector residual check is not a privacy proof.']}
    started = time.monotonic()
    from importlib.metadata import version
    results['versions'] = {name:version(name) for name in ('av','faster-whisper','opencv-python','rapidocr-onnxruntime')}
    manifest = ROOT / 'models/faster-whisper-base/download-manifest.json'
    if manifest.exists(): results['asr_model']=json.loads(manifest.read_text(encoding='utf-8'))
    cases = [('sync_ntsc',lambda:sync_case(work,'ntsc')),
             ('sync_vfr',lambda:sync_case(work,'vfr')),
             ('real_transient_moving_ocr_qr',lambda:visual_case(work)),
             ('real_asr_boundaries',lambda:{'results':audio_cases(work)})]
    for name, callback in cases:
        print('RUN',name,flush=True)
        try:
            result = callback()
            if name=='real_asr_boundaries': result['passed']=all(x['passed'] for x in result['results'])
            result['case']=name
            results['cases'].append(result)
            known_miss = (name=='real_transient_moving_ocr_qr' and result.get('missed')
                          and result.get('manual_recovery',{}).get('passed')
                          and result['source_unchanged'] and result['coverage']=='complete')
            if known_miss:
                failure = {'case':name,'missed':result['missed'],
                           'residual_detector_hits':result['residual_detector_hits'],
                           'expected':result['expected_target_instances'],
                           'detected':result['detected_target_instances'],
                           'manual_recovery_passed':True}
                results['first_pass_failures'].append(failure)
                results['known_limitations'].append(failure)
                result['engineering_passed'] = True
            elif not result['passed']:
                results['failures'].append({'case':name,'reason':'See recorded measurements'})
            print('KNOWN_DETECTOR_MISS' if known_miss else ('PASS' if result['passed'] else 'FAIL'),name,flush=True)
        except Exception as exc:
            results['failures'].append({'case':name,'error':str(exc),'type':type(exc).__name__})
            print('FAIL',name,type(exc).__name__,str(exc),flush=True)
    results['elapsed_seconds']=time.monotonic()-started
    output=ROOT/'.validation/special-results.json'
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT',output,'engineering_failures',len(results['failures']),
          'first_pass_detector_failures',len(results['first_pass_failures']),flush=True)
    return 1 if results['failures'] else 0


if __name__=='__main__':
    raise SystemExit(main())
