import { api, postToPresignedUrl } from "./client";
import type {
  Alert,
  Branch,
  BranchKey,
  BranchPrice,
  ChangeRow,
  ComparisonPage,
  ComparisonSummary,
  CompetitorPage,
  Dashboard,
  HeadToHeadRow,
  IngestJob,
  InvitedMember,
  Member,
  MerchantResponse,
  OrgBranch,
  PricePoint,
  Product,
  ProductCompetitorPrice,
  ProductDetail,
  Rule,
  UploadDeleted,
  UploadDetail,
  UploadPresign,
  UploadRecord,
  UploadRejectedRow,
  User,
} from "../types";

function branchPath(key: BranchKey, suffix = ""): string {
  return `/branches/${key.company_id}/${key.banner_id}/${key.branch_id}${suffix}`;
}

// -- Ops ----------------------------------------------------------------

export const getFeedDates = () => api.get<string[]>("/feed-dates");

export const getIngestJobs = (params: { feed_date?: string; status?: string; limit?: number }) =>
  api.get<IngestJob[]>("/ingest/jobs", params);

// -- Auth / tenancy -------------------------------------------------------

export const getMe = () => api.get<User>("/auth/me");

// The first login's onboarding: the org, with the user as its owner.
export const createOrg = (body: { name: string }) => api.post<User>("/me/org", body);

// The owner's: the org's users, and the invitation (Cognito emails the
// member a temporary password).
export const getMembers = () => api.get<Member[]>("/org/members");

export const inviteMember = (body: { email: string }) =>
  api.post<InvitedMember>("/org/members", body);

export const getMerchant = () => api.get<MerchantResponse | null>("/me/merchant");

export const setMerchant = (body: {
  name: string;
  province: string;
  locality: string;
  latitude: number;
  longitude: number;
}) => api.post<MerchantResponse>("/me/merchant", body);

// -- Branches ---------------------------------------------------------------

export const searchBranches = (params: { q?: string; province?: string; limit?: number; offset?: number }) =>
  api.get<Branch[]>("/branches", params);

export const getBranch = (key: BranchKey) => api.get<Branch>(branchPath(key));

export const getMyBranches = () => api.get<OrgBranch[]>("/me/branches");

export const subscribeBranch = (body: {
  company_id: number;
  banner_id: number;
  branch_id: string;
  label?: string;
  radius_km: number;
}) => api.post<OrgBranch>("/me/branches", body);

export const updateMyBranch = (key: BranchKey, body: { label?: string; radius_km: number }) =>
  api.patch<OrgBranch>(`/me/branches/${key.company_id}/${key.banner_id}/${key.branch_id}`, body);

export const unsubscribeBranch = (key: BranchKey) =>
  api.delete<void>(`/me/branches/${key.company_id}/${key.banner_id}/${key.branch_id}`);

export const getBranchPrices = (
  key: BranchKey,
  params: { feed_date?: string; q?: string; limit?: number; offset?: number },
) => api.get<BranchPrice[]>(branchPath(key, "/prices"), params);

// -- Products -----------------------------------------------------------------

export const searchProducts = (params: { q: string; limit?: number; offset?: number }) =>
  api.get<Product[]>("/products", params);

export const getProduct = (ean: string) => api.get<ProductDetail>(`/products/${ean}`);

export const getProductCompetitorPrices = (
  key: BranchKey,
  ean: string,
  params: { feed_date?: string; radius_km?: number; limit?: number } = {},
) =>
  api.get<ProductCompetitorPrice[]>(branchPath(key, `/products/${ean}/competitors`), params);

export const getProductHistory = (key: BranchKey, ean: string, days = 60) =>
  api.get<PricePoint[]>(branchPath(key, `/products/${ean}/history`), { days });

// -- Conjunto competitivo -------------------------------------------------------

/** Nearest first. `q` searches the neighbours by name, banner or locality on
 * the server -- a dense radius holds hundreds, and filtering those in the
 * browser meant downloading all of them to show a handful. */
export const getCompetitors = (
  key: BranchKey,
  params: { radius_km?: number; q?: string; limit?: number; offset?: number } = {},
) => api.get<CompetitorPage>(branchPath(key, "/competitors"), params);

/** Mis Precios, a page at a time: the next page is the previous answer's
 * `next_cursor`, not an offset. */
export const getComparison = (
  key: BranchKey,
  params: { feed_date?: string; radius_km?: number; q?: string; limit?: number; cursor?: string },
) => api.get<ComparisonPage>(branchPath(key, "/comparison"), params);

/** `vs` entries are "company_id:banner_id:branch_id"; the API takes at most
 * four and rejects the rest rather than silently truncating. */
export const getHeadToHead = (
  key: BranchKey,
  params: { vs: string[]; feed_date?: string; q?: string; limit?: number; offset?: number },
) => api.get<HeadToHeadRow[]>(branchPath(key, "/head-to-head"), params);

export const getComparisonSummary = (key: BranchKey, params: { feed_date?: string; radius_km?: number }) =>
  api.get<ComparisonSummary>(branchPath(key, "/comparison/summary"), params);

/** The tablero's whole payload in one call. It is the summary plus the first
 * page of the comparison, which are one aggregate over one fan-out -- asking
 * for them separately ran the most expensive query in the service twice. */
export const getDashboard = (
  key: BranchKey,
  params: { feed_date?: string; radius_km?: number; limit?: number } = {},
) => api.get<Dashboard>(branchPath(key, "/dashboard"), params);

export const getBranchChanges = (
  key: BranchKey,
  params: { feed_date?: string; min_pct?: number; limit?: number },
) => api.get<ChangeRow[]>(branchPath(key, "/changes"), params);

// -- Alertas ------------------------------------------------------------------

export const getAlerts = (params: { unread_only?: boolean; limit?: number; offset?: number }) =>
  api.get<Alert[]>("/alerts", params);

export const getUnreadAlertCount = () => api.get<{ count: number }>("/alerts/unread-count");

export const markAlertRead = (alertId: number) => api.post<{ ok: boolean }>(`/alerts/${alertId}/read`);

export const markAllAlertsRead = () => api.post<{ marked: number }>("/alerts/read-all");

export const getRules = () => api.get<Rule[]>("/rules");

export const createRule = (body: {
  name: string;
  company_id: number;
  banner_id: number;
  branch_id: string;
  radius_km?: number | null;
  target_company_id?: number | null;
  target_banner_id?: number | null;
  target_branch_id?: string | null;
  threshold_pct: number;
  products: string[];
}) => api.post<{ rule_id: number }>("/rules", body);

export const setRuleEnabled = (ruleId: number, enabled: boolean) =>
  api.patch<{ ok: boolean }>(`/rules/${ruleId}`, { enabled });

export const deleteRule = (ruleId: number) => api.delete<void>(`/rules/${ruleId}`);

export const replaceRuleProducts = (ruleId: number, products: string[]) =>
  api.put<{ ok: boolean }>(`/rules/${ruleId}/products`, { products });

// -- Carga propia ---------------------------------------------------------------

export const createUpload = (body: { feed_date: string; filename: string }) =>
  api.post<UploadPresign>("/uploads", body);

export const getUploads = (limit = 50) => api.get<UploadRecord[]>("/uploads", { limit });

export const deleteUpload = (uploadId: string) =>
  api.delete<UploadDeleted>(`/uploads/${uploadId}`);

export const getUpload = (uploadId: string) => api.get<UploadDetail>(`/uploads/${uploadId}`);

export const getUploadRejected = (uploadId: string) =>
  api.get<UploadRejectedRow[]>(`/uploads/${uploadId}/rejected`);

/** The whole upload: the API signs a POST, and the file goes straight to S3.
 * Nothing else is asked of the API: S3 announces the file to the jobs
 * queue, and the worker that takes it creates the job. */
export async function uploadPriceList(file: File, feedDate: string): Promise<UploadPresign> {
  const presign = await createUpload({ feed_date: feedDate, filename: file.name });
  await postToPresignedUrl(presign.url, presign.fields, file, presign.max_bytes);
  return presign;
}

// -- Admin ------------------------------------------------------------------
// The platform admin's (Cognito group platform-admin): any other user gets 403.

export const refreshNeighbors = () => api.post<{ status: "started" }>("/admin/neighbors/refresh");

export const normalizeCatalog = () => api.post<{ products: number }>("/admin/catalog/normalize");
