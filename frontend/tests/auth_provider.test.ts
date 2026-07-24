import { afterEach, describe, expect, it } from "vitest";
import {
  getAccessToken,
  initializeAuth,
  isAuthenticated,
  onAccessTokenChange,
  setAccessToken,
  signOut,
} from "@/services/authProvider";

/**
 * The test environment never sets VITE_ENTRA_CLIENT_ID/TENANT_ID/API_SCOPE,
 * so `isEntraConfigured` is false and `authProvider` must fall back to the
 * original manual in-memory token seam (used by local/dev/test workflows)
 * instead of ever constructing a real MSAL PublicClientApplication.
 */
describe("authProvider (Entra ID not configured)", () => {
  afterEach(() => {
    setAccessToken(null);
  });

  it("starts signed out", () => {
    expect(getAccessToken()).toBeNull();
    expect(isAuthenticated()).toBe(false);
  });

  it("initializeAuth is a no-op without Entra config", async () => {
    await expect(initializeAuth()).resolves.toBeUndefined();
    expect(isAuthenticated()).toBe(false);
  });

  it("setAccessToken updates state and notifies listeners", () => {
    const seen: Array<string | null> = [];
    const unsubscribe = onAccessTokenChange((token) => seen.push(token));

    setAccessToken("  a-token  ");
    expect(getAccessToken()).toBe("a-token");
    expect(isAuthenticated()).toBe(true);

    signOut();
    expect(getAccessToken()).toBeNull();
    expect(isAuthenticated()).toBe(false);

    expect(seen).toEqual(["a-token", null]);
    unsubscribe();
  });

  it("blank tokens are treated as signed out", () => {
    setAccessToken("   ");
    expect(getAccessToken()).toBeNull();
    expect(isAuthenticated()).toBe(false);
  });
});
