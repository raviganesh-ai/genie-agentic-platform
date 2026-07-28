import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, MessageBar, MessageBarBody, MessageBarTitle, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useGovernanceTrace } from "@/hooks/useGovernanceTrace";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { approvalApi } from "@/services/approvalApi";
import { workflowApi } from "@/services/workflowApi";
import { getTraceId } from "@/state/traceRegistry";
import type { ApiError } from "@/services/httpClient";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { GovernanceStatusBadge } from "@/components/StatusBadge";
import { LiveWorkflowPulse } from "@/components/LiveWorkflowPulse";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import type { GovernanceEventCategory } from "@/types/governance";

const POLL_MS = Number(import.meta.env.VITE_GOVERNANCE_POLL_MS ?? 5000);

const CATEGORY_META: Record<GovernanceEventCategory, { icon: string; accent: string }> = {
  agent_registration: { icon: "🆕", accent: "#2f83e0" },
  agent_version: { icon: "🔢", accent: "#2f83e0" },
  agent_lifecycle: { icon: "♻️", accent: "#2f83e0" },
  agent_execution: { icon: "✅", accent: "#3fa66a" },
  agent_communication: { icon: "💬", accent: "#3fa66a" },
  memory_read: { icon: "📖", accent: "#8a63d2" },
  memory_write: { icon: "💾", accent: "#8a63d2" },
  tool_request: { icon: "🛠️", accent: "#d99a2b" },
  policy_evaluation: { icon: "📋", accent: "#2f83e0" },
  access_denied: { icon: "⛔", accent: "#d1495b" },
  human_checkpoint_confirmation: { icon: "🗐️", accent: "#c98a2c" },
};

export function GovernancePage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, workflowRunId } = useSessionContext();
  const { data, loading, error, refresh } = useGovernanceTrace(sessionId, POLL_MS);

  const runFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? workflowApi.getRun(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { data: run } = useAsyncResource(runFetcher, [sessionId, workflowRunId], {
    enabled: Boolean(sessionId && workflowRunId),
  });
  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
  const governanceReviewText = useMemo(
    () => run?.step_results.find((result) => result.step_id === "governance-review")?.output_text ?? "",
    [run],
  );

  const pendingDeployApproval = data?.approvals.find(
    (request) => request.status === "pending" && request.subject_id === "deploy-solution",
  );
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  const handleApproveDeploy = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingDeployApproval) return;
    setDeploying(true);
    setDeployError(null);
    try {
      await approvalApi.decide(sessionId, pendingDeployApproval.id, "approved");
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await workflowApi.resumeRun(sessionId, workflowRunId, traceId);
      await refresh();
    } catch (err) {
      setDeployError((err as ApiError).message ?? "Failed to resume the workflow.");
    } finally {
      setDeploying(false);
    }
  }, [sessionId, workflowRunId, pendingDeployApproval, refresh]);

  if (!sessionId) {
    return (
      <div>
        <PageHeader title="Governance Center" subtitle="No active session yet." />
        <Button appearance="primary" onClick={() => navigate("/")}>
          Start a session
        </Button>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title="Governance Center" subtitle="Policy checks, authorization decisions, and compliance status." />
      {loading && !data ? <LoadingState label="Loading governance trace..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      <LiveWorkflowPulse connected={liveConnected} events={liveEvents} />

      {data ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Text weight="semibold" size={500}>
              Overall status
            </Text>
            <GovernanceStatusBadge state={data.complianceState} />
          </div>

          <SectionCard title="📜 Governance Events Timeline">
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 360, overflowY: "auto" }}>
              {[...data.events]
                .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
                .map((event) => {
                  const meta = CATEGORY_META[event.category];
                  return (
                    <div key={event.id} style={{ borderLeft: `2px solid ${meta.accent}`, paddingLeft: 10 }}>
                      <Text size={200} style={{ opacity: 0.6, display: "block" }}>
                        {new Date(event.timestamp).toLocaleString()}
                        {event.agent_id ? ` · ${event.agent_id}` : ""}
                      </Text>
                      <Text size={300}>
                        {meta.icon} {event.category.replace(/_/g, " ")}
                      </Text>
                    </div>
                  );
                })}
              {data.events.length === 0 ? (
                <Text size={300} style={{ opacity: 0.7 }}>
                  No governance events recorded yet.
                </Text>
              ) : null}
            </div>
          </SectionCard>

          {governanceReviewText ? (
            <SectionCard title="Security & Governance Review">
              <Text size={300} style={{ whiteSpace: "pre-wrap" }}>
                {governanceReviewText}
              </Text>
            </SectionCard>
          ) : (
            <AgentActivityAnimation
              label="Genie is working with the Governance Reviewer agent to evaluate your selected policies..."
              events={liveEvents}
            />
          )}

          {pendingDeployApproval ? (
            <SectionCard title="Approve & Deploy">
              {deployError ? (
                <MessageBar intent="error" layout="multiline" style={{ marginBottom: 12 }}>
                  <MessageBarBody>
                    <MessageBarTitle>Failed to resume the workflow</MessageBarTitle>
                    {deployError}
                  </MessageBarBody>
                </MessageBar>
              ) : null}
              <Text size={300} style={{ display: "block", marginBottom: 12, opacity: 0.8 }}>
                Governance has reviewed the build above. Approving provisions access control and
                deploys the UI and agent workflow.
              </Text>
              <Button appearance="primary" disabled={deploying} onClick={() => void handleApproveDeploy()}>
                {deploying ? "Deploying..." : "Approve & Deploy"}
              </Button>
            </SectionCard>
          ) : null}

          <SectionCard title="Approvals">
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {data.approvals.map((approval) => (
                <Text key={approval.id} size={300}>
                  {approval.subject_type.replace(/_/g, " ")} · {approval.subject_id} ·{" "}
                  {approval.status}
                </Text>
              ))}
              {data.approvals.length === 0 ? (
                <Text size={300} style={{ opacity: 0.7 }}>
                  No approvals recorded yet.
                </Text>
              ) : null}
            </div>
          </SectionCard>
        </div>
      ) : null}
    </div>
  );
}
