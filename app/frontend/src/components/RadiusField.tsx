import { useId } from "react";
import {
  MAX_RADIUS_KM,
  MIN_RADIUS_KM,
  RADIUS_PRESETS,
  clampRadius,
  formatRadius,
} from "../lib/radius";

/* The one control for the competitive radius, used wherever it is set, so the
 * number means the same thing and is explained the same way everywhere.
 *
 * The point of the diagram is that "3 km" is an abstraction and an area is
 * not. Seeing the disc grow against fixed rings is what makes the setting
 * legible to someone who has never thought about it in kilometres. */
export function RadiusField({
  value,
  onChange,
  competitorCount,
  isCountLoading = false,
  nearestLabel,
}: {
  value: number;
  onChange: (km: number) => void;
  /** How many competitors the current radius catches, when it can be known.
   * It is the only feedback that says whether the radius is any good. */
  competitorCount?: number;
  isCountLoading?: boolean;
  nearestLabel?: string;
}) {
  const id = useId();
  const km = clampRadius(value);

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <label htmlFor={id} className="text-[15px] font-medium text-ink">
          Radio de competencia
        </label>
        <output htmlFor={id} className="tnum text-2xl font-semibold tracking-tight text-brand-ink">
          {formatRadius(km)}
        </output>
      </div>

      <p className="mt-1 text-[13px] text-ink-2">
        Solo los competidores dentro de este radio entran en tus comparaciones y alertas.
      </p>

      <div className="mt-3 flex items-center gap-4">
        <RadiusDiagram km={km} />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap gap-1.5">
            {RADIUS_PRESETS.map((preset) => {
              const isSelected = Math.abs(km - preset.km) < 0.05;
              return (
                <button
                  key={preset.km}
                  type="button"
                  onClick={() => onChange(preset.km)}
                  aria-pressed={isSelected}
                  className={`rounded-lg border px-2.5 py-1.5 text-left transition-colors ${
                    isSelected
                      ? "border-brand bg-brand-soft text-brand-ink"
                      : "border-line bg-surface text-ink-2 hover:border-brand hover:bg-brand-tint"
                  }`}
                >
                  <span className="block text-[15px] font-medium leading-tight">{preset.label}</span>
                  <span className="block text-xs leading-tight text-muted">{preset.hint}</span>
                </button>
              );
            })}
          </div>

          <input
            id={id}
            type="range"
            min={MIN_RADIUS_KM}
            max={MAX_RADIUS_KM}
            step={0.1}
            value={km}
            onChange={(e) => onChange(Number(e.target.value))}
            className="mt-3 h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-brand"
          />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{formatRadius(MIN_RADIUS_KM)}</span>
            <span>{formatRadius(MAX_RADIUS_KM)}</span>
          </div>
        </div>
      </div>

      {(competitorCount !== undefined || isCountLoading) && (
        <p
          className="mt-3 rounded-lg bg-plane px-3 py-2 text-[13px] text-ink-2"
          role="status"
          aria-live="polite"
        >
          {isCountLoading ? (
            "Buscando competidores…"
          ) : (
            <>
              <span className="tnum font-semibold text-ink">{competitorCount}</span> competidores
              dentro del radio
              {nearestLabel ? ` · el más cercano a ${nearestLabel}` : ""}
            </>
          )}
        </p>
      )}
    </div>
  );
}

/* Fixed rings at the preset distances, and a filled disc that grows with the
 * value: the reading is "how much of the map am I taking in", not a number. */
function RadiusDiagram({ km }: { km: number }) {
  const SIZE = 108;
  const CENTER = SIZE / 2;
  const MAX_R = CENTER - 6;
  const scale = (value: number) => (value / MAX_RADIUS_KM) * MAX_R;

  return (
    <svg
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      className="size-[6.75rem] shrink-0"
      role="img"
      aria-label={`Área de comparación de ${formatRadius(km)} de radio`}
    >
      {RADIUS_PRESETS.map((preset) => (
        <circle
          key={preset.km}
          cx={CENTER}
          cy={CENTER}
          r={scale(preset.km)}
          fill="none"
          stroke="var(--color-line)"
          strokeWidth="1"
          strokeDasharray="2 3"
        />
      ))}

      <circle
        cx={CENTER}
        cy={CENTER}
        r={scale(km)}
        fill="var(--color-brand)"
        fillOpacity="0.14"
        stroke="var(--color-brand)"
        strokeWidth="1.5"
      />

      {/* Your branch, at the centre of its own area. */}
      <circle cx={CENTER} cy={CENTER} r="3.5" fill="var(--color-brand)" />
    </svg>
  );
}
