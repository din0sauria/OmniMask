"""Media processing; no failed operation may substitute the original file."""
import io
import math
import tempfile
from fractions import Fraction
from pathlib import Path

from .code import eligible
from ..media import kind_of
from ..media_runtime import MediaError, check, progress, probe, video_frames, run_ffmpeg, extract_track


def _cv():
    import cv2
    return cv2


def _accepted(findings):
    return [f for f in findings if f.get('status') == 'accepted']


def _boxes(image, boxes):
    # Opaque fill avoids the weak protection of blurring small/high-contrast text.
    h, w = image.shape[:2]
    for box in boxes:
        x, y, bw, bh = [int(round(float(n))) for n in box]
        x0, y0, x1, y1 = max(0,x), max(0,y), min(w,x+bw), min(h,y+bh)
        if x1 <= x0 or y1 <= y0:
            raise MediaError('处理区域超出画面，请重新调整')
        image[y0:y1, x0:x1] = 0
    return image


def mask_image_bytes(path, findings, policy, context=None):
    check(context)
    boxes = [f['bbox'] for f in _accepted(findings) if f.get('bbox')]
    if not boxes:
        return Path(path).read_bytes()
    import numpy as np
    img = _cv().imdecode(np.frombuffer(Path(path).read_bytes(), dtype=np.uint8), _cv().IMREAD_COLOR)
    if img is None:
        raise MediaError('图像解码失败')
    ok, buf = _cv().imencode(Path(path).suffix or '.png', _boxes(img, boxes))
    if not ok:
        raise MediaError('图像编码失败')
    return buf.tobytes()


def _read_audio(path, context=None):
    import soundfile as sf
    try:
        return sf.read(str(path), dtype='float32')
    except Exception:
        with tempfile.TemporaryDirectory(prefix='omnimask_audio_') as td:
            out = Path(td) / 'decoded.wav'
            run_ffmpeg(['-i', path, '-map', '0:a:0', '-vn', '-c:a', 'pcm_f32le', out], context)
            try:
                return sf.read(str(out), dtype='float32')
            except Exception as exc:
                raise MediaError('音频解码失败') from exc


def mute_intervals(findings, duration, padding=.15):
    intervals = []
    for f in _accepted(findings):
        if f.get('bbox') or f.get('start') is None or f.get('end') is None:
            continue
        start, end = float(f['start']), float(f['end'])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise MediaError('静音时间区间无效')
        a, b = max(0., start-padding), min(duration, end+padding)
        if a < b:
            intervals.append((a,b))
    merged = []
    for a,b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    return merged


def _pitch(data, sr, steps, context=None):
    import numpy as np
    import soundfile as sf
    if not steps or len(data) == 0:
        return data
    factor = 2 ** (steps/12)
    with tempfile.TemporaryDirectory(prefix='omnimask_pitch_') as td:
        inp, out = Path(td)/'input.wav', Path(td)/'output.wav'
        sf.write(inp, data, sr, subtype='FLOAT')
        run_ffmpeg(['-i', inp, '-af', f'asetrate={sr}*{factor},aresample={sr},atempo={1/factor}',
                    '-c:a','pcm_f32le',out], context)
        shifted, actual_sr = sf.read(out,dtype='float32')
        if actual_sr != sr:
            raise MediaError('变声采样率异常')
        # atempo may trim a few samples; do not alter the media timeline.
        if len(shifted) < len(data):
            pads = [(0,len(data)-len(shifted))] + ([(0,0)] if shifted.ndim==2 else [])
            shifted=np.pad(shifted,pads)
        return shifted[:len(data)]


def mask_audio_bytes(path, findings, policy, context=None, audit=None):
    import numpy as np
    import soundfile as sf
    check(context)
    data, sr = _read_audio(path, context)
    data = data.copy()
    voice = policy.get('voice_change','')
    if voice == 'pitch':
        data = _pitch(data,sr,4,context)
    elif voice == 'robot':
        carrier=.5+.5*np.sin(2*np.pi*400*np.arange(len(data))/sr)
        data *= carrier[:,None] if data.ndim==2 else carrier
    elif voice:
        raise MediaError('不支持的变声效果')
    # Mute AFTER voice transforms so atempo cannot spread nearby speech into gaps.
    intervals = mute_intervals(findings,len(data)/sr,float(policy.get('mute_padding',.15)))
    actual_intervals = []
    for a,b in intervals:
        start, end = max(0,math.floor(a*sr)), min(len(data),math.ceil(b*sr))
        data[start:end] = 0
        actual_intervals.append([start/sr, end/sr])
    if audit is not None:
        audit.update(duration=len(data)/sr, sample_rate=sr,
                     channels=data.shape[1] if data.ndim == 2 else 1,
                     mute_intervals=actual_intervals)
    check(context)
    out=io.BytesIO()
    sf.write(out,data,sr,format='WAV',subtype='PCM_16')
    return out.getvalue()


def _visual_findings(findings, index, ts, frame_end=None):
    selected=[]
    for f in _accepted(findings):
        if not f.get('bbox'):
            continue
        if f.get('source') == 'manual' and f.get('start') is not None and f.get('end') is not None:
            # A requested interval can be shorter than a displayed frame. Mask
            # every frame whose display span overlaps it, including boundaries.
            overlaps = (float(f['start']) < frame_end and float(f['end']) > ts) if frame_end is not None else float(f['start']) <= ts < float(f['end'])
            if overlaps:
                selected.append(f)
        elif f.get('frame_index') is not None:
            if int(f['frame_index']) == index:
                selected.append(f)
        else:
            raise MediaError('旧版视频结果缺少帧定位，请重新检测')
    return selected


def _visual_boxes(findings, index, ts, frame_end=None):
    return [finding['bbox'] for finding in _visual_findings(findings, index, ts, frame_end)]


def _frames_with_end(path, metadata, context):
    previous = None
    for item in video_frames(path, metadata, context):
        if previous is not None:
            yield (*previous, item[2])
        previous = item
    if previous is not None:
        frame = previous[1]
        duration = float(frame.duration * frame.time_base) if frame.duration else 0.0
        yield (*previous, max(metadata['duration'], previous[2] + duration))


def mask_video_bytes(path, findings, policy, context=None, audit=None):
    import av
    metadata=probe(path)
    accepted=_accepted(findings)
    visual_regions = [f for f in accepted if f.get('bbox')]
    applications = {id(f): 0 for f in visual_regions}
    masked_frames = 0
    # Validate legacy findings even if the video happens to contain no decoded frame.
    _visual_boxes(accepted,-1,-1)
    with tempfile.TemporaryDirectory(prefix='omnimask_video_') as td:
        td=Path(td)
        intermediate, final=td/'visual.mp4',td/'masked.mp4'
        count=0
        try:
            with av.open(str(intermediate),'w') as target:
                rate=Fraction(metadata['frame_rate'])
                if rate<=0: rate=Fraction(25)
                output=target.add_stream('libx264',rate=rate)
                output.width=metadata['height'] if metadata['rotation'] in (90,270) else metadata['width']
                output.height=metadata['width'] if metadata['rotation'] in (90,270) else metadata['height']
                output.pix_fmt='yuv420p'
                output.codec_context.max_b_frames=0
                output.options={'crf':'18','preset':'veryfast'}
                output.time_base=Fraction(1,90000)
                output.codec_context.time_base=Fraction(1,90000)
                durations = {}
                for index,original,ts,image,frame_end in _frames_with_end(path,metadata,context):
                    selected = _visual_findings(accepted,index,ts,frame_end)
                    _boxes(image,[f['bbox'] for f in selected])
                    if selected:
                        masked_frames += 1
                    for finding in selected:
                        applications[id(finding)] += 1
                    frame=av.VideoFrame.from_ndarray(image,format='bgr24')
                    frame.pts=round(ts*90000)
                    frame.time_base=Fraction(1,90000)
                    if original.duration:
                        frame.duration=round(float(original.duration*original.time_base)*90000)
                    durations[frame.pts] = frame.duration or round(90000 / rate)
                    for packet in output.encode(frame):
                        if packet.pts in durations:
                            packet.duration = durations[packet.pts]
                        target.mux(packet)
                    count+=1
                    progress(context,'画面脱敏',ts,metadata['duration'])
                for packet in output.encode():
                    if packet.pts in durations:
                        packet.duration = durations[packet.pts]
                    target.mux(packet)
        except MediaError:
            raise
        except Exception as exc:
            raise MediaError('视频编码失败，请检查 H.264 编码器与画面尺寸') from exc
        if not count:
            raise MediaError('视频无可处理帧')
        args=['-i',intermediate]
        track_audits = {}
        for track in metadata['audio_tracks']:
            track_index=track['track_index']
            raw=td/f'audio{track_index}.wav'
            extract_track(path,track,raw,metadata,context)
            track_findings=[f for f in accepted if not f.get('bbox') and int(f.get('track_index',0))==track_index]
            masked=td/f'masked{track_index}.wav'
            track_audit = {}
            masked.write_bytes(mask_audio_bytes(raw,track_findings,policy,context,track_audit))
            track_audits[track_index] = track_audit
            args += ['-i',masked]
        args += ['-map','0:v:0','-c:v','copy']
        for i in range(len(metadata['audio_tracks'])):
            args += ['-map',f'{i+1}:a:0']
        args += ['-c:a','aac','-map_metadata','-1','-movflags','+faststart',final]
        run_ffmpeg(args,context)
        result=probe(final)
        if abs(result['duration']-metadata['duration'])>.1:
            raise MediaError('输出时长校验失败，未生成可导出结果')
        if len(result['audio_tracks']) != len(metadata['audio_tracks']):
            raise MediaError('输出音轨数量校验失败')
        if audit is not None:
            audit.update(audio_tracks=track_audits, frames_processed=count,
                         masked_frames=masked_frames,
                         region_frame_applications=[applications[id(f)] for f in visual_regions])
        return final.read_bytes()


def compute_media(task, findings, accepted_only=False, context=None):
    policy=task.get('policy') or {}
    use=eligible(findings,policy,True)
    outputs={}
    audits={}
    stats={'media_changed':0,'media_findings_applied':0}
    for fi in task['files']:
        check(context)
        kind=kind_of(fi['path'])
        if kind not in ('image','audio','video'): continue
        path=Path(fi['abs'])
        if not path.is_file(): raise MediaError('源媒体文件不存在')
        hits=[f for f in use if f['file_id']==fi['id']]
        stats['media_findings_applied']+=len(hits)
        fn={'image':mask_image_bytes,'audio':mask_audio_bytes,'video':mask_video_bytes}[kind]
        audit = {}
        data=fn(path,hits,policy,context,audit) if kind in ('audio','video') else fn(path,hits,policy,context)
        audits[fi['id']] = audit
        ext={'audio':'.wav','video':'.mp4'}.get(kind,path.suffix)
        outputs[fi['id']]=(data,ext)
        if hits or (kind in ('audio','video') and policy.get('voice_change')):
            stats['media_changed']+=1
    return {'stats':stats,'outputs':outputs,'audits':audits}
