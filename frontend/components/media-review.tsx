"use client";
import { useEffect, useRef, useState } from "react";
import type { Finding, ManualFinding, ReviewEdit, TaskFile } from "@/lib/types";

export type SeekRequest = { token: number; start: number; end?: number };
export function MediaPlayer({
  src,
  video = false,
  findings = [],
  activeId,
  onPick,
  seek,
  showBoxes = false,
}: {
  src: string;
  video?: boolean;
  findings?: Finding[];
  activeId?: string;
  onPick?: (id: string) => void;
  seek?: SeekRequest;
  showBoxes?: boolean;
}) {
  const ref = useRef<HTMLMediaElement | null>(null);
  const stop = useRef<number | undefined>(undefined);
  const [time, setTime] = useState(0);
  const [dims, setDims] = useState({ w: 0, h: 0 });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const applySeek = () => {
    if (!ref.current || !seek || ref.current.readyState === 0) return;
    ref.current.currentTime = seek.start;
    stop.current = seek.end;
    if (seek.end != null)
      void ref.current.play().catch(() => setError("请点击播放按钮试听该片段"));
  };
  useEffect(() => {
    applySeek();
  }, [seek]); // Event request is independent of playback time.
  useEffect(() => {
    setError("");
    setLoading(true);
    setTime(0);
    setDims({ w: 0, h: 0 });
    stop.current = undefined;
  }, [src]);
  useEffect(() => {
    let frame: number;
    const update = () => {
      if (ref.current && !ref.current.paused) tick();
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, []);
  function tick() {
    const el = ref.current;
    if (!el) return;
    setTime(el.currentTime);
    if (stop.current != null && el.currentTime >= stop.current) {
      el.pause();
      stop.current = undefined;
    }
  }
  const boxes = showBoxes
    ? findings.filter(
        (f) =>
          f.bbox &&
          (f.start != null && f.end != null
            ? time >= f.start && time < f.end
            : f.frame_ts != null &&
              time >= f.frame_ts &&
              time < f.frame_ts + (f.frame_duration || 0.05)),
      )
    : [];
  return (
    <div
      className={video ? "media-pane" : "media-pane audio-pane"}
      aria-busy={loading}
    >
      {loading && (
        <div className="media-loading" role="status">
          <span className="spinner" />
          正在加载{video ? "视频" : "音频"}…
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {video ? (
        <div className="video-box">
          <video
            ref={(el) => {
              ref.current = el;
            }}
            controls
            preload="metadata"
            src={src}
            onTimeUpdate={tick}
            onSeeked={tick}
            onCanPlay={() => setLoading(false)}
            onPlay={() => setError("")}
            onLoadedMetadata={(e) => {
              setLoading(false);
              setDims({
                w: e.currentTarget.videoWidth,
                h: e.currentTarget.videoHeight,
              });
              applySeek();
            }}
            onError={() => {
              setLoading(false);
              setError(
                "视频加载失败，请重试预览；若源文件已变化，请重新检测。",
              );
            }}
          />
          {dims.w > 0 &&
            boxes.map((f) => (
              <button
                key={f.id}
                type="button"
                aria-label={`定位${f.type_label}`}
                className={`bbox ${activeId === f.id ? "active" : ""} ${f.status === "rejected" ? "rejected" : ""}`}
                style={{
                  left: `${(f.bbox![0] / dims.w) * 100}%`,
                  top: `${(f.bbox![1] / dims.h) * 100}%`,
                  width: `${(f.bbox![2] / dims.w) * 100}%`,
                  height: `${(f.bbox![3] / dims.h) * 100}%`,
                }}
                onClick={() => onPick?.(f.id)}
              />
            ))}
        </div>
      ) : (
        <audio
          ref={(el) => {
            ref.current = el;
          }}
          controls
          preload="metadata"
          src={src}
          onTimeUpdate={tick}
          onLoadedMetadata={() => {
            setLoading(false);
            applySeek();
          }}
          onCanPlay={() => setLoading(false)}
          onPlay={() => setError("")}
          onError={() => {
            setLoading(false);
            setError(
              "音频加载失败或试听已取消，请重新点击试听；若源文件已变化，请重新检测。",
            );
          }}
        />
      )}
    </div>
  );
}

export function MediaEditor({
  file,
  media,
  finding,
  disabled,
  onSave,
  onAudition,
}: {
  file: TaskFile;
  media: "audio" | "video" | "image";
  finding?: Finding;
  disabled: boolean;
  onSave: (edit: ReviewEdit | ManualFinding) => Promise<void>;
  onAudition?: (start: number, end: number, track?: number) => void;
}) {
  const tracks = file.detection?.metadata?.audio_tracks;
  const [kind, setKind] = useState(
    finding?.bbox || media === "image" || (media === "video" && !tracks?.length)
      ? "region"
      : "audio",
  );
  const [start, setStart] = useState(
    String(finding?.start ?? finding?.frame_ts ?? 0),
  );
  const [end, setEnd] = useState(
    String(
      finding?.end ??
        Math.min(
          file.detection?.metadata?.duration || Infinity,
          (finding?.frame_ts ?? 0) + (finding?.frame_duration ?? 1),
        ),
    ),
  );
  const [box, setBox] = useState(
    (
      finding?.bbox ?? [
        0,
        0,
        Math.min(100, file.detection?.metadata?.width || 100),
        Math.min(100, file.detection?.metadata?.height || 100),
      ]
    ).map(String),
  );
  const [track, setTrack] = useState(String(finding?.track_index ?? 0));
  const [error, setError] = useState("");
  const duration = file.detection?.metadata?.duration;
  function values(): ReviewEdit | ManualFinding {
    const s = Number(start),
      e = Number(end),
      t = Number(track);
    if (
      media !== "image" &&
      (!Number.isFinite(s) ||
        !Number.isFinite(e) ||
        s < 0 ||
        e <= s ||
        (duration != null && e > duration + 0.001))
    )
      throw Error("时间须满足 0 ≤ 开始 < 结束，且不超过文件时长");
    if (!Number.isInteger(t) || t < 0)
      throw Error("音轨编号须为非负整数（从 0 开始）");
    if (kind === "audio" && tracks && t >= tracks.length)
      throw Error("此音轨不存在，请按提示选择有效音轨编号");
    const b = box.map(Number) as [number, number, number, number];
    const meta = file.detection?.metadata;
    if (
      kind === "region" &&
      (!b.every(Number.isFinite) ||
        b[0] < 0 ||
        b[1] < 0 ||
        b[2] <= 0 ||
        b[3] <= 0 ||
        (meta?.width != null && b[0] + b[2] > meta.width) ||
        (meta?.height != null && b[1] + b[3] > meta.height))
    )
      throw Error("区域使用原始像素坐标，宽高须大于 0，且不得超出画面");
    const location = {
      ...(media !== "image" ? { start: s, end: e } : {}),
      ...(kind === "region" ? { bbox: b } : { track_index: t }),
    };
    return finding
      ? { id: finding.id, ...location }
      : { file_id: file.id, media, ...location };
  }
  return (
    <form
      className="media-editor"
      onClick={(e) => e.stopPropagation()}
      onSubmit={async (e) => {
        e.preventDefault();
        setError("");
        try {
          await onSave(values());
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <b>{finding ? "调整处理范围" : "手动补充"}</b>
      <small>
        {duration != null
          ? `时长 ${duration.toFixed(3)} 秒`
          : "时长以播放器为准"}
        {file.detection?.metadata?.width
          ? ` · ${file.detection.metadata.width}×${file.detection.metadata.height} 像素`
          : ""}
        {file.detection?.metadata?.audio_tracks?.length
          ? ` · ${file.detection.metadata.audio_tracks.length} 条音轨`
          : ""}
      </small>
      {!finding && media === "video" && (
        <label>
          处理对象
          <select
            disabled={disabled}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="audio" disabled={!tracks?.length}>
              音轨静音{!tracks?.length ? "（无音轨）" : ""}
            </option>
            <option value="region">画面区域遮挡</option>
          </select>
        </label>
      )}
      {media !== "image" && (
        <div className="media-fields">
          <label>
            开始（秒）
            <input
              aria-label="开始秒数"
              type="number"
              min="0"
              max={duration}
              step="0.001"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              required
              disabled={disabled}
            />
          </label>
          <label>
            结束（秒）
            <input
              aria-label="结束秒数"
              type="number"
              min="0"
              max={duration}
              step="0.001"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              required
              disabled={disabled}
            />
          </label>
        </div>
      )}
      {kind === "region" ? (
        <div className="media-fields">
          {["X", "Y", "宽", "高"].map((label, i) => (
            <label key={label}>
              {label}（像素）
              <input
                type="number"
                min={i < 2 ? 0 : 1}
                step="1"
                required
                disabled={disabled}
                value={box[i]}
                onChange={(e) =>
                  setBox((old) =>
                    old.map((v, j) => (j === i ? e.target.value : v)),
                  )
                }
              />
            </label>
          ))}
        </div>
      ) : (
        <label>
          音轨编号
          {tracks?.length ? `（0–${tracks.length - 1}）` : "（从0开始）"}
          <input
            type="number"
            min="0"
            max={tracks?.length ? tracks.length - 1 : undefined}
            step="1"
            required
            disabled={disabled}
            value={track}
            onChange={(e) => setTrack(e.target.value)}
          />
        </label>
      )}
      {finding?.timing_precision === "segment" && (
        <small>整段定位：ASR 未提供可用的词级时间，可能影响整句。</small>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <div className="finding-actions">
        <button className="btn btn-sm" type="submit" disabled={disabled}>
          {finding ? "保存调整并重新审核" : "添加待审核结果"}
        </button>
        {kind === "audio" && onAudition && (
          <button
            className="btn btn-sm"
            type="button"
            disabled={disabled}
            onClick={() => {
              try {
                values();
                setError("");
                onAudition(Number(start), Number(end), Number(track));
              } catch (err) {
                setError((err as Error).message);
              }
            }}
          >
            试听此片段
          </button>
        )}
      </div>
    </form>
  );
}
