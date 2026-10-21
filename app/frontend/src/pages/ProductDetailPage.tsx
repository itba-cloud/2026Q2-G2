import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getProduct, getProductCompetitorPrices, getProductHistory } from "../api/endpoints";
import { PriceHistoryChart } from "../components/charts/PriceHistoryChart";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { IconChevronRight, IconTag } from "../components/icons";
import {
  Badge,
  Button,
  Card,
  Page,
  PageHeader,
  Skeleton,
  SkeletonRows,
  StatTile,
} from "../components/ui";
import { useActiveBranch } from "../hooks/useActiveBranch";
import {
  DIRECTION_STYLE,
  formatCurrency,
  formatDistance,
  formatPercent,
  MAX_CREDIBLE_GAP_PCT,
  priceDirection,
  toNumber,
} from "../lib/format";
import { formatRadius } from "../lib/radius";
import type { PricePoint } from "../types";

const HISTORY_DAYS = 60;

// Both of these hold their whole result set in memory -- the history is capped
// at HISTORY_DAYS and the competitor list at 50 -- so their exports are the
// complete thing, not a page of it.
const HISTORY_COLUMNS = ["feed_date", "price"];
const COMPETITOR_COLUMNS = [
  "banner_name", "name", "locality", "distance_m", "price",
  "company_id", "banner_id", "branch_id",
];

export function ProductDetailPage() {
  const { ean = "" } = useParams<{ ean: string }>();
  const { activeBranch } = useActiveBranch();

  const productQuery = useQuery({ queryKey: ["product", ean], queryFn: () => getProduct(ean) });

  const historyKey = activeBranch
    ? {
        company_id: activeBranch.company_id,
        banner_id: activeBranch.banner_id,
        branch_id: activeBranch.branch_id,
      }
    : null;

  const historyQuery = useQuery({
    queryKey: ["product-history", historyKey, ean],
    queryFn: () => getProductHistory(historyKey!, ean, HISTORY_DAYS),
    enabled: !!historyKey,
  });

  const competitorPricesQuery = useQuery({
    queryKey: ["product-competitor-prices", historyKey, ean],
    queryFn: () => getProductCompetitorPrices(historyKey!, ean, { limit: 50 }),
    enabled: !!historyKey,
  });

  if (productQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-2/3 max-w-lg" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (productQuery.isError) return <ErrorNotice error={productQuery.error} retry={() => productQuery.refetch()} />;
  if (!productQuery.data) return null;

  const product = productQuery.data;
  const points = historyQuery.data ?? [];
  const competitorPrices = competitorPricesQuery.data ?? [];
  // The last point of the history is this branch's own current price, which is
  // what each competitor's price is measured against.
  const own = points.length > 0 ? toNumber(points[points.length - 1].price) : null;
  /* A product has two faces here, and which one carries the page depends on
     whether you stock it. If you do, your own history is the story and the
     competition is context. If you do not, your side has nothing to say and
     the competition is the whole point -- so the two swap widths instead of
     leaving a dead panel taking two thirds of the screen. */
  const isStocked = own !== null;

  return (
    <Page
      header={
        <div className="space-y-3">
          <nav aria-label="Volver">
            <Link
              to="/products"
              className="-ml-2 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-[15px] font-medium text-ink-2 hover:bg-plane hover:text-ink"
            >
              <span aria-hidden="true">←</span> Mis precios
            </Link>
          </nav>
          <PageHeader
            icon={<IconTag />}
            title={product.description}
            description={[product.brand || "Sin marca", formatPresentation(product.presentation)]
              .filter(Boolean)
              .join(" · ")}
            actions={<Badge tone="neutral">EAN {product.ean}</Badge>}
          />
        </div>
      }
    >
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-rows-[auto_minmax(0,1fr)]">
        {points.length > 0 && <HistoryStats points={points} />}

        <div className="grid min-w-0 grid-cols-1 gap-4 lg:min-h-0 xl:grid-cols-3">
        <Card
          scroll
          className={isStocked ? "xl:col-span-2" : ""}
          title="En tu sucursal"
          actions={
            points.length > 0 ? (
              <ExportCsvButton
                rows={points}
                columns={HISTORY_COLUMNS}
                filenameStem={`historial-${ean}`}
                title={`Descarga los ${points.length} puntos del historial completo.`}
              />
            ) : undefined
          }
          description={
            activeBranch
              ? isStocked
                ? `${activeBranch.label || activeBranch.name}, últimos ${HISTORY_DAYS} días`
                : `${activeBranch.label || activeBranch.name} no lo publica`
              : undefined
          }
        >
          {!activeBranch ? (
            <EmptyState
              title="No hay una sucursal activa"
              description="El historial de precio es siempre el de una sucursal concreta. Elegí una desde el tablero o desde su propia ficha."
              action={
                <Link to="/">
                  <Button variant="primary">
                    Ir al tablero
                    <IconChevronRight className="size-4" />
                  </Button>
                </Link>
              }
            />
          ) : historyQuery.isLoading ? (
            <Skeleton className="h-[17rem] w-full" />
          ) : historyQuery.isError ? (
            <ErrorNotice error={historyQuery.error} retry={() => historyQuery.refetch()} />
          ) : points.length === 0 ? (
            <EmptyState
              fill
              variant="quiet"
              icon={<IconTag className="size-8" />}
              title="No lo tenés a la venta"
              description="Esta sucursal no publicó este código de barras en las ingestas cargadas. A la derecha está lo que cobran las que sí lo venden."
            />
          ) : (
            <PriceHistoryChart points={points} />
          )}
        </Card>

        {/* Was "how each chain names this barcode", which is trivia. What you
            want standing in front of the shelf is what the shop down the
            street charges for it. */}
        <Card
          scroll
          className={isStocked ? "" : "xl:col-span-2"}
          bodyClassName="p-0"
          title="Precio en otras cadenas"
          actions={
            competitorPrices.length > 0 ? (
              <ExportCsvButton
                rows={competitorPrices}
                columns={COMPETITOR_COLUMNS}
                filenameStem={`competencia-${ean}`}
                title={`Descarga los ${competitorPrices.length} competidores del radio.`}
              />
            ) : undefined
          }
          description={
            activeBranch
              ? `Dentro de ${formatRadius(toNumber(activeBranch.radius_km))} de ${activeBranch.label || activeBranch.name}, del más barato al más caro.`
              : "Elegí una sucursal activa para ver a su competencia."
          }
        >
          {!activeBranch && (
            <EmptyState
              fill
              variant="quiet"
              title="No hay una sucursal activa"
              description="Los precios de la competencia son siempre los del radio de una sucursal concreta."
            />
          )}
          {activeBranch && competitorPricesQuery.isLoading && (
            <div className="h-full p-4">
              <SkeletonRows fill rowClassName="h-14" />
            </div>
          )}
          {activeBranch && competitorPricesQuery.isError && (
            <div className="p-4">
              <ErrorNotice
                error={competitorPricesQuery.error}
                retry={() => competitorPricesQuery.refetch()}
              />
            </div>
          )}
          {activeBranch && competitorPricesQuery.data && competitorPrices.length === 0 && (
            <EmptyState
              fill
              variant="quiet"
              title="Nadie más lo publica"
              description="Ningún competidor dentro del radio publicó este código de barras en esta ingesta."
            />
          )}
          {competitorPrices.length > 0 && (
            <ul className="divide-y divide-line-soft">
              {competitorPrices.map((c) => {
                const theirs = toNumber(c.price);
                const gap = own !== null && theirs > 0 ? ((own - theirs) / theirs) * 100 : NaN;
                const direction = priceDirection(Number.isNaN(gap) ? null : gap);
                // Said from the competitor's side, in words: "+211 % vs. vos"
                // on a competitor three times cheaper read the other way round.
                const theirGap = own ? ((theirs - own) / own) * 100 : NaN;
                const style = DIRECTION_STYLE[direction];
                return (
                  <li
                    key={`${c.company_id}-${c.banner_id}-${c.branch_id}`}
                    className="flex items-center gap-3 px-5 py-2.5"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] text-ink">{c.name}</span>
                      <span className="block truncate text-[13px] text-muted">
                        {c.banner_name} · {formatDistance(c.distance_m)}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="tnum block text-[15px] font-medium text-ink">
                        {formatCurrency(c.price)}
                      </span>
                      {!Number.isNaN(gap) && (
                        <span className={`tnum block text-[13px] ${style.className}`}>
                          {direction === "flat"
                            ? "igual al tuyo"
                            : `${formatPercent(Math.abs(theirGap), { cap: MAX_CREDIBLE_GAP_PCT })} ${theirGap < 0 ? "más barato" : "más caro"} que vos`}
                        </span>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        </div>
      </div>
    </Page>
  );
}

/** The three numbers the chart is there to answer, stated outright so nobody
 * has to read them off the line. */
function HistoryStats({ points }: { points: PricePoint[] }) {
  const prices = points.map((p) => toNumber(p.price)).filter((p) => !Number.isNaN(p));
  if (prices.length === 0) return null;

  const first = prices[0];
  const last = prices[prices.length - 1];
  const change = first > 0 ? ((last - first) / first) * 100 : NaN;
  const direction = priceDirection(Number.isNaN(change) ? null : change);
  const style = DIRECTION_STYLE[direction];

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile label="Precio actual" value={formatCurrency(last)} />
      <StatTile
        label="Variación del período"
        value={
          Number.isNaN(change) ? (
            "—"
          ) : (
            <span>
              {style.arrow && <span aria-hidden="true">{style.arrow} </span>}
              {formatPercent(change, { signed: true, cap: 999 })}
            </span>
          )
        }
        tone={direction === "up" ? "bad" : direction === "down" ? "good" : "neutral"}
      />
      <StatTile label="Mínimo del período" value={formatCurrency(Math.min(...prices))} />
      <StatTile label="Máximo del período" value={formatCurrency(Math.max(...prices))} />
    </div>
  );
}

/** "1.00 unidad" -> "1 unidad", "0.50 kg" -> "0,5 kg": the SEPA sends the
 * quantity with a decimal point and trailing zeros. */
function formatPresentation(presentation: string | null | undefined): string | null {
  if (!presentation) return null;
  return presentation.replace(/\d+(?:\.\d+)?/, (n) =>
    Number(n).toLocaleString("es-AR", { maximumFractionDigits: 3 }),
  );
}
