from typing import Literal

from pydantic import BaseModel, Field

MaskMode = Literal["mask", "delete", "replace", "encrypt", "offset"]
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
    pii_types: list[str] = []
    crypto_key: str = ""
    offset_amount: int = 0
    offset_round: int = 1
    voice_change: Literal["", "pitch", "robot"] = ""
    use_model: bool = False
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
    pii_types: list[str] = []
    crypto_key: str = ""
    offset_amount: int = 0
    offset_round: int = 1
    voice_change: Literal["", "pitch", "robot"] = ""
    use_model: bool = False


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
    modality: str = ""
    voice_change: Literal["", "pitch", "robot"] | None = None


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
    modality: str = "code"


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
    media: Literal["image", "audio", "video"] | None = None
    bbox: tuple[float, float, float, float] | None = None
    start: float | None = None
    end: float | None = None
    frame_index: int | None = None
    frame_pts: int | None = None
    frame_time_base: str | None = None
    frame_ts: float | None = None
    frame_duration: float | None = None
    track_index: int = 0
    timing_precision: Literal["word", "segment", "manual"] | None = None
    source: Literal["automatic", "manual"] = "automatic"
    transcript_spans: list[dict] = []


class ReviewItem(BaseModel):
    id: str
    status: FindingStatus | None = None
    mode: MaskMode | Literal["default"] | None = None
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    bbox: tuple[float, float, float, float] | None = None
    track_index: int | None = Field(default=None, ge=0)


class ManualFinding(BaseModel):
    file_id: str
    media: Literal["audio", "video", "image"]
    start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    bbox: tuple[float, float, float, float] | None = None
    track_index: int = Field(default=0, ge=0)
    value: str = Field(default="人工标记", max_length=200)


class ReviewIn(BaseModel):
    items: list[ReviewItem] = []
    accept_all: bool = False
    reject_all: bool = False
    add: list[ManualFinding] = []


class DetectIn(BaseModel):
    policy_id: str | None = None
    background: bool = False


class MaskIn(BaseModel):
    accepted_only: bool = False
    background: bool = False


class RestoreIn(BaseModel):
    mapping: dict | None = None
