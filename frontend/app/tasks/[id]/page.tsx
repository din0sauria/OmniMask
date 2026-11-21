"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  cancelTask,
  detectTask,
  download,
  getPendingFindings,
  getPolicies,
  getPreview,
  getTask,
  mappingUrl,
  maskTask,
  restoreDownloadUrl,
  restoreTask,
  review,
} from "@/lib/api";
import type {
  Finding,
  ManualFinding,
  ReviewEdit,
  Policy,
  Preview,
  Summary,
  Task,
  TaskFile,
} from "@/lib/types";
import {
  Empty,
  FindingStatusPill,
  formatBytes,
  formatTime,
  RiskPill,
  StatusPill,
} from "@/components/ui";
import {
  AlertIcon,
  CheckIcon,
  ChevronIcon,
  DownloadIcon,
  FileIcon,
  KeyIcon,
  RestoreIcon,
  ScanIcon,
  ShieldIcon,
} from "@/components/icons";

import {
  MediaEditor,
  MediaPlayer,
  type SeekRequest,
} from "@/components/media-review";
import { ReviewTimeline } from "@/components/review-timeline";
import {
  groupFindings,
  groupReviewItems,
  type ReviewGroup,
} from "@/lib/review-groups";
import {
  getExportPreflight,
  qualityExportUrl,
  verifyTask,
  type ExportPreflight,
} from "@/lib/quality-api";

type Busy =
  | ""
  | "detect"
  | "review"
  | "mask"
  | "export"
  | "restore"
  | "mapping"
  | "cancel"
  | "locate"
  | "verify";

const FLOW = ["路径", "检测", "审核", "脱敏", "导出"];

function modeText(m?: string) {
  return m === "mask"
    ? "打码"
    : m === "delete"
      ? "删除"
      : m === "replace"
        ? "代号替换"
        : m === "encrypt"
          ? "实验加密"
          : m === "offset"
            ? "数字偏移"
            : "打码";
}

function findingPos(f: Finding): string {
  if (f.start != null && f.end != null)
    return `${f.start.toFixed(1)}–${f.end.toFixed(1)}s`;
  if (f.bbox && f.bbox.length === 4) {
    const area = `区域 ${f.bbox.map((n) => Math.round(n)).join("×")}`;
    return f.frame_ts != null ? `${area} · ${f.frame_ts.toFixed(1)}s` : area;
  }
  return `L${f.line_start}${f.line_end !== f.line_start ? `-${f.line_end}` : ""}`;
}

function kindLabel(f: TaskFile): string {
  if (f.is_text) return `${f.lines} 行`;
  const e = (f.path.split(".").pop() || "").toLowerCase();
  const map: Record<string, string> = {
    jpg: "图像",
    jpeg: "图像",
    png: "图像",
    bmp: "图像",
    webp: "图像",
    tif: "图像",
    tiff: "图像",
    mp3: "音频",
    wav: "音频",
    m4a: "音频",
    flac: "音频",
    ogg: "音频",
    aac: "音频",
    wma: "音频",
    mp4: "视频",
    avi: "视频",
    mov: "视频",
    mkv: "视频",
    wmv: "视频",
    flv: "视频",
    webm: "视频",
  };
  return map[e] || "二进制";
}

const MODALITY_TEXT: Record<string, string> = {
  code: "代码密钥",
  text: "文本 PII",
  image: "图像",
  audio: "音频",
  video: "视频",
  mixed: "混合",
};

function flowState(status: string, summary: Summary | null) {
  const masked = status === "masked" || status === "exported";
  const reviewStage = ["detected", "reviewed", "masked", "exported"].includes(
    status,
  );
  const done = [
    true,
    reviewStage,
    reviewStage && summary
      ? summary.pending === 0 || summary.total === 0 || masked
      : false,
    masked,
    status === "exported",
  ];
  const current = done.findIndex((x) => !x);
  return { done, current };
}

export default function TaskPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const route = useRef(id);
  const initialNotice = useRef<{ id: string; message: string } | null>(null);
  route.current = id;
  const [task, setTask] = useState<Task | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [policyId, setPolicyId] = useState("");
  const [fileId, setFileId] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [active, setActive] = useState("");
  const [tab, setTab] = useState<"original" | "masked">("original");
  const [busy, setBusy] = useState<Busy>("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [filter, setFilter] = useState<
    "all" | "pending" | "accepted" | "rejected"
  >("all");
  const [fileSearch, setFileSearch] = useState("");
  const [resultSearch, setResultSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(20);
  const [refresh, setRefresh] = useState(0);
  const [includeMapping, setIncludeMapping] = useState(false);
  const [groupedReview, setGroupedReview] = useState(true);
  const [exportOpen, setExportOpen] = useState(false);
  const [anonymizeMetadata, setAnonymizeMetadata] = useState(true);
  const [acknowledged, setAcknowledged] = useState(false);
  const [preflight, setPreflight] = useState<ExportPreflight | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightError, setPreflightError] = useState("");
  const [preflightRefresh, setPreflightRefresh] = useState(0);
  const exportDialog = useRef<HTMLDialogElement>(null);
  const [groupConfirmation, setGroupConfirmation] = useState<{
    group: ReviewGroup;
    status: "accepted" | "rejected" | "pending";
  } | null>(null);
  const groupDialog = useRef<HTMLDialogElement>(null);
  const [loading, setLoading] = useState(true);
  const [seek, setSeek] = useState<SeekRequest>();
  const [auditionTrack, setAuditionTrack] = useState<number>();
  const [auditionWatch, setAuditionWatch] = useState<{
    token: number;
    previousJobId?: string;
  }>();
  const running =
    !!task?.job && ["queued", "running"].includes(task.job.status);
  const locked = !!busy || running;

  const load = useCallback(async () => {
    const data = await getTask(id);
    if (route.current !== id) return;
    setTask(data.task);
    setSummary(data.summary);
    setPolicyId((prev) => prev || data.task.policy_id);
    setFileId((prev) =>
      data.task.files.some((f) => f.id === prev)
        ? prev
        : (
            data.task.files.find((f) => f.findings > 0) ||
            data.task.files.find((f) => f.is_text) ||
            data.task.files[0]
          )?.id || "",
    );
  }, [id]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setTask(null);
    setSummary(null);
    setFileId("");
    setPolicyId("");
    setPreview(null);
    setError("");
    setMsg("");
    setActive("");
    setFileSearch("");
    setResultSearch("");
    setFilter("all");
    setBusy("");
    setAuditionTrack(undefined);
    setAuditionWatch(undefined);
    setSeek(undefined);
    setExportOpen(false);
    setPreflight(null);
    setAcknowledged(false);
    setGroupConfirmation(null);
    // A task may exist even when the homepage's background-detection request
    // failed. Consume its one-time message here, including Strict Mode replay.
    let taskNotice =
      initialNotice.current?.id === id ? initialNotice.current.message : "";
    try {
      const key = `omnimask:task-notice:${id}`;
      taskNotice = sessionStorage.getItem(key) || taskNotice;
      sessionStorage.removeItem(key);
    } catch {
      /* Session storage may be blocked; the task still loads normally. */
    }
    initialNotice.current = { id, message: taskNotice };
    if (taskNotice) setError(taskNotice);
    Promise.all([load(), getPolicies().then(setPolicies)])
      .catch((e) => alive && setError((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [load]);

  useEffect(() => {
    if (!fileId) return;
    let alive = true;
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(true);
    getPreview(id, fileId)
      .then((p) => alive && setPreview(p))
      .catch((e) => alive && setPreviewError((e as Error).message))
      .finally(() => alive && setPreviewLoading(false));
    return () => {
      alive = false;
    };
  }, [
    id,
    fileId,
    refresh,
    task?.job?.operation === "audition" ? "audition" : task?.job?.status,
    task?.status,
    task?.revision,
  ]);

  useEffect(() => {
    setVisibleCount(20);
  }, [fileId]);

  useEffect(() => {
    if (!running) return;
    let alive = true;
    const poll = async () => {
      try {
        const data = await getTask(id);
        if (alive) {
          setTask(data.task);
          setSummary(data.summary);
        }
      } catch (err) {
        if (alive) setError((err as Error).message);
      }
    };
    const timer = window.setInterval(poll, 1000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [running, id]);

  useEffect(() => {
    if (!auditionWatch) return;
    // The audio element starts a GET that may create an audition job. Briefly
    // discover it, then let the existing active-job poll handle completion.
    // Cached tracks create no job, so discovery always stops after four seconds.
    let alive = true;
    const deadline = Date.now() + 4000;
    const timer = window.setInterval(async () => {
      try {
        const data = await getTask(id);
        if (!alive) return;
        setTask(data.task);
        setSummary(data.summary);
        if (
          (data.task.job?.operation === "audition" &&
            data.task.job.id !== auditionWatch.previousJobId) ||
          Date.now() >= deadline
        )
          window.clearInterval(timer);
      } catch (err) {
        if (alive) {
          setError((err as Error).message);
          window.clearInterval(timer);
        }
      }
    }, 400);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [auditionWatch, id]);

  useEffect(() => {
    if (exportOpen) exportDialog.current?.showModal();
    else exportDialog.current?.close();
  }, [exportOpen]);
  useEffect(() => {
    if (groupConfirmation) groupDialog.current?.showModal();
    else groupDialog.current?.close();
  }, [groupConfirmation]);

  useEffect(() => {
    if (!exportOpen) return;
    let alive = true;
    setPreflightLoading(true);
    setPreflightError("");
    setPreflight(null);
    setAcknowledged(false);
    getExportPreflight(id, anonymizeMetadata, includeMapping)
      .then((data) => {
        if (alive) setPreflight(data);
      })
      .catch((err) => {
        if (alive) setPreflightError((err as Error).message);
      })
      .finally(() => {
        if (alive) setPreflightLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [
    exportOpen,
    anonymizeMetadata,
    includeMapping,
    preflightRefresh,
    id,
    task?.revision,
  ]);

  async function act(kind: Busy, fn: () => Promise<void>) {
    setBusy(kind);
    setError("");
    setMsg("");
    try {
      await fn();
      await load();
      setRefresh((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  const doDetect = () =>
    act("detect", async () => {
      const res = await detectTask(id, policyId);
      setSummary(res.summary);
      setFileId((prev) => {
        const cur = res.task.files.find((f) => f.id === prev);
        return cur && cur.findings > 0
          ? prev
          : res.task.files.find((f) => f.findings > 0)?.id || prev;
      });
      setTask(res.task);
      setMsg("检测任务已提交，可查看进度或取消；检测失败不会被当作无命中。");
    });

  const showPending = () =>
    act("locate", async () => {
      const data = await getPendingFindings(id);
      if (route.current !== id) return;
      const first = data.findings[0];
      if (!first) {
        setMsg("当前任务已没有待审核项，已刷新最新状态。");
        return;
      }
      setFileId(first.file_id);
      setFileSearch("");
      setFilter("pending");
      setResultSearch("");
      setVisibleCount(20);
      setActive(first.id);
      setTab("original");
      setAuditionTrack(undefined);
      setAuditionWatch(undefined);
      setSeek({ token: Date.now(), start: first.start ?? first.frame_ts ?? 0 });
      window.setTimeout(
        () =>
          document
            .getElementById("review-results")
            ?.scrollIntoView({ block: "start", behavior: "smooth" }),
        30,
      );
    });

  const doReviewOne = (
    f: Finding,
    status: "accepted" | "rejected" | "pending",
  ) =>
    act("review", async () => {
      const res = await review(id, { items: [{ id: f.id, status }] });
      setSummary(res.summary);
      setPreview(
        (prev) =>
          prev && {
            ...prev,
            findings: prev.findings.map((x) =>
              x.id === f.id ? { ...x, status } : x,
            ),
          },
      );
    });

  const doMode = (f: Finding, mode: string) =>
    act("review", async () => {
      const res = await review(id, { items: [{ id: f.id, mode }] });
      setSummary(res.summary);
      setPreview(
        (prev) =>
          prev && {
            ...prev,
            findings: prev.findings.map((x) =>
              x.id === f.id
                ? {
                    ...x,
                    mode: mode === "default" ? null : (mode as Finding["mode"]),
                  }
                : x,
            ),
          },
      );
    });

  const doBulk = (accept: boolean) =>
    act("review", async () => {
      const res = await review(
        id,
        accept ? { accept_all: true } : { reject_all: true },
      );
      setSummary(res.summary);
      setMsg(
        accept ? "已将全部待确认结果标记为接受。" : "已忽略全部待确认结果。",
      );
    });

  const doMask = () =>
    act("mask", async () => {
      const res = await maskTask(id);
      setSummary(res.summary);
      setTask(res.task);
      setMsg("脱敏任务已提交，完成后可播放预览并导出同一份处理结果。");
    });

  const doExport = () => {
    setAcknowledged(false);
    setPreflightError("");
    setExportOpen(true);
  };
  const doVerify = () =>
    act("verify", async () => {
      await verifyTask(id);
      setMsg("残留复查已提交。复查复用当前检测器，不构成零漏检或安全保证。");
    });
  const confirmExport = async () => {
    if (!preflight || busy || running) return;
    setBusy("export");
    setPreflightError("");
    try {
      const fresh = await getExportPreflight(
        id,
        anonymizeMetadata,
        includeMapping,
      );
      if (fresh.revision !== preflight.revision) {
        setPreflight(fresh);
        setAcknowledged(false);
        throw Error("处理结果已发生变化，请重新复核后再导出。");
      }
      if (
        JSON.stringify([
          fresh.issues,
          fresh.verification_status,
          fresh.metadata_candidates,
          fresh.requires_acknowledgement,
        ]) !==
        JSON.stringify([
          preflight.issues,
          preflight.verification_status,
          preflight.metadata_candidates,
          preflight.requires_acknowledgement,
        ])
      ) {
        setPreflight(fresh);
        setAcknowledged(false);
        throw Error("复查或导出提示已更新，请阅读最新结果后重新确认。");
      }
      if ((fresh.requires_acknowledgement || includeMapping) && !acknowledged)
        throw Error("请先确认已阅读本次导出的风险提示。");
      await download(
        qualityExportUrl(id, {
          includeMapping,
          anonymizeMetadata,
          acknowledged,
          expectedRevision: fresh.revision,
        }),
        `omnimask_${id}.zip`,
      );
      setExportOpen(false);
      setMsg("导出包已下载，内含同一份脱敏产物与处理报告。");
      await load();
    } catch (err) {
      setPreflightError((err as Error).message);
    } finally {
      setBusy("");
    }
  };
  const reviewGroup = (
    group: ReviewGroup,
    status: "accepted" | "rejected" | "pending",
  ) =>
    act("review", async () => {
      const result = await review(id, {
        items: groupReviewItems(group, status),
      });
      setTask(result.task);
      setSummary(result.summary);
      setMsg(
        `已将该组 ${group.members.length} 条原始结果标为${status === "accepted" ? "接受" : status === "rejected" ? "忽略" : "待确认"}；未改变逐帧处理范围。`,
      );
    });

  const doMapping = () =>
    act("mapping", async () => {
      await download(mappingUrl(id), `omnimask_${id}_mapping.json`);
      setMsg("映射表已下载，请妥善保管。");
    });
  const doRestore = () =>
    act("restore", async () => {
      const res = await restoreTask(id);
      setMsg(
        `已使用 ${res.tokens} 条映射还原 ${res.files} 个文件，正在下载还原包…`,
      );
      await download(restoreDownloadUrl(id), `omnimask_${id}_restored.zip`);
    });

  const saveLocation = async (edit: ReviewEdit | ManualFinding) => {
    setBusy("review");
    setError("");
    setMsg("");
    try {
      const res = await review(
        id,
        "id" in edit ? { items: [edit] } : { add: [edit] },
      );
      setTask(res.task);
      setSummary(res.summary);
      setRefresh((n) => n + 1);
      setMsg("处理范围已保存，请重新审核；原脱敏缓存已失效。");
    } finally {
      setBusy("");
    }
  };
  const audition = (start: number, end: number, track = 0) => {
    setTab("original");
    setAuditionTrack(track);
    setSeek({ token: Date.now(), start, end });
    if (preview?.media === "video")
      setAuditionWatch({ token: Date.now(), previousJobId: task?.job?.id });
  };
  const pick = (fid: string, forceRaw = false) => {
    setActive(fid);
    setTab("original");
    const hit = preview?.findings.find((f) => f.id === fid);
    const useGroups =
      groupedReview &&
      !forceRaw &&
      (preview?.media === "video" || preview?.media === "audio");
    const groups = useGroups ? groupFindings(preview?.findings || []) : [];
    const groupIndex = groups.findIndex((group) =>
      group.members.some((member) => member.id === fid),
    );
    const index = useGroups
      ? groupIndex
      : (preview?.findings.findIndex((f) => f.id === fid) ?? -1);
    const resultId = useGroups ? (groups[groupIndex]?.id ?? fid) : fid;
    // Overlay/text picks may refer to a result outside the current page/filter.
    setFilter("all");
    setResultSearch("");
    setVisibleCount(Math.max(20, Math.ceil((index + 1) / 20) * 20));
    setAuditionTrack(undefined);
    if (hit)
      setSeek({ token: Date.now(), start: hit.start ?? hit.frame_ts ?? 0 });
    setTimeout(() => {
      document
        .getElementById(`result-${resultId}`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 30);
  };

  if (loading) return <Empty title="正在加载任务…" />;
  if (!task || !summary) {
    return (
      <div className="card card-body">
        <Empty
          icon={<AlertIcon size={26} />}
          title="任务加载失败"
          hint={error || "任务可能已被删除"}
        />
        <div style={{ textAlign: "center" }}>
          <Link href="/" className="btn">
            返回工作台
          </Link>
        </div>
      </div>
    );
  }

  const { done, current } = flowState(task.status, summary);
  const selectedFile = task.files.find((f) => f.id === fileId);
  const currentPreview = preview?.file.id === fileId ? preview : null;
  const findings = currentPreview?.findings || [];
  const mediaGroups = groupFindings(findings);
  const selectedGroup = (group: ReviewGroup) => {
    const first = group.members[0];
    pick(first.id);
    if (!first.bbox && currentPreview?.media === "video")
      audition(group.start ?? 0, group.end ?? 0, first.track_index ?? 0);
  };
  const query = resultSearch.trim().toLowerCase();
  const shown = findings.filter(
    (f) =>
      (filter === "all" || f.status === filter) &&
      (!query ||
        `${f.type_label} ${f.rule_name} ${f.value} ${findingPos(f)}`
          .toLowerCase()
          .includes(query)),
  );
  const isTimedMedia =
    currentPreview?.media === "video" || currentPreview?.media === "audio";
  const shownGroups = mediaGroups.filter((group) =>
    group.members.some(
      (f) =>
        (filter === "all" || f.status === filter) &&
        (!query ||
          `${f.type_label} ${f.rule_name} ${f.value} ${findingPos(f)}`
            .toLowerCase()
            .includes(query)),
    ),
  );
  const visibleFiles = task.files.filter((f) =>
    f.path.toLowerCase().includes(fileSearch.trim().toLowerCase()),
  );
  const detectionBlocked = task.files.some(
    (f) => f.detection?.state !== "complete",
  );
  const policyChanged = policyId !== task.policy_id;
  const reversible =
    !!task.policy?.reversible && task.files.some((f) => f.is_text);
  const maskedDone = task.status === "masked" || task.status === "exported";
  const verification =
    task.verification?.revision === task.revision
      ? task.verification
      : undefined;
  const verificationStatus =
    verification?.status === "running" &&
    task.job?.operation === "verify" &&
    ["failed", "cancelled", "interrupted"].includes(task.job.status)
      ? task.job.status
      : verification?.status;
  const verificationLabel = verification
    ? (
        {
          running: "复查中",
          complete: verification.candidate_count
            ? "发现候选残留"
            : "当前检测器未发现候选残留",
          partial: "复查不完整",
          failed: "复查失败",
          cancelled: "复查已取消",
          interrupted: "复查已中断",
        } as Record<string, string>
      )[verificationStatus || ""] || "复查状态未知"
    : "尚未复查当前产物";
  const appliedCount =
    (task.stats.mask?.findings_applied ?? 0) +
    (task.stats.mask?.media_findings_applied ??
      task.stats.mask?.media_changed ??
      0);
  const changedFiles =
    (task.stats.mask?.files_changed ?? 0) +
    (task.stats.mask?.media_changed ?? 0);
  const maskBlockedReason = locked
    ? "请等待当前操作结束，或取消正在运行的任务。"
    : policyChanged
      ? "策略尚未应用，请重新检测后审核结果。"
      : task.status === "created"
        ? "先完成检测，再审核检测结果。"
        : detectionBlocked
          ? "有文件尚未完成检查，请查看检测提示并重新检测。"
          : summary.pending > 0
            ? `还有 ${summary.pending} 条待确认，请先接受或忽略。`
            : "";
  const nextTitle = running
    ? "任务正在处理中"
    : policyChanged
      ? "应用新的检测策略"
      : task.status === "created"
        ? "从检测开始"
        : detectionBlocked
          ? "先解决未完成的检查"
          : summary.pending > 0
            ? "确认需要处理的内容"
            : maskedDone
              ? "查看预览并导出"
              : "生成脱敏结果";
  const nextHint = running
    ? `${task.job?.stage}，可在下方查看进度或取消。`
    : policyChanged
      ? `当前结果仍基于「${task.policy.name}」。点击重新检测后，新策略才会生效。`
      : maskBlockedReason ||
        (maskedDone
          ? "原始文件保持只读，预览与导出使用同一份已审核产物。"
          : "审核已完成。点击执行脱敏，生成可预览和导出的处理结果。");

  return (
    <>
      <div className="crumbs">
        <Link href="/">工作台</Link>
        <ChevronIcon size={13} />
        <span className="mono">{task.id}</span>
      </div>

      <div className="task-head">
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">
            任务详情 · {MODALITY_TEXT[task.modality] || "多模态"}
          </div>
          <div className="task-title">
            <h1>{task.name}</h1>
            <StatusPill status={task.status} />
          </div>
          <div className="task-sub">
            <span>策略 {task.policy?.name}</span>
            <span>{task.files.length} 文件</span>
            <span>创建 {formatTime(task.created_at)}</span>
            {task.operator && <span>操作人 {task.operator}</span>}
            <span className="source-path" title={task.source_path}>
              {task.source_path}
            </span>
          </div>
        </div>
        <div className="task-actions">
          <div className="policy-select-inline">
            <select
              aria-label="本次检测策略"
              value={policyId}
              onChange={(e) => setPolicyId(e.target.value)}
              disabled={locked}
            >
              {policies.map((p) => (
                <option key={p.id} value={p.id} disabled={p.mode === "encrypt"}>
                  {p.name}{p.mode === "encrypt" ? "（已停用，请切换策略）" : ""}
                </option>
              ))}
            </select>
            <button className="btn" onClick={doDetect} disabled={locked}>
              {busy === "detect" ? (
                <>
                  <span className="spinner" />
                  检测中…
                </>
              ) : (
                <>
                  <ScanIcon size={15} />
                  {task.status === "created" ? "开始检测" : "重新检测"}
                </>
              )}
            </button>
          </div>
          <button
            className={`btn ${!maskedDone ? "btn-primary" : ""}`}
            onClick={doMask}
            disabled={!!maskBlockedReason}
            title={maskBlockedReason || "按当前审核结果处理"}
            aria-describedby="task-next-hint"
          >
            {busy === "mask" ? (
              <>
                <span className="spinner" />
                脱敏中…
              </>
            ) : (
              <>
                <ShieldIcon size={15} />
                执行脱敏
              </>
            )}
          </button>
          <button
            className={`btn ${maskedDone ? "btn-primary" : ""}`}
            onClick={doExport}
            disabled={locked || !maskedDone || policyChanged}
            title={
              !maskedDone
                ? "完成脱敏后可导出"
                : policyChanged
                  ? "请先应用新策略，或切回当前任务策略"
                  : "下载脱敏文件与报告"
            }
          >
            {busy === "export" ? (
              <>
                <span className="spinner" />
                打包中…
              </>
            ) : (
              <>
                <DownloadIcon size={15} />
                导出 ZIP
              </>
            )}
          </button>
        </div>
      </div>

      <section
        className={`task-next ${policyChanged || detectionBlocked ? "attention" : ""}`}
        aria-label="当前操作指引"
      >
        <div>
          <b>{nextTitle}</b>
          <p id="task-next-hint">{nextHint}</p>
        </div>
        {!running && summary.pending > 0 && !policyChanged && (
          <button
            className="btn btn-sm"
            onClick={showPending}
            disabled={locked}
          >
            {busy === "locate" ? (
              <>
                <span className="spinner" />
                正在定位…
              </>
            ) : (
              <>
                查看待审核{" "}
                <span className="badge amber">{summary.pending}</span>
              </>
            )}
          </button>
        )}
      </section>

      <div className="flow">
        {FLOW.map((label, i) => (
          <div key={label} style={{ display: "contents" }}>
            <div
              className={`flow-step ${done[i] ? "done" : ""} ${current === i ? "current" : ""}`}
            >
              <span className="flow-num">
                {done[i] ? <CheckIcon size={12} /> : i + 1}
              </span>
              {label}
            </div>
            {i < FLOW.length - 1 && (
              <div className={`flow-line ${done[i] ? "done" : ""}`} />
            )}
          </div>
        ))}
      </div>

      <div className="tiles">
        <div className="tile">
          <span>命中总数</span>
          <b>{summary.total}</b>
          <span className="tile-sub">待确认 {summary.pending}</span>
        </div>
        <div className="tile red">
          <span>高风险</span>
          <b>{summary.high}</b>
          <span className="tile-sub">
            中 {summary.medium} · 低 {summary.low}
          </span>
        </div>
        <div className="tile">
          <span>已检查文件</span>
          <b>{task.stats.scanned ?? 0}</b>
          <span className="tile-sub">共 {task.files.length} 个</span>
        </div>
        <div className="tile lime">
          <span>已应用</span>
          <b>{appliedCount}</b>
          <span className="tile-sub">
            {task.stats.mask ? `${changedFiles} 个文件` : "尚未脱敏"}
          </span>
        </div>
      </div>

      {error && (
        <div className="notice error" role="alert" style={{ marginBottom: 14 }}>
          <AlertIcon />
          {error}
          <button className="btn btn-sm btn-ghost" onClick={() => setError("")}>
            关闭
          </button>
        </div>
      )}
      {msg &&
        !(task.job?.status === "succeeded" && msg.includes("任务已提交")) && (
          <div
            className="notice success"
            role="status"
            style={{ marginBottom: 14 }}
          >
            <CheckIcon />
            {msg}
          </div>
        )}
      {maskedDone && reversible && (
        <div className="banner">
          <ShieldIcon size={17} />
          <span>
            文本映射与还原
            {reversible &&
              " 文本映射表按需生成；媒体遮挡和静音不支持逆向恢复。"}
          </span>
          {reversible && (
            <label className="switch" style={{ marginLeft: "auto" }}>
              <input
                type="checkbox"
                checked={includeMapping}
                onChange={(e) => setIncludeMapping(e.target.checked)}
              />
              导出包包含映射表
            </label>
          )}
          {reversible && (
            <button
              className="btn btn-sm"
              onClick={doMapping}
              disabled={locked}
            >
              <KeyIcon size={13} />
              下载映射表
            </button>
          )}
          {reversible && (
            <button
              className="btn btn-sm"
              onClick={doRestore}
              disabled={locked}
            >
              <RestoreIcon size={13} />
              还原并下载
            </button>
          )}
        </div>
      )}

      {!!task.stats.warnings?.length && (
        <div className="notice" style={{ marginBottom: 14 }}>
          <AlertIcon size={15} />
          <span>检测提示：{task.stats.warnings.join("；")}</span>
        </div>
      )}

      {task.job &&
        (task.job.status !== "succeeded" ||
          task.job.operation === "audition") && (
          <div
            className={`notice task-job ${task.job.status === "failed" ? "error" : ""}`}
            role="status"
            style={{ marginBottom: 14 }}
          >
            <div style={{ flex: 1 }}>
              <b>
                {task.job.operation === "audition"
                  ? "音轨试听 · "
                  : task.job.operation === "verify"
                    ? "残留复查 · "
                    : ""}
                {{
                  queued: "排队中",
                  running: "处理中",
                  succeeded:
                    task.job.operation === "audition"
                      ? "试听已就绪"
                      : "处理完成",
                  failed: "处理失败",
                  cancelled: "已取消",
                  interrupted: "上次任务已中断",
                }[task.job.status] || task.job.status}
              </b>{" "}
              · {task.job.stage}
              {task.job.status !== "succeeded" && task.job.total > 0 && (
                <>
                  <progress
                    aria-label={task.job.stage}
                    value={Math.min(task.job.completed, task.job.total)}
                    max={task.job.total}
                  />{" "}
                  {Number(task.job.completed.toFixed(1))}/
                  {Number(task.job.total.toFixed(1))}
                </>
              )}
              {task.job.error && <p>{task.job.error}</p>}
              {["failed", "interrupted", "cancelled"].includes(
                task.job.status,
              ) && (
                <p>
                  {task.job.operation === "audition"
                    ? "请检查提示后再次点击试听；已有审核与脱敏结果不受影响。"
                    : task.job.operation === "verify"
                      ? "请检查覆盖提示后重新复查；已有脱敏产物仍保留，但不能视为复查通过。"
                      : "请检查提示后点击重新检测或执行脱敏重试。"}
                </p>
              )}
            </div>
            {running && (
              <button
                className="btn btn-sm"
                disabled={busy === "cancel"}
                onClick={() =>
                  act("cancel", async () => {
                    await cancelTask(id);
                    if (task.job?.operation === "audition") {
                      setAuditionTrack(undefined);
                      setAuditionWatch(undefined);
                    }
                  })
                }
              >
                取消任务
              </button>
            )}
          </div>
        )}
      {maskedDone && (
        <section className="quality-panel" aria-label="脱敏产物残留复查">
          <div className="quality-head">
            <div>
              <b>脱敏后残留复查</b>
              <p className="quality-note">
                复用当前检测器检查已生成的产物；未检出不代表没有敏感内容。
              </p>
            </div>
            <button
              type="button"
              className="btn btn-sm"
              disabled={locked || policyChanged}
              onClick={doVerify}
            >
              {busy === "verify"
                ? "正在提交…"
                : verification
                  ? "重新复查"
                  : "开始残留复查"}
            </button>
          </div>
          <div className="quality-summary" role="status">
            <span>{verificationLabel}</span>
            {verification && (
              <span>
                候选残留 {verification.candidate_count ?? 0} 处
                {verification.checked_at
                  ? ` · ${formatTime(verification.checked_at)}`
                  : ""}
              </span>
            )}
          </div>
          {!!verification?.files?.length && (
            <details className="quality-files">
              <summary>逐文件覆盖范围与候选残留</summary>
              <ul>
                {verification.files.map((file) => (
                  <li key={file.file_id}>
                    <b>{file.path}</b>
                    <span>
                      {(
                        {
                          complete: "已检查",
                          partial: "检查不完整",
                          failed: "检查失败",
                          skipped: "已跳过",
                          not_checked: "未检查",
                        } as Record<string, string>
                      )[file.status] || file.status}{" "}
                      · 候选 {file.candidate_count ?? 0} 处
                    </span>
                    {file.coverage?.frames_scanned != null && (
                      <p>
                        已检查 {file.coverage.frames_scanned} 帧
                        {file.coverage.frame_check === "all" ? "（逐帧）" : ""}
                      </p>
                    )}
                    {!!file.warnings?.length && (
                      <p>{file.warnings.join("；")}</p>
                    )}
                    {!!file.candidates?.length && (
                      <p>
                        {file.candidates
                          .slice(0, 5)
                          .map(
                            (c) =>
                              `${c.type_label || c.rule_name || "敏感信息"}${c.start != null ? ` ${c.start.toFixed(2)}s` : c.frame_index != null ? ` 第${c.frame_index}帧` : c.line_start != null ? ` 第${c.line_start}行` : ""}${c.track_index != null ? ` 音轨${c.track_index}` : ""}`,
                          )
                          .join("；")}
                        {file.candidates.length > 5
                          ? "；更多位置见复查记录"
                          : ""}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!verification?.limitations?.length && (
            <details className="quality-files">
              <summary>复查范围与限制</summary>
              <ul>
                {verification.limitations.map((limitation, index) => (
                  <li key={index}>{limitation}</li>
                ))}
              </ul>
            </details>
          )}
          {!verification && task.verification && (
            <p className="quality-note">
              审核或处理版本已变化，旧复查结果不适用于当前产物。
            </p>
          )}
        </section>
      )}

      <div className="workspace">
        <aside className="rail">
          <div className="rail-head">
            <span>文件</span>
            <span>{task.files.length}</span>
          </div>
          <div className="file-search">
            <input
              type="search"
              aria-label="搜索任务文件"
              placeholder="搜索文件…"
              value={fileSearch}
              onChange={(e) => setFileSearch(e.target.value)}
            />
          </div>
          <div className="rail-list">
            {visibleFiles.length === 0 && (
              <Empty title="没有匹配文件" hint="试试其他文件名或清空搜索" />
            )}
            {visibleFiles.map((f) => (
              <button
                key={f.id}
                className={`rail-item ${fileId === f.id ? "active" : ""}`}
                onClick={() => {
                  setFileId(f.id);
                  setActive("");
                  setTab("original");
                  setSeek(undefined);
                  setAuditionTrack(undefined);
                  setAuditionWatch(undefined);
                  setResultSearch("");
                }}
                aria-current={fileId === f.id ? "true" : undefined}
                title={f.path}
              >
                <div className="rail-file">
                  <FileIcon size={13} />
                  <span>{f.path}</span>
                </div>
                <div className="rail-meta">
                  <span>
                    {kindLabel(f)} ·{" "}
                    {f.detection
                      ? {
                          complete: "已检查",
                          partial: "检查不完整",
                          failed: "检测失败",
                          skipped: "已跳过",
                          not_checked: "未检测",
                        }[f.detection.state]
                      : "未检测"}
                  </span>
                  {f.findings > 0 && (
                    <span className="hit">{f.findings} 命中</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="center">
          <div className="panel-top">
            <span className="panel-dot" />
            <span
              className="panel-title mono"
              style={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {selectedFile?.path || "选择文件"}
            </span>
            {selectedFile && (
              <span className="badge" style={{ marginLeft: 4 }}>
                {formatBytes(selectedFile.size)}
              </span>
            )}
            <div className="tabs">
              <button
                className={`tab ${tab === "original" ? "active" : ""}`}
                onClick={() => setTab("original")}
              >
                {currentPreview?.media === "image"
                  ? "原始图像"
                  : currentPreview?.media === "audio"
                    ? "原始音频"
                    : currentPreview?.media === "video"
                      ? "原始视频"
                      : "原始文本"}
              </button>
              <button
                className={`tab ${tab === "masked" ? "active" : ""}`}
                onClick={() => setTab("masked")}
              >
                脱敏预览
              </button>
            </div>
          </div>
          {isTimedMedia && (
            <ReviewTimeline
              groups={mediaGroups}
              duration={selectedFile?.detection?.metadata?.duration ?? 0}
              disabled={locked}
              activeId={active}
              onPick={selectedGroup}
              onSeek={(time) => {
                setTab("original");
                setAuditionTrack(undefined);
                setSeek({ token: Date.now(), start: time });
              }}
            />
          )}
          {selectedFile?.detection?.warnings?.length ? (
            <div className="notice error">
              {selectedFile.detection.warnings.join("；")}
            </div>
          ) : null}
          {!selectedFile ? (
            <Empty title="选择一个文件" hint="左侧文件列表中选择要查看的文件" />
          ) : previewError ? (
            <div className="preview-error">
              <Empty
                icon={<AlertIcon size={26} />}
                title="文件预览加载失败"
                hint={previewError}
              />
              <button
                className="btn btn-sm"
                onClick={() => setRefresh((n) => n + 1)}
              >
                重试预览
              </button>
            </div>
          ) : previewLoading ? (
            <Empty
              title="正在加载文件…"
              hint="正在读取当前文件的预览与检测结果"
            />
          ) : currentPreview?.media === "image" ? (
            <div className="compare">
              <div
                className={`pane tabbed ${tab === "original" ? "active" : ""}`}
              >
                <div className="pane-head">
                  <span>原始内容</span>
                  <span className="mode">{findings.length} 处检测结果</span>
                </div>
                <ImagePane
                  src={currentPreview.image_url!}
                  findings={findings}
                  activeId={active}
                  onPick={pick}
                  showBoxes
                />
              </div>
              <div
                className={`pane tabbed ${tab === "masked" ? "active" : ""}`}
              >
                <div className="pane-head">
                  <span>脱敏预览</span>
                  <span className="mode">{modeText(task.policy?.mode)}</span>
                </div>
                {maskedDone && currentPreview.masked_ready !== false ? (
                  <ImagePane
                    src={currentPreview.masked_url!}
                    findings={findings}
                    activeId={active}
                    onPick={pick}
                  />
                ) : (
                  <Empty
                    title="尚未生成脱敏结果"
                    hint="完成审核后点击执行脱敏"
                  />
                )}
              </div>
            </div>
          ) : currentPreview?.media === "audio" ||
            currentPreview?.media === "video" ? (
            <div className="compare">
              <div
                className={`pane tabbed ${tab === "original" ? "active" : ""}`}
              >
                <div className="pane-head">
                  <span>
                    {auditionTrack != null
                      ? `音轨 ${auditionTrack} 试听`
                      : "原始媒体"}
                  </span>
                  <span>{findings.length} 处</span>
                </div>
                <MediaPlayer
                  key={`${fileId}-original-${auditionTrack ?? "main"}-${auditionTrack != null && currentPreview.media === "video" ? seek?.token : task.job?.operation === "audition" ? "audition" : (task.job?.status ?? "idle")}`}
                  video={
                    currentPreview.media === "video" && auditionTrack == null
                  }
                  src={
                    auditionTrack != null && currentPreview.media === "video"
                      ? `/api/tasks/${id}/files/${fileId}/audio?track_index=${auditionTrack}`
                      : (currentPreview.audio_url || currentPreview.video_url)!
                  }
                  seek={seek}
                  findings={findings}
                  activeId={active}
                  onPick={pick}
                  showBoxes
                />
                {auditionTrack != null && currentPreview.media === "video" && (
                  <button
                    className="btn btn-sm"
                    onClick={() => setAuditionTrack(undefined)}
                  >
                    返回视频画面
                  </button>
                )}
              </div>
              <div
                className={`pane tabbed ${tab === "masked" ? "active" : ""}`}
              >
                <div className="pane-head">
                  <span>脱敏结果</span>
                  <span>
                    {currentPreview.media === "audio"
                      ? "片段静音"
                      : "区域遮挡与音轨处理"}
                    {task.policy?.voice_change && " · 变声效果"}
                  </span>
                </div>
                {maskedDone &&
                currentPreview.masked_ready !== false &&
                currentPreview.masked_url ? (
                  <MediaPlayer
                    key={`${fileId}-${currentPreview.masked_url}`}
                    video={currentPreview.media === "video"}
                    src={currentPreview.masked_url}
                  />
                ) : (
                  <Empty
                    title="尚未生成脱敏结果"
                    hint="完成审核后点击执行脱敏，处理成功后将在此播放"
                  />
                )}
              </div>
            </div>
          ) : currentPreview?.binary ? (
            <Empty
              icon={<FileIcon size={26} />}
              title="二进制文件"
              hint="该文件类型不提供文本预览，检测结果见右侧列表"
            />
          ) : currentPreview ? (
            <div className="compare">
              <div
                className={`pane tabbed ${tab === "original" ? "active" : ""}`}
              >
                <div className="pane-head">
                  <span>原始内容</span>
                  <span className="mode">{findings.length} 处检测结果</span>
                </div>
                <div className="code-scroll">
                  <pre className="code">
                    {renderHighlighted(
                      currentPreview.original,
                      findings,
                      active,
                      pick,
                    )}
                  </pre>
                </div>
              </div>
              <div
                className={`pane tabbed ${tab === "masked" ? "active" : ""}`}
              >
                <div className="pane-head">
                  <span>脱敏预览</span>
                  <span className="mode">
                    {modeText(task.policy?.mode)} · {currentPreview.applied} 处
                  </span>
                </div>
                <div className="code-scroll">
                  <pre className="code masked">{currentPreview.masked}</pre>
                </div>
              </div>
            </div>
          ) : (
            <Empty title="正在加载文件…" />
          )}
        </section>

        <aside
          className="findings-panel"
          id="review-results"
          aria-label="当前文件检测结果"
        >
          <div className="findings-head">
            <span className="panel-title">检测结果</span>
            <span className="badge">
              {shown.length}/{findings.length}
            </span>
          </div>
          <div className="review-tools">
            <input
              type="search"
              aria-label="搜索检测结果"
              placeholder="搜索类型、规则或内容…"
              value={resultSearch}
              onChange={(e) => {
                setResultSearch(e.target.value);
                setVisibleCount(20);
              }}
            />
            <select
              aria-label="检测结果审核状态"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value as typeof filter);
                setVisibleCount(20);
              }}
            >
              <option value="all">全部状态</option>
              <option value="pending">待确认</option>
              <option value="accepted">已接受</option>
              <option value="rejected">已忽略</option>
            </select>
          </div>
          {isTimedMedia && (
            <div
              className="review-view-toggle"
              role="group"
              aria-label="媒体结果展示方式"
            >
              <button
                type="button"
                aria-pressed={groupedReview}
                className={groupedReview ? "active" : ""}
                onClick={() => {
                  setGroupedReview(true);
                  setVisibleCount(20);
                }}
              >
                连续片段 {mediaGroups.length} 组
              </button>
              <button
                type="button"
                aria-pressed={!groupedReview}
                className={!groupedReview ? "active" : ""}
                onClick={() => {
                  setGroupedReview(false);
                  setVisibleCount(20);
                }}
              >
                原始结果 {findings.length} 条
              </button>
            </div>
          )}
          <details className="review-bulk">
            <summary>
              批量审核 <span>全任务 · {summary.pending} 条待确认</span>
            </summary>
            <div>
              <p>将处理整个任务的待确认项，不限于当前文件或搜索结果。</p>
              <div className="btn-row">
                <button
                  className="btn btn-sm"
                  onClick={() => doBulk(true)}
                  disabled={locked || summary.pending === 0}
                >
                  全部接受
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() => doBulk(false)}
                  disabled={locked || summary.pending === 0}
                >
                  全部忽略
                </button>
              </div>
            </div>
          </details>
          {selectedFile &&
            currentPreview?.media &&
            ["audio", "video", "image"].includes(currentPreview.media) && (
              <details className="review-add">
                <summary>
                  手动补充处理范围
                  <span>
                    {selectedFile.detection?.state === "complete"
                      ? "添加漏检内容"
                      : "检测完成后可补充"}
                  </span>
                </summary>
                <MediaEditor
                  key={`${fileId}-manual`}
                  file={selectedFile}
                  media={currentPreview.media as "audio" | "video" | "image"}
                  disabled={
                    locked || selectedFile.detection?.state !== "complete"
                  }
                  onSave={saveLocation}
                  onAudition={audition}
                />
              </details>
            )}
          {selectedFile?.detection?.transcript?.length ? (
            <details className="transcript">
              <summary>
                语音转写{" "}
                <span>
                  辅助审核 · {selectedFile.detection.transcript.length} 段
                </span>
              </summary>
              {selectedFile.detection.transcript.map((segment, i) => (
                <button
                  key={i}
                  className="transcript-line"
                  disabled={locked}
                  onClick={() =>
                    audition(
                      segment.start,
                      segment.end,
                      segment.track_index ?? 0,
                    )
                  }
                >
                  <span>
                    {segment.start.toFixed(2)}–{segment.end.toFixed(2)}s · 音轨{" "}
                    {segment.track_index ?? 0}
                  </span>
                  {segment.text}
                </button>
              ))}
            </details>
          ) : null}
          <div className="findings-list">
            {previewLoading ? (
              <Empty title="正在载入检测结果…" />
            ) : previewError ? (
              <Empty title="结果暂不可用" hint="请在预览区重试加载" />
            ) : shown.length === 0 ? (
              <Empty
                icon={<ShieldIcon size={26} />}
                title={
                  findings.length
                    ? "当前筛选下没有结果"
                    : selectedFile?.detection?.state === "complete"
                      ? "本次检测无命中"
                      : "尚未完成检测"
                }
                hint={
                  findings.length
                    ? "切换筛选或搜索条件；其他文件的待审核项请从文件列表查看"
                    : selectedFile?.detection?.state === "complete"
                      ? "无命中不代表没有敏感内容，可人工补充"
                      : "检查提示后重新检测；未完成检测不能导出"
                }
              />
            ) : isTimedMedia && groupedReview ? (
              shownGroups.slice(0, visibleCount).map((group) => (
                <article
                  className={`finding review-group ${group.members.some((f) => f.id === active) ? "active" : ""}`}
                  key={group.id}
                  id={`result-${group.id}`}
                >
                  <div className="finding-head">
                    <button
                      type="button"
                      className="finding-pick"
                      onClick={() => selectedGroup(group)}
                    >
                      {group.label}
                    </button>
                    <span className={`badge group-status ${group.status}`}>
                      {group.status === "mixed"
                        ? "混合状态"
                        : group.status === "accepted"
                          ? "已接受"
                          : group.status === "rejected"
                            ? "已忽略"
                            : "待确认"}
                    </span>
                  </div>
                  <div className="group-meta">
                    {group.start != null
                      ? `${group.start.toFixed(2)}–${group.end?.toFixed(2)} 秒`
                      : "无时间定位"}{" "}
                    · {group.track} · {group.members.length} 条原始结果
                  </div>
                  <p className="quality-note">
                    整组操作影响全部成员，当前筛选不会缩小操作范围。帧定位与处理范围保持原样。
                  </p>
                  <div className="group-actions btn-row">
                    {(
                      [
                        ["accepted", "接受整组"],
                        ["rejected", "忽略整组"],
                        ["pending", "整组待定"],
                      ] as const
                    ).map(([status, label]) => (
                      <button
                        className="btn btn-sm"
                        type="button"
                        key={status}
                        disabled={locked || group.status === status}
                        onClick={() => setGroupConfirmation({ group, status })}
                      >
                        {label}（{group.members.length}）
                      </button>
                    ))}
                  </div>
                  <details className="group-members">
                    <summary>查看原始成员与单项审核</summary>
                    <div>
                      {group.members.slice(0, 20).map((member) => (
                        <div key={member.id}>
                          <button
                            type="button"
                            className="btn btn-sm btn-ghost"
                            onClick={() => {
                              setGroupedReview(false);
                              pick(member.id, true);
                            }}
                          >
                            {findingPos(member)} ·{" "}
                            {member.status === "accepted"
                              ? "已接受"
                              : member.status === "rejected"
                                ? "已忽略"
                                : "待确认"}{" "}
                            · 定位并单项审核
                          </button>
                        </div>
                      ))}
                      {group.members.length > 20 && (
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={() => {
                            setGroupedReview(false);
                            pick(group.members[20].id, true);
                          }}
                        >
                          其余 {group.members.length - 20}{" "}
                          条：切换原始列表继续审核
                        </button>
                      )}
                    </div>
                  </details>
                </article>
              ))
            ) : (
              shown.slice(0, visibleCount).map((f) => (
                <div
                  key={f.id}
                  id={`result-${f.id}`}
                  className={`finding ${active === f.id ? "active" : ""} ${f.status === "rejected" ? "rejected" : ""}`}
                >
                  <div className="finding-head">
                    <button
                      type="button"
                      className="finding-pick"
                      onClick={() => pick(f.id)}
                      aria-label={`定位${f.type_label}，${findingPos(f)}`}
                      aria-pressed={active === f.id}
                    >
                      {f.type_label}
                    </button>
                    <RiskPill risk={f.risk} />
                    <FindingStatusPill status={f.status} />
                    <span className="finding-rule">{f.rule_name}</span>
                  </div>
                  {f.value ? (
                    <div className="finding-value" title={f.value}>
                      {f.value}
                    </div>
                  ) : (
                    <div className="finding-value empty">
                      无文本内容（仅区域处理）
                    </div>
                  )}
                  <div className="finding-meta">
                    <span>{findingPos(f)}</span>
                    <span>置信度 {Math.round(f.confidence * 100)}%</span>
                    <span>{f.series}</span>
                    {f.mode && <span>方式 {f.mode}</span>}
                  </div>
                  <div
                    className="finding-actions"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="status-toggle">
                      <button
                        className={f.status === "accepted" ? "on-accepted" : ""}
                        aria-pressed={f.status === "accepted"}
                        onClick={() => doReviewOne(f, "accepted")}
                        disabled={locked || f.status === "accepted"}
                      >
                        接受
                      </button>
                      <button
                        className={f.status === "rejected" ? "on-rejected" : ""}
                        aria-pressed={f.status === "rejected"}
                        onClick={() => doReviewOne(f, "rejected")}
                        disabled={locked || f.status === "rejected"}
                      >
                        忽略
                      </button>
                      <button
                        className={f.status === "pending" ? "on-pending" : ""}
                        aria-pressed={f.status === "pending"}
                        title="撤销本条审核，重新标为待确认"
                        onClick={() => doReviewOne(f, "pending")}
                        disabled={locked || f.status === "pending"}
                      >
                        待定
                      </button>
                    </div>
                    {!f.media && (
                      <select
                        aria-label={`${f.type_label}处理方式`}
                        value={f.mode || "default"}
                        onChange={(e) => doMode(f, e.target.value)}
                        disabled={locked}
                      >
                        <option value="default">跟随策略</option>
                        <option value="mask">打码</option>
                        <option value="delete">删除</option>
                        <option value="replace">代号替换</option>
                        {f.mode === "encrypt" && (
                          <option value="encrypt" disabled>
                            旧实验加密（已停用）
                          </option>
                        )}
                        <option value="offset">数字偏移</option>
                      </select>
                    )}
                    {f.media && (
                      <span>
                        {f.bbox ? "区域遮挡" : "片段静音"}
                        {f.timing_precision === "segment" ? " · 整段定位" : ""}
                        {f.track_index != null
                          ? ` · 音轨 ${f.track_index}`
                          : ""}
                      </span>
                    )}
                  </div>
                  {f.media && selectedFile && active === f.id && (
                    <MediaEditor
                      key={f.id + refresh}
                      file={selectedFile}
                      media={f.media as "audio" | "video" | "image"}
                      finding={f}
                      disabled={locked}
                      onSave={saveLocation}
                      onAudition={(start, end, track) =>
                        audition(start, end, track ?? f.track_index ?? 0)
                      }
                    />
                  )}
                </div>
              ))
            )}
            {(isTimedMedia && groupedReview
              ? shownGroups.length
              : shown.length) > visibleCount && (
              <div className="review-more">
                <p>
                  已显示{" "}
                  {Math.min(
                    visibleCount,
                    isTimedMedia && groupedReview
                      ? shownGroups.length
                      : shown.length,
                  )}{" "}
                  /{" "}
                  {isTimedMedia && groupedReview
                    ? shownGroups.length
                    : shown.length}{" "}
                  {isTimedMedia && groupedReview ? "组" : "条"}
                </p>
                <button
                  className="btn btn-sm"
                  onClick={() => setVisibleCount((n) => n + 20)}
                >
                  再显示 20 {isTimedMedia && groupedReview ? "组" : "条"}
                </button>
              </div>
            )}
            {shown.length > 0 &&
              (isTimedMedia && groupedReview
                ? shownGroups.length
                : shown.length) <= visibleCount && (
                <div className="review-list-end">
                  当前筛选共 {shown.length} 条 · 已全部显示
                </div>
              )}
          </div>
        </aside>
      </div>
      <dialog
        ref={groupDialog}
        className="confirmation-dialog"
        aria-labelledby="group-review-title"
        onCancel={(event) => {
          event.preventDefault();
          setGroupConfirmation(null);
        }}
      >
        {groupConfirmation && (
          <>
            <h2 id="group-review-title">确认整组审核操作</h2>
            <p>
              将把「{groupConfirmation.group.label}」组的全部{" "}
              {groupConfirmation.group.members.length} 条原始结果标为
              {groupConfirmation.status === "accepted"
                ? "接受"
                : groupConfirmation.status === "rejected"
                  ? "忽略"
                  : "待确认"}
              。包含当前筛选外的成员；混合状态也会被覆盖。每帧定位不会改变。
            </p>
            <div className="btn-row">
              <button
                type="button"
                className="btn"
                onClick={() => setGroupConfirmation(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                  const value = groupConfirmation;
                  setGroupConfirmation(null);
                  void reviewGroup(value.group, value.status);
                }}
              >
                确认更新整组
              </button>
            </div>
          </>
        )}
      </dialog>
      <dialog
        ref={exportDialog}
        className="confirmation-dialog export-dialog"
        aria-labelledby="export-preflight-title"
        onCancel={(event) => {
          event.preventDefault();
          if (busy !== "export") setExportOpen(false);
        }}
      >
        <h2 id="export-preflight-title">导出前复核</h2>
        <p>
          当前任务包含 {task.files.length}{" "}
          个文件；导出范围与未处理内容以以下提示和报告为准。源文件保持只读，检测与复查均不能保证零漏检。
        </p>
        <div className="export-options">
          <label className="switch">
            <input
              type="checkbox"
              checked={anonymizeMetadata}
              disabled={busy === "export"}
              onChange={(event) => setAnonymizeMetadata(event.target.checked)}
            />
            使用通用文件名并隐藏任务备注
          </label>
          {reversible && (
            <label className="switch">
              <input
                type="checkbox"
                checked={includeMapping}
                disabled={busy === "export"}
                onChange={(event) => setIncludeMapping(event.target.checked)}
              />
              附带文本映射表（含原始敏感值）
            </label>
          )}
        </div>
        {preflightLoading ? (
          <div className="quality-note" role="status">
            正在核对当前产物与元信息…
          </div>
        ) : (
          preflight && (
            <>
              <p className="quality-note">
                产物版本 {preflight.revision} · 复查状态{" "}
                {(
                  {
                    not_run: "尚未复查",
                    complete: "已完成复查（不保证零漏检）",
                    partial: "复查不完整",
                    failed: "复查失败",
                    cancelled: "复查取消",
                    interrupted: "复查中断",
                    stale: "旧结果已失效",
                  } as Record<string, string>
                )[preflight.verification_status] ||
                  preflight.verification_status}
              </p>
              <ul className="export-issues">
                {preflight.issues.map((issue, index) => (
                  <li key={`${issue.code}-${index}`} className={issue.severity}>
                    {issue.message}
                  </li>
                ))}
              </ul>
              {!!preflight.metadata_candidates.length && (
                <p className="quality-note">
                  元信息候选：
                  {preflight.metadata_candidates
                    .map(
                      (c) =>
                        `${c.field}（${c.type_label}，${c.omitted_from_export ? "导出时隐藏" : "将保留"}）`,
                    )
                    .join("；")}
                  。此处不显示敏感值。
                </p>
              )}
              {!!preflight.limitations?.length && (
                <p className="quality-note">
                  {preflight.limitations.join(" ")}
                </p>
              )}
              {(preflight.requires_acknowledgement || includeMapping) && (
                <label className="switch export-ack">
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(event) => setAcknowledged(event.target.checked)}
                    disabled={busy === "export"}
                  />
                  我已复核以上问题，了解可能存在残留或明文映射，确认继续导出。
                </label>
              )}
            </>
          )
        )}
        {includeMapping && (
          <div className="notice error">
            映射表包含原始敏感值，不会因隐藏元信息而变成脱敏文件，请单独保管。
          </div>
        )}
        {preflightError && (
          <div className="notice error" role="alert">
            {preflightError}
          </div>
        )}
        <div className="export-actions btn-row">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy === "export"}
            onClick={() => setExportOpen(false)}
          >
            取消
          </button>
          {preflightError && (
            <button
              type="button"
              className="btn"
              onClick={() => setPreflightRefresh((n) => n + 1)}
            >
              重新核对
            </button>
          )}
          <button
            type="button"
            className="btn"
            disabled={locked || policyChanged}
            onClick={() => {
              setExportOpen(false);
              void doVerify();
            }}
          >
            先做残留复查
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={
              !preflight ||
              preflightLoading ||
              locked ||
              ((preflight.requires_acknowledgement || includeMapping) &&
                !acknowledged)
            }
            onClick={() => void confirmExport()}
          >
            {busy === "export" ? "正在导出…" : "确认导出 ZIP"}
          </button>
        </div>
      </dialog>
    </>
  );
}

function renderHighlighted(
  text: string,
  findings: Finding[],
  activeId: string,
  onPick: (id: string) => void,
): ReactNode[] {
  const sorted = findings
    .filter(
      (f) =>
        f.index_start >= 0 &&
        f.index_end <= text.length &&
        f.index_end > f.index_start,
    )
    .sort((a, b) => a.index_start - b.index_start);
  const out: ReactNode[] = [];
  let pos = 0;
  let n = 0;
  for (const f of sorted) {
    if (f.index_start < pos) continue;
    if (f.index_start > pos)
      out.push(<span key={`t${n++}`}>{text.slice(pos, f.index_start)}</span>);
    out.push(
      <mark
        key={f.id}
        data-fid={f.id}
        className={`hl ${f.risk} ${f.status === "rejected" ? "rejected" : ""} ${f.id === activeId ? "active" : ""}`}
        role="button"
        tabIndex={0}
        aria-label={`定位${f.type_label}`}
        onClick={(e) => {
          e.stopPropagation();
          onPick(f.id);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onPick(f.id);
          }
        }}
      >
        {text.slice(f.index_start, f.index_end)}
      </mark>,
    );
    pos = f.index_end;
  }
  if (pos < text.length)
    out.push(<span key={`t${n++}`}>{text.slice(pos)}</span>);
  return out;
}

function ImagePane({
  src,
  findings,
  activeId,
  onPick,
  showBoxes = false,
}: {
  src: string;
  findings: Finding[];
  activeId: string;
  onPick: (id: string) => void;
  showBoxes?: boolean;
}) {
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setDims(null);
    setFailed(false);
  }, [src]);
  const boxes = showBoxes
    ? findings.filter((f) => f.bbox && f.bbox.length === 4)
    : [];
  return (
    <div className="img-pane">
      {failed && (
        <div className="notice error" role="alert">
          图像加载失败，请检查处理状态并重试。
        </div>
      )}
      <div className="img-box">
        <img
          src={src}
          alt="预览"
          onError={() => setFailed(true)}
          onLoad={(e) => {
            const el = e.currentTarget;
            if (el.naturalWidth)
              setDims({ w: el.naturalWidth, h: el.naturalHeight });
          }}
        />
        {dims &&
          boxes.map((f) => {
            const [x, y, w, h] = f.bbox!;
            return (
              <button
                type="button"
                key={f.id}
                aria-label={`定位${f.type_label}`}
                data-fid={f.id}
                onClick={(e) => {
                  e.stopPropagation();
                  onPick(f.id);
                }}
                className={`bbox ${f.id === activeId ? "active" : ""} ${f.status === "rejected" ? "rejected" : ""}`}
                style={{
                  left: `${(x / dims.w) * 100}%`,
                  top: `${(y / dims.h) * 100}%`,
                  width: `${(w / dims.w) * 100}%`,
                  height: `${(h / dims.h) * 100}%`,
                }}
                title={`${f.type_label} · ${Math.round(f.confidence * 100)}%`}
              />
            );
          })}
      </div>
    </div>
  );
}
