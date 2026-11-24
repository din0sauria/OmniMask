"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  createTask,
  deleteTask,
  detectTask,
  getEnv,
  getPolicies,
  getTasks,
} from "@/lib/api";
import type { Policy, TaskListItem } from "@/lib/types";
import { Empty, formatTime, modeText, StatusPill } from "@/components/ui";
import { PathPicker } from "@/components/path-picker";
import { Dropdown } from "@/components/dropdown";
import TaskStoragePanel from "@/components/task-storage-panel";
import {
  AlertIcon,
  ChevronIcon,
  FileIcon,
  FolderIcon,
  KeyIcon,
  ScanIcon,
  ShieldIcon,
  XIcon,
} from "@/components/icons";
const LAST_PATH = "omnimask:lastPath";
function suggestedPolicy(path: string) {
  const ext = path.trim().split(".").pop()?.toLowerCase() || "";
  if (/^(png|jpg|jpeg|webp|bmp|tiff|gif|wav|mp3|m4a|flac|ogg|mp4|mov|mkv|webm|avi)$/.test(ext)) return "media";
  if (/^(txt|md|csv|log|rst|html)$/.test(ext)) return "text-pii";
  return "standard";
}
function autoName(p: string) {
  const base =
    p
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .filter(Boolean)
      .pop() || "";
  return base ? `${base} 脱敏` : "";
}
const VOICE_LABEL: Record<string, string> = { "": "不变声", pitch: "升调", robot: "机器声" };
export default function HomePage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<TaskListItem[]>([]),
    [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true),
    [loadError, setLoadError] = useState("");
  const [name, setName] = useState(""),
    [sourcePath, setSourcePath] = useState(""),
    [policyId, setPolicyId] = useState("standard");
  const [operator, setOperator] = useState(""),
    [purpose, setPurpose] = useState("内部敏感信息检查"),
    [voiceChange, setVoiceChange] = useState<"follow" | "" | "pitch" | "robot">("follow");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState(""),
    [status, setStatus] = useState("all"),
    [showAll, setShowAll] = useState(false);
  const [del, setDel] = useState<TaskListItem | null>(null),
    [delBusy, setDelBusy] = useState(false),
    [delError, setDelError] = useState("");
  const delDialog = useRef<HTMLDialogElement>(null);
  const nameTouched = useRef(false),
    policyTouched = useRef(false),
    operatorTouched = useRef(false),
    submitting = useRef(false);
  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    const results = await Promise.allSettled([getTasks(), getPolicies()]);
    const messages: string[] = [];
    if (results[0].status === "fulfilled") setTasks(results[0].value);
    else
      messages.push(`任务加载失败：${results[0].reason.message || "请重试"}`);
    if (results[1].status === "fulfilled") setPolicies(results[1].value.filter(p => p.mode !== "encrypt"));
    else
      messages.push(`策略加载失败：${results[1].reason.message || "请重试"}`);
    setLoadError(messages.join("；"));
    setLoading(false);
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);
  useEffect(() => {
    if (del && !delDialog.current?.open) delDialog.current?.showModal();
    else if (!del && delDialog.current?.open) delDialog.current.close();
  }, [del]);
  useEffect(() => {
    getEnv()
      .then((env) => {
        if (!operatorTouched.current) setOperator(env.user);
      })
      .catch(() => {});
    try {
      const last = localStorage.getItem(LAST_PATH);
      if (last) {
        setSourcePath(last);
        setPolicyId(suggestedPolicy(last));
        if (!nameTouched.current) setName(autoName(last));
      }
    } catch {}
  }, []);
  function applyPath(path: string) {
    setSourcePath(path);
    if (!policyTouched.current) setPolicyId(suggestedPolicy(path));
    if (!nameTouched.current) setName(autoName(path));
  }
  function rememberPath(path: string) {
    try {
      localStorage.setItem(LAST_PATH, path);
    } catch {}
  }
  async function submit() {
    if (submitting.current) return;
    setError("");
    if (!sourcePath.trim()) {
      setError("请先选择一个本地文件或目录。");
      return;
    }
    if (!policies.some((p) => p.id === policyId)) {
      setError("请选择可用的检测策略。");
      return;
    }
    submitting.current = true;
    setBusy(true);
    try {
      const { task } = await createTask({
        source_path: sourcePath.trim(),
        name: name.trim(),
        policy_id: policyId,
        operator: operator.trim(),
        purpose: purpose.trim(),
        voice_change: voiceChange === "follow" ? null : voiceChange,
      });
      rememberPath(sourcePath.trim());
      try {
        await detectTask(task.id, policyId);
      } catch (err) {
        try {
          sessionStorage.setItem(
            `omnimask:task-notice:${task.id}`,
            `任务已创建，但检测未能启动：${(err as Error).message}。请点击开始检测重试。`,
          );
        } catch {}
      }
      router.push(`/tasks/${task.id}`);
    } catch (err) {
      setError((err as Error).message);
      submitting.current = false;
      setBusy(false);
    }
  }
  async function remove() {
    if (!del || delBusy) return;
    setDelBusy(true);
    setDelError("");
    try {
      await deleteTask(del.id);
      setDel(null);
      await reload();
    } catch (err) {
      setDelError((err as Error).message);
    } finally {
      setDelBusy(false);
    }
  }
  const selectedPolicy = policies.find((p) => p.id === policyId);
  const voiceBadge =
    voiceChange === "follow"
      ? selectedPolicy?.voice_change
        ? "变声效果"
        : ""
      : voiceChange
        ? `变声：${VOICE_LABEL[voiceChange]}`
        : "不变声";
  const totalFiles = tasks.reduce((n, t) => n + t.file_count, 0),
    totalFindings = tasks.reduce((n, t) => n + t.findings, 0);
  const filtered = tasks.filter(
    (t) =>
      (status === "all" || t.status === status) &&
      `${t.name} ${t.id} ${t.source_path} ${t.policy}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  const visible = showAll ? filtered : filtered.slice(0, 6);
  return (
    <div className="home-page">
      <section className="home-heading">
        <div>
          <div className="eyebrow">面向多模态数据的隐私脱敏系统</div>
          <h1>
            让每一次分享，<em>更有把握</em>。
          </h1>
          <p className="sub">
            从本地文件开始，检测敏感信息，审核后生成脱敏副本。
          </p>
        </div>
        <div className="home-local">
          <ShieldIcon size={17} />
          <span>
            本机处理<b>源文件只读</b>
          </span>
        </div>
      </section>
      <section className="home-overview" aria-label="任务概览">
        {[
          {
            label: "任务总数",
            value: tasks.length,
            icon: <FolderIcon size={19} />,
            hint: "本机任务记录",
          },
          {
            label: "累计命中",
            value: totalFindings,
            icon: <ScanIcon size={19} />,
            hint: "检测结果需人工复核",
          },
          {
            label: "累计导入文件",
            value: totalFiles,
            icon: <FileIcon size={19} />,
            hint: "含未检测或被跳过文件",
          },
        ].map((item) => (
          <div className="overview-item" key={item.label}>
            <span className="overview-icon">{item.icon}</span>
            <div>
              <span>{item.label}</span>
              <b>{loading ? "—" : item.value.toLocaleString()}</b>
              <small>{item.hint}</small>
            </div>
          </div>
        ))}
      </section>
      {loadError && (
        <div className="notice error home-load-error" role="alert">
          <AlertIcon size={17} />
          <span>{loadError}</span>
          <button
            className="btn btn-sm"
            type="button"
            onClick={() => void reload()}
            disabled={loading}
          >
            重新加载
          </button>
        </div>
      )}
      <div className="home-create-grid">
        <section className="card home-create-card">
          <div className="card-head">
            <div>
              <div className="card-title">
                <ScanIcon size={18} />
                新建检测任务
              </div>
              <div className="card-sub">选择内容与策略，检测将在后台运行</div>
            </div>
            <span className="badge teal">01 / 开始检查</span>
          </div>
          <form
            className="card-body home-create-form"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
            aria-busy={busy}
          >
            <fieldset disabled={busy} className="home-fieldset">
              <div className="home-step-label">
                <span>1</span>
                <label htmlFor="source-path">选择文件或目录</label>
              </div>
              <div className="path-row">
                <input
                  id="source-path"
                  className="mono path-input"
                  value={sourcePath}
                  onChange={(e) => applyPath(e.target.value)}
                  onBlur={() =>
                    sourcePath.trim() && rememberPath(sourcePath.trim())
                  }
                  placeholder="输入本机绝对路径，或点击浏览选择"
                  spellCheck={false}
                  required
                  aria-describedby="source-path-hint"
                />
                <button
                  type="button"
                  className="btn"
                  onClick={() => setPickerOpen(true)}
                >
                  <FolderIcon size={16} />
                  浏览文件
                </button>
              </div>
              <p id="source-path-hint" className="path-hint">
                支持代码、文本、图片、音频与视频。检测不设文件大小与数量上限。
              </p>
              <div className="home-step-label">
                <span>2</span>
                <label htmlFor="home-policy">选择处理策略</label>
                <Link href="/policies">
                  管理策略
                  <ChevronIcon size={12} />
                </Link>
              </div>
              <div className="home-policy-compact">
                <label className="sr-only" htmlFor="home-policy">处理策略</label>
                <Dropdown
                  id="home-policy"
                  ariaLabel="处理策略"
                  value={policyId}
                  disabled={busy}
                  onChange={(v) => {
                    policyTouched.current = true;
                    setPolicyId(v);
                  }}
                  options={policies.map((p) => ({ value: p.id, label: p.name }))}
                />
                <p className="path-hint">{policyTouched.current ? "已保留你手动选择的策略。" : "根据路径扩展名建议策略；目录和未知类型使用标准策略，可自行切换。"}</p>
                {selectedPolicy && <div className="home-policy-summary">
                  <span className="badge teal">{modeText(selectedPolicy.mode)}</span>
                  {voiceBadge && <span className="badge">{voiceBadge}</span>}
                  <p>{selectedPolicy.description}</p>
                  <details><summary>查看处理示例</summary><p>仅为示意，实际效果取决于检测与人工审核。图片/视频遮挡选中区域，音频将选中区间静音；变声开关独立生效。</p><code>{selectedPolicy.mode === "delete" ? "手机号 13800138000 → 手机号" : selectedPolicy.mode === "replace" ? "手机号 13800138000 → 手机号 [PHONE_001]" : selectedPolicy.mode === "offset" ? "数字偏移仅适用于匹配的数字文本，不保证不可恢复。" : "手机号 13800138000 → 手机号 138****8000（示意）"}</code></details>
                </div>}
                {!policies.length && (
                  <Empty
                    title={loading ? "正在加载策略…" : "暂无可用策略"}
                    hint="请重新加载，或到策略配置页创建策略"
                  />
                )}
                <div className="field" style={{ marginTop: 12 }}>
                  <label htmlFor="home-voice">音频 / 视频变声</label>
                  <Dropdown
                    id="home-voice"
                    ariaLabel="音频 / 视频变声"
                    value={voiceChange}
                    onChange={(v) => setVoiceChange(v as typeof voiceChange)}
                    options={[
                      { value: "follow", label: `跟随策略（${VOICE_LABEL[selectedPolicy?.voice_change || ""]}）` },
                      { value: "", label: "不变声，仅静音敏感片段" },
                      { value: "pitch", label: "升调（四半音）" },
                      { value: "robot", label: "机器声" },
                    ]}
                  />
                  <p className="path-hint">覆盖所选策略的变声设置；无敏感项命中时也会生效，不等于声纹匿名化。</p>
                </div>
              </div>
              <details className="home-options">
                <summary>
                  任务信息<span>名称、操作人和用途（可选）</span>
                </summary>
                <div className="form-grid">
                  <div className="field">
                    <label htmlFor="task-name">任务名称</label>
                    <input
                      id="task-name"
                      value={name}
                      onChange={(e) => {
                        nameTouched.current = true;
                        setName(e.target.value);
                      }}
                      placeholder="根据路径自动填写"
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="operator">操作人</label>
                    <input
                      id="operator"
                      value={operator}
                      onChange={(e) => {
                        operatorTouched.current = true;
                        setOperator(e.target.value);
                      }}
                      placeholder="当前系统用户"
                    />
                  </div>
                  <div className="field full">
                    <label htmlFor="task-purpose">数据用途</label>
                    <input
                      id="task-purpose"
                      value={purpose}
                      onChange={(e) => setPurpose(e.target.value)}
                      placeholder="例如：内部资料共享"
                    />
                  </div>
                </div>
              </details>
            </fieldset>
            {error && (
              <div className="notice error" role="alert">
                <AlertIcon size={16} />
                {error}
              </div>
            )}
            <div className="home-submit">
              <span>
                {selectedPolicy ? (
                  <>
                    已选 <b>{selectedPolicy.name}</b>
                  </>
                ) : (
                  "请先选择策略"
                )}
                <small>审核后再生成脱敏结果</small>
              </span>
              <button
                className="btn btn-primary"
                type="submit"
                disabled={busy || !sourcePath.trim() || !selectedPolicy}
              >
                {busy ? (
                  <>
                    <span className="spinner" />
                    正在创建…
                  </>
                ) : (
                  <>
                    <ScanIcon size={16} />
                    创建并开始检测
                    <ChevronIcon size={14} />
                  </>
                )}
              </button>
            </div>
          </form>
        </section>
        <aside className="home-aside">
          <section className="card home-guide">
            <div className="card-head">
              <div className="card-title">每一步，都由你掌握</div>
            </div>
            <div className="card-body">
              <ol className="home-guide-list">
                {[
                  ["检测", "自动定位可能的敏感信息"],
                  ["审核", "确认、忽略，或补充遗漏范围"],
                  ["脱敏", "预览遮挡、静音或文本替换"],
                  ["导出", "下载处理副本与处理报告"],
                ].map(([title, description], i) => (
                  <li key={title}>
                    <span>{i + 1}</span>
                    <div>
                      <b>{title}</b>
                      <p>{description}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </section>
          <div className="home-note">
            <ShieldIcon size={20} />
            <div>
              <b>原件保留，结果可检查</b>
              <p>
                源文件不会被覆盖。检测结果需要人工复核；原始预览与转写也会保存在本机任务目录。
              </p>
              <Link href="/policies">
                了解处理策略
                <ChevronIcon size={13} />
              </Link>
            </div>
          </div>
        </aside>
      </div>
      <section className="card home-recent">
        <div className="card-head">
          <div>
            <div className="card-title">
              <KeyIcon size={17} />
              任务记录<span className="badge">{tasks.length}</span>
            </div>
            <div className="card-sub">继续审核，或查看已生成的处理结果</div>
          </div>
          <button
            className="btn btn-sm btn-ghost"
            type="button"
            onClick={() => void reload()}
            disabled={loading}
          >
            {loading ? "更新中…" : "刷新记录"}
          </button>
        </div>
        <div className="task-list-tools">
          <input
            aria-label="搜索任务"
            type="search"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setShowAll(false);
            }}
            placeholder="搜索任务名称、路径或策略…"
          />
          <Dropdown
            ariaLabel="筛选任务状态"
            value={status}
            onChange={(v) => {
              setStatus(v);
              setShowAll(false);
            }}
            options={[
              { value: "all", label: "全部状态" },
              { value: "created", label: "待检测" },
              { value: "queued", label: "排队中" },
              { value: "detecting", label: "检测中" },
              { value: "detected", label: "待审核" },
              { value: "reviewed", label: "已审核" },
              { value: "masking", label: "脱敏中" },
              { value: "masked", label: "已脱敏" },
              { value: "exported", label: "已导出" },
              { value: "failed", label: "处理失败" },
              { value: "cancelled", label: "已取消" },
              { value: "interrupted", label: "已中断" },
              { value: "partial", label: "部分检测失败" },
            ]}
          />
        </div>
        <div className="card-body tight">
          {loading && !tasks.length ? (
            <Empty title="正在加载任务…" />
          ) : !tasks.length ? (
            <Empty
              icon={<FolderIcon size={25} />}
              title={loadError ? "任务记录暂不可用" : "还没有任务"}
              hint={
                loadError
                  ? "请检查服务连接后重新加载"
                  : "选择上方本地文件，开始第一次检测"
              }
            />
          ) : !filtered.length ? (
            <div className="home-no-results">
              <Empty title="没有匹配的任务" hint="试试其他关键词或任务状态" />
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => {
                  setQuery("");
                  setStatus("all");
                }}
              >
                清除筛选
              </button>
            </div>
          ) : (
            <div className="task-list">
              {visible.map((t) => (
                <div className="task-row" key={t.id}>
                  <Link href={`/tasks/${t.id}`} className="task-row-link">
                    <span className="task-row-icon">
                      <FileIcon size={18} />
                    </span>
                    <div className="task-row-main">
                      <div className="task-name">
                        <span>{t.name}</span>
                        <StatusPill status={t.status} />
                      </div>
                      <div className="task-meta">
                        <span className="path" title={t.source_path}>
                          {t.source_path || t.id}
                        </span>
                        <span>{t.policy}</span>
                        <span>{t.file_count} 文件</span>
                      </div>
                    </div>
                    <div className="task-row-end">
                      <span>{formatTime(t.updated_at)}</span>
                      <b>
                        {t.findings}
                        <small> 命中</small>
                      </b>
                    </div>
                    <ChevronIcon size={16} />
                  </Link>
                  <button
                    type="button"
                    className="task-del"
                    aria-label={`删除任务 ${t.name}`}
                    title="移入回收站"
                    onClick={() => {
                      setDel(t);
                      setDelError("");
                    }}
                  >
                    <XIcon size={15} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        {filtered.length > 6 && (
          <div className="home-list-footer">
            <span>
              显示 {visible.length} / {filtered.length} 条记录
            </span>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              onClick={() => setShowAll(!showAll)}
            >
              {showAll ? "收起记录" : "显示全部记录"}
              <ChevronIcon size={13} />
            </button>
          </div>
        )}
      </section>
      <TaskStoragePanel onChanged={reload} />
      <dialog
        ref={delDialog}
        className="confirmation-dialog"
        aria-labelledby="del-title"
        onCancel={(event) => {
          event.preventDefault();
          if (!delBusy) setDel(null);
        }}
      >
        {del && (
          <>
            <h2 id="del-title">删除任务「{del.name}」？</h2>
            <p>
              任务移入回收站，可在「本地任务与存储」中恢复。源文件不会被移动或删除。
            </p>
            {delError && (
              <div className="notice error" role="alert">
                <AlertIcon size={16} />
                <span>{delError}</span>
              </div>
            )}
            <div className="btn-row">
              <button
                type="button"
                className="btn"
                autoFocus
                disabled={delBusy}
                onClick={() => setDel(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={delBusy}
                onClick={() => void remove()}
              >
                {delBusy ? "删除中…" : "移入回收站"}
              </button>
            </div>
          </>
        )}
      </dialog>
      {pickerOpen && (
        <PathPicker
          initial={sourcePath}
          onClose={() => setPickerOpen(false)}
          onPick={(path) => {
            applyPath(path);
            rememberPath(path);
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}
