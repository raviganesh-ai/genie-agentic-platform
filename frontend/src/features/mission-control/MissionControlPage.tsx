import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useMissionControl } from "@/hooks/useMissionControl";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { MissionControlHeader } from "./components/MissionControlHeader";
import { ExecutiveScoreboard } from "./components/ExecutiveScoreboard";
import { MissionProgressTracker } from "./components/MissionProgressTracker";
import { LiveActivityFeed } from "./components/LiveActivityFeed";
import { GovernanceHealthPanel } from "./components/GovernanceHealthPanel";
import { ActiveAgentRibbon } from "./components/ActiveAgentRibbon";

const POLL_MS = Number(import.meta.env.VITE_MISSION_CONTROL_POLL_MS ?? 5000);

export function MissionControlPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, setWorkflowRunId } = useSessionContext();
  const { data: snapshot, loading, error, refresh } = useMissionControl(sessionId, POLL_MS);

  useEffect(() => {
    if (snapshot?.workflow_run_id) setWorkflowRunId(snapshot.workflow_run_id);
  }, [snapshot?.workflow_run_id, setWorkflowRunId]);

  if (!sessionId) {
    return (
      <div>
        <Button appearance="primary" onClick={() => navigate("/")}>
          Start a session
        </Button>
      </div>
    );
  }

  if (loading && !snapshot) return <LoadingState label="Loading Mission Control..." />;
  if (error) return <ErrorState error={error} onRetry={refresh} />;
  if (!snapshot) return <LoadingState />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <MissionControlHeader snapshot={snapshot} />
      <ExecutiveScoreboard snapshot={snapshot} />
      <MissionProgressTracker snapshot={snapshot} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <ActiveAgentRibbon snapshot={snapshot} />
        <GovernanceHealthPanel snapshot={snapshot} />
      </div>
      <LiveActivityFeed timeline={snapshot.timeline} />
    </div>
  );
}
