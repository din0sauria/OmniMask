import hashlib
import json
from collections import Counter

from ..core.storage import now


def summarize(findings: list[dict]) -> dict:
    by_type = Counter(f.get("type_label") or f.get("rule_name") for f in findings)
    by_rule = Counter(f.get("rule_name", "") for f in findings)
    by_file = Counter(f.get("file_path", "") for f in findings)
    status = Counter(f.get("status", "pending") for f in findings)
    risk = Counter(f.get("risk", "medium") for f in findings)
    return {
        "total": len(findings),
        "pending": status.get("pending", 0),
        "accepted": status.get("accepted", 0),
        "rejected": status.get("rejected", 0),
        "high": risk.get("high", 0),
        "medium": risk.get("medium", 0),
        "low": risk.get("low", 0),
        "by_type": [{"name": k, "count": v} for k, v in by_type.most_common()],
        "by_rule": [{"name": k, "count": v} for k, v in by_rule.most_common(20)],
        "by_file": [{"name": k, "count": v} for k, v in by_file.most_common(50)],
    }


def build_report(task: dict, findings: list[dict]) -> dict:
    policy = task.get("policy") or {}
    version = hashlib.sha1(
        json.dumps(policy, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    return {
        "title": "OmniMask 脱敏报告",
        "generated_at": now(),
        "task": {
            "id": task["id"],
            "name": task["name"],
            "status": task["status"],
            "created_at": task["created_at"],
            "updated_at": task["updated_at"],
            "operator": task.get("operator", ""),
            "purpose": task.get("purpose", ""),
            "source": task.get("source", "upload"),
        },
        "policy": {
            "id": policy.get("id"),
            "name": policy.get("name"),
            "version": version,
            "mode": policy.get("mode"),
            "reversible": policy.get("reversible"),
            "mask_keep": policy.get("mask_keep"),
            "min_confidence": policy.get("min_confidence"),
            "custom_regex": len(policy.get("custom_regex") or []),
        },
        "files": task.get("files", []),
        "summary": summarize(findings),
        "stats": task.get("stats", {}),
        "scope": "代码密钥 / 敏感串检测（code）",
    }


def render_markdown(report: dict) -> str:
    t = report["task"]
    p = report["policy"]
    s = report["summary"]
    lines = [
        "# OmniMask 脱敏报告",
        "",
        f"- 任务名称：{t['name']}",
        f"- 任务编号：`{t['id']}`",
        f"- 操作人：{t['operator'] or '未填写'}",
        f"- 数据用途：{t['purpose'] or '未填写'}",
        f"- 处理时间：{report['generated_at']}",
        f"- 策略：{p['name']}（{p['mode']}，{'可逆' if p['reversible'] else '不可逆'}）· 版本 {p['version']}",
        f"- 范围：{report['scope']}",
        "",
        "## 统计",
        "",
        f"- 文件数：{len(report['files'])}",
        f"- 命中总数：{s['total']}（已接受 {s['accepted']} / 待确认 {s['pending']} / 已忽略 {s['rejected']}）",
        f"- 风险分布：高 {s['high']} / 中 {s['medium']} / 低 {s['low']}",
        "",
        "| 类型 | 数量 |",
        "|---|---|",
    ]
    for item in s["by_type"]:
        lines.append(f"| {item['name']} | {item['count']} |")
    lines += ["", "## 文件明细", "", "| 文件 | 大小 | 命中 |", "|---|---|---|"]
    for f in report["files"]:
        lines.append(f"| {f['path']} | {f['size']} B | {f.get('findings', 0)} |")
    lines += [
        "",
        "## 说明",
        "",
        "- 本报告由 OmniMask 自动生成，仅覆盖代码密钥与敏感串检测。",
        "- 源文件目录未被修改；脱敏结果在导出时即时生成。",
        "- 可逆策略的映射表请与导出包分开保管。",
        "- 导出文件中的敏感内容已按策略处理，建议上线前人工复核。",
    ]
    return "\n".join(lines)
