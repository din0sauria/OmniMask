"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  createPolicy,
  deletePolicy,
  getPolicies,
  updatePolicy,
} from "@/lib/api";
import type { CustomRule, MaskMode, Policy } from "@/lib/types";
import { Empty } from "@/components/ui";
import {
  AlertIcon,
  CheckIcon,
  KeyIcon,
  PlusIcon,
  ScanIcon,
  ShieldIcon,
  XIcon,
} from "@/components/icons";

type FormState = {
  name: string;
  description: string;
  mode: MaskMode;
  reversible: boolean;
  mask_keep: number;
  min_confidence: number;
  blocked: string;
  custom_regex: CustomRule[];
  pii_types: string[];
  crypto_key: string;
  offset_amount: number;
  offset_round: number;
  voice_change: string;
  use_model: boolean;
};
const PII_OPTIONS = [
  ["phone", "手机号"],
  ["id_card", "身份证号"],
  ["email", "邮箱"],
  ["bank_card", "银行卡号"],
  ["name", "姓名"],
  ["address", "地址"],
] as const;
const EMPTY: FormState = {
  name: "",
  description: "",
  mode: "mask",
  reversible: false,
  mask_keep: 4,
  min_confidence: 0,
  blocked: "certification",
  custom_regex: [],
  pii_types: [],
  crypto_key: "",
  offset_amount: 0,
  offset_round: 1,
  voice_change: "",
  use_model: false,
};
const modeText = (mode: string) =>
  ({
    mask: "打码",
    delete: "删除",
    replace: "代号替换",
    encrypt: "旧实验加密（已停用）",
    offset: "数字偏移",
  })[mode] || mode;
function fromPolicy(p: Policy): FormState {
  return {
    name: p.name,
    description: p.description,
    mode: p.mode,
    reversible: p.reversible,
    mask_keep: p.mask_keep,
    min_confidence: p.min_confidence,
    blocked: p.blocked_series.join(", "),
    custom_regex: p.custom_regex.map((r) => ({ ...r })),
    pii_types: [...(p.pii_types || [])],
    crypto_key: p.crypto_key || "",
    offset_amount: p.offset_amount ?? 0,
    offset_round: p.offset_round ?? 1,
    voice_change: p.voice_change || "",
    use_model: p.use_model ?? false,
  };
}
type Confirmation = {
  title: string;
  detail: string;
  actionLabel: string;
  danger?: boolean;
  run: () => void;
};

export default function PoliciesPage() {
  const router = useRouter();
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState("");
  const [form, setForm] = useState<FormState>(EMPTY);
  const [baseline, setBaseline] = useState(JSON.stringify(EMPTY));
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const editor = useRef<HTMLElement>(null);
  const dirty = !!editing && JSON.stringify(form) !== baseline;
  const reload = async () => {
    try {
      setPolicies(await getPolicies());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void reload();
  }, []);
  useEffect(() => {
    if (confirmation) dialog.current?.showModal();
    else dialog.current?.close();
  }, [confirmation]);
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  useEffect(() => {
    if (!dirty) return;
    // Next Link navigates without unloading the document. Capture departure
    // links while this editor owns a draft, including links in the site header.
    // Modified clicks/new tabs do not discard this page and must work normally.
    const guardNavigation = (event: MouseEvent) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        !(event.target instanceof Element)
      )
        return;
      const anchor = event.target.closest("a");
      if (
        !(anchor instanceof HTMLAnchorElement) ||
        anchor.hasAttribute("download") ||
        (anchor.target && anchor.target !== "_self")
      )
        return;
      const destination = new URL(anchor.href, window.location.href);
      const current = new URL(window.location.href);
      if (
        destination.origin !== current.origin ||
        (destination.pathname === current.pathname &&
          destination.search === current.search)
      )
        return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (busy) return;
      setConfirmation({
        title: "放弃未保存的修改？",
        detail: "当前策略的修改尚未保存。继续后将丢弃这些修改。",
        actionLabel: "放弃修改并继续",
        run: () =>
          router.push(
            destination.pathname + destination.search + destination.hash,
          ),
      });
    };
    document.addEventListener("click", guardNavigation, true);
    return () => document.removeEventListener("click", guardNavigation, true);
  }, [dirty, busy, router]);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setError("");
    setForm((f) => ({ ...f, [key]: value }));
  };
  const switchTo = (action: () => void) => {
    if (busy) return;
    if (dirty)
      setConfirmation({
        title: "放弃未保存的修改？",
        detail: "当前策略的修改尚未保存。继续后将丢弃这些修改。",
        actionLabel: "放弃修改并继续",
        run: action,
      });
    else action();
  };
  function open(p?: Policy, copy = false) {
    const value = p
      ? fromPolicy(p)
      : { ...EMPTY, custom_regex: [], pii_types: [] };
    if (copy && p) {
      value.name = `${p.name} 副本`;
      value.crypto_key = "";
    }
    setForm(value);
    setBaseline(JSON.stringify(value));
    setEditing(p && !copy ? p.id : "new");
    setError("");
    setMsg("");
    window.setTimeout(() => {
      editor.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      document.getElementById("policy-name")?.focus({ preventScroll: true });
    }, 0);
  }
  async function save() {
    if (busy) return;
    setError("");
    setMsg("");
    if (form.mode === "encrypt") {
      setError("旧实验加密已停用，请选择掩码、删除或文本代号替换后保存。");
      return;
    }
    if (!form.name.trim()) {
      setError("请填写策略名称。");
      document.getElementById("policy-name")?.focus();
      return;
    }
    if (
      !Number.isFinite(form.mask_keep) ||
      form.mask_keep < 0 ||
      form.mask_keep > 16 ||
      !Number.isInteger(form.mask_keep)
    ) {
      setError("保留字符数须为0–16的整数。");
      return;
    }
    if (
      !Number.isFinite(form.min_confidence) ||
      form.min_confidence < 0 ||
      form.min_confidence > 1
    ) {
      setError("置信度须在0–1之间。");
      return;
    }
    if (
      form.custom_regex.some(
        (r) =>
          !r.regex.trim() ||
          !Number.isFinite(r.confidence) ||
          r.confidence < 0 ||
          r.confidence > 1,
      )
    ) {
      setError("请补全自定义正则，置信度须在0–1之间。");
      document.getElementById("policy-advanced")?.setAttribute("open", "");
      return;
    }
    if (
      form.mode === "offset" &&
      (!Number.isInteger(form.offset_amount) ||
        !Number.isInteger(form.offset_round) ||
        form.offset_round < 1)
    ) {
      setError("偏移量须为整数，取整单位须为正整数。");
      return;
    }
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      mode: form.mode,
      reversible: form.reversible,
      mask_keep: form.mask_keep,
      min_confidence: form.min_confidence,
      blocked_series: form.blocked
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      custom_regex: form.custom_regex.map((r) => ({
        ...r,
        name: r.name.trim() || "自定义规则",
      })),
      pii_types: form.pii_types,
      crypto_key: form.crypto_key.trim(),
      offset_amount: form.offset_amount,
      offset_round: form.offset_round,
      voice_change: form.voice_change,
      use_model: form.use_model,
    };
    setBusy(true);
    try {
      if (editing === "new") await createPolicy(payload);
      else await updatePolicy(editing, payload);
      setMsg(
        editing === "new"
          ? "策略已创建，可在新建任务时选择。"
          : "策略已保存。已有任务需重新检测才能采用新配置。",
      );
      setEditing("");
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(p: Policy) {
    setBusy(true);
    setDeleting(p.id);
    setError("");
    setMsg("");
    try {
      await deletePolicy(p.id);
      if (editing === p.id) setEditing("");
      setMsg(`已删除策略「${p.name}」。`);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setDeleting("");
    }
  }
  const visible = policies.filter(
    (p) =>
      (category === "all" ||
        (category === "builtin" ? p.builtin : !p.builtin)) &&
      `${p.name} ${p.description}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  const back = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (dirty) {
      e.preventDefault();
      switchTo(() => router.push("/"));
    }
  };
  return (
    <>
      <section className="hero policy-hero">
        <div>
          <div className="eyebrow">处理规则 / POLICIES</div>
          <h1>策略配置</h1>
          <p className="sub">
            选择处理方式与检测范围。复制内置模板，建立适合自己场景的策略。
          </p>
        </div>
        <div className="btn-row">
          <Link href="/" onClick={back} className="btn btn-ghost">
            返回工作台
          </Link>
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() => switchTo(() => open())}
          >
            <PlusIcon size={15} />
            新建策略
          </button>
        </div>
      </section>
      {error && (
        <div className="notice error" role="alert">
          <AlertIcon />
          <span>{error}</span>
        </div>
      )}
      {msg && (
        <div className="notice success" role="status">
          <CheckIcon />
          <span>{msg}</span>
        </div>
      )}
      <div className="policy-page">
        <section className="card">
          <div className="card-head">
            <div>
              <div className="card-title">
                <ShieldIcon size={17} />
                策略库 <span className="badge">{policies.length}</span>
              </div>
              <div className="card-sub">内置模板只读；自定义策略可编辑</div>
            </div>
          </div>
          <div className="policy-library-tools">
            <input
              aria-label="搜索策略"
              type="search"
              placeholder="搜索策略名称或说明"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <div className="segmented" aria-label="策略类型">
              {[
                ["all", "全部"],
                ["builtin", "内置模板"],
                ["custom", "自定义"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  aria-pressed={category === key}
                  className={category === key ? "active" : ""}
                  onClick={() => setCategory(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="card-body tight">
            {loading ? (
              <Empty title="正在加载策略…" />
            ) : !policies.length ? (
              <div className="policy-empty">
                <Empty
                  icon={<ShieldIcon size={25} />}
                  title={error ? "策略加载失败" : "还没有可用策略"}
                  hint="可以重新加载，或新建一个自定义策略。"
                />
                <button
                  className="btn"
                  onClick={() => {
                    setLoading(true);
                    setError("");
                    void reload();
                  }}
                >
                  重新加载
                </button>
              </div>
            ) : !visible.length ? (
              <div className="policy-empty">
                <Empty
                  title="没有匹配的策略"
                  hint="试试其他关键词或筛选条件。"
                />
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    setQuery("");
                    setCategory("all");
                  }}
                >
                  清除筛选
                </button>
              </div>
            ) : (
              <div className="policy-rows">
                {visible.map((p) => (
                  <article
                    className={`policy-row ${editing === p.id ? "is-editing" : ""}`}
                    key={p.id}
                  >
                    <div>
                      <h3>
                        {p.name}
                        <span className={`badge ${p.builtin ? "" : "teal"}`}>
                          {p.builtin ? "内置模板" : "自定义"}
                        </span>
                        {editing === p.id && (
                          <span className="badge lime">编辑中</span>
                        )}
                      </h3>
                      <p>{p.description || "暂无说明"}</p>
                      <div className="policy-meta">
                        <span className="badge">文本 · {modeText(p.mode)}</span>
                        {p.reversible && p.mode === "replace" && (
                          <span className="badge lime">文本映射还原</span>
                        )}
                        {p.min_confidence > 0 && (
                          <span className="badge">
                            阈值 {Math.round(p.min_confidence * 100)}%
                          </span>
                        )}
                        {p.voice_change && (
                          <span className="badge teal">
                            {p.voice_change === "pitch" ? "升调变声" : "机器声"}
                          </span>
                        )}
                        {p.custom_regex.length > 0 && (
                          <span className="badge">
                            {p.custom_regex.length} 条自定义规则
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="policy-row-actions">
                      {!p.builtin && (
                        <button
                          disabled={busy}
                          className="btn btn-sm"
                          onClick={() => switchTo(() => open(p))}
                        >
                          编辑
                        </button>
                      )}
                      <button
                        disabled={busy}
                        className="btn btn-sm btn-ghost"
                        onClick={() => switchTo(() => open(p, true))}
                      >
                        复制
                      </button>
                      {!p.builtin && (
                        <button
                          disabled={busy}
                          aria-label={`删除策略 ${p.name}`}
                          title="删除策略"
                          className="btn btn-sm btn-ghost btn-danger"
                          onClick={() =>
                            setConfirmation({
                              title: `删除「${p.name}」？`,
                              detail:
                                editing === p.id && dirty
                                  ? "该策略及当前未保存的修改将被删除，无法撤销。"
                                  : "删除后无法在新任务中选择此策略。此操作无法撤销。",
                              actionLabel: "删除策略",
                              danger: true,
                              run: () => void remove(p),
                            })
                          }
                        >
                          {deleting === p.id ? (
                            <span className="spinner" />
                          ) : (
                            <XIcon size={14} />
                          )}
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
        <section className="editor-sticky" ref={editor}>
          {editing ? (
            <form
              className="card policy-editor"
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <div className="card-head">
                <div>
                  <div className="card-title">
                    <ScanIcon size={17} />
                    {editing === "new" ? "新建策略" : "编辑策略"}
                  </div>
                  <div className="card-sub">
                    {dirty ? "有未保存的修改" : "设置处理方式，保存后用于任务"}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  className="btn btn-sm btn-ghost"
                  aria-label="收起策略编辑器"
                  onClick={() =>
                    switchTo(() => {
                      setEditing("");
                      setError("");
                    })
                  }
                >
                  <XIcon size={15} />
                </button>
              </div>
              <fieldset disabled={busy} className="policy-editor-fields">
                <section className="editor-section">
                  <div className="editor-section-title">
                    <span>01</span>
                    <h3>基本信息</h3>
                  </div>
                  <div className="field">
                    <label htmlFor="policy-name">
                      策略名称 <span className="hint">必填</span>
                    </label>
                    <input
                      id="policy-name"
                      value={form.name}
                      onChange={(e) => set("name", e.target.value)}
                      placeholder="例如：对外分享"
                      required
                      maxLength={100}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="policy-description">策略说明</label>
                    <textarea
                      id="policy-description"
                      rows={2}
                      value={form.description}
                      onChange={(e) => set("description", e.target.value)}
                      placeholder="说明使用场景，方便后续选择"
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="policy-confidence">
                      处理置信度阈值{" "}
                      <span className="hint">
                        {Math.round(form.min_confidence * 100)}%
                      </span>
                    </label>
                    <input
                      id="policy-confidence"
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={form.min_confidence}
                      onChange={(e) =>
                        set("min_confidence", Number(e.target.value))
                      }
                    />
                    <small>
                      低于阈值的结果不处理；提高阈值可能跳过敏感项。
                    </small>
                  </div>
                </section>
                <details className="editor-group" open>
                  <summary>
                    <span>02</span>文本处理与检测类型
                  </summary>
                  <div className="editor-group-body">
                    <div className="form-grid">
                      <div className="field">
                        <label htmlFor="policy-mode">文本处理方式</label>
                        <select
                          id="policy-mode"
                          value={form.mode}
                          onChange={(e) =>
                            set("mode", e.target.value as MaskMode)
                          }
                        >
                          <option value="mask">打码（保留首尾）</option>
                          <option value="delete">删除</option>
                          <option value="replace">代号替换</option>
                          {form.mode === "encrypt" && (
                            <option value="encrypt" disabled>
                              旧实验加密（已停用，请更换）
                            </option>
                          )}
                          <option value="offset">数字偏移</option>
                        </select>
                      </div>
                      {form.mode === "mask" && (
                        <div className="field">
                          <label htmlFor="policy-keep">首尾各保留字符数</label>
                          <input
                            id="policy-keep"
                            type="number"
                            min={0}
                            max={16}
                            step={1}
                            value={form.mask_keep}
                            onChange={(e) =>
                              set("mask_keep", Number(e.target.value))
                            }
                          />
                        </div>
                      )}
                    </div>
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={form.reversible}
                        onChange={(e) => set("reversible", e.target.checked)}
                      />
                      允许文本代号映射还原
                    </label>
                    <small>
                      仅实际执行的代号替换可通过映射表还原；媒体遮挡和静音不支持还原。
                    </small>
                    <div className="field">
                      <span className="field-label">
                        文本 PII 类型{" "}
                        <span className="hint">不勾选时全部启用</span>
                      </span>
                      <div className="policy-checks">
                        {PII_OPTIONS.map(([key, label]) => (
                          <label key={key}>
                            <input
                              type="checkbox"
                              checked={form.pii_types.includes(key)}
                              onChange={(e) =>
                                set(
                                  "pii_types",
                                  e.target.checked
                                    ? [...form.pii_types, key]
                                    : form.pii_types.filter((v) => v !== key),
                                )
                              }
                            />
                            {label}
                          </label>
                        ))}
                      </div>
                    </div>
                    {form.mode === "encrypt" && (
                      <div className="notice error" role="alert">
                        <AlertIcon size={15} />
                        <span>
                          旧实验加密已停止生成。请选择受支持的处理方式后保存；原文件和已有产物不会自动改写。
                        </span>
                      </div>
                    )}
                    {form.mode === "offset" && (
                      <div className="form-grid">
                        <div className="field">
                          <label htmlFor="policy-offset">数字偏移量</label>
                          <input
                            id="policy-offset"
                            type="number"
                            step={1}
                            value={form.offset_amount}
                            onChange={(e) =>
                              set("offset_amount", Number(e.target.value))
                            }
                          />
                        </div>
                        <div className="field">
                          <label htmlFor="policy-round">
                            取整单位（1为不取整）
                          </label>
                          <input
                            id="policy-round"
                            type="number"
                            min={1}
                            step={1}
                            value={form.offset_round}
                            onChange={(e) =>
                              set("offset_round", Number(e.target.value))
                            }
                          />
                        </div>
                      </div>
                    )}
                  </div>
                </details>
                <details className="editor-group">
                  <summary>
                    <span>03</span>媒体处理与可选增强
                  </summary>
                  <div className="editor-group-body">
                    <div className="field">
                      <label htmlFor="policy-voice">音频 / 视频变声效果</label>
                      <select
                        id="policy-voice"
                        value={form.voice_change}
                        onChange={(e) => set("voice_change", e.target.value)}
                      >
                        <option value="">不变声，仅静音敏感片段</option>
                        <option value="pitch">升调（四半音）</option>
                        <option value="robot">机器声</option>
                      </select>
                      <small>
                        变声在无敏感项命中时也生效；不等于声纹匿名化。画面中的已接受区域使用遮挡。
                      </small>
                    </div>
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={form.use_model}
                        onChange={(e) => set("use_model", e.target.checked)}
                      />
                      使用本地模型增强检测
                    </label>
                    <small>
                      可选 HaS 文本 /
                      图像增强，需另外准备本地模型与依赖；未配置时会报告检查未完成。
                    </small>
                  </div>
                </details>
                <details id="policy-advanced" className="editor-group">
                  <summary>
                    <span>04</span>高级规则{" "}
                    <span className="badge">{form.custom_regex.length}</span>
                  </summary>
                  <div className="editor-group-body">
                    <div className="field">
                      <label htmlFor="policy-blocked">忽略的规则系列</label>
                      <input
                        id="policy-blocked"
                        className="mono"
                        value={form.blocked}
                        onChange={(e) => set("blocked", e.target.value)}
                        placeholder="certification, Uri"
                      />
                      <small>英文逗号分隔；仅对相关规则系列生效。</small>
                    </div>
                    <p className="card-sub">
                      自定义正则使用 Python re
                      语法，语法错误由检测阶段报告。仅有一个捕获组时，只处理该组内容。
                    </p>
                    <div className="regex-list">
                      {form.custom_regex.map((rule, index) => (
                        <div className="regex-row" key={index}>
                          <div className="grid3">
                            <div className="field">
                              <label htmlFor={`rule-name-${index}`}>
                                规则名称
                              </label>
                              <input
                                id={`rule-name-${index}`}
                                value={rule.name}
                                onChange={(e) =>
                                  set(
                                    "custom_regex",
                                    form.custom_regex.map((r, i) =>
                                      i === index
                                        ? { ...r, name: e.target.value }
                                        : r,
                                    ),
                                  )
                                }
                              />
                            </div>
                            <div className="field">
                              <label htmlFor={`rule-confidence-${index}`}>
                                置信度
                              </label>
                              <input
                                id={`rule-confidence-${index}`}
                                type="number"
                                min={0}
                                max={1}
                                step={0.05}
                                value={rule.confidence}
                                onChange={(e) =>
                                  set(
                                    "custom_regex",
                                    form.custom_regex.map((r, i) =>
                                      i === index
                                        ? {
                                            ...r,
                                            confidence: Number(e.target.value),
                                          }
                                        : r,
                                    ),
                                  )
                                }
                              />
                            </div>
                            <button
                              type="button"
                              className="btn btn-sm btn-ghost btn-danger"
                              aria-label={`移除规则 ${rule.name || index + 1}`}
                              onClick={() =>
                                set(
                                  "custom_regex",
                                  form.custom_regex.filter(
                                    (_, i) => i !== index,
                                  ),
                                )
                              }
                            >
                              <XIcon size={14} />
                            </button>
                          </div>
                          <div className="field">
                            <label htmlFor={`rule-pattern-${index}`}>
                              正则表达式
                            </label>
                            <textarea
                              id={`rule-pattern-${index}`}
                              className="mono"
                              rows={2}
                              value={rule.regex}
                              required
                              onChange={(e) =>
                                set(
                                  "custom_regex",
                                  form.custom_regex.map((r, i) =>
                                    i === index
                                      ? { ...r, regex: e.target.value }
                                      : r,
                                  ),
                                )
                              }
                              placeholder="INT-[0-9]{4}"
                            />
                          </div>
                        </div>
                      ))}
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() =>
                          set("custom_regex", [
                            ...form.custom_regex,
                            { name: "内部编号", regex: "", confidence: 0.9 },
                          ])
                        }
                      >
                        <PlusIcon size={13} />
                        添加自定义规则
                      </button>
                    </div>
                  </div>
                </details>
              </fieldset>
              <div className="editor-actions">
                <button
                  className="btn btn-primary"
                  type="submit"
                  disabled={busy}
                >
                  {busy ? (
                    <>
                      <span className="spinner" />
                      保存中…
                    </>
                  ) : (
                    <>
                      <CheckIcon size={14} />
                      保存策略
                    </>
                  )}
                </button>
                <button
                  className="btn btn-ghost"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    switchTo(() => {
                      setEditing("");
                      setError("");
                    })
                  }
                >
                  取消
                </button>
                <span>{dirty ? "未保存" : "保存后可在任务中选择"}</span>
              </div>
            </form>
          ) : (
            <div className="card policy-guide">
              <div className="card-body">
                <span className="policy-guide-icon">
                  <ShieldIcon size={26} />
                </span>
                <h2>从模板开始</h2>
                <p>复制一份内置模板，按使用场景调整检测范围与处理方式。</p>
                <div className="policy-guide-steps">
                  <div>
                    <b>1</b>
                    <span>选择模板并复制</span>
                  </div>
                  <div>
                    <b>2</b>
                    <span>调整处理方式和检测类型</span>
                  </div>
                  <div>
                    <b>3</b>
                    <span>保存后在新任务中选择</span>
                  </div>
                </div>
                <div className="notice info">
                  <KeyIcon size={15} />
                  <span>映射还原仅适用于文本代号替换；媒体处理不可逆。</span>
                </div>
                <button className="btn" onClick={() => open()}>
                  <PlusIcon size={14} />
                  从空白新建
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
      <dialog
        ref={dialog}
        className="confirmation-dialog"
        aria-labelledby="policy-confirm-title"
        onCancel={(e) => {
          e.preventDefault();
          setConfirmation(null);
        }}
      >
        {confirmation && (
          <>
            <h2 id="policy-confirm-title">{confirmation.title}</h2>
            <p>{confirmation.detail}</p>
            <div className="btn-row">
              <button
                className="btn"
                autoFocus
                onClick={() => setConfirmation(null)}
              >
                取消
              </button>
              <button
                className={`btn ${confirmation.danger ? "btn-danger" : "btn-primary"}`}
                onClick={() => {
                  const action = confirmation.run;
                  setConfirmation(null);
                  action();
                }}
              >
                {confirmation.actionLabel}
              </button>
            </div>
          </>
        )}
      </dialog>
    </>
  );
}
