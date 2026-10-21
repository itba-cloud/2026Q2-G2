import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import {
  deleteUpload,
  getMerchant,
  getUploadRejected,
  getUploads,
  uploadPriceList,
} from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice, errorMessage } from "../components/ErrorNotice";
import { MerchantForm } from "../components/MerchantForm";
import { Modal } from "../components/Modal";
import { Spinner } from "../components/Spinner";
import { StatusPill } from "../components/StatusPill";
import { IconHistory, IconUpload } from "../components/icons";
import {
  Button,
  Card,
  ConfirmButton,
  Field,
  Input,
  Page,
  PageHeader,
  SkeletonRows,
  TableFrame,
  Td,
  Th,
} from "../components/ui";
import { formatDate, formatDateTime, todayIsoDate } from "../lib/format";
import { provinceName } from "../lib/provinces";
import type { UploadRecord } from "../types";

export function UploadPage() {
  const queryClient = useQueryClient();
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<string | null>(null);
  const [detail, setDetail] = useState<UploadRecord | null>(null);

  // Sin sucursal propia, `POST /uploads` responde 400 y no hay archivo que
  // subir. Antes eso se resolvía mandando al usuario a /account a completar
  // un formulario en otra pantalla; ahora el alta está acá, que es donde se
  // descubre que falta.
  const merchantQuery = useQuery({ queryKey: ["me", "merchant"], queryFn: getMerchant });

  const uploadsQuery = useQuery({
    queryKey: ["uploads"],
    queryFn: () => getUploads(50),
    // Poll only while something is in flight, then stop. The upload's own
    // status is one of awaiting_file / queued / done / failed: a worker takes
    // the file (queued) and settles it (done or failed).
    refetchInterval: (query) => {
      const rows = query.state.data ?? [];
      const hasPending = rows.some(
        (r) => r.status === "awaiting_file" || r.status === "queued",
      );
      return hasPending ? 4000 : false;
    },
  });

  const uploads = uploadsQuery.data ?? [];

  return (
    <Page
      header={
        <PageHeader
          icon={<IconUpload />}
          title="Cargar precios"
          description="Si tu comercio no publica en el SEPA, subí tu lista y entrá igual en la comparación."
        />
      }
    >
      {/* The act of uploading on the left, its history on the right: the
          history is the thing that grows, so it is the pane that scrolls. */}
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:h-full lg:min-h-0 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]">
        <Card
          scroll
          className="lg:h-full"
          title={merchantQuery.data ? "Nueva carga" : "Primero, tu sucursal"}
          description={
            merchantQuery.data
              ? undefined
              : "Se da de alta una sola vez. Su ubicación es lo que define contra quién te comparás."
          }
        >
          {merchantQuery.isLoading ? (
            <Spinner label="Buscando tu sucursal…" />
          ) : !merchantQuery.data ? (
            <MerchantForm submitLabel="Crear la sucursal" />
          ) : (
            <>
              {/* Lo primero que hay que saber en esta pantalla, y lo que antes
                  no decía en ninguna parte: los precios no entran en la
                  sucursal activa de la barra lateral -- ésa es ajena, del SEPA
                  -- sino en la sucursal propia de la organización. */}
              <div className="mb-4 rounded-xl border border-line-soft bg-plane px-3.5 py-3">
                <p className="text-[13px] text-muted">Los precios entran en tu sucursal</p>
                <p className="mt-0.5 text-[15px] font-semibold text-ink">
                  {merchantQuery.data.name}
                </p>
                <p className="mt-0.5 text-[13px] text-muted">
                  {[merchantQuery.data.locality, provinceName(merchantQuery.data.province ?? "")]
                    .filter(Boolean)
                    .join(", ")}
                  {" · "}
                  <Link to="/account" className="rounded text-brand-ink hover:underline">
                    editarla
                  </Link>
                </p>
              </div>
              <UploadWidget
                onDone={(filename) => {
                  setUploadError(null);
                  setUploaded(filename);
                  queryClient.invalidateQueries({ queryKey: ["uploads"] });
                }}
                onError={(message) => {
                  setUploaded(null);
                  setUploadError(message);
                }}
              />
              {/* Nothing is left to do after the POST: S3 hands the file to
                  the jobs queue. A worker may take a few minutes to start. */}
              {uploaded && !uploadError && (
                <p className="mt-3 text-[13px] text-muted" role="status">
                  Recibimos {uploaded}. Un worker lo procesa en unos minutos; su estado se ve
                  en Cargas anteriores.
                </p>
              )}
              {uploadError && (
                <div className="mt-3">
                  <ErrorNotice error={new Error(uploadError)} />
                </div>
              )}
            </>
          )}

          {/* Los tres pasos, dichos una vez y donde hacen falta. El primero
              se marca cumplido en cuanto la sucursal existe: es lo que
              explica por qué el formulario de arriba cambió de uno a otro. */}
          <ol className="mt-5 space-y-2.5 border-t border-line-soft pt-4">
            {[
              {
                n: 1,
                t: "Creá tu sucursal",
                d: merchantQuery.data
                  ? `Lista: ${merchantQuery.data.name}. Se edita en Tu cuenta.`
                  : "Una sola vez, en el formulario de arriba.",
                done: Boolean(merchantQuery.data),
              },
              {
                n: 2,
                t: "Subí el archivo",
                d: "CSV con código de barras y precio, del día que corresponda.",
                done: false,
              },
              {
                n: 3,
                t: "Esperá el procesamiento",
                d: "Un worker lo procesa en unos minutos y ya entra en las comparaciones. Si alguna fila no se pudo leer, la ves en el detalle de la carga.",
                done: false,
              },
            ].map((step) => (
              <li key={step.n} className="flex gap-2.5">
                <span
                  className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                    step.done ? "bg-brand text-white" : "bg-brand-soft text-brand-ink"
                  }`}
                >
                  {step.done ? "✓" : step.n}
                </span>
                <div className="min-w-0">
                  <p className="text-[13px] font-medium leading-tight text-ink">{step.t}</p>
                  <p className="mt-0.5 text-[13px] leading-snug text-muted">{step.d}</p>
                </div>
              </li>
            ))}
          </ol>
        </Card>

        <Card
          scroll
          className="lg:h-full"
          title="Cargas anteriores"
          description={
            uploads.length > 0 ? "Se refresca solo mientras haya algo procesándose." : undefined
          }
          bodyClassName="p-0"
        >
          {uploadsQuery.isLoading && (
            <div className="h-full p-4">
              <SkeletonRows fill rowClassName="h-9" />
            </div>
          )}
          {uploadsQuery.isError && (
            <div className="p-4">
              <ErrorNotice error={uploadsQuery.error} retry={() => uploadsQuery.refetch()} />
            </div>
          )}
          {uploadsQuery.data && uploads.length === 0 && (
            /* Centred in the whole panel rather than parked at the top: the
               empty column was the largest thing on the screen and it was
               using none of it. */
            <div className="flex h-full min-h-[16rem] items-center justify-center p-4">
              <EmptyState
                variant="quiet"
                icon={<IconHistory className="size-8" />}
                title="Todavía no subiste ningún archivo"
                description="Cuando subas una lista con el panel de la izquierda, acá vas a ver en qué estado quedó su procesamiento."
              />
            </div>
          )}

          {uploads.length > 0 && (
            <TableFrame caption="Historial de cargas de listas de precios propias" minWidth="46rem">
              <thead>
                <tr>
                  <Th>Archivo</Th>
                  <Th>Fecha de datos</Th>
                  <Th>Subida</Th>
                  <Th>Procesamiento</Th>
                  <Th align="right">Filas OK</Th>
                  <Th align="right">Rechazadas</Th>
                  <Th align="right">Creada</Th>
                  <Th align="right">
                    <span className="sr-only">Acciones</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {uploads.map((u) => (
                  <tr key={u.upload_id} className="hover:bg-plane">
                    <Td className="font-medium">{u.filename}</Td>
                    <Td numeric className="text-ink-2">
                      {formatDate(u.feed_date)}
                    </Td>
                    <Td>
                      <StatusPill status={u.status} />
                    </Td>
                    <Td>
                      <StatusPill status={u.job_status} />
                    </Td>
                    <Td align="right" numeric className="text-ink-2">
                      {u.rows_ok?.toLocaleString("es-AR") ?? "—"}
                    </Td>
                    <Td align="right" numeric className="text-ink-2">
                      {u.rows_rejected?.toLocaleString("es-AR") ?? "—"}
                    </Td>
                    <Td align="right" numeric className="text-[13px] text-muted">
                      {formatDateTime(u.created_at)}
                    </Td>
                    <Td align="right">
                      <div className="flex justify-end gap-2">
                        {(Boolean(u.rows_rejected) || u.status === "failed") && (
                          <Button size="sm" onClick={() => setDetail(u)}>
                            {u.status === "failed" ? "Ver el error" : "Ver rechazadas"}
                          </Button>
                        )}
                        <UploadRowActions upload={u} onError={setUploadError} />
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableFrame>
          )}
        </Card>
      </div>
      {detail && <UploadDetailModal upload={detail} onClose={() => setDetail(null)} />}
    </Page>
  );
}

/* Why an upload failed, and the rows the worker could not load: the line in
 * the file (the header is line 1), the reason, and what the line said. Enough
 * to fix the file and upload it again. */
function UploadDetailModal({ upload, onClose }: { upload: UploadRecord; onClose: () => void }) {
  const rejectedQuery = useQuery({
    queryKey: ["uploads", upload.upload_id, "rejected"],
    queryFn: () => getUploadRejected(upload.upload_id),
  });
  const rows = rejectedQuery.data ?? [];
  const shown = rows.length;
  const total = upload.rows_rejected ?? shown;

  return (
    <Modal
      isOpen
      onClose={onClose}
      size="lg"
      title={upload.filename}
      description={`Lista del ${formatDate(upload.feed_date)}`}
    >
      {upload.last_error && (
        <div className="mb-4 rounded-xl border border-[#f6cfcd] bg-[#fdeceb] px-3.5 py-3">
          <p className="text-[13px] font-medium text-critical">No se pudo cargar el archivo</p>
          <p className="mt-0.5 break-words text-[13px] text-ink-2">{upload.last_error}</p>
        </div>
      )}
      {rejectedQuery.isLoading && <Spinner label="Buscando las filas rechazadas…" />}
      {rejectedQuery.isError && (
        <ErrorNotice error={rejectedQuery.error} retry={() => rejectedQuery.refetch()} />
      )}
      {rejectedQuery.data && rows.length === 0 && !upload.last_error && (
        <p className="text-[13px] text-muted">No hay filas rechazadas.</p>
      )}
      {rows.length > 0 && (
        <>
          <p className="mb-2 text-[13px] text-muted">
            {total > shown
              ? `Las primeras ${shown.toLocaleString("es-AR")} de ${total.toLocaleString("es-AR")} filas rechazadas.`
              : `${shown.toLocaleString("es-AR")} ${shown === 1 ? "fila rechazada" : "filas rechazadas"}.`}
          </p>
          <TableFrame caption="Filas rechazadas de la carga" minWidth="32rem">
            <thead>
              <tr>
                <Th align="right">Línea</Th>
                <Th>Motivo</Th>
                <Th>Contenido</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.line}>
                  <Td align="right" numeric className="text-ink-2">
                    {row.line}
                  </Td>
                  <Td className="text-ink-2">{row.reason}</Td>
                  <Td className="max-w-[16rem] truncate font-mono text-[13px] text-muted">
                    <span title={row.content}>{row.content}</span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableFrame>
        </>
      )}
    </Modal>
  );
}

/* Borrar una carga deshace también los precios que dejó, así que el botón
 * tiene que decirlo antes de que alguien lo apriete: `ConfirmButton` obliga a
 * un segundo click sobre un texto que nombra la consecuencia. */
function UploadRowActions({
  upload,
  onError,
}: {
  upload: UploadRecord;
  onError: (message: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => deleteUpload(upload.upload_id),
    onSuccess: (result) => {
      onError(null);
      queryClient.invalidateQueries({ queryKey: ["uploads"] });
      // Los precios que se fueron con la carga estaban en la comparación y en
      // el tablero, así que todo lo que los lee queda viejo.
      queryClient.invalidateQueries({ queryKey: ["feed-dates"] });
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      if (result.otras_cargas_del_dia > 0) {
        onError(
          `Se borró ${result.filename}, pero sus precios quedaron: hay otra lista ` +
            `del ${formatDate(result.feed_date)} que también los explica.`,
        );
      }
    },
    onError: (err) => onError(errorMessage(err)),
  });

  // A worker is loading it: the API refuses to delete it (409) until it settles.
  if (upload.status === "queued") return null;

  return (
    <ConfirmButton
      size="sm"
      pending={mutation.isPending}
      pendingLabel="Borrando…"
      confirmLabel="Confirmar: se borran sus precios"
      onConfirm={() => {
        onError(null);
        mutation.mutate();
      }}
    >
      Borrar
    </ConfirmButton>
  );
}

function UploadWidget({
  onDone,
  onError,
}: {
  onDone: (filename: string) => void;
  onError: (message: string | null) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [feedDate, setFeedDate] = useState(todayIsoDate());
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const mutation = useMutation({
    mutationFn: () => uploadPriceList(file!, feedDate),
    onSuccess: () => {
      onDone(file!.name);
      setFile(null);
    },
    onError: (err) => onError(errorMessage(err)),
  });

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  return (
    <div>
      {/* A real <button> around the drop zone: dropping is the shortcut, and
          clicking or pressing Enter is the path that always works. */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`rounded-xl border-2 border-dashed transition-colors ${
          isDragging ? "border-brand bg-brand-soft/40" : "border-axis"
        }`}
      >
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="flex w-full flex-col items-center gap-2 rounded-xl px-6 py-8 text-center"
        >
          <IconUpload className="size-7 text-muted" />
          {file ? (
            <>
              <span className="text-[15px] font-medium text-ink">{file.name}</span>
              <span className="text-[13px] text-muted">
                {(file.size / 1024).toLocaleString("es-AR", { maximumFractionDigits: 0 })} KB · click
                para cambiarlo
              </span>
            </>
          ) : (
            <>
              <span className="text-[15px] font-medium text-ink">
                Arrastrá tu archivo, o hacé click para elegirlo
              </span>
              <span className="text-[13px] text-muted">
                CSV con las columnas ean y price (y, si querés, description, brand y presentation)
              </span>
            </>
          )}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.txt"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </div>

      {/* Stacked, not side by side: in a column this narrow the date's hint
          wrapped to two lines and dragged the button out of alignment with it. */}
      <div className="mt-4 space-y-3">
        <Field label="Fecha de los precios" hint="A qué día corresponde la lista.">
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              type="date"
              value={feedDate}
              onChange={(e) => setFeedDate(e.target.value)}
              className="min-h-11"
            />
          )}
        </Field>
        <Button
          variant="primary"
          className="w-full"
          onClick={() => {
            onError(null);
            mutation.mutate();
          }}
          disabled={!file || mutation.isPending}
        >
          {mutation.isPending ? "Subiendo…" : "Subir archivo"}
        </Button>
        {mutation.isPending && (
          <p className="text-[13px] text-muted" role="status">
            Subiendo directo al almacenamiento…
          </p>
        )}
      </div>
    </div>
  );
}
