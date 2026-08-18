import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Text,
} from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { deployLaunchApi } from "@/services/deployLaunchApi";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { RequirementFidelityDashboard } from "@/components/RequirementFidelityDashboard";

const POLL_MS = 4000;

/**
 * The Requirement Fidelity Gate, shown as a modal popup over the Outputs
 * hub (its own tab) instead of a full page - lets the user check the real
 * per-requirement executable-test coverage/passing-evidence gate for the
 * most recent Deploy & Launch run without leaving whatever tab they were
 * on. Closing the dialog returns to the Deploy & Launch tab.
 */
export function RequirementFidelityGatePage(): JSX.Element {
  const { sessionId } = useSessionContext();
  const navigate = useNavigate();

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

  const handleClose = () => navigate("/outputs");

  return (
    <Dialog open modalType="modal" onOpenChange={(_event, data) => (!data.open ? handleClose() : undefined)}>
      <DialogSurface style={{ maxWidth: 960, width: "90vw" }}>
        <DialogBody>
          <DialogTitle>Requirement Fidelity Gate</DialogTitle>
          <DialogContent>
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
          </DialogContent>
          <DialogActions>
            <Button appearance="primary" onClick={handleClose}>
              Close
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
