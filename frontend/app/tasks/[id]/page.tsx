"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  detectTask, download, exportUrl, getPolicies, getPreview, getTask, mappingUrl, maskTask,
  restoreDownloadUrl, restoreTask, review,
} from "@/lib/api";
import type { Finding, Policy, Preview, Summary, Task } from "@/lib/types";
import {
  Empty, FindingStatusPill, formatBytes, formatTime, RiskPill, StatusPill,
} from "@/components/ui";
import {
  AlertIcon, CheckIcon, ChevronIcon, DownloadIcon, FileIcon, KeyIcon,
  RestoreIcon, ScanIcon, ShieldIcon,
} from "@/components/icons";

type Busy = "" | "detect" | "review" | "mask" | "export" | "restore" | "mapping";

const FLOW = ["路径", "检测", "审核", "脱敏", "导出"];

function modeText(m?: string) {
  return m === "mask" ? "打码" : m === "delete" ? "删除" : m === "replace" ? "代号替换"
    : m === "encrypt" ? "加密" : m === "offset" ? "数字偏移" : "打码";
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
  const reviewStage = status !== "created";
  const done = [
    true,
    reviewStage,
    reviewStage && summary ? (summary.pending === 0 || summary.total === 0 || masked) : false,
    masked,
    status === "exported",
  ];
  const current = done.findIndex((x) => !x);
  return { done, current };
}

export default function TaskPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [task, setTask] = useState<Task | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [policyId, setPolicyId] = useState("");
  const [fileId, setFileId] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [active, setActive] = useState("");
  const [tab, setTab] = useState<"original" | "masked">("original");
  const [busy, setBusy] = useState<Busy>("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [filter, setFilter] = useState<"all" | "pending" | "accepted" | "rejected">("all");
  const [refresh, setRefresh] = useState(0);
  const [includeMapping, setIncludeMapping] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const data = await getTask(id);
    setTask(data.task);
    setSummary(data.summary);
    setPolicyId((prev) => prev || data.task.policy_id);
    setFileId((prev) => prev || (
      data.task.files.find((f) => f.findings > 0)
      || data.task.files.find((f) => f.is_text)
      || data.task.files[0]
    )?.id || "");
  }, [id]);

  useEffect(() => {
    Promise.all([load(), getPolicies().then(setPolicies)])
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    if (!fileId) return;
    let alive = true;
    getPreview(id, fileId)
      .then((p) => alive && setPreview(p))
      .catch((e) => alive && setError((e as Error).message));
    return () => { alive = false; };
  }, [id, fileId, refresh]);

  async function act(kind: Busy, fn: () => Promise<void>) {
    setBusy(kind); setError(""); setMsg("");
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

  const doDetect = () => act("detect", async () => {
    const res = await detectTask(id, policyId);
    setSummary(res.summary);
    setFileId((prev) => {
      const cur = res.task.files.find((f) => f.id === prev);
      return cur && cur.findings > 0
        ? prev
        : (res.task.files.find((f) => f.findings > 0)?.id || prev);
    });
    if (res.warnings?.length) setError(res.warnings.slice(0, 2).join("；"));
    setMsg(`检测完成：扫描 ${res.task.stats.scanned ?? 0} 个文件，命中 ${res.summary.total} 处敏感内容。`);
  });

  const doReviewOne = (f: Finding, status: "accepted" | "rejected" | "pending") =>
    act("review", async () => {
      const res = await review(id, { items: [{ id: f.id, status }] });
      setSummary(res.summary);
      setPreview((prev) => prev && {
        ...prev,
        findings: prev.findings.map((x) => (x.id === f.id ? { ...x, status } : x)),
      });
    });

  const doMode = (f: Finding, mode: string) =>
    act("review", async () => {
      const res = await review(id, { items: [{ id: f.id, mode }] });
      setSummary(res.summary);
      setPreview((prev) => prev && {
        ...prev,
        findings: prev.findings.map((x) => (x.id === f.id
          ? { ...x, mode: mode === "default" ? null : mode as Finding["mode"] }
          : x)),
      });
    });

  const doBulk = (accept: boolean) => act("review", async () => {
    const res = await review(id, accept ? { accept_all: true } : { reject_all: true });
    setSummary(res.summary);
    setMsg(accept ? "已将全部待确认结果标记为接受。" : "已忽略全部待确认结果。");
  });

  const doMask = () => act("mask", async () => {
    const res = await maskTask(id);
    setSummary(res.summary);
    const applied = res.stats.findings_applied + (res.stats.media_changed || 0);
    const files = res.stats.files_changed + (res.stats.media_changed || 0);
    setMsg(`脱敏完成：处理 ${applied} 处，涉及 ${files} 个文件。导出时生成文件，源目录保持不变。`);
  });

  const doExport = () => act("export", async () => {
    await download(exportUrl(id, includeMapping), `omnimask_${id}.zip`);
    setMsg("导出包已下载，内含脱敏文件、report.json 与 report.md。");
  });

  const doMapping = () => act("mapping", async () => {
    await download(mappingUrl(id), `omnimask_${id}_mapping.json`);
    setMsg("映射表已下载，请妥善保管。");
  });
  const doRestore = () => act("restore", async () => {
    const res = await restoreTask(id);
    setMsg(`已使用 ${res.tokens} 条映射还原 ${res.files} 个文件，正在下载还原包…`);
    await download(restoreDownloadUrl(id), `omnimask_${id}_restored.zip`);
  });

  const pick = (fid: string) => {
    setActive(fid);
    setTab("original");
    setTimeout(() => {
      document.querySelector(`[data-fid="${fid}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }, 30);
  };

  if (loading) return <Empty title="正在加载任务…" />;
  if (!task || !summary) {
    return (
      <div className="card card-body">
        <Empty icon={<AlertIcon size={26} />} title="任务加载失败" hint={error || "任务可能已被删除"} />
        <div style={{ textAlign: "center" }}><Link href="/" className="btn">返回工作台</Link></div>
      </div>
    );
  }

  const { done, current } = flowState(task.status, summary);
  const selectedFile = task.files.find((f) => f.id === fileId);
  const findings = preview?.findings || [];
  const shown = findings.filter((f) => filter === "all" || f.status === filter);
  const reversible = !!task.policy?.reversible;
  const maskedDone = task.status === "masked" || task.status === "exported";
  const appliedCount = (task.stats.mask?.findings_applied ?? 0) + (task.stats.mask?.media_changed ?? 0);
  const changedFiles = (task.stats.mask?.files_changed ?? 0) + (task.stats.mask?.media_changed ?? 0);

  return (
    <>
      <div className="crumbs">
        <Link href="/">工作台</Link>
        <ChevronIcon size={13} />
        <span className="mono">{task.id}</span>
      </div>

      <div className="task-head">
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow">task · {MODALITY_TEXT[task.modality] || "多模态"}</div>
          <div className="task-title">
            <h1>{task.name}</h1>
            <StatusPill status={task.status} />
          </div>
          <div className="task-sub">
            <span>策略 {task.policy?.name}</span>
            <span>{task.files.length} 文件</span>
            <span>创建 {formatTime(task.created_at)}</span>
            {task.operator && <span>操作人 {task.operator}</span>}
            <span className="source-path" title={task.source_path}>{task.source_path}</span>
          </div>
        </div>
        <div className="task-actions">
          <div className="policy-select-inline">
            <select value={policyId} onChange={(e) => setPolicyId(e.target.value)} disabled={!!busy}>
              {policies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <button className="btn" onClick={doDetect} disabled={!!busy}>
              {busy === "detect" ? <><span className="spinner" />检测中…</> : <><ScanIcon size={15} />{task.status === "created" ? "开始检测" : "重新检测"}</>}
            </button>
          </div>
          <button className="btn" onClick={() => doBulk(true)} disabled={!!busy || summary.pending === 0}>
            <CheckIcon size={14} />全部接受
          </button>
          <button className="btn btn-primary" onClick={doMask} disabled={!!busy || task.status === "created"}>
            {busy === "mask" ? <><span className="spinner" />脱敏中…</> : <><ShieldIcon size={15} />执行脱敏</>}
          </button>
          <button className="btn" onClick={doExport} disabled={!!busy || !maskedDone}>
            {busy === "export" ? <><span className="spinner" />打包中…</> : <><DownloadIcon size={15} />导出 ZIP</>}
          </button>
        </div>
      </div>

      <div className="flow">
        {FLOW.map((label, i) => (
          <div key={label} style={{ display: "contents" }}>
            <div className={`flow-step ${done[i] ? "done" : ""} ${current === i ? "current" : ""}`}>
              <span className="flow-num">{done[i] ? <CheckIcon size={12} /> : i + 1}</span>
              {label}
            </div>
            {i < FLOW.length - 1 && <div className={`flow-line ${done[i] ? "done" : ""}`} />}
          </div>
        ))}
      </div>

      <div className="tiles">
        <div className="tile"><span>命中总数</span><b>{summary.total}</b><span className="tile-sub">待确认 {summary.pending}</span></div>
        <div className="tile red"><span>高风险</span><b>{summary.high}</b><span className="tile-sub">中 {summary.medium} · 低 {summary.low}</span></div>
        <div className="tile"><span>扫描文件</span><b>{task.files.filter((f) => f.is_text).length}</b><span className="tile-sub">共 {task.files.length} 个</span></div>
        <div className="tile lime"><span>已应用</span><b>{appliedCount}</b><span className="tile-sub">{task.stats.mask ? `${changedFiles} 个文件` : "尚未脱敏"}</span></div>
      </div>

      {error && <div className="notice error" style={{ marginBottom: 14 }}><AlertIcon />{error}</div>}
      {msg && <div className="notice success" style={{ marginBottom: 14 }}><CheckIcon />{msg}</div>}
      {maskedDone && (
        <div className="banner">
          <ShieldIcon size={17} />
          <span>
            脱敏方案已确认（{appliedCount} 处）。导出时从源目录即时生成文件，源文件不会被修改。
            {reversible && " 当前策略可逆，映射表由任务结果实时生成。"}
          </span>
          {reversible && (
            <label className="switch" style={{ marginLeft: "auto" }}>
              <input type="checkbox" checked={includeMapping} onChange={(e) => setIncludeMapping(e.target.checked)} />
              导出包包含映射表
            </label>
          )}
          {reversible && <button className="btn btn-sm" onClick={doMapping} disabled={!!busy}><KeyIcon size={13} />下载映射表</button>}
          {reversible && <button className="btn btn-sm" onClick={doRestore} disabled={!!busy}><RestoreIcon size={13} />还原并下载</button>}
          <button className="btn btn-sm btn-primary" onClick={doExport} disabled={!!busy}><DownloadIcon size={13} />导出 ZIP</button>
        </div>
      )}

      {!!task.stats.warnings?.length && (
        <div className="notice" style={{ marginBottom: 14 }}>
          <AlertIcon size={15} />
          <span>检测提示：{task.stats.warnings.slice(0, 3).join("；")}{task.stats.warnings.length > 3 ? ` 等 ${task.stats.warnings.length} 条` : ""}</span>
        </div>
      )}

      <div className="workspace">
        <aside className="rail">
          <div className="rail-head"><span>文件</span><span>{task.files.length}</span></div>
          <div className="rail-list">
            {task.files.map((f) => (
              <button
                key={f.id}
                className={`rail-item ${fileId === f.id ? "active" : ""}`}
                onClick={() => { setFileId(f.id); setActive(""); setTab("original"); }}
                title={f.path}
              >
                <div className="rail-file"><FileIcon size={13} /><span>{f.path}</span></div>
                <div className="rail-meta">
                  <span>{f.is_text ? `${f.lines} 行` : "二进制"}</span>
                  {f.findings > 0 && <span className="hit">{f.findings} 命中</span>}
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="center">
          <div className="panel-top">
            <span className="panel-dot" />
            <span className="panel-title mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {selectedFile?.path || "选择文件"}
            </span>
            {selectedFile && (
              <span className="badge" style={{ marginLeft: 4 }}>{formatBytes(selectedFile.size)}</span>
            )}
            <div className="tabs">
              <button className={`tab ${tab === "original" ? "active" : ""}`} onClick={() => setTab("original")}>原始代码</button>
              <button className={`tab ${tab === "masked" ? "active" : ""}`} onClick={() => setTab("masked")}>脱敏预览</button>
            </div>
          </div>
          {!selectedFile ? (
            <Empty title="选择一个文件" hint="左侧文件列表中选择要查看的源码" />
          ) : preview?.binary ? (
            <Empty icon={<FileIcon size={26} />} title="二进制文件" hint="该文件不会参与代码检测，也不提供预览" />
          ) : preview ? (
            <div className="compare">
              <div className={`pane tabbed ${tab === "original" ? "active" : ""}`}>
                <div className="pane-head">
                  <span>original</span>
                  <span className="mode">{findings.length} findings</span>
                </div>
                <div className="code-scroll">
                  <pre className="code">
                    {renderHighlighted(preview.original, findings, active, pick)}
                  </pre>
                </div>
              </div>
              <div className={`pane tabbed ${tab === "masked" ? "active" : ""}`}>
                <div className="pane-head">
                  <span>masked preview</span>
                  <span className="mode">{modeText(task.policy?.mode)} · {preview.applied} 处</span>
                </div>
                <div className="code-scroll">
                  <pre className="code masked">{preview.masked}</pre>
                </div>
              </div>
            </div>
          ) : (
            <Empty title="正在加载文件…" />
          )}
        </section>

        <aside className="findings-panel">
          <div className="findings-head">
            <span className="panel-title">检测结果</span>
            <span className="badge">{shown.length}/{findings.length}</span>
            <select
              style={{ width: "auto", padding: "4px 24px 4px 8px", fontSize: 11 }}
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
            >
              <option value="all">全部状态</option>
              <option value="pending">待确认</option>
              <option value="accepted">已接受</option>
              <option value="rejected">已忽略</option>
            </select>
            <button className="btn btn-sm btn-ghost" onClick={() => doBulk(true)} disabled={!!busy || summary.pending === 0}>全接受</button>
            <button className="btn btn-sm btn-ghost" onClick={() => doBulk(false)} disabled={!!busy || summary.pending === 0}>全忽略</button>
          </div>
          <div className="findings-list">
            {shown.length === 0 ? (
              <Empty icon={<ShieldIcon size={26} />} title={findings.length ? "当前筛选下没有结果" : "该文件未发现密钥"}
                hint={findings.length ? "切换筛选条件查看其他结果" : "可以尝试切换策略后重新检测"} />
            ) : shown.map((f) => (
              <div
                key={f.id}
                className={`finding ${active === f.id ? "active" : ""} ${f.status === "rejected" ? "rejected" : ""}`}
                onClick={() => pick(f.id)}
              >
                <div className="finding-head">
                  <span className="finding-type">{f.type_label}</span>
                  <RiskPill risk={f.risk} />
                  <FindingStatusPill status={f.status} />
                  <span className="finding-rule">{f.rule_name}</span>
                </div>
                <div className="finding-value" title={f.value}>{f.value}</div>
                <div className="finding-meta">
                  <span>L{f.line_start}{f.line_end !== f.line_start ? `-${f.line_end}` : ""}</span>
                  <span>置信度 {Math.round(f.confidence * 100)}%</span>
                  <span>{f.series}</span>
                  {f.mode && <span>方式 {f.mode}</span>}
                </div>
                <div className="finding-actions" onClick={(e) => e.stopPropagation()}>
                  <div className="status-toggle">
                    <button className={f.status === "accepted" ? "on-accepted" : ""} onClick={() => doReviewOne(f, "accepted")} disabled={!!busy}>接受</button>
                    <button className={f.status === "rejected" ? "on-rejected" : ""} onClick={() => doReviewOne(f, "rejected")} disabled={!!busy}>忽略</button>
                    <button className={f.status === "pending" ? "on-pending" : ""} onClick={() => doReviewOne(f, "pending")} disabled={!!busy}>待定</button>
                  </div>
                  <select value={f.mode || "default"} onChange={(e) => doMode(f, e.target.value)} disabled={!!busy}>
                    <option value="default">跟随策略</option>
                    <option value="mask">打码</option>
                    <option value="delete">删除</option>
                    <option value="replace">代号替换</option>
                    <option value="encrypt">加密</option>
                    <option value="offset">数字偏移</option>
                  </select>
                </div>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </>
  );
}

function renderHighlighted(
  text: string, findings: Finding[], activeId: string, onPick: (id: string) => void,
): ReactNode[] {
  const sorted = findings
    .filter((f) => f.index_start >= 0 && f.index_end <= text.length && f.index_end > f.index_start)
    .sort((a, b) => a.index_start - b.index_start);
  const out: ReactNode[] = [];
  let pos = 0;
  let n = 0;
  for (const f of sorted) {
    if (f.index_start < pos) continue;
    if (f.index_start > pos) out.push(<span key={`t${n++}`}>{text.slice(pos, f.index_start)}</span>);
    out.push(
      <mark
        key={f.id}
        data-fid={f.id}
        className={`hl ${f.risk} ${f.status === "rejected" ? "rejected" : ""} ${f.id === activeId ? "active" : ""}`}
        onClick={(e) => { e.stopPropagation(); onPick(f.id); }}
      >
        {text.slice(f.index_start, f.index_end)}
      </mark>,
    );
    pos = f.index_end;
  }
  if (pos < text.length) out.push(<span key={`t${n++}`}>{text.slice(pos)}</span>);
  return out;
}
