import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getComparison, searchProducts } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { IconStore, IconTag } from "../components/icons";
import { Tabs } from "../components/Tabs";
import {
  Badge,
  Button,
  Card,
  CursorPagination,
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
import { useSlow } from "../hooks/useSlow";
import { formatRadius } from "../lib/radius";
import {
  formatCurrency,
  formatPercent,
  isImplausibleMin,
  MAX_CREDIBLE_GAP_PCT,
  toNumber,
} from "../lib/format";

const MIN_QUERY_LENGTH = 2;
const PAGE_SIZE = 50;

/* Every field of ComparisonRow, in the order the table shows them. Prices stay
 * as the strings the API sent -- see lib/csv.ts. */
const COMPARISON_COLUMNS = [
  "ean", "description", "brand", "own_price", "min_price", "avg_price",
  "max_price", "offers", "pct_over_min",
];

type Tab = "mine" | "catalogue";

/* Two jobs that were tangled into one screen. "Mi comparación" is your own
 * assortment priced against the competition -- the thing you open every
 * morning. "Catálogo SEPA" is a lookup over all 79 thousand barcodes, which
 * you reach for occasionally. The tabs say which one you are looking at, so
 * neither has to be inferred from what happens to be on screen. */
export function ProductsPage() {
  const [tab, setTab] = useState<Tab>("mine");

  return (
    <Page
      header={
        <PageHeader
          icon={<IconTag />}
          title="Mis precios"
          description="Tu surtido contra el mínimo de tu radio, y el catálogo unificado del SEPA."
        />
      }
    >
      {tab === "mine" ? (
        <MyComparison tab={tab} onTabChange={setTab} />
      ) : (
        <CatalogueSearch tab={tab} onTabChange={setTab} />
      )}
    </Page>
  );
}

function TabControl({ tab, onTabChange }: { tab: Tab; onTabChange: (t: Tab) => void }) {
  return (
    <Tabs
      label="Vistas de precios"
      value={tab}
      onChange={onTabChange}
      options={[
        { value: "mine", label: "Mi comparación" },
        { value: "catalogue", label: "Catálogo SEPA" },
      ]}
    />
  );
}

function MyComparison({ tab, onTabChange }: { tab: Tab; onTabChange: (t: Tab) => void }) {
  const [q, setQ] = useState("");
  /* The cursor of each page after the first, as they were walked: the page on
   * screen is the last one, and "Anterior" drops it. */
  const [cursors, setCursors] = useState<string[]>([]);
  const debouncedQ = useDebouncedValue(q, 300);
  const { activeBranch } = useActiveBranch();

  useEffect(() => setCursors([]), [debouncedQ, activeBranch]);
  const cursor = cursors.at(-1);

  const key = activeBranch
    ? {
        company_id: activeBranch.company_id,
        banner_id: activeBranch.banner_id,
        branch_id: activeBranch.branch_id,
      }
    : null;

  /* Filtering and paging both happen in the API: the branch has thousands of
   * comparable products, and shipping them all to filter in the browser was
   * never going to hold. No feed_date: the API answers the last complete day
   * of the precompute (or the latest, live) and says which. */
  // The radius is in the key, and the previous page is kept on screen only
  // while the radius is the same: a radius just edited is another answer.
  const radius = activeBranch?.radius_km;
  const comparisonQuery = useQuery({
    queryKey: ["my-comparison", key, radius, debouncedQ, cursor ?? null],
    queryFn: () =>
      getComparison(key!, {
        q: debouncedQ || undefined,
        limit: PAGE_SIZE,
        cursor,
      }),
    enabled: !!key,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2] === radius ? previous : undefined,
    refetchInterval: (query) => (query.state.data?.source === "pending" ? 5_000 : false),
  });

  const slow = useSlow(comparisonQuery.isLoading);
  const pending = comparisonQuery.data?.source === "pending" || slow;
  const rows = comparisonQuery.data?.items ?? [];
  const nextCursor = comparisonQuery.data?.next_cursor ?? null;
  const feedDate = comparisonQuery.data?.feed_date ?? undefined;
  const isLive = comparisonQuery.data?.source === "live";

  return (
    <Card
      scroll
      className="lg:h-full"
      isBusy={rows.length > 0 && comparisonQuery.isFetching}
      title="Mi comparación"
      description={
        activeBranch
          ? `${activeBranch.label || activeBranch.name}, contra la competencia en ${formatRadius(toNumber(activeBranch.radius_km))}.` +
            (isLive ? " Calculada en vivo: el precálculo de esta sucursal todavía no está." : "")
          : undefined
      }
      actions={
        <>
          {/* Paging is server-side here, so this is the page on screen and
              not the whole assortment -- the title says so rather than
              letting someone assume they exported everything. */}
          <ExportCsvButton
            rows={rows}
            columns={COMPARISON_COLUMNS}
            filenameStem="mi-comparacion"
            feedDate={feedDate}
            label="Exportar página"
            title={`Descarga las ${rows.length} filas de esta página, no el surtido completo.`}
          />
          <TabControl tab={tab} onTabChange={onTabChange} />
        </>
      }
      bodyClassName="p-0"
      toolbar={
        <div className="sm:max-w-md">
          <SearchInput
            label="Filtrar mis productos por descripción"
            value={q}
            onChange={setQ}
            placeholder="Filtrar por descripción…"
          />
        </div>
      }
      footer={
        rows.length > 0 ? (
          <CursorPagination
            label="Paginación de mi comparación"
            page={cursors.length}
            pageSize={PAGE_SIZE}
            rowsOnPage={rows.length}
            hasNext={nextCursor !== null}
            onPrevious={() => setCursors((walked) => walked.slice(0, -1))}
            onNext={() => nextCursor && setCursors((walked) => [...walked, nextCursor])}
            isBusy={comparisonQuery.isFetching}
          />
        ) : undefined
      }
    >
      {!key && (
        <EmptyState
            fill
            icon={<IconStore className="size-8" />}
            title="Elegí una sucursal primero"
            description="Esta vista compara el surtido de una sucursal concreta. Seguí una y elegila en el selector del panel izquierdo."
            action={
              <Link to="/branches">
                <Button variant="primary">Ir a mis sucursales</Button>
              </Link>
            }
          />
      )}
      {key && comparisonQuery.isLoading && !slow && (
        <div className="h-full p-4">
          <SkeletonRows fill rowClassName="h-9" />
        </div>
      )}
      {key && comparisonQuery.isError && (
        <div className="p-4">
          <ErrorNotice error={comparisonQuery.error} retry={() => comparisonQuery.refetch()} />
        </div>
      )}
      {key && ((comparisonQuery.data && rows.length === 0) || slow) && (
        <EmptyState
            fill
            variant="quiet"
            title={
              pending
                ? "Calculando la comparación"
                : debouncedQ
                  ? "Sin resultados"
                  : "Todavía no hay comparaciones"
            }
            description={
              pending
                ? "Es la primera vez que se compara esta sucursal con este radio: tarda alrededor de un minuto. La pantalla se actualiza sola."
                : debouncedQ
                  ? "Ningún producto de tu surtido coincide con ese texto."
                  : "Cuando la ingesta cargue precios de la competencia dentro del radio, tu comparación aparece acá."
            }
          />
      )}

      {rows.length > 0 && (
        <TableFrame
          fixed
          caption="Tu precio contra el conjunto competitivo, producto por producto"
          minWidth="56rem"
        >
          <thead>
            <tr>
              <Th>Producto</Th>
              <Th className="w-32">Posición</Th>
              <Th align="right" className="w-28">
                Tu precio
              </Th>
              <Th align="right" className="w-28">
                Mín.
              </Th>
              <Th align="right" className="w-28">
                Prom.
              </Th>
              <Th align="right" className="w-20">
                Ofertas
              </Th>
              <Th align="right" className="w-28">
                Diferencia
              </Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const suspicious = isImplausibleMin(row.min_price, row.avg_price, row.pct_over_min);
              const pct = Number.parseFloat(row.pct_over_min);
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
                  <Td>
                    {suspicious ? (
                      <Badge tone="neutral" icon="?">
                        Sin comparar
                      </Badge>
                    ) : pct > 0 ? (
                      <Badge tone="bad" icon="▲">
                        Más caro
                      </Badge>
                    ) : pct < 0 ? (
                      <Badge tone="good" icon="▼">
                        Más barato
                      </Badge>
                    ) : (
                      <Badge tone="neutral" icon="=">
                        Iguala
                      </Badge>
                    )}
                  </Td>
                  <Td align="right" numeric className="font-medium">
                    {formatCurrency(row.own_price)}
                  </Td>
                  <Td align="right" numeric className={suspicious ? "text-muted" : "text-ink-2"}>
                    {formatCurrency(row.min_price)}
                  </Td>
                  <Td align="right" numeric className="text-ink-2">
                    {formatCurrency(row.avg_price)}
                  </Td>
                  <Td align="right" numeric className="text-muted">
                    {row.offers}
                  </Td>
                  <Td
                    align="right"
                    numeric
                    className={`font-medium ${
                      suspicious ? "text-muted" : pct > 0 ? "text-critical" : "text-good-ink"
                    }`}
                  >
                    {suspicious
                      ? "—"
                      : formatPercent(row.pct_over_min, { signed: true, cap: MAX_CREDIBLE_GAP_PCT })}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </TableFrame>
      )}
    </Card>
  );
}

function CatalogueSearch({ tab, onTabChange }: { tab: Tab; onTabChange: (t: Tab) => void }) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedQ = useDebouncedValue(q, 300);
  const isSearching = debouncedQ.trim().length >= MIN_QUERY_LENGTH;

  useEffect(() => setOffset(0), [debouncedQ]);

  const productsQuery = useQuery({
    queryKey: ["products-search", debouncedQ, offset],
    queryFn: () => searchProducts({ q: debouncedQ, limit: PAGE_SIZE, offset }),
    enabled: isSearching,
    placeholderData: (previous) => previous,
  });

  const rows = productsQuery.data ?? [];

  return (
    <Card
      scroll
      className="lg:h-full"
      isBusy={rows.length > 0 && productsQuery.isFetching}
      title="Catálogo SEPA"
      description="Un mismo código de barras, aunque cada cadena lo describa distinto."
      actions={<TabControl tab={tab} onTabChange={onTabChange} />}
      bodyClassName="p-0"
      toolbar={
        <div className="sm:max-w-md">
          <SearchInput
            label="Buscar producto por descripción o marca"
            value={q}
            onChange={setQ}
            placeholder="Ej: leche entera, coca cola…"
          />
        </div>
      }
      footer={
        isSearching && rows.length > 0 ? (
          <Pagination
            label="Paginación del catálogo"
            offset={offset}
            pageSize={PAGE_SIZE}
            rowsOnPage={rows.length}
            onChange={setOffset}
            isBusy={productsQuery.isFetching}
          />
        ) : undefined
      }
    >
      {!isSearching && (
          <EmptyState
            fill
            variant="quiet"
            icon={<IconTag className="size-8" />}
            title="Buscá en el catálogo unificado"
            description={`Unos 79 mil códigos de barras, enlazados entre cadenas. Escribí al menos ${MIN_QUERY_LENGTH} caracteres. Para ver tus propios precios, usá la pestaña "Mi comparación".`}
          />
      )}
      {isSearching && productsQuery.isLoading && (
        <div className="h-full p-4">
          <SkeletonRows fill rowClassName="h-9" />
        </div>
      )}
      {isSearching && productsQuery.isError && (
        <div className="p-4">
          <ErrorNotice error={productsQuery.error} retry={() => productsQuery.refetch()} />
        </div>
      )}
      {isSearching && productsQuery.data && rows.length === 0 && (
        <EmptyState
            fill
            variant="quiet"
            title="Sin resultados"
            description="Probá con una sola palabra, o con la marca en lugar de la descripción completa."
          />
      )}

      {rows.length > 0 && (
        <TableFrame caption="Productos del catálogo unificado que coinciden con la búsqueda">
          <thead>
            <tr>
              <Th>Producto</Th>
              <Th>Marca</Th>
              <Th align="right">Tamaño</Th>
              <Th align="right">EAN</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((product) => (
              <tr key={product.ean} className="hover:bg-plane">
                <Td>
                  <Link
                    to={`/products/${product.ean}`}
                    className="rounded font-medium text-brand-ink hover:underline"
                  >
                    {product.name_norm}
                  </Link>
                </Td>
                <Td className="text-ink-2">{product.brand_norm || "—"}</Td>
                <Td align="right" numeric className="text-ink-2">
                  {product.size_value ? `${product.size_value} ${product.size_unit ?? ""}` : "—"}
                </Td>
                <Td align="right" numeric className="text-[13px] text-muted">
                  {product.ean}
                </Td>
              </tr>
            ))}
          </tbody>
        </TableFrame>
      )}
    </Card>
  );
}
