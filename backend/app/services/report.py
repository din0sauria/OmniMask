import hashlib
import json
from collections import Counter

from ..core.storage import now


_PROCESSING_FIELDS = {
    'recorded', 'version', 'media', 'filter', 'required_status', 'min_confidence',
    'eligible_findings', 'excluded_findings', 'mapping_reversible_operations',
    'method', 'operations', 'mode', 'start', 'end', 'applied_operations', 'modes',
    'mask', 'replace', 'delete', 'encrypt', 'offset', 'output', 'format', 'width',
    'height', 'container', 'video_codec', 'audio_tracks', 'track_index', 'codec',
    'channels', 'sample_rate', 'mute_padding_seconds', 'voice_change', 'semitones',
    'duration_compensated', 'carrier_hz', 'duration_seconds', 'interval_basis',
    'mute_intervals_seconds', 'frames_processed', 'regions', 'bbox', 'frame_index',
    'frame_ts', 'masked_frames', 'frames_applied',
}


def _snapshot(value):
    """Never expose future/internal audit keys by blindly serializing metadata."""
    if isinstance(value, dict):
        return {key: _snapshot(item) for key, item in value.items() if key in _PROCESSING_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_snapshot(item) for item in value]
    return value


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
    from .verification import current_verification
    checked = current_verification(task)
    verification = {key: checked.get(key) for key in ('status', 'current', 'checked_at', 'candidate_count', 'limitations')}
    verification['files'] = [{key: f.get(key) for key in ('file_id', 'path', 'status', 'candidate_count', 'coverage', 'warnings')}
                             for f in checked.get('files', [])]
    policy = task.get("policy") or {}
    version = hashlib.sha1(
        json.dumps(policy, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:8]
    artifacts = task.get('artifacts') or {}
    entries = artifacts.get('files', {}) if artifacts.get('revision') == task.get('revision') else {}
    files = []
    for fi in task.get('files', []):
        saved = entries.get(fi['id'], {}).get('processing')
        record = _snapshot(saved) if saved else {'recorded': False, 'reason': '该产物没有处理快照；重新执行脱敏后可记录'}
        files.append({
            'path': fi['path'], 'size': fi['size'], 'findings': fi.get('findings', 0),
            'coverage': fi.get('detection', {}).get('state', 'not_checked'),
            'frames_scanned': fi.get('detection', {}).get('metadata', {}).get('frames_scanned'),
            'omitted_streams': fi.get('detection', {}).get('metadata', {}).get('omitted_streams', []),
            'processing': record,
        })
    text_operations = sum(f['processing'].get('mapping_reversible_operations', 0) for f in files)
    unknown = any(not f['processing'].get('recorded') or (
        f['processing'].get('media') in ('text', 'code') and f['processing'].get('operations') is None
    ) for f in files)
    reversibility = {
        'status': 'text_only' if text_operations else ('unknown' if unknown else 'none'),
        'text_mapping_operations': text_operations,
        'text_files': [f['path'] for f in files if f['processing'].get('mapping_reversible_operations', 0)],
        'media_restoration_supported': False,
        'explanation': '仅实际执行的文本代号替换可借助单独映射表还原；媒体遮挡、静音和变声不提供逆向恢复。'
                       if text_operations else ('缺少历史处理快照，无法确认是否实际执行文本代号替换；媒体不提供逆向恢复。'
                                               if unknown else '本次没有可通过代号映射还原的已执行文本操作；媒体不提供逆向恢复。'),
    }
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
            # This flag describes actual text mapping support, never the media.
            "reversible": bool(text_operations),
            "reversible_requested": bool(policy.get("reversible")),
            "mask_keep": policy.get("mask_keep"),
            "min_confidence": policy.get("min_confidence"),
            "custom_regex": len(policy.get("custom_regex") or []),
        },
        "files": files,
        "verification": verification,
        "reversibility": reversibility,
        "summary": summarize(findings),
        "stats": {k: v for k, v in task.get("stats", {}).items()
                  if k in ('total', 'scanned', 'skipped', 'mask', 'detected_at', 'masked_at')},
        "limitations": ["逐帧检查不保证模型零漏检，需人工复核", "变声仅为声音效果，未验证声纹匿名化",
                        "人工区域按帧显示区间相交处理，边界帧会整帧应用该区域遮挡",
                        "视频静音区间记录的是AAC编码前PCM范围，编码后边界可能受有损编码影响",
                        "导出前使用通用规则检查文件名及用户元信息；不能覆盖所有秘密。隐藏元信息不清除文件内容中的EXIF等嵌入元数据",
                        "旧版实验encrypt已停用，offset不属于安全加密或文本代号映射还原"],
        "scope": f"多模态检测（{task.get('modality', 'code')}）",
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
        f"- 策略：{p['name']}（文本默认模式 {p['mode']}）· 版本 {p['version']}",
        f"- 还原范围：{report['reversibility']['explanation']}",
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
    lines += ['', '## 实际处理记录', '']
    for item in report['files']:
        lines.append(f"### {item['path']}")
        processing = item['processing']
        if not processing.get('recorded'):
            lines += ['', '- 未记录：该产物没有处理快照，请重新执行脱敏。', '']
            continue
        lines += ['', '- 以下参数与该次成功生成的产物绑定；仅满足审核状态和置信度阈值的结果参与处理。',
                  '- 音频区间为秒；视频区间是AAC编码前实际处理的PCM范围。', '',
                  '```json', json.dumps(processing, ensure_ascii=False, indent=2), '```', '']
    lines += [
        '', '## 残留复查', '',
        f"- 状态：{report.get('verification', {}).get('status', 'not_run')}；候选数量：{report.get('verification', {}).get('candidate_count', 0)}。",
        '- 复查复用当前检测器，无候选不等于无敏感内容；未检查、失败和过期结果不能作为安全证明。',
        "",
        "## 说明",
        "",
        "- 本报告由 OmniMask 自动生成，覆盖代码密钥与多模态敏感信息检测。",
        "- 源文件目录未被修改；预览与导出使用同一份审核后缓存。",
        "- 仅文本代号替换支持映射还原；映射表请与导出包分开保管。",
        "- 已接受且符合阈值的内容按策略处理；被忽略或低于阈值的内容不会处理。",
        "- 检测无法保证零漏检，变声效果不等同于经过验证的声纹匿名化。",
    ]
    for item in report['files']:
        lines.append(f"- {item['path']}：检查状态 {item['coverage']}；逐帧检查数 {item.get('frames_scanned') or 0}；未透传流 {', '.join(item['omitted_streams']) or '无'}")
    review = report.get('export_review')
    if review:
        lines += ['', '## 导出复核', '',
                  f"- 产物版本：{review['revision']}；残留复查状态：{review['verification_status']}。",
                  f"- 隐藏元信息：{'是' if review['anonymize_metadata'] else '否'}；包含明文映射表：{'是' if review['include_mapping'] else '否'}。",
                  f"- 已明确确认导出提示：{'是' if review['acknowledged'] else '否'}。"]
        lines.extend('- ' + issue['message'] for issue in review['issues'])
    lines.extend('- ' + text for text in report['limitations'])
    return "\n".join(lines)
