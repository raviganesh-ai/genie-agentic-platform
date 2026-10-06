import type { ReactNode } from "react";
import { useCallback, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Button, MessageBar, MessageBarBody, MessageBarTitle, Switch, Text } from "@fluentui/react-components";
import { useSessionContext, type MissionKind } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { workflowApi } from "@/services/workflowApi";
import { TriagePanel } from "@/features/triage/TriagePanel";
import { isSessionExpiredError } from "@/types/common";
import { getAuthToken, setAuthToken } from "@/services/authToken";

interface NavItemConfig {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
  aliases?: string[];
  /** What must exist before this step has anything real to show - see
   * readiness gating below. `"session"` only needs a created session;
   * `"run"` needs an active workflow run (every step past the point a
   * mission's workflow actually starts). */
  requires?: "session" | "run";
}

const NAV_ITEMS: NavItemConfig[] = [
  { to: "/", label: "Home", icon: "🏠", end: true },
  { to: "/upload", label: "Upload", icon: "📤", requires: "session" },
  { to: "/requirements", label: "Requirements", icon: "📋", aliases: ["/discovery"], requires: "session" },
  {
    to: "/repository-connections",
    label: "Repository Analysis",
    icon: "🔗",
    aliases: [
      "/dependency-mapping",
      "/iq-collaboration",
      "/modernization",
    ],
    requires: "session",
  },
  { to: "/architecture-studio", label: "UI & Agent Design", icon: "🏗️", requires: "run" },
  { to: "/workshop", label: "Build", icon: "🤖", requires: "run" },
  { to: "/phases", label: "Governance", icon: "🛡️", requires: "run" },
  {
    to: "/outputs",
    label: "Deploy & Launch",
    icon: "🚀",
    aliases: ["/production-promotion"],
    requires: "run",
  },
];

/**
 * Which steps actually apply to a given mission kind (see
 * SessionContext.MissionKind). Not every ask needs every step - e.g.
 * modernizing existing code has no use for Upload/Requirements, and
 * reviewing governance or deploying doesn't need a fresh design pass.
 * `null`/unmapped kinds fall back to showing every step (legacy sessions,
 * direct deep links, or a kind this map hasn't been taught yet).
 */
const MISSION_KIND_STEPS: Record<MissionKind, string[]> = {
  discover_requirements: ["/", "/upload", "/requirements", "/architecture-studio", "/workshop", "/phases", "/outputs"],
  understand_code: ["/", "/repository-connections", "/architecture-studio", "/workshop", "/phases", "/outputs"],
  modernize_and_deliver: ["/", "/repository-connections", "/phases", "/outputs"],
};

function findCurrentNavIndex(items: NavItemConfig[], pathname: string): number {
  return items.findIndex((item) => {
    const paths = [item.to, ...(item.aliases ?? [])];
    return paths.some((path) =>
      path === "/"
        ? pathname === path
        : pathname === path || pathname.startsWith(`${path}/`),
    );
  });
}

function navLinkStyle(isActive: boolean, disabled: boolean): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 10px",
    borderRadius: 6,
    textDecoration: "none",
    color: disabled ? "#5a626c" : isActive ? "#0b0f14" : "#c7cdd6",
    backgroundColor: isActive ? "#2f83e0" : "transparent",
    boxShadow: isActive ? "0 2px 10px rgba(47, 131, 224, 0.35)" : "none",
    fontSize: 14,
    fontWeight: isActive ? 600 : 400,
    width: "100%",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.55 : 1,
  };
}

// Polling for the mission flow/stage statuses. Disabled by default (0) -
// pages independently poll for their own data, and polling the top-level run
// here just causes aggressive re-renders and a "flickering" feel. Set via
// VITE_MISSION_FLOW_POLL_MS if needed for specific scenarios.
const MISSION_FLOW_POLL_MS = Number(import.meta.env.VITE_MISSION_FLOW_POLL_MS ?? 0);

export function AppShell(): JSX.Element {
  // Default to on: without this, starting a workflow run gives no visual
  // feedback at all until the user discovers and manually flips the
  // sidebar switch, leaving them wondering if anything is happening.
  const [triageOn, setTriageOn] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();
  const {
    sessionId,
    workflowRunId,
    missionKind,
    setSessionId,
    setWorkflowRunId,
    setMissionKind,
    setMissionStartedAt,
    setMissionError,
    setSelectedModelDeploymentRef,
  } = useSessionContext();

  // Only show the steps relevant to what the user actually asked for -
  // falls back to every step for legacy/unknown-kind sessions and direct
  // deep links, so nothing becomes unreachable.
  const visibleNavItems = useMemo(() => {
    const allowedPaths = missionKind ? MISSION_KIND_STEPS[missionKind] : null;
    return allowedPaths ? NAV_ITEMS.filter((item) => allowedPaths.includes(item.to)) : NAV_ITEMS;
  }, [missionKind]);

  const currentIndex = findCurrentNavIndex(visibleNavItems, location.pathname);

  // A step is only actually clickable once its data can exist - otherwise
  // "Mission Flow" numbering a step that does nothing yet is misleading.
  // Home is always open; Upload/Requirements/Repository Analysis just need
  // a created session; everything past that needs the mission's workflow
  // run to have actually started.
  const isStepReady = useCallback(
    (item: NavItemConfig): boolean => {
      if (!item.requires) return true;
      if (item.requires === "session") return Boolean(sessionId);
      return Boolean(sessionId && workflowRunId);
    },
    [sessionId, workflowRunId],
  );

  const runFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? workflowApi.getRun(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { error: runError } = useAsyncResource(runFetcher, [sessionId, workflowRunId], {
    enabled: Boolean(sessionId && workflowRunId),
    pollIntervalMs: MISSION_FLOW_POLL_MS,
  });
  // Sessions live only in the backend's in-memory store - any backend
  // restart/redeploy since this browser tab's session was created makes
  // every poll here 404 forever with no other symptom (see
  // isSessionExpiredError doc comment). Surface that clearly instead of
  // leaving the current page's own "in progress" animation spinning
  // forever with no explanation.
  const sessionExpired = isSessionExpiredError(runError);

  const handleStartNewMission = useCallback(() => {
    setSessionId(null);
    setWorkflowRunId(null);
    setMissionKind(null);
    setMissionStartedAt(null);
    setMissionError(null);
    setSelectedModelDeploymentRef(null);
    navigate("/");
  }, [
    navigate,
    setMissionError,
    setMissionKind,
    setMissionStartedAt,
    setSelectedModelDeploymentRef,
    setSessionId,
    setWorkflowRunId,
  ]);

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav
        style={{
          width: 264,
          flexShrink: 0,
          backgroundColor: "#11161d",
          backgroundImage: "radial-gradient(circle at 0% 0%, rgba(47, 131, 224, 0.10), transparent 55%)",
          borderRight: "1px solid #232a33",
          padding: 16,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div style={{ marginBottom: 22 }}>
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

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <Text size={100} className="genie-stage-eyebrow" style={{ opacity: 0.55, fontWeight: 700 }}>
            Mission Flow
          </Text>
          {workflowRunId ? (
            <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span className="genie-live-dot" aria-hidden="true" />
              <Text size={100} style={{ opacity: 0.65, fontWeight: 600, letterSpacing: 1 }}>
                LIVE
              </Text>
            </span>
          ) : null}
        </div>

        <Text size={100} style={{ opacity: 0.62, marginBottom: 12 }}>
          Steps unlock as your mission progresses.
        </Text>
        {visibleNavItems.map((item, index) => {
          const selected = index === currentIndex;
          const ready = isStepReady(item);
          const label = index === 0 ? item.label : `${index}. ${item.label}`;

          if (!ready) {
            return (
              <span
                key={item.to}
                aria-disabled="true"
                title={
                  item.requires === "session"
                    ? "Start a mission from Home first."
                    : "This unlocks once your mission's workflow has started."
                }
                style={navLinkStyle(false, true)}
              >
                <span aria-hidden="true">{item.icon}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {label}
                </span>
              </span>
            );
          }

          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              style={navLinkStyle(selected, false)}
            >
              <span aria-hidden="true">{item.icon}</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {label}
              </span>
            </NavLink>
          );
        })}
        <div style={{ marginTop: "auto", paddingTop: 16, borderTop: "1px solid #232a33" }}>
          <NavLink
            to="/configure"
            style={({ isActive }) => navLinkStyle(isActive, false)}
          >
            <span aria-hidden="true">⚙️</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              Configure
            </span>
          </NavLink>
          <div style={{ marginTop: 12, marginBottom: 12, paddingBottom: 12, borderBottom: "1px solid #232a33" }}>
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
          {getAuthToken() ? (
            <Button
              size="small"
              appearance="subtle"
              onClick={() => {
                setAuthToken(null);
                navigate("/login", { replace: true });
              }}
            >
              🚪 Sign out
            </Button>
          ) : null}
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
        {sessionExpired ? (
          <MessageBar intent="error" layout="multiline">
            <MessageBarBody>
              <MessageBarTitle>Your session has expired</MessageBarTitle>
              This mission's session is no longer available on the backend (it may have been
              restarted since this page was opened). Your progress on this session can't be
              recovered - start a new mission to continue.
              <div style={{ marginTop: 8 }}>
                <Button size="small" appearance="primary" onClick={handleStartNewMission}>
                  Start a new mission
                </Button>
              </div>
            </MessageBarBody>
          </MessageBar>
        ) : (
          <Outlet />
        )}
      </main>
      <TriagePanel enabled={triageOn && !sessionExpired} />
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
