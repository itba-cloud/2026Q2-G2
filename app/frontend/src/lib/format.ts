// The API returns every NUMERIC column (money, percentages, coordinates) as a
// JSON string, to avoid float rounding of money in Postgres -> JSON. This is
// the one place that turns those strings into numbers.
export function toNumber(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return NaN;
  if (typeof value === "number") return value;
  const n = Number.parseFloat(value);
  return n;
}

const currencyFormatter = new Intl.NumberFormat("es-AR", {
  style: "currency",
  currency: "ARS",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatCurrency(value: string | number | null | undefined): string {
  const n = toNumber(value);
  if (Number.isNaN(n)) return "—";
  return currencyFormatter.format(n);
}

const percentFormatter = new Intl.NumberFormat("es-AR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

export function formatPercent(
  value: string | number | null | undefined,
  { signed = false, cap }: { signed?: boolean; cap?: number } = {},
): string {
  const n = toNumber(value);
  if (Number.isNaN(n)) return "—";
  const sign = signed && n > 0 ? "+" : "";
  // Past the cap the exact figure stops carrying information and starts
  // costing legibility: a "+58.689.900,0%" column is unreadable and, worse,
  // reads as precision the underlying price does not have. See
  // `isImplausibleMin` for where those numbers come from.
  if (cap !== undefined && Math.abs(n) > cap) {
    return `${n > 0 ? ">+" : "<-"}${percentFormatter.format(cap)}%`;
  }
  return `${sign}${percentFormatter.format(n)}%`;
}

/* -- When a comparison cannot be trusted ----------------------------------
 *
 * A handful of rows in the SEPA are not shelf prices: 0,01 and 1,00
 * placeholders for "not for sale", and listings whose unit does not match
 * ours (a single bar against a box of twenty-four). They are a few thousand
 * rows in 14.5 million, and they matter out of all proportion, because one of
 * them becomes the minimum of a competitive set and turns the whole
 * comparison against it into a four-digit percentage.
 *
 * Three tests, in the order they catch things. Every threshold is here and
 * nowhere else, so they can be tuned in one place. */

/** Below this, a published price cannot be a real shelf price. */
export const IMPLAUSIBLE_PRICE_FLOOR = 10;

/** How far under its own set's average a minimum can sit before it is an
 * outlier rather than a competitor's price. */
export const OUTLIER_RATIO = 8;

/** Past this gap it stops being a price difference and starts being a
 * different product: nobody is 400% over the market and still selling. */
export const MAX_CREDIBLE_GAP_PCT = 300;

/** Whether the cheapest offer in a competitive set should be read with
 * suspicion, so the row is shown as "sin comparar" rather than as a gap the
 * merchant could act on. Pass `pctOverMin` where it is known: it is the test
 * that catches a mismatched unit, which the price alone cannot. */
export function isImplausibleMin(
  minPrice: string | number | null | undefined,
  avgPrice: string | number | null | undefined,
  pctOverMin?: string | number | null,
): boolean {
  const min = toNumber(minPrice);
  const avg = toNumber(avgPrice);

  if (!Number.isNaN(min)) {
    if (min < IMPLAUSIBLE_PRICE_FLOOR) return true;
    if (!Number.isNaN(avg) && avg > 0 && min < avg / OUTLIER_RATIO) return true;
  }

  if (pctOverMin !== undefined && pctOverMin !== null) {
    const pct = toNumber(pctOverMin);
    if (!Number.isNaN(pct) && pct > MAX_CREDIBLE_GAP_PCT) return true;
  }

  return false;
}

const dateFormatter = new Intl.DateTimeFormat("es-AR", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  // feed_date arrives as "YYYY-MM-DD"; parse as local to avoid UTC-shifting
  // it a day back/forward depending on the viewer's timezone.
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const date = isDateOnly ? new Date(`${value}T00:00:00`) : new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return dateFormatter.format(date);
}

const dateTimeFormatter = new Intl.DateTimeFormat("es-AR", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return dateTimeFormatter.format(date);
}

export function formatDistance(meters: number | null | undefined): string {
  if (meters === null || meters === undefined || Number.isNaN(meters)) return "—";
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(1).replace(".", ",")} km`;
}

export function todayIsoDate(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Which way a price moved. `>= 0` was treated as a rise in four places, so a
 * product that did not move at all was drawn as a red increase with an up
 * arrow. Zero is its own case: neutral, and no arrow to misread. */
export type PriceDirection = "up" | "down" | "flat";

export function priceDirection(value: string | number | null | undefined): PriceDirection {
  const n = toNumber(value);
  if (Number.isNaN(n) || n === 0) return "flat";
  return n > 0 ? "up" : "down";
}

/** The colour and the glyph that go with a direction, so the two never drift
 * apart. The arrow repeats what the colour says, which is what keeps the
 * direction readable without telling red from green. */
export const DIRECTION_STYLE: Record<PriceDirection, { className: string; arrow: string }> = {
  up: { className: "text-critical", arrow: "▲" },
  down: { className: "text-good-ink", arrow: "▼" },
  flat: { className: "text-muted", arrow: "" },
};
