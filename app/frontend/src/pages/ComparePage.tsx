import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getCompetitors, getFeedDates, getHeadToHead, getMyBranches } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { IconChevronDown, IconClose, IconCompare, IconStore } from "../components/icons";
import { Modal } from "../components/Modal";
import {
  Badge,
  Button,
  Card,
  Page,
  PageHeader,
  Pagination,
  SearchInput,
  SkeletonRows,
  TableFrame,
  Td,
  Th,
} from "../components/ui";
import { useActiveBranch } from "../hooks/useActiveBranch";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import {
  DIRECTION_STYLE,
  formatCurrency,
  formatDistance,
  formatPercent,
  isImplausibleMin,
  MAX_CREDIBLE_GAP_PCT,
  priceDirection,
  toNumber,
} from "../lib/format";
import type { Competitor, HeadToHeadRow } from "../types";

const PAGE_SIZE = 50;
/** How many neighbours the rival picker lists at once. It is a search box, not
 * a table: past this many the answer is to type, not to scroll. */
const PICKER_ROWS = 60;
/** Mirrors MAX_HEAD_TO_HEAD_TARGETS in app/api/main.py, which rejects more. */
const MAX_RIVALS = 4;

function rivalKey(c: { company_id: number; banner_id: number; branch_id: string }): string {
  return `${c.company_id}:${c.banner_id}:${c.branch_id}`;
}

/* HeadToHeadRow carries the rival prices in a nested `rival_prices` object
 * keyed by rivalKey(), which a CSV cannot represent -- so it is flattened into
 * one column per chosen rival, named after the branch the way the table header
 * names it. The columns therefore depend on the selection, which is why this is
 * a function and not a constant. */
function headToHeadCsv(rows: HeadToHeadRow[], rivals: Competitor[]) {
  const rivalColumn = (r: Competitor) => `${r.banner_name} ${r.name}`;
  return {
    columns: [
      "ean", "description", "brand", "own_price",
      ...rivals.map(rivalColumn),
      "min_price", "avg_price", "offers", "pct_over_min",
    ],
    rows: rows.map((row) => ({
      ...row,
      ...Object.fromEntries(
        rivals.map((r) => [rivalColumn(r), row.rival_prices[rivalKey(r)] ?? ""]),
      ),
    })),
  };
}

/* The comparison everywhere else in the app is against "whatever the radius
 * catches", which is the right default and the wrong answer to "am I beating
 * the Dia across the street?". This screen is that question: you name the
 * rivals, and the radius stops being part of the picture -- a branch outside
 * it compares just as well. */
const SELECTIONS_KEY = "lgdc_compare_rivals";
const NO_RIVALS: Competitor[] = [];

function readSelections(): Record<string, Competitor[]> {
  try {
    return JSON.parse(sessionStorage.getItem(SELECTIONS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function writeSelections(selections: Record<string, Competitor[]>): void {
  try {
    sessionStorage.setItem(SELECTIONS_KEY, JSON.stringify(selections));
  } catch {
    // Storage full or blocked: the selection just does not outlive the screen.
  }
}

export function ComparePage() {
  const { activeBranch } = useActiveBranch();
  // The rivals chosen for each branch, kept for the session: leaving the
  // screen and coming back used to start over from none.
  const branchKey = activeBranch
    ? `${activeBranch.company_id}:${activeBranch.banner_id}:${activeBranch.branch_id}`
    : "";
  const [byBranch, setByBranch] = useState<Record<string, Competitor[]>>(readSelections);
  const selected = byBranch[branchKey] ?? NO_RIVALS;
  function setSelected(update: Competitor[] | ((current: Competitor[]) => Competitor[])) {
    setByBranch((previous) => {
      const current = previous[branchKey] ?? NO_RIVALS;
      const next = { ...previous, [branchKey]: typeof update === "function" ? update(current) : update };
      writeSelections(next);
      return next;
    });
  }
  const [isPickerOpen, setIsPickerOpen] = useState(false);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedQ = useDebouncedValue(q, 300);

  const branchesQuery = useQuery({ queryKey: ["me", "branches"], queryFn: getMyBranches });
  const feedDatesQuery = useQuery({ queryKey: ["feed-dates"], queryFn: getFeedDates });
  const latestFeedDate = feedDatesQuery.data?.[0];

  const key = activeBranch
    ? {
        company_id: activeBranch.company_id,
        banner_id: activeBranch.banner_id,
        branch_id: activeBranch.branch_id,
      }
    : null;

  useEffect(() => setOffset(0), [debouncedQ, selected]);

  const vs = useMemo(() => selected.map(rivalKey), [selected]);

  const rowsQuery = useQuery({
    queryKey: ["head-to-head", key, latestFeedDate, vs, debouncedQ, offset],
    queryFn: () =>
      getHeadToHead(key!, {
        vs,
        feed_date: latestFeedDate,
        q: debouncedQ || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    enabled: !!key && !!latestFeedDate && vs.length > 0,
    placeholderData: (previous) => previous,
  });

  const rows = rowsQuery.data ?? [];

  const header = (
    <PageHeader
      icon={<IconCompare />}
      title="Comparar"
      description={
        activeBranch ? (
          <>
            <span className="font-semibold text-ink">
              {activeBranch.label || activeBranch.name}
            </span>{" "}
            contra las sucursales que elijas, estén o no dentro de su radio
          </>
        ) : (
          "Tu sucursal contra las que elijas, una por una."
        )
      }
    />
  );

  if (branchesQuery.data?.length === 0) {
    return (
      <Page header={header}>
        <EmptyState
          fill
          icon={<IconStore className="size-8" />}
          title="Todavía no seguís ninguna sucursal"
          description="Para comparar hace falta una sucursal tuya de la que partir."
          action={
            <Link to="/branches">
              <Button variant="primary">Ir a mis sucursales</Button>
            </Link>
          }
        />
      </Page>
    );
  }

  return (
    <Page header={header}>
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-rows-[auto_minmax(0,1fr)]">
        <RivalBar
          selected={selected}
          onRemove={(c) => setSelected((s) => s.filter((x) => rivalKey(x) !== rivalKey(c)))}
          onOpenPicker={() => setIsPickerOpen(true)}
          disabled={!key}
        />

        <Card
          scroll
          className="lg:h-full"
          isBusy={rows.length > 0 && rowsQuery.isFetching}
          title="Precio a precio"
          description={
            selected.length > 0
              ? `Tu precio contra ${selected.length} ${selected.length === 1 ? "sucursal" : "sucursales"}, ordenado por dónde perdés más.`
              : undefined
          }
          actions={
            selected.length > 0 ? (
              <ExportCsvButton
                {...headToHeadCsv(rows, selected)}
                filenameStem="comparacion"
                feedDate={latestFeedDate}
                title={`Descarga las ${rows.length} filas de esta página, una columna por sucursal elegida.`}
              />
            ) : undefined
          }
          bodyClassName="p-0"
          toolbar={
            selected.length > 0 ? (
              <div className="sm:max-w-md">
                <SearchInput
                  label="Filtrar productos de la comparación"
                  value={q}
                  onChange={setQ}
                  placeholder="Filtrar por descripción…"
                />
              </div>
            ) : undefined
          }
          footer={
            rows.length > 0 ? (
              <Pagination
                label="Paginación de la comparación"
                offset={offset}
                pageSize={PAGE_SIZE}
                rowsOnPage={rows.length}
                onChange={setOffset}
                isBusy={rowsQuery.isFetching}
              />
            ) : undefined
          }
        >
          {selected.length === 0 && (
            <EmptyState
              fill
              variant="quiet"
              icon={<IconCompare className="size-8" />}
              title="Elegí contra quién comparar"
              description={`Sumá hasta ${MAX_RIVALS} sucursales y vas a ver tu precio y el de cada una, producto por producto.`}
              action={
                <Button variant="primary" onClick={() => setIsPickerOpen(true)} disabled={!key}>
                  Elegir sucursales
                </Button>
              }
            />
          )}
          {selected.length > 0 && rowsQuery.isLoading && (
            <div className="h-full p-4">
              <SkeletonRows fill rowClassName="h-9" />
            </div>
          )}
          {selected.length > 0 && rowsQuery.isError && (
            <div className="p-4">
              <ErrorNotice error={rowsQuery.error} retry={() => rowsQuery.refetch()} />
            </div>
          )}
          {selected.length > 0 && rowsQuery.data && rows.length === 0 && (
            <EmptyState
              fill
              variant="quiet"
              title={debouncedQ ? "Sin resultados" : "No hay productos en común"}
              description={
                debouncedQ
                  ? "Ningún producto en común coincide con ese texto."
                  : "Estas sucursales no publican ningún código de barras que también esté en tu surtido para esta fecha."
              }
            />
          )}

          {rows.length > 0 && (
            <ComparisonTable rows={rows} rivals={selected} />
          )}
        </Card>
      </div>

      {key && (
        <RivalPicker
          isOpen={isPickerOpen}
          onClose={() => setIsPickerOpen(false)}
          branchKey={key}
          selected={selected}
          onChange={setSelected}
        />
      )}
    </Page>
  );
}

/* The rivals as chips rather than a list: they are the subject of the screen,
 * they change often, and each one has to be removable in one click. */
function RivalBar({
  selected,
  onRemove,
  onOpenPicker,
  disabled,
}: {
  selected: Competitor[];
  onRemove: (c: Competitor) => void;
  onOpenPicker: () => void;
  disabled: boolean;
}) {
  return (
    /* Two columns, not one wrapping row: the chips wrap inside their own box
       and the action stays put. Sharing one flex container meant a third chip
       pushed the button onto a line of its own. */
    <div className="flex min-w-0 items-center gap-3 rounded-xl border border-line bg-surface px-3 py-2">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        {/* "Comparando contra" only works as a label when something follows it.
            With nothing selected it read as a half-finished sentence, so the
            empty case is its own complete one. */}
        {selected.length === 0 ? (
          <span className="text-[15px] text-ink-2">
            Elegí las sucursales con las que querés compararte.
          </span>
        ) : (
          <span className="text-[13px] font-medium text-muted">Comparando contra</span>
        )}
        {selected.map((c) => (
          <span
            key={rivalKey(c)}
            className="inline-flex min-w-0 items-center gap-1.5 rounded-lg border border-line bg-plane py-1 pl-2.5 pr-1 text-[13px]"
          >
            {/* Branch names in the feed run long -- "Rivadavia y
                Misiones[Misiones 6]" -- so the chip caps its width instead of
                letting one name eat the bar. */}
            <span className="min-w-0 max-w-[12rem] truncate font-medium text-ink" title={c.name}>
              {c.name}
            </span>
            <span className="shrink-0 text-muted">{c.banner_name}</span>
            <button
              type="button"
              onClick={() => onRemove(c)}
              aria-label={`Quitar ${c.banner_name} ${c.name} de la comparación`}
              className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted hover:bg-line hover:text-ink"
            >
              <IconClose className="size-3.5" />
            </button>
          </span>
        ))}
      </div>

      {/* The one action on this bar, so it takes the app's normal button size
          rather than the compact one used inside table rows. */}
      <Button
        className="shrink-0"
        onClick={onOpenPicker}
        disabled={disabled || selected.length >= MAX_RIVALS}
      >
        {selected.length >= MAX_RIVALS ? `Máximo ${MAX_RIVALS}` : "Agregar sucursal"}
      </Button>
    </div>
  );
}

function ComparisonTable({ rows, rivals }: { rows: HeadToHeadRow[]; rivals: Competitor[] }) {
  return (
    <TableFrame
      fixed
      caption="Tu precio contra el de cada sucursal elegida, producto por producto"
      minWidth={`${32 + rivals.length * 9}rem`}
    >
      <thead>
        <tr>
          <Th>Producto</Th>
          <Th align="right" className="w-28">
            Tu precio
          </Th>
          {rivals.map((r) => (
            <Th key={rivalKey(r)} align="right" className="w-36">
              <span className="block truncate" title={r.name}>
                {r.name}
              </span>
              <span className="block truncate font-normal text-muted">{r.banner_name}</span>
            </Th>
          ))}
          <Th align="right" className="w-32">
            vs. el más barato
          </Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const suspicious = isImplausibleMin(row.min_price, row.avg_price, row.pct_over_min);
          const own = toNumber(row.own_price);
          const min = toNumber(row.min_price);
          const direction = priceDirection(suspicious ? 0 : toNumber(row.pct_over_min));
          const style = DIRECTION_STYLE[direction];

          return (
            <tr key={row.ean} className="hover:bg-plane">
              <Td className="truncate">
                <Link
                  to={`/products/${row.ean}`}
                  title={row.description}
                  className="rounded font-medium text-brand-ink hover:underline"
                >
                  {row.description}
                </Link>
              </Td>
              <Td align="right" numeric className="font-semibold">
                {formatCurrency(row.own_price)}
              </Td>
              {rivals.map((r) => {
                const price = row.rival_prices[rivalKey(r)];
                if (price === undefined) {
                  return (
                    <Td key={rivalKey(r)} align="right" numeric className="text-muted">
                      <span title="Esta sucursal no publica este producto">—</span>
                    </Td>
                  );
                }
                // The cheapest of the row gets the emphasis: that is the number
                // the merchant is trying to beat.
                const isCheapest = !suspicious && Math.abs(price - min) < 0.005 && price < own;
                return (
                  <Td
                    key={rivalKey(r)}
                    align="right"
                    numeric
                    className={isCheapest ? "font-semibold text-good-ink" : "text-ink-2"}
                  >
                    {formatCurrency(price)}
                  </Td>
                );
              })}
              <Td align="right" numeric className={`font-medium ${style.className}`}>
                {suspicious ? (
                  <span title="El precio más bajo no parece un precio real de góndola.">—</span>
                ) : (
                  <>
                    {style.arrow && <span aria-hidden="true">{style.arrow} </span>}
                    {formatPercent(row.pct_over_min, { signed: true, cap: MAX_CREDIBLE_GAP_PCT })}
                  </>
                )}
              </Td>
            </tr>
          );
        })}
      </tbody>
    </TableFrame>
  );
}

/* Rivals come from the branch's own competitor list, which is already sorted
 * by distance and carries the names -- so picking is choosing from "who is
 * near me", not typing three identifiers. The radius here is only the net that
 * finds candidates; once chosen, a rival compares regardless of it. */
function RivalPicker({
  isOpen,
  onClose,
  branchKey,
  selected,
  onChange,
}: {
  isOpen: boolean;
  onClose: () => void;
  branchKey: { company_id: number; banner_id: number; branch_id: string };
  selected: Competitor[];
  onChange: (next: Competitor[]) => void;
}) {
  const [q, setQ] = useState("");
  const [searchRadius, setSearchRadius] = useState(10);
  const debouncedQ = useDebouncedValue(q, 250);

  // The needle goes to the server. This list shows PICKER_ROWS at a time, and
  // a 10 km radius over a dense area holds hundreds of branches -- fetching
  // every one of them to filter the rest away in the browser was most of the
  // cost of opening this dialog.
  const competitorsQuery = useQuery({
    queryKey: ["competitors", "picker", branchKey, searchRadius, debouncedQ],
    queryFn: () =>
      getCompetitors(branchKey, {
        radius_km: searchRadius,
        q: debouncedQ || undefined,
        limit: PICKER_ROWS,
      }),
    enabled: isOpen,
    // Typing re-queries; without this the list blinks out on every keystroke.
    placeholderData: (previous) => previous,
  });

  const selectedKeys = new Set(selected.map(rivalKey));
  const shown = competitorsQuery.data?.items ?? [];
  const total = competitorsQuery.data?.total ?? 0;

  function toggle(c: Competitor) {
    if (selectedKeys.has(rivalKey(c))) {
      onChange(selected.filter((x) => rivalKey(x) !== rivalKey(c)));
    } else if (selected.length < MAX_RIVALS) {
      onChange([...selected, c]);
    }
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      size="lg"
      title="Elegir sucursales"
      description={`Hasta ${MAX_RIVALS} a la vez. Se ordenan por distancia a tu sucursal.`}
      footer={
        <Button variant="primary" onClick={onClose}>
          Listo ({selected.length})
        </Button>
      }
    >
      <div className="space-y-3">
        <SearchInput
          label="Buscar sucursal por nombre, bandera o localidad"
          value={q}
          onChange={setQ}
          placeholder="Nombre, bandera o localidad…"
        />

        {competitorsQuery.isLoading && <SkeletonRows rows={6} rowClassName="h-14" />}
        {competitorsQuery.isError && (
          <ErrorNotice error={competitorsQuery.error} retry={() => competitorsQuery.refetch()} />
        )}
        {competitorsQuery.data && shown.length === 0 && (
          <EmptyState
            variant="quiet"
            title="Ninguna sucursal coincide"
            description={
              q
                ? "Probá con menos palabras."
                : "No hay otras cadenas publicando precios cerca de esta sucursal."
            }
          />
        )}

        {shown.length > 0 && (
          <ul className="divide-y divide-line-soft">
            {shown.map((c) => {
              const isSelected = selectedKeys.has(rivalKey(c));
              const isFull = !isSelected && selected.length >= MAX_RIVALS;
              return (
                <li key={rivalKey(c)}>
                  <button
                    type="button"
                    onClick={() => toggle(c)}
                    disabled={isFull}
                    aria-pressed={isSelected}
                    className={`flex w-full items-center gap-3 px-1 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      isSelected ? "" : "hover:bg-plane"
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`grid size-5 shrink-0 place-items-center rounded border text-[11px] ${
                        isSelected
                          ? "border-brand bg-brand text-white"
                          : "border-axis bg-surface text-transparent"
                      }`}
                    >
                      ✓
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] font-medium text-ink">
                        {c.name}
                      </span>
                      <span className="block truncate text-[13px] text-muted">
                        {c.banner_name} · {c.locality}
                      </span>
                    </span>
                    <Badge tone="neutral">{formatDistance(c.distance_m)}</Badge>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {total > shown.length && (
          <p className="text-[13px] text-muted">
            {total} sucursales entran en el radio; se muestran las {shown.length} más
            cercanas. Buscá por nombre, bandera o localidad para acortar la lista.
          </p>
        )}

        {competitorsQuery.data && (
          <label className="flex items-center gap-2 border-t border-line-soft pt-3 text-[13px] text-ink-2">
            <IconChevronDown className="size-4 text-muted" />
            Buscando dentro de {searchRadius} km
            <input
              type="range"
              min={1}
              max={10}
              step={1}
              value={searchRadius}
              onChange={(e) => setSearchRadius(Number(e.target.value))}
              className="ml-2 h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-line accent-brand"
            />
          </label>
        )}
      </div>
    </Modal>
  );
}
