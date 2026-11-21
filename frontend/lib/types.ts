export type MaskMode = "mask" | "delete" | "replace" | "encrypt" | "offset";
export type FindingStatus = "pending" | "accepted" | "rejected";

export type CustomRule = {
  name: string;
  regex: string;
  confidence: number;
};

export type Policy = {
  id: string;
  name: string;
  description: string;
  mode: MaskMode;
  reversible: boolean;
  mask_keep: number;
  min_confidence: number;
  blocked_series: string[];
  custom_regex: CustomRule[];
  pii_types: string[];
  crypto_key: string;
  offset_amount: number;
  offset_round: number;
  voice_change: string;
  use_model: boolean;
  builtin: boolean;
};

export type Detection = {
  state: "complete" | "partial" | "failed" | "skipped" | "not_checked";
  warnings?: string[];
  metadata?: { duration?: number; width?: number; height?: number; audio_tracks?: { index?: number; track_index?: number; duration?: number }[] };
  transcript?: { start: number; end: number; text: string; track_index?: number }[];
};
export type TaskJob = { id: string; operation: string; status: string; stage: string; completed: number; total: number; error?: string };
export type ReviewEdit = { id: string; status?: string; mode?: string; start?: number; end?: number; bbox?: [number, number, number, number]; track_index?: number };
export type ManualFinding = Omit<ReviewEdit, "id" | "status" | "mode"> & { file_id: string; media: "audio" | "video" | "image"; value?: string };

export type TaskFile = {
  id: string;
  path: string;
  abs?: string;
  name: string;
  size: number;
  is_text: boolean;
  lines: number;
  findings: number;
  detection?: Detection;
};

export type Task = {
  revision?: number;
  job?: TaskJob;
  verification?: Verification;
  id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  policy_id: string;
  policy: Policy;
  operator: string;
  purpose: string;
  files: TaskFile[];
  stats: {
    total?: number;
    scanned?: number;
    skipped?: number;
    warnings?: string[];
    mask?: { findings_applied: number; chars_removed: number; files_changed: number; media_changed?: number; media_findings_applied?: number; mapping_items?: number };
    [key: string]: unknown;
  };
  source: string;
  source_path: string;
  root: string;
  modality: string;
};

export type Verification = {
  revision: number;
  artifacts: { file_id: string; sha256: string }[];
  status: "running" | "complete" | "partial" | "failed" | "cancelled" | "interrupted";
  checked_at?: string;
  candidate_count: number;
  files: { file_id: string; path: string; media?: string; status: string; candidate_count: number;
    candidates?: { type_label?: string; rule_name?: string; start?: number; end?: number; frame_index?: number; line_start?: number; track_index?: number }[];
    warnings?: string[]; coverage?: { frames_scanned?: number; frame_check?: string } }[];
  limitations?: string[];
};

export type Finding = {
  id: string;
  file_id: string;
  file_path: string;
  rule_name: string;
  series: string;
  is_mechanical: string;
  value: string;
  match: string;
  line_start: number;
  line_end: number;
  col_start: number;
  col_end: number;
  index_start: number;
  index_end: number;
  confidence: number;
  risk: "high" | "medium" | "low";
  status: FindingStatus;
  mode: MaskMode | null;
  type_label: string;
  bbox?: [number, number, number, number];
  media?: string;
  start?: number;
  end?: number;
  frame_ts?: number;
  frame_index?: number;
  frame_duration?: number;
  track_index?: number;
  timing_precision?: string;
  source?: string;
};

export type Summary = {
  total: number;
  pending: number;
  accepted: number;
  rejected: number;
  high: number;
  medium: number;
  low: number;
  by_type: { name: string; count: number }[];
  by_rule: { name: string; count: number }[];
  by_file: { name: string; count: number }[];
};

export type TaskListItem = {
  id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  file_count: number;
  findings: number;
  policy: string;
  source: string;
  source_path: string;
};

export type Preview = {
  masked_ready?: boolean;
  file: TaskFile;
  original: string;
  masked: string;
  applied: number;
  findings: Finding[];
  binary: boolean;
  media?: string;
  image_url?: string;
  audio_url?: string;
  video_url?: string;
  masked_url?: string;
};

export type FsEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
};

export type FsData = {
  path: string;
  parent: string;
  selected: string;
  entries: FsEntry[];
  roots: { name: string; path: string }[];
  home: string;
  truncated: boolean;
};
