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
  builtin: boolean;
};

export type TaskFile = {
  id: string;
  path: string;
  abs?: string;
  name: string;
  size: number;
  is_text: boolean;
  lines: number;
  findings: number;
};

export type Task = {
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
    mask?: { findings_applied: number; chars_removed: number; files_changed: number; media_changed?: number; mapping_items?: number };
    [key: string]: unknown;
  };
  source: string;
  source_path: string;
  root: string;
  modality: string;
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
  file: TaskFile;
  original: string;
  masked: string;
  applied: number;
  findings: Finding[];
  binary: boolean;
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
