export default function Loading() {
  return (
    <div
      className="route-loading"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <span className="spinner" />
      <span>正在打开工作区…</span>
    </div>
  );
}
