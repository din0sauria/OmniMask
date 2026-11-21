"""Review export content and user metadata without publishing matched plaintext."""
from copy import deepcopy
from pathlib import Path

from fastapi import HTTPException

from ..core.config import AUDIO_EXTS, IMAGE_EXTS, TEXT_EXTS, VIDEO_EXTS
from ..core.storage import load_findings
from . import workflow
from .detect.text import scan_pii
from .verification import current_verification

_SAFE_EXTENSIONS = AUDIO_EXTS | IMAGE_EXTS | TEXT_EXTS | VIDEO_EXTS | {
    '.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.java', '.c',
    '.h', '.cpp', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.sh', '.ps1',
    '.bat', '.cmd', '.sql', '.json', '.xml', '.yaml', '.yml', '.toml', '.ini',
    '.cfg', '.conf', '.properties', '.css', '.scss', '.vue', '.svelte',
    '.ipynb', '.r', '.swift', '.kt', '.dart', '.pem', '.key', '.crt', '.cer',
}


def _custom_labels(task):
    """Only user-provided labels need aliases; tokens and values stay untouched."""
    names = [str(rule.get('name', '')) for rule in task.get('policy', {}).get('custom_regex', [])]
    return {name: f'自定义规则 {index + 1}' for index, name in enumerate(dict.fromkeys(names)) if name}


def aliases(task):
    result = {}
    for index, fi in enumerate(task['files'], 1):
        entry = task['artifacts']['files'][fi['id']]
        suffix = Path(entry['path']).suffix.lower()
        if suffix not in _SAFE_EXTENSIONS:
            suffix = '.txt' if entry.get('media') in ('text', 'code') or fi.get('is_text') else '.bin'
        result[fi['id']] = f"file_{index:04d}{suffix}"
    return result


def preflight(task, anonymize_metadata=False, include_mapping=False):
    workflow.ready(task, load_findings(task['id']))
    workflow.verify_sources(task)
    if task.get('status') not in ('masked', 'exported'):
        raise HTTPException(409, '请先执行脱敏')
    for fi in task['files']:
        path, entry = workflow.artifact_path(task, fi['id'])
        if workflow.fingerprint(path)['sha256'] != entry['sha256']:
            raise HTTPException(409, '脱敏缓存已改变，请重新执行脱敏')
    verification = current_verification(task)
    status = verification['status']
    issues, candidates = [], []
    metadata = [('task.name', task.get('name', ''), None),
                ('task.operator', task.get('operator', ''), None),
                ('task.purpose', task.get('purpose', ''), None),
                ('policy.name', task.get('policy', {}).get('name', ''), None)]
    metadata.extend(('file.path', fi['path'], fi['id']) for fi in task['files'])
    metadata.extend((f'policy.custom_regex[{index}].name', rule.get('name', ''), None)
                    for index, rule in enumerate(task.get('policy', {}).get('custom_regex', [])))
    for field, content, file_id in metadata:
        for label in sorted({hit['type_label'] for hit in scan_pii(str(content), {})}):
            candidates.append({'field': field, 'file_id': file_id, 'type_label': label,
                               'omitted_from_export': bool(anonymize_metadata)})
    if candidates and not anonymize_metadata:
        issues.append({'code': 'metadata_candidates', 'severity': 'warning',
                       'message': '文件名或任务备注等元信息中发现敏感候选；可隐藏元信息，或明确复核后保留。'})
    if status in ('not_run', 'stale'):
        issues.append({'code': 'verification_not_current', 'severity': 'notice',
                       'message': '尚未完成当前产物的残留复查；导出不代表已经验证无残留。'})
    elif status != 'complete':
        issues.append({'code': 'verification_incomplete', 'severity': 'warning',
                       'message': '当前产物残留复查未完整完成，请检查原因或明确复核后继续。'})
    if verification.get('candidate_count', 0):
        issues.append({'code': 'residual_candidates', 'severity': 'warning',
                       'message': f"当前产物复查发现{verification['candidate_count']}处敏感候选，需人工判断和处理。"})
    if include_mapping:
        issues.append({'code': 'mapping_plaintext', 'severity': 'notice',
                       'message': '映射表包含敏感原文；隐藏元信息不会移除其中的明文，请单独保管。'})
    return {'revision': task.get('revision', 0), 'issues': issues,
            'verification_status': status, 'candidate_count': verification.get('candidate_count', 0),
            'metadata_candidates': candidates, 'anonymize_metadata': bool(anonymize_metadata),
            'include_mapping': bool(include_mapping),
            'requires_acknowledgement': any(issue['severity'] == 'warning' for issue in issues),
            'limitations': ['元信息检查复用通用PII规则，并不覆盖所有文件名或备注中的秘密。',
                            '残留复查复用当前检测器，无候选不等于安全。']}


def anonymize_report(report, task):
    result = deepcopy(report)
    names = aliases(task)
    custom_labels = _custom_labels(task)
    path_aliases = {}
    for fi in task['files']:
        path_aliases[fi['path']] = names[fi['id']]
        path_aliases[task['artifacts']['files'][fi['id']]['path']] = names[fi['id']]
    result['task'].update(name='脱敏任务', operator='', purpose='')
    result['policy']['name'] = '已应用策略'
    for item, fi in zip(result['files'], task['files']):
        item['path'] = names[fi['id']]
        processing = item.get('processing') or {}
        if processing.get('media') in ('text', 'code') and isinstance(processing.get('output'), dict):
            processing['output']['format'] = Path(names[fi['id']]).suffix.lstrip('.')
    for item in result['summary'].get('by_file', []):
        item['name'] = path_aliases.get(item['name'], 'file')
    for key in ('by_rule', 'by_type'):
        for item in result['summary'].get(key, []):
            item['name'] = custom_labels.get(item['name'], item['name'])
    result['reversibility']['text_files'] = [path_aliases.get(name, 'file') for name in result['reversibility']['text_files']]
    for item in result.get('verification', {}).get('files', []):
        item['path'] = names.get(item.get('file_id'), path_aliases.get(item.get('path'), 'file'))
    result['export_metadata'] = {'anonymized': True, 'mapping_values_removed': False}
    return result


def anonymize_mapping(mapping, task):
    """Hide descriptive metadata while preserving explicit restoration material."""
    result = deepcopy(mapping)
    names = aliases(task)
    paths = {fi['path']: names[fi['id']] for fi in task['files']}
    custom_labels = _custom_labels(task)
    for item in result.get('items', []):
        item['file_path'] = paths.get(item.get('file_path'), 'file')
        if item.get('rule_name') in custom_labels:
            item['rule_name'] = custom_labels[item['rule_name']]
            item['type_label'] = '自定义规则'
        else:
            item['type_label'] = custom_labels.get(item.get('type_label'), item.get('type_label', ''))
    return result
