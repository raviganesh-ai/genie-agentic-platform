import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Button, MessageBar, MessageBarBody, MessageBarTitle, Switch, Text } from "@fluentui/react-components";
import { getActiveAccountName, isAuthenticated, onAccessTokenChange, signOut } from "@/services/authProvider";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { workflowApi } from "@/services/workflowApi";
import { TriagePanel } from "@/features/triage/TriagePanel";
import type { WorkflowRunResult } from "@/types/workflow";
import { isSessionExpiredError } from "@/types/common";

/** Where a stage sits on the guided mission flow at any given moment. */
type StageStatus = "locked" | "active" | "complete";

interface NavItemConfig {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
  /**
   * Presence of this step id (any status - completed or failed, run just
   * has to have reached it) in the active workflow run's `step_results`
   * marks this stage complete. Landing/Upload/Deploy & Launch use bespoke
   * checks below instead, since they aren't gated by a single workflow
   * step id.
   */
  completionStepId?: string;
}

// Each stage's completion is defined by the *next* step having started,
// not by its "own" step id - this is what naturally captures the human
// approval checkpoint that sits between steps (e.g. Requirements isn't
// really "done" from the user's point of view until design-architecture
// has been kicked off, which only happens after the requirements approval
// is granted). build-solution is the discovery workflow's LAST step, so
// there is no next step to key off for "UI & Agent Design" - it (and
// Deploy & Launch after it) instead falls through to the whole-run
// completion check below.
const NAV_ITEMS: NavItemConfig[] = [
  { to: "/", label: "Landing", icon: "🏠", end: true },
  { to: "/upload", label: "Upload", icon: "📤" },
  { to: "/requirements", label: "Requirements", icon: "📋", completionStepId: "design-architecture" },
  { to: "/architecture-studio", label: "Architecture", icon: "🏗️", completionStepId: "build-solution" },
  { to: "/workshop", label: "UI & Agent Design", icon: "🤖" },
  { to: "/outputs", label: "Deploy & Launch", icon: "🚀" },
];

/**
 * Finds which NAV_ITEMS entry the browser is currently sitting on (matching
 * sub-routes too, e.g. "/outputs/replay" -> the "/outputs" item), or -1 if
 * the current route isn't part of the guided flow at all.
 */
function findCurrentNavIndex(pathname: string): number {
  let matchIndex = -1;
  NAV_ITEMS.forEach((item, index) => {
    if (item.to === "/") {
      if (pathname === "/") matchIndex = index;
      return;
    }
    if (pathname === item.to || pathname.startsWith(`${item.to}/`)) {
      matchIndex = index;
    }
  });
  return matchIndex;
}

/**
 * Derives each stage's traffic-light status from real mission state
 * (session id, workflow run id, and the run's own step results), plus the
 * page the user is actually looking at right now. That last input matters
 * because several pages `navigate()` to the NEXT stage immediately after an
 * approval and only resume/execute the underlying workflow step in the
 * background (see ArchitectureStudioPage/WorkshopPage) - `step_results`
 * only gains an entry once a step actually FINISHES, so relying on polled
 * step data alone can show the stage the user is literally viewing as
 * still "locked" for the several seconds/minutes it takes that step to
 * complete. The current route is real ground truth that the user has at
 * least reached that stage - it never hardcodes what the *next* stage
 * should be, only reconciles a lag in already-reachable state.
 */
function computeStageStatuses(
  sessionId: string | null,
  workflowRunId: string | null,
  run: WorkflowRunResult | null,
  currentIndex: number,
  maxReachedIndex: number,
): StageStatus[] {
  const reachedStepIds = new Set(run?.step_results.map((result) => result.step_id) ?? []);
  const statuses: StageStatus[] = [];
  let previousComplete = true; // Landing is always reachable.

  NAV_ITEMS.forEach((item, index) => {
    let reachable = index === 0 ? true : previousComplete;
    let complete: boolean;
    if (item.to === "/") {
      complete = sessionId !== null;
    } else if (item.to === "/upload") {
      complete = workflowRunId !== null;
    } else if (item.completionStepId) {
      complete = reachedStepIds.has(item.completionStepId);
    } else {
      // UI & Agent Design / Deploy & Launch: build-solution is now the
      // discovery workflow's last step, so both are only truly done once
      // the whole run completes (there is no later step's presence to
      // infer this from any more - see the comment above NAV_ITEMS).
      complete = run?.status === "completed";
    }
    if (index <= maxReachedIndex) {
      // Once a stage has been reached in this run, keep it reachable even
      // when the user navigates back to an earlier stage before poll data
      // catches up (e.g. Architecture <-> Workshop back-and-forth).
      reachable = true;
    }
    if (index < currentIndex) {
      // The user has already navigated past this stage - it's done, no
      // matter what the (possibly still-catching-up) poll data says.
      reachable = true;
      complete = true;
    } else if (index === currentIndex) {
      reachable = true;
    }
    statuses.push(!reachable ? "locked" : complete ? "complete" : "active");
    previousComplete = reachable && complete;
  });

  return statuses;
}

function StageNode({ status, icon }: { status: StageStatus; icon: string }): JSX.Element {
  const ringColor = status === "complete" ? "#3fa66a" : status === "active" ? "#d99a2b" : "#3a4250";
  const glowClass =
    status === "complete" ? "genie-stage-node-complete" : status === "active" ? "genie-stage-node-active" : "genie-stage-node-locked";

  return (
    <span
      className={glowClass}
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 38,
        height: 38,
        borderRadius: "50%",
        flexShrink: 0,
        backgroundColor: "#161c24",
        border: `2px solid ${ringColor}`,
        fontSize: 17,
      }}
    >
      <span aria-hidden="true">{icon}</span>
      {status === "complete" ? (
        <span
          aria-label="Complete"
          style={{
            position: "absolute",
            bottom: -3,
            right: -3,
            width: 16,
            height: 16,
            borderRadius: "50%",
            backgroundColor: "#3fa66a",
            color: "#0b0f14",
            fontSize: 10,
            fontWeight: 700,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "2px solid #11161d",
          }}
        >
          ✓
        </span>
      ) : null}
      {status === "locked" ? (
        <span
          aria-label="Not reached yet"
          style={{ position: "absolute", bottom: -4, right: -4, fontSize: 11 }}
        >
          🔒
        </span>
      ) : null}
    </span>
  );
}

function navLinkStyle(isActive: boolean): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 10px",
    borderRadius: 6,
    textDecoration: "none",
    color: isActive ? "#0b0f14" : "#c7cdd6",
    backgroundColor: isActive ? "#2f83e0" : "transparent",
    boxShadow: isActive ? "0 2px 10px rgba(47, 131, 224, 0.35)" : "none",
    fontSize: 14,
    fontWeight: isActive ? 600 : 400,
    width: "100%",
  };
}

// Polling for the mission flow/stage statuses. Disabled by default (0) -
// pages independently poll for their own data, and polling the top-level run
// here just causes aggressive re-renders and a "flickering" feel. Set via
// VITE_MISSION_FLOW_POLL_MS if needed for specific scenarios.
const MISSION_FLOW_POLL_MS = Number(import.meta.env.VITE_MISSION_FLOW_POLL_MS ?? 0);

export function AppShell(): JSX.Element {
  const [signedIn, setSignedIn] = useState(isAuthenticated());
  // Default to on: without this, starting a workflow run gives no visual
  // feedback at all until the user discovers and manually flips the
  // sidebar switch, leaving them wondering if anything is happening.
  const [triageOn, setTriageOn] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();
  const {
    sessionId,
    workflowRunId,
    setSessionId,
    setWorkflowRunId,
    setMissionStartedAt,
    setMissionError,
    setSelectedModelDeploymentRef,
  } = useSessionContext();
  const currentIndex = findCurrentNavIndex(location.pathname);
  const [maxReachedIndex, setMaxReachedIndex] = useState(currentIndex);

  useEffect(() => {
    setMaxReachedIndex(currentIndex);
    // Intentionally reset only when a new run starts, not on every nav
    // change - currentIndex is captured at that moment, not tracked live.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowRunId]);

  useEffect(() => {
    if (currentIndex > maxReachedIndex) {
      setMaxReachedIndex(currentIndex);
    }
  }, [currentIndex, maxReachedIndex]);

  useEffect(() => onAccessTokenChange((token) => setSignedIn(token !== null)), []);

  const runFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? workflowApi.getRun(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { data: run, error: runError } = useAsyncResource(runFetcher, [sessionId, workflowRunId], {
    enabled: Boolean(sessionId && workflowRunId),
    pollIntervalMs: MISSION_FLOW_POLL_MS,
  });
  const stageStatuses = computeStageStatuses(
    sessionId,
    workflowRunId,
    run,
    currentIndex,
    maxReachedIndex,
  );
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
    setMissionStartedAt(null);
    setMissionError(null);
    setSelectedModelDeploymentRef(null);
    navigate("/");
  }, [
    navigate,
    setMissionError,
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

        {/* The mission flow: a glowing vertical pipeline (deliberately not a
            standard nav menu) - each stage is an icon in a status ring (dim
            gray = not reached, pulsing amber glow = active, green check
            badge = complete) joined by a connector line that itself animates
            a flowing current toward whichever stage is currently active, so
            progress through Upload -> ... -> Deploy & Launch reads as a
            literal flow rather than a flat list of links. */}
        {NAV_ITEMS.map((item, index) => {
          const status = stageStatuses[index];
          const locked = status === "locked";
          const isLast = index === NAV_ITEMS.length - 1;
          const caption = status === "complete" ? "Completed" : status === "active" ? "In progress…" : "Not started";
          const captionColor = status === "complete" ? "#3fa66a" : status === "active" ? "#d99a2b" : "#5c6572";
          const lineIsFlowing = status === "active";

          return (
            <div
              key={item.to}
              className={`genie-stage-row${locked ? "" : " genie-stage-clickable"}`}
              style={{ display: "flex", alignItems: "stretch" }}
            >
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 46, flexShrink: 0 }}>
                <StageNode status={status} icon={item.icon} />
                {!isLast ? (
                  <div
                    className={lineIsFlowing ? "genie-stage-line-active" : undefined}
                    style={{
                      flex: 1,
                      width: 3,
                      minHeight: 22,
                      margin: "4px 0",
                      borderRadius: 2,
                      backgroundColor: lineIsFlowing ? undefined : status === "complete" ? "#3fa66a" : "#232a33",
                    }}
                  />
                ) : null}
              </div>
              <div style={{ flex: 1, paddingBottom: isLast ? 4 : 12, minWidth: 0, paddingTop: 4 }}>
                {locked ? (
                  <div
                    aria-disabled="true"
                    title="Complete the earlier stages to unlock this"
                    style={{ ...navLinkStyle(false), color: "#5c6572", cursor: "not-allowed" }}
                  >
                    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {item.label}
                      </span>
                      <span style={{ fontSize: 10, color: captionColor, letterSpacing: 0.3 }}>{caption}</span>
                    </div>
                  </div>
                ) : (
                  <NavLink key={item.to} to={item.to} style={({ isActive }) => navLinkStyle(isActive)} end={item.end}>
                    <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {item.label}
                      </span>
                      <span style={{ fontSize: 10, color: captionColor, letterSpacing: 0.3 }}>{caption}</span>
                    </div>
                  </NavLink>
                )}
              </div>
            </div>
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
