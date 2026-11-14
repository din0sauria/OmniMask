"""Audio detection: ASR -> text PII on transcript. Needs faster-whisper; degrades."""
import functools
from pathlib import Path

from ...core.config import MAX_MEDIA_BYTES
from .text import scan_pii


@functools.lru_cache(maxsize=1)
def _model():
    try:
        from faster_whisper import WhisperModel
        return WhisperModel("base", device="cpu", compute_type="int8")
    except Exception:
        return None


def _transcribe(path: Path) -> list[dict] | None:
    m = _model()
    if m is None:
        return None
    try:
        segments, _ = m.transcribe(str(path))
        return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
    except Exception:
        return None


def detect_audio(path: Path, policy: dict) -> tuple[list[dict], list[str]]:
    segs = _transcribe(path)
    if segs is None:
        return [], ["音频检测需要 faster-whisper（首次会下载模型）：cd backend && uv sync --group media"]
    findings: list[dict] = []
    for seg in segs:
        for hit in scan_pii(seg["text"], policy):
            f = dict(hit)
            f["start"] = round(seg["start"], 2)
            f["end"] = round(seg["end"], 2)
            f["media"] = "audio"
            f["type_label"] = f"语音{hit['type_label']}"
            f["line_start"] = f["line_end"] = f["col_start"] = f["col_end"] = 0
            f["index_start"] = f["index_end"] = 0
            findings.append(f)
    return findings, []


def detect_audio_files(files: list[dict], policy: dict) -> dict:
    findings: list[dict] = []
    scanned = skipped = 0
    warnings: list[str] = []
    for f in files:
        p = Path(f["abs"])
        if not p.exists():
            continue
        if p.stat().st_size > MAX_MEDIA_BYTES:
            skipped += 1
            warnings.append(f"文件过大已跳过：{f['path']}")
            continue
        hits, w = detect_audio(p, policy)
        scanned += 1
        warnings.extend(w)
        for h in hits:
            h["file_id"] = f["id"]
            h["file_path"] = f["path"]
            findings.append(h)
    return {"findings": findings, "scanned": scanned, "skipped": skipped, "warnings": warnings}
