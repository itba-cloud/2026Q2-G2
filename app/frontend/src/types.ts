// Shapes mirror app/api/main.py + app/api/queries.py, checked against the
// live API (GET /auth/me, /branches, /comparison, /me/branches…).
//
// IMPORTANT: every money/percentage NUMERIC field the API returns comes back
// as a JSON *string* (Postgres NUMERIC is preserved as text to avoid float
// rounding of money) -- confirmed live for own_price/min_price/avg_price/
// max_price/pct_over_min/radius_km. Those fields are typed `string` here on
// purpose -- callers must run them through toNumber() (see lib/format.ts)
// before doing math, sorting, or charting with them.
//
// One correction to that rule as briefed: latitude/longitude come back as
// plain JSON numbers, not strings, on this running backend (branch and
// distance_m columns are float-typed, not NUMERIC) -- typed `number` below.
// toNumber() still accepts either shape, so call sites stay defensive.

export type Role = "owner" | "member";

// The three-part key that identifies a branch everywhere in the API path,
// e.g. /api/branches/{company_id}/{banner_id}/{branch_id}/...
export interface BranchKey {
  company_id: number;
  banner_id: number;
  branch_id: string;
}

// The org fields are null from the first login until the user names its
// org (POST /me/org): the portal shows the onboarding until then.
export interface User {
  user_id: number;
  email: string;
  role: Role;
  org_id: number | null;
  org_name: string | null;
  org_slug: string | null;
}

// A user of the org, as GET /org/members lists it (only the owner may).
// `last_login_at` is null for an invited member who has not logged in yet.
export interface Member {
  user_id: number;
  email: string;
  role: Role;
  created_at: string;
  last_login_at: string | null;
}

// POST /org/members: the member, and whether Cognito emailed it a temporary
// password. False for someone who already had a Cognito user: it joins with
// the password it has.
export interface InvitedMember extends Member {
  invitation_sent: boolean;
}

export interface MerchantResponse {
  company_id: number;
  banner_id: number;
  branch_id: string;
  name: string;
  // Salen del LEFT JOIN contra `branch` en el backend: null sólo si la fila
  // de `branch` no está, que es una instalación a medio arreglar.
  province?: string | null;
  locality?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export interface Branch {
  company_id: number;
  banner_id: number;
  branch_id: string;
  banner_name: string;
  legal_name: string;
  name: string;
  kind: string | null;
  province: string;
  locality: string;
  latitude: number;
  longitude: number;
}

export interface OrgBranch {
  company_id: number;
  banner_id: number;
  branch_id: string;
  label: string | null;
  radius_km: string;
  name: string;
  banner_name: string;
  province: string;
  locality: string;
  latitude: number;
  longitude: number;
}

export interface BranchPrice {
  ean: string;
  description: string;
  brand: string | null;
  presentation: string | null;
  price: string;
}

export interface Product {
  ean: string;
  name_norm: string;
  brand_norm: string | null;
  size_value: string | null;
  size_unit: string | null;
}

export interface ProductChainVariant {
  company_id: number;
  description: string;
  brand: string | null;
  presentation: string | null;
}

export interface ProductDetail {
  ean: string;
  description: string;
  brand: string | null;
  presentation: string | null;
  chains: ProductChainVariant[];
}

export interface PricePoint {
  feed_date: string;
  price: string;
}

export interface Competitor {
  company_id: number;
  banner_id: number;
  branch_id: string;
  distance_m: number;
  banner_name: string;
  name: string;
  locality: string;
  province: string;
  latitude: number;
  longitude: number;
}

/** A page of competitors, plus how many the radius holds in all. The two are
 * not interchangeable: `items.length` is what is on screen, `total` is the
 * answer to "cuantos competidores tengo cerca". */
export interface CompetitorPage {
  total: number;
  items: Competitor[];
}

/** One competitor's price for a single barcode, with how far away it is. */
export interface ProductCompetitorPrice {
  company_id: number;
  banner_id: number;
  branch_id: string;
  distance_m: number;
  banner_name: string;
  name: string;
  locality: string;
  price: string;
}

export interface ComparisonRow {
  ean: string;
  description: string;
  brand: string | null;
  own_price: string;
  min_price: string;
  avg_price: string;
  max_price: string;
  offers: number;
  pct_over_min: string;
}

export interface ComparisonSummary {
  compared: number;
  cheaper: number;
  matched_min: number;
  dearer: number;
  own_assortment: number;
}

/** Where an answer came from: the comparison precomputed once a day for the
 * branch's configured radius, or the live query (another radius or day, or a
 * branch the precompute has not reached yet), or `pending`: the live query
 * took too long and the precompute is on its way. */
export type ComparisonSource = "precomputed" | "live" | "pending";

/** Everything the tablero shows, from one request. `feed_date` is the date the
 * API actually compared -- it resolves the latest itself, so the screen labels
 * itself from the answer instead of asking what the latest is first. Null when
 * no ingestion has ever finished. */
export interface Dashboard {
  feed_date: string | null;
  source: ComparisonSource;
  summary: ComparisonSummary;
  worst: ComparisonRow[];
}

/** A page of Mis Precios. `next_cursor` is opaque: passed back as `cursor`, it
 * asks for the page after this one, from the same day; null on the last. */
export interface ComparisonPage {
  feed_date: string | null;
  source: ComparisonSource;
  items: ComparisonRow[];
  next_cursor: string | null;
}

/** One product, your price, and each named rival's price for it. The rival
 * prices arrive keyed by "company_id:banner_id:branch_id" -- the same string
 * the `vs` query parameter takes -- because the set of columns is chosen by
 * the caller, so it cannot be a fixed shape. */
export interface HeadToHeadRow {
  ean: string;
  description: string;
  brand: string | null;
  own_price: string;
  rival_prices: Record<string, number>;
  min_price: string;
  avg_price: string;
  offers: number;
  pct_over_min: string;
}

export interface ChangeRow {
  ean: string;
  description: string;
  previous_price: string;
  price: string;
  delta_pct: string;
}

export interface Alert {
  alert_id: number;
  rule_id: number;
  rule_name: string;
  feed_date: string;
  ean: string;
  description: string;
  company_id: number;
  banner_id: number;
  branch_id: string;
  banner_name: string;
  branch_name: string;
  previous_price: string;
  price: string;
  delta_pct: string;
  created_at: string;
  read_at: string | null;
}

export interface Rule {
  rule_id: number;
  name: string;
  company_id: number;
  banner_id: number;
  branch_id: string;
  radius_km: string | null;
  target_company_id: number | null;
  target_banner_id: number | null;
  target_branch_id: string | null;
  /** Resolved from the target_* ids by the API (queries.py LIST_RULES) so the
   * rule can be read out loud. Null when the rule watches the whole radius;
   * the branch pair is also null when the rule names only a chain. */
  target_banner_name: string | null;
  target_branch_name: string | null;
  target_locality: string | null;
  threshold_pct: string;
  enabled: boolean;
  created_at: string;
  products: string[];
}

/** A presigned POST: the browser sends `fields` and then the file to `url`,
 * straight to S3. S3 takes files up to `max_bytes`. */
export interface UploadPresign {
  upload_id: string;
  url: string;
  fields: Record<string, string>;
  expires_in: number;
  max_bytes: number;
}

export interface UploadRecord {
  upload_id: string;
  feed_date: string;
  filename: string;
  status: string;
  job_id: number | null;
  created_at: string;
  job_status: string | null;
  rows_ok: number | null;
  rows_rejected: number | null;
  last_error: string | null;
}

/** A row of an upload the worker could not load: its line in the file (the
 * header is line 1), why, and what the line said. */
export interface UploadRejectedRow {
  line: number;
  reason: string;
  content: string;
}

// Lo que devuelve DELETE /uploads/{id}: no un OK a ciegas, sino qué se
// deshizo. `otras_cargas_del_dia` mayor a cero significa que los precios NO se
// borraron, porque hay otra lista del mismo día que también los explica.
export interface UploadDeleted {
  upload_id: string;
  filename: string;
  feed_date: string;
  precios: number;
  cambios: number;
  alertas: number;
  otras_cargas_del_dia: number;
}

export interface UploadDetail extends UploadRecord {
  chain_id: string | null;
}

export interface IngestJob {
  job_id: number;
  feed_date: string;
  chain_id: string;
  kind: string;
  /** A merchant's name for an upload, the chain's first banner for the SEPA;
   * null until the chain has loaded a branch. */
  chain_name: string | null;
  status: string;
  worker_id: string | null;
  attempts: number;
  rows_ok: number | null;
  rows_rejected: number | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}
