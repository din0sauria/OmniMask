from fastapi import HTTPException

from ..core.config import POLICY_FILE
from ..core.storage import new_id, read_json, write_json
from ..models.schemas import Policy, PolicyIn

BUILTIN: list[dict] = [
    {
        "id": "standard",
        "name": "标准脱敏",
        "description": "保留首尾少量字符，中间打码。适合日常代码分享。",
        "mode": "mask",
        "reversible": False,
        "mask_keep": 4,
        "min_confidence": 0.0,
        "blocked_series": ["certification"],
        "custom_regex": [],
        "builtin": True,
    },
    {
        "id": "delete",
        "name": "彻底删除",
        "description": "直接删除命中的密钥内容，不可恢复。",
        "mode": "delete",
        "reversible": False,
        "mask_keep": 0,
        "min_confidence": 0.0,
        "blocked_series": ["certification"],
        "custom_regex": [],
        "builtin": True,
    },
    {
        "id": "reversible",
        "name": "可逆代号替换",
        "description": "用唯一代号替换文本密钥，映射表按需生成，可单独下载并还原文本。",
        "mode": "replace",
        "reversible": True,
        "mask_keep": 4,
        "min_confidence": 0.0,
        "blocked_series": ["certification"],
        "custom_regex": [],
        "builtin": True,
    },
    {
        "id": "strict",
        "name": "严格模式",
        "description": "只保留置信度 >= 0.7 的结果，减少误报。",
        "mode": "mask",
        "reversible": False,
        "mask_keep": 4,
        "min_confidence": 0.7,
        "blocked_series": ["certification", "Uri"],
        "custom_regex": [],
        "builtin": True,
    },
    {
        "id": "text-pii",
        "name": "文本 PII 标准脱敏",
        "description": "检测手机号 / 身份证 / 邮箱 / 银行卡 / 姓名 / 地址并打码。",
        "mode": "mask",
        "reversible": False,
        "mask_keep": 2,
        "min_confidence": 0.0,
        "blocked_series": [],
        "custom_regex": [],
        "pii_types": [],
        "builtin": True,
    },
    {
        "id": "media",
        "name": "媒体脱敏",
        "description": "图像人脸、二维码、OCR 敏感文字遮挡，音频敏感片段静音；本地模型增强需另行启用。",
        "mode": "mask",
        "reversible": False,
        "mask_keep": 4,
        "min_confidence": 0.0,
        "blocked_series": [],
        "custom_regex": [],
        "voice_change": "",
        "use_model": False,
        "builtin": True,
    },
]


def _custom() -> list[dict]:
    return read_json(POLICY_FILE, []) or []


DISABLED_ENCRYPT_MESSAGE = '旧实验加密已停用：请改用掩码、删除或文本代号替换；已有文件不会被自动改写。'


def ensure_supported_generation(policy: dict, findings=()) -> None:
    """Legacy configuration stays readable, but unsafe encryption cannot run."""
    if policy.get('mode') == 'encrypt' or any(f.get('mode') == 'encrypt' for f in findings):
        raise HTTPException(422, DISABLED_ENCRYPT_MESSAGE)


def list_policies() -> list[dict]:
    return [{**p, **({'generation_disabled': True, 'disabled_reason': DISABLED_ENCRYPT_MESSAGE}
                     if p.get('mode') == 'encrypt' else {})} for p in BUILTIN + _custom()]


def get_policy(policy_id: str) -> dict:
    for p in list_policies():
        if p["id"] == policy_id:
            return p
    raise HTTPException(404, "策略不存在")


def create_policy(data: PolicyIn) -> dict:
    ensure_supported_generation(data.model_dump())
    p = Policy(id=new_id(8), **data.model_dump(), builtin=False).model_dump()
    items = _custom()
    items.append(p)
    write_json(POLICY_FILE, items)
    return p


def update_policy(policy_id: str, data: PolicyIn) -> dict:
    ensure_supported_generation(data.model_dump())
    items = _custom()
    for i, p in enumerate(items):
        if p["id"] == policy_id:
            items[i] = {**p, **data.model_dump()}
            write_json(POLICY_FILE, items)
            return items[i]
    raise HTTPException(400, "内置策略不可修改，请复制后自定义")


def delete_policy(policy_id: str) -> None:
    items = [p for p in _custom() if p["id"] != policy_id]
    if len(items) == len(_custom()):
        raise HTTPException(400, "内置策略不可删除")
    write_json(POLICY_FILE, items)
