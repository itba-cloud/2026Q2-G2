import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  getBranch,
  getCompetitors,
  getMyBranches,
  subscribeBranch,
  unsubscribeBranch,
  updateMyBranch,
} from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice, errorMessage } from "../components/ErrorNotice";
import { ExportCsvButton } from "../components/ExportCsvButton";
import { IconStore } from "../components/icons";
import { CompetitorsMap } from "../components/map/CompetitorsMap";
import { RadiusField } from "../components/RadiusField";
import {
  Badge,
  Button,
  Card,
  ConfirmButton,
  Field,
  Input,
  Page,
  PageHeader,
  Pagination,
  Skeleton,
  SkeletonRows,
} from "../components/ui";
import { useActiveBranch } from "../hooks/useActiveBranch";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { provinceName } from "../lib/provinces";
import { formatDistance, toNumber } from "../lib/format";
import type { BranchKey } from "../types";

/** The list pages at this. The map does not page, so it takes MAP_PINS in one
 * request instead -- 500 is both the API's ceiling for this endpoint and about
 * where drawing Leaflet markers stops being free. A radius holding more than
 * that says so on the card rather than quietly dropping the far ones. */
const PAGE_SIZE = 50;
const MAP_PINS = 500;

// The export takes what the map already fetched, so it is the whole radius
// wherever the radius fits in MAP_PINS -- which is every branch in this
// dataset bar the densest few -- and its most-central 500 otherwise. Either
// way the button's `title` says which, so nobody guesses.
const COMPETITOR_COLUMNS = [
  "banner_name", "name", "locality", "province", "distance_m",
  "company_id", "banner_id", "branch_id", "latitude", "longitude",
];

export function BranchDetailPage() {
  const params = useParams<{ companyId: string; bannerId: string; branchId: string }>();
  const key: BranchKey = {
    company_id: Number(params.companyId),
    banner_id: Number(params.bannerId),
    branch_id: params.branchId ?? "",
  };
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { activeBranch, setActiveBranch } = useActiveBranch();

  const branchQuery = useQuery({ queryKey: ["branch", key], queryFn: () => getBranch(key) });
  const myBranchesQuery = useQuery({ queryKey: ["me", "branches"], queryFn: getMyBranches });

  const unfollow = useMutation({
    mutationFn: () => unsubscribeBranch(key),
    onSuccess: () => {
      // Leaving the active branch pointing at something no longer followed
      // would break every other screen, so it is cleared and useSyncActiveBranch
      // picks the next one once the list comes back.
      if (isActive) setActiveBranch(null);
      queryClient.invalidateQueries({ queryKey: ["me", "branches"] });
      // Nothing left to look at here.
      navigate("/branches", { replace: true });
    },
  });

  const subscription = myBranchesQuery.data?.find(
    (b) =>
      b.company_id === key.company_id &&
      b.banner_id === key.banner_id &&
      b.branch_id === key.branch_id,
  );
  const isSubscribed = !!subscription;
  const isActive =
    activeBranch?.company_id === key.company_id &&
    activeBranch?.banner_id === key.banner_id &&
    activeBranch?.branch_id === key.branch_id;

  if (branchQuery.isLoading || myBranchesQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-1/2 max-w-md" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-80 w-full" />
      </div>
    );
  }
  if (branchQuery.isError) {
    return <ErrorNotice error={branchQuery.error} retry={() => branchQuery.refetch()} />;
  }
  if (!branchQuery.data) return null;

  const branch = branchQuery.data;

  return (
    <Page
      header={
        <div className="space-y-3">
          <nav aria-label="Volver">
            <Link
              to="/branches"
              className="-ml-2 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-[15px] font-medium text-ink-2 hover:bg-plane hover:text-ink"
            >
              <span aria-hidden="true">←</span> Mis sucursales
            </Link>
          </nav>
          <PageHeader
            icon={<IconStore />}
            title={subscription?.label || branch.name}
            description={`${branch.banner_name} · ${branch.locality}, ${provinceName(branch.province)}`}
            actions={
              isSubscribed && (
                <>
                  {isActive ? (
                    <Badge tone="info" icon="◉">
                      Sucursal activa
                    </Badge>
                  ) : (
                    <Button onClick={() => setActiveBranch(subscription!)}>
                      Marcar como activa
                    </Button>
                  )}
                  <ConfirmButton
                    pending={unfollow.isPending}
                    confirmLabel="Confirmar: se pierde su comparación"
                    onConfirm={() => unfollow.mutate()}
                  >
                    Dejar de seguir
                  </ConfirmButton>
                </>
              )
            }
          />
        </div>
      }
    >
      {!isSubscribed ? (
        <SubscribePrompt
          branchKey={key}
          onSubscribed={() => queryClient.invalidateQueries({ queryKey: ["me", "branches"] })}
        />
      ) : (
        <SubscribedBranchView
          branch={branch}
          branchKey={key}
          subscriptionRadiusKm={toNumber(subscription!.radius_km)}
        />
      )}
    </Page>
  );
}

function SubscribePrompt({
  branchKey,
  onSubscribed,
}: {
  branchKey: BranchKey;
  onSubscribed: () => void;
}) {
  const [radiusKm, setRadiusKm] = useState(3);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => subscribeBranch({ ...branchKey, radius_km: radiusKm }),
    onSuccess: onSubscribed,
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <Card>
      <div className="flex flex-col items-start gap-4">
        <div className="flex items-start gap-3">
          <IconStore className="mt-0.5 size-6 text-muted" />
          <div>
            <p className="text-[15px] font-medium text-ink">Todavía no seguís esta sucursal</p>
            <p className="mt-1 max-w-xl text-[15px] text-ink-2">
              Seguila para ver su conjunto competitivo en el mapa, la comparación producto a producto
              y los cambios de precio. El radio define qué competencia entra: podés cambiarlo después
              en cualquier momento.
            </p>
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            mutation.mutate();
          }}
          className="flex flex-wrap items-end gap-3"
        >
          <Field label="Radio de competencia" hint="Entre 0,1 y 10 km" className="w-40">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                type="number"
                min={0.1}
                max={10}
                step={0.1}
                value={radiusKm}
                onChange={(e) => setRadiusKm(Number(e.target.value))}
              />
            )}
          </Field>
          <Button type="submit" variant="primary" disabled={mutation.isPending}>
            {mutation.isPending ? "Guardando…" : "Seguir esta sucursal"}
          </Button>
        </form>

        {error && (
          <p role="alert" className="text-[15px] text-critical">
            {error}
          </p>
        )}
      </div>
    </Card>
  );
}

/* Was a third dashboard: it repeated the tablero's metrics and Mis precios'
 * comparison table, so the same numbers lived in three places. What only this
 * screen can answer is "who am I actually up against, and how wide is the net"
 * -- the radius and the branches it catches. Prices are one click away, on the
 * screens that own them. */
function SubscribedBranchView({
  branch,
  branchKey,
  subscriptionRadiusKm,
}: {
  branch: { name: string; latitude: number; longitude: number };
  branchKey: BranchKey;
  subscriptionRadiusKm: number;
}) {
  const [radiusKm, setRadiusKm] = useState(subscriptionRadiusKm);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const debouncedRadius = useDebouncedValue(radiusKm, 250);
  const queryClient = useQueryClient();

  // Moving the radius changes what the set *is*, so page one is the only
  // honest place to land. Without this, widening the net while on page four
  // shows page four of a different set.
  useEffect(() => setOffset(0), [debouncedRadius]);

  // Two reads of the same set, because the map and the list want different
  // amounts of it. The map draws every pin at once and has no "siguiente", so
  // it takes a capped slice; the list pages. Both are bounded -- before this
  // they were one unbounded request feeding both.
  const mapQuery = useQuery({
    queryKey: ["competitors", "map", branchKey, debouncedRadius],
    queryFn: () => getCompetitors(branchKey, { radius_km: debouncedRadius, limit: MAP_PINS }),
    enabled: !!debouncedRadius,
  });

  const listQuery = useQuery({
    queryKey: ["competitors", "list", branchKey, debouncedRadius, offset],
    queryFn: () =>
      getCompetitors(branchKey, { radius_km: debouncedRadius, limit: PAGE_SIZE, offset }),
    enabled: !!debouncedRadius,
    // Keeps the current page on screen while the next one loads.
    placeholderData: (previous) => previous,
  });

  const saveDefaultRadius = useMutation({
    mutationFn: () => updateMyBranch(branchKey, { radius_km: radiusKm }),
    onSuccess: () => {
      setSaveMessage("Guardado como radio por defecto.");
      queryClient.invalidateQueries({ queryKey: ["me", "branches"] });
    },
    onError: (err) => setSaveMessage(errorMessage(err)),
  });

  const pins = mapQuery.data?.items ?? [];
  const page = listQuery.data?.items ?? [];
  // Whichever query answered first. It is the same number from either -- the
  // radius' total, not the page's length -- so a count on screen does not
  // flicker while the other half loads.
  const total = listQuery.data?.total ?? mapQuery.data?.total ?? 0;
  // The nearest branch is the first row of a set sorted by distance, and page
  // one always holds it.
  const nearest = pins.length ? pins[0].distance_m : null;

  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-rows-[auto_minmax(0,1fr)]">
      <div className="flex flex-col gap-5 rounded-xl border border-line bg-surface p-4 lg:flex-row lg:items-start lg:gap-8">
        <div className="min-w-0 flex-1">
          <RadiusField
            value={radiusKm}
            onChange={setRadiusKm}
            competitorCount={total}
            isCountLoading={mapQuery.isLoading && listQuery.isLoading}
            nearestLabel={nearest !== null ? formatDistance(nearest) : undefined}
          />
        </div>

        <div className="flex shrink-0 flex-col gap-2 lg:w-56">
          <Button
            onClick={() => saveDefaultRadius.mutate()}
            disabled={saveDefaultRadius.isPending || radiusKm === subscriptionRadiusKm}
          >
            Guardar radio por defecto
          </Button>
          {saveMessage && (
            <p role="status" className="text-[13px] text-muted">
              {saveMessage}
            </p>
          )}
          {/* Where the prices actually live, so this screen does not have to
              grow its own copy of them again. */}
          <Link to="/products" className="mt-1">
            <Button className="w-full">Ver mis precios acá</Button>
          </Link>
          <Link to="/compare">
            <Button className="w-full">Comparar contra otras</Button>
          </Link>
        </div>
      </div>

      <div className="grid min-w-0 grid-cols-1 gap-4 lg:min-h-0 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card
          scroll
          className="lg:h-full"
          title="Dónde caen"
          description={
            total > MAP_PINS
              ? `Las ${MAP_PINS} más cercanas de ${total}. Achicá el radio para verlas todas.`
              : "Cada punto es una sucursal de otra cadena dentro del radio."
          }
          bodyClassName="p-2"
        >
          <div className="h-[22rem] lg:h-full">
            {mapQuery.isLoading && <Skeleton className="h-full w-full" />}
            {mapQuery.isError && (
              <div className="p-3">
                <ErrorNotice error={mapQuery.error} retry={() => mapQuery.refetch()} />
              </div>
            )}
            {mapQuery.data && (
              <CompetitorsMap
                ownName={branch.name}
                ownLatitude={branch.latitude}
                ownLongitude={branch.longitude}
                competitors={pins}
                radiusKm={debouncedRadius}
              />
            )}
          </div>
        </Card>

        {/* The map answers "where"; this answers "who", which is the half you
            can read without squinting at pins. */}
        <Card
          scroll
          className="lg:h-full"
          title={`Quiénes son (${total})`}
          description="Ordenadas por distancia."
          isBusy={page.length > 0 && listQuery.isFetching}
          actions={
            <ExportCsvButton
              rows={pins}
              columns={COMPETITOR_COLUMNS}
              filenameStem={`competencia-${branchKey.company_id}-${branchKey.banner_id}-${branchKey.branch_id}`}
              title={
                total > MAP_PINS
                  ? `Descarga las ${MAP_PINS} sucursales más cercanas, de las ${total} del radio de ${debouncedRadius} km.`
                  : `Descarga las ${total} sucursales del radio de ${debouncedRadius} km.`
              }
            />
          }
          bodyClassName="p-0"
          footer={
            total > 0 ? (
              <Pagination
                label="Paginación de competidores"
                offset={offset}
                pageSize={PAGE_SIZE}
                rowsOnPage={page.length}
                total={total}
                onChange={setOffset}
                isBusy={listQuery.isFetching}
              />
            ) : undefined
          }
        >
          {listQuery.isLoading && (
            <div className="h-full p-4">
              <SkeletonRows fill rowClassName="h-14" />
            </div>
          )}
          {listQuery.isError && (
            <div className="p-3">
              <ErrorNotice error={listQuery.error} retry={() => listQuery.refetch()} />
            </div>
          )}
          {listQuery.data && total === 0 && (
            <EmptyState
              fill
              variant="quiet"
              icon={<IconStore className="size-8" />}
              title="Ningún competidor en este radio"
              description="Ampliá el radio para que entren más sucursales en la comparación."
            />
          )}
          {page.length > 0 && (
            <ul className="divide-y divide-line-soft">
              {page.map((c) => (
                <li key={`${c.company_id}-${c.banner_id}-${c.branch_id}`}>
                  <Link
                    to={`/branches/${c.company_id}/${c.banner_id}/${c.branch_id}`}
                    className="flex items-center gap-3 px-5 py-2.5 hover:bg-plane"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] font-medium text-ink">
                        {c.name}
                      </span>
                      <span className="block truncate text-[13px] text-muted">
                        {c.banner_name} · {c.locality}
                      </span>
                    </span>
                    <Badge tone="neutral">{formatDistance(c.distance_m)}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
