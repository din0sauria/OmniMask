"""Media masking: blur image regions, mute / pitch-shift audio, process video."""
import io
import subprocess
import tempfile
from pathlib import Path

from .code import eligible
from ..detect.video import _open_capture


def _cv():
    try:
        import cv2
        return cv2
    except Exception:
        return None


def _accepted(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f.get("status") != "rejected"]


def mask_image_bytes(path: Path, findings: list[dict], policy: dict) -> bytes | None:
    cv2 = _cv()
    if cv2 is None:
        return None
    boxes = [f["bbox"] for f in _accepted(findings) if f.get("bbox")]
    if not boxes:
        return None
    img = cv2.imread(str(path))
    if img is None:
        return None
    for (x, y, w, h) in boxes:
        x, y, w, h = max(0, int(x)), max(0, int(y)), int(w), int(h)
        roi = img[y:y + h, x:x + w]
        if roi.size == 0:
            continue
        img[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (0, 0), 15)
    ok, buf = cv2.imencode(Path(path).suffix or ".png", img)
    return buf.tobytes() if ok else None


def _np():
    try:
        import numpy as np
        return np
    except Exception:
        return None


def _read_audio(path: Path):
    """Return (data, sr) as float mono/stereo, via soundfile then ffmpeg fallback."""
    try:
        import soundfile as sf
        return sf.read(str(path))
    except Exception:
        pass
    try:
        import subprocess
        import soundfile as sf
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-ac", "1", "-ar", "16000", tmp.name],
            capture_output=True, check=True, timeout=300,
        )
        data, sr = sf.read(tmp.name)
        Path(tmp.name).unlink(missing_ok=True)
        return data, sr
    except Exception:
        return None


def _pitch(data, sr: int, steps: int):
    np = _np()
    if np is None or steps == 0:
        return data
    factor = 2 ** (steps / 12)
    n = len(data)

    def _shift(x):
        src = np.arange(0, n, factor)
        src = src[src < n - 1]
        if len(src) < 2:
            return x
        shifted = np.interp(src, np.arange(n), x)
        out_idx = np.linspace(0, len(shifted) - 1, n)
        return np.interp(out_idx, np.arange(len(shifted)), shifted)

    if data.ndim == 2:
        return np.stack([_shift(data[:, c]) for c in range(data.shape[1])], axis=1)
    return _shift(data)


def mask_audio_bytes(path: Path, findings: list[dict], policy: dict) -> bytes | None:
    np = _np()
    try:
        import soundfile as sf
    except Exception:
        return None
    loaded = _read_audio(path)
    if loaded is None:
        return None
    data, sr = loaded
    segs = [(f["start"], f["end"]) for f in _accepted(findings) if "start" in f and f.get("end") is not None]
    if segs:
        for s, e in segs:
            a, b = int(float(s) * sr), int(float(e) * sr)
            if 0 <= a < b <= len(data):
                data[a:b] = 0
    voice = policy.get("voice_change")
    if voice == "pitch":
        data = _pitch(data, sr, 4)
    elif voice == "robot":
        t = np.arange(len(data)) / sr
        carrier = 0.5 + 0.5 * np.sin(2 * np.pi * 400 * t)
        if data.ndim == 2:
            carrier = carrier[:, None]
        data = (data * carrier).astype(data.dtype)
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV")
    return buf.getvalue()


def _extract_audio_track(video: Path) -> Path | None:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "2", "-ar", "48000", tmp.name],
            capture_output=True, check=True, timeout=300,
        )
        return Path(tmp.name)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        return None


def _mux_audio(video: Path, audio: bytes) -> bytes | None:
    awav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    awav.close()
    Path(awav.name).write_bytes(audio)
    out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    out.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-i", awav.name,
             "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
             "-shortest", out.name],
            capture_output=True, check=True, timeout=600,
        )
    except Exception:
        Path(awav.name).unlink(missing_ok=True)
        Path(out.name).unlink(missing_ok=True)
        return None
    data = Path(out.name).read_bytes()
    Path(awav.name).unlink(missing_ok=True)
    Path(out.name).unlink(missing_ok=True)
    return data


def mask_video_bytes(path: Path, findings: list[dict], policy: dict) -> bytes | None:
    cv2 = _cv()
    if cv2 is None:
        return None
    cap, src = _open_capture(cv2, path)
    if cap is None:
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    out = cv2.VideoWriter(tmp.name, fourcc, fps, (w, h))
    by_ts = {}
    for f in _accepted(findings):
        if f.get("bbox"):
            by_ts.setdefault(round(float(f.get("frame_ts", 0)), 2), []).append(f["bbox"])
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        ts = round(idx / fps, 2)
        for (x, y, bw, bh) in by_ts.get(ts, []):
            x, y, bw, bh = max(0, int(x)), max(0, int(y)), int(bw), int(bh)
            roi = frame[y:y + bh, x:x + bw]
            if roi.size:
                frame[y:y + bh, x:x + bw] = cv2.GaussianBlur(roi, (0, 0), 15)
        out.write(frame)
        idx += 1
    cap.release()
    out.release()
    if src is not None:
        src.unlink(missing_ok=True)
    video_bytes = Path(tmp.name).read_bytes()

    # keep the (muted / voice-changed) audio track instead of dropping it
    audio_track = _extract_audio_track(path)
    if audio_track is not None:
        af = [f for f in _accepted(findings) if "start" in f and f.get("end") is not None]
        audio_bytes = mask_audio_bytes(audio_track, af, policy)
        audio_track.unlink(missing_ok=True)
        if audio_bytes is not None:
            muxed = _mux_audio(Path(tmp.name), audio_bytes)
            if muxed is not None:
                Path(tmp.name).unlink(missing_ok=True)
                return muxed
    Path(tmp.name).unlink(missing_ok=True)
    return video_bytes


def compute_media(task: dict, findings: list[dict], accepted_only: bool = False) -> dict:
    policy = task.get("policy") or {}
    use = eligible(findings, policy, accepted_only)
    by_file: dict[str, list[dict]] = {}
    for f in use:
        by_file.setdefault(f["file_id"], []).append(f)

    outputs: dict[str, tuple[bytes, str]] = {}
    stats = {"media_changed": 0}
    for fi in task["files"]:
        hits = by_file.get(fi["id"], [])
        if not hits:
            continue
        p = Path(fi["abs"])
        if not p.exists():
            continue
        kind = hits[0].get("media")
        data = None
        ext = Path(p).suffix or ".bin"
        if kind == "image":
            data = mask_image_bytes(p, hits, policy)
        elif kind == "audio":
            data = mask_audio_bytes(p, hits, policy)
            ext = ".wav"
        elif kind == "video":
            data = mask_video_bytes(p, hits, policy)
            ext = ".mp4"
        if data is not None:
            outputs[fi["id"]] = (data, ext)
            stats["media_changed"] += 1
    return {"stats": stats, "outputs": outputs}
