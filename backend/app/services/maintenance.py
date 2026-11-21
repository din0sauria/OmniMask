"""Bounded lifecycle operations on owned task data, never on source files.

Deletion is recoverable by default. Sizes describe regular-file logical bytes,
not reclaimed filesystem allocation. Reparse points are rejected on Windows.
"""
import os
import re
import shutil
import stat
from pathlib import Path

from fastapi import HTTPException

from ..core import storage
from . import jobs

_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_NOTES = ['大小为文件逻辑字节数，不代表实际磁盘可释放空间。',
          '只管理本机任务副本；源文件不会被移动或删除。']


def _is_reparse(path):
    try:
        info = Path(path).lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def _plain_components(path):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if _is_reparse(part):
            raise HTTPException(409, '任务数据路径含符号链接或重解析点，拒绝操作')


def _roots():
    tasks = Path(storage.TASKS_DIR).absolute()
    trash = tasks.parent / 'trash'
    _plain_components(tasks)
    _plain_components(trash)
    return tasks, trash


def _within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _tree_files(path):
    """Check the full owned subtree before any recursive move/removal."""
    path = Path(path)
    _plain_components(path)
    if path.is_file():
        return [path]
    found = []
    for current, dirs, names in os.walk(path, followlinks=False):
        current = Path(current)
        for name in dirs + names:
            if _is_reparse(current / name):
                raise HTTPException(409, '任务副本含符号链接或重解析点，拒绝操作')
        found.extend(current / name for name in names)
    return found


def _size(path):
    return sum(p.stat().st_size for p in _tree_files(path))


def _task(task_id, in_trash=False):
    if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
        raise HTTPException(422, '任务编号无效')
    tasks, trash = _roots()
    root = trash if in_trash else tasks
    path = root / task_id
    if not path.is_dir():
        raise HTTPException(404, '任务不存在')
    _plain_components(path)
    if path.resolve().parent != root.resolve():
        raise HTTPException(409, '任务路径超出管理范围')
    _plain_components(path / 'meta.json')
    meta = storage.read_json(path / 'meta.json')
    if not isinstance(meta, dict) or meta.get('id') != task_id:
        raise HTTPException(409, '任务归属信息不完整，拒绝操作')
    return path, meta


def _all_metadata():
    for root in _roots():
        if not root.exists():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            _plain_components(entry)
            if not (entry / 'meta.json').exists():
                continue
            _plain_components(entry / 'meta.json')
            meta = storage.read_json(entry / 'meta.json')
            if not isinstance(meta, dict) or meta.get('id') != entry.name:
                raise HTTPException(409, '其他任务元数据损坏，无法核验源文件引用')
            yield entry, meta


def _ensure_unreferenced(target):
    target = Path(target).resolve()
    for _, meta in _all_metadata():
        for fi in meta.get('files', []):
            if not fi.get('abs'):
                continue
            try:
                source = Path(fi['abs']).expanduser().absolute().resolve()
            except (OSError, RuntimeError):
                raise HTTPException(409, '源文件引用无法核验，拒绝清理') from None
            if source == target or _within(source, target):
                raise HTTPException(409, f"待清理副本被任务 {meta['id']} 用作源文件，请先处理其引用")


def _stale_candidates(path, meta):
    """Only known generated cache layouts; retain the current revision/hash."""
    result = []
    # Old tasks may lack a revision or source fingerprint. Unknown ownership is
    # not evidence that a generated cache is obsolete: retain it conservatively.
    revisions = {str(value) for value in [meta.get('revision'), (meta.get('artifacts') or {}).get('revision')]
                 if value is not None}
    media = path / 'media-cache'
    if media.is_dir() and revisions:
        result.extend(p for p in media.iterdir() if p.is_dir() and p.name.isdigit() and p.name not in revisions)
    by_id = {fi['id']: fi for fi in meta.get('files', [])}
    originals = path / 'original-cache'
    if originals.is_dir():
        for folder in originals.iterdir():
            if not folder.is_dir() or folder.name not in by_id:
                continue
            fi = by_id[folder.name]
            current_hash = (fi.get('source_fingerprint') or {}).get('sha256')
            current_preview = fi.get('original_preview')
            for item in folder.iterdir():
                relative = item.relative_to(path).as_posix()
                if relative == current_preview or item.stem == current_hash:
                    continue
                if (current_hash and item.is_file() and re.fullmatch(r'[0-9a-f]{64}', item.stem)) or (item.is_dir() and item.name.startswith('original_preview_')):
                    result.append(item)
    audio = path / 'original-audio-cache'
    if audio.is_dir():
        for folder in audio.iterdir():
            if not folder.is_dir() or folder.name not in by_id:
                continue
            current_hash = (by_id[folder.name].get('source_fingerprint') or {}).get('sha256')
            if current_hash:
                result.extend(p for p in folder.iterdir() if p.is_dir() and re.fullmatch(r'[0-9a-f]{64}', p.name) and p.name != current_hash)
    result.extend(p for p in path.iterdir() if p.is_dir() and p.name.startswith('cache-build-'))
    return result


def storage_info():
    with jobs._lock:
        tasks, trash = _roots()
        output = {'tasks': [], 'trash': [], 'totals': {'tasks_bytes': 0, 'trash_bytes': 0, 'stale_cache_bytes': 0},
                  'logical_bytes': True, 'notes': list(_NOTES), 'warnings': []}
        for root, key in ((tasks, 'tasks'), (trash, 'trash')):
            if not root.exists():
                continue
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    path, meta = _task(entry.name, key == 'trash')
                    size = _size(path)
                    stale = sum(_size(p) for p in _stale_candidates(path, meta)) if key == 'tasks' else 0
                    item = {'id': meta['id'], 'name': meta.get('name', meta['id']), 'status': meta.get('status', ''),
                            'bytes': size, 'stale_cache_bytes': stale,
                            'busy': (meta.get('job') or {}).get('status') in jobs.ACTIVE}
                    if key == 'trash':
                        marker = storage.read_json(path / '_trash.json', {}) or {}
                        item['deleted_at'] = marker.get('deleted_at')
                    output[key].append(item)
                    output['totals'][key + '_bytes'] += size
                    output['totals']['stale_cache_bytes'] += stale
                except (HTTPException, OSError) as exc:
                    output['warnings'].append({'id': entry.name, 'error': exc.detail if isinstance(exc, HTTPException) else '任务数据无法读取'})
        return output


def _batch(task_ids, callback):
    if not task_ids or len(task_ids) > 500:
        raise HTTPException(422, '请选择1–500个任务')
    results = []
    with jobs._lock:
        for task_id in dict.fromkeys(task_ids):
            try:
                values = callback(task_id)
                results.append({'id': task_id, 'ok': True, **values})
            except HTTPException as exc:
                results.append({'id': task_id, 'ok': False, 'error': str(exc.detail)})
            except OSError:
                results.append({'id': task_id, 'ok': False, 'error': '文件被占用或无法访问，操作未完成，请停止播放后重试'})
    return {'results': results, 'logical_bytes': True, 'notes': list(_NOTES)}


def soft_delete(task_ids):
    def move(task_id):
        path, meta = _task(task_id)
        jobs.ensure_idle(meta)
        _tree_files(path)
        _ensure_unreferenced(path)
        _, trash = _roots()
        trash.mkdir(parents=True, exist_ok=True)
        dest = trash / task_id
        if dest.exists():
            raise HTTPException(409, '回收站中存在同编号任务，拒绝覆盖')
        size = _size(path)
        marker = path / '_trash.json'
        storage.write_json(marker, {'deleted_at': storage.now(), 'task_id': task_id})
        try:
            path.rename(dest)
        except OSError:
            marker.unlink(missing_ok=True)
            raise
        return {'bytes': size, 'deleted_at': storage.read_json(dest / '_trash.json')['deleted_at']}
    return _batch(task_ids, move)


def restore_tasks(task_ids):
    def restore(task_id):
        path, meta = _task(task_id, True)
        jobs.ensure_idle(meta)
        _tree_files(path)
        _ensure_unreferenced(path)
        tasks, _ = _roots()
        tasks.mkdir(parents=True, exist_ok=True)
        dest = tasks / task_id
        if dest.exists():
            raise HTTPException(409, '活动任务中存在同编号任务，拒绝覆盖')
        size = _size(path)
        path.rename(dest)
        # Keeping the historical marker on an unusual Windows lock is harmless;
        # task membership is determined by its parent, not by this marker.
        try:
            (dest / '_trash.json').unlink(missing_ok=True)
        except OSError:
            pass
        return {'bytes': size}
    return _batch(task_ids, restore)


def purge_tasks(task_ids, confirm=False):
    if not confirm:
        raise HTTPException(422, '永久删除需要明确确认')
    def purge(task_id):
        path, meta = _task(task_id, True)
        jobs.ensure_idle(meta)
        _tree_files(path)
        _ensure_unreferenced(path)
        size = _size(path)
        shutil.rmtree(path)
        if path.exists():
            raise OSError('Task folder remains')
        return {'bytes': size}
    return _batch(task_ids, purge)


def clean_cache(task_ids):
    def clean(task_id):
        path, meta = _task(task_id)
        jobs.ensure_idle(meta)
        _tree_files(path)
        removed = 0
        errors = []
        for item in _stale_candidates(path, meta):
            if not _within(item, path) or item == path:
                raise HTTPException(409, '缓存路径超出任务范围')
            try:
                _ensure_unreferenced(item)
                amount = _size(item)
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed += amount
            except HTTPException as exc:
                errors.append(str(exc.detail))
            except OSError:
                errors.append('部分过期缓存被占用，未完成清理')
        if errors:
            return {'bytes': removed, 'partial': True, 'warnings': errors}
        return {'bytes': removed, 'partial': False}
    return _batch(task_ids, clean)
