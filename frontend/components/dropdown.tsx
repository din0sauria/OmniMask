"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CheckIcon, ChevronIcon } from "./icons";

export type DropdownOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

type Props = {
  value: string;
  onChange: (value: string) => void;
  options: DropdownOption[];
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
  id?: string;
};

export function Dropdown({
  value,
  onChange,
  options,
  ariaLabel,
  disabled,
  className,
  id,
}: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const selected = useMemo(
    () => options.find((o) => o.value === value)?.label ?? value,
    [options, value],
  );
  const enabledValues = useMemo(
    () => options.filter((o) => !o.disabled).map((o) => o.value),
    [options],
  );

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        return;
      }
      if (!enabledValues.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const index = enabledValues.indexOf(value);
        const next =
          e.key === "ArrowDown"
            ? enabledValues[(index + 1 + enabledValues.length) % enabledValues.length]
            : enabledValues[(index - 1 + enabledValues.length) % enabledValues.length];
        onChange(next);
      } else if (e.key === "Enter" && value) {
        setOpen(false);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, enabledValues, value, onChange]);

  return (
    <div
      ref={root}
      className={`dropdown ${open ? "open" : ""} ${disabled ? "disabled" : ""} ${className || ""}`}
    >
      <button
        type="button"
        id={id}
        className="dropdown-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="dropdown-value">{selected}</span>
        <ChevronIcon size={14} className="dropdown-chev" />
      </button>
      {open && (
        <div className="dropdown-menu" role="listbox" aria-label={ariaLabel}>
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              disabled={option.disabled}
              className={`dropdown-item ${option.value === value ? "selected" : ""}`}
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              {option.value === value && <CheckIcon size={14} className="check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
