import React from "react";
import ReactDOM from "react-dom/client";
import { FluentProvider } from "@fluentui/react-components";
import { App } from "./App";
import { SessionProvider } from "@/state/SessionContext";
import { genieDarkTheme } from "@/styles/theme";
import { initializeAuth } from "@/services/authProvider";
import "@/styles/global.css";

/**
 * `initializeAuth()` completes the automatic Microsoft Entra ID sign-in
 * flow before the app ever renders. When no user is signed in yet, it
 * redirects the browser to Microsoft's sign-in page - this function never
 * resolves in that case (the page navigates away), so nothing renders,
 * which is the desired behavior (no flash of an unauthenticated app shell).
 */
async function bootstrap(): Promise<void> {
  await initializeAuth();

  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <FluentProvider theme={genieDarkTheme}>
        <SessionProvider>
          <App />
        </SessionProvider>
      </FluentProvider>
    </React.StrictMode>,
  );
}

void bootstrap();
