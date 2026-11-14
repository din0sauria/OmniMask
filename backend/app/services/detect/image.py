"""Image detection: face / QR / OCR(PII). Needs opencv; degrades without it."""
import functools
from pathlib import Path

from ...core.config import MAX_FILE_BYTES
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


def _ocr(img) -> str:
    eng = _ocr_engine()
    if eng is None:
        return ""
    try:
        result, _ = eng(img)
    except Exception:
        return ""
    if not result:
        return ""
    return "\n".join(line[1] for line in result)


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


def detect_image(path: Path, policy: dict) -> tuple[list[dict], list[str]]:
    cv2 = _cv()
    if cv2 is None:
        return [], ["图像检测需要安装 media 依赖：cd backend && uv sync --group media"]
    img = cv2.imread(str(path))
    if img is None:
        return [], [f"无法读取图像：{path.name}"]
    h, w = img.shape[:2]
    findings: list[dict] = []

    try:
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for (x, y, fw, fh) in cascade.detectMultiScale(gray, 1.1, 5, minSize=(30, 30)):
            findings.append(_mh("face", "人脸", [int(x), int(y), int(fw), int(fh)], 0.85))
    except Exception:
        pass

    try:
        det = cv2.QRCodeDetector()
        data, pts, _ = det.detectAndDecode(img)
        if pts is not None:
            xs = [int(p[0]) for p in pts[0]]
            ys = [int(p[1]) for p in pts[0]]
            findings.append(_mh("qr_code", "二维码", [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)], 0.9, data))
    except Exception:
        pass

    text = _ocr(img)
    if text:
        for hit in scan_pii(text, policy):
            f = dict(hit)
            f["bbox"] = [0, 0, w, h]
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
                pass

    warnings: list[str] = []
    return findings, warnings


def detect_image_files(files: list[dict], policy: dict) -> dict:
    findings: list[dict] = []
    scanned = skipped = 0
    warnings: list[str] = []
    for f in files:
        p = Path(f["abs"])
        if not p.exists():
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
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
