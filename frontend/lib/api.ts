import type {
  Finding,
  ManualFinding,
  ReviewEdit,
  FsData,
  Policy,
  Preview,
  Summary,
  Task,
  TaskListItem,
} from "./types";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: "no-store", ...init });
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    throw new Error(res.ok ? "响应格式异常" : `请求失败（${res.status}）`);
  }
  const json = await res.json();
  if (!res.ok || json.code !== 0) throw new Error(json.msg || "请求失败");
  return json.data as T;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function health() {
  return api<{ service: string; keyleak: boolean; rules: boolean }>(
    "/api/health",
  );
}

export function getEnv() {
  return api<{ user: string; home: string; cwd: string }>("/api/env");
}

export function browseFs(path = "") {
  return api<FsData>(
    `/api/fs${path ? `?path=${encodeURIComponent(path)}` : ""}`,
  );
}

export function getPolicies() {
  return api<Policy[]>("/api/policies");
}

export function createPolicy(data: Omit<Policy, "id" | "builtin">) {
  return api<Policy>("/api/policies", jsonInit("POST", data));
}

export function updatePolicy(id: string, data: Omit<Policy, "id" | "builtin">) {
  return api<Policy>(`/api/policies/${id}`, jsonInit("PUT", data));
}

export function deletePolicy(id: string) {
  return api<null>(`/api/policies/${id}`, { method: "DELETE" });
}

export function getTasks(q = "") {
  return api<TaskListItem[]>(
    `/api/tasks${q ? `?q=${encodeURIComponent(q)}` : ""}`,
  );
}

export function getTask(id: string) {
  return api<{ task: Task; summary: Summary }>(`/api/tasks/${id}`);
}

export function getPendingFindings(taskId: string) {
  return api<{ findings: Finding[]; summary: Summary }>(
    `/api/tasks/${taskId}/findings?status=pending`,
  );
}

export function deleteTask(id: string) {
  return api<null>(`/api/tasks/${id}`, { method: "DELETE" });
}

export function createTask(data: {
  source_path: string;
  name?: string;
  policy_id?: string;
  operator?: string;
  purpose?: string;
}) {
  return api<{ task: Task; warnings: string[] }>(
    "/api/tasks",
    jsonInit("POST", data),
  );
}

export function detectTask(id: string, policyId: string) {
  return api<{ task: Task; summary: Summary; warnings: string[] }>(
    `/api/tasks/${id}/detect`,
    jsonInit("POST", { policy_id: policyId, background: true }),
  );
}

export function getPreview(taskId: string, fileId: string) {
  return api<Preview>(`/api/tasks/${taskId}/files/${fileId}`);
}

export function review(
  taskId: string,
  payload: {
    items?: ReviewEdit[];
    add?: ManualFinding[];
    accept_all?: boolean;
    reject_all?: boolean;
  },
) {
  return api<{ summary: Summary; task: Task }>(
    `/api/tasks/${taskId}/review`,
    jsonInit("POST", payload),
  );
}

export function maskTask(taskId: string, acceptedOnly = false) {
  return api<{
    stats: {
      findings_applied: number;
      chars_removed: number;
      files_changed: number;
      media_changed?: number;
      mapping_items?: number;
    };
    summary: Summary;
    task: Task;
  }>(
    `/api/tasks/${taskId}/mask`,
    jsonInit("POST", { accepted_only: acceptedOnly, background: true }),
  );
}

export function restoreTask(taskId: string) {
  return api<{ files: number; tokens: number; download: string }>(
    `/api/tasks/${taskId}/restore`,
    jsonInit("POST", {}),
  );
}

export const exportUrl = (id: string, includeMapping = false) =>
  `/api/tasks/${id}/export?inline=1${includeMapping ? "&include_mapping=true" : ""}`;
export const restoreDownloadUrl = (id: string) =>
  `/api/tasks/${id}/restore/download?inline=1`;
export const mappingUrl = (id: string) => `/api/tasks/${id}/mapping`;

export async function download(
  url: string,
  fallbackName: string,
): Promise<void> {
  const res = await fetch(url);
  if (!res.ok) {
    let msg = `下载失败（${res.status}）`;
    try {
      const j = await res.json();
      if (j?.msg) msg = j.msg;
    } catch {}
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = match ? decodeURIComponent(match[1]) : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1500);
}

export function cancelTask(id: string) {
  return api<{ task: Task }>(`/api/tasks/${id}/cancel`, jsonInit("POST", {}));
}
