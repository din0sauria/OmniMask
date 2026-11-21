export type StoredTask = {
  id: string;
  name: string;
  status: string;
  bytes: number;
  stale_cache_bytes: number;
  busy: boolean;
  deleted_at?: string | null;
};

export type StorageUsage = {
  tasks: StoredTask[];
  trash: StoredTask[];
  totals: {
    tasks_bytes: number;
    trash_bytes: number;
    stale_cache_bytes: number;
  };
  logical_bytes: boolean;
  notes: string[];
  warnings: { id: string; error: string }[];
};

export type MaintenanceOperation =
  | "trash"
  | "restore"
  | "purge"
  | "clean-cache";
export type MaintenanceResult = {
  id: string;
  ok: boolean;
  bytes?: number;
  error?: string;
  partial?: boolean;
  warnings?: string[];
  deleted_at?: string;
};
export type MaintenanceResponse = {
  results: MaintenanceResult[];
  logical_bytes: boolean;
  notes: string[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/maintenance/${path}`, {
    cache: "no-store",
    ...init,
  });
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error(
      `任务存储请求失败（${response.status}），请检查本地服务后刷新。`,
    );
  }
  const payload = await response.json();
  if (!response.ok || payload.code !== 0)
    throw new Error(payload.msg || payload.detail || "任务存储操作失败");
  return payload.data as T;
}

export function getStorageUsage() {
  return request<StorageUsage>("storage");
}

export function maintainTasks(
  operation: MaintenanceOperation,
  taskIds: string[],
  confirm = false,
) {
  return request<MaintenanceResponse>(operation, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task_ids: taskIds,
      ...(operation === "purge" ? { confirm } : {}),
    }),
  });
}
