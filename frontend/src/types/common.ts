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
