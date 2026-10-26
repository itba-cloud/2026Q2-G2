import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  createRule,
  deleteRule,
  getAlerts,
  getCompetitors,
  getMyBranches,
  getRules,
  getUnreadAlertCount,
  markAlertRead,
  markAllAlertsRead,
  replaceRuleProducts,
  setRuleEnabled,
} from "../api/endpoints";
import { Dropdown } from "../components/Dropdown";
import { EmptyState } from "../components/EmptyState";
import { Modal } from "../components/Modal";
import { RadiusField } from "../components/RadiusField";
import { ErrorNotice, errorMessage } from "../components/ErrorNotice";
import { IconBell, IconClose } from "../components/icons";
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
} from "../components/ui";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import {
  DIRECTION_STYLE,
  formatCurrency,
  formatDate,
  formatDateTime,
  formatDistance,
  formatPercent,
  priceDirection,
  toNumber,
} from "../lib/format";
import { DEFAULT_RADIUS_KM, formatRadius } from "../lib/radius";
import type { BranchKey, Competitor, OrgBranch, Rule } from "../types";

const PAGE_SIZE = 20;
/** How many neighbours the target picker offers at once -- same reasoning as
 * /compare's rival picker: it is a search box, not a table. */
const TARGET_ROWS = 60;

type Tab = "alerts" | "rules";

export function AlertsPage() {
  const [tab, setTab] = useState<Tab>("alerts");
  const [openCreateForm, setOpenCreateForm] = useState(false);

  const unreadQuery = useQuery({
    queryKey: ["alerts", "unread-count"],
    queryFn: getUnreadAlertCount,
  });
  const rulesQuery = useQuery({ queryKey: ["rules"], queryFn: getRules });

  const rules = rulesQuery.data ?? [];
  const active = rules.filter((r) => r.enabled).length;

  function goCreateRule() {
    setTab("rules");
    setOpenCreateForm(true);
  }

  return (
    <Page
      header={
        <PageHeader
          icon={<IconBell />}
          title="Alertas"
          description="Tus reglas vigilan a la competencia; cuando una se cumple, aparece acá."
          actions={
            /* A real tablist, so arrow keys and screen readers behave as
               expected instead of this being two buttons that swap a div. */
            <div
              role="tablist"
              aria-label="Vistas de alertas"
              className="flex rounded-lg bg-plane p-0.5"
            >
              {(["alerts", "rules"] as const).map((t) => (
                <button
                  key={t}
                  role="tab"
                  id={`tab-${t}`}
                  aria-selected={tab === t}
                  aria-controls={`panel-${t}`}
                  tabIndex={tab === t ? 0 : -1}
                  onClick={() => setTab(t)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                      setTab(t === "alerts" ? "rules" : "alerts");
                    }
                  }}
                  className={`min-h-9 rounded-[0.4rem] px-3.5 text-[15px] font-medium transition-colors ${
                    tab === t
                      ? "bg-surface text-ink shadow-[0_1px_2px_rgba(20,20,15,0.08)]"
                      : "text-ink-2 hover:text-ink"
                  }`}
                >
                  {t === "alerts" ? "Feed" : "Reglas"}
                </button>
              ))}
            </div>
          }
        />
      }
    >
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-rows-[auto_minmax(0,1fr)]">
        {/* Three numbers and the one action. Even with an empty feed the page
            now says what state the vigilancia is in, instead of showing a bell
            and a paragraph of excuses. */}
        <div className="flex flex-wrap items-center gap-3">
          <Chip label="Sin leer" value={unreadQuery.data?.count ?? 0} tone={unreadQuery.data?.count ? "bad" : "neutral"} />
          <Chip label="Reglas activas" value={active} tone={active ? "good" : "neutral"} />
          <Chip label="Reglas pausadas" value={rules.length - active} />
          <Button variant="primary" className="ml-auto" onClick={goCreateRule}>
            Nueva regla
          </Button>
        </div>

        <div
          role="tabpanel"
          id={`panel-${tab}`}
          aria-labelledby={`tab-${tab}`}
          className="min-h-0"
        >
          {tab === "alerts" ? (
            <AlertsFeed onCreateRule={goCreateRule} hasRules={rules.length > 0} />
          ) : (
            <RulesManager isFormOpen={openCreateForm} setIsFormOpen={setOpenCreateForm} />
          )}
        </div>
      </div>
    </Page>
  );
}

function Chip({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "good" | "bad";
}) {
  const valueTone = { neutral: "text-ink", good: "text-good-ink", bad: "text-critical" }[tone];
  return (
    <div className="flex items-baseline gap-2 rounded-xl border border-line bg-surface px-3.5 py-2">
      <span className={`tnum text-lg font-semibold leading-none ${valueTone}`}>{value}</span>
      <span className="text-[13px] text-muted">{label}</span>
    </div>
  );
}

function AlertsFeed({ onCreateRule, hasRules }: { onCreateRule: () => void; hasRules: boolean }) {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const queryClient = useQueryClient();

  useEffect(() => setOffset(0), [unreadOnly]);

  const alertsQuery = useQuery({
    queryKey: ["alerts-feed", unreadOnly, offset],
    queryFn: () => getAlerts({ unread_only: unreadOnly, limit: PAGE_SIZE, offset }),
    placeholderData: (previous) => previous,
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["alerts-feed"] });
    queryClient.invalidateQueries({ queryKey: ["alerts", "unread-count"] });
  }

  const markReadMutation = useMutation({ mutationFn: markAlertRead, onSuccess: invalidate });
  const markAllMutation = useMutation({ mutationFn: markAllAlertsRead, onSuccess: invalidate });

  const alerts = alertsQuery.data ?? [];

  return (
    <Card
      scroll
      className="lg:h-full"
      isBusy={alerts.length > 0 && alertsQuery.isFetching}
      title={unreadOnly ? "Alertas sin leer" : "Todas las alertas"}
      actions={
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-[15px] text-ink-2">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.target.checked)}
              className="size-4 accent-brand"
            />
            Solo no leídas
          </label>
          <Button size="sm" onClick={() => markAllMutation.mutate()} disabled={markAllMutation.isPending}>
            Marcar todas como leídas
          </Button>
        </div>
      }
      footer={
        alerts.length > 0 ? (
          <Pagination
            label="Paginación de alertas"
            offset={offset}
            pageSize={PAGE_SIZE}
            rowsOnPage={alerts.length}
            onChange={setOffset}
            isBusy={alertsQuery.isFetching}
          />
        ) : undefined
      }
    >
      {alertsQuery.isLoading && <SkeletonRows fill rowClassName="h-[5.5rem]" />}
      {alertsQuery.isError && <ErrorNotice error={alertsQuery.error} retry={() => alertsQuery.refetch()} />}
      {alertsQuery.data && alerts.length === 0 && (
        /* An empty feed has two very different causes, and the fix is
           different for each: no rule to fire, or a rule that has not fired
           yet. Say which one it is and offer the way out of it. */
        <EmptyState
          fill
          variant="quiet"
          icon={<IconBell className="size-8" />}
          title={
            unreadOnly
              ? "No hay alertas sin leer"
              : hasRules
                ? "Tus reglas todavía no se dispararon"
                : "Todavía no vigilás ningún precio"
          }
          description={
            unreadOnly
              ? "Ya leíste todo lo que llegó. Sacá el filtro para ver el historial completo."
              : hasRules
                ? "El worker compara cada ingesta contra la anterior y avisa cuando una regla se cumple. Hacen falta dos días de datos para que haya con qué comparar."
                : "Una regla dice qué sucursal mirar, con qué radio y a partir de qué variación querés enterarte. Sin al menos una, este feed no puede llenarse."
          }
          action={
            !unreadOnly && !hasRules ? (
              <Button variant="primary" onClick={onCreateRule}>
                Crear la primera regla
              </Button>
            ) : undefined
          }
        />
      )}

      {alerts.length > 0 && (
        <>
          <ul className="divide-y divide-line-soft">
            {alerts.map((alert) => {
              const style = DIRECTION_STYLE[priceDirection(alert.delta_pct)];
              const isUnread = !alert.read_at;
              return (
                <li
                  key={alert.alert_id}
                  className={`flex flex-wrap items-start justify-between gap-3 py-3 ${
                    isUnread ? "-mx-2 rounded-lg bg-brand-soft/30 px-2" : ""
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      {isUnread && (
                        <span
                          aria-hidden="true"
                          className="size-2 shrink-0 rounded-full bg-brand"
                        />
                      )}
                      <Link
                        to={`/products/${alert.ean}`}
                        className="min-w-0 truncate rounded text-[15px] font-medium text-ink hover:underline"
                      >
                        {alert.description}
                      </Link>
                      {isUnread && <span className="sr-only">(sin leer)</span>}
                    </div>

                    <p className="tnum mt-1 flex flex-wrap items-center gap-2 text-[15px]">
                      <span className="text-muted line-through">
                        {formatCurrency(alert.previous_price)}
                      </span>
                      <span className="font-medium text-ink">{formatCurrency(alert.price)}</span>
                      <span className={style.className}>
                        {style.arrow && <span aria-hidden="true">{style.arrow} </span>}
                        {formatPercent(alert.delta_pct, { signed: true, cap: 999 })}
                      </span>
                    </p>

                    <p className="mt-1 text-[13px] text-muted">
                      <Badge tone="neutral">{alert.rule_name}</Badge>{" "}
                      <span className="ml-1">
                        {alert.banner_name} {alert.branch_name} · datos del{" "}
                        {formatDate(alert.feed_date)} · detectada {formatDateTime(alert.created_at)}
                      </span>
                    </p>
                  </div>

                  <div className="shrink-0">
                    {isUnread ? (
                      <Button
                        size="sm"
                        onClick={() => markReadMutation.mutate(alert.alert_id)}
                        aria-label={`Marcar como leída la alerta de ${alert.description}`}
                      >
                        Marcar leída
                      </Button>
                    ) : (
                      <span className="text-[13px] text-muted">Leída</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Card>
  );
}

function RulesManager({
  isFormOpen,
  setIsFormOpen,
}: {
  isFormOpen: boolean;
  setIsFormOpen: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const rulesQuery = useQuery({ queryKey: ["rules"], queryFn: getRules });
  const branchesQuery = useQuery({ queryKey: ["me", "branches"], queryFn: getMyBranches });

  function invalidateRules() {
    queryClient.invalidateQueries({ queryKey: ["rules"] });
  }

  const toggleMutation = useMutation({
    mutationFn: ({ ruleId, enabled }: { ruleId: number; enabled: boolean }) =>
      setRuleEnabled(ruleId, enabled),
    onSuccess: invalidateRules,
  });
  const deleteMutation = useMutation({ mutationFn: deleteRule, onSuccess: invalidateRules });

  const branches = branchesQuery.data ?? [];
  const rules = rulesQuery.data ?? [];

  return (
    <Card scroll className="lg:h-full" title={`Reglas de vigilancia (${rules.length})`}>
      {/* h-full so the empty state below can centre itself in the pane rather
          than in a box the height of its own text. */}
      <div className="h-full space-y-4">
        <CreateRuleForm
          branches={branches}
          isOpen={isFormOpen}
          setIsOpen={setIsFormOpen}
          onCreated={invalidateRules}
        />

        {rulesQuery.isLoading && <SkeletonRows rows={3} />}
        {rulesQuery.isError && <ErrorNotice error={rulesQuery.error} retry={() => rulesQuery.refetch()} />}
        {rulesQuery.data && rules.length === 0 && !isFormOpen && (
          <EmptyState
            fill
            variant="quiet"
            icon={<IconBell className="size-8" />}
            title="Sin reglas configuradas"
            description="Una regla dice qué sucursal vigilar, con qué radio y a partir de qué variación de precio querés que te avise."
            action={
              <Button variant="primary" onClick={() => setIsFormOpen(true)}>
                Crear una regla
              </Button>
            }
          />
        )}
        {rules.length > 0 && (
          <ul className="divide-y divide-line-soft">
            {rules.map((rule) => (
              <RuleRow
                key={rule.rule_id}
                rule={rule}
                onToggle={(enabled) => toggleMutation.mutate({ ruleId: rule.rule_id, enabled })}
                onDelete={() => deleteMutation.mutate(rule.rule_id)}
                onProductsChanged={invalidateRules}
              />
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

/* Read back the way it was written: the names the merchant picked, not the
 * SEPA identifiers they resolve to. The API sends both (queries.py
 * LIST_RULES); the identifiers only surface for a target whose branch has
 * since left the catalogue, where they are all that is left to show. */
function ruleTargetLabel(rule: Rule): string {
  if (rule.target_company_id === null) return "cualquier competidor del radio";
  if (rule.target_branch_name) {
    return [rule.target_banner_name, rule.target_branch_name].filter(Boolean).join(" ");
  }
  if (rule.target_banner_name) return `toda la cadena ${rule.target_banner_name}`;
  return (
    `la cadena #${rule.target_company_id}` +
    (rule.target_branch_id ? `, sucursal ${rule.target_branch_id}` : "")
  );
}

function RuleRow({
  rule,
  onToggle,
  onDelete,
  onProductsChanged,
}: {
  rule: Rule;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
  onProductsChanged: () => void;
}) {
  const [isEditingProducts, setIsEditingProducts] = useState(false);
  const [productsText, setProductsText] = useState(rule.products.join("\n"));
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      replaceRuleProducts(
        rule.rule_id,
        parseEans(productsText),
      ),
    onSuccess: () => {
      setIsEditingProducts(false);
      onProductsChanged();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-[15px] font-medium text-ink">{rule.name}</p>
            {rule.enabled ? (
              <Badge tone="good" icon="✓">
                Activa
              </Badge>
            ) : (
              <Badge tone="neutral" icon="•">
                Pausada
              </Badge>
            )}
          </div>

          {/* Plain sentences instead of four lines of raw identifiers. */}
          <dl className="mt-1.5 grid gap-x-6 gap-y-0.5 text-[13px] text-ink-2 sm:grid-cols-2">
            <div>
              <dt className="inline text-muted">Avisa cuando el precio se mueve </dt>
              <dd className="inline font-medium">más de {formatPercent(rule.threshold_pct)}</dd>
            </div>
            <div>
              <dt className="inline text-muted">Radio: </dt>
              <dd className="inline font-medium">
                {rule.radius_km
                  ? formatRadius(toNumber(rule.radius_km))
                  : "el de la suscripción"}
              </dd>
            </div>
            <div>
              <dt className="inline text-muted">Vigila: </dt>
              <dd className="inline font-medium">{ruleTargetLabel(rule)}</dd>
            </div>
            <div>
              <dt className="inline text-muted">Productos: </dt>
              <dd className="inline font-medium">
                {rule.products.length > 0 ? `${rule.products.length} elegidos` : "todos"}
              </dd>
            </div>
          </dl>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-[13px] text-ink-2">
            <input
              type="checkbox"
              checked={rule.enabled}
              onChange={(e) => onToggle(e.target.checked)}
              className="size-4 accent-brand"
            />
            Activa
          </label>
          <Button size="sm" onClick={() => setIsEditingProducts((v) => !v)} aria-expanded={isEditingProducts}>
            Editar productos
          </Button>
          <ConfirmButton
            size="sm"
            confirmLabel="Confirmar: se borra la regla"
            pendingLabel="Borrando…"
            onConfirm={onDelete}
          >
            Eliminar
          </ConfirmButton>
        </div>
      </div>

      {isEditingProducts && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            mutation.mutate();
          }}
          className="mt-3 rounded-lg border border-line bg-plane p-3"
        >
          <Field
            label="Códigos de barras a vigilar"
            hint="Uno por línea o separados por coma. Vacío vigila todos los productos de la sucursal."
          >
            {({ id, describedBy }) => (
              <textarea
                id={id}
                aria-describedby={describedBy}
                value={productsText}
                onChange={(e) => setProductsText(e.target.value)}
                rows={4}
                className="tnum block w-full rounded-lg border border-line bg-surface px-3 py-2 text-[13px] text-ink"
              />
            )}
          </Field>
          <div className="mt-2 flex gap-2">
            <Button type="submit" size="sm" variant="primary" disabled={mutation.isPending}>
              {mutation.isPending ? "Guardando…" : "Guardar"}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setIsEditingProducts(false)}>
              Cancelar
            </Button>
          </div>
          {error && (
            <p role="alert" className="mt-2 text-[13px] text-critical">
              {error}
            </p>
          )}
        </form>
      )}
    </li>
  );
}

/* Was a form unfolding inside the Reglas tab, which made "Reglas" and "Nueva
 * regla" land on the same screen and read as the same button. As a dialog the
 * two are different things: one changes what you are looking at, the other
 * starts something.
 *
 * The fields are grouped by the question they answer -- what to call it, what
 * to watch, when to fire -- instead of being a four-column grid of unrelated
 * inputs, and the two rarely-used sections stay folded. */
function CreateRuleForm({
  branches,
  isOpen,
  setIsOpen,
  onCreated,
}: {
  branches: OrgBranch[];
  isOpen: boolean;
  setIsOpen: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [branchKey, setBranchKey] = useState("");
  const [useOwnRadius, setUseOwnRadius] = useState(true);
  const [radiusKm, setRadiusKm] = useState<number>(DEFAULT_RADIUS_KM);
  const [target, setTarget] = useState<Competitor | null>(null);
  const [thresholdPct, setThresholdPct] = useState(5);
  const [products, setProducts] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isAdvancedOpen, setIsAdvancedOpen] = useState(false);
  const [isProductsOpen, setIsProductsOpen] = useState(false);

  const selectedBranch = branches.find(
    (b) => `${b.company_id}-${b.banner_id}-${b.branch_id}` === branchKey,
  );

  /* The radius the rule will actually use -- the branch's own, or the one
   * overridden below. It is what decides which competitors are worth offering
   * as a target, so the picker asks for the same number the worker will. */
  const effectiveRadiusKm = useOwnRadius
    ? selectedBranch
      ? toNumber(selectedBranch.radius_km)
      : DEFAULT_RADIUS_KM
    : radiusKm;

  // A different branch has a different set of neighbours, so a target chosen
  // for the previous one is not a target here.
  useEffect(() => setTarget(null), [branchKey]);

  const mutation = useMutation({
    mutationFn: () => {
      if (!selectedBranch) throw new Error("Elegí una sucursal");
      return createRule({
        name,
        company_id: selectedBranch.company_id,
        banner_id: selectedBranch.banner_id,
        branch_id: selectedBranch.branch_id,
        radius_km: useOwnRadius ? null : radiusKm,
        target_company_id: target?.company_id ?? null,
        target_banner_id: target?.banner_id ?? null,
        target_branch_id: target?.branch_id ?? null,
        threshold_pct: thresholdPct,
        products: parseEans(products),
      });
    },
    onSuccess: () => {
      setName("");
      setUseOwnRadius(true);
      setRadiusKm(DEFAULT_RADIUS_KM);
      setTarget(null);
      setThresholdPct(5);
      setProducts("");
      setIsOpen(false);
      onCreated();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  if (branches.length === 0) {
    return (
      <Modal
        isOpen={isOpen}
        onClose={() => setIsOpen(false)}
        title="Primero seguí una sucursal"
      >
        <EmptyState
          variant="quiet"
          title="No hay ninguna sucursal que vigilar"
          description="Una regla vigila la competencia alrededor de una de tus sucursales, así que necesitás al menos una."
          action={
            <Link to="/branches">
              <Button variant="primary">Ir a mis sucursales</Button>
            </Link>
          }
        />
      </Modal>
    );
  }

  const canSubmit = name.trim().length >= 2 && !!selectedBranch;

  return (
    <Modal
      isOpen={isOpen}
      onClose={() => setIsOpen(false)}
      size="lg"
      title="Nueva regla de vigilancia"
      description="Te avisa cuando un competidor mueve un precio que te importa."
      footer={
        <>
          <Button variant="ghost" onClick={() => setIsOpen(false)}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            disabled={!canSubmit || mutation.isPending}
            onClick={() => {
              setError(null);
              mutation.mutate();
            }}
          >
            {mutation.isPending ? "Creando…" : "Crear regla"}
          </Button>
        </>
      }
    >
      <div className="space-y-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Nombre de la regla" hint="Para reconocerla en el feed.">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                required
                minLength={2}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Ej: Bebidas, competencia cercana"
                className="min-h-11"
              />
            )}
          </Field>

          <Field label="Sucursal a vigilar">
            {({ id }) => (
              <Dropdown
                id={id}
                value={branchKey}
                onChange={setBranchKey}
                placeholder="Elegí una sucursal"
                options={branches.map((b) => ({
                  value: `${b.company_id}-${b.banner_id}-${b.branch_id}`,
                  label: b.label || b.name,
                  hint: b.banner_name,
                }))}
              />
            )}
          </Field>
        </div>

        {/* The threshold in the same shape as the radius: rungs you can tap,
            and a number for anything in between. */}
        <div className="border-t border-line-soft pt-5">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span className="text-[15px] font-medium text-ink">Cuánto se tiene que mover</span>
            <span className="tnum text-2xl font-semibold tracking-tight text-brand-ink">
              {thresholdPct}%
            </span>
          </div>
          <p className="mt-1 text-[13px] text-ink-2">
            Se dispara cuando un precio vigilado varía más que esto entre una ingesta y la
            siguiente. Un umbral bajo genera muchas alertas.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {[2, 5, 10, 20].map((pct) => (
              <button
                key={pct}
                type="button"
                onClick={() => setThresholdPct(pct)}
                aria-pressed={thresholdPct === pct}
                className={`min-h-10 rounded-lg border px-3 text-[15px] font-medium transition-colors ${
                  thresholdPct === pct
                    ? "border-brand bg-brand-soft text-brand-ink"
                    : "border-line bg-surface text-ink-2 hover:border-brand hover:bg-brand-tint"
                }`}
              >
                {pct}%
              </button>
            ))}
            <div className="ml-1 w-24">
              <Input
                type="number"
                min={0}
                step={0.5}
                value={thresholdPct}
                onChange={(e) => setThresholdPct(Number(e.target.value))}
                aria-label="Umbral personalizado en por ciento"
                className="min-h-10"
              />
            </div>
          </div>
        </div>

        <div className="border-t border-line-soft pt-5">
          <label className="flex items-center gap-2.5 text-[15px] text-ink">
            <input
              type="checkbox"
              checked={useOwnRadius}
              onChange={(e) => setUseOwnRadius(e.target.checked)}
              className="size-4 accent-brand"
            />
            Usar el radio de la sucursal
            {selectedBranch && (
              <span className="text-[13px] text-muted">
                ({formatRadius(toNumber(selectedBranch.radius_km))})
              </span>
            )}
          </label>
          {!useOwnRadius && (
            <div className="mt-4">
              <RadiusField value={radiusKm} onChange={setRadiusKm} />
            </div>
          )}
        </div>

        <Disclosure
          isOpen={isProductsOpen}
          onToggle={() => setIsProductsOpen((v) => !v)}
          label="Vigilar sólo algunos productos"
          hint="Por defecto la regla mira todo el surtido de la sucursal."
        >
          <Field
            label="Códigos de barras"
            hint="Uno por línea o separados por coma."
          >
            {({ id, describedBy }) => (
              <textarea
                id={id}
                aria-describedby={describedBy}
                value={products}
                onChange={(e) => setProducts(e.target.value)}
                rows={4}
                className="tnum block w-full rounded-lg border border-line bg-surface px-3 py-2 text-[13px] text-ink"
              />
            )}
          </Field>
        </Disclosure>

        <Disclosure
          isOpen={isAdvancedOpen}
          onToggle={() => setIsAdvancedOpen((v) => !v)}
          label="Vigilar un competidor puntual"
          /* The hint carries the choice, so folding this section back up does
             not hide the fact that the rule is now aimed at one branch. */
          hint={
            target
              ? `Avisa sólo por ${target.banner_name} ${target.name}.`
              : "Por defecto mira a cualquier competidor dentro del radio. Elegí una sucursal para que la regla avise sólo por esa."
          }
        >
          <TargetPicker
            branchKey={
              selectedBranch
                ? {
                    company_id: selectedBranch.company_id,
                    banner_id: selectedBranch.banner_id,
                    branch_id: selectedBranch.branch_id,
                  }
                : null
            }
            radiusKm={effectiveRadiusKm}
            value={target}
            onChange={setTarget}
          />
        </Disclosure>

        {error && (
          <p role="alert" className="rounded-lg bg-[#fdeceb] px-3 py-2 text-[15px] text-critical">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}

/* Was three number boxes asking for company_id, banner_id and branch_id --
 * identifiers that live in the SEPA feed and nowhere a merchant can see. The
 * picker in /compare already solved this for the comparison screen: search the
 * branch's own neighbours by name and click one. Same thing here, single-pick,
 * inline instead of in a dialog because this form is already one.
 *
 * The candidates come from the rule's own radius, not from the maximum: that
 * radius is also what decides whether the alert can fire at all, since
 * RAISE_ALERTS (app/batch/worker/main.py) joins branch_neighbor with `distance_m <=
 * coalesce(rule.radius_km, branch.radius_km)`. Offering a target the rule
 * could never fire on would be offering a rule that never goes off -- which is
 * exactly what the raw identifiers made easy to build by accident.
 */
function TargetPicker({
  branchKey,
  radiusKm,
  value,
  onChange,
}: {
  branchKey: BranchKey | null;
  radiusKm: number;
  value: Competitor | null;
  onChange: (competitor: Competitor | null) => void;
}) {
  const [q, setQ] = useState("");

  // The radius slider fires per 0.1 km; without this every nudge is a request.
  const debouncedRadius = useDebouncedValue(radiusKm, 300);
  const debouncedQ = useDebouncedValue(q, 250);

  // Both the needle and the cap are the server's job: the rule's radius can
  // hold hundreds of branches and this list offers TARGET_ROWS of them.
  const competitorsQuery = useQuery({
    queryKey: ["competitors", "target", branchKey, debouncedRadius, debouncedQ],
    queryFn: () =>
      getCompetitors(branchKey!, {
        radius_km: debouncedRadius,
        q: debouncedQ || undefined,
        limit: TARGET_ROWS,
      }),
    enabled: !!branchKey,
    placeholderData: (previous) => previous,
  });

  if (!branchKey) {
    return (
      <p className="rounded-lg bg-surface px-3 py-2 text-[13px] text-ink-2">
        Elegí primero la sucursal a vigilar: los competidores se buscan alrededor de ella.
      </p>
    );
  }

  // Chosen before the radius shrank, so it is still on screen but out of reach
  // of the rule. Saying so beats letting the rule sit there never firing.
  const isOutOfRadius = value !== null && value.distance_m > debouncedRadius * 1000;

  if (value) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-3 rounded-lg border border-brand bg-brand-soft/40 px-3 py-2">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[15px] font-medium text-ink">{value.name}</span>
            <span className="block truncate text-[13px] text-muted">
              {value.banner_name} · {value.locality} · {formatDistance(value.distance_m)}
            </span>
          </span>
          <button
            type="button"
            onClick={() => onChange(null)}
            aria-label={`Dejar de vigilar sólo a ${value.banner_name} ${value.name}`}
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-line hover:text-ink"
          >
            <IconClose className="size-4" />
          </button>
        </div>
        {isOutOfRadius && (
          <p role="alert" className="text-[13px] text-critical">
            Esta sucursal está a {formatDistance(value.distance_m)}, fuera del radio de{" "}
            {formatRadius(debouncedRadius)} de la regla: así como está, no se va a disparar nunca.
            Ampliá el radio o elegí otra.
          </p>
        )}
      </div>
    );
  }

  const shown = competitorsQuery.data?.items ?? [];
  const total = competitorsQuery.data?.total ?? 0;
  const needle = debouncedQ.trim();

  return (
    <div className="space-y-3">
      <SearchInput
        label="Buscar competidor por nombre, bandera o localidad"
        value={q}
        onChange={setQ}
        placeholder="Nombre, bandera o localidad…"
      />

      {competitorsQuery.isLoading && <SkeletonRows rows={3} rowClassName="h-12" />}
      {competitorsQuery.isError && (
        <ErrorNotice error={competitorsQuery.error} retry={() => competitorsQuery.refetch()} />
      )}
      {competitorsQuery.data && shown.length === 0 && (
        <p className="rounded-lg bg-surface px-3 py-2 text-[13px] text-ink-2">
          {needle
            ? "Ningún competidor del radio coincide. Probá con menos palabras."
            : `No hay otras cadenas publicando precios dentro de ${formatRadius(debouncedRadius)}. Ampliá el radio de la regla.`}
        </p>
      )}

      {shown.length > 0 && (
        /* Capped and scrollable: a dense radius returns hundreds, and this
           list lives inside a form that already scrolls. */
        <ul className="max-h-64 divide-y divide-line-soft overflow-y-auto rounded-lg border border-line bg-surface px-2">
          {shown.map((c) => (
            <li key={`${c.company_id}:${c.banner_id}:${c.branch_id}`}>
              <button
                type="button"
                onClick={() => onChange(c)}
                className="flex w-full items-center gap-3 px-1 py-2 text-left hover:bg-plane"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[15px] font-medium text-ink">{c.name}</span>
                  <span className="block truncate text-[13px] text-muted">
                    {c.banner_name} · {c.locality}
                  </span>
                </span>
                <Badge tone="neutral">{formatDistance(c.distance_m)}</Badge>
              </button>
            </li>
          ))}
        </ul>
      )}

      {total > shown.length && (
        <p className="text-[13px] text-muted">
          Mostrando {shown.length === 1 ? "la más cercana" : `las ${shown.length} más cercanas`} de{" "}
          {total}. Afiná la búsqueda para ver el resto.
        </p>
      )}
    </div>
  );
}

function Disclosure({
  isOpen,
  onToggle,
  label,
  hint,
  children,
}: {
  isOpen: boolean;
  onToggle: () => void;
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line bg-plane p-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        className="flex w-full items-center gap-2 text-left text-[15px] font-medium text-ink"
      >
        <span aria-hidden="true" className="text-muted">
          {isOpen ? "▾" : "▸"}
        </span>
        {label}
      </button>
      <p className="mt-1 pl-5 text-[13px] text-muted">{hint}</p>
      {isOpen && <div className="mt-3 pl-5">{children}</div>}
    </div>
  );
}

/** The barcodes typed into a rule, one per line or comma-separated. Throws,
 * naming them, if any is not one: a rule that watches "123" never fires, and
 * the API refuses it anyway. */
function parseEans(text: string): string[] {
  const eans = text
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const invalid = eans.filter((ean) => !/^\d{8,14}$/.test(ean));
  if (invalid.length > 0) {
    throw new Error(
      `${invalid.length === 1 ? "Este código no es válido" : "Estos códigos no son válidos"}: ${invalid.join(", ")}. Un código de barras tiene entre 8 y 14 dígitos.`,
    );
  }
  return eans;
}
