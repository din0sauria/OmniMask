import type { Verification } from "./types";

export type ExportPreflight = {
  revision: number;
  issues: { code: string; message: string; severity: string }[];
  verification_status: string;
  metadata_candidates: {
    field: string;
    file_id?: string;
    type_label: string;
    omitted_from_export?: boolean;
  }[];
  requires_acknowledgement: boolean;
  limitations?: string[];
};
async function quality<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (
    !(response.headers.get("content-type") || "").includes("application/json")
  )
    throw new Error(
      response.ok
        ? "质量检查响应格式异常"
        : `质量检查请求失败（${response.status}）`,
    );
  const result = await response.json();
  if (!response.ok || result.code !== 0)
    throw new Error(result.msg || "质量检查请求失败");
  return result.data as T;
}
export function verifyTask(id: string) {
  return quality<{ verification?: Verification }>(
    `/api/quality/tasks/${id}/verify`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ background: true }),
    },
  );
}
export function getExportPreflight(
  id: string,
  anonymizeMetadata: boolean,
  includeMapping = false,
) {
  return quality<ExportPreflight>(
    `/api/quality/tasks/${id}/preflight?anonymize_metadata=${anonymizeMetadata}&include_mapping=${includeMapping}`,
  );
}
export function qualityExportUrl(
  id: string,
  options: {
    includeMapping: boolean;
    anonymizeMetadata: boolean;
    acknowledged: boolean;
    expectedRevision: number;
  },
) {
  return `/api/tasks/${id}/export?inline=1&include_mapping=${options.includeMapping}&anonymize_metadata=${options.anonymizeMetadata}&acknowledge_preflight=${options.acknowledged}&expected_revision=${options.expectedRevision}`;
}
