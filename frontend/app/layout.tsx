import type { Metadata } from "next";
import "./globals.css";
import { SiteHeader } from "@/components/nav";

export const metadata: Metadata = {
  title: "面向多模态数据的隐私脱敏系统",
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
          <span>面向多模态数据的隐私脱敏系统</span>
          <span className="mono">
            本地处理，审核后导出 <span className="footer-dot">/</span> v1.0
          </span>
        </footer>
      </body>
    </html>
  );
}
