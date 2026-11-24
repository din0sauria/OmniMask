"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getStorageUsage,
  maintainTasks,
  type MaintenanceOperation,
  type MaintenanceResponse,
  type StoredTask,
  type StorageUsage,
} from "@/lib/maintenance-api";
import { AlertIcon, CheckIcon, FolderIcon } from "./icons";
import { Empty, formatBytes, formatTime, StatusPill } from "./ui";

type Confirmation = {
  operation: MaintenanceOperation;
  taskIds: string[];
  names: string[];
};
const LABELS: Record<MaintenanceOperation, string> = {
  trash: "移入回收站",
  restore: "恢复任务",
  purge: "永久删除副本",
  "clean-cache": "清理过期缓存",
};

export default function TaskStoragePanel({
  onChanged,
}: {
  onChanged: () => Promise<void>;
}) {
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [trashSelected, setTrashSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState<MaintenanceOperation | "">("");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [irreversible, setIrreversible] = useState(false);
  const [result, setResult] = useState<{
    operation: MaintenanceOperation;
    response: MaintenanceResponse;
    names: Record<string, string>;
  } | null>(null);
  const inFlight = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const feedback = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getStorageUsage();
      if (!alive.current) return;
      setUsage(data);
      setSelected((ids) =>
        ids.filter((id) => data.tasks.some((t) => t.id === id && !t.busy)),
      );
      setTrashSelected((ids) =>
        ids.filter((id) => data.trash.some((t) => t.id === id && !t.busy)),
      );
    } catch (err) {
      if (alive.current) setError((err as Error).message);
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (confirmation && !dialog.current?.open) dialog.current?.showModal();
    else if (!confirmation && dialog.current?.open) dialog.current.close();
  }, [confirmation]);

  function selectTask(id: string, checked: boolean, trash = false) {
    const setter = trash ? setTrashSelected : setSelected;
    setter((ids) =>
      checked ? [...new Set([...ids, id])] : ids.filter((x) => x !== id),
    );
  }

  function ask(operation: MaintenanceOperation, taskIds: string[]) {
    if (!taskIds.length || busy || loading) return;
    const rows =
      operation === "restore" || operation === "purge"
        ? usage?.trash
        : usage?.tasks;
    const available = taskIds.filter((id) =>
      rows?.some((t) => t.id === id && !t.busy),
    );
    if (!available.length) {
      setError("所选任务正在处理中，暂不能操作；请稍后再试。");
      window.requestAnimationFrame(() => feedback.current?.focus());
      return;
    }
    setIrreversible(false);
    setConfirmation({
      operation,
      taskIds: available,
      names: available.map((id) => rows?.find((t) => t.id === id)?.name || id),
    });
  }

  async function execute() {
    if (
      !confirmation ||
      inFlight.current ||
      (confirmation.operation === "purge" && !irreversible)
    )
      return;
    const request = confirmation;
    const names = Object.fromEntries(
      request.taskIds.map((id, i) => [id, request.names[i]]),
    );
    inFlight.current = true;
    setBusy(request.operation);
    setError("");
    setResult(null);
    try {
      const response = await maintainTasks(
        request.operation,
        request.taskIds,
        request.operation === "purge" && irreversible,
      );
      if (!alive.current) return;
      setResult({ operation: request.operation, response, names });
      setConfirmation(null);
      await reload();
      try {
        await onChanged();
      } catch {
        setError("存储操作已返回，但任务列表刷新失败，请刷新工作台核对。");
      }
    } catch (err) {
      if (alive.current) {
        setError(
          `${(err as Error).message}；请刷新核对实际状态，避免重复操作。`,
        );
        setConfirmation(null);
        await reload();
      }
    } finally {
      inFlight.current = false;
      if (alive.current) {
        setBusy("");
        window.requestAnimationFrame(() => feedback.current?.focus());
      }
    }
  }

  function rows(items: StoredTask[], trash = false) {
    const selection = trash ? trashSelected : selected;
    return (
      <div className="storage-list">
        {items.map((task) => (
          <div className="storage-row" key={task.id}>
            <label className="storage-row-main">
              <input
                type="checkbox"
                checked={selection.includes(task.id)}
                disabled={!!busy || loading || task.busy}
                onChange={(event) =>
                  selectTask(task.id, event.target.checked, trash)
                }
                aria-label={`${trash ? "选择回收任务" : "选择任务"} ${task.name}`}
              />
              <span>
                <strong>{task.name}</strong>
                <small className="mono">
                  {task.id}
                  {trash && task.deleted_at
                    ? ` · ${formatTime(task.deleted_at)}`
                    : ""}
                </small>
              </span>
            </label>
            <div className="storage-row-info">
              <span>{formatBytes(task.bytes)}</span>
              {task.busy ? (
                <span className="badge amber">正在处理</span>
              ) : trash ? (
                <span className="badge">可恢复</span>
              ) : (
                <StatusPill status={task.status} />
              )}
              {!trash && task.stale_cache_bytes > 0 && (
                <small>过期缓存 {formatBytes(task.stale_cache_bytes)}</small>
              )}
            </div>
          </div>
        ))}
      </div>
    );
  }

  const activeIdle = usage?.tasks.filter((t) => !t.busy).map((t) => t.id) || [];
  const trashIdle = usage?.trash.filter((t) => !t.busy).map((t) => t.id) || [];
  const allActive =
    activeIdle.length > 0 && activeIdle.every((id) => selected.includes(id));
  const allTrash =
    trashIdle.length > 0 && trashIdle.every((id) => trashSelected.includes(id));

  return (
    <section className="card task-storage">
      <details
        onToggle={(event) => {
          if (
            event.currentTarget.open &&
            !usage &&
            !loading &&
            !inFlight.current
          )
            void reload();
        }}
      >
        <summary className="storage-summary">
          <FolderIcon size={18} />
          <span>
            <b>本地任务与存储</b>
            <small>管理任务副本、过期缓存和回收站，源文件不受影响</small>
          </span>
          {usage && (
            <span className="badge">
              {formatBytes(usage.totals.tasks_bytes + usage.totals.trash_bytes)}
            </span>
          )}
        </summary>
        <div className="storage-body">
          <div className="storage-toolbar">
            <p>
              容量为逻辑文件大小；移入回收站仍占空间，清理操作只针对本机任务副本。
            </p>
            <button
              type="button"
              className="btn btn-sm"
              disabled={!!busy || loading}
              onClick={() => {
                setError("");
                void reload();
              }}
            >
              {loading ? "读取中…" : "刷新容量"}
            </button>
          </div>
          <div className={error || result ? "storage-feedback" : ""} ref={feedback} tabIndex={-1}>
            {error && (
              <div className="notice error" role="alert">
                <AlertIcon size={16} />
                <span>{error}</span>
              </div>
            )}
            {result && (
              <div
                className="storage-result-list"
                role="status"
                aria-live="polite"
              >
                <b>{LABELS[result.operation]}结果</b>
                {result.response.results.map((item) => (
                  <div
                    className={`notice ${!item.ok ? "error" : item.partial ? "" : "success"}`}
                    key={item.id}
                  >
                    {item.ok && !item.partial ? (
                      <CheckIcon size={15} />
                    ) : (
                      <AlertIcon size={15} />
                    )}
                    <span>
                      <b>{result.names[item.id] || item.id}</b>：
                      {item.ok
                        ? item.partial
                          ? "部分完成"
                          : "已完成"
                        : "未完成"}
                      {item.bytes != null && (
                        <> · {formatBytes(item.bytes)}逻辑文件</>
                      )}
                      {item.error && <> · {item.error}</>}
                      {item.warnings?.map((warning, i) => (
                        <small key={i}>{warning}</small>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {usage && (
            <>
              <div className="storage-metrics">
                {[
                  ["任务副本", usage.totals.tasks_bytes],
                  ["过期缓存", usage.totals.stale_cache_bytes],
                  ["回收站", usage.totals.trash_bytes],
                ].map(([label, bytes]) => (
                  <div className="storage-metric" key={String(label)}>
                    <span>{label}</span>
                    <b>{formatBytes(Number(bytes))}</b>
                  </div>
                ))}
              </div>
              {!!usage.warnings.length && (
                <div className="notice storage-warning" role="status">
                  <AlertIcon size={16} />
                  <span>
                    部分目录无法核验：
                    {usage.warnings
                      .map((w) => `${w.id}：${w.error}`)
                      .join("；")}
                  </span>
                </div>
              )}
              <section>
                <div className="storage-section-head">
                  <h3>
                    活动任务 <span className="badge">{usage.tasks.length}</span>
                  </h3>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    disabled={!!busy || loading || !activeIdle.length}
                    onClick={() => setSelected(allActive ? [] : activeIdle)}
                  >
                    {allActive ? "清空选择" : "选择全部空闲任务"}
                  </button>
                </div>
                {usage.tasks.length ? (
                  rows(usage.tasks)
                ) : (
                  <Empty
                    title="没有活动任务副本"
                    hint="移入回收站的任务可以在下方恢复"
                  />
                )}
                <div className="storage-toolbar">
                  <span>已选 {selected.length} 个</span>
                  <div className="btn-row">
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={!!busy || loading || !selected.length}
                      onClick={() => ask("clean-cache", selected)}
                    >
                      清理过期缓存
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={!!busy || loading || !selected.length}
                      onClick={() => ask("trash", selected)}
                    >
                      移入回收站
                    </button>
                  </div>
                </div>
              </section>
              <details className="storage-trash">
                <summary>
                  回收站 <span className="badge">{usage.trash.length}</span>
                </summary>
                <div className="storage-section-head">
                  <p>恢复会保留原审核及处理记录。永久删除不能从本站撤销。</p>
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    disabled={!!busy || loading || !trashIdle.length}
                    onClick={() => setTrashSelected(allTrash ? [] : trashIdle)}
                  >
                    {allTrash ? "清空选择" : "选择全部回收任务"}
                  </button>
                </div>
                {usage.trash.length ? (
                  rows(usage.trash, true)
                ) : (
                  <Empty title="回收站为空" />
                )}
                <div className="storage-toolbar">
                  <span>已选 {trashSelected.length} 个</span>
                  <div className="btn-row">
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={!!busy || loading || !trashSelected.length}
                      onClick={() => ask("restore", trashSelected)}
                    >
                      恢复任务
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      disabled={!!busy || loading || !trashSelected.length}
                      onClick={() => ask("purge", trashSelected)}
                    >
                      永久删除副本
                    </button>
                  </div>
                </div>
              </details>
            </>
          )}
          {loading && !usage && <Empty title="正在读取任务容量…" />}
          <p className="storage-warning">
            只清理旧修订与过期源指纹缓存，保留当前预览与当前脱敏产物。被其他任务作为源文件使用的副本会保留并提示。
          </p>
        </div>
      </details>
      <dialog
        ref={dialog}
        className="confirmation-dialog storage-confirm"
        aria-labelledby="storage-confirm-title"
        onCancel={(event) => {
          event.preventDefault();
          if (!busy) setConfirmation(null);
        }}
      >
        {confirmation && (
          <>
            <h2 id="storage-confirm-title">
              {LABELS[confirmation.operation]} {confirmation.taskIds.length}{" "}
              个任务？
            </h2>
            <p>
              {confirmation.operation === "trash"
                ? "任务将从工作台移入回收站，可在此面板恢复。源文件不会被移动或删除。"
                : confirmation.operation === "restore"
                  ? "恢复任务及其审核、预览和脱敏副本；原编号冲突时会拒绝覆盖。"
                  : confirmation.operation === "clean-cache"
                    ? "仅移除过期缓存。当前预览和脱敏产物、检测结果及源文件都会保留。"
                    : "将永久删除所选回收任务的元数据、检测明文、转写、原始预览及脱敏副本。源文件不会被删除。"}
            </p>
            <ul>
              {confirmation.names.map((name, i) => (
                <li key={confirmation.taskIds[i]}>{name}</li>
              ))}
            </ul>
            {confirmation.operation === "purge" && (
              <label className="switch storage-purge-confirm">
                <input
                  type="checkbox"
                  checked={irreversible}
                  disabled={!!busy}
                  onChange={(event) => setIrreversible(event.target.checked)}
                />
                我确认永久删除任务副本，且无法从本站恢复
              </label>
            )}
            <div className="btn-row">
              <button
                type="button"
                className="btn"
                autoFocus
                disabled={!!busy}
                onClick={() => setConfirmation(null)}
              >
                取消
              </button>
              <button
                type="button"
                className={`btn ${confirmation.operation === "purge" ? "btn-danger" : "btn-primary"}`}
                disabled={
                  !!busy ||
                  (confirmation.operation === "purge" && !irreversible)
                }
                onClick={() => void execute()}
              >
                {busy ? (
                  <>
                    <span className="spinner" />
                    处理中…
                  </>
                ) : (
                  LABELS[confirmation.operation]
                )}
              </button>
            </div>
          </>
        )}
      </dialog>
    </section>
  );
}
