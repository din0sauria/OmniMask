import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import TASKS_DIR


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(n: int = 12) -> str:
    return uuid.uuid4().hex[:n]


def task_dir(task_id: str) -> Path:
    return TASKS_DIR / task_id


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 7:
                # Windows may briefly lock the target (AV/indexer); fall back to direct write.
                path.write_text(text, encoding="utf-8")
                tmp.unlink(missing_ok=True)
                return
            time.sleep(0.05 * (i + 1))


def load_task(task_id: str) -> dict | None:
    return read_json(task_dir(task_id) / "meta.json")


def save_task(task: dict) -> None:
    write_json(task_dir(task["id"]) / "meta.json", task)


def load_findings(task_id: str) -> list[dict]:
    return read_json(task_dir(task_id) / "findings.json", []) or []


def save_findings(task_id: str, findings: list[dict]) -> None:
    write_json(task_dir(task_id) / "findings.json", findings)
