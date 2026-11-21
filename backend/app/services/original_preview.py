"""Browser-readable, private original previews. Never modifies source media.

These are original-content derivatives, not sanitized exports. The caller owns
cache invalidation, access and disclosure labels.
"""
import os
import tempfile
from pathlib import Path

from .media_runtime import MediaError, check, extract_track, probe, progress, run_ffmpeg


def prepare_original(path: Path, kind: str, dest: Path, context=None) -> Path:
    path, dest = Path(path), Path(dest)
    check(context)
    if kind not in ('audio', 'video'):
        raise MediaError('原始媒体预览仅支持音频和视频')
    if path.resolve() == dest.resolve():
        raise MediaError('原始预览缓存不能覆盖源文件')
    if dest.suffix.lower() != ('.wav' if kind == 'audio' else '.mp4'):
        raise MediaError('原始预览缓存扩展名不正确')
    metadata = probe(path)
    if kind == 'audio' and not metadata['audio_tracks']:
        raise MediaError('文件不包含可预览音轨')
    if kind == 'video' and not metadata['has_video']:
        raise MediaError('文件不包含可预览画面')
    dest.parent.mkdir(parents=True, exist_ok=True)
    progress(context, '准备浏览器原始预览', 0, 1)
    # Same-volume staging permits atomic publication only after validation.
    with tempfile.TemporaryDirectory(prefix='original_preview_', dir=dest.parent) as td:
        td = Path(td)
        output = td / dest.name
        if kind == 'audio':
            run_ffmpeg(['-i', path, '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le',
                        '-map_metadata', '-1', '-map_chapters', '-1', output], context)
        else:
            args = ['-i', path]
            if metadata['audio_tracks']:
                # Use the same timeline as detection and masked-video processing.
                audio = td / 'audio.wav'
                extract_track(path, metadata['audio_tracks'][0], audio, metadata, context)
                args += ['-i', audio]
            args += ['-map', '0:v:0']
            if metadata['audio_tracks']:
                args += ['-map', '1:a:0']
            args += ['-vf', 'setpts=PTS-STARTPTS,pad=ceil(iw/2)*2:ceil(ih/2)*2',
                     '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
                     '-fps_mode', 'passthrough', '-enc_time_base:v', '1:90000',
                     '-video_track_timescale', '90000', '-c:a', 'aac', '-map_metadata', '-1',
                     '-map_chapters', '-1', '-movflags', '+faststart', output]
            # FFmpeg autorotation applies the display matrix before -vf; metadata
            # is removed so browsers do not rotate the already-upright pixels twice.
            run_ffmpeg(args, context)
        result = probe(output)
        if abs(result['duration'] - metadata['duration']) > .1:
            raise MediaError('原始预览时长校验失败')
        if kind == 'video' and (not result['has_video'] or result['rotation'] != 0):
            raise MediaError('原始预览画面校验失败')
        if len(result['audio_tracks']) != min(1, len(metadata['audio_tracks'])):
            raise MediaError('原始预览音轨校验失败')
        progress(context, '原始预览准备完成', 1, 1)
        os.replace(output, dest)
    return dest
