"use client";

import { useCallback, useEffect, useState } from "react";
import { browseFs } from "@/lib/api";
import type { FsData } from "@/lib/types";
import { Empty, formatBytes } from "./ui";
import { AlertIcon, ChevronIcon, FileIcon, FolderIcon, XIcon } from "./icons";

type Props = {
  initial?: string;
  onPick: (path: string) => void;
  onClose: () => void;
};

export function PathPicker({ initial = "", onPick, onClose }: Props) {
  const [data, setData] = useState<FsData | null>(null);
  const [manual, setManual] = useState(initial);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (p: string) => {
    setLoading(true);
    setError("");
    try {
      const d = await browseFs(p);
      setData(d);
      setManual(d.path);
      setSelected(d.selected || "");
      setFilter("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(initial); }, [initial, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  const current = data?.path || "";
  const entries = (data?.entries || []).filter(
    (e) => !filter || e.name.toLowerCase().includes(filter.toLowerCase()),
  );
  const activeRoot = data?.roots.find((r) => current.toLowerCase().startsWith(r.path.toLowerCase()));

  return (
    <div className="modal-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="path-modal" role="dialog" aria-modal="true" aria-label="选择本地文件或目录">
        <div className="path-modal-head">
          <div>
            <div className="card-title"><FolderIcon size={16} />选择本地文件或目录</div>
            <div className="card-sub">只读浏览；单击文件夹进入，单击文件选中</div>
          </div>
          <div className="btn-row">
            <span className="badge">read only</span>
            <button className="btn btn-sm btn-ghost" onClick={onClose} aria-label="关闭"><XIcon size={14} /></button>
          </div>
        </div>

        <div className="path-roots">
          {data?.home && (
            <button className="root-btn" onClick={() => load(data.home)} title={data.home}>主目录</button>
          )}
          {(data?.roots || []).map((r) => (
            <button
              key={r.path}
              className={`root-btn ${activeRoot?.path === r.path ? "active" : ""}`}
              onClick={() => load(r.path)}
              title={r.path}
            >
              {r.name}
            </button>
          ))}
          <span className="path-roots-hint">仅浏览，不会修改任何文件</span>
        </div>

        <div className="path-bar">
          <button className="btn btn-sm" onClick={() => data?.parent && load(data.parent)} disabled={!data?.parent}>上一级</button>
          <input
            className="mono"
            value={manual}
            onChange={(e) => setManual(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") load(manual); }}
            placeholder="输入路径后回车"
            spellCheck={false}
          />
          <button className="btn btn-sm" onClick={() => load(manual)} disabled={!manual.trim()}>前往</button>
        </div>

        <div className="path-filter">
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="筛选当前目录…" />
          {data?.truncated && <span className="badge amber">仅展示前 2000 项</span>}
        </div>

        <div className="path-list">
          {loading ? (
            <Empty title="正在读取目录…" />
          ) : error ? (
            <div className="notice error"><AlertIcon />{error}</div>
          ) : entries.length === 0 ? (
            <Empty title={filter ? "没有匹配项" : "空目录"} />
          ) : entries.map((e) => (
            <button
              key={e.path}
              className={`path-entry ${!e.is_dir && selected === e.path ? "selected" : ""}`}
              onClick={() => (e.is_dir ? load(e.path) : setSelected(e.path))}
              onDoubleClick={() => { if (!e.is_dir) { setSelected(e.path); onPick(e.path); } }}
              title={e.path}
            >
              {e.is_dir ? <FolderIcon size={16} /> : <FileIcon size={15} />}
              <span className="name">{e.name}</span>
              {!e.is_dir && <span className="size mono">{formatBytes(e.size)}</span>}
              {e.is_dir && <ChevronIcon size={13} className="chev" />}
            </button>
          ))}
        </div>

        <div className="path-foot">
          <div className="path-current">
            <span>当前</span>
            <b className="mono">{current || "-"}</b>
            {selected && <span className="selected-file" title={selected}>已选文件 <b className="mono">{selected}</b></span>}
          </div>
          <div className="btn-row">
            <button className="btn btn-sm" onClick={() => selected && onPick(selected)} disabled={!selected}>
              <FileIcon size={13} />选择此文件
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => current && onPick(current)} disabled={!current}>
              <FolderIcon size={13} />选择此目录
            </button>
            <button className="btn btn-sm btn-ghost" onClick={onClose}>取消</button>
          </div>
        </div>
      </div>
    </div>
  );
}
