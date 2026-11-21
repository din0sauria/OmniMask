"""Recheck immutable masked artifacts with local detectors, never claim safety.

The result intentionally stores positions and counts, not detected plaintext or
ASR transcripts. The same detector can miss the same target twice; this is an
additional review aid, not an independent privacy guarantee.
"""
import math
from copy import deepcopy
from pathlib import Path

from fastapi import HTTPException

from ..core.storage import load_task, load_findings, now, save_task, task_dir
from . import jobs, workflow
from .media import kind_of
from .media_runtime import MediaCancelled, check, progress

LIMITATIONS = [
    '复查复用当前规则和本地模型，可能再次漏检；无候选不等于没有敏感信息。',
    '人工忽略、低于处理阈值或未启用的目标可能仍存在；候选需要人工判断。',
    '仅检查已生成的主文件内容，不检查文件名、任务备注、媒体附件及未支持的数据类型。',
]
MAX_CANDIDATES = 1000


def _manifest(task):
    return [{'file_id': file_id, 'sha256': entry.get('sha256')}
            for file_id, entry in sorted((task.get('artifacts') or {}).get('files', {}).items())]


def _safe_path(fi, entry):
    text = str(entry.get('path') or fi.get('path') or fi['id'])
    path = Path(text)
    if path.is_absolute() or ':' in text or '..' in path.parts or text.startswith(('\\', '/')):
        return fi['id'] + path.suffix
    return text.replace('\\', '/')


def _artifact(task, fi, context=None):
    path, entry = workflow.artifact_path(task, fi['id'])
    base = (task_dir(task['id']) / 'media-cache' / str(task['revision'])).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(base):
        raise HTTPException(409, '脱敏产物路径无效，请重新执行脱敏')
    if workflow.fingerprint(path, context).get('sha256') != entry['sha256']:
        raise HTTPException(409, '脱敏缓存已改变，请重新执行脱敏后复查')
    return path, entry


def current_verification(task, check_hashes=False):
    """Return a safe snapshot or an explicit absent/stale state.

    check_hashes=True is intended for preflight/export. UI polling can compare
    only the immutable manifest to avoid repeatedly hashing large media files.
    """
    saved = task.get('verification')
    if not saved:
        return {'status': 'not_run', 'current': False, 'candidate_count': 0,
                'files': [], 'limitations': list(LIMITATIONS)}
    current = (workflow.cache_valid(task) and saved.get('revision') == task.get('revision')
               and saved.get('artifacts') == _manifest(task))
    if current and check_hashes:
        try:
            for fi in task['files']:
                _artifact(task, fi)
        except (HTTPException, OSError):
            current = False
    if not current:
        return {'status': 'stale', 'current': False, 'candidate_count': 0,
                'files': [], 'limitations': list(LIMITATIONS),
                'reason': '审核版本或产物已变化，原复查结果失效，请重新复查。'}
    result = deepcopy(saved)
    result['current'] = True
    job = task.get('job') or {}
    if result['status'] == 'running' and job.get('operation') == 'verify' and job.get('status') in ('cancelled', 'interrupted', 'failed'):
        result['status'] = job['status']
    return result


def _label(value, plaintext, fallback):
    text = str(value or '')[:100]
    if not text or (plaintext and str(plaintext) in text) or '\\' in text or ':/' in text or text.startswith('/'):
        return fallback
    return text


def _candidate(hit, kind):
    result = {'media': kind, 'rule_name': _label(hit.get('rule_name'), hit.get('value'), 'sensitive'),
              'type_label': _label(hit.get('type_label'), hit.get('value'), '敏感候选')}
    for key in ('line_start', 'line_end', 'col_start', 'col_end', 'index_start', 'index_end',
                'confidence', 'start', 'end', 'frame_index', 'frame_pts', 'frame_ts',
                'frame_duration', 'track_index'):
        value = hit.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[key] = value
    box = hit.get('bbox')
    if isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
        result['bbox'] = list(box)
    if hit.get('risk') in ('high', 'medium', 'low'):
        result['risk'] = hit['risk']
    if hit.get('timing_precision') in ('word', 'segment', 'manual'):
        result['timing_precision'] = hit['timing_precision']
    return result


def _detect(fi, policy, context):
    from .detect.code import detect_files
    from .detect.text import detect_text_files
    from .detect.image import detect_image_files
    from .detect.audio import detect_audio_files
    from .detect.video import detect_video_files
    kind = kind_of(fi['path'])
    detector = {'code': detect_files, 'text': detect_text_files, 'image': detect_image_files,
                'audio': detect_audio_files, 'video': detect_video_files}[kind]
    args = ([fi], policy, context) if kind in ('audio', 'video') else ([fi], policy)
    result = detector(*args)
    detail = result.get('files', {}).get(fi['id'])
    if detail is None:
        state = 'not_checked' if not result.get('scanned') else ('partial' if result.get('warnings') else 'complete')
        detail = {'state': state, 'metadata': {}}
    return result.get('findings', []), detail


def _coverage(kind, policy, metadata):
    """Whitelist detector scope and structural metadata, never source metadata."""
    result = {'detector': kind, 'enhanced_model_requested': bool(policy.get('use_model'))}
    supported = ('phone', 'email', 'id_card', 'bank_card', 'name', 'address')
    if kind != 'code':
        enabled = policy.get('pii_types') or supported
        result['pii_types'] = [item for item in supported if item in enabled]
    for key in ('duration', 'frames_scanned'):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[key] = value
    if metadata.get('frame_check') == 'all':
        result['frame_check'] = 'all'
    if isinstance(metadata.get('audio_tracks'), list):
        result['audio_track_count'] = len(metadata['audio_tracks'])
    return result


def _persist(task_id, revision, manifest, result):
    with jobs._lock:
        current = load_task(task_id)
        if not current or current.get('revision') != revision or _manifest(current) != manifest or not workflow.cache_valid(current):
            raise HTTPException(409, '审核版本或脱敏产物已变化，请重新复查')
        current['verification'] = deepcopy(result)
        current['updated_at'] = now()
        save_task(current)


def verify_task(task_id, context):
    task = load_task(task_id)
    if not task:
        raise HTTPException(404, '任务不存在')
    workflow.ready(task, load_findings(task_id))
    if task.get('status') not in ('masked', 'exported') or not workflow.cache_valid(task):
        raise HTTPException(409, '请先生成当前审核版本的脱敏结果')
    workflow.verify_sources(task, context)
    revision, manifest = task['revision'], _manifest(task)
    result = {'revision': revision, 'artifacts': manifest, 'status': 'running',
              'checked_at': None, 'candidate_count': 0, 'files': [],
              'limitations': list(LIMITATIONS)}
    for fi in task['files']:
        entry = task['artifacts']['files'].get(fi['id'], {})
        result['files'].append({'file_id': fi['id'], 'path': _safe_path(fi, entry),
                                'media': entry.get('media') or kind_of(entry.get('path') or fi['path']),
                                'status': 'not_checked', 'candidate_count': 0,
                                'candidates': [], 'warnings': [], 'coverage': {}})
    _persist(task_id, revision, manifest, result)
    try:
        for index, (fi, record) in enumerate(zip(task['files'], result['files'])):
            progress(context, '残留复查', index, len(task['files']))
            try:
                path, entry = _artifact(task, fi, context)
                masked_file = {**fi, 'abs': str(path), 'path': entry['path']}
                hits, detail = _detect(masked_file, task['policy'], context)
                _artifact(task, fi, context)  # Reject changes while a detector was reading.
                state = detail.get('state', 'failed')
                if state not in ('complete', 'partial', 'failed', 'not_checked', 'skipped'):
                    state = 'failed'
                record['status'] = state
                record['candidate_count'] = len(hits)
                record['candidates'] = [_candidate(h, record['media']) for h in hits[:MAX_CANDIDATES]]
                record['candidates_truncated'] = len(hits) > MAX_CANDIDATES
                metadata = detail.get('metadata') or {}
                record['coverage'] = _coverage(record['media'], task['policy'], metadata)
                if state != 'complete':
                    record['warnings'] = ['该文件未完成全部复查，请检查本地依赖、模型或文件完整性。']
                result['candidate_count'] = sum(f['candidate_count'] for f in result['files'])
            except MediaCancelled:
                raise
            except HTTPException:
                raise
            except Exception:
                record.update(status='failed', warnings=['该文件复查失败，请检查本地依赖、模型或文件完整性。'])
            _persist(task_id, revision, manifest, result)
        check(context)
        workflow.verify_sources(load_task(task_id), context)
        for fi in task['files']:
            _artifact(task, fi, context)
        states = [f['status'] for f in result['files']]
        result['status'] = 'complete' if all(s == 'complete' for s in states) else (
            'failed' if not any(s in ('complete', 'partial') for s in states) else 'partial')
        result['checked_at'] = now()
        _persist(task_id, revision, manifest, result)
        return result
    except MediaCancelled:
        result.update(status='cancelled', checked_at=now())
        _persist(task_id, revision, manifest, result)
        raise
    except Exception:
        result.update(status='failed', checked_at=now())
        # A newer revision must never be overwritten by the old verification.
        try:
            _persist(task_id, revision, manifest, result)
        except HTTPException:
            pass
        raise
