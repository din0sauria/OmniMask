"""Text PII detection: regex rules (phone / id / email / bank card) + light NER."""
import re
from pathlib import Path

from ...core.config import MAX_SCAN_BYTES
from ..ingest import read_text
from .code import _is_text_file

_PHONE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_ID = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)"
)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9._%+-])")
_DIGITS = re.compile(r"(?<!\d)[\d][\d -]{11,20}\d(?!\d)")
_NAME = re.compile(
    r"(?:姓名|名字|联系人|用户|客户|作者|署名|员工|本人|经办人|收件人|寄件人|持卡人)\s*[:：]?\s*([一-龥]{2,4})"
)
_ADDR = re.compile(
    r"[一-龥]{2,10}(?:省|市|自治区|特别行政区)"
    r"[一-龥]{1,12}(?:市|区|县|旗)"
    r"[一-龥]{1,24}(?:路|街|道|巷|镇|乡|村|号|室|栋|单元|楼|大厦|小区|弄)"
)

_ID_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_C = "10X98765432"


def _valid_id(s: str) -> bool:
    if len(s) != 18:
        return False
    try:
        y, m, d = int(s[6:10]), int(s[10:12]), int(s[12:14])
    except ValueError:
        return False
    if not (1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
        return False
    total = sum(int(s[i]) * _ID_W[i] for i in range(17))
    return _ID_C[total % 11] == s[17].upper()


def _luhn(s: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(s)):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _bare_digits(text: str, taken: set[tuple[int, int]]) -> list[tuple[str, int, int]]:
    """Bank-card candidates: 13–19 digit runs that pass Luhn and don't overlap phone/id."""
    out = []
    for m in _DIGITS.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if not (13 <= len(digits) <= 19) or not _luhn(digits):
            continue
        if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
            continue
        out.append((digits, m.start(), m.end()))
    return out


def _ner(text: str) -> list[dict]:
    """Name / address via light rules; model NER is layered on top when available."""
    out = []
    for m in _NAME.finditer(text):
        out.append((m.group(1), m.start(1), m.end(1), "name", "姓名", 0.6))
    for m in _ADDR.finditer(text):
        val, s = m.group(0), m.start(0)
        for pref in ("家庭住址", "通讯地址", "联系地址", "住址", "地址"):
            if val.startswith(pref):
                val, s = val[len(pref):], s + len(pref)
                break
        if len(val) >= 4:
            out.append((val, s, s + len(val), "address", "地址", 0.6))
    return out


def _spans(text: str, start: int, end: int) -> tuple[int, int, int, int]:
    ls = text.count("\n", 0, start) + 1
    le = text.count("\n", 0, end) + 1
    cs = start - text.rfind("\n", 0, start)
    ce = end - text.rfind("\n", 0, end)
    return ls, le, cs, ce


def scan_pii(content: str, policy: dict) -> list[dict]:
    enabled = set(policy.get("pii_types") or [])
    want = lambda k: not enabled or k in enabled
    hits: list[dict] = []
    taken: set[tuple[int, int]] = set()

    if want("phone"):
        for m in _PHONE.finditer(content):
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) != 11:
                continue
            hits.append(_make(content, digits, m.start(), m.end(), "phone", "手机号", 0.95))
            taken.add((m.start(), m.end()))

    if want("id_card"):
        for m in _ID.finditer(content):
            v = m.group(0).upper()
            if not _valid_id(v):
                continue
            hits.append(_make(content, v, m.start(), m.end(), "id_card", "身份证号", 0.97))
            taken.add((m.start(), m.end()))

    if want("email"):
        for m in _EMAIL.finditer(content):
            hits.append(_make(content, m.group(0), m.start(), m.end(), "email", "邮箱", 0.9))

    if want("bank_card"):
        for v, s, e in _bare_digits(content, taken):
            hits.append(_make(content, v, s, e, "bank_card", "银行卡号", 0.93))

    if want("name") or want("address"):
        for v, s, e, rule, label, conf in _ner(content):
            if want(rule):
                hits.append(_make(content, v, s, e, rule, label, conf))

    hits.sort(key=lambda h: (h["index_start"], h["index_end"]))
    return hits


def _make(content: str, value: str, start: int, end: int, rule: str, label: str, conf: float) -> dict:
    ls, le, cs, ce = _spans(content, start, end)
    return {
        "rule_name": rule,
        "series": "pii",
        "is_mechanical": "machine",
        "value": value,
        "match": value,
        "line_start": ls,
        "line_end": le,
        "col_start": cs,
        "col_end": ce,
        "index_start": start,
        "index_end": end,
        "confidence": conf,
        "risk": "high" if conf >= 0.8 else "medium",
        "type_label": label,
    }


def detect_text_files(files: list[dict], policy: dict) -> dict:
    findings: list[dict] = []
    scanned = skipped = 0
    warnings: list[str] = []
    for f in files:
        abs_path = Path(f["abs"])
        if not abs_path.exists():
            continue
        try:
            if not _is_text_file(abs_path):
                skipped += 1
                continue
            if abs_path.stat().st_size > MAX_SCAN_BYTES:
                skipped += 1
                warnings.append(f"文件过大已跳过：{f['path']}")
                continue
            content, _ = read_text(abs_path)
        except OSError:
            skipped += 1
            continue
        scanned += 1
        for h in scan_pii(content, policy):
            h["file_id"] = f["id"]
            h["file_path"] = f["path"]
            findings.append(h)
    return {"findings": findings, "scanned": scanned, "skipped": skipped, "warnings": warnings}
