"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { health } from "@/lib/api";
import { ShieldIcon } from "./icons";

export function SiteHeader() {
  const path = usePathname();
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    health().then(() => alive && setOnline(true)).catch(() => alive && setOnline(false));
    return () => { alive = false; };
  }, []);

  return (
    <header className="site-header">
      <div className="header-inner">
        <Link href="/" className="brand">
          <span className="brand-mark"><ShieldIcon size={20} /></span>
          <span className="brand-name">Omni<span>Mask</span></span>
          <span className="brand-tag">code secrets</span>
        </Link>
        <nav className="nav">
          <Link href="/" className={path === "/" ? "nav-link active" : "nav-link"}>工作台</Link>
          <Link href="/policies" className={path.startsWith("/policies") ? "nav-link active" : "nav-link"}>策略配置</Link>
        </nav>
        <div className={`backend-dot ${online === null ? "wait" : online ? "on" : "off"}`}>
          <span className="dot" />
          {online === null ? "连接中" : online ? "服务在线" : "后端未连接"}
        </div>
      </div>
    </header>
  );
}
