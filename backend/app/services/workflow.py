"""Detection coverage and immutable, reviewed output shared by preview and export."""
import hashlib
import json
import math
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException

from ..core.storage import load_task, load_findings, save_findings, task_dir, now
from ..core.config import MAX_MEDIA_BYTES, MAX_SCAN_BYTES
from .jobs import save_current
from .media import kind_of
from .report import build_report, render_markdown, summarize

VERSION = 2


def fingerprint(path: Path, context=None) -> dict:
    h = hashlib.sha256()
    try:
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                if context:
                    context.check_cancelled()
                h.update(chunk)
        return {'sha256': h.hexdigest(), 'size': path.stat().st_size}
    except OSError as exc:
        raise HTTPException(409, '源文件不存在或无法读取，请重新检测') from exc


def invalidate(task: dict):
    task['revision'] = int(task.get('revision', 0)) + 1
    task.pop('artifacts', None)
    task.pop('verification', None)
    task.get('stats', {}).pop('mask', None)
    # Only this implementation's generated cache is removed, never source files.
    # Immutable revision directories also keep a playing FileResponse valid while
    # review creates the next revision. All are removed when the task is deleted.


def verify_sources(task: dict, context=None):
    for fi in task['files']:
        if not fi.get('source_fingerprint') or fingerprint(Path(fi['abs']), context) != fi['source_fingerprint']:
            raise HTTPException(409, '源文件已改变或检测信息过旧，请重新检测')


def detect_task(task_id: str, policy_id: str | None, context):
    from .detect.code import detect_files
    from .detect.text import detect_text_files
    from .detect.image import detect_image_files
    from .detect.audio import detect_audio_files
    from .detect.video import detect_video_files
    from .policy import get_policy
    from .media_runtime import MediaCancelled

    task = load_task(task_id)
    if policy_id:
        task['policy'] = get_policy(policy_id)
        task['policy_id'] = task['policy']['id']
    invalidate(task)
    task['status'] = 'created'
    task.pop('detection_version', None)
    task['stats'] = {'file_count': len(task['files']), 'scanned': 0, 'skipped': 0, 'warnings': []}
    for fi in task['files']:
        fi['findings'] = 0
        fi['detection'] = {'state': 'not_checked', 'warnings': [], 'metadata': {}, 'transcript': []}
    save_findings(task_id, [])
    save_current(task)
    findings, warnings = [], []
    scanned = skipped = 0
    detectors = {'code': detect_files, 'text': detect_text_files, 'image': detect_image_files,
                 'audio': detect_audio_files, 'video': detect_video_files}
    for index, fi in enumerate(task['files']):
        context.update('检测文件', index, len(task['files']))
        kind = kind_of(fi['path'])
        try:
            limit = MAX_MEDIA_BYTES if kind in ('image', 'audio', 'video') else MAX_SCAN_BYTES
            if Path(fi['abs']).stat().st_size > limit:
                fi['detection'] = {'state': 'skipped', 'warnings': ['超过该模态处理大小上限'], 'metadata': {}, 'transcript': []}
                fi['findings'] = 0
                skipped += 1
                warnings.append(fi['path'] + '：超过处理大小上限，未检查')
                save_current(task)
                continue
            before = fingerprint(Path(fi['abs']), context)
            if kind in ('audio', 'video'):
                from .original_preview import prepare_original
                ext = '.wav' if kind == 'audio' else '.mp4'
                relative = f"original-cache/{fi['id']}/{before['sha256']}{ext}"
                dest = task_dir(task_id) / relative
                if not dest.is_file():
                    context.update('准备可播放原始预览', 0, 1)
                    prepare_original(Path(fi['abs']), kind, dest, context)
                fi['original_preview'] = relative
            args = ([fi], task['policy'], context) if kind in ('audio', 'video') else ([fi], task['policy'])
            result = detectors[kind](*args)
            detail = result.get('files', {}).get(fi['id'])
            if detail is None:
                state = 'skipped' if not result['scanned'] else ('partial' if result['warnings'] else 'complete')
                detail = {'state': state, 'metadata': {}, 'transcript': [], 'warnings': result['warnings']}
            if fingerprint(Path(fi['abs']), context) != before:
                raise HTTPException(409, '检测期间源文件改变，请重新检测')
            fi['source_fingerprint'] = before
            if kind == 'image' and detail['state'] == 'complete':
                from PIL import Image
                with Image.open(fi['abs']) as image:
                    detail['metadata'].update(width=image.width, height=image.height)
            fi['detection'] = detail
            fi['findings'] = len(result['findings'])
            findings.extend(result['findings'])
            warnings.extend(result['warnings'])
            scanned += result['scanned']
            skipped += result['skipped']
        except MediaCancelled:
            raise
        except Exception as exc:
            msg = exc.detail if isinstance(exc, HTTPException) else '文件检查失败，请检查文件与依赖'
            fi['detection'] = {'state': 'failed', 'warnings': [msg], 'metadata': {}, 'transcript': []}
            fi['findings'] = 0
            warnings.append(fi['path'] + '：' + msg)
        save_current(task)
    relative_paths = {fi['id']: fi['path'] for fi in task['files']}
    for index, finding in enumerate(findings):
        finding.update(id=f'f{index + 1:06d}', status='pending', mode=None,
                       file_path=relative_paths[finding['file_id']])
    save_findings(task_id, findings)
    summary = summarize(findings)
    task['status'] = 'detected'
    task['detection_version'] = VERSION
    task['stats'] = {'file_count': len(task['files']), 'scanned': scanned, 'skipped': skipped,
                     'warnings': list(dict.fromkeys(warnings)), **summary, 'detected_at': now()}
    save_current(task)
    return {'task': task, 'summary': summary, 'warnings': task['stats']['warnings']}


def ready(task, findings):
    if task.get('detection_version') != VERSION:
        raise HTTPException(409, '请重新检测；旧版媒体定位不用于脱敏导出')
    incomplete = [f for f in task['files'] if f.get('detection', {}).get('state') != 'complete']
    if incomplete:
        raise HTTPException(409, '存在未完成检查的文件，请修复检测提示并重新检测')
    if any(f.get('status') == 'pending' for f in findings):
        raise HTTPException(409, '仍有待审核结果，请先接受或忽略全部结果')


def cache_valid(task):
    return bool(task.get('artifacts')) and task['artifacts'].get('revision') == task.get('revision')


def artifact_path(task, file_id):
    if not cache_valid(task):
        raise HTTPException(409, '脱敏结果尚未生成或已失效，请执行脱敏')
    entry = task['artifacts']['files'].get(file_id)
    if not entry:
        raise HTTPException(409, '该文件没有可用的脱敏产物')
    path = task_dir(task['id']) / 'media-cache' / str(task['revision']) / entry['cache_name']
    if not path.is_file():
        raise HTTPException(409, '脱敏缓存已丢失，请重新执行脱敏')
    return path, entry


def mask_task(task_id, context):
    from .mask.code import compute
    from .mask.media import compute_media
    from .processing_record import processing_record
    from .policy import ensure_supported_generation

    task = load_task(task_id)
    findings = load_findings(task_id)
    ensure_supported_generation(task['policy'], findings)
    ready(task, findings)
    verify_sources(task, context)
    invalidate(task)
    task['status'] = 'reviewed'
    save_current(task)
    context.update('处理文本', 0, len(task['files']))
    text = compute(task, findings, accepted_only=True)
    media_stats = {'media_changed': 0, 'media_findings_applied': 0}
    verify_sources(task, context)
    parent = task_dir(task_id)
    files = {}
    with tempfile.TemporaryDirectory(prefix='cache-build-', dir=parent) as tmp:
        for i, fi in enumerate(task['files']):
            context.update('保存脱敏产物', i, len(task['files']))
            kind = kind_of(fi['path'])
            ext = Path(fi['path']).suffix
            media = compute_media({**task, 'files': [fi]}, findings, accepted_only=True, context=context) if kind in ('image','audio','video') else {'outputs': {}, 'stats': {}}
            for key in media_stats:
                media_stats[key] += media['stats'].get(key, 0)
            if fi['id'] in media['outputs']:
                data, ext = media['outputs'][fi['id']]
            elif kind in ('image', 'audio', 'video'):
                raise HTTPException(422, '媒体处理未生成结果，禁止回退到原文件')
            elif fi['id'] in text['outputs']:
                content, encoding = text['outputs'][fi['id']]
                data = content.encode(encoding)
            else:
                data = Path(fi['abs']).read_bytes()
            cache_name = fi['id'] + ext
            output_path = Path(tmp, cache_name)
            output_path.write_bytes(data)
            # File IDs prevent same-stem output collisions without exposing local paths.
            target = Path(fi['path']).with_suffix(ext).as_posix()
            if any(e['path'] == target for e in files.values()):
                target = str(Path(target).with_name(Path(target).stem + '_' + fi['id'] + ext)).replace('\\', '/')
            files[fi['id']] = {'cache_name': cache_name, 'path': target, 'media': kind,
                               'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                               'processing': processing_record(
                                   fi, findings, task['policy'], output_path,
                                   text_operations=text['operations'].get(fi['id'], []),
                                   media_audit=media.get('audits', {}).get(fi['id']))}
        context.check_cancelled()
        verify_sources(task, context)
        cache = parent / 'media-cache'
        cache.mkdir(exist_ok=True)
        Path(tmp).rename(cache / str(task['revision']))
    task['artifacts'] = {'revision': task['revision'], 'files': files, 'created_at': now()}
    task['status'] = 'masked'
    task['stats']['mask'] = {**text['stats'], **media_stats}
    task['stats']['masked_at'] = now()
    save_current(task)
    return {'task': task, 'summary': summarize(findings), 'stats': task['stats']['mask']}


def export_cached(task, include_mapping=False, anonymize_metadata=False,
                  acknowledge_preflight=False, expected_revision=None):
    from .export_preflight import preflight, anonymize_report, anonymize_mapping, aliases
    from .policy import ensure_supported_generation
    findings = load_findings(task['id'])
    ensure_supported_generation(task['policy'], findings)
    ready(task, findings)
    verify_sources(task)
    if task['status'] not in ('masked', 'exported'):
        raise HTTPException(409, '请先执行脱敏')
    if expected_revision is not None and expected_revision != task.get('revision'):
        raise HTTPException(409, '审核或产物版本已经改变，请重新进行导出复核')
    review = preflight(task, anonymize_metadata, include_mapping)
    if review['requires_acknowledgement'] and not acknowledge_preflight:
        raise HTTPException(409, '导出复核发现需确认的问题，请复查或隐藏元信息后明确确认')
    sources = [artifact_path(task, fi['id']) for fi in task['files']]
    report = build_report(task, findings)
    if anonymize_metadata:
        report = anonymize_report(report, task)
    report['export_review'] = {**review, 'acknowledged': bool(acknowledge_preflight)}
    export_names = aliases(task) if anonymize_metadata else {}
    handle = tempfile.NamedTemporaryFile(prefix='omnimask_', suffix='.zip', delete=False)
    handle.close()
    path = Path(handle.name)
    try:
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for fi, (cached, entry) in zip(task['files'], sources):
                data = cached.read_bytes()
                if hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise HTTPException(409, '脱敏缓存损坏，请重新执行脱敏')
                archive.writestr('masked/' + export_names.get(fi['id'], entry['path']), data)
            archive.writestr('report.json', json.dumps(report, ensure_ascii=False, indent=2))
            archive.writestr('report.md', render_markdown(report))
            if include_mapping:
                from .mask.code import build_mapping
                mapping = build_mapping(task, findings, accepted_only=True)
                if mapping:
                    if anonymize_metadata:
                        mapping = anonymize_mapping(mapping, task)
                    archive.writestr('mapping.json', json.dumps(mapping, ensure_ascii=False, indent=2))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def validate_location(finding, fi):
    """Validate manual edits against detected media metadata, before persisting."""
    if finding.get('media') == 'image' and not finding.get('bbox'):
        raise HTTPException(422, '图像标记必须包含区域')
    if finding.get('bbox') is not None:
        x, y, w, h = finding['bbox']
        if not all(math.isfinite(v) for v in (x, y, w, h)) or min(x, y) < 0 or min(w, h) <= 0:
            raise HTTPException(422, '区域坐标必须有限、非负且宽高大于零')
        meta = fi.get('detection', {}).get('metadata', {})
        if meta.get('width') and x + w > meta['width'] or meta.get('height') and y + h > meta['height']:
            raise HTTPException(422, '区域超出画面边界')
    if finding.get('media') in ('audio', 'video'):
        if finding.get('bbox') and finding.get('frame_index') is not None and finding.get('source') != 'manual':
            return
        start, end = finding.get('start'), finding.get('end')
        duration = fi.get('detection', {}).get('metadata', {}).get('duration', 0)
        if start is None or end is None or not 0 <= start < end or (duration and end > duration + .001):
            raise HTTPException(422, '时间区间须满足 0 ≤ 开始 < 结束 ≤ 文件时长')
        meta = fi.get('detection', {}).get('metadata', {})
        tracks = meta.get('audio_tracks', [])
        if not finding.get('bbox') and (not tracks or finding.get('track_index', 0) >= len(tracks)):
            raise HTTPException(422, '音轨编号不存在')
