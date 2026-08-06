export function LoadingBlock({ label = "加载中…" }: { label?: string }) {
  return (
    <div className="loading-block" role="status" aria-live="polite">
      <span className="loading-dot" aria-hidden />
      <span>{label}</span>
    </div>
  );
}
