import React from "react";
import ReactDOM from "react-dom/client";
import { FluentProvider } from "@fluentui/react-components";
import { App } from "./App";
import { SessionProvider } from "@/state/SessionContext";
import { genieDarkTheme } from "@/styles/theme";
import "@/styles/global.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <FluentProvider theme={genieDarkTheme}>
      <SessionProvider>
        <App />
      </SessionProvider>
    </FluentProvider>
  </React.StrictMode>,
);
