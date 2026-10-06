import { describe, expect, it, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { FluentProvider } from "@fluentui/react-components";
import { genieDarkTheme } from "@/styles/theme";
import { AuthGate } from "@/features/auth/AuthGate";
import { LoginPage } from "@/features/auth/LoginPage";
import { getAuthToken, setAuthToken } from "@/services/authToken";
import { UNAUTHORIZED_EVENT } from "@/services/httpClient";
import { mockFetchSequence } from "./testUtils";

function renderGated(initialRoute = "/") {
  return render(
    <FluentProvider theme={genieDarkTheme}>
      <MemoryRouter initialEntries={[initialRoute]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <AuthGate>
                <div>Protected page marker</div>
              </AuthGate>
            }
          />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  );
}

describe("AuthGate", () => {
  beforeEach(() => {
    setAuthToken(null);
  });

  it("renders the protected page immediately when auth is disabled", async () => {
    mockFetchSequence([{ match: "/auth/status", response: { auth_enabled: false } }]);
    renderGated();

    await waitFor(() => expect(screen.getByText(/Protected page marker/i)).toBeInTheDocument());
  });

  it("redirects to /login when auth is enabled and no token is stored", async () => {
    mockFetchSequence([{ match: "/auth/status", response: { auth_enabled: true } }]);
    renderGated();

    await waitFor(() =>
      expect(screen.getByText(/Sign in to continue/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Protected page marker/i)).not.toBeInTheDocument();
  });

  it("renders the protected page directly when a token is already stored", async () => {
    setAuthToken("existing-token");
    mockFetchSequence([{ match: "/auth/status", response: { auth_enabled: true } }]);
    renderGated();

    await waitFor(() => expect(screen.getByText(/Protected page marker/i)).toBeInTheDocument());
  });

  it("bounces back to /login after a 401 clears the stored token", async () => {
    setAuthToken("expiring-token");
    mockFetchSequence([{ match: "/auth/status", response: { auth_enabled: true } }]);
    renderGated();

    await waitFor(() => expect(screen.getByText(/Protected page marker/i)).toBeInTheDocument());

    // Mirrors what httpClient.handleUnauthorized() actually does on a 401:
    // clear the token, then notify listeners.
    setAuthToken(null);
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));

    await waitFor(() => expect(screen.getByText(/Sign in to continue/i)).toBeInTheDocument());
  });

  it("signs in successfully, stores the token, and reaches the protected page", async () => {
    mockFetchSequence([
      { match: "/auth/status", response: { auth_enabled: true } },
      {
        match: "/auth/login",
        response: { access_token: "new-token", token_type: "bearer", expires_in_seconds: 3600 },
      },
    ]);
    renderGated();

    await waitFor(() => expect(screen.getByText(/Sign in to continue/i)).toBeInTheDocument());

    await userEvent.type(screen.getByPlaceholderText(/username/i), "alice");
    await userEvent.type(screen.getByPlaceholderText(/password/i), "alice-password");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(screen.getByText(/Protected page marker/i)).toBeInTheDocument());
    expect(getAuthToken()).toBe("new-token");
  });

  it("shows an error and stays on the login page when credentials are rejected", async () => {
    mockFetchSequence([
      { match: "/auth/status", response: { auth_enabled: true } },
      { match: "/auth/login", status: 401, response: { detail: "Invalid username or password." } },
    ]);
    renderGated();

    await waitFor(() => expect(screen.getByText(/Sign in to continue/i)).toBeInTheDocument());

    await userEvent.type(screen.getByPlaceholderText(/username/i), "alice");
    await userEvent.type(screen.getByPlaceholderText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(screen.getByText(/Invalid username or password/i)).toBeInTheDocument());
    expect(getAuthToken()).toBeNull();
  });
});
