/**
 * Generic, cross-cutting API response shapes shared across every Genie
 * backend router (mirrors backend/app/models/api_response_models.py 1:1).
 */

export interface ErrorResponse {
  detail: string;
}

export interface MessageResponse {
  message: string;
}

/** A safe, non-sensitive error surfaced to the UI. Never carries stack traces,
 * secrets, tokens, or internal Azure resource identifiers. */
export interface SafeError {
  message: string;
  status?: number;
  correlationId?: string;
}

/**
 * True when a `SafeError` is the backend's `SessionNotFoundError` (see
 * backend/app/api/error_mapping.py) - a 404 whose `detail`/message is
 * literally "Unknown session id '<id>'." Sessions live only in the
 * backend's in-memory store (never Cosmos/SQL), so this happens whenever
 * the container has restarted/redeployed since the browser's session was
 * created - the mission is unrecoverable and the user must start a new one,
 * rather than the page silently polling forever with no explanation.
 */
export function isSessionExpiredError(error: SafeError | null | undefined): boolean {
  return Boolean(error && error.status === 404 && /unknown session id/i.test(error.message));
}
