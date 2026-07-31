import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, MessageBar, MessageBarBody, MessageBarTitle, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { deployLaunchApi } from "@/services/deployLaunchApi";
import { approvalApi } from "@/services/approvalApi";
import { getTraceId } from "@/state/traceRegistry";
import { ApiError } from "@/services/httpClient";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";
import { DEPLOYMENT_STEP_ORDER, DEPLOYMENT_STEP_NAMES } from "@/types/deployLaunch";
import type { DeploymentStepResult } from "@/types/deployLaunch";

const POLL_MS = 4000;

const STEP_STATUS_COLORS: Record<DeploymentStepResult["status"], string> = {
  pending: "#8a8f98",
  running: "#d99a2b",
  completed: "#3fa66a",
  failed: "#d1495b",
  skipped: "#8a8f98",
};

const STEP_STATUS_LABELS: Record<DeploymentStepResult["status"], string> = {
  pending: "Pending",
  running: "Running…",
  completed: "✅ Completed",
  failed: "❌ Failed",
  skipped: "Skipped",
};

function StepRow({ step }: { step: DeploymentStepResult }): JSX.Element {
  const color = STEP_STATUS_COLORS[step.status];
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        border: `1px solid ${color}`,
        borderRadius: 6,
        padding: "8px 12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Text size={300} weight="semibold">
          {DEPLOYMENT_STEP_NAMES[step.step_id]}
        </Text>
        <Text size={200} style={{ color }}>
          {STEP_STATUS_LABELS[step.status]}
        </Text>
      </div>
      {step.detail ? (
        <Text size={200} style={{ opacity: 0.8, whiteSpace: "pre-wrap" }}>
          {step.detail}
        </Text>
      ) : null}
      {step.error ? (
        <Text size={200} style={{ color: "#d1495b" }}>
          {step.error}
        </Text>
      ) : null}
    </div>
  );
}

/**
 * The real Deploy & Launch pipeline: nine named, code-driven steps
 * (`DEPLOYMENT_STEP_ORDER`) executed by the backend's
 * `DeploymentPipelineService` against real Azure SDKs (or their Null/local
 * equivalents in local provider mode) - never simulated. Gated on the
 * `final-output-approval` checkpoint: the first `start()` call auto-requests
 * that checkpoint if none exists yet, and the backend returns 409 while it
 * is still pending, so this page surfaces an inline "Approve" action rather
 * than requiring a separate approvals screen.
 */
export function DeployLaunchPage(): JSX.Element {
  const { sessionId, workflowRunId } = useSessionContext();

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

  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);

  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [pendingApprovalId, setPendingApprovalId] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);

  const handleStart = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    setStarting(true);
    setStartError(null);
    try {
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await deployLaunchApi.start(sessionId, workflowRunId, traceId);
      setPendingApprovalId(null);
      refresh();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Final Output Approval was just auto-requested (or is still
        // pending from an earlier attempt) - look it up so we can offer an
        // inline "Approve" action instead of a dead end.
        try {
          const approvals = await approvalApi.list(sessionId);
          const pending = approvals.find(
            (request) =>
              request.checkpoint_id === "final-output-approval" &&
              request.subject_id === workflowRunId &&
              request.status === "pending",
          );
          setPendingApprovalId(pending?.id ?? null);
          setStartError(
            pending
              ? "Final Output Approval is required before deploying. Approve below to continue."
              : "Deploy & Launch is waiting on Final Output Approval.",
          );
        } catch {
          setStartError("Deploy & Launch is waiting on Final Output Approval.");
        }
      } else if (err instanceof ApiError && err.status === 403) {
        setStartError("Final Output Approval was rejected or expired - Deploy & Launch is blocked.");
      } else {
        setStartError((err as ApiError).message ?? "Failed to start Deploy & Launch.");
      }
    } finally {
      setStarting(false);
    }
  }, [sessionId, workflowRunId, refresh]);

  const handleApproveAndStart = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingApprovalId) return;
    setApproving(true);
    try {
      await approvalApi.decide(sessionId, pendingApprovalId, "approved", "", workflowRunId);
      setPendingApprovalId(null);
      await handleStart();
    } catch (err) {
      setStartError((err as ApiError).message ?? "Failed to approve Final Output Approval.");
    } finally {
      setApproving(false);
    }
  }, [sessionId, workflowRunId, pendingApprovalId, handleStart]);

  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const handleDownload = useCallback(async () => {
    if (!sessionId || !activeRun) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const { blob, filename } = await deployLaunchApi.download(sessionId, activeRun.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setDownloadError((err as ApiError).message ?? "Failed to download the build.");
    } finally {
      setDownloading(false);
    }
  }, [sessionId, activeRun]);

  if (!sessionId || !workflowRunId) {
    return (
      <div>
        <PageHeader title="Deploy & Launch" subtitle="No active mission yet." />
        <Text size={300} style={{ opacity: 0.7 }}>
          Complete Peer Review from an active mission run before deploying.
        </Text>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Deploy & Launch"
        subtitle="Provisions access control, agents, backend/frontend, runs full testing and a security scan, then mints the customer-facing launch link."
      />
      {loading && !runs ? <LoadingState label="Loading Deploy & Launch status..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {!activeRun || activeRun.status === "failed" ? (
          <SectionCard title="Start Deploy & Launch">
            {startError ? (
              <MessageBar intent="warning" layout="multiline" style={{ marginBottom: 12 }}>
                <MessageBarBody>
                  <MessageBarTitle>
                    {pendingApprovalId ? "Approval required" : "Deploy & Launch"}
                  </MessageBarTitle>
                  {startError}
                </MessageBarBody>
              </MessageBar>
            ) : null}
            <div style={{ display: "flex", gap: 8 }}>
              {pendingApprovalId ? (
                <Button appearance="primary" disabled={approving} onClick={() => void handleApproveAndStart()}>
                  {approving ? "Approving..." : "Approve Final Output & Start"}
                </Button>
              ) : (
                <Button appearance="primary" disabled={starting} onClick={() => void handleStart()}>
                  {starting ? "Starting..." : activeRun?.status === "failed" ? "Retry Deploy & Launch" : "Start Deploy & Launch"}
                </Button>
              )}
            </div>
          </SectionCard>
        ) : null}

        {activeRun ? (
          <>
            <SectionCard title="Pipeline Progress">
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {DEPLOYMENT_STEP_ORDER.map((stepId) => {
                  const step = activeRun.steps.find((candidate) => candidate.step_id === stepId) ?? {
                    step_id: stepId,
                    name: DEPLOYMENT_STEP_NAMES[stepId],
                    status: "pending" as const,
                    detail: "",
                    error: null,
                    started_at: null,
                    completed_at: null,
                  };
                  return <StepRow key={stepId} step={step} />;
                })}
              </div>
            </SectionCard>

            {activeRun.access_policy ? (
              <SectionCard title="🔐 Least-Access Policy">
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {activeRun.access_policy.agents.map((agent) => (
                    <Text key={agent.agent_id} size={300}>
                      <b>{agent.agent_id}</b> ({agent.role}) — tools: {agent.allowed_tools.join(", ") || "none"};
                      memory: {agent.memory_access.join(", ") || "none"}
                    </Text>
                  ))}
                </div>
              </SectionCard>
            ) : null}

            {activeRun.test_summary || activeRun.security_findings_count !== null ? (
              <SectionCard title="🧪 Testing & Security">
                {activeRun.test_summary ? (
                  <Text size={300} style={{ whiteSpace: "pre-wrap", display: "block", marginBottom: 8 }}>
                    {activeRun.test_summary}
                  </Text>
                ) : null}
                {activeRun.security_findings_count !== null ? (
                  <Text size={300}>Security scan findings: {activeRun.security_findings_count}</Text>
                ) : null}
              </SectionCard>
            ) : null}

            {activeRun.status === "completed" && activeRun.launch_url ? (
              <SectionCard title="🚀 Launch">
                <Text size={300} style={{ display: "block", marginBottom: 12 }}>
                  Your solution is live at:{" "}
                  <a href={activeRun.launch_url} target="_blank" rel="noreferrer">
                    {activeRun.launch_url}
                  </a>
                </Text>
                {downloadError ? <ErrorState error={{ message: downloadError }} /> : null}
                <Button appearance="primary" disabled={downloading} onClick={() => void handleDownload()}>
                  {downloading ? "Preparing download..." : "Download Code & Access Policy"}
                </Button>
              </SectionCard>
            ) : null}
          </>
        ) : null}

        {!liveConnected && activeRun && activeRun.status === "running" ? (
          <Text size={200} style={{ opacity: 0.6 }}>
            Live updates disconnected - still polling every {POLL_MS / 1000}s.
          </Text>
        ) : null}
      </div>
    </div>
  );
}
