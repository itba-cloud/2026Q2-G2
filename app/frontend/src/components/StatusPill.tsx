import { Badge } from "./ui";

/* Job and upload states, in Spanish and with an icon. Status never rides on
 * colour alone -- the glyph and the word both carry it, which is what makes
 * the row readable in greyscale and to a colour-blind reader. */
const STATES: Record<string, { label: string; tone: "neutral" | "good" | "bad" | "warn" | "info"; icon: string }> = {
  pending: { label: "en cola", tone: "neutral", icon: "•" },
  processing: { label: "procesando", tone: "info", icon: "◐" },
  done: { label: "listo", tone: "good", icon: "✓" },
  failed: { label: "falló", tone: "bad", icon: "✕" },
  // An upload's own: no worker has taken its file yet, then one did.
  awaiting_file: { label: "pendiente", tone: "neutral", icon: "•" },
  queued: { label: "recibido", tone: "info", icon: "↑" },
};

export function StatusPill({ status }: { status: string | null | undefined }) {
  const key = (status ?? "").toLowerCase();
  const state = STATES[key];
  if (!state) return <Badge tone="neutral">{status ?? "—"}</Badge>;
  return (
    <Badge tone={state.tone} icon={state.icon}>
      {state.label}
    </Badge>
  );
}
