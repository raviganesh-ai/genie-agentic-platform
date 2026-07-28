import { useCallback, useMemo, useState } from "react";
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

const POLL_MS = Number(import.meta.env.VITE_GOVERNANCE_POLL_MS ?? 5000);

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

      {data ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Text weight="semibold" size={500}>
              Overall status
            </Text>
            <GovernanceStatusBadge state={data.complianceState} />
          </div>

          <SectionCard title="Governance Events Timeline">
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 360, overflowY: "auto" }}>
              {[...data.events]
                .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
                .map((event) => (
                  <div key={event.id} style={{ borderLeft: "2px solid #2f83e0", paddingLeft: 10 }}>
                    <Text size={200} style={{ opacity: 0.6, display: "block" }}>
                      {new Date(event.timestamp).toLocaleString()}
                      {event.agent_id ? ` · ${event.agent_id}` : ""}
                    </Text>
                    <Text size={300}>{event.category.replace(/_/g, " ")}</Text>
                  </div>
                ))}
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
          ) : null}

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
