export function Spinner({ label = "Cargando…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-8 text-[15px] text-ink-2">
      <span className="size-4 animate-spin rounded-full border-2 border-line border-t-brand" />
      {label}
    </div>
  );
}
