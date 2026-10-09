/* eslint-disable react-refresh/only-export-components -- this module intentionally
   pairs the SessionProvider component with its useSessionContext hook. */
import { createContext, useContext, useCallback, useMemo, useState, type ReactNode } from "react";
import type { SafeError } from "@/types/common";

/**
 * What the user actually asked Genie to do, chosen on the Home page.
 * Drives AppShell's adaptive Mission Flow (different asks don't all need
 * the same seven steps) - see AppShell.tsx's MISSION_KIND_STEPS. `null`
 * means "unknown/legacy" (e.g. a session created before this existed, or
 * a direct deep link) and falls back to showing every step.
 *
 * Deliberately excludes things like "govern" or "deploy" - those only make
 * sense partway through an existing mission, never as a blank-session
 * starting point, so they're not valid fresh-start kinds.
 */
export type MissionKind =
  | "discover_requirements"
  | "understand_code"
  | "modernize_and_deliver";

const _MISSION_KINDS: readonly MissionKind[] = [
  "discover_requirements",
  "understand_code",
  "modernize_and_deliver",
];

// Every one of these keys holds only a plain identifier/small string -
// never session content - matching Upload's own "Not saved · kept only
// for this active session" messaging: sessionStorage persists across a
// reload/direct navigation within this same browser tab (fixing the real
// bug where any full-page navigation lost the active mission entirely,
// stranding the user on a page with nothing to show even though the
// mission was still running server-side), but never leaks to a new tab
// or another device.
const _STORAGE_KEYS = {
  sessionId: "genie_mission_session_id",
  workflowRunId: "genie_mission_workflow_run_id",
  missionKind: "genie_mission_kind",
  missionStartedAt: "genie_mission_started_at",
  governancePolicies: "genie_mission_governance_policies",
  selectedModelDeploymentRef: "genie_mission_model_deployment_ref",
} as const;

function _readStorage(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function _writeStorage(key: string, value: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch {
    // Storage can legitimately be unavailable (private browsing, quota) -
    // persistence is a best-effort convenience, never a requirement for
    // the mission to keep working within this same render.
  }
}

function _readMissionKind(initial: MissionKind | null): MissionKind | null {
  if (initial !== null) return initial;
  const stored = _readStorage(_STORAGE_KEYS.missionKind);
  return stored !== null && (_MISSION_KINDS as readonly string[]).includes(stored)
    ? (stored as MissionKind)
    : null;
}

function _readNumber(initial: number | null, key: string): number | null {
  if (initial !== null) return initial;
  const stored = _readStorage(key);
  if (stored === null) return null;
  const parsed = Number(stored);
  return Number.isFinite(parsed) ? parsed : null;
}

export interface SessionContextValue {
  sessionId: string | null;
  workflowRunId: string | null;
  missionKind: MissionKind | null;
  /** Timestamp (ms) the user last clicked "Start Prototyping", or null if no
   * mission is currently underway. Lets the Agent Triage panel show a live
   * "clicked -> orchestrator engaged" mission console without the Upload
   * page needing its own duplicate view. */
  missionStartedAt: number | null;
  /** Surfaces a background workflow-run failure to whatever page the user
   * has already been navigated to ahead of that work finishing - e.g. the
   * Upload -> Requirements handoff (failed before a workflow_run_id was
   * ever minted, so Requirements has no run to poll) and the Requirements
   * approve -> Architecture Studio handoff (failed resuming an existing
   * run). Cleared by the destination page once shown/retried. Deliberately
   * never persisted across a reload - a stale error from a previous visit
   * would be confusing to see reappear. */
  missionError: SafeError | null;
  /** The governance/policy expectations the user selected on the
   * Architecture Studio page (semicolon-joined, may be ""), carried
   * forward so the Workshop page's "Re-run UI & Agent Design" action can
   * still supply them as build-solution's step_input override - that
   * can happen in a LATER, separate resume call than the one Architecture
   * Studio triggers, so the value can't just be a local variable on that
   * page. */
  governancePolicies: string;
  selectedModelDeploymentRef: string | null;
  setSessionId: (sessionId: string | null) => void;
  setWorkflowRunId: (workflowRunId: string | null) => void;
  setMissionKind: (missionKind: MissionKind | null) => void;
  setMissionStartedAt: (missionStartedAt: number | null) => void;
  setMissionError: (missionError: SafeError | null) => void;
  setGovernancePolicies: (governancePolicies: string) => void;
  setSelectedModelDeploymentRef: (selectedModelDeploymentRef: string | null) => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

/**
 * Tracks the single "active" session and workflow run the whole Mission
 * Control experience is currently focused on. Deliberately holds only
 * identifiers (never session content) so every page still fetches real
 * data itself through the hooks/services layer.
 */
export function SessionProvider({
  children,
  initialSessionId = null,
  initialWorkflowRunId = null,
  initialMissionKind = null,
  initialMissionStartedAt = null,
  initialMissionError = null,
  initialGovernancePolicies = "",
  initialSelectedModelDeploymentRef = null,
}: {
  children: ReactNode;
  /** Test-only seams for rendering pages without going through LandingPage. */
  initialSessionId?: string | null;
  initialWorkflowRunId?: string | null;
  initialMissionKind?: MissionKind | null;
  initialMissionStartedAt?: number | null;
  initialMissionError?: SafeError | null;
  initialGovernancePolicies?: string;
  initialSelectedModelDeploymentRef?: string | null;
}): JSX.Element {
  const [sessionId, setSessionIdState] = useState<string | null>(
    () => initialSessionId ?? _readStorage(_STORAGE_KEYS.sessionId),
  );
  const [workflowRunId, setWorkflowRunIdState] = useState<string | null>(
    () => initialWorkflowRunId ?? _readStorage(_STORAGE_KEYS.workflowRunId),
  );
  const [missionKind, setMissionKindState] = useState<MissionKind | null>(() =>
    _readMissionKind(initialMissionKind),
  );
  const [missionStartedAt, setMissionStartedAtState] = useState<number | null>(() =>
    _readNumber(initialMissionStartedAt, _STORAGE_KEYS.missionStartedAt),
  );
  const [missionError, setMissionError] = useState<SafeError | null>(initialMissionError);
  const [governancePolicies, setGovernancePoliciesState] = useState<string>(
    () => initialGovernancePolicies || _readStorage(_STORAGE_KEYS.governancePolicies) || "",
  );
  const [selectedModelDeploymentRef, setSelectedModelDeploymentRefState] = useState<string | null>(
    () => initialSelectedModelDeploymentRef ?? _readStorage(_STORAGE_KEYS.selectedModelDeploymentRef),
  );

  const setSessionId = useCallback((value: string | null) => {
    _writeStorage(_STORAGE_KEYS.sessionId, value);
    setSessionIdState(value);
  }, []);
  const setWorkflowRunId = useCallback((value: string | null) => {
    _writeStorage(_STORAGE_KEYS.workflowRunId, value);
    setWorkflowRunIdState(value);
  }, []);
  const setMissionKind = useCallback((value: MissionKind | null) => {
    _writeStorage(_STORAGE_KEYS.missionKind, value);
    setMissionKindState(value);
  }, []);
  const setMissionStartedAt = useCallback((value: number | null) => {
    _writeStorage(_STORAGE_KEYS.missionStartedAt, value === null ? null : String(value));
    setMissionStartedAtState(value);
  }, []);
  const setGovernancePolicies = useCallback((value: string) => {
    _writeStorage(_STORAGE_KEYS.governancePolicies, value);
    setGovernancePoliciesState(value);
  }, []);
  const setSelectedModelDeploymentRef = useCallback((value: string | null) => {
    _writeStorage(_STORAGE_KEYS.selectedModelDeploymentRef, value);
    setSelectedModelDeploymentRefState(value);
  }, []);

  const value = useMemo(
    () => ({
      sessionId,
      workflowRunId,
      missionKind,
      missionStartedAt,
      missionError,
      governancePolicies,
      selectedModelDeploymentRef,
      setSessionId,
      setWorkflowRunId,
      setMissionKind,
      setMissionStartedAt,
      setMissionError,
      setGovernancePolicies,
      setSelectedModelDeploymentRef,
    }),
    [
      sessionId,
      workflowRunId,
      missionKind,
      missionStartedAt,
      missionError,
      governancePolicies,
      selectedModelDeploymentRef,
      setSessionId,
      setWorkflowRunId,
      setMissionKind,
      setMissionStartedAt,
      setGovernancePolicies,
      setSelectedModelDeploymentRef,
    ],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSessionContext(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSessionContext must be used within a SessionProvider");
  return context;
}

