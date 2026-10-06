/**
 * Holds the current first-party login bearer token (see
 * `app.security.auth_service` on the backend). Persisted to
 * `sessionStorage` (not `localStorage`) so a reload keeps the user signed
 * in for that tab/session but a token never silently outlives the browser
 * session. Deliberately separate from `SessionContext` - this is
 * authentication identity, not mission/workflow state.
 */
const STORAGE_KEY = "genie_auth_token";

let currentToken: string | null =
  typeof sessionStorage !== "undefined" ? sessionStorage.getItem(STORAGE_KEY) : null;

type Listener = () => void;
const listeners = new Set<Listener>();

export function getAuthToken(): string | null {
  return currentToken;
}

export function setAuthToken(token: string | null): void {
  currentToken = token;
  if (typeof sessionStorage !== "undefined") {
    if (token) sessionStorage.setItem(STORAGE_KEY, token);
    else sessionStorage.removeItem(STORAGE_KEY);
  }
  for (const listener of listeners) listener();
}

/** Lets React components (e.g. the login gate) re-render when sign-in/sign-out happens. */
export function subscribeAuthToken(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
