"""Thin adapter around keyleak: merged rule matching + keyleak's filters."""
import contextlib
import functools
import io
import json
import re
import sys
import threading
import warnings
from pathlib import Path
from urllib.parse import urlparse

from ...core.config import KEYLEAK_DIR, MAX_SCAN_BYTES, RULES_FILE
from ..ingest import read_text

_lock = threading.Lock()
_state: dict | None = None


def _read_json(p: str):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_txt(p: str):
    with open(p, "r", encoding="utf-8") as f:
        return [line.strip() for line in f]


def _init() -> dict:
    global _state
    with _lock:
        if _state is not None:
            return _state
        warnings.filterwarnings("ignore", category=FutureWarning)
        key = str(KEYLEAK_DIR)
        if key not in sys.path:
            sys.path.insert(0, key)

        from base_func.base_func import entropy
        import filter.key_value_Filter as kvf
        from filter.filter_multireason import check_multi_reason
        from filter.filter_substr import combine_substr
        from get_strings.filter_strings import filter_strings

        # keyleak reloads several MB of word lists per hit; cache them once.
        kvf.read_json = functools.lru_cache(maxsize=64)(_read_json)
        kvf.load_txt = functools.lru_cache(maxsize=64)(_read_txt)

        compiled = []
        for r in _read_json(str(RULES_FILE)):
            try:
                compiled.append((r, re.compile(r["regex"])))
            except re.error:
                continue

        _state = {
            "entropy": entropy,
            "kvf": kvf,
            "check_multi": check_multi_reason,
            "combine_substr": combine_substr,
            "filter_strings": filter_strings,
            "rules": compiled,
        }
        return _state


def _label(rule_name: str, series: str) -> str:
    s = rule_name.lower()
    if series == "private" or "private-key" in s:
        return "私钥"
    if series == "jwt" or s == "jwt":
        return "JWT Token"
    if series == "jdbc":
        return "数据库连接串"
    if series == "uri":
        return "URL 凭据"
    if series == "custom":
        return "自定义规则"
    if "password" in s or "passwd" in s:
        return "密码"
    if "aws" in s:
        return "AWS 密钥"
    if "github" in s:
        return "GitHub Token"
    if "slack" in s:
        return "Slack Token"
    if "token" in s:
        return "Token"
    if "api" in s and "key" in s:
        return "API 密钥"
    if "certificate" in s or series == "certification":
        return "证书"
    return rule_name


def _confidence(info: dict, value: str, st: dict) -> float:
    if info.get("is_mechanical") == "human":
        c = 0.55
    elif info.get("series") in ("private", "jwt", "jdbc", "uri"):
        c = 0.92
    elif info.get("series") == "generic":
        c = 0.78
    else:
        c = 0.86
    if len(value) >= 20:
        c += 0.05
    if info.get("entropy") and st["entropy"](value) >= info["entropy"] + 0.5:
        c += 0.03
    return round(min(c, 0.99), 2)


def _span_of(match: re.Match, rule: re.Pattern, info: dict) -> tuple[str, int, int, str]:
    series = info.get("series")
    groups = rule.groups or 0
    if series == "generic" and groups >= 2:
        return match.group(2), match.start(2), match.end(2), match.group(1)
    if groups == 1:
        return match.group(0) if match.group(1) is None else match.group(1), match.start(1), match.end(1), ""
    return match.group(0), match.start(0), match.end(0), ""


def _make_hit(content: str, path: str, value: str, start: int, end: int, match: str,
              prefix: str, name: str, series: str, mech: str, regex: str, need_kv: bool) -> dict:
    return {
        "file": path,
        "value": value,
        "match": match,
        "prefix": prefix,
        "rule_name": name,
        "is_mechanical": mech,
        "series": series,
        "filter_count": [],
        "word_weight": 0,
        "line_start": content.count("\n", 0, start) + 1,
        "line_end": content.count("\n", 0, end) + 1,
        "col_start": start - content.rfind("\n", 0, start),
        "col_end": end - content.rfind("\n", 0, end),
        "index_start": start,
        "index_end": end,
        "regex": regex,
        "need_keyvalue": need_kv,
    }


def scan_text(content: str, path: str, policy: dict) -> dict:
    st = _init()
    blocked = set(policy.get("blocked_series") or [])
    raw: list[dict] = []
    custom_raw: list[dict] = []
    warnings_out: list[str] = []

    for info, rule in st["rules"]:
        series = info.get("series", "")
        if series in blocked:
            continue
        for m in rule.finditer(content):
            try:
                if series == "uri":
                    val = urlparse(m.group(0)).password
                    if not val:
                        continue
                    pos = m.group(0).find(val)
                    value, start, end, prefix = val, m.start(0) + pos, m.start(0) + pos + len(val), ""
                else:
                    value, start, end, prefix = _span_of(m, rule, info)
            except (IndexError, ValueError):
                continue
            if not value or len(value) < 5:
                continue
            if series == "private" and len(value) < 128:
                continue
            if info.get("entropy") and st["entropy"](value) < info["entropy"]:
                continue
            raw.append(_make_hit(content, path, value, start, end, m.group(0), prefix,
                                 info["rule_name"], series, info.get("is_mechanical", "machine"),
                                 info.get("regex", ""), bool(info.get("need_keyvalue"))))

    for cr in policy.get("custom_regex") or []:
        try:
            cre = re.compile(cr["regex"])
        except (re.error, KeyError):
            warnings_out.append(f"自定义规则无效：{cr.get('name', '?')}")
            continue
        for m in cre.finditer(content):
            if cre.groups == 1:
                value, start, end = m.group(1), m.start(1), m.end(1)
            else:
                value, start, end = m.group(0), m.start(0), m.end(0)
            if not value or len(value) < 2:
                continue
            hit = _make_hit(content, path, value, start, end, m.group(0), "",
                            cr.get("name", "自定义规则"), "custom", "machine", cr["regex"], False)
            hit["custom_confidence"] = float(cr.get("confidence", 0.9))
            custom_raw.append(hit)

    with contextlib.redirect_stdout(io.StringIO()):
        kept = [h for h in raw if st["kvf"].key_value_filter_single(h)]
        kept = st["check_multi"](kept + custom_raw)
        kept, _, _ = st["combine_substr"](kept)

    normal = [h for h in kept if h["series"] != "custom"]
    custom = [h for h in kept if h["series"] == "custom"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            normal = st["filter_strings"](path, normal)
    except Exception:
        pass
    kept = normal + custom
    kept = [h for h in kept if h["series"] != "certification"]
    kept = [h for h in kept if 0 <= h["index_start"] < h["index_end"] <= len(content)]

    out: list[dict] = []
    seen: set[tuple] = set()
    for h in kept:
        if content[h["index_start"]:h["index_end"]] != h["value"]:
            pos = content.find(h["value"], max(0, h["index_start"] - 200))
            if pos < 0:
                continue
            h["index_start"], h["index_end"] = pos, pos + len(h["value"])
        key = (h["rule_name"], h["index_start"], h["index_end"])
        key2 = (h["value"], h["index_start"], h["index_end"])
        if key in seen or key2 in seen:
            continue
        seen.add(key)
        seen.add(key2)
        conf = h.pop("custom_confidence", None)
        out.append({
            "file_path": path,
            "rule_name": h["rule_name"],
            "series": h["series"],
            "is_mechanical": h["is_mechanical"],
            "value": h["value"],
            "match": h["match"][:2000],
            "line_start": h["line_start"],
            "line_end": h["line_end"],
            "col_start": h["col_start"],
            "col_end": h["col_end"],
            "index_start": h["index_start"],
            "index_end": h["index_end"],
            "confidence": conf if conf is not None else _confidence(
                {"is_mechanical": h["is_mechanical"], "series": h["series"], "entropy": None}, h["value"], st),
            "risk": "high",
            "type_label": _label(h["rule_name"], h["series"]),
        })
    for f in out:
        f["risk"] = "high" if f["confidence"] >= 0.8 else "medium" if f["confidence"] >= 0.6 else "low"
    out.sort(key=lambda x: (x["index_start"], x["index_end"]))
    if len(out) > 3000:
        warnings_out.append('单文件命中超过3000条，仅保留前3000条；检查结果不完整')
    return {"findings": out[:3000], "warnings": warnings_out}


def _is_text_file(path: Path) -> bool:
    try:
        with path.open('rb') as stream:
            data = stream.read(8192)
    except OSError:
        return False
    if b"\x00" in data:
        return False
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            if enc == "utf-8" and len(data) % 3:
                try:
                    text = data.decode(enc, errors="ignore")
                    break
                except UnicodeDecodeError:
                    pass
            continue
    if text is None:
        return False
    if not text:
        return True
    printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
    return printable / len(text) > 0.85


def detect_files(files: list[dict], policy: dict) -> dict:
    _init()
    findings: list[dict] = []
    scanned = skipped = 0
    warnings_out: list[str] = []
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
                warnings_out.append(f"文件过大已跳过：{f['path']}")
                continue
        except OSError:
            skipped += 1
            continue
        try:
            content, _ = read_text(abs_path)
        except OSError:
            skipped += 1
            continue
        try:
            res = scan_text(content, str(abs_path), policy)
        except Exception as e:  # keep the task usable even if one file trips keyleak
            warnings_out.append(f"{f['path']} 检测失败：{type(e).__name__}")
            continue
        scanned += 1
        warnings_out.extend(res["warnings"])
        for item in res["findings"]:
            item["file_id"] = f["id"]
            findings.append(item)
    return {"findings": findings, "scanned": scanned, "skipped": skipped, "warnings": warnings_out}
