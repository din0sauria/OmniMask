import os
import string
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/fs", tags=["filesystem"])

MAX_ENTRIES = 2000


def _roots() -> list[dict]:
    out = []
    if os.name == "nt":
        for ch in string.ascii_uppercase:
            p = Path(f"{ch}:\\")
            try:
                if p.exists():
                    out.append({"name": f"{ch}:", "path": str(p)})
            except OSError:
                continue
    else:
        out.append({"name": "/", "path": "/"})
    return out


def _default_path() -> Path:
    home = Path.home()
    return home if home.exists() else Path.cwd()


@router.get("")
def browse(path: str = ""):
    home = _default_path()
    raw = path.strip().strip('"').strip("'")
    p = Path(raw).expanduser() if raw else home
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p = p.resolve()
    except OSError:
        raise HTTPException(400, f"路径无效：{raw}")

    selected = ""
    if p.is_file():
        selected = str(p)
        p = p.parent
    if not p.exists() or not p.is_dir():
        raise HTTPException(404, f"目录不存在：{p}")

    entries = []
    truncated = False
    try:
        with os.scandir(p) as it:
            for e in it:
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    is_dir = e.is_dir()
                except OSError:
                    continue
                size = 0
                if not is_dir:
                    try:
                        size = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                entries.append({
                    "name": e.name,
                    "path": e.path,
                    "is_dir": is_dir,
                    "size": size,
                })
    except PermissionError:
        raise HTTPException(403, "没有权限读取该目录")
    except OSError as e:
        raise HTTPException(400, f"无法读取目录：{e}")

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "path": str(p),
            "parent": "" if p.parent == p else str(p.parent),
            "selected": selected,
            "entries": entries,
            "roots": _roots(),
            "home": str(home),
            "truncated": truncated,
        },
    }
