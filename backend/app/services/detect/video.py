"""Video detection: sample frames -> image detect, audio track -> audio detect."""
import subprocess
import tempfile
from pathlib import Path

from ...core.config import MAX_MEDIA_BYTES
from .audio import detect_audio
from .image import _cv, detect_image


def _to_mjpeg(video: Path) -> Path | None:
    tmp = tempfile.NamedTemporaryFile(suffix=".avi", delete=False)
    tmp.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-an", "-c:v", "mjpeg", "-q:v", "3", tmp.name],
            capture_output=True, check=True, timeout=900,
        )
        return Path(tmp.name)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        return None


def _open_capture(cv2, path: Path):
    """Open a video with cv2, transcoding via ffmpeg when the codec is unsupported."""
    cap = cv2.VideoCapture(str(path))
    if cap.isOpened():
        return cap, None
    cap.release()
    mj = _to_mjpeg(path)
    if mj is None:
        return None, None
    cap = cv2.VideoCapture(str(mj))
    if not cap.isOpened():
        cap.release()
        mj.unlink(missing_ok=True)
        return None, None
    return cap, mj


def _extract_audio(video: Path) -> Path | None:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", tmp.name],
            capture_output=True, check=True, timeout=300,
        )
        return Path(tmp.name)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        return None


def detect_video(path: Path, policy: dict) -> tuple[list[dict], list[str]]:
    findings: list[dict] = []
    warnings: list[str] = []
    cv2 = _cv()
    if cv2 is not None:
        cap, tmp = _open_capture(cv2, path)
        if cap is not None:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            sample_every = max(int(fps), 1)
            idx = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if idx % sample_every == 0:
                    ftmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                    ftmp.close()
                    cv2.imwrite(ftmp.name, frame)
                    hits, _ = detect_image(Path(ftmp.name), policy)
                    for h in hits:
                        h["media"] = "video"
                        h["frame_ts"] = round(idx / fps, 2)
                        findings.append(h)
                    Path(ftmp.name).unlink(missing_ok=True)
                idx += 1
            cap.release()
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        else:
            warnings.append("视频抽帧需要 opencv 与 ffmpeg：cd backend && uv sync --group media")
    else:
        warnings.append("视频抽帧需要 opencv：cd backend && uv sync --group media")

    audio = _extract_audio(path)
    if audio is not None:
        ahits, _ = detect_audio(audio, policy)
        for h in ahits:
            h["media"] = "video"
            findings.append(h)
        audio.unlink(missing_ok=True)
    else:
        warnings.append("视频音轨检测需要 ffmpeg（未找到）")
    return findings, warnings


def detect_video_files(files: list[dict], policy: dict) -> dict:
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
        hits, w = detect_video(p, policy)
        scanned += 1
        warnings.extend(w)
        for h in hits:
            h["file_id"] = f["id"]
            h["file_path"] = f["path"]
            findings.append(h)
    return {"findings": findings, "scanned": scanned, "skipped": skipped, "warnings": warnings}
