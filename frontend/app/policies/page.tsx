"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  createPolicy, deletePolicy, getPolicies, updatePolicy,
} from "@/lib/api";
import type { CustomRule, MaskMode, Policy } from "@/lib/types";
import { Empty } from "@/components/ui";
import { AlertIcon, CheckIcon, KeyIcon, PlusIcon, ScanIcon, ShieldIcon, XIcon } from "@/components/icons";

type FormState = {
  name: string;
  description: string;
  mode: MaskMode;
  reversible: boolean;
  mask_keep: number;
  min_confidence: number;
  blocked: string;
  custom_regex: CustomRule[];
};

const EMPTY: FormState = {
  name: "",
  description: "",
  mode: "mask",
  reversible: false,
  mask_keep: 4,
  min_confidence: 0,
  blocked: "certification",
  custom_regex: [],
};

function regexError(pattern: string): string {
  return pattern.trim() ? "" : "正则不能为空";
}

export default function PoliciesPage() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<"" | "new" | string>("");
  const [form, setForm] = useState<FormState>(EMPTY);
  const [busy, setBusy] = useState(false);

  const reload = () => getPolicies().then(setPolicies).catch((e) => setError(e.message)).finally(() => setLoading(false));
  useEffect(() => { reload(); }, []);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  function startNew(p?: Policy) {
    setError(""); setMsg("");
    if (p) {
      setForm({
        name: `${p.name} 副本`,
        description: p.description,
        mode: p.mode,
        reversible: p.reversible,
        mask_keep: p.mask_keep,
        min_confidence: p.min_confidence,
        blocked: p.blocked_series.join(", "),
        custom_regex: p.custom_regex.map((r) => ({ ...r })),
      });
      setEditing("new");
    } else {
      setForm(EMPTY);
      setEditing("new");
    }
  }

  function startEdit(p: Policy) {
    setError(""); setMsg("");
    setForm({
      name: p.name,
      description: p.description,
      mode: p.mode,
      reversible: p.reversible,
      mask_keep: p.mask_keep,
      min_confidence: p.min_confidence,
      blocked: p.blocked_series.join(", "),
      custom_regex: p.custom_regex.map((r) => ({ ...r })),
    });
    setEditing(p.id);
  }

  async function save() {
    setError(""); setMsg("");
    if (!form.name.trim()) { setError("策略名称不能为空"); return; }
    const bad = form.custom_regex.find((r) => regexError(r.regex));
    if (bad) { setError(`自定义规则「${bad.name || "未命名"}」正则无效：${regexError(bad.regex)}`); return; }
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      mode: form.mode,
      reversible: form.reversible,
      mask_keep: Number(form.mask_keep) || 0,
      min_confidence: Number(form.min_confidence) || 0,
      blocked_series: form.blocked.split(",").map((s) => s.trim()).filter(Boolean),
      custom_regex: form.custom_regex.map((r) => ({
        name: r.name.trim() || "自定义规则",
        regex: r.regex,
        confidence: Number(r.confidence) || 0.9,
      })),
    };
    try {
      setBusy(true);
      if (editing === "new") await createPolicy(payload);
      else await updatePolicy(editing, payload);
      setMsg(editing === "new" ? "策略已创建。" : "策略已更新。");
      setEditing("");
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(p: Policy) {
    if (!window.confirm(`确定删除策略「${p.name}」？`)) return;
    try {
      await deletePolicy(p.id);
      setMsg("策略已删除。");
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const modeText = (m: string) => (m === "mask" ? "打码" : m === "delete" ? "删除" : "代号替换");

  return (
    <>
      <section className="hero" style={{ marginBottom: 24 }}>
        <div>
          <div className="eyebrow">policy studio</div>
          <h1 style={{ fontSize: "clamp(1.8rem, 2.8vw, 2.5rem)" }}>策略配置</h1>
          <p className="sub" style={{ maxWidth: 620 }}>
            策略决定什么被处理、处理方式与可逆性。内置四套模板，也可以添加自定义正则识别企业内部字段。
          </p>
        </div>
        <div className="btn-row">
          <button className="btn btn-primary" onClick={() => startNew()}>
            <PlusIcon size={15} />新建策略
          </button>
          <Link href="/" className="btn btn-ghost">返回工作台</Link>
        </div>
      </section>

      {error && <div className="notice error" style={{ marginBottom: 14 }}><AlertIcon />{error}</div>}
      {msg && <div className="notice success" style={{ marginBottom: 14 }}><CheckIcon />{msg}</div>}

      <div className="policy-page">
        <section className="card">
          <div className="card-head">
            <div>
              <div className="card-title"><ShieldIcon size={17} />策略列表</div>
              <div className="card-sub">内置模板只读，可复制后修改</div>
            </div>
            <span className="badge">{policies.length} 个</span>
          </div>
          <div className="card-body tight">
            {loading ? <Empty title="正在加载…" /> : policies.length === 0 ? <Empty title="暂无策略" /> : (
              <div className="policy-rows">
                {policies.map((p) => (
                  <div className="policy-row" key={p.id}>
                    <div>
                      <h3>
                        {p.name}
                        {p.builtin ? <span className="badge">内置</span> : <span className="badge teal">自定义</span>}
                        {editing === p.id && <span className="badge lime">编辑中</span>}
                      </h3>
                      <p>{p.description || "未填写说明"}</p>
                      <div className="policy-meta">
                        <span className="badge">{modeText(p.mode)}</span>
                        <span className={`badge ${p.reversible ? "lime" : ""}`}>{p.reversible ? "可逆" : "不可逆"}</span>
                        <span className="badge">保留 {p.mask_keep} 字符</span>
                        {p.min_confidence > 0 && <span className="badge amber">置信度 ≥ {p.min_confidence}</span>}
                        {p.blocked_series.length > 0 && <span className="badge">忽略 {p.blocked_series.join(" / ")}</span>}
                        {p.custom_regex.length > 0 && <span className="badge teal">{p.custom_regex.length} 条自定义正则</span>}
                      </div>
                      {p.custom_regex.length > 0 && (
                        <div className="file-chips">
                          {p.custom_regex.map((r, i) => (
                            <span className="chip" key={i}><KeyIcon size={11} /><span>{r.name}</span></span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="policy-row-actions">
                      {!p.builtin && <button className="btn btn-sm" onClick={() => startEdit(p)}>编辑</button>}
                      <button className="btn btn-sm btn-ghost" onClick={() => startNew(p)}>复制</button>
                      {!p.builtin && <button className="btn btn-sm btn-ghost btn-danger" onClick={() => remove(p)}><XIcon size={13} /></button>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="editor-sticky">
          {editing ? (
            <div className="card">
              <div className="card-head">
                <div>
                  <div className="card-title"><ScanIcon size={17} />{editing === "new" ? "新建策略" : "编辑策略"}</div>
                  <div className="card-sub">保存后可在新建任务时选择</div>
                </div>
                <button className="btn btn-sm btn-ghost" onClick={() => setEditing("")}><XIcon size={14} />收起</button>
              </div>
              <div className="card-body" style={{ display: "grid", gap: 14 }}>
                <div className="field">
                  <label>策略名称</label>
                  <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="例如：对外分享-严格" />
                </div>
                <div className="field">
                  <label>说明</label>
                  <input value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="这个策略适合什么场景" />
                </div>
                <div className="form-grid">
                  <div className="field">
                    <label>处理方式</label>
                    <select value={form.mode} onChange={(e) => set("mode", e.target.value as MaskMode)}>
                      <option value="mask">打码（保留首尾）</option>
                      <option value="delete">删除</option>
                      <option value="replace">代号替换</option>
                    </select>
                  </div>
                  <div className="field">
                    <label>保留字符数 <span className="hint">打码时首尾各保留</span></label>
                    <input type="number" min={0} max={16} value={form.mask_keep}
                      onChange={(e) => set("mask_keep", Number(e.target.value))} />
                  </div>
                </div>
                <label className="switch">
                  <input type="checkbox" checked={form.reversible} onChange={(e) => set("reversible", e.target.checked)} />
                  可逆脱敏（保留映射表，可用授权映射还原）
                </label>
                <div className="field">
                  <label>置信度阈值 <span className="hint">{Math.round(form.min_confidence * 100)}%</span></label>
                  <input type="range" min={0} max={1} step={0.05} value={form.min_confidence}
                    onChange={(e) => set("min_confidence", Number(e.target.value))} />
                </div>
                <div className="field">
                  <label>忽略的规则系列 <span className="hint">逗号分隔，例如 certification, Uri</span></label>
                  <input className="mono" value={form.blocked} onChange={(e) => set("blocked", e.target.value)} placeholder="certification" />
                </div>

                <div className="divider" />
                <div className="field">
                  <label>
                    自定义正则
                    <span className="hint">命中内容将直接进入结果列表</span>
                  </label>
                  <div className="regex-list">
                    {form.custom_regex.map((r, i) => {
                      const err = r.regex && regexError(r.regex);
                      return (
                        <div className="regex-row" key={i}>
                          <div className="grid3">
                            <div className="field">
                              <label>名称</label>
                              <input value={r.name} onChange={(e) => {
                                const list = [...form.custom_regex];
                                list[i] = { ...r, name: e.target.value };
                                set("custom_regex", list);
                              }} placeholder="内部账号" />
                            </div>
                            <div className="field">
                              <label>置信度</label>
                              <input type="number" min={0} max={1} step={0.05} value={r.confidence} onChange={(e) => {
                                const list = [...form.custom_regex];
                                list[i] = { ...r, confidence: Number(e.target.value) };
                                set("custom_regex", list);
                              }} />
                            </div>
                            <button className="btn btn-sm btn-ghost btn-danger" onClick={() => {
                              set("custom_regex", form.custom_regex.filter((_, j) => j !== i));
                            }}><XIcon size={13} /></button>
                          </div>
                          <div className="field">
                            <label>正则表达式</label>
                            <textarea className="mono" value={r.regex} rows={2} onChange={(e) => {
                              const list = [...form.custom_regex];
                              list[i] = { ...r, regex: e.target.value };
                              set("custom_regex", list);
                            }} placeholder="INT-[0-9]{4}" />
                          </div>
                          {err && <div className="regex-err">{err}</div>}
                        </div>
                      );
                    })}
                    <button className="btn btn-sm" onClick={() => set("custom_regex", [
                      ...form.custom_regex,
                      { name: "内部编号", regex: "", confidence: 0.9 },
                    ])}>
                      <PlusIcon size={13} />添加自定义规则
                    </button>
                  </div>
                </div>

                <div className="btn-row">
                  <button className="btn btn-primary" onClick={save} disabled={busy}>
                    {busy ? <><span className="spinner" />保存中…</> : <><CheckIcon size={14} />保存策略</>}
                  </button>
                  <button className="btn btn-ghost" onClick={() => setEditing("")} disabled={busy}>取消</button>
                </div>
              </div>
            </div>
          ) : (
            <div className="card">
              <div className="card-head">
                <div className="card-title"><KeyIcon size={17} />使用说明</div>
              </div>
              <div className="card-body" style={{ display: "grid", gap: 12 }}>
                <div className="kv"><span>打码</span><b>保留首尾字符，中间替换为 *</b></div>
                <div className="kv"><span>删除</span><b>直接移除命中内容</b></div>
                <div className="kv"><span>代号替换</span><b>同一密钥始终对应同一代号</b></div>
                <div className="kv"><span>可逆</span><b>映射表保存在任务目录，可还原</b></div>
                <div className="divider" />
                <p className="sub" style={{ fontSize: 12.5 }}>
                  自定义正则使用 Python re 语法（可在后端检测时报告无效规则），支持捕获组：若存在且仅存在一个捕获组，只有该组内容会被处理。
                </p>
                <button className="btn btn-primary" onClick={() => startNew()}><PlusIcon size={14} />新建策略</button>
              </div>
            </div>
          )}

          <div className="card" style={{ marginTop: 16 }}>
            <div className="card-body" style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div className="badge teal">keyleak</div>
              <span className="card-sub" style={{ margin: 0 }}>
                检测规则库：merged_rules.json · 1124 条内置规则
              </span>
            </div>
          </div>
        </section>
      </div>

      <div style={{ marginTop: 22 }}>
        <Link href="/" className="btn btn-ghost">返回工作台</Link>
      </div>
    </>
  );
}
