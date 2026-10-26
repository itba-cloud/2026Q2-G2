/* The competitive radius, in one place.
 *
 * It is the single most consequential number a merchant sets here: it decides
 * which branches count as competition, and therefore what every comparison,
 * summary and alert on the site actually means. It used to be a bare `3`
 * hardcoded in two forms with no explanation anywhere. */

/** What a new subscription starts at: a walkable neighbourhood, which is the
 * distance at which a shopper actually chooses between two shops. */
export const DEFAULT_RADIUS_KM = 3;

export const MIN_RADIUS_KM = 0.1;

/** Mirrors the backend's MAX_RADIUS_KM (app/api/main.py, env-overridable).
 * The API clamps to it regardless of what is sent, so the control should not
 * offer more than the server will honour. */
export const MAX_RADIUS_KM = 10;

/** The rungs worth one tap. Chosen to span the useful range rather than to be
 * round: a block, a neighbourhood, a district, the whole area. */
export const RADIUS_PRESETS = [
  { km: 1, label: "1 km", hint: "La cuadra" },
  { km: 3, label: "3 km", hint: "El barrio" },
  { km: 5, label: "5 km", hint: "La zona" },
  { km: 10, label: "10 km", hint: "Toda el área" },
] as const;

export function clampRadius(km: number): number {
  if (Number.isNaN(km)) return DEFAULT_RADIUS_KM;
  return Math.min(MAX_RADIUS_KM, Math.max(MIN_RADIUS_KM, km));
}

export function formatRadius(km: number): string {
  return `${km.toFixed(1).replace(".", ",")} km`;
}
