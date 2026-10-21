import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  getMyBranches,
  searchBranches,
  subscribeBranch,
  unsubscribeBranch,
  updateMyBranch,
} from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice, errorMessage } from "../components/ErrorNotice";
import { IconStore } from "../components/icons";
import { Tabs } from "../components/Tabs";
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
  SearchInput,
  SkeletonRows,
  TableFrame,
  Td,
  Th,
} from "../components/ui";
import { useActiveBranch } from "../hooks/useActiveBranch";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { Dropdown } from "../components/Dropdown";
import { Modal } from "../components/Modal";
import { RadiusField } from "../components/RadiusField";
import { DEFAULT_RADIUS_KM, formatRadius } from "../lib/radius";
import { PROVINCES, provinceName } from "../lib/provinces";
import { toNumber } from "../lib/format";
import type { Branch, BranchKey, OrgBranch } from "../types";

const PAGE_SIZE = 25;

type Tab = "mine" | "add";

function keyOf(b: BranchKey): string {
  return `${b.company_id}-${b.banner_id}-${b.branch_id}`;
}

/* Following a branch is a one-off; living with the ones you already follow is
 * the recurring job. So the branches you own come first and get a table you
 * can actually operate -- rename, re-radius, unfollow -- and the SEPA search
 * moves behind its own tab. */
export function BranchesPage() {
  const [tab, setTab] = useState<Tab>("mine");
  const myBranchesQuery = useQuery({ queryKey: ["me", "branches"], queryFn: getMyBranches });
  const mine = myBranchesQuery.data ?? [];

  const tabControl = (
    <Tabs
      label="Vistas de sucursales"
      value={tab}
      onChange={setTab}
      options={[
        { value: "mine", label: `Las que sigo${mine.length ? ` (${mine.length})` : ""}` },
        { value: "add", label: "Agregar del SEPA" },
      ]}
    />
  );

  return (
    <Page
      header={
        <PageHeader
          icon={<IconStore />}
          title="Mis sucursales"
          description="Cada sucursal que seguís define, con su radio, contra quién se comparan tus precios."
        />
      }
    >
      {tab === "mine" ? (
        <FollowedBranches
          branches={mine}
          isLoading={myBranchesQuery.isLoading}
          error={myBranchesQuery.isError ? myBranchesQuery.error : null}
          retry={() => myBranchesQuery.refetch()}
          actions={tabControl}
          onAdd={() => setTab("add")}
        />
      ) : (
        <SepaSearch mine={mine} actions={tabControl} />
      )}
    </Page>
  );
}

function FollowedBranches({
  branches,
  isLoading,
  error,
  retry,
  actions,
  onAdd,
}: {
  branches: OrgBranch[];
  isLoading: boolean;
  error: unknown;
  retry: () => void;
  actions: React.ReactNode;
  onAdd: () => void;
}) {
  return (
    <Card
      scroll
      className="lg:h-full"
      title="Las que sigo"
      description="Su radio es lo que decide qué competencia entra en cada comparación."
      actions={actions}
      bodyClassName="p-0"
    >
      {isLoading && (
        <div className="h-full p-4">
          <SkeletonRows fill rowClassName="h-9" />
        </div>
      )}
      {!!error && (
        <div className="p-4">
          <ErrorNotice error={error} retry={retry} />
        </div>
      )}
      {!isLoading && !error && branches.length === 0 && (
        <EmptyState
            fill
            icon={<IconStore className="size-8" />}
            title="Todavía no seguís ninguna sucursal"
            description="Sin al menos una, el tablero y tus precios no tienen contra qué compararse. Elegí una del SEPA y definí su radio."
            action={
              <Button variant="primary" onClick={onAdd}>
                Buscar en el SEPA
              </Button>
            }
          />
      )}

      {branches.length > 0 && (
        <TableFrame caption="Sucursales que seguís, con su etiqueta y su radio" minWidth="52rem">
          <thead>
            <tr>
              <Th>Sucursal</Th>
              <Th>Bandera</Th>
              <Th>Localidad</Th>
              <Th align="right">Radio</Th>
              <Th align="right">
                <span className="sr-only">Acciones</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {branches.map((branch) => (
              <FollowedRow key={keyOf(branch)} branch={branch} />
            ))}
          </tbody>
        </TableFrame>
      )}
    </Card>
  );
}

function FollowedRow({ branch }: { branch: OrgBranch }) {
  const queryClient = useQueryClient();
  const { activeBranch, setActiveBranch } = useActiveBranch();
  const [isEditing, setIsEditing] = useState(false);
  const [label, setLabel] = useState(branch.label ?? "");
  const [radiusKm, setRadiusKm] = useState(toNumber(branch.radius_km));
  const [error, setError] = useState<string | null>(null);

  const isActive = activeBranch ? keyOf(activeBranch) === keyOf(branch) : false;

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["me", "branches"] });
  }

  const save = useMutation({
    mutationFn: () =>
      updateMyBranch(branch, { label: label || undefined, radius_km: radiusKm }),
    onSuccess: (updated) => {
      setIsEditing(false);
      setError(null);
      // The sidebar switcher reads the active branch from storage, so an edit
      // to the one you are looking at has to land there too.
      if (isActive) setActiveBranch(updated);
      refresh();
      // What the tablero and Mis Precios cached is for the old radius.
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["my-comparison"] });
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const remove = useMutation({
    mutationFn: () => unsubscribeBranch(branch),
    onSuccess: () => {
      // Dropping the active branch would leave every screen pointed at nothing;
      // useSyncActiveBranch picks the next one once the list comes back.
      if (isActive) setActiveBranch(null);
      refresh();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <>
      <tr className="hover:bg-plane">
        <Td>
          <Link
            to={`/branches/${branch.company_id}/${branch.banner_id}/${branch.branch_id}`}
            className="rounded font-medium text-brand-ink hover:underline"
          >
            {branch.label || branch.name}
          </Link>
          {isActive && (
            <span className="ml-2 align-middle">
              <Badge tone="info" icon="◉">
                Activa
              </Badge>
            </span>
          )}
        </Td>
        <Td className="text-ink-2">{branch.banner_name}</Td>
        <Td className="text-ink-2">
          {branch.locality}, {provinceName(branch.province)}
        </Td>
        <Td align="right" numeric className="text-ink-2">
          {formatRadius(toNumber(branch.radius_km))}
        </Td>
        <Td align="right">
          <div className="flex justify-end gap-1.5">
            {!isActive && (
              <Button
                size="sm"
                onClick={() => setActiveBranch(branch)}
                aria-label={`Activar ${branch.label || branch.name}`}
              >
                Activar
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => setIsEditing((v) => !v)}
              aria-expanded={isEditing}
              aria-label={`Editar ${branch.label || branch.name}`}
            >
              Editar
            </Button>
            <ConfirmButton
              size="sm"
              pending={remove.isPending}
              confirmLabel="Confirmar: se pierde su comparación"
              onConfirm={() => {
                setError(null);
                remove.mutate();
              }}
            >
              Dejar de seguir
            </ConfirmButton>
          </div>
        </Td>
      </tr>

      <Modal
        isOpen={isEditing}
        onClose={() => setIsEditing(false)}
        title={`Editar ${branch.label || branch.name}`}
        description={`${branch.banner_name} · ${branch.locality}, ${provinceName(branch.province)}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setIsEditing(false)}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setError(null);
                save.mutate();
              }}
              disabled={save.isPending}
            >
              {save.isPending ? "Guardando…" : "Guardar cambios"}
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          <Field label="Etiqueta" hint="Opcional. Cómo querés verla nombrada en el portal.">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder={branch.name}
                className="min-h-11"
              />
            )}
          </Field>

          <div className="border-t border-line-soft pt-5">
            <RadiusField value={radiusKm} onChange={setRadiusKm} />
          </div>

          {error && (
            <p role="alert" className="rounded-lg bg-[#fdeceb] px-3 py-2 text-[15px] text-critical">
              {error}
            </p>
          )}
        </div>
      </Modal>
    </>
  );
}

function SepaSearch({ mine, actions }: { mine: OrgBranch[]; actions: React.ReactNode }) {
  const [q, setQ] = useState("");
  const [province, setProvince] = useState("");
  const [offset, setOffset] = useState(0);
  const debouncedQ = useDebouncedValue(q, 300);
  const queryClient = useQueryClient();

  // A new search starts at the first page. Without this, typing while on page
  // four silently shows the fourth page of the new results.
  useEffect(() => setOffset(0), [debouncedQ, province]);

  const branchesQuery = useQuery({
    queryKey: ["branches-search", debouncedQ, province, offset],
    queryFn: () =>
      searchBranches({
        q: debouncedQ || undefined,
        province: province || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    // Keeps the current page on screen while the next one loads, instead of
    // collapsing to a skeleton on every page turn.
    placeholderData: (previous) => previous,
  });

  const subscribedKeys = new Set(mine.map(keyOf));
  const results = branchesQuery.data ?? [];
  const isFiltered = !!debouncedQ || !!province;

  return (
    <Card
      scroll
      className="lg:h-full"
      isBusy={results.length > 0 && branchesQuery.isFetching}
      title="Agregar del SEPA"
      description="Todas las sucursales que publican precios. Seguí las que quieras comparar."
      actions={actions}
      bodyClassName="p-0"
      toolbar={
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <SearchInput
              label="Buscar sucursal por nombre, bandera o localidad"
              value={q}
              onChange={setQ}
              placeholder="Nombre, bandera o localidad…"
            />
          </div>
          <Field label="Provincia" className="w-full sm:w-64">
            {({ id }) => (
              <Dropdown
                id={id}
                value={province}
                onChange={setProvince}
                options={[
                  { value: "", label: "Todo el país" },
                  ...PROVINCES.map((p) => ({ value: p.code, label: p.name })),
                ]}
              />
            )}
          </Field>
          {isFiltered && (
            <Button
              onClick={() => {
                setQ("");
                setProvince("");
              }}
            >
              Limpiar
            </Button>
          )}
        </div>
      }
      footer={
        <Pagination
          label="Paginación de sucursales"
          offset={offset}
          pageSize={PAGE_SIZE}
          rowsOnPage={results.length}
          onChange={setOffset}
          isBusy={branchesQuery.isFetching}
        />
      }
    >
      {branchesQuery.isLoading && (
        <div className="h-full p-4">
          <SkeletonRows fill rowClassName="h-9" />
        </div>
      )}
      {branchesQuery.isError && (
        <div className="p-4">
          <ErrorNotice error={branchesQuery.error} retry={() => branchesQuery.refetch()} />
        </div>
      )}
      {branchesQuery.data && results.length === 0 && (
        <EmptyState
            fill
            variant="quiet"
            title="Sin resultados"
            description="Probá con menos palabras, o buscá en todo el país en lugar de una sola provincia."
          />
      )}

      {results.length > 0 && (
        <TableFrame caption="Sucursales del SEPA que coinciden con la búsqueda" minWidth="44rem">
          <thead>
            <tr>
              <Th>Bandera</Th>
              <Th>Sucursal</Th>
              <Th>Localidad</Th>
              <Th>Provincia</Th>
              <Th align="right">
                <span className="sr-only">Acciones</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {results.map((branch) => (
              <SepaRow
                key={keyOf(branch)}
                branch={branch}
                isSubscribed={subscribedKeys.has(keyOf(branch))}
                onSubscribed={() => queryClient.invalidateQueries({ queryKey: ["me", "branches"] })}
              />
            ))}
          </tbody>
        </TableFrame>
      )}
    </Card>
  );
}

function SepaRow({
  branch,
  isSubscribed,
  onSubscribed,
}: {
  branch: Branch;
  isSubscribed: boolean;
  onSubscribed: () => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [radiusKm, setRadiusKm] = useState<number>(DEFAULT_RADIUS_KM);
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      subscribeBranch({
        company_id: branch.company_id,
        banner_id: branch.banner_id,
        branch_id: branch.branch_id,
        radius_km: radiusKm,
        label: label || undefined,
      }),
    onSuccess: () => {
      setIsOpen(false);
      onSubscribed();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <>
      <tr className="hover:bg-plane">
        <Td className="font-medium">{branch.banner_name}</Td>
        <Td>
          <Link
            to={`/branches/${branch.company_id}/${branch.banner_id}/${branch.branch_id}`}
            className="rounded text-brand-ink hover:underline"
          >
            {branch.name}
          </Link>
        </Td>
        <Td className="text-ink-2">{branch.locality}</Td>
        <Td className="text-ink-2">{provinceName(branch.province)}</Td>
        <Td align="right">
          {isSubscribed ? (
            <Badge tone="good" icon="✓">
              La seguís
            </Badge>
          ) : (
            <Button
              size="sm"
              onClick={() => setIsOpen(true)}
              aria-label={`Seguir ${branch.banner_name} ${branch.name}`}
            >
              Seguir
            </Button>
          )}
        </Td>
      </tr>

      {/* A dialog, not a row that unfolds inside the table. The radius needs
          room to be explained, and a table row is the one place on the page
          that has none. */}
      <Modal
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        title={`Seguir ${branch.name}`}
        description={`${branch.banner_name} · ${branch.locality}, ${provinceName(branch.province)}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setIsOpen(false)}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setError(null);
                mutation.mutate();
              }}
              disabled={mutation.isPending}
            >
              {mutation.isPending ? "Guardando…" : "Seguir sucursal"}
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          <Field label="Etiqueta" hint="Opcional. Cómo querés verla nombrada en el portal.">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder={branch.name}
                className="min-h-11"
              />
            )}
          </Field>

          <div className="border-t border-line-soft pt-5">
            <RadiusField value={radiusKm} onChange={setRadiusKm} />
          </div>

          {error && (
            <p role="alert" className="rounded-lg bg-[#fdeceb] px-3 py-2 text-[15px] text-critical">
              {error}
            </p>
          )}
        </div>
      </Modal>
    </>
  );
}
