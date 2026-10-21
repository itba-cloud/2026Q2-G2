import { ApiError } from "../api/client";
import { IconWarning } from "./icons";

/* The API speaks English (its `detail`s are for developers and logs); the
 * portal does not. The ones a person can run into, in their words. */
const DETAILS: Record<string, string> = {
  "that email is already in an organization": "Ese email ya pertenece a una organización.",
  "that Cognito user is already in LGDC with another email":
    "Ese usuario ya está en LGDC con otro email.",
  "the user already has an organization": "Ya tenés una organización.",
  "Cognito refused that email": "Ese email no es válido.",
  "could not reach Cognito": "No se pudo enviar la invitación. Probá de nuevo en un momento.",
  "Cognito did not create the user": "No se pudo enviar la invitación. Probá de nuevo en un momento.",
  "set up your merchant profile first via POST /me/merchant": "Primero creá tu sucursal.",
  "feed_date must be YYYY-MM-DD": "La fecha no es válida.",
  "filename must name a file": "El archivo no tiene nombre.",
  "upload is being processed": "La carga todavía se está procesando.",
  "branch not subscribed by this org": "No seguís esa sucursal.",
  "branch not found": "No se encontró la sucursal.",
  "product not found": "No se encontró el producto.",
  "rule not found": "No se encontró la regla.",
  "upload not found": "No se encontró la carga.",
  "alert not found": "No se encontró la alerta.",
  comparison_too_slow:
    "La comparación en vivo con este radio tarda demasiado. Probá con un radio más chico.",
  "database unreachable": "La base de datos no responde. Probá de nuevo en un momento.",
};

function byStatus(status: number): string | null {
  if (status === 403) return "No tenés permiso para hacer esto.";
  if (status === 404) return "No se encontró lo que buscabas.";
  if (status === 422) return "Revisá los datos: hay alguno que no es válido.";
  if (status >= 502 && status <= 504)
    return "El servidor tardó demasiado en responder. Probá de nuevo en un momento.";
  if (status >= 500) return "Ocurrió un error en el servidor.";
  return null;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return DETAILS[error.message] ?? byStatus(error.status) ?? error.message;
  }
  if (error instanceof Error) return error.message;
  return "Ocurrió un error inesperado.";
}

/* role="alert" so the message is announced when it appears rather than
 * silently replacing the content someone was waiting for. */
export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-[#f6cfcd] bg-[#fdeceb] px-4 py-3"
    >
      <IconWarning className="mt-0.5 size-5 shrink-0 text-critical" />
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-medium text-critical">No se pudo cargar</p>
        <p className="mt-0.5 text-[15px] break-words text-ink-2">{errorMessage(error)}</p>
      </div>
      {retry && (
        <button
          type="button"
          onClick={retry}
          className="shrink-0 rounded-lg px-2.5 py-1 text-[15px] font-medium text-critical hover:bg-white/60"
        >
          Reintentar
        </button>
      )}
    </div>
  );
}
