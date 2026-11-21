import type { Finding, FindingStatus } from "./types";

export type ReviewGroup = {
  id: string;
  members: Finding[];
  start?: number;
  end?: number;
  status: FindingStatus | "mixed";
  label: string;
  track: string;
};

export function findingInterval(
  finding: Finding,
): { start: number; end: number } | null {
  const start = finding.start ?? finding.frame_ts;
  const end =
    finding.end ??
    (finding.frame_ts != null
      ? finding.frame_ts + (finding.frame_duration ?? 0.05)
      : undefined);
  return start != null &&
    end != null &&
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    end > start
    ? { start: Math.max(0, start), end }
    : null;
}

function intersectionOverUnion(a: number[], b: number[]): number {
  const left = Math.max(a[0], b[0]),
    top = Math.max(a[1], b[1]);
  const right = Math.min(a[0] + a[2], b[0] + b[2]);
  const bottom = Math.min(a[1] + a[3], b[1] + b[3]);
  const intersection = Math.max(0, right - left) * Math.max(0, bottom - top);
  const union = a[2] * a[3] + b[2] * b[3] - intersection;
  return union > 0 ? intersection / union : 0;
}

function compatible(previous: Finding, next: Finding): boolean {
  if (
    previous.file_id !== next.file_id ||
    previous.rule_name !== next.rule_name ||
    previous.value !== next.value ||
    previous.media !== next.media ||
    previous.source !== next.source ||
    !!previous.bbox !== !!next.bbox ||
    (previous.track_index ?? 0) !== (next.track_index ?? 0)
  )
    return false;
  const a = findingInterval(previous),
    b = findingInterval(next);
  if (!a || !b || b.start < a.start) return false;
  // Human rectangles remain explicit independent operations, even if nearby.
  if (previous.source === "manual" || next.source === "manual") return false;
  if (previous.bbox && next.bbox) {
    if (
      previous.frame_index == null ||
      next.frame_index == null ||
      next.frame_index !== previous.frame_index + 1
    )
      return false;
    const gap = Math.min(0.5, Math.max(0.08, a.end - a.start));
    return (
      b.start <= a.end + gap &&
      intersectionOverUnion(previous.bbox, next.bbox) >= 0.35
    );
  }
  return b.start <= a.end + 0.15;
}

/** Presentation-only grouping. It never rewrites frame identities or drops findings. */
export function groupFindings(findings: Finding[]): ReviewGroup[] {
  const sorted = [...findings].sort(
    (a, b) =>
      (findingInterval(a)?.start ?? Infinity) -
        (findingInterval(b)?.start ?? Infinity) ||
      (a.frame_index ?? 0) - (b.frame_index ?? 0) ||
      a.id.localeCompare(b.id),
  );
  const groups: ReviewGroup[] = [];
  const activeBuckets = new Map<string, ReviewGroup[]>();
  for (const finding of sorted) {
    const interval = findingInterval(finding);
    let group: ReviewGroup | undefined;
    const key = JSON.stringify([
      finding.file_id,
      finding.rule_name,
      finding.value,
      finding.media,
      finding.source,
      finding.track_index ?? 0,
      !!finding.bbox,
    ]);
    const active = (activeBuckets.get(key) ?? []).filter(
      (g) => interval && g.end != null && g.end + 0.5 >= interval.start,
    );
    if ((finding.media === "video" || finding.media === "audio") && interval) {
      // Choose the closest compatible box; simultaneous objects keep separate chains.
      const candidates = active.filter((g) =>
        compatible(g.members[g.members.length - 1], finding),
      );
      group = candidates.sort((a, b) => {
        if (!finding.bbox) return (b.end ?? 0) - (a.end ?? 0);
        return (
          intersectionOverUnion(
            b.members[b.members.length - 1].bbox!,
            finding.bbox,
          ) -
          intersectionOverUnion(
            a.members[a.members.length - 1].bbox!,
            finding.bbox,
          )
        );
      })[0];
    }
    if (!group) {
      group = {
        id: finding.id,
        members: [],
        start: interval?.start,
        end: interval?.end,
        status: finding.status,
        label: finding.type_label || finding.rule_name,
        track: finding.bbox
          ? `画面 · ${finding.type_label || finding.rule_name}`
          : `音轨 ${finding.track_index ?? 0}`,
      };
      groups.push(group);
      if (interval) active.push(group);
    }
    if (group.members.length && group.status !== finding.status)
      group.status = "mixed";
    group.members.push(finding);
    if (interval) {
      group.start = Math.min(group.start ?? interval.start, interval.start);
      group.end = Math.max(group.end ?? interval.end, interval.end);
    }
    if (interval) activeBuckets.set(key, active);
  }
  return groups;
}

export function groupReviewItems(group: ReviewGroup, status: FindingStatus) {
  return group.members.map((finding) => ({ id: finding.id, status }));
}
