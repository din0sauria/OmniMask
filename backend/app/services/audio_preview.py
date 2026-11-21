"""Private source-track cache; extraction shares the heavy-operation queue."""
import tempfile
from pathlib import Path

from fastapi import HTTPException

from ..core.storage import load_task, task_dir, read_json, write_json
from . import jobs
from .media_runtime import probe, extract_track
from .workflow import fingerprint


def _file(task_id, file_id):
    task = load_task(task_id)
    fi = next((f for f in (task or {}).get('files', []) if f['id'] == file_id), None)
    if fi is None:
        raise HTTPException(404, '文件不存在')
    expected = fi.get('source_fingerprint')
    if not expected or fingerprint(Path(fi['abs'])) != expected:
        raise HTTPException(409, '源文件已改变或尚未检测，请重新检测')
    return fi


def _cached(path):
    manifest = read_json(path.with_suffix('.json'))
    if path.is_file() and manifest and fingerprint(path) == manifest:
        return path
    return None


def prepare_track(task_id: str, file_id: str, track_index: int) -> Path:
    fi = _file(task_id, file_id)
    if track_index < 0:
        raise HTTPException(404, '音轨不存在')
    digest = fi['source_fingerprint']['sha256']
    folder = task_dir(task_id) / 'original-audio-cache' / file_id / digest
    path = folder / f'track-{track_index}.wav'
    if _cached(path):
        return path

    def build(context):
        current = _file(task_id, file_id)
        if current['source_fingerprint'] != fi['source_fingerprint']:
            raise HTTPException(409, '源文件已改变，请重新检测')
        if _cached(path):  # A previous identical request may have just finished.
            return path
        context.update('准备音轨试听', 0, 1)
        metadata = probe(Path(current['abs']))
        tracks = metadata['audio_tracks']
        if not 0 <= track_index < len(tracks):
            raise HTTPException(404, '音轨不存在')
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='track-build-', dir=folder) as td:
            temporary = Path(td) / 'track.wav'
            extract_track(Path(current['abs']), tracks[track_index], temporary, metadata, context)
            audio = probe(temporary)
            if not audio['audio_tracks'] or audio['duration'] <= 0:
                raise HTTPException(422, '音轨试听未生成有效音频')
            context.check_cancelled()
            _file(task_id, file_id)  # Never publish output for a changed source.
            manifest = fingerprint(temporary, context)
            context.check_cancelled()
            temporary.replace(path)
            write_json(path.with_suffix('.json'), manifest)
        return path

    # Both new and joined requests wait without holding the jobs lock. Existing
    # detection/masking on this same task returns 409 rather than starting more
    # work; operations on other tasks are serialized by the shared worker.
    return jobs.launch(task_id, 'audition', build, False,
                       shared_key=f'{file_id}:{digest}:{track_index}')
