import { useCallback, useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { authApi } from "@/services/authApi";
import { getAuthToken, subscribeAuthToken } from "@/services/authToken";
import { ApiError, UNAUTHORIZED_EVENT } from "@/services/httpClient";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import type { SafeError } from "@/types/common";

/**
 * Gates every real route behind first-party sign-in, but only when the
 * backend actually requires it (`GET /auth/status`) - a dev/test
 * deployment with auth disabled renders `children` immediately, exactly
 * as before this existed. Re-checks on every token change (sign-in,
 * sign-out, or a 401 from any backend call - see
 * `httpClient.UNAUTHORIZED_EVENT`) so an expired session bounces back to
 * `/login` without needing a full page reload.
 *
 * Fails closed when the status check itself can't be completed (network
 * error, or the API gateway rejecting the call - e.g. a missing/invalid
 * subscription key): shows a retryable error instead of guessing
 * "auth must be disabled" and rendering every route ungated. That guess
 * used to be made here, and it was wrong in exactly the scenario where it
 * mattered most - a gateway misconfiguration that fails *every* call the
 * same way - which silently let the whole app load with no sign-in
 * prompt, then broke everywhere a call was actually made.
 */
export function AuthGate({ children }: { children: JSX.Element }): JSX.Element {
  const location = useLocation();
  const [authEnabled, setAuthEnabled] = useState<boolean | null>(null);
  const [statusError, setStatusError] = useState<SafeError | null>(null);
  const [, forceRecheck] = useState(0);

  const checkStatus = useCallback(() => {
    setStatusError(null);
    authApi
      .status()
      .then((status) => setAuthEnabled(status.auth_enabled))
      .catch((err) => {
        setAuthEnabled(null);
        setStatusError(err instanceof ApiError ? err : new ApiError("Unable to reach Genie."));
      });
  }, []);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  useEffect(() => {
    const unsubscribeToken = subscribeAuthToken(() => forceRecheck((tick) => tick + 1));
    const onUnauthorized = (): void => forceRecheck((tick) => tick + 1);
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => {
      unsubscribeToken();
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    };
  }, []);

  if (statusError) {
    return (
      <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center" }}>
        <div style={{ width: 420 }}>
          <ErrorState error={statusError} onRetry={checkStatus} />
        </div>
      </div>
    );
  }
  if (authEnabled === null) return <LoadingState label="Loading Genie..." />;
  if (authEnabled && !getAuthToken()) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return children;
}
