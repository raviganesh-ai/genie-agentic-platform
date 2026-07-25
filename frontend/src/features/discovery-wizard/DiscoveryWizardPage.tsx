import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Text } from "@fluentui/react-components";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { useSessionContext } from "@/state/SessionContext";
import { useMissionControl } from "@/hooks/useMissionControl";
import { WIZARD_STAGES, resolveWizardStageIndex } from "@/config/discoveryWorkflow";
import { getTraceId } from "@/state/traceRegistry";
import { governanceApi } from "@/services/governanceApi";
import type { ApiError } from "@/services/httpClient";
import { MissionControlPage } from "@/features/mission-control/MissionControlPage";
import { RequirementDiscoveryPage } from "@/features/requirement-map/RequirementDiscoveryPage";
import { AgentArenaPage } from "@/features/agent-arena/AgentArenaPage";
import { CollaborationGraphPage } from "@/features/collaboration-graph/CollaborationGraphPage";
import { ArchitectureStudioPage } from "@/features/architecture-studio/ArchitectureStudioPage";
import { GovernancePage } from "@/features/governance-center/GovernancePage";
import { FinalOutputPage } from "@/features/final-output-center/FinalOutputPage";

const POLL_MS = Number(import.meta.env.VITE_MISSION_CONTROL_POLL_MS ?? 4000);

const STAGE_PAGES: JSX.Element[] = [
  <MissionControlPage key="mission-control" />,
  <RequirementDiscoveryPage key="requirements" />,
  <AgentArenaPage key="agent-arena" />,
  <CollaborationGraphPage key="collaboration-graph" />,
  <ArchitectureStudioPage key="architecture-studio" />,
  <GovernancePage key="governance" />,
  <FinalOutputPage key="final-output" />,
];

/**
 * Human-in-the-loop discovery wizard shown after clicking "Generate
 * Prototype" on Upload. Polls Mission Control's snapshot to know how far
 * the backend workflow has actually progressed (`readyStageIndex`), but -
 * per the Responsible AI Accountability principle - never advances the
 * displayed stage on its own. A person must explicitly click "Proceed to
 * Next Step" to move forward once a stage is ready; the stepper can also be
 * used to jump back to any already-reached stage to review it.
 */
export function DiscoveryWizardPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, workflowRunId, setWorkflowRunId } = useSessionContext();
  const { data: snapshot, loading, error, refresh } = useMissionControl(sessionId, POLL_MS);
  const [stageIndex, setStageIndex] = useState(0);
  const [readyStageIndex, setReadyStageIndex] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  useEffect(() => {
    if (snapshot?.workflow_run_id) setWorkflowRunId(snapshot.workflow_run_id);
  }, [snapshot?.workflow_run_id, setWorkflowRunId]);

  useEffect(() => {
    setReadyStageIndex((previous) => resolveWizardStageIndex(snapshot ?? null, previous));
  }, [snapshot]);

  const canProceed = readyStageIndex > stageIndex;
  const handleProceed = async () => {
    if (!sessionId) return;
    setConfirmError(null);
    setConfirming(true);
    try {
      const traceId = (workflowRunId && getTraceId(workflowRunId)) || "unknown-trace";
      const stage = WIZARD_STAGES[stageIndex];
      // Responsible AI Accountability: permanently record this human
      // confirmation as a real GovernanceEvent before advancing, so every
      // wizard step is attributable to the person who approved it.
      await governanceApi.confirmCheckpoint(sessionId, traceId, stage.key, stage.label);
      setStageIndex((previous) => Math.min(previous + 1, readyStageIndex, WIZARD_STAGES.length - 1));
    } catch (err) {
      setConfirmError((err as ApiError).message ?? "Failed to record confirmation.");
    } finally {
      setConfirming(false);
    }
  };

  if (!sessionId) {
    return (
      <div>
        <PageHeader title="Discovery" subtitle="No active session yet." />
        <Button appearance="primary" onClick={() => navigate("/")}>
          Start a session
        </Button>
      </div>
    );
  }

  if (loading && !snapshot) return <LoadingState label="Starting discovery orchestration..." />;
  if (error) return <ErrorState error={error} onRetry={refresh} />;

  const isComplete = snapshot?.workflow_status === "completed";

  return (
    <div>
      <PageHeader
        title="Discovery in Progress"
        subtitle={
          isComplete && stageIndex === WIZARD_STAGES.length - 1
            ? "Discovery complete - your prototype and final output are ready."
            : "Human-in-the-loop: review each stage, then click Proceed to move Genie forward."
        }
      />

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {WIZARD_STAGES.map((stage, index) => {
          const isReached = index <= readyStageIndex;
          return (
            <button
              key={stage.key}
              type="button"
              title={stage.description}
              disabled={!isReached}
              onClick={() => isReached && setStageIndex(index)}
              style={{
                border: "none",
                background: "none",
                padding: 0,
                cursor: isReached ? "pointer" : "not-allowed",
                opacity: isReached ? 1 : 0.5,
              }}
            >
              <Badge
                appearance={index === stageIndex ? "filled" : index < stageIndex ? "tint" : "outline"}
                color={index === stageIndex ? "brand" : index < stageIndex ? "success" : "informative"}
                style={{ padding: "6px 10px" }}
              >
                {index + 1}. {stage.label}
              </Badge>
            </button>
          );
        })}
      </div>

      <Text size={300} style={{ display: "block", marginBottom: 12 }}>
        {WIZARD_STAGES[stageIndex].description}
      </Text>

      {snapshot ? (
        <Text size={200} style={{ display: "block", marginBottom: 16, opacity: 0.75 }}>
          Progress: {Math.round(snapshot.mission_progress * 100)}% - status:{" "}
          {snapshot.workflow_status ?? "starting"}
        </Text>
      ) : null}

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <Button appearance="primary" disabled={!canProceed || confirming} onClick={() => void handleProceed()}>
          {stageIndex === WIZARD_STAGES.length - 1
            ? "Discovery Complete"
            : confirming
              ? "Recording confirmation..."
              : canProceed
                ? `Proceed to Next Step: ${WIZARD_STAGES[stageIndex + 1].label}`
                : "Waiting for agents..."}
        </Button>
        <Text size={200} style={{ opacity: 0.75 }}>
          Accountability checkpoint - Genie will not advance past this stage without your confirmation, and
          every confirmation is permanently recorded in the governance audit trail.
        </Text>
      </div>

      {confirmError ? (
        <Text size={200} style={{ display: "block", marginBottom: 16, color: "#a80000" }}>
          {confirmError}
        </Text>
      ) : null}

      {STAGE_PAGES[stageIndex]}
    </div>
  );
}
