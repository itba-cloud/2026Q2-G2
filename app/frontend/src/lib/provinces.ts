/* The SEPA publishes provinces as ISO 3166-2:AR codes ("AR-B"), which is not
 * something anyone should have to type into a filter or read off a table. The
 * codes stay the value sent to the API; only the label changes. */
export const PROVINCES: { code: string; name: string }[] = [
  { code: "AR-C", name: "Ciudad de Buenos Aires" },
  { code: "AR-B", name: "Buenos Aires" },
  { code: "AR-K", name: "Catamarca" },
  { code: "AR-H", name: "Chaco" },
  { code: "AR-U", name: "Chubut" },
  { code: "AR-X", name: "Córdoba" },
  { code: "AR-W", name: "Corrientes" },
  { code: "AR-E", name: "Entre Ríos" },
  { code: "AR-P", name: "Formosa" },
  { code: "AR-Y", name: "Jujuy" },
  { code: "AR-L", name: "La Pampa" },
  { code: "AR-F", name: "La Rioja" },
  { code: "AR-M", name: "Mendoza" },
  { code: "AR-N", name: "Misiones" },
  { code: "AR-Q", name: "Neuquén" },
  { code: "AR-R", name: "Río Negro" },
  { code: "AR-A", name: "Salta" },
  { code: "AR-J", name: "San Juan" },
  { code: "AR-D", name: "San Luis" },
  { code: "AR-Z", name: "Santa Cruz" },
  { code: "AR-S", name: "Santa Fe" },
  { code: "AR-G", name: "Santiago del Estero" },
  { code: "AR-V", name: "Tierra del Fuego" },
  { code: "AR-T", name: "Tucumán" },
];

const BY_CODE = new Map(PROVINCES.map((p) => [p.code, p.name]));

/** Falls back to whatever the feed published: a handful of rows carry a plain
 * name or an empty string instead of a code, and dropping those on the floor
 * would be worse than showing them as they came. */
export function provinceName(code: string | null | undefined): string {
  if (!code) return "—";
  return BY_CODE.get(code) ?? code;
}
