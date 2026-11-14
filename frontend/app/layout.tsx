import type { Metadata } from "next";
import "./globals.css";
import { SiteHeader } from "@/components/nav";

export const metadata: Metadata = {
  title: "OmniMask · 代码密钥脱敏工作台",
  description: "填入本地代码路径，检测密钥与敏感串、审核后脱敏导出。",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="bg-grid" />
        <SiteHeader />
        <main className="page">{children}</main>
        <footer className="site-footer">
          <span>OmniMask · 本地运行 · 数据不出机器</span>
          <span className="mono">code detection only · v0.1</span>
        </footer>
      </body>
    </html>
  );
}
