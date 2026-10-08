import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button, Input, Text } from "@fluentui/react-components";
import { Eye24Regular, EyeOff24Regular } from "@fluentui/react-icons";
import { authApi } from "@/services/authApi";
import { setAuthToken } from "@/services/authToken";
import { ApiError } from "@/services/httpClient";
import { ErrorState } from "@/components/ErrorState";
import type { SafeError } from "@/types/common";

/**
 * First-party sign-in (see `app.security.auth_service`) - only ever shown
 * when `GET /auth/status` reports `auth_enabled: true` (see `AuthGate`).
 * A deliberately small form: username/password in, a bearer token out,
 * stored by `setAuthToken` for every subsequent backend call.
 */
export function LoginPage(): JSX.Element {
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<SafeError | null>(null);

  const redirectTo = (location.state as { from?: string } | null)?.from ?? "/";

  async function handleSubmit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await authApi.login(username, password);
      setAuthToken(result.access_token);
      navigate(redirectTo, { replace: true });
    } catch (err) {
      // httpClient's generic 401 message ("Your session has expired...")
      // is correct for an expired/invalid existing token, but wrong here -
      // a failed login attempt never had a session to expire.
      if (err instanceof ApiError && err.status === 401) {
        setError(new ApiError("Invalid username or password.", 401, err.correlationId));
      } else {
        setError(err instanceof ApiError ? err : new ApiError("Sign-in failed."));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        minHeight: "100vh",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{ display: "flex", flexDirection: "column", gap: 16, width: 320 }}
      >
        <div>
          <Text size={600} weight="semibold">
            ✨ Genie
          </Text>
          <div>
            <Text size={300}>Sign in to continue to the Agentic Experience Center.</Text>
          </div>
        </div>
        <Input
          placeholder="Username"
          value={username}
          onChange={(_, data) => setUsername(data.value)}
          autoFocus
          disabled={submitting}
        />
        <Input
          type={passwordVisible ? "text" : "password"}
          placeholder="Password"
          value={password}
          onChange={(_, data) => setPassword(data.value)}
          disabled={submitting}
          contentAfter={
            <Button
              type="button"
              appearance="transparent"
              size="small"
              icon={passwordVisible ? <EyeOff24Regular /> : <Eye24Regular />}
              aria-label={passwordVisible ? "Hide password" : "Show password"}
              title={passwordVisible ? "Hide password" : "Show password"}
              onClick={() => setPasswordVisible((visible) => !visible)}
              disabled={submitting}
            />
          }
        />
        {error ? <ErrorState error={error} /> : null}
        <Button
          appearance="primary"
          type="submit"
          disabled={submitting || !username || !password}
        >
          {submitting ? "Signing in..." : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
