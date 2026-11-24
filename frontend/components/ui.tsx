import type { ReactNode } from "react";
import type { FindingStatus } from "@/lib/types";

export const STATUS_LABELS: Record<string, string> = {
  created: "待检测",
  queued: "排队中",
  detecting: "检测中",
  masking: "脱敏中",
  failed: "处理失败",
  cancelled: "已取消",
  interrupted: "已中断",
  partial: "部分检测失败",
  detected: "待审核",
  reviewed: "已审核",
  masked: "已脱敏",
  exported: "已导出",
};

const MODE_LABELS: Record<string, string> = {
  mask: "打码",
  delete: "删除",
  replace: "代号替换",
  encrypt: "旧实验加密（已停用）",
  offset: "数字偏移",
};

export const modeText = (mode: string) => MODE_LABELS[mode] || mode;

export function StatusPill({ status }: { status: string }) {
  return <span className={`pill status-${status}`}>{STATUS_LABELS[status] || status}</span>;
}

export function RiskPill({ risk }: { risk: string }) {
  return <span className={`pill risk-${risk}`}>{risk === "high" ? "高风险" : risk === "medium" ? "中风险" : "低风险"}</span>;
}

export function FindingStatusPill({ status }: { status: FindingStatus }) {
  const label = status === "accepted" ? "已接受" : status === "rejected" ? "已忽略" : "待确认";
  return <span className={`pill fstatus-${status}`}>{label}</span>;
}

export function Empty({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: string }) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
    </div>
  );
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function formatTime(iso: string): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
