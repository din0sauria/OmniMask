"""Modality helpers: split task files into text / code / image / audio / video."""
from pathlib import Path

from ..core.config import AUDIO_EXTS, IMAGE_EXTS, TEXT_EXTS, VIDEO_EXTS


def kind_of(path: str) -> str:
    e = Path(path).suffix.lower()
    if e in IMAGE_EXTS:
        return "image"
    if e in AUDIO_EXTS:
        return "audio"
    if e in VIDEO_EXTS:
        return "video"
    if e in TEXT_EXTS:
        return "text"
    return "code"


def partition(files: list[dict]) -> dict[str, list[dict]]:
    groups = {"text": [], "code": [], "image": [], "audio": [], "video": []}
    for f in files:
        groups[kind_of(f["path"])].append(f)
    return groups


def label_of(groups: dict[str, list[dict]]) -> str:
    active = [k for k, v in groups.items() if v]
    if len(active) == 1:
        return active[0]
    return "mixed" if active else "code"
