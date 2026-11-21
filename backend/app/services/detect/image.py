"""Image detection: face / QR / OCR(PII). Needs opencv; degrades without it."""
import functools
import math
from pathlib import Path

from ...core.config import MAX_MEDIA_BYTES
from ..models import image_yolo
from .text import scan_pii


def _cv():
    try:
        import cv2
        return cv2
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def _ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        return None


def _ocr_lines(img) -> list[tuple[str, list[int]]]:
    """OCR each text line together with its bounding box."""
    eng = _ocr_engine()
    if eng is None:
        raise RuntimeError('OCR engine unavailable')
    try:
        result, _ = eng(img)
    except Exception:
        raise RuntimeError('OCR inference failed') from None
    if not result:
        return []
    out = []
    for line in result:
        try:
            pts, text = line[0], line[1]
            if not text:
                continue
            xs = [int(p[0]) for p in pts]
            ys = [int(p[1]) for p in pts]
            out.append((text, [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]))
        except (IndexError, ValueError, TypeError):
            continue
    return out


def _mh(rule: str, label: str, bbox: list[int], conf: float, value: str = "") -> dict:
    return {
        "rule_name": rule,
        "series": "media",
        "is_mechanical": "machine",
        "value": value,
        "match": value,
        "type_label": label,
        "line_start": 0, "line_end": 0, "col_start": 0, "col_end": 0,
        "index_start": 0, "index_end": 0,
        "confidence": conf,
        "risk": "high" if conf >= 0.8 else "medium",
        "bbox": bbox,
        "media": "image",
    }


_CLASS_ZH = {
    "biometric_face": "人脸", "biometric_fingerprint": "指纹", "biometric_palmprint": "掌纹",
    "id_card": "身份证", "hk_macau_permit": "港澳通行证", "passport": "护照",
    "employee_badge": "工牌", "license_plate": "车牌", "bank_card": "银行卡",
    "physical_key": "钥匙", "receipt": "收据", "shipping_label": "快递单",
    "official_seal": "公章", "paper": "纸张", "whiteboard": "白板",
    "sticky_note": "便签", "mobile_screen": "手机屏幕", "monitor_screen": "显示器屏幕",
    "medical_wristband": "医用腕带", "qr_code": "二维码", "barcode": "条形码",
}


def _qr_region(img, cv2):
    """Retry failed QR localization at bounded scales, map back to source pixels.

    Detection (including an undecodable QR) is enough to mask a region. Resizing
    improves scale-sensitive corner finding, not target identity or decoding
    guarantees. A successful original-scale result avoids extra per-frame work.
    """
    detector = cv2.QRCodeDetector()
    height, width = img.shape[:2]
    for scale in (1.0, .75, 1.5):
        if scale > 1 and max(width, height) * scale > 2048:
            continue
        scaled = img if scale == 1 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        data, points, _ = detector.detectAndDecode(scaled)
        if points is None:
            continue
        # Use actual resized dimensions: cv2 rounds noninteger image sizes.
        sx, sy = width / scaled.shape[1], height / scaled.shape[0]
        xs = [float(p[0]) * sx for p in points[0]]
        ys = [float(p[1]) * sy for p in points[0]]
        left, top = max(0, math.floor(min(xs))-2), max(0, math.floor(min(ys))-2)
        right, bottom = min(width, math.ceil(max(xs))+2), min(height, math.ceil(max(ys))+2)
        if right > left and bottom > top:
            return data, [left, top, right-left, bottom-top]
    return None


def detect_image(path: Path, policy: dict) -> tuple[list[dict], list[str]]:
    cv2 = _cv()
    if cv2 is None:
        return [], ["图像检测需要安装 media 依赖：cd backend && uv sync --group media"]
    img = cv2.imread(str(path))
    if img is None:
        return [], [f"无法读取图像：{path.name}"]
    findings: list[dict] = []
    warnings: list[str] = []

    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for (x, y, fw, fh) in cascade.detectMultiScale(gray, 1.1, 5, minSize=(30, 30)):
            findings.append(_mh("face", "人脸", [int(x), int(y), int(fw), int(fh)], 0.85))
    except Exception:
        warnings.append('人脸检测未完成')

    try:
        qr = _qr_region(img, cv2)
        if qr:
            data, bbox = qr
            findings.append(_mh("qr_code", "二维码", bbox, 0.9, data))
    except Exception:
        warnings.append('二维码检测未完成')

    try:
        ocr_lines = _ocr_lines(img)
    except RuntimeError:
        ocr_lines = []
        warnings.append('OCR 检测未完成')
    for text, box in ocr_lines:
        for hit in scan_pii(text, policy):
            f = dict(hit)
            f["bbox"] = box
            f["media"] = "image"
            f["type_label"] = f"图中{hit['type_label']}"
            findings.append(f)

    if policy.get("use_model"):
        yolo = image_yolo()
        if yolo is not None:
            try:
                res = yolo.predict(str(path), conf=0.25, verbose=False)[0]
                have = {f["type_label"] for f in findings}
                for box in res.boxes:
                    x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                    name = yolo.names[int(box.cls[0])]
                    label = _CLASS_ZH.get(name, name)
                    if label in have:
                        continue
                    conf = float(box.conf[0])
                    findings.append(_mh(f"has:{name}", label, [x1, y1, x2 - x1, y2 - y1], conf))
            except Exception:
                warnings.append('本地图像增强模型推理失败')
        else:
            warnings.append('本地图像增强模型不可用')

    return findings, warnings


def detect_image_files(files: list[dict], policy: dict) -> dict:
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
        hits, w = detect_image(p, policy)
        scanned += 1
        warnings.extend(w)
        for h in hits:
            h["file_id"] = f["id"]
            h["file_path"] = f["path"]
            findings.append(h)
    return {"findings": findings, "scanned": scanned, "skipped": skipped, "warnings": warnings}
