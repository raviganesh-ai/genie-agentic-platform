/**
 * Microsoft Entra ID (MSAL) configuration for the automatic sign-in flow.
 *
 * Every value is sourced from externalized Vite env vars
 * (VITE_ENTRA_CLIENT_ID / VITE_ENTRA_TENANT_ID / VITE_ENTRA_API_SCOPE) - never
 * hardcoded here - per the Configuration Rules in
 * .github/copilot-instructions.md. When these are not set (e.g. local
 * component tests, or a developer running `npm run dev` without Entra
 * configured), `isEntraConfigured` is false and `authProvider` falls back to
 * the manual in-memory token seam it already exposed.
 */
import type { Configuration } from "@azure/msal-browser";
import { LogLevel } from "@azure/msal-browser";

const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID;
const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID;
const apiScope = import.meta.env.VITE_ENTRA_API_SCOPE;

export const isEntraConfigured = Boolean(
  clientId && clientId.trim() && tenantId && tenantId.trim() && apiScope && apiScope.trim(),
);

export const msalConfig: Configuration = {
  auth: {
    clientId: clientId ?? "",
    authority: `https://login.microsoftonline.com/${tenantId ?? "common"}`,
    redirectUri: "/",
    postLogoutRedirectUri: "/",
  },
  cache: {
    // sessionStorage (not localStorage) limits token exposure to the current
    // tab/session, and the raw access token itself is still only ever held
    // in the in-memory seam in authProvider.ts, never read out of MSAL's
    // cache directly by application code (OWASP A02/A05).
    cacheLocation: "sessionStorage",
  },
  system: {
    loggerOptions: {
      loggerCallback: () => {
        // Intentionally silent: never log token contents or MSAL internals
        // to the browser console in any environment.
      },
      logLevel: LogLevel.Error,
    },
  },
};

/** The single delegated scope the Genie SPA requests to call its own backend API. */
export const loginRequest = {
  scopes: apiScope ? [apiScope] : [],
};
