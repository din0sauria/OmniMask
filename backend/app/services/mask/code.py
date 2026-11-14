import base64
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path

from ...core.storage import now
from ..ingest import read_text
from ..report import build_report, render_markdown

DEFAULT_KEY = "omnimask-local"
_ENC_RE = re.compile(r"enc:([A-Za-z0-9_-]+)")


def _keystream(key: str, n: int) -> bytes:
    out, c = b"", 0
    while len(out) < n:
        out += hashlib.sha256(f"{key}:{c}".encode()).digest()
        c += 1
    return out[:n]


def encrypt_value(value: str, key: str) -> str:
    data = value.encode("utf-8")
    ks = _keystream(key, len(data))
    return base64.urlsafe_b64encode(bytes(a ^ b for a, b in zip(data, ks))).decode()


def decrypt_value(token: str, key: str) -> str:
    data = base64.urlsafe_b64decode(token.encode())
    ks = _keystream(key, len(data))
    return bytes(a ^ b for a, b in zip(data, ks)).decode("utf-8")


def _offset(value: str, amount: int, round_to: int) -> str:
    if not value.isdigit() or round_to < 1:
        return mask_value(value, "mask", 4)
    n = int(value) + amount
    n = round(n / round_to) * round_to if round_to > 1 else n
    return str(n)


def mask_value(value: str, mode: str, keep: int, policy: dict | None = None) -> str:
    policy = policy or {}
    if mode == "delete":
        return ""
    if mode == "replace":
        return value  # replaced via token in mask_text
    if mode == "encrypt":
        return "enc:" + encrypt_value(value, policy.get("crypto_key") or DEFAULT_KEY)
    if mode == "offset":
        return _offset(value, int(policy.get("offset_amount", 0)), int(policy.get("offset_round", 1)))
    if keep <= 0 or len(value) <= keep * 2 + 2:
        return "*" * min(max(len(value), 6), 16)
    return value[:keep] + "*" * min(len(value) - keep * 2, 24) + value[-keep:]


def token_for(value: str, order: int, reversible: bool) -> str:
    if reversible:
        return f"__OMNI_SECRET_{order:04d}__"
    return f"__MASKED_{hashlib.sha1(value.encode('utf-8')).hexdigest()[:10]}__"


def eligible(findings: list[dict], policy: dict, accepted_only: bool = False) -> list[dict]:
    min_conf = float(policy.get("min_confidence", 0) or 0)
    out = []
    for f in findings:
        if f.get("status") == "rejected":
            continue
        if accepted_only and f.get("status") != "accepted":
            continue
        if float(f.get("confidence", 0)) < min_conf:
            continue
        out.append(f)
    return out


def build_tokens(findings: list[dict], policy: dict) -> dict[str, str]:
    """Stable value -> token map; the same secret always gets the same token."""
    reversible = bool(policy.get("reversible"))
    ordered = sorted(findings, key=lambda f: (f.get("file_path", ""), f.get("index_start", 0)))
    values: list[str] = []
    for f in ordered:
        if f["value"] not in values:
            values.append(f["value"])
    return {v: token_for(v, i + 1, reversible) for i, v in enumerate(values)}


def _replacement(f: dict, policy: dict, tokens: dict[str, str]) -> str:
    mode = f.get("mode") or policy.get("mode", "mask")
    if mode == "replace":
        return tokens.get(f["value"]) or token_for(f["value"], 1, bool(policy.get("reversible")))
    return mask_value(f["value"], mode, int(policy.get("mask_keep", 4)), policy)


def mask_text(text: str, findings: list[dict], policy: dict, tokens: dict[str, str]) -> tuple[str, int, int]:
    hits = sorted(findings, key=lambda f: f["index_start"], reverse=True)
    limit = len(text)
    applied = 0
    removed = 0
    for f in hits:
        s, e = int(f["index_start"]), int(f["index_end"])
        if s < 0 or e > len(text) or e <= s or text[s:e] != f["value"]:
            pos = text.find(f["value"], max(0, s - 300))
            if pos < 0:
                continue
            s, e = pos, pos + len(f["value"])
        if e > limit:
            continue  # overlapping an already masked region
        text = text[:s] + _replacement(f, policy, tokens) + text[e:]
        limit = s
        applied += 1
        removed += e - s
    return text, applied, removed


def compute(task: dict, findings: list[dict], accepted_only: bool = False) -> dict:
    """Compute masked outputs in memory; never writes to the source directory."""
    policy = task.get("policy") or {}
    use = eligible(findings, policy, accepted_only)
    tokens = build_tokens(use, policy)
    by_file: dict[str, list[dict]] = {}
    for f in use:
        by_file.setdefault(f["file_id"], []).append(f)

    stats = {"findings_applied": 0, "chars_removed": 0, "files_changed": 0}
    outputs: dict[str, tuple[str, str]] = {}
    if use:
        for fi in task["files"]:
            hits = by_file.get(fi["id"], [])
            fi["findings"] = len(hits)
            if not hits:
                continue
            p = Path(fi["abs"])
            if not p.exists():
                continue
            try:
                text, enc = read_text(p)
            except OSError:
                continue
            new, applied, removed = mask_text(text, hits, policy, tokens)
            if applied:
                outputs[fi["id"]] = (new, enc)
                stats["findings_applied"] += applied
                stats["chars_removed"] += removed
                stats["files_changed"] += 1

    mapping = build_mapping(task, findings, accepted_only) if policy.get("reversible") else None
    if mapping is not None:
        stats["mapping_items"] = len(mapping["items"])
    return {"stats": stats, "outputs": outputs, "mapping": mapping, "tokens": tokens, "policy": policy}


def build_mapping(task: dict, findings: list[dict], accepted_only: bool = False) -> dict | None:
    policy = task.get("policy") or {}
    if not policy.get("reversible"):
        return None
    use = eligible(findings, policy, accepted_only)
    tokens = build_tokens(use, policy)
    items = []
    for value, token in tokens.items():
        first = next((f for f in use if f["value"] == value), None)
        items.append({
            "token": token,
            "value": value,
            "file_path": first["file_path"] if first else "",
            "line": first["line_start"] if first else 0,
            "rule_name": first["rule_name"] if first else "",
            "type_label": first["type_label"] if first else "",
        })
    return {
        "task_id": task["id"],
        "policy_id": policy.get("id"),
        "reversible": True,
        "created_at": now(),
        "items": items,
    }


def preview_file(task: dict, fi: dict, findings: list[dict]) -> dict:
    policy = task.get("policy") or {}
    text, _ = read_text(Path(fi["abs"]))
    use = [f for f in findings if f["file_id"] == fi["id"] and f.get("status") != "rejected"]
    tokens = build_tokens(use, policy)
    masked, applied, _ = mask_text(text, use, policy, tokens)
    return {"text": text, "masked": masked, "applied": applied}


def _write_zip(task: dict, findings: list[dict], prefix: str, accepted_only: bool = False,
               media: dict | None = None) -> Path:
    res = compute(task, findings, accepted_only)
    report = build_report(task, findings)
    policy = task.get("policy") or {}
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"omnimask_{task['id']}_", suffix=".zip", delete=False
    )
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
        for fi in task["files"]:
            p = Path(fi["abs"])
            if not p.exists():
                continue
            mdata = (media or {}).get(fi["id"])
            entry = res["outputs"].get(fi["id"])
            if mdata is not None:
                data, ext = mdata
                name = Path(fi["path"]).with_suffix(ext).as_posix()
                zf.writestr(f"{prefix}/{name}", data)
            elif entry is not None:
                text, enc = entry
                zf.writestr(f"{prefix}/{fi['path']}", text.encode(enc))
            else:
                zf.write(p, f"{prefix}/{fi['path']}")
        zf.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=2))
        zf.writestr("report.md", render_markdown(report))
    return Path(tmp.name)


def build_export_zip(task: dict, findings: list[dict], include_mapping: bool = False,
                     media: dict | None = None) -> Path:
    path = _write_zip(task, findings, "masked", media=media)
    mapping = build_mapping(task, findings)
    if include_mapping and mapping is not None:
        # append mapping.json to the freshly written archive
        with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mapping.json", json.dumps(mapping, ensure_ascii=False, indent=2))
    return path


def build_restore_zip(task: dict, findings: list[dict], mapping: dict) -> Path:
    lookup = {item["token"]: item["value"] for item in mapping.get("items", [])}
    key = (task.get("policy") or {}).get("crypto_key") or DEFAULT_KEY
    res = compute(task, findings)
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"omnimask_{task['id']}_restored_", suffix=".zip", delete=False
    )
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
        for fi in task["files"]:
            p = Path(fi["abs"])
            if not p.exists():
                continue
            entry = res["outputs"].get(fi["id"])
            if entry is not None:
                text, enc = entry
                for token, value in lookup.items():
                    text = text.replace(token, value)
                text = _ENC_RE.sub(lambda m: decrypt_value(m.group(1), key), text)
                zf.writestr(f"restored/{fi['path']}", text.encode(enc))
            else:
                zf.write(p, f"restored/{fi['path']}")
    return Path(tmp.name)


def restore_info(task: dict, findings: list[dict], mapping: dict) -> dict:
    lookup = {item["token"]: item["value"] for item in mapping.get("items", [])}
    files = sum(1 for fi in task["files"] if Path(fi["abs"]).exists())
    return {"files": files, "tokens": len(lookup)}
