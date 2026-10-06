import type { SafeError } from "@/types/common";
import { getAuthToken, setAuthToken } from "./authToken";

/**
 * The Genie backend base URL. Configurable via VITE_GENIE_API_BASE_URL so
 * different environments (dev/staging/prod) can point at different
 * Container Apps/App Service deployments without any code change - never
 * a Foundry endpoint, never hardcoded per environment in source.
 */
const API_BASE_URL: string =
  (import.meta.env.VITE_GENIE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

/**
 * The platform APIM subscription key, required on every call once the
 * gateway sets `subscriptionRequired: true` (see
 * infra/platform-private-gateway.bicep). Unset in local dev, where the
 * backend is called directly (no APIM in front of it) - `undefined` simply
 * means the header is omitted, matching that APIM isn't in the path at all.
 */
const APIM_SUBSCRIPTION_KEY: string | undefined = import.meta.env
  .VITE_GENIE_APIM_SUBSCRIPTION_KEY as string | undefined;

/** Dispatched whenever a backend call is rejected with 401, after the
 * stored token (if any) has already been cleared - the one place the app
 * needs to know "the user must sign in again", regardless of which page
 * or hook triggered the call. */
export const UNAUTHORIZED_EVENT = "genie:unauthorized";

function buildAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (APIM_SUBSCRIPTION_KEY) headers["Ocp-Apim-Subscription-Key"] = APIM_SUBSCRIPTION_KEY;
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function handleUnauthorized(): void {
  if (!getAuthToken()) return; // already signed out - avoid a redundant event
  setAuthToken(null);
  if (typeof window !== "undefined") window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

/** Exposes the configured backend origin for building absolute links to
 * backend-rendered routes (e.g. the cx customer prototype surface) that
 * aren't fetched via `apiFetch` but still need to be shown/copied as a
 * full URL in the UI. */
export function getApiBaseUrl(): string {
  return API_BASE_URL;
}

export class ApiError extends Error implements SafeError {
  status?: number;
  correlationId?: string;

  constructor(message: string, status?: number, correlationId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.correlationId = correlationId;
  }
}

export interface ApiRequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  /** For multipart/form-data uploads - when set, `body` is sent as-is (a FormData). */
  isFormData?: boolean;
}

function buildUrl(path: string, query?: ApiRequestOptions["query"]): string {
  const url = new URL(path.replace(/^\//, ""), `${API_BASE_URL}/`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

/**
 * A single typed HTTP call to the Genie backend. Never calls Azure AI
 * Foundry directly - every path here is a Genie FastAPI route.
 *
 * Errors are always mapped to a `SafeError` (human-readable message +
 * optional correlation id) - never a raw stack trace, token, connection
 * string, or Foundry identifier.
 */
export async function apiFetch<TResponse>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<TResponse> {
  const { method = "GET", body, query, isFormData = false } = options;
  const correlationId =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}`;

  const headers: Record<string, string> = {
    "X-Correlation-Id": correlationId,
    ...buildAuthHeaders(),
  };
  if (!isFormData && body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body: body === undefined ? undefined : isFormData ? (body as FormData) : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      "Unable to reach the Genie backend. Check your connection and try again.",
      undefined,
      correlationId,
    );
  }

  if (response.status === 401) handleUnauthorized();
  if (response.status === 204) return undefined as TResponse;

  const rawText = await response.text();
  const parsed = rawText ? safeJsonParse(rawText) : undefined;

  if (!response.ok) {
    const detail =
      parsed && typeof parsed === "object" && parsed !== null && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : undefined;
    throw new ApiError(
      mapStatusToMessage(response.status, detail),
      response.status,
      response.headers.get("X-Correlation-Id") ?? correlationId,
    );
  }

  return parsed as TResponse;
}

/**
 * Opens a Server-Sent Events connection to a Genie backend streaming route
 * (currently only the live workflow-events route). Kept here - rather than
 * in the calling hook - so the "every HTTP call goes through httpClient"
 * rule (see tests/no_foundry_direct_access.test.tsx) still holds: this is
 * the one place in the app that needs raw `fetch` + a `ReadableStream` body
 * (SSE) instead of `apiFetch`'s buffered JSON response handling. Auth still
 * remains centralized with every other backend request.
 */
export async function openEventStream(path: string, signal: AbortSignal): Promise<Response> {
  const headers: Record<string, string> = { Accept: "text/event-stream", ...buildAuthHeaders() };

  let response: Response;
  try {
    response = await fetch(buildUrl(path), { headers, signal });
  } catch (err) {
    if (signal.aborted) throw err;
    throw new ApiError("Unable to reach the Genie backend. Check your connection and try again.");
  }

  if (response.status === 401) handleUnauthorized();
  if (!response.ok || !response.body) {
    throw new ApiError(mapStatusToMessage(response.status), response.status);
  }
  return response;
}

/**
 * Downloads a binary response (currently only the Deploy & Launch code +
 * access-policy zip archive) as a `Blob`. Kept here - rather than in the calling
 * service - for the same "every HTTP call goes through httpClient" reason
 * as `openEventStream` above: `apiFetch` always parses its response body as
 * JSON, which would corrupt a binary zip archive.
 */
export async function downloadBinary(path: string): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = { ...buildAuthHeaders() };

  let response: Response;
  try {
    response = await fetch(buildUrl(path), { headers });
  } catch {
    throw new ApiError("Unable to reach the Genie backend. Check your connection and try again.");
  }

  if (response.status === 401) handleUnauthorized();
  if (!response.ok) {
    throw new ApiError(mapStatusToMessage(response.status), response.status);
  }

  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filenameMatch = /filename="?([^"]+)"?/.exec(disposition);
  const filename = filenameMatch?.[1] ?? "download.zip";
  return { blob: await response.blob(), filename };
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function mapStatusToMessage(status: number, detail?: string): string {
  if (status === 401) return "Your session has expired. Please sign in again.";
  if (status === 403) return detail ?? "You do not have access to this resource.";
  if (status === 404) return detail ?? "The requested resource was not found.";
  if (status === 409) return detail ?? "This action conflicts with the current state.";
  if (status === 422) return detail ?? "This request could not be routed.";
  if (status >= 500) return "The Genie backend encountered an error. Please try again.";
  return detail ?? "The request could not be completed.";
}
