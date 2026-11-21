"""Local media primitives. Errors never become successful unmodified media."""
import json
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path


class MediaError(RuntimeError):
    pass


class MediaCancelled(MediaError):
    pass


def check(context=None):
    if context is not None:
        context.check_cancelled()


def progress(context, stage, completed, total):
    check(context)
    if context is not None:
        context.update(stage, completed, total)


def executable(name):
    candidate = os.environ.get('OMNIMASK_' + name.upper()) or shutil.which(name)
    if not candidate:
        raise MediaError(f'缺少 {name}，请配置本地媒体工具')
    return str(candidate)


def run_ffmpeg(args, context=None, timeout=900):
    """communicate drains pipes; short waits keep cancellation responsive."""
    check(context)
    import time
    command = [executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', *map(str, args)]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    started = time.monotonic()
    try:
        while True:
            check(context)
            if time.monotonic() - started > timeout:
                raise MediaError('媒体处理超时，请缩短文件后重试')
            try:
                _, stderr = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if proc.returncode:
            # ffmpeg stderr may contain sensitive source paths, never expose it.
            raise MediaError('媒体解码或编码失败，请检查文件完整性和编码器支持')
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate()


def probe(path):
    try:
        result = subprocess.run([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format',
                                 '-of', 'json', str(path)], capture_output=True, timeout=30, check=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(result.stdout)
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError('无法读取媒体信息，文件可能损坏') from exc
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    rotation = 0
    if video:
        rotation = int(float(video.get('tags', {}).get('rotate', 0)))
        for side in video.get('side_data_list', []):
            if 'rotation' in side:
                rotation = int(round(float(side['rotation'])))
    rotation %= 360
    if rotation not in (0, 90, 180, 270):
        raise MediaError('暂不支持非直角视频旋转')
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    origin = float(video.get('start_time', 0) or 0) if video else 0.0
    duration = float((video or {}).get('duration') or data.get('format', {}).get('duration') or 0)
    return {'duration': duration, 'width': int((video or {}).get('width', 0)),
            'height': int((video or {}).get('height', 0)), 'rotation': rotation,
            'video_origin': origin, 'has_video': video is not None,
            'frame_rate': (video or {}).get('avg_frame_rate', '0/1'),
            'audio_tracks': [{'track_index': i, 'start': float(s.get('start_time', 0) or 0),
                              'channels': s.get('channels', 1), 'sample_rate': int(s.get('sample_rate') or 48000)}
                             for i, s in enumerate(audio)],
            'omitted_streams': [s.get('codec_type', 'unknown') for s in streams
                                if s.get('codec_type') not in ('audio', 'video')]
                              + ['additional_video'] * max(0, sum(s.get('codec_type') == 'video' for s in streams) - 1)}


def video_frames(path, metadata=None, context=None):
    import av
    import numpy as np
    meta = metadata or probe(path)
    if not meta['has_video']:
        raise MediaError('文件不含可处理的视频流')
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            origin = None
            for index, frame in enumerate(container.decode(stream)):
                check(context)
                if frame.pts is None:
                    raise MediaError('视频帧缺少时间戳，无法安全定位')
                absolute = float(frame.pts * frame.time_base)
                if origin is None:
                    origin = absolute
                image = frame.to_ndarray(format='bgr24')
                if meta['rotation']:
                    image = np.ascontiguousarray(np.rot90(image, meta['rotation'] // 90))
                yield index, frame, absolute - origin, image
    except (MediaError, MediaCancelled):
        raise
    except Exception as exc:
        raise MediaError('视频帧解码失败') from exc


def extract_track(path, track, out, metadata, context=None):
    """Align each audio stream to the first displayed video frame."""
    start = track['start'] - metadata.get('video_origin', 0)
    filters = []
    if start < 0:
        filters.append(f'atrim=start={-start:.9f}')
    filters.append('asetpts=PTS-STARTPTS')
    if start > 0:
        filters.append(f'adelay={round(start * 1000)}:all=1')
    args = ['-i', path, '-map', f"0:a:{track['track_index']}", '-vn', '-af', ','.join(filters)]
    if metadata.get('duration', 0) > 0:
        args += ['-t', str(metadata['duration'])]
    run_ffmpeg([*args, '-c:a', 'pcm_f32le', out], context)
