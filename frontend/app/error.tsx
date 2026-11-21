"use client";

import Link from "next/link";
import { AlertIcon } from "@/components/icons";

export default function ErrorPage({
  retry,
}: {
  error: unknown;
  retry: () => void;
}) {
  return (
    <section className="card route-error" role="alert">
      <span className="error-mark">
        <AlertIcon size={26} />
      </span>
      <h1>页面暂时无法显示</h1>
      <p className="sub">请重试加载，或返回工作台重新打开任务。</p>
      <div className="btn-row">
        <button type="button" className="btn btn-primary" onClick={retry}>
          重新加载
        </button>
        <Link className="btn" href="/">
          返回工作台
        </Link>
      </div>
    </section>
  );
}
