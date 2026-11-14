from typing import Literal

from pydantic import BaseModel, Field

MaskMode = Literal["mask", "delete", "replace"]
FindingStatus = Literal["pending", "accepted", "rejected"]


class CustomRule(BaseModel):
    name: str = "自定义规则"
    regex: str
    confidence: float = 0.9


class Policy(BaseModel):
    id: str
    name: str
    description: str = ""
    mode: MaskMode = "mask"
    reversible: bool = False
    mask_keep: int = Field(default=4, ge=0, le=16)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    blocked_series: list[str] = []
    custom_regex: list[CustomRule] = []
    builtin: bool = False


class PolicyIn(BaseModel):
    name: str
    description: str = ""
    mode: MaskMode = "mask"
    reversible: bool = False
    mask_keep: int = Field(default=4, ge=0, le=16)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    blocked_series: list[str] = []
    custom_regex: list[CustomRule] = []


class FileInfo(BaseModel):
    id: str
    path: str
    abs: str = ""
    name: str
    size: int
    is_text: bool = True
    lines: int = 0
    findings: int = 0


class TaskCreateIn(BaseModel):
    source_path: str
    name: str = ""
    policy_id: str = "standard"
    operator: str = ""
    purpose: str = ""


class Task(BaseModel):
    id: str
    name: str
    status: str = "created"
    created_at: str
    updated_at: str
    policy_id: str
    policy: dict = {}
    operator: str = ""
    purpose: str = ""
    files: list[FileInfo] = []
    stats: dict = {}
    source: str = "upload"


class Finding(BaseModel):
    id: str
    file_id: str
    file_path: str
    rule_name: str
    series: str
    is_mechanical: str = "machine"
    value: str
    match: str = ""
    line_start: int
    line_end: int
    col_start: int
    col_end: int
    index_start: int
    index_end: int
    confidence: float = 0.8
    risk: str = "medium"
    status: FindingStatus = "pending"
    mode: str | None = None
    type_label: str = ""


class ReviewItem(BaseModel):
    id: str
    status: FindingStatus | None = None
    mode: MaskMode | Literal["default"] | None = None


class ReviewIn(BaseModel):
    items: list[ReviewItem] = []
    accept_all: bool = False
    reject_all: bool = False


class DetectIn(BaseModel):
    policy_id: str | None = None


class MaskIn(BaseModel):
    accepted_only: bool = False


class RestoreIn(BaseModel):
    mapping: dict | None = None
