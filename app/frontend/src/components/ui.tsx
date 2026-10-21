/* The shared vocabulary every screen is built from. It exists so a page reads
 * as layout and data rather than as a wall of utility classes, and so the
 * accessibility details -- label association, 44px touch targets, a caption on
 * every table, one focus treatment -- are decided once here instead of being
 * re-remembered nine times. */
import type { ReactNode, SelectHTMLAttributes, InputHTMLAttributes, ButtonHTMLAttributes } from "react";
import { useEffect, useId, useState } from "react";
import { BrandMark } from "./BrandMark";

/* -- Page scaffolding ------------------------------------------------------ */

/** The frame every page sits in. The app shell owns the viewport, so a page is
 * a fixed header over a content area that fills whatever is left -- scrolling
 * happens inside panes, never on the page itself. */
export function Page({ header, children }: { header: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="shrink-0">{header}</div>
      {/* One scroll container on a phone, where the panes stack; on a desktop
          nothing here scrolls and each pane handles its own overflow.

          The -m-1/p-1 pair is what keeps focus rings whole. This box clips, and
          a control sitting flush against its edge had the outside half of its
          2px outline cut off. The negative margin lets the box grow 4px past
          its slot and the padding puts the content back where it was, so the
          ring has somewhere to be drawn. `main` has more padding than that, so
          nothing bleeds. */}
      <div className="-m-1.5 min-h-0 min-w-0 flex-1 overflow-y-auto p-1.5 lg:overflow-hidden">
        {children}
      </div>
    </div>
  );
}

/** Title, one line of context, actions. The icon tile repeats the section's
 * nav glyph so the header reads as a place rather than as a paragraph, and
 * `description` is deliberately one line: anything longer belongs next to the
 * control it explains, not above the whole screen. */
export function PageHeader({
  title,
  description,
  icon,
  actions,
}: {
  title: string;
  description?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-3">
      {icon && (
        <span
          aria-hidden="true"
          className="grid size-12 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-ink [&>svg]:size-6"
        >
          {icon}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-2xl font-semibold leading-tight tracking-tight text-ink">
          {title}
        </h1>
        {description && <p className="truncate text-[15px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  className = "",
  bodyClassName = "p-4 sm:p-5",
  scroll = false,
  toolbar,
  footer,
  isBusy = false,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** Give the body its own scrollbar and let the card fill its grid cell,
   * instead of growing the page past the viewport. */
  scroll?: boolean;
  /** Filters. Pinned above the scrolling body, because a filter that scrolls
   * out of reach of the rows it filters is worse than no filter. */
  toolbar?: ReactNode;
  /** Paging, counts. Pinned below the scrolling body for the same reason. */
  footer?: ReactNode;
  /** Veils the body while the rows on screen are being replaced. Only for a
   * refetch that keeps the old page visible -- a first load shows skeletons,
   * and blurring those says nothing. */
  isBusy?: boolean;
}) {
  return (
    <section
      className={`min-w-0 rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(20,20,15,0.04)] ${
        scroll ? "flex flex-col lg:min-h-0 lg:overflow-hidden" : ""
      } ${className}`}
    >
      {(title || actions) && (
        <div className="flex min-h-16 min-w-0 shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line-soft px-4 py-3 sm:px-5">
          <div className="min-w-0">
            {title && <h2 className="truncate text-base font-semibold text-ink">{title}</h2>}
            {description && <p className="mt-0.5 truncate text-[13px] text-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      {toolbar && (
        <div className="min-w-0 shrink-0 border-b border-line-soft px-4 py-3 sm:px-5">{toolbar}</div>
      )}
      {scroll ? (
        <div className="relative min-w-0 lg:min-h-0 lg:flex-1">
          <div className={`lg:h-full lg:overflow-auto ${bodyClassName}`}>{children}</div>
          {isBusy && <BusyOverlay />}
        </div>
      ) : (
        <div className={`relative min-w-0 ${bodyClassName}`}>
          {children}
          {isBusy && <BusyOverlay />}
        </div>
      )}
      {footer && (
        <div className="min-w-0 shrink-0 border-t border-line-soft px-4 py-2 sm:px-5">{footer}</div>
      )}
    </section>
  );
}

/** A row of filters above the content, per the one-row-of-filters rule. */
export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-end gap-3">{children}</div>;
}

/** The frame both /login and /register sit in, so the two cannot drift apart.
 * Half the screen introduces the product, half of it is the form. On a phone
 * the pitch panel drops away entirely and only the form is left -- there is no
 * room to sell to someone who is already trying to sign in. */
export function AuthShell({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="grid min-h-[100dvh] lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-brand p-10 text-white lg:flex xl:p-14">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(120% 90% at 15% 0%, rgba(255,255,255,0.16), rgba(255,255,255,0) 55%)",
          }}
        />

        <div className="relative flex items-center gap-3">
          <BrandMark size="md" tone="on-brand" />
          <span className="text-xl font-semibold leading-tight tracking-tight">LGDC</span>
        </div>

        {/* The whole pitch is one question, so the type carries it and nothing
            competes with it. The bars are a small accent under the line, not
            wallpaper behind the words. */}
        <div className="relative max-w-xl">
          <h2 className="text-6xl font-semibold leading-[0.98] tracking-tight xl:text-7xl">
            ¿Cuánto sale
            <br />enfrente?
          </h2>
          <p className="mt-6 text-xl leading-snug text-white/75">
            Tus precios contra los de tu zona, todos los días.
          </p>
          <svg aria-hidden="true" viewBox="0 0 200 34" className="mt-9 h-8 w-52">
            {[
              { y: 0, w: 104, o: 0.3 },
              { y: 12, w: 196, o: 1 },
              { y: 24, w: 68, o: 0.3 },
            ].map((bar) => (
              <rect
                key={bar.y}
                y={bar.y}
                width={bar.w}
                height="7"
                rx="3.5"
                fill="#ffffff"
                fillOpacity={bar.o}
              />
            ))}
          </svg>
        </div>

        <p className="relative text-[13px] text-white/50">Datos públicos del SEPA.</p>
      </aside>

      <main className="flex items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-sm">
          <div className="mb-7 flex items-center gap-2.5 lg:hidden">
            <BrandMark />
            <span>
              <span className="block text-[15px] font-semibold leading-tight tracking-tight text-ink">
                LGDC
              </span>
              <span className="block text-[11px] leading-tight text-muted">
                Precios comparados del SEPA
              </span>
            </span>
          </div>

          <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
          {description && <p className="mt-1.5 text-sm text-ink-2">{description}</p>}
          {children}
          {footer && <p className="mt-6 text-sm text-ink-2">{footer}</p>}
        </div>
      </main>
    </div>
  );
}

/* -- Numbers on display ---------------------------------------------------- */

/** A single measure. `hint` carries the unit or the basis of comparison so the
 * number is never a bare figure the reader has to guess the meaning of. */
export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "neutral" | "good" | "bad" | "warn" | "brand";
}) {
  const valueTone = {
    neutral: "text-ink",
    good: "text-good-ink",
    bad: "text-critical",
    warn: "text-ink",
    brand: "text-brand-ink",
  }[tone];

  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tracking-tight ${valueTone}`}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}

/** Horizontal share of a whole, labelled. Used for the price-position split,
 * where three counts add up to the products compared. */
export function StackedBar({
  segments,
  total,
  legend = true,
}: {
  segments: { label: string; value: number; color: string }[];
  total: number;
  /** Off when the counts are already spelled out next to the bar, so the
   * same three numbers are not printed twice. */
  legend?: boolean;
}) {
  if (total <= 0) return null;
  return (
    <div>
      {/* 2px surface gaps between fills, so adjacent segments stay separate
          marks rather than reading as one band. */}
      <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-plane">
        {segments.map((s) => (
          <div
            key={s.label}
            className="h-full first:rounded-l-full last:rounded-r-full"
            style={{ width: `${(s.value / total) * 100}%`, backgroundColor: s.color }}
          />
        ))}
      </div>
      {legend && (
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
        {segments.map((s) => (
          <li key={s.label} className="flex items-center gap-1.5 text-xs text-ink-2">
            <span
              aria-hidden="true"
              className="size-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: s.color }}
            />
            <span>{s.label}</span>
            <span className="tnum font-medium text-ink">{s.value.toLocaleString("es-AR")}</span>
          </li>
        ))}
      </ul>
      )}
    </div>
  );
}

/* -- Controls -------------------------------------------------------------- */

const CONTROL =
  "block w-full rounded-lg border border-line bg-surface px-3 py-2 text-[15px] text-ink " +
  "placeholder:text-muted focus:border-brand focus:outline-none focus-visible:outline-none " +
  "disabled:cursor-not-allowed disabled:bg-plane disabled:text-muted";

/** Wraps a control with a real <label for>, which is what makes the label
 * clickable and what a screen reader reads out. `hint` is wired through
 * aria-describedby rather than left as loose text next to the field. */
export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label: string;
  hint?: string;
  children: (ids: { id: string; describedBy?: string }) => ReactNode;
  className?: string;
}) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className={className}>
      <label htmlFor={id} className="block text-[13px] font-medium text-ink-2">
        {label}
      </label>
      <div className="mt-1">{children({ id, describedBy: hint ? hintId : undefined })}</div>
      {hint && (
        <p id={hintId} className="mt-1 text-[13px] text-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${CONTROL} ${props.className ?? ""}`} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${CONTROL} ${props.className ?? ""}`} />;
}

/** Search box with the magnifier as decoration only -- the accessible name
 * comes from the label the caller passes, never from the icon. */
export function SearchInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const id = useId();
  return (
    <div className="w-full">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <div className="relative">
        <span aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted">
          {"⌕"}
        </span>
        <input
          id={id}
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={`${CONTROL} min-h-11 pl-9`}
        />
      </div>
    </div>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
};

/** min-h-11 is the 44px touch target; it is the default rather than the
 * exception, and `sm` is only for controls inside a dense table row. */
export function Button({ variant = "secondary", size = "md", className = "", ...props }: ButtonProps) {
  const variants = {
    primary: "bg-brand text-white hover:bg-brand-ink disabled:bg-muted",
    secondary: "border border-line bg-surface text-ink hover:bg-plane",
    ghost: "text-ink-2 hover:bg-plane hover:text-ink",
    danger: "border border-line bg-surface text-critical hover:bg-plane",
  }[variant];
  const sizes = { sm: "min-h-9 px-3 py-1 text-[13px]", md: "min-h-11 px-4 py-2 text-[15px]" }[size];

  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${variants} ${sizes} ${className}`}
    />
  );
}

/** A destructive action that asks once, in place. The first click arms the
 * button and it says what is about to happen; the second does it. It disarms
 * on blur and after a few seconds, so a button left armed by accident cannot
 * be triggered later by a stray click.
 *
 * Preferred over a confirmation banner for row actions: the question appears
 * under the cursor instead of somewhere else on screen, and the row does not
 * change height. */
export function ConfirmButton({
  onConfirm,
  children,
  confirmLabel,
  pending = false,
  pendingLabel = "Quitando…",
  size = "md",
  disarmAfterMs = 4000,
}: {
  onConfirm: () => void;
  children: ReactNode;
  /** What the armed button says. Make it name the consequence. */
  confirmLabel: string;
  pending?: boolean;
  pendingLabel?: string;
  size?: "sm" | "md";
  disarmAfterMs?: number;
}) {
  const [isArmed, setIsArmed] = useState(false);

  useEffect(() => {
    if (!isArmed) return;
    const timer = setTimeout(() => setIsArmed(false), disarmAfterMs);
    return () => clearTimeout(timer);
  }, [isArmed, disarmAfterMs]);

  if (pending) {
    return (
      <Button variant="danger" size={size} disabled>
        {pendingLabel}
      </Button>
    );
  }

  return (
    <Button
      variant={isArmed ? "primary" : "danger"}
      size={size}
      onClick={() => {
        if (isArmed) {
          setIsArmed(false);
          onConfirm();
        } else {
          setIsArmed(true);
        }
      }}
      onBlur={() => setIsArmed(false)}
      onKeyDown={(e) => {
        if (e.key === "Escape") setIsArmed(false);
      }}
      className={isArmed ? "bg-critical hover:bg-critical" : ""}
    >
      {isArmed ? confirmLabel : children}
    </Button>
  );
}

/* -- Labels ---------------------------------------------------------------- */

export function Badge({
  children,
  tone = "neutral",
  icon,
  size = "sm",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "bad" | "warn" | "info";
  icon?: string;
  /** `md` is for a badge that stands alone as a page-level fact, not one
   * riding inside a dense table row. */
  size?: "sm" | "md";
}) {
  const tones = {
    neutral: "bg-plane text-ink-2 ring-line",
    good: "bg-[#eaf7ea] text-good-ink ring-[#c7e8c7]",
    bad: "bg-[#fdeceb] text-critical ring-[#f6cfcd]",
    warn: "bg-[#fdf4e0] text-[#7a5300] ring-[#f3e0b0]",
    info: "bg-brand-soft text-brand-ink ring-[#a9cdf6]",
  }[tone];

  const sizes = { sm: "gap-1 px-2 py-0.5 text-[13px]", md: "gap-1.5 px-3.5 py-1.5 text-[15px]" }[size];

  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full font-medium ring-1 ring-inset ${tones} ${sizes}`}
    >
      {icon && <span aria-hidden="true">{icon}</span>}
      {children}
    </span>
  );
}

/* -- Tables ---------------------------------------------------------------- */

/** Tables scroll horizontally on a phone instead of squashing or clipping.
 * The caption is the table's accessible name and is always required. */
export function TableFrame({
  caption,
  children,
  minWidth = "40rem",
  fixed = false,
}: {
  caption: string;
  children: ReactNode;
  minWidth?: string;
  /** Fixed layout so the columns keep the widths their headers declare. With
   * auto layout a long product name takes the space it wants and squeezes
   * everything else -- which is what made the position badge wrap onto two
   * lines and every row grow taller. Pair it with `truncate` on the cell that
   * is allowed to overflow. */
  fixed?: boolean;
}) {
  return (
    <div className="overflow-x-auto">
      {/* The outer cells take the card's gutter, so a table dropped into a
          `p-0` body still lines up with the title above it instead of sitting
          flush against the border. */}
      <table
        className={`w-full border-collapse text-[15px] text-ink [&_td:first-child]:pl-5 [&_td:last-child]:pr-5 [&_th:first-child]:pl-5 [&_th:last-child]:pr-5 ${
          fixed ? "table-fixed" : ""
        }`}
        style={{ minWidth }}
      >
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function Th({
  children,
  align = "left",
  className = "",
  ...props
}: React.ThHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right" }) {
  return (
    <th
      scope="col"
      {...props}
      className={`sticky top-0 z-[1] border-b border-line bg-surface px-3 py-2.5 text-[13px] font-medium text-muted ${
        align === "right" ? "text-right" : "text-left"
      } ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  align = "left",
  numeric = false,
  className = "",
}: {
  children: ReactNode;
  align?: "left" | "right";
  numeric?: boolean;
  className?: string;
}) {
  return (
    <td
      className={`border-b border-line px-3 py-2.5 ${align === "right" ? "text-right" : "text-left"} ${
        numeric ? "tnum" : ""
      } ${className}`}
    >
      {children}
    </td>
  );
}

/** A column header that sorts. aria-sort is what tells a screen reader the
 * table is sorted and in which direction. */
export function SortableTh({
  children,
  active,
  direction,
  onClick,
  align = "left",
}: {
  children: ReactNode;
  active: boolean;
  direction: "asc" | "desc";
  onClick: () => void;
  align?: "left" | "right";
}) {
  return (
    <Th align={align} aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}>
      <button
        type="button"
        onClick={onClick}
        className={`inline-flex items-center gap-1 rounded font-medium hover:text-ink ${
          active ? "text-ink" : ""
        }`}
      >
        {children}
        <span aria-hidden="true" className={active ? "" : "opacity-0"}>
          {direction === "asc" ? "↑" : "↓"}
        </span>
      </button>
    </Th>
  );
}

/* -- Paging ---------------------------------------------------------------- */

/** Offset paging. Most endpoints here return a page and no count, so by
 * default there is no last page to jump to and no "of N" to show: "next" is
 * offered when the page came back full, which is the one thing a short page
 * reliably tells us. Pass `total` where the endpoint does report one and both
 * get better -- the range reads "de 621", and the last page stops offering a
 * "siguiente" that lands on nothing, which is exactly what the full-page
 * heuristic gets wrong when the total is a multiple of the page size. The
 * range is spelled out either way, so the control is not just two arrows with
 * no sense of place. */
export function Pagination({
  offset,
  pageSize,
  rowsOnPage,
  onChange,
  label,
  total,
  isBusy = false,
}: {
  offset: number;
  pageSize: number;
  rowsOnPage: number;
  onChange: (offset: number) => void;
  label: string;
  /** How many rows the query matches in all, when the endpoint says so. */
  total?: number;
  /** True while the next page is in flight. These lists keep the previous page
   * on screen while the new one loads, which is right -- but with no other
   * signal a slow page looks like a dead button, and people click it four more
   * times. The card says what is happening; this just stops the clicks. */
  isBusy?: boolean;
}) {
  const hasPrevious = offset > 0;
  const hasNext = total !== undefined ? offset + rowsOnPage < total : rowsOnPage === pageSize;
  if (!hasPrevious && !hasNext) return null;

  const from = rowsOnPage === 0 ? 0 : offset + 1;
  const to = offset + rowsOnPage;
  const range = total !== undefined ? `Mostrando ${from}–${to} de ${total}` : `Mostrando ${from}–${to}`;

  return (
    <PageNav
      label={label}
      status={rowsOnPage === 0 ? "Sin resultados" : range}
      onPrevious={() => onChange(Math.max(0, offset - pageSize))}
      onNext={() => onChange(offset + pageSize)}
      hasPrevious={hasPrevious}
      hasNext={hasNext}
      isBusy={isBusy}
    />
  );
}

/** What both paginations draw: where the page is, and the two buttons. */
function PageNav({
  label,
  status,
  onPrevious,
  onNext,
  hasPrevious,
  hasNext,
  isBusy,
}: {
  label: string;
  status: string;
  onPrevious: () => void;
  onNext: () => void;
  hasPrevious: boolean;
  hasNext: boolean;
  isBusy: boolean;
}) {
  return (
    <nav aria-label={label} className="flex flex-wrap items-center justify-between gap-3">
      <p className="tnum text-[13px] text-muted" role="status" aria-live="polite">
        {status}
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={onPrevious} disabled={!hasPrevious || isBusy}>
          ← Anterior
        </Button>
        <Button size="sm" onClick={onNext} disabled={!hasNext || isBusy}>
          Siguiente →
        </Button>
      </div>
    </nav>
  );
}

/** Pagination for an endpoint that pages by cursor: it knows the page it is
 * on and whether there is a next one, never how many rows there are. The
 * caller keeps the cursors it walked, so "Anterior" goes back to one it
 * already has. */
export function CursorPagination({
  page,
  pageSize,
  rowsOnPage,
  hasNext,
  onPrevious,
  onNext,
  label,
  isBusy = false,
}: {
  /** 0 for the first page. */
  page: number;
  pageSize: number;
  rowsOnPage: number;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  label: string;
  isBusy?: boolean;
}) {
  const hasPrevious = page > 0;
  if (!hasPrevious && !hasNext) return null;

  const from = page * pageSize + 1;
  const to = page * pageSize + rowsOnPage;

  return (
    <PageNav
      label={label}
      status={rowsOnPage === 0 ? "Sin resultados" : `Mostrando ${from}–${to}`}
      onPrevious={onPrevious}
      onNext={onNext}
      hasPrevious={hasPrevious}
      hasNext={hasNext}
      isBusy={isBusy}
    />
  );
}

/** Sits over the rows it is replacing: the old page stays legible enough to
 * keep your place, blurred enough to say it is stale. Covers only the body, so
 * the filters above it stay sharp and usable. */
function BusyOverlay() {
  return (
    <div
      className="absolute inset-0 z-10 grid place-items-center bg-surface/50 backdrop-blur-[3px]"
      role="status"
      aria-live="polite"
    >
      <span className="flex items-center gap-2 rounded-full border border-line bg-surface px-4 py-2 text-[15px] font-medium text-ink-2 shadow-[0_4px_16px_rgba(20,20,15,0.12)]">
        <span
          aria-hidden="true"
          className="size-4 shrink-0 animate-spin rounded-full border-2 border-line border-t-brand"
        />
        Cargando…
      </span>
    </div>
  );
}

/* -- Pending, empty and broken -------------------------------------------- */

/** A shape the size of the content that is coming, rather than a spinner in
 * the middle of nowhere: the layout does not jump when the data lands. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-line/70 ${className}`} aria-hidden="true" />;
}

/** A stand-in for a list that is coming.
 *
 * `rowClassName` carries the height of the real row, because a placeholder
 * that is not the size of the thing it replaces makes the layout jump when the
 * data lands -- which is the whole problem skeletons exist to avoid.
 *
 * `fill` keeps that height and instead renders more rows than fit, letting the
 * pane clip them. Stretching the rows to the pane was the earlier fix for the
 * gap at the bottom, and it traded a gap for rows of the wrong size. */
export function SkeletonRows({
  rows = 5,
  fill = false,
  rowClassName = "h-9",
}: {
  rows?: number;
  fill?: boolean;
  rowClassName?: string;
}) {
  const count = fill ? Math.max(rows, 30) : rows;
  return (
    <div
      className={`space-y-2 ${fill ? "h-full overflow-hidden" : ""}`}
      role="status"
      aria-label="Cargando"
    >
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className={`w-full ${rowClassName}`} />
      ))}
    </div>
  );
}
