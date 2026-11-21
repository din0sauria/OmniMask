"use client";
import { useEffect, useState } from "react";
import type { ReviewGroup } from "@/lib/review-groups";

export function ReviewTimeline({
  groups,
  duration,
  disabled,
  activeId,
  onSeek,
  onPick,
}: {
  groups: ReviewGroup[];
  duration: number;
  disabled: boolean;
  activeId: string;
  onSeek: (time: number) => void;
  onPick: (group: ReviewGroup) => void;
}) {
  const [time, setTime] = useState(0);
  useEffect(() => {
    setTime(0);
  }, [duration]);
  const timed = groups.filter((g) => g.start != null && g.end != null);
  if (!duration || !timed.length) return null;
  const lanes: { track: string; groups: ReviewGroup[] }[] = [];
  for (const group of timed) {
    let lane = lanes.find(
      (l) =>
        l.track === group.track &&
        (l.groups[l.groups.length - 1]?.end ?? 0) <= (group.start ?? 0),
    );
    if (!lane) {
      lane = { track: group.track, groups: [] };
      lanes.push(lane);
    }
    lane.groups.push(group);
  }
  return (
    <section className="review-timeline" aria-label="敏感片段时间轴">
      <div className="timeline-head">
        <b>敏感片段时间轴</b>
        <span>
          定位 {time.toFixed(2)} / {duration.toFixed(2)} 秒
        </span>
      </div>
      <label className="timeline-scrubber">
        定位时间
        <input
          aria-label="时间轴定位秒数"
          type="range"
          min={0}
          max={duration}
          step={0.01}
          value={Math.min(time, duration)}
          disabled={disabled}
          onChange={(e) => {
            const value = Number(e.target.value);
            setTime(value);
            onSeek(value);
          }}
        />
      </label>
      <div className="timeline-lanes">
        {lanes.map((lane, index) => (
          <div className="timeline-track" key={`${lane.track}-${index}`}>
            <span>{lane.track}</span>
            <div className="timeline-track-bar">
              {lane.groups.map((group) => (
                <button
                  type="button"
                  key={group.id}
                  className={`timeline-segment ${group.status} ${group.members.some((f) => f.id === activeId) ? "active" : ""}`}
                  style={{
                    left: `${Math.min(100, ((group.start ?? 0) / duration) * 100)}%`,
                    width: `${Math.max(0, Math.min(100 - ((group.start ?? 0) / duration) * 100, Math.max(0.6, (((group.end ?? 0) - (group.start ?? 0)) / duration) * 100)))}%`,
                  }}
                  disabled={disabled}
                  aria-label={`定位${group.label}，${group.start!.toFixed(2)}至${group.end!.toFixed(2)}秒，${group.members.length}条原始结果`}
                  title={`${group.label} · ${group.start!.toFixed(2)}–${group.end!.toFixed(2)}s · ${group.members.length}条`}
                  onClick={() => {
                    setTime(group.start ?? 0);
                    onPick(group);
                  }}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="quality-note">
        条带是成员时间跨度，间隙不会自动补遮挡；分组基于时间与框重叠，不代表身份确认。原始定位保持。
      </p>
    </section>
  );
}
