import { apiFetch } from "./httpClient";

export interface AuthStatus {
  auth_enabled: boolean;
}

export interface LoginResult {
  access_token: string;
  token_type: string;
  expires_in_seconds: number;
}

export const authApi = {
  status(): Promise<AuthStatus> {
    return apiFetch<AuthStatus>("/auth/status");
  },
  login(username: string, password: string): Promise<LoginResult> {
    return apiFetch<LoginResult>("/auth/login", {
      method: "POST",
      body: { username, password },
    });
  },
};
