"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { health } from "@/lib/api";
import { ShieldIcon } from "./icons";

export function SiteHeader() {
  const path = usePathname();
  const [online, setOnline] = useState<boolean | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    let alive = true;
    const check = () => {
      if (document.visibilityState === "hidden") return;
      health()
        .then(() => alive && setOnline(true))
        .catch(() => alive && setOnline(false));
    };
    check();
    const timer = window.setInterval(check, 30000);
    document.addEventListener("visibilitychange", check);
    return () => {
      alive = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

  const retry = async () => {
    setChecking(true);
    try {
      await health();
      setOnline(true);
    } catch {
      setOnline(false);
    } finally {
      setChecking(false);
    }
  };
  const atWorkbench = path === "/" || path.startsWith("/tasks/");

  return (
    <header className="site-header">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <div className="header-inner">
        <Link href="/" className="brand">
          <span className="brand-mark">
            <ShieldIcon size={20} />
          </span>
          <span className="brand-name">
            Omni<span>Mask</span>
          </span>
          <span className="brand-tag">本地隐私工作台</span>
        </Link>
        <nav className="nav" aria-label="主要导航">
          <Link
            href="/"
            aria-current={atWorkbench ? "page" : undefined}
            className={atWorkbench ? "nav-link active" : "nav-link"}
          >
            工作台
          </Link>
          <Link
            href="/policies"
            aria-current={path.startsWith("/policies") ? "page" : undefined}
            className={
              path.startsWith("/policies") ? "nav-link active" : "nav-link"
            }
          >
            策略配置
          </Link>
        </nav>
        <div
          className={`backend-dot ${online === null ? "wait" : online ? "on" : "off"}`}
        >
          <span className="dot" />
          {online === null
            ? "连接中"
            : online
              ? "本地服务在线"
              : "本地服务未连接"}
          {online === false && (
            <button
              type="button"
              className="connection-retry"
              onClick={retry}
              disabled={checking}
            >
              {checking ? "检查中…" : "重试"}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
