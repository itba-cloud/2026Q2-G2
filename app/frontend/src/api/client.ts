// Thin fetch wrapper. Every call goes to `${apiUrl}/api/*`, with apiUrl from
// the config.json Terraform writes next to the portal (see config.ts): the
// portal (its bucket) and the API (API Gateway) are two origins, and API
// Gateway answers the CORS preflights. No host is written in the code.
//
// The token is the Cognito ID token the callback handed over (see
// pages/AuthCallbackPage.tsx); API Gateway validates it before the API runs.

import { portalConfig } from "../config";

const TOKEN_KEY = "lgdc_id_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Set by AuthProvider so a 401 anywhere (an expired token, mostly) can drop
// the user back on /login, without this module needing to know about React
// Router.
let unauthorizedHandler: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

async function parseErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return body.detail;
    if (body && body.detail) return JSON.stringify(body.detail);
  } catch {
    // body wasn't JSON -- fall through to the status text
  }
  return res.statusText || `HTTP ${res.status}`;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined | string[]>;
}

function buildQuery(query?: RequestOptions["query"]): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === "") continue;
    // An array becomes a repeated parameter (`?vs=a&vs=b`), which is what
    // FastAPI reads back into a list. Joining with commas would not.
    if (Array.isArray(value)) {
      for (const item of value) if (item !== "") params.append(key, String(item));
      continue;
    }
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${portalConfig().apiUrl}/api${path}${buildQuery(query)}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401) {
    clearToken();
    unauthorizedHandler?.();
    throw new ApiError(401, await parseErrorMessage(res));
  }

  if (!res.ok) {
    throw new ApiError(res.status, await parseErrorMessage(res));
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export const api = {
  get: <T>(path: string, query?: RequestOptions["query"]) =>
    apiRequest<T>(path, { method: "GET", query }),
  post: <T>(path: string, body?: unknown, query?: RequestOptions["query"]) =>
    apiRequest<T>(path, { method: "POST", body, query }),
  patch: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "PATCH", body }),
  put: <T>(path: string, body?: unknown) => apiRequest<T>(path, { method: "PUT", body }),
  delete: <T>(path: string) => apiRequest<T>(path, { method: "DELETE" }),
};

// The S3 presigned POST is a bare absolute URL and the fields of its signed
// policy, handed back by POST /uploads -- it must NOT go through apiRequest
// (no /api prefix, no bearer token, and the body is a form, not JSON).
export async function postToPresignedUrl(
  url: string,
  fields: Record<string, string>,
  file: File,
  maxBytes: number,
): Promise<void> {
  const form = new FormData();
  for (const [name, value] of Object.entries(fields)) form.append(name, value);
  // Last: S3 ignores every field after the file.
  form.append("file", file);
  const res = await fetch(url, { method: "POST", body: form });
  if (res.ok) return;
  // S3 answers in XML; the one refusal a merchant can fix is the size.
  const text = await res.text();
  const message = text.includes("EntityTooLarge")
    ? `El archivo pasa el máximo de ${Math.round(maxBytes / 1024 / 1024)} MB`
    : `upload to storage failed: ${res.statusText || res.status}`;
  throw new ApiError(res.status, message);
}
