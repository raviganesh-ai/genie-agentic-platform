/**
 * Automatic Microsoft Entra ID authentication for the Genie SPA.
 *
 * Every Genie API call must carry a Microsoft Entra ID access token (see
 * backend/app/security/token_validator.py). This module owns the *entire*
 * sign-in lifecycle so no other code ever has to think about it:
 *
 *   - `initializeAuth()` (called once, before the app renders) completes any
 *     in-flight redirect, and if no user is signed in, automatically starts
 *     the Microsoft Entra ID sign-in redirect - no manual token entry.
 *   - Once signed in, the access token is silently acquired and proactively
 *     refreshed on a timer, so it never has to be re-entered.
 *   - `getAccessToken()` stays a *synchronous* read of the last-acquired
 *     token (an in-memory module variable, never localStorage/cookies - see
 *     OWASP A02/A05) so `httpClient.ts` and every API client are completely
 *     unaware that MSAL exists behind this seam - per "isolate assumptions
 *     behind interfaces" in .github/copilot-instructions.md.
 *
 * When Entra ID is not configured (`isEntraConfigured` false - e.g. local
 * component tests, or `npm run dev` without the VITE_ENTRA_* env vars), this
 * module never talks to MSAL at all and falls back to the original manual
 * `setAccessToken()` seam, so existing local/dev workflows keep working.
 */
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import { isEntraConfigured, loginRequest, msalConfig } from "./msalConfig";

const SILENT_REFRESH_INTERVAL_MS = 5 * 60 * 1000;

let currentAccessToken: string | null = null;
const listeners = new Set<(token: string | null) => void>();

let msalInstance: PublicClientApplication | null = null;
let silentRefreshTimer: ReturnType<typeof setInterval> | null = null;

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

/** Display name of the signed-in user, or null if Entra ID isn't in use. */
export function getActiveAccountName(): string | null {
  const account = msalInstance?.getActiveAccount();
  return account?.name ?? account?.username ?? null;
}

/**
 * Completes MSAL setup and guarantees that by the time it resolves, either:
 *   (a) a valid access token has been acquired and stored, or
 *   (b) the browser has been redirected away to Microsoft's sign-in page
 *       (in which case the caller should render nothing further - the page
 *       is about to unload).
 *
 * No-ops when Entra ID isn't configured, leaving the manual token seam
 * (`setAccessToken`) as the only way to authenticate - used in local/dev/test.
 */
export async function initializeAuth(): Promise<void> {
  if (!isEntraConfigured) return;

  msalInstance = new PublicClientApplication(msalConfig);
  await msalInstance.initialize();

  const redirectResult = await msalInstance.handleRedirectPromise();
  if (redirectResult?.account) {
    msalInstance.setActiveAccount(redirectResult.account);
  }

  const account: AccountInfo | null =
    msalInstance.getActiveAccount() ?? msalInstance.getAllAccounts()[0] ?? null;

  if (!account) {
    // No signed-in user yet: redirect immediately. Execution resumes on the
    // next page load, after Entra ID redirects back with an auth code.
    await msalInstance.loginRedirect(loginRequest);
    return;
  }

  msalInstance.setActiveAccount(account);
  await refreshAccessToken();
  startSilentRefreshTimer();
}

async function refreshAccessToken(): Promise<void> {
  if (!msalInstance) return;
  const account = msalInstance.getActiveAccount();
  if (!account) return;

  try {
    const result = await msalInstance.acquireTokenSilent({ ...loginRequest, account });
    setAccessToken(result.accessToken);
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      // Silent refresh can't proceed without user interaction (e.g. revoked
      // consent, expired session) - fall back to a full redirect.
      await msalInstance.acquireTokenRedirect(loginRequest);
      return;
    }
    setAccessToken(null);
  }
}

function startSilentRefreshTimer(): void {
  if (silentRefreshTimer) return;
  silentRefreshTimer = setInterval(() => {
    void refreshAccessToken();
  }, SILENT_REFRESH_INTERVAL_MS);
}

export function signOut(): void {
  setAccessToken(null);
  if (silentRefreshTimer) {
    clearInterval(silentRefreshTimer);
    silentRefreshTimer = null;
  }
  if (msalInstance) {
    void msalInstance.logoutRedirect();
  }
}
