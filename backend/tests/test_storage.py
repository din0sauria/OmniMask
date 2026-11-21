"""Windows sharing failures must never erase a saved task or masquerade as 404."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.core import storage


def test_failed_replace_preserves_previous_json(tmp_path, monkeypatch):
    target = tmp_path / 'meta.json'
    target.write_text('{"revision": 1}', encoding='utf-8')
    monkeypatch.setattr(storage.time, 'sleep', lambda _: None)
    def blocked(*args):
        raise PermissionError('sharing violation')
    monkeypatch.setattr(storage.os, 'replace', blocked)
    with pytest.raises(PermissionError):
        storage.write_json(target, {'revision': 2})
    assert json.loads(target.read_text()) == {'revision': 1}
    assert list(tmp_path.glob('*.tmp')) == []


def test_transient_read_failure_retries(tmp_path, monkeypatch):
    target = tmp_path / 'meta.json'
    target.write_text('{"id": "saved"}', encoding='utf-8')
    original = Path.read_text
    calls = []
    def read(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError('sharing violation')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(storage.time, 'sleep', lambda _: None)
    assert storage.read_json(target) == {'id': 'saved'}
    assert len(calls) == 2


def test_corrupt_task_is_not_missing(tmp_path, monkeypatch):
    target = tmp_path / 'meta.json'
    target.write_text('{', encoding='utf-8')
    monkeypatch.setattr(storage.time, 'sleep', lambda _: None)
    with pytest.raises(json.JSONDecodeError):
        storage.read_json(target)
    assert storage.read_json(tmp_path / 'missing.json') is None


def test_concurrent_readers_never_see_partial_task(tmp_path):
    target = tmp_path / 'meta.json'
    storage.write_json(target, {'revision': 0, 'payload': 'x' * 10000})
    def writer():
        for revision in range(30):
            storage.write_json(target, {'revision': revision, 'payload': 'x' * 10000})
    def reader():
        for _ in range(100):
            value = storage.read_json(target)
            assert value is not None and value['payload'] == 'x' * 10000
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(writer), *(pool.submit(reader) for _ in range(3))]
        for future in futures:
            future.result()
