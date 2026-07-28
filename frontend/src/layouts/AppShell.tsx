import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Button, Switch, Text } from "@fluentui/react-components";
import { getActiveAccountName, isAuthenticated, onAccessTokenChange, signOut } from "@/services/authProvider";
import { useSessionContext } from "@/state/SessionContext";
import { TriagePanel } from "@/features/triage/TriagePanel";

/** What must exist before a step becomes reachable/clickable. */
type StepPrerequisite = "none" | "session" | "run";

const NAV_ITEMS: Array<{ to: string; label: string; requires: StepPrerequisite }> = [
  { to: "/", label: "Landing", requires: "none" },
  { to: "/upload", label: "Upload", requires: "session" },
  { to: "/requirements", label: "Requirements", requires: "session" },
  { to: "/architecture-studio", label: "Architecture", requires: "run" },
  { to: "/workshop", label: "UI & Agent Design", requires: "run" },
  { to: "/governance", label: "Governance", requires: "session" },
  { to: "/outputs", label: "Deploy & Launch", requires: "session" },
];

function isStepReachable(
  requires: StepPrerequisite,
  sessionId: string | null,
  workflowRunId: string | null,
): boolean {
  if (requires === "none") return true;
  if (requires === "session") return sessionId !== null;
  return workflowRunId !== null;
}

function navLinkStyle(isActive: boolean): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "9px 12px",
    borderRadius: 6,
    textDecoration: "none",
    color: isActive ? "#0b0f14" : "#c7cdd6",
    backgroundColor: isActive ? "#2f83e0" : "transparent",
    boxShadow: isActive ? "0 2px 10px rgba(47, 131, 224, 0.35)" : "none",
    fontSize: 14,
    fontWeight: isActive ? 600 : 400,
    marginBottom: 2,
  };
}

const stepBadgeStyle = (locked: boolean, isActive: boolean): React.CSSProperties => ({
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 20,
  height: 20,
  flexShrink: 0,
  borderRadius: "50%",
  fontSize: 11,
  fontWeight: 600,
  backgroundColor: isActive ? "rgba(11, 15, 20, 0.2)" : "rgba(199, 205, 214, 0.12)",
  color: locked ? "#5c6572" : isActive ? "#0b0f14" : "#c7cdd6",
});

export function AppShell(): JSX.Element {
  const [signedIn, setSignedIn] = useState(isAuthenticated());
  const [triageOn, setTriageOn] = useState(false);
  const location = useLocation();
  const { sessionId, workflowRunId } = useSessionContext();

  useEffect(() => onAccessTokenChange((token) => setSignedIn(token !== null)), []);

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav
        style={{
          width: 240,
          flexShrink: 0,
          backgroundColor: "#11161d",
          borderRight: "1px solid #232a33",
          padding: 16,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div style={{ marginBottom: 20 }}>
          <Text weight="bold" size={500} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="genie-sparkle" aria-hidden="true">
              ✨
            </span>
            Genie
          </Text>
          <Text size={200} style={{ display: "block", opacity: 0.7 }}>
            Agentic Experience Center
          </Text>
        </div>
        {NAV_ITEMS.map((item, index) => {
          const stepNumber = index + 1;
          const reachable = isStepReachable(item.requires, sessionId, workflowRunId);

          if (!reachable) {
            return (
              <div
                key={item.to}
                aria-disabled="true"
                title="Complete the earlier steps to unlock this"
                style={{ ...navLinkStyle(false), color: "#5c6572", cursor: "not-allowed" }}
              >
                <span style={stepBadgeStyle(true, false)}>{stepNumber}</span>
                {item.label}
                <span style={{ marginLeft: "auto", opacity: 0.6 }} aria-hidden="true">
                  🔒
                </span>
              </div>
            );
          }

          return (
            <NavLink key={item.to} to={item.to} style={({ isActive }) => navLinkStyle(isActive)} end={item.to === "/"}>
              {({ isActive }) => (
                <>
                  <span style={stepBadgeStyle(false, isActive)}>{stepNumber}</span>
                  {item.label}
                </>
              )}
            </NavLink>
          );
        })}
        <div style={{ marginTop: "auto", paddingTop: 16, borderTop: "1px solid #232a33" }}>
          <div style={{ marginBottom: 12, paddingBottom: 12, borderBottom: "1px solid #232a33" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <Text size={200} weight="semibold">
                🎮 Triage Mode
              </Text>
              <Switch
                checked={triageOn}
                onChange={(_, data) => setTriageOn(data.checked)}
                aria-label="Toggle triage mode"
              />
            </div>
            <Text size={100} style={{ opacity: 0.6, display: "block" }}>
              Gamified live agent call traceability
            </Text>
          </div>
          {signedIn ? (
            <>
              <Text size={200} style={{ display: "block", marginBottom: 8, opacity: 0.85 }}>
                👋 Welcome back, {getActiveAccountName() ?? "there"}
              </Text>
              <Button size="small" appearance="secondary" onClick={signOut}>
                Sign out
              </Button>
            </>
          ) : (
            <Text size={200} style={{ opacity: 0.7 }}>
              Not signed in
            </Text>
          )}
        </div>
      </nav>
      <main
        key={location.pathname}
        className="genie-fade-in"
        style={{
          flex: 1,
          padding: 24,
          paddingRight: triageOn ? 340 + 24 : 24,
          overflowY: "auto",
          transition: "padding-right 200ms ease",
        }}
      >
        <Outlet />
      </main>
      <TriagePanel enabled={triageOn} />
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}): JSX.Element {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        marginBottom: 20,
      }}
    >
      <div>
        <Text weight="bold" size={600} style={{ display: "block" }}>
          {title}
        </Text>
        {subtitle ? (
          <Text size={300} style={{ opacity: 0.7 }}>
            {subtitle}
          </Text>
        ) : null}
      </div>
      {action}
    </div>
  );
}
