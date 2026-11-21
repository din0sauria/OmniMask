import type { Metadata } from "next";
import "./globals.css";
import { SiteHeader } from "@/components/nav";

export const metadata: Metadata = {
  title: "OmniMask · 多模态隐私脱敏工作台",
  description:
    "检测本地代码、文本、图像和音视频中的敏感信息，人工审核后脱敏导出。",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="bg-grid" />
        <SiteHeader />
        <main className="page" id="main-content" tabIndex={-1}>
          {children}
        </main>
        <footer className="site-footer">
          <span>
            OmniMask <span className="footer-dot">·</span> 本地处理，审核后导出
          </span>
          <span className="mono">
            LOCAL WORKSPACE <span className="footer-dot">/</span> v0.2
          </span>
        </footer>
      </body>
    </html>
  );
}
