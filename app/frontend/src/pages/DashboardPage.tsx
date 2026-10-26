import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getBranchChanges, getDashboard, getMyBranches } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { PositionStrip } from "../components/PositionStrip";
import { ErrorNotice } from "../components/ErrorNotice";
import { IconChevronRight, IconDashboard, IconHistory, IconStore, IconTag } from "../components/icons";
import { Badge, Button, Card, Page, PageHeader, SkeletonRows } from "../components/ui";
import { useActiveBranch } from "../hooks/useActiveBranch";
import { useSlow } from "../hooks/useSlow";
import {
  formatCurrency,
  formatDate,
  formatPercent,
  isImplausibleMin,
  MAX_CREDIBLE_GAP_PCT,
  priceDirection,
  DIRECTION_STYLE,
  toNumber,
} from "../lib/format";
import { formatRadius } from "../lib/radius";
import type { ChangeRow, ComparisonRow } from "../types";

/* Fetched a little over what fits: the worst list drops the rows whose
 * minimum is not a real price, so it needs slack to still fill the pane. */
const ROWS = 20;

export function DashboardPage() {
  const { activeBranch } = useActiveBranch();

  const branchesQuery = useQuery({ queryKey: ["me", "branches"], queryFn: getMyBranches });

  const key = activeBranch
    ? {
        company_id: activeBranch.company_id,
        banner_id: activeBranch.banner_id,
        branch_id: activeBranch.branch_id,
      }
    : null;

  // Neither of these is told which feed_date to use, so neither waits to be
  // told: the API resolves the latest itself and the tablero labels itself
  // from what comes back. Passing the date meant fetching /feed-dates first
  // and holding both of these -- the expensive one included -- behind a round
  // trip that answered a question the API could answer on its own.
  // The radius is part of the key: a radius just edited is another answer,
  // not the cached one under a new label. While the comparison of a new
  // branch or radius is being precomputed the API answers `pending`, and the
  // screen asks again until it is there.
  const dashboardQuery = useQuery({
    queryKey: ["dashboard", key, activeBranch?.radius_km],
    queryFn: () => getDashboard(key!, { limit: ROWS }),
    enabled: !!key,
    refetchInterval: (query) => (query.state.data?.source === "pending" ? 5_000 : false),
  });
  // `pending` from the API, or a first answer that is taking long: both mean
  // the comparison is being computed, and the screen says so.
  const slow = useSlow(dashboardQuery.isLoading);
  const pending = dashboardQuery.data?.source === "pending" || slow;

  const changesQuery = useQuery({
    queryKey: ["changes-recent", key],
    queryFn: () => getBranchChanges(key!, { limit: ROWS }),
    enabled: !!key,
  });

  const latestFeedDate = dashboardQuery.data?.feed_date ?? undefined;
  const summary = dashboardQuery.data?.summary;
  const worst = dashboardQuery.data?.worst;

  const header = (
    <PageHeader
      icon={<IconDashboard />}
      title="Tablero"
      description={
        activeBranch ? (
          <>
            <span className="font-semibold text-ink">
              {activeBranch.label || activeBranch.name}
            </span>{" "}
            contra la competencia en{" "}
            <span className="tnum font-semibold text-ink">
              {formatRadius(toNumber(activeBranch.radius_km))}
            </span>
          </>
        ) : (
          "Tu sucursal contra la competencia de su radio."
        )
      }
      actions={
        latestFeedDate ? (
          <Badge tone="neutral" size="md">
            Precios del {formatDate(latestFeedDate)}
          </Badge>
        ) : undefined
      }
    />
  );

  if (branchesQuery.isError) {
    return (
      <Page header={header}>
        <ErrorNotice error={branchesQuery.error} retry={() => branchesQuery.refetch()} />
      </Page>
    );
  }

  if (branchesQuery.data?.length === 0) {
    return (
      <Page header={header}>
        <EmptyState
          icon={<IconStore className="size-8" />}
          title="Todavía no seguís ninguna sucursal"
          description="Elegí una sucursal del SEPA y definí un radio. A partir de ahí el tablero compara sus precios contra los de la competencia que quede dentro de ese radio."
          action={
            <Link to="/branches">
              <Button variant="primary">Elegir una sucursal</Button>
            </Link>
          }
        />
      </Page>
    );
  }

  if (dashboardQuery.data && !latestFeedDate) {
    return (
      <Page header={header}>
        <EmptyState
          title="Todavía no hay ingestas cargadas"
          description="Cuando corra la ingesta diaria del SEPA, las comparaciones aparecen acá."
        />
      </Page>
    );
  }

  return (
    <Page header={header}>
      {/* One auto row for the position summary, one elastic row for the two
          feeds. Nothing here grows the page: the feeds scroll inside. */}
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-rows-[auto_minmax(12rem,1fr)]">
        <PositionStrip
          summary={pending ? undefined : summary}
          isLoading={dashboardQuery.isLoading || pending}
          error={dashboardQuery.isError ? dashboardQuery.error : null}
          retry={() => dashboardQuery.refetch()}
        />

        <div className="grid min-w-0 grid-cols-1 gap-4 lg:min-h-0 xl:grid-cols-2">
          <Card
            scroll
            title="Dónde perdés por más"
            description="Tu precio contra el más bajo del conjunto competitivo."
            actions={
              <Link
                to="/products"
                className="inline-flex items-center gap-0.5 rounded text-[13px] font-medium text-brand-ink hover:underline"
              >
                Ver todo
                <IconChevronRight className="size-4" />
              </Link>
            }
            bodyClassName="p-2"
          >
            {dashboardQuery.isLoading && !slow && <SkeletonRows fill rowClassName="h-14" />}
            {dashboardQuery.isError && (
              <ErrorNotice error={dashboardQuery.error} retry={() => dashboardQuery.refetch()} />
            )}
            {pending && (
              <EmptyState
                  fill
                  variant="quiet"
                  icon={<IconTag className="size-8" />}
                  title="Calculando la comparación"
                  description="Es la primera vez que se compara esta sucursal con este radio: tarda alrededor de un minuto. La pantalla se actualiza sola."
                />
            )}
            {!pending && worst?.length === 0 && (
              <EmptyState
                  fill
                  variant="quiet"
                  icon={<IconTag className="size-8" />}
                  title="Nada por revisar todavía"
                  description="Ningún producto de esta sucursal tiene competencia comparable dentro del radio. Podés ampliarlo desde Mis sucursales."
                />
            )}
            {!!worst?.length && <WorstList rows={worst} />}
          </Card>

          <Card
            scroll
            title="Movimientos recientes"
            description="Precios que cambiaron respecto de la ingesta anterior."
            bodyClassName="p-2"
          >
            {changesQuery.isLoading && <SkeletonRows fill rowClassName="h-14" />}
            {changesQuery.isError && (
              <ErrorNotice error={changesQuery.error} retry={() => changesQuery.refetch()} />
            )}
            {changesQuery.data?.length === 0 && (
              /* Two different situations wearing the same words before: a
                 quiet day, or a feed with nothing to diff against yet. */
              <EmptyState
                  fill
                  variant="quiet"
                  icon={<IconHistory className="size-8" />}
                  title="Sin movimientos en esta ingesta"
                  description="Ningún precio de tu sucursal cambió respecto del día anterior. Hacen falta al menos dos ingestas cargadas para que haya con qué comparar."
                />
            )}
            {!!changesQuery.data?.length && <ChangesList rows={changesQuery.data} />}
          </Card>
        </div>
      </div>
    </Page>
  );
}

/** Each row carries a bar as well as the number, so the ranking is visible at
 * a glance instead of having to be read percentage by percentage.
 *
 * Rows whose minimum is not a believable shelf price are dropped rather than
 * shown greyed out: they are not a gap anyone can close, and they were taking
 * the top slots of the one list on the tablero that is meant to be a to-do
 * list. The count of what was dropped stays, so the list is never quietly
 * shorter than the data. */
function WorstList({ rows }: { rows: ComparisonRow[] }) {
  const usable = rows.filter((r) => !isImplausibleMin(r.min_price, r.avg_price, r.pct_over_min));
  const hidden = rows.length - usable.length;

  if (usable.length === 0) {
    return (
      <p className="px-2 py-6 text-center text-[15px] text-ink-2">
        Ninguna de las diferencias de esta sucursal es comparable: los mínimos publicados no parecen
        precios reales de góndola.
      </p>
    );
  }

  const worst = Math.max(...usable.map((r) => toNumber(r.pct_over_min)).filter(Number.isFinite), 1);

  return (
    <>
      <ul className="divide-y divide-line-soft">
        {usable.map((row) => {
          const pct = toNumber(row.pct_over_min);
          const width = Math.min(100, (pct / worst) * 100);

          return (
            <li key={row.ean}>
              <Link to={`/products/${row.ean}`} className="block rounded-lg px-3 py-2.5 hover:bg-plane">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 flex-1 truncate text-[15px] text-ink">{row.description}</span>
                  <span className="tnum shrink-0 text-[15px] font-medium text-critical">
                    {formatPercent(row.pct_over_min, { signed: true, cap: MAX_CREDIBLE_GAP_PCT })}
                  </span>
                </div>

                <div className="mt-1 flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-plane">
                    <div
                      className="h-full rounded-full bg-critical"
                      style={{ width: `${width}%` }}
                      aria-hidden="true"
                    />
                  </div>
                  <span className="tnum shrink-0 text-[13px] text-muted">
                    {formatCurrency(row.own_price)} vs {formatCurrency(row.min_price)}
                  </span>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>

      {hidden > 0 && (
        <p className="px-3 pt-3 text-[13px] text-muted">
          {hidden} producto{hidden === 1 ? "" : "s"} sin mostrar: el mínimo publicado no parece un
          precio real de góndola.
        </p>
      )}
    </>
  );
}

function ChangesList({ rows }: { rows: ChangeRow[] }) {
  return (
    <ul className="divide-y divide-line-soft">
      {rows.map((row) => {
        const direction = priceDirection(row.delta_pct);
        const style = DIRECTION_STYLE[direction];
        return (
          <li key={row.ean} className="px-3 py-2.5">
            <div className="flex items-baseline justify-between gap-3">
              <Link
                to={`/products/${row.ean}`}
                className="min-w-0 flex-1 truncate text-[15px] text-ink hover:underline"
              >
                {row.description}
              </Link>
              {/* The arrow repeats what the colour says, so the direction of a
                  change never depends on telling red from green. */}
              <span className={`tnum shrink-0 text-[15px] font-medium ${style.className}`}>
                {style.arrow && <span aria-hidden="true">{style.arrow} </span>}
                {formatPercent(row.delta_pct, { signed: true, cap: 999 })}
              </span>
            </div>
            <p className="tnum text-[13px] text-muted">
              {formatCurrency(row.previous_price)} → {formatCurrency(row.price)}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
