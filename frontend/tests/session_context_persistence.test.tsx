import { describe, expect, it, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { SessionProvider, useSessionContext } from "@/state/SessionContext";

/** Wraps the real SessionProvider exactly as every page does, letting a
 * test both read and mutate context state via the same hook pages use. */
function wrapper({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

describe("SessionContext sessionStorage persistence", () => {
  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("hydrates sessionId/workflowRunId/missionKind from sessionStorage on mount, with no initial props supplied", () => {
    window.sessionStorage.setItem("genie_mission_session_id", "session-123");
    window.sessionStorage.setItem("genie_mission_workflow_run_id", "run-456");
    window.sessionStorage.setItem("genie_mission_kind", "discover_requirements");

    const { result } = renderHook(() => useSessionContext(), { wrapper });

    expect(result.current.sessionId).toBe("session-123");
    expect(result.current.workflowRunId).toBe("run-456");
    expect(result.current.missionKind).toBe("discover_requirements");
  });

  it("persists every setter's value to sessionStorage so a later fresh mount (e.g. a full-page reload/direct navigation) recovers it", () => {
    const { result } = renderHook(() => useSessionContext(), { wrapper });

    act(() => {
      result.current.setSessionId("session-abc");
      result.current.setWorkflowRunId("run-def");
      result.current.setMissionKind("understand_code");
      result.current.setGovernancePolicies("Must use managed identity");
      result.current.setSelectedModelDeploymentRef("gpt-5-1");
    });

    expect(window.sessionStorage.getItem("genie_mission_session_id")).toBe("session-abc");
    expect(window.sessionStorage.getItem("genie_mission_workflow_run_id")).toBe("run-def");
    expect(window.sessionStorage.getItem("genie_mission_kind")).toBe("understand_code");
    expect(window.sessionStorage.getItem("genie_mission_governance_policies")).toBe(
      "Must use managed identity",
    );
    expect(window.sessionStorage.getItem("genie_mission_model_deployment_ref")).toBe("gpt-5-1");

    // A brand-new mount (simulating the next page load) must recover every
    // value without needing any initial prop at all.
    const { result: rehydrated } = renderHook(() => useSessionContext(), { wrapper });
    expect(rehydrated.current.sessionId).toBe("session-abc");
    expect(rehydrated.current.workflowRunId).toBe("run-def");
    expect(rehydrated.current.missionKind).toBe("understand_code");
    expect(rehydrated.current.governancePolicies).toBe("Must use managed identity");
    expect(rehydrated.current.selectedModelDeploymentRef).toBe("gpt-5-1");
  });

  it("clears the matching sessionStorage key when a setter is called with null", () => {
    const { result } = renderHook(() => useSessionContext(), { wrapper });

    act(() => {
      result.current.setSessionId("session-abc");
    });
    expect(window.sessionStorage.getItem("genie_mission_session_id")).toBe("session-abc");

    act(() => {
      result.current.setSessionId(null);
    });
    expect(window.sessionStorage.getItem("genie_mission_session_id")).toBeNull();
    expect(result.current.sessionId).toBeNull();
  });

  it("never persists missionError across a reload - a stale error reappearing would be confusing", () => {
    const { result } = renderHook(() => useSessionContext(), { wrapper });

    act(() => {
      result.current.setMissionError({ message: "Something failed." });
    });

    const { result: rehydrated } = renderHook(() => useSessionContext(), { wrapper });
    expect(rehydrated.current.missionError).toBeNull();
  });

  it("falls back to null instead of crashing when sessionStorage holds a garbage mission kind", () => {
    window.sessionStorage.setItem("genie_mission_kind", "not-a-real-kind");

    const { result } = renderHook(() => useSessionContext(), { wrapper });

    expect(result.current.missionKind).toBeNull();
  });

  it("prefers an explicit initial prop over whatever sessionStorage already holds", () => {
    window.sessionStorage.setItem("genie_mission_session_id", "stale-session");

    const { result } = renderHook(() => useSessionContext(), {
      wrapper: ({ children }) => (
        <SessionProvider initialSessionId="fresh-session">{children}</SessionProvider>
      ),
    });

    expect(result.current.sessionId).toBe("fresh-session");
  });
});
