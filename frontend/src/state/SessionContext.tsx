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
  /** Set if the workflow run kicked off from Upload failed *before* a
   * workflow_run_id was ever minted - the Requirements page (which the user
   * is navigated to immediately on click, ahead of the run finishing) has
   * no other way to learn the run never produced a run id to poll. */
  missionError: SafeError | null;
  setSessionId: (sessionId: string | null) => void;
  setWorkflowRunId: (workflowRunId: string | null) => void;
  setMissionStartedAt: (missionStartedAt: number | null) => void;
  setMissionError: (missionError: SafeError | null) => void;
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
}: {
  children: ReactNode;
  /** Test-only seams for rendering pages without going through LandingPage. */
  initialSessionId?: string | null;
  initialWorkflowRunId?: string | null;
  initialMissionStartedAt?: number | null;
}): JSX.Element {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [workflowRunId, setWorkflowRunId] = useState<string | null>(initialWorkflowRunId);
  const [missionStartedAt, setMissionStartedAt] = useState<number | null>(initialMissionStartedAt);
  const [missionError, setMissionError] = useState<SafeError | null>(null);

  const value = useMemo(
    () => ({
      sessionId,
      workflowRunId,
      missionStartedAt,
      missionError,
      setSessionId,
      setWorkflowRunId,
      setMissionStartedAt,
      setMissionError,
    }),
    [sessionId, workflowRunId, missionStartedAt, missionError],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSessionContext(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSessionContext must be used within a SessionProvider");
  return context;
}
