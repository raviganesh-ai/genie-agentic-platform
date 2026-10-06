import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { authApi } from "@/services/authApi";
import { getAuthToken, subscribeAuthToken } from "@/services/authToken";
import { UNAUTHORIZED_EVENT } from "@/services/httpClient";
import { LoadingState } from "@/components/LoadingState";

/**
 * Gates every real route behind first-party sign-in, but only when the
 * backend actually requires it (`GET /auth/status`) - a dev/test
 * deployment with auth disabled renders `children` immediately, exactly
 * as before this existed. Re-checks on every token change (sign-in,
 * sign-out, or a 401 from any backend call - see
 * `httpClient.UNAUTHORIZED_EVENT`) so an expired session bounces back to
 * `/login` without needing a full page reload.
 */
export function AuthGate({ children }: { children: JSX.Element }): JSX.Element {
  const location = useLocation();
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [, forceRecheck] = useState(0);

  useEffect(() => {
    let cancelled = false;
    authApi
      .status()
      .then((status) => {
        if (!cancelled) setAuthEnabled(status.auth_enabled);
      })
      .catch(() => {
        // The backend is unreachable - every protected call will fail the
        // same way regardless, so there is nothing meaningful to gate here.
        if (!cancelled) setAuthEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const unsubscribeToken = subscribeAuthToken(() => forceRecheck((tick) => tick + 1));
    const onUnauthorized = (): void => forceRecheck((tick) => tick + 1);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => {
      unsubscribeToken();
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    };
  }, []);

  if (authEnabled === null) return <LoadingState label="Loading Genie..." />;
  if (authEnabled && !getAuthToken()) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return children;
}
