/**
 * Minimal, isolated auth token seam.
 *
 * The frontend must authenticate every Genie API call with a Microsoft
 * Entra ID access token (see backend/app/security/token_validator.py). This
 * module deliberately isolates *how* that token is obtained behind one
 * function so a full MSAL (@azure/msal-browser) authorization-code/PKCE
 * flow can be dropped in later without touching any API client or
 * component - per "isolate assumptions behind interfaces" in
 * .github/copilot-instructions.md.
 *
 * The token is held only in memory (module-level variable) - never in
 * localStorage/sessionStorage/cookies - to limit exposure if the page is
 * compromised (OWASP A02/A05).
 */

let currentAccessToken: string | null = null;
const listeners = new Set<(token: string | null) => void>();

export function getAccessToken(): string | null {
  return currentAccessToken;
}

export function setAccessToken(token: string | null): void {
  currentAccessToken = token && token.trim().length > 0 ? token.trim() : null;
  for (const listener of listeners) listener(currentAccessToken);
}

export function isAuthenticated(): boolean {
  return currentAccessToken !== null;
}

export function onAccessTokenChange(listener: (token: string | null) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function signOut(): void {
  setAccessToken(null);
}
