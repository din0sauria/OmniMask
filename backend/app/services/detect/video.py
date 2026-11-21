"""Full-frame detection with frame identity, display orientation and every audio track."""
import tempfile
from pathlib import Path

from ...core.config import MAX_MEDIA_BYTES
from ..media_runtime import MediaError, MediaCancelled, check, progress, probe, video_frames, extract_track
from .audio import detect_audio_detail
from .image import _cv, _ocr_engine, detect_image


def detect_video_detail(path, policy, context=None):
    metadata = probe(path)
    findings, warnings, transcript = [], [], []
    if _cv() is None:
        raise MediaError('缺少 OpenCV，无法逐帧检查')
    if _ocr_engine() is None:
        warnings.append('OCR 引擎不可用，画面文字未完成检查')
    if policy.get('use_model'):
        from ..models import image_yolo
        if image_yolo() is None:
            warnings.append('本地图像增强模型不可用')
    count = 0
    frame_times = []
    with tempfile.TemporaryDirectory(prefix='omnimask_detect_') as td:
        image_path = Path(td) / 'frame.png'
        for index, frame, ts, image in video_frames(path, metadata, context):
            frame_times.append(ts)
            if not _cv().imwrite(str(image_path), image):
                raise MediaError('无法生成待检查的视频帧')
            hits, w = detect_image(image_path, policy)
            warnings.extend(w)
            for hit in hits:
                hit.update(media='video', frame_index=index, frame_pts=frame.pts,
                           frame_time_base=str(frame.time_base), frame_ts=ts, source='automatic')
            findings.extend(hits)
            count += 1
            progress(context, '逐帧检测', ts, metadata['duration'])
        if not count:
            raise MediaError('视频不包含可解码画面')
        for hit in findings:
            i = hit['frame_index']
            next_time = frame_times[i + 1] if i + 1 < count else metadata['duration']
            hit['frame_duration'] = max(0.001, next_time - frame_times[i])
        for track in metadata['audio_tracks']:
            check(context)
            audio = Path(td) / f"audio_{track['track_index']}.wav"
            try:
                extract_track(path, track, audio, metadata, context)
                hits, detail = detect_audio_detail(audio, policy, context)
                for hit in hits:
                    hit.update(media='video', track_index=track['track_index'])
                findings.extend(hits)
                transcript.extend([{**s, 'track_index': track['track_index']} for s in detail['transcript']])
            except MediaCancelled:
                raise
            except MediaError as exc:
                warnings.append(f"音轨 {track['track_index'] + 1}：{exc}")
    metadata['coded_width'], metadata['coded_height'] = metadata['width'], metadata['height']
    if metadata['rotation'] in (90,270):
        metadata['width'], metadata['height'] = metadata['height'], metadata['width']
    metadata['display_width'], metadata['display_height'] = metadata['width'], metadata['height']
    metadata['frames_scanned'] = count
    metadata['frame_check'] = 'all'
    return findings, {'state':'partial' if warnings else 'complete', 'warnings':sorted(set(warnings)),
                      'metadata':metadata, 'transcript':transcript}


def detect_video(path, policy, context=None):
    hits, detail = detect_video_detail(path, policy, context)
    return hits, detail['warnings']


def detect_video_files(files, policy, context=None):
    findings, warnings, states = [], [], {}
    scanned = skipped = 0
    for fi in files:
        check(context)
        try:
            path = Path(fi['abs'])
            if path.stat().st_size > MAX_MEDIA_BYTES:
                skipped += 1
                msg = '文件超过媒体处理大小上限'
                states[fi['id']] = {'state':'skipped','warnings':[msg],'metadata':{},'transcript':[]}
                warnings.append(fi['path'] + '：' + msg)
                continue
            hits, detail = detect_video_detail(path, policy, context)
            scanned += 1
            for hit in hits:
                hit.update(file_id=fi['id'], file_path=fi['path'])
            findings.extend(hits)
            states[fi['id']] = detail
            warnings.extend(fi['path'] + '：' + w for w in detail['warnings'])
        except MediaCancelled:
            raise
        except Exception as exc:
            msg = str(exc) if isinstance(exc, MediaError) else '视频检查失败'
            warnings.append(fi['path'] + '：' + msg)
            states[fi['id']] = {'state':'failed','warnings':[msg],'metadata':{},'transcript':[]}
    return dict(findings=findings, scanned=scanned, skipped=skipped, warnings=warnings, files=states)
