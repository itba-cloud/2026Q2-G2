import {
  Area,
  AreaChart,
  CartesianGrid,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCurrency, formatDate, toNumber } from "../../lib/format";
import type { PricePoint } from "../../types";

/* Chart chrome, from the same tokens as the rest of the portal. Kept as
 * constants because recharts wants concrete values in props, not classes. */
const INK_MUTED = "#898781";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";
const SERIES = "#2a78d6";

/** One branch's price for one product over time. A single series, so there is
 * no legend: the card title names it. The last point is labelled directly
 * instead of putting a number on every point. */
export function PriceHistoryChart({ points }: { points: PricePoint[] }) {
  const data = points
    .map((p) => ({ feed_date: p.feed_date, price: toNumber(p.price) }))
    .filter((p) => !Number.isNaN(p.price))
    .sort((a, b) => a.feed_date.localeCompare(b.feed_date));

  // A line needs two points to be a line. With one the honest thing is to say
  // so and show the value, not to draw a chart with a single dot in it.
  if (data.length < 2) {
    const only = data[0];
    return (
      <div className="flex h-[17rem] flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-axis text-center">
        <p className="text-[15px] font-medium text-ink">
          {only ? formatCurrency(only.price) : "Sin precios"}
        </p>
        <p className="max-w-xs text-[13px] text-ink-2">
          {only
            ? `Un solo día cargado (${formatDate(only.feed_date)}). El historial necesita al menos dos ingestas para dibujar una serie.`
            : "Esta sucursal no publicó este producto en el período."}
        </p>
      </div>
    );
  }

  const prices = data.map((d) => d.price);
  const average = prices.reduce((sum, p) => sum + p, 0) / prices.length;
  // Pad the domain so the line is not glued to the frame, and so a flat series
  // reads as flat rather than filling the height with noise.
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const pad = Math.max((max - min) * 0.25, max * 0.02);

  return (
    <div>
      <ResponsiveContainer width="100%" height={272}>
        <AreaChart data={data} margin={{ top: 16, right: 52, bottom: 4, left: 4 }}>
          <defs>
            <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={SERIES} stopOpacity={0.16} />
              <stop offset="100%" stopColor={SERIES} stopOpacity={0} />
            </linearGradient>
          </defs>

          {/* Recessive chrome: horizontal hairlines only. Vertical gridlines
              add nothing here -- the x axis is a handful of dates. */}
          <CartesianGrid stroke={GRID} strokeWidth={1} vertical={false} />

          <XAxis
            dataKey="feed_date"
            tickFormatter={(value: string) => formatDate(value).slice(0, 5)}
            tick={{ fontSize: 11, fill: INK_MUTED }}
            stroke={AXIS}
            tickLine={false}
            minTickGap={16}
          />
          <YAxis
            tickFormatter={(value: number) =>
              value >= 1000 ? `${Math.round(value / 1000)}k` : String(Math.round(value))
            }
            width={44}
            tick={{ fontSize: 11, fill: INK_MUTED }}
            stroke={AXIS}
            tickLine={false}
            domain={[min - pad, max + pad]}
          />

          {/* The period's own average, as a reference the eye can read the
              line against. Labelled, so it is not an unexplained line. */}
          <ReferenceLine
            y={average}
            stroke={AXIS}
            strokeDasharray="4 4"
            label={{
              value: `prom. ${formatCurrency(average)}`,
              position: "insideTopRight",
              fill: INK_MUTED,
              fontSize: 10,
            }}
          />

          <Tooltip
            cursor={{ stroke: AXIS, strokeWidth: 1 }}
            formatter={(value: number) => [formatCurrency(value), "Precio"]}
            labelFormatter={(label: string) => formatDate(label)}
            contentStyle={{
              borderRadius: 10,
              border: `1px solid ${GRID}`,
              boxShadow: "0 4px 12px rgba(11,11,11,0.08)",
              fontSize: 12,
            }}
          />

          <Area
            type="monotone"
            dataKey="price"
            stroke={SERIES}
            strokeWidth={2}
            fill="url(#priceFill)"
            // >=8px markers, with a surface ring so a marker over the fill
            // stays a separate mark.
            dot={{ r: 4, fill: SERIES, stroke: "#fcfcfb", strokeWidth: 2 }}
            activeDot={{ r: 6, fill: SERIES, stroke: "#fcfcfb", strokeWidth: 2 }}
            isAnimationActive={false}
          >
            <LabelList
              dataKey="price"
              position="right"
              formatter={(value: number) => formatCurrency(value)}
              style={{ fontSize: 11, fill: "#0b0b0b", fontWeight: 500 }}
              // Only the most recent point carries a label.
              content={(props) => {
                const { x, y, value, index } = props as {
                  x: number;
                  y: number;
                  value: number;
                  index: number;
                };
                if (index !== data.length - 1) return null;
                return (
                  <text x={x + 8} y={y + 4} fontSize={11} fontWeight={500} fill="#0b0b0b">
                    {formatCurrency(value)}
                  </text>
                );
              }}
            />
          </Area>
        </AreaChart>
      </ResponsiveContainer>

      {/* The table view the relief rule asks for: the same numbers, reachable
          without reading the chart at all. */}
      <details className="mt-2 px-1">
        <summary className="cursor-pointer text-[13px] text-muted hover:text-ink-2">
          Ver los valores como tabla
        </summary>
        <table className="mt-2 w-full text-[13px]">
          <caption className="sr-only">Precio por fecha de ingesta</caption>
          <thead>
            <tr>
              <th scope="col" className="border-b border-line py-1 text-left font-medium text-muted">
                Fecha
              </th>
              <th scope="col" className="border-b border-line py-1 text-right font-medium text-muted">
                Precio
              </th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.feed_date}>
                <td className="border-b border-line py-1 text-ink-2">{formatDate(d.feed_date)}</td>
                <td className="tnum border-b border-line py-1 text-right text-ink">
                  {formatCurrency(d.price)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
