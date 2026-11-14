"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  createTask, detectTask, getEnv, getPolicies, getTasks,
} from "@/lib/api";
import type { Policy, TaskListItem } from "@/lib/types";
import { Empty, formatTime, StatusPill } from "@/components/ui";
import { PathPicker } from "@/components/path-picker";
import {
  AlertIcon, CheckIcon, ChevronIcon, FolderIcon, KeyIcon, ScanIcon,
} from "@/components/icons";

const LAST_PATH = "omnimask:lastPath";

function autoName(p: string): string {
  const base = p.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean).pop() || "";
  return base ? `${base} 密钥检测` : "";
}

export default function HomePage() {
  const router = useRouter();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [sourcePath, setSourcePath] = useState("");
  const [policyId, setPolicyId] = useState("standard");
  const [operator, setOperator] = useState("");
  const [purpose, setPurpose] = useState("内部代码安全检查");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const nameTouched = useRef(false);
  const operatorTouched = useRef(false);

  const reload = () => {
    Promise.all([getTasks(), getPolicies()])
      .then(([t, p]) => { setTasks(t); setPolicies(p); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(reload, []);

  useEffect(() => {
    getEnv()
      .then((env) => { if (!operatorTouched.current) setOperator(env.user); })
      .catch(() => {});
    try {
      const last = localStorage.getItem(LAST_PATH);
      if (last) {
        setSourcePath(last);
        if (!nameTouched.current) setName(autoName(last));
      }
    } catch {}
  }, []);

  function applyPath(p: string) {
    setSourcePath(p);
    if (!nameTouched.current) setName(autoName(p));
  }

  function rememberPath(p: string) {
    try { localStorage.setItem(LAST_PATH, p); } catch {}
  }

  async function submit() {
    setError(""); setNotice("");
    if (!sourcePath.trim()) {
      setError("请填写本地文件或目录路径。");
      return;
    }
    setBusy(true);
    try {
      const { task, warnings } = await createTask({
        source_path: sourcePath.trim(),
        name: name.trim(),
        policy_id: policyId,
        operator: operator.trim(),
        purpose: purpose.trim(),
      });
      rememberPath(sourcePath.trim());
      if (warnings?.length) setNotice(warnings.slice(0, 2).join("；"));
      try {
        await detectTask(task.id, policyId);
      } catch (e) {
        setNotice(`任务已创建，但自动检测失败：${(e as Error).message}。可在任务页重新检测。`);
      }
      router.push(`/tasks/${task.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const selectedPolicy = policies.find((p) => p.id === policyId);
  const totalFindings = tasks.reduce((n, t) => n + t.findings, 0);
  const totalFiles = tasks.reduce((n, t) => n + t.file_count, 0);

  return (
    <>
      <section className="hero">
        <div>
          <div className="eyebrow">code secret scanner · local first</div>
          <h1>把密钥留在<em>源头</em>。</h1>
          <p className="sub">
            填入本机代码文件或目录，复用 keyleak 规则库原地扫描硬编码密钥与敏感串。
            先检测、再人工审核、最后导出，源文件目录不会被修改或复制。
          </p>
        </div>
        <div className="hero-stats">
          <div className="stat"><b>{tasks.length}</b><span>任务总数</span></div>
          <div className="stat hot"><b>{totalFindings}</b><span>累计命中</span></div>
          <div className="stat"><b>{totalFiles}</b><span>已扫描文件</span></div>
        </div>
      </section>

      {error && <div className="notice error" style={{ marginBottom: 16 }}><AlertIcon />{error}</div>}
      {notice && <div className="notice info" style={{ marginBottom: 16 }}><ScanIcon />{notice}</div>}

      <div className="grid-main">
        <div className="stack">
          <section className="card">
            <div className="card-head">
              <div>
                <div className="card-title"><ScanIcon size={17} />新建检测任务</div>
                <div className="card-sub">后端直接读取本地路径，不复制、不移动源文件</div>
              </div>
              <span className="badge teal">local path</span>
            </div>
            <div className="card-body">
              <div className="form-grid" style={{ marginBottom: 16 }}>
                <div className="field">
                  <label>任务名称 <span className="hint">自动取路径名，可修改</span></label>
                  <input value={name} onChange={(e) => { nameTouched.current = true; setName(e.target.value); }} placeholder="选择路径后自动填写" />
                </div>
                <div className="field">
                  <label>操作人 <span className="hint">默认当前系统用户</span></label>
                  <input value={operator} onChange={(e) => { operatorTouched.current = true; setOperator(e.target.value); }} placeholder="例如：陈一东" />
                </div>
                <div className="field full">
                  <label>数据用途 <span className="hint">默认内部代码安全检查</span></label>
                  <input value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="例如：向外部审计方提供脱敏代码用于漏洞评估" />
                </div>
              </div>

              <div className="field">
                <label>本地文件或目录 <span className="hint">绝对路径 · 支持单文件或整个仓库</span></label>
                <div className="path-row">
                  <input
                    className="mono path-input"
                    value={sourcePath}
                    onChange={(e) => applyPath(e.target.value)}
                    onBlur={() => { if (sourcePath.trim()) rememberPath(sourcePath.trim()); }}
                    placeholder="D:\work\my-repo 或 D:\work\app\config.py"
                    spellCheck={false}
                  />
                  <button type="button" className="btn" onClick={() => setPickerOpen(true)}>
                    <FolderIcon size={15} />浏览…
                  </button>
                </div>
                <div className="path-hint">
                  <FolderIcon size={13} />
                  <span>点击「浏览…」选择本机文件或目录；扫描上限 1 MB/文件，二进制与超限文件自动跳过</span>
                </div>
              </div>

              <div className="divider" />

              <div className="field">
                <label>脱敏策略</label>
                <div className="policy-grid">
                  {policies.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className={`policy-card ${policyId === p.id ? "selected" : ""}`}
                      onClick={() => setPolicyId(p.id)}
                    >
                      <div className="policy-name">
                        {p.name}
                        {policyId === p.id && <CheckIcon size={14} className="selected-check" />}
                      </div>
                      <div className="policy-desc">{p.description}</div>
                      <div className="policy-meta">
                        <span className="badge">{p.mode === "mask" ? "打码" : p.mode === "delete" ? "删除" : "代号替换"}</span>
                        {p.reversible && <span className="badge lime">可逆</span>}
                        {p.min_confidence > 0 && <span className="badge amber">阈值 {p.min_confidence}</span>}
                        {p.custom_regex.length > 0 && <span className="badge teal">{p.custom_regex.length} 条自定义</span>}
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="btn-row" style={{ marginTop: 20 }}>
                <button className="btn btn-primary" onClick={submit} disabled={busy || !sourcePath.trim()}>
                  {busy ? <><span className="spinner" />正在检测…</> : <><ScanIcon size={16} />创建任务并开始检测</>}
                </button>
                <span className="sub" style={{ fontSize: 12 }}>
                  {selectedPolicy ? `将使用「${selectedPolicy.name}」策略` : "选择策略后开始"}
                </span>
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <div>
                <div className="card-title"><KeyIcon size={17} />最近任务</div>
                <div className="card-sub">最近的检测与脱敏记录</div>
              </div>
              <span className="badge">{tasks.length} 个</span>
            </div>
            <div className="card-body tight">
              {loading ? (
                <Empty title="正在加载…" />
              ) : tasks.length === 0 ? (
                <Empty icon={<KeyIcon size={26} />} title="还没有任务" hint="填入一个本地代码目录，开始密钥扫描" />
              ) : (
                <div className="task-list">
                  {tasks.slice(0, 8).map((t) => (
                    <Link key={t.id} href={`/tasks/${t.id}`} className="task-row">
                      <div style={{ minWidth: 0 }}>
                        <div className="task-name">
                          <span>{t.name}</span>
                          <StatusPill status={t.status} />
                        </div>
                        <div className="task-meta">
                          <span className="path" title={t.source_path}>{t.source_path || t.id}</span>
                          <span>{t.policy}</span>
                          <span>{t.file_count} 文件</span>
                          <span>{formatTime(t.updated_at)}</span>
                        </div>
                      </div>
                      <div className="task-count"><b>{t.findings}</b>命中</div>
                      <div className="task-go"><ChevronIcon size={16} /></div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </section>
        </div>

        <div className="stack">
          <section className="card">
            <div className="card-head">
              <div>
                <div className="card-title">策略模板</div>
                <div className="card-sub">标准 / 删除 / 可逆 / 严格</div>
              </div>
              <Link href="/policies" className="btn btn-sm btn-ghost">管理<ChevronIcon size={14} /></Link>
            </div>
            <div className="card-body" style={{ display: "grid", gap: 10 }}>
              {policies.map((p) => (
                <div key={p.id} className="policy-card" style={{ cursor: "default" }}>
                  <div className="policy-name">
                    {p.name}
                    {p.builtin ? <span className="badge">内置</span> : <span className="badge teal">自定义</span>}
                  </div>
                  <div className="policy-desc">{p.description}</div>
                </div>
              ))}
              {!policies.length && <Empty title="暂无策略" />}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <div className="card-title">流程</div>
            </div>
            <div className="card-body" style={{ display: "grid", gap: 12 }}>
              {[
                ["01", "输入", "填入本地文件或目录路径"],
                ["02", "检测", "keyleak 规则库原地逐文件匹配"],
                ["03", "审核", "逐条确认、忽略或调整方式"],
                ["04", "脱敏", "生成脱敏内容，不写回源目录"],
                ["05", "导出", "ZIP + 脱敏报告，可附映射表"],
              ].map(([n, t, d]) => (
                <div key={n} style={{ display: "flex", gap: 11 }}>
                  <span className="badge" style={{ height: 22, display: "grid", placeItems: "center" }}>{n}</span>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 550 }}>{t}</div>
                    <div style={{ color: "var(--muted-2)", fontSize: 11.5 }}>{d}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>

      {pickerOpen && (
        <PathPicker
          initial={sourcePath}
          onClose={() => setPickerOpen(false)}
          onPick={(p) => { applyPath(p); rememberPath(p); setPickerOpen(false); }}
        />
      )}
    </>
  );
}
