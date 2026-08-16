/* eslint-disable react-refresh/only-export-components -- this module intentionally
   pairs the SessionProvider component with its useSessionContext hook. */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { SafeError } from "@/types/common";

export interface SessionContextValue {
  sessionId: string | null;
  workflowRunId: string | null;
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
   * run). Cleared by the destination page once shown/retried. */
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
  initialMissionStartedAt = null,
  initialMissionError = null,
  initialGovernancePolicies = "",
  initialSelectedModelDeploymentRef = null,
}: {
  children: ReactNode;
  /** Test-only seams for rendering pages without going through LandingPage. */
  initialSessionId?: string | null;
  initialWorkflowRunId?: string | null;
  initialMissionStartedAt?: number | null;
  initialMissionError?: SafeError | null;
  initialGovernancePolicies?: string;
  initialSelectedModelDeploymentRef?: string | null;
}): JSX.Element {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [workflowRunId, setWorkflowRunId] = useState<string | null>(initialWorkflowRunId);
  const [missionStartedAt, setMissionStartedAt] = useState<number | null>(initialMissionStartedAt);
  const [missionError, setMissionError] = useState<SafeError | null>(initialMissionError);
  const [governancePolicies, setGovernancePolicies] = useState<string>(initialGovernancePolicies);
  const [selectedModelDeploymentRef, setSelectedModelDeploymentRef] = useState<string | null>(
    initialSelectedModelDeploymentRef,
  );

  const value = useMemo(
    () => ({
      sessionId,
      workflowRunId,
      missionStartedAt,
      missionError,
      governancePolicies,
      selectedModelDeploymentRef,
      setSessionId,
      setWorkflowRunId,
      setMissionStartedAt,
      setMissionError,
      setGovernancePolicies,
      setSelectedModelDeploymentRef,
    }),
    [
      sessionId,
      workflowRunId,
      missionStartedAt,
      missionError,
      governancePolicies,
      selectedModelDeploymentRef,
    ],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSessionContext(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSessionContext must be used within a SessionProvider");
  return context;
}
