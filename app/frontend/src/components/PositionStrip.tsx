import { ErrorNotice } from "./ErrorNotice";
import { Card, Skeleton, StackedBar } from "./ui";
import type { ComparisonSummary } from "../types";

/* Diverging, not categorical: the measure runs cheaper <-> dearer with "matches
 * the minimum" as the neutral middle, so it takes the documented diverging pair
 * (blue/red) over a red-green split. The metrics carry the same three colours
 * as dots, which is what lets the bar go without a legend of its own. */
const POSITION_COLORS = { cheaper: "#2a78d6", matched: "#c3c2b7", dearer: "#d03b3b" };

/* The four counts and the split they add up to, in one card. They were five
 * stacked blocks before; they are one measure read four ways, so they belong
 * in one frame -- and that is most of what a screen had to give back to fit. */
export function PositionStrip({
  summary,
  isLoading,
  error,
  retry,
  assortmentLabel = "en tu surtido",
}: {
  summary: ComparisonSummary | undefined;
  isLoading: boolean;
  error: unknown;
  retry: () => void;
  assortmentLabel?: string;
}) {
  if (error) {
    return (
      <Card bodyClassName="p-4">
        <ErrorNotice error={error} retry={retry} />
      </Card>
    );
  }

  if (isLoading || !summary) {
    return (
      /* Same grid, same dividers, same three stacked blocks per metric as the
         real card below, so nothing shifts when the numbers arrive. */
      <Card bodyClassName="p-4 sm:p-5">
        <div
          className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4 sm:divide-x sm:divide-line-soft"
          role="status"
          aria-label="Cargando"
        >
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className={i > 0 ? "sm:pl-4" : ""}>
              <Skeleton className="h-5 w-28" />
              <Skeleton className="mt-0.5 h-9 w-24" />
              <Skeleton className="h-5 w-32" />
            </div>
          ))}
        </div>
        <div className="mt-4 border-t border-line-soft pt-4">
          <Skeleton className="h-3 w-full rounded-full" />
        </div>
      </Card>
    );
  }

  return (
    <Card bodyClassName="p-4 sm:p-5">
      <div className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-4 sm:divide-x sm:divide-line-soft">
        <Metric
          label="Productos comparados"
          value={summary.compared}
          hint={`de ${summary.own_assortment.toLocaleString("es-AR")} ${assortmentLabel}`}
        />
        <Metric
          label="Sos el más barato"
          value={summary.cheaper}
          hint={share(summary.cheaper, summary.compared)}
          dot={POSITION_COLORS.cheaper}
          className="sm:pl-4"
        />
        <Metric
          label="Igualás el mínimo"
          value={summary.matched_min}
          hint={share(summary.matched_min, summary.compared)}
          dot={POSITION_COLORS.matched}
          className="sm:pl-4"
        />
        <Metric
          label="Estás más caro"
          value={summary.dearer}
          hint={share(summary.dearer, summary.compared)}
          dot={POSITION_COLORS.dearer}
          tone="bad"
          className="sm:pl-4"
        />
      </div>

      {summary.compared > 0 && (
        <div className="mt-4 border-t border-line-soft pt-4">
          <StackedBar
            total={summary.compared}
            legend={false}
            segments={[
              { label: "Más barato", value: summary.cheaper, color: POSITION_COLORS.cheaper },
              {
                label: "Iguala el mínimo",
                value: summary.matched_min,
                color: POSITION_COLORS.matched,
              },
              { label: "Más caro", value: summary.dearer, color: POSITION_COLORS.dearer },
            ]}
          />
        </div>
      )}
    </Card>
  );
}

function Metric({
  label,
  value,
  hint,
  dot,
  tone = "neutral",
  className = "",
}: {
  label: string;
  value: number;
  hint?: string;
  dot?: string;
  tone?: "neutral" | "bad";
  className?: string;
}) {
  return (
    <div className={`min-w-0 ${className}`}>
      <p className="flex items-center gap-1.5 text-[13px] font-medium text-muted">
        {dot && (
          <span
            aria-hidden="true"
            className="size-2 shrink-0 rounded-full"
            style={{ backgroundColor: dot }}
          />
        )}
        <span className="truncate">{label}</span>
      </p>
      <p
        className={`tnum mt-0.5 text-[1.85rem] font-semibold leading-tight tracking-tight ${
          tone === "bad" ? "text-critical" : "text-ink"
        }`}
      >
        {value.toLocaleString("es-AR")}
      </p>
      {hint && <p className="truncate text-[13px] text-muted">{hint}</p>}
    </div>
  );
}

function share(part: number, whole: number): string {
  if (!whole) return "—";
  return `${Math.round((part / whole) * 100)}% de los comparados`;
}
