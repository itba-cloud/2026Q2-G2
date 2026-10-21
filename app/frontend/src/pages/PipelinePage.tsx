import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getFeedDates, getIngestJobs } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { Dropdown } from "../components/Dropdown";
import { IconPipeline } from "../components/icons";
import { StatusPill } from "../components/StatusPill";
import {
  Card,
  Field,
  Page,
  PageHeader,
  SkeletonRows,
  StackedBar,
  TableFrame,
  Td,
  Th,
  Toolbar,
} from "../components/ui";
import { formatDate, formatDateTime } from "../lib/format";

const STATUSES = ["pending", "processing", "done", "failed"] as const;

const STATUS_LABELS: Record<(typeof STATUSES)[number], string> = {
  pending: "En cola",
  processing: "Procesando",
  done: "Listos",
  failed: "Fallidos",
};

/* SEPA chains come from the ingest; uploads, from a merchant's own list. */
const KIND_LABELS: Record<string, string> = { sepa: "SEPA", upload: "Carga propia" };

/* Sequential for progress (in-flight states step up the blue ramp), and the
 * reserved critical red only for the one state that is actually a problem. */
const STATUS_COLORS: Record<(typeof STATUSES)[number], string> = {
  pending: "#b7d3f6",
  processing: "#5598e7",
  done: "#184f95",
  failed: "#d03b3b",
};

export function PipelinePage() {
  const [feedDate, setFeedDate] = useState("");
  const [status, setStatus] = useState("");

  const feedDatesQuery = useQuery({ queryKey: ["feed-dates"], queryFn: getFeedDates });

  const jobsQuery = useQuery({
    queryKey: ["ingest-jobs", feedDate, status],
    queryFn: () =>
      getIngestJobs({
        feed_date: feedDate || undefined,
        status: status || undefined,
        limit: 200,
      }),
    refetchInterval: 10_000,
  });

  const jobs = jobsQuery.data ?? [];
  const counts = STATUSES.reduce<Record<string, number>>((acc, s) => {
    acc[s] = jobs.filter((j) => j.status === s).length;
    return acc;
  }, {});
  const failed = counts.failed ?? 0;

  return (
    <Page
      header={
        <PageHeader
          icon={<IconPipeline />}
          title="Pipeline de ingesta"
          description="Un job por cadena y por día, y uno por cada carga propia. Que una falle no arrastra a las demás."
        />
      }
    >
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-rows-[auto_minmax(0,1fr)]">
        {/* The four counts and the progress they add up to, in one strip. */}
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3">
          {STATUSES.map((s) => (
            <div key={s} className="flex items-baseline gap-2">
              <span
                aria-hidden="true"
                className="size-2 shrink-0 self-center rounded-full"
                style={{ backgroundColor: STATUS_COLORS[s] }}
              />
              <span
                className={`tnum text-lg font-semibold leading-none ${
                  s === "failed" && failed > 0 ? "text-critical" : "text-ink"
                }`}
              >
                {(counts[s] ?? 0).toLocaleString("es-AR")}
              </span>
              <span className="text-[13px] text-muted">{STATUS_LABELS[s]}</span>
            </div>
          ))}
          {jobs.length > 0 && (
            <div className="min-w-40 flex-1">
              <StackedBar
                total={jobs.length}
                legend={false}
                segments={STATUSES.filter((s) => (counts[s] ?? 0) > 0).map((s) => ({
                  label: STATUS_LABELS[s],
                  value: counts[s] ?? 0,
                  color: STATUS_COLORS[s],
                }))}
              />
            </div>
          )}
        </div>

        <Card
          scroll
          className="lg:h-full"
          title="Jobs"
          description="Se refresca solo cada 10 segundos."
          bodyClassName="p-0"
          toolbar={
            <Toolbar>
              <Field label="Fecha de ingesta" className="w-full sm:w-56">
                {({ id }) => (
                  <Dropdown
                    id={id}
                    value={feedDate}
                    onChange={setFeedDate}
                    options={[
                      { value: "", label: "Todas las fechas" },
                      ...(feedDatesQuery.data ?? []).map((d) => ({
                        value: d,
                        label: formatDate(d),
                      })),
                    ]}
                  />
                )}
              </Field>
              <Field label="Estado" className="w-full sm:w-48">
                {({ id }) => (
                  <Dropdown
                    id={id}
                    value={status}
                    onChange={setStatus}
                    options={[
                      { value: "", label: "Todos" },
                      ...STATUSES.map((st) => ({ value: st, label: STATUS_LABELS[st] })),
                    ]}
                  />
                )}
              </Field>
            </Toolbar>
          }
        >
          {jobsQuery.isLoading && (
            <div className="h-full p-4">
              <SkeletonRows fill rowClassName="h-9" />
            </div>
          )}
          {jobsQuery.isError && (
            <div className="p-4">
              <ErrorNotice error={jobsQuery.error} retry={() => jobsQuery.refetch()} />
            </div>
          )}
          {jobsQuery.data && jobs.length === 0 && (
            <EmptyState
                fill
                variant="quiet"
                title="No hay jobs para ese filtro"
                description="Probá con otra fecha, o quitá el filtro de estado."
              />
          )}

        {jobs.length > 0 && (
          <TableFrame caption="Jobs de ingesta por cadena, fecha y estado" minWidth="66rem">
            <thead>
              <tr>
                <Th>Job</Th>
                <Th>Fecha</Th>
                <Th>Cadena</Th>
                <Th>Tipo</Th>
                <Th>Estado</Th>
                <Th>Worker</Th>
                <Th align="right">Intentos</Th>
                <Th align="right">Filas OK</Th>
                <Th align="right">Rechazadas</Th>
                <Th>Último error</Th>
                <Th align="right">Actualizado</Th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr
                  key={job.job_id}
                  className={job.status === "failed" ? "bg-[#fdeceb]/50" : "hover:bg-plane"}
                >
                  <Td numeric className="text-[13px] text-muted">
                    #{job.job_id}
                  </Td>
                  <Td numeric className="text-ink-2">
                    {formatDate(job.feed_date)}
                  </Td>
                  <Td className="text-ink-2">
                    <span className="block font-medium text-ink">{job.chain_name ?? "—"}</span>
                    <span className="tnum block text-[13px] text-muted">{job.chain_id}</span>
                  </Td>
                  <Td className="text-ink-2">{KIND_LABELS[job.kind] ?? job.kind}</Td>
                  <Td>
                    <StatusPill status={job.status} />
                  </Td>
                  <Td className="text-[13px] text-muted">{job.worker_id ?? "—"}</Td>
                  <Td align="right" numeric className="text-ink-2">
                    {job.attempts}
                  </Td>
                  <Td align="right" numeric className="text-ink-2">
                    {job.rows_ok?.toLocaleString("es-AR") ?? "—"}
                  </Td>
                  <Td align="right" numeric className="text-ink-2">
                    {job.rows_rejected?.toLocaleString("es-AR") ?? "—"}
                  </Td>
                  <Td className="max-w-[15rem] truncate text-[13px] text-critical">
                    <span title={job.last_error ?? ""}>{job.last_error ?? "—"}</span>
                  </Td>
                  <Td align="right" numeric className="text-[13px] text-muted">
                    {formatDateTime(job.updated_at)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableFrame>
        )}
        </Card>
      </div>
    </Page>
  );
}
