import { useCallback, useMemo } from "react";
import { Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { deployLaunchApi } from "@/services/deployLaunchApi";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { RequirementFidelityDashboard } from "@/components/RequirementFidelityDashboard";

const POLL_MS = 4000;

/**
 * The Requirement Fidelity Gate, rendered inline under its own Outputs sub-tab
 * (not a popup) - shows the real per-requirement executable-test coverage/
 * passing-evidence gate for the most recent Deploy & Launch run.
 */
export function RequirementFidelityGatePage(): JSX.Element {
  const { sessionId } = useSessionContext();

  const runsFetcher = useCallback(
    () => (sessionId ? deployLaunchApi.list(sessionId) : Promise.reject(new Error("No active session"))),
    [sessionId],
  );
  const { data: runs, loading, error, refresh } = useAsyncResource(runsFetcher, [sessionId], {
    enabled: Boolean(sessionId),
    pollIntervalMs: POLL_MS,
  });

  const activeRun = useMemo(() => {
    if (!runs || runs.length === 0) return null;
    return [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  }, [runs]);

  return (
    <div>
      {loading && !runs ? <LoadingState label="Loading Requirement Fidelity Gate..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      {!loading && !error && !activeRun ? (
        <Text size={300} style={{ opacity: 0.7 }}>
          No Deploy & Launch run has been started for this mission yet.
        </Text>
      ) : null}
      {activeRun?.fidelity_report ? (
        <RequirementFidelityDashboard report={activeRun.fidelity_report} />
      ) : activeRun ? (
        <Text size={300} style={{ opacity: 0.7 }}>
          Requirement fidelity evidence has not been generated yet for this run.
        </Text>
      ) : null}
    </div>
  );
}
