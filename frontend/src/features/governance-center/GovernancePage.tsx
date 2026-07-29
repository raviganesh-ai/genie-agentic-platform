import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Checkbox,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Text,
  Textarea,
} from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useGovernanceTrace } from "@/hooks/useGovernanceTrace";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { approvalApi } from "@/services/approvalApi";
import { governanceApi } from "@/services/governanceApi";
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
import type { GateName, GovernanceEventCategory } from "@/types/governance";

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
  risk_accepted: { icon: "⚠️", accent: "#d99a2b" },
};

const GATE_LABELS: Record<GateName, string> = {
  security: "Security",
  test_coverage: "Test Coverage",
  architecture: "Architecture",
  code_quality: "Code Quality",
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

  const gateReportFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? governanceApi.getGateReport(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { data: gateReport, refresh: refreshGateReport } = useAsyncResource(
    gateReportFetcher,
    [sessionId, workflowRunId],
    { enabled: Boolean(sessionId && workflowRunId) },
  );

  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refresh();
      refreshGateReport();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
  const governanceReviewText = useMemo(
    () => run?.step_results.find((result) => result.step_id === "governance-review")?.output_text ?? "",
    [run],
  );

  const riskAccepted = useMemo(
    () =>
      data?.events.some(
        (event) =>
          event.category === "risk_accepted" &&
          (event.detail as { workflow_run_id?: string }).workflow_run_id === workflowRunId,
      ) ?? false,
    [data, workflowRunId],
  );

  const [selectedFindings, setSelectedFindings] = useState<Set<string>>(new Set());
  const toggleFinding = useCallback((findingId: string, checked: boolean) => {
    setSelectedFindings((prev) => {
      const next = new Set(prev);
      if (checked) next.add(findingId);
      else next.delete(findingId);
      return next;
    });
  }, []);
  const [applyingFixes, setApplyingFixes] = useState(false);
  const [applyFixesError, setApplyFixesError] = useState<string | null>(null);

  const handleApplySelectedFixes = useCallback(async () => {
    if (!sessionId || !workflowRunId || !gateReport) return;
    const descriptions = gateReport.findings
      .filter((finding) => selectedFindings.has(finding.id))
      .map((finding) => finding.description);
    if (descriptions.length === 0) return;
    setApplyingFixes(true);
    setApplyFixesError(null);
    try {
      const traceId = getTraceId(workflowRunId) ?? crypto.randomUUID();
      await governanceApi.applyFixes(sessionId, workflowRunId, traceId, descriptions);
      setSelectedFindings(new Set());
      refreshGateReport();
      await refresh();
    } catch (err) {
      setApplyFixesError((err as ApiError).message ?? "Failed to apply the selected fixes.");
    } finally {
      setApplyingFixes(false);
    }
  }, [sessionId, workflowRunId, gateReport, selectedFindings, refresh, refreshGateReport]);

  const [justification, setJustification] = useState("");
  const [submittingRiskAcceptance, setSubmittingRiskAcceptance] = useState(false);
  const [riskAcceptanceError, setRiskAcceptanceError] = useState<string | null>(null);

  const handleAcceptRisk = useCallback(async () => {
    if (!sessionId || !workflowRunId || !gateReport || justification.trim().length === 0) return;
    setSubmittingRiskAcceptance(true);
    setRiskAcceptanceError(null);
    try {
      const traceId = getTraceId(workflowRunId) ?? crypto.randomUUID();
      const acceptedFindingIds = gateReport.findings.map((finding) => finding.id);
      await governanceApi.submitRiskAcceptance(
        sessionId,
        workflowRunId,
        traceId,
        justification.trim(),
        acceptedFindingIds,
      );
      await refresh();
    } catch (err) {
      setRiskAcceptanceError((err as ApiError).message ?? "Failed to record the risk acceptance.");
    } finally {
      setSubmittingRiskAcceptance(false);
    }
  }, [sessionId, workflowRunId, gateReport, justification, refresh]);

  const pendingDeployApproval = data?.approvals.find(
    (request) => request.status === "pending" && request.subject_id === "deploy-solution",
  );
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [acknowledgedRisk, setAcknowledgedRisk] = useState(false);

  const isBlocked = gateReport?.decision === "blocked";
  const canDeploy = acknowledgedRisk && (!isBlocked || riskAccepted);

  const handleApproveDeploy = useCallback(async () => {
    if (!sessionId || !workflowRunId || !pendingDeployApproval) return;
    setDeploying(true);
    setDeployError(null);
    try {
      await approvalApi.decide(sessionId, pendingDeployApproval.id, "approved", "", workflowRunId);
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

          {gateReport && gateReport.status === "reviewed" ? (
            <SectionCard title="🛡️ Peer Review Gate Verdict">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 12 }}>
                {(["security", "test_coverage", "architecture", "code_quality"] as GateName[]).map(
                  (gate) => {
                    const status =
                      gate === "security"
                        ? gateReport.security_gate
                        : gate === "test_coverage"
                          ? gateReport.test_coverage_gate
                          : gate === "architecture"
                            ? gateReport.architecture_gate
                            : gateReport.code_quality_gate;
                    const color = status === "pass" ? "#3fa66a" : status === "fail" ? "#d1495b" : "#8a8f98";
                    return (
                      <div
                        key={gate}
                        style={{
                          border: `1px solid ${color}`,
                          borderRadius: 6,
                          padding: "6px 10px",
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                        }}
                      >
                        <Text size={200} weight="semibold">
                          {GATE_LABELS[gate]}
                        </Text>
                        <Text size={200} style={{ color }}>
                          {status ? (status === "pass" ? "✅ PASS" : "❌ FAIL") : "— unknown"}
                        </Text>
                      </div>
                    );
                  },
                )}
              </div>
              <Text
                size={300}
                weight="semibold"
                style={{ color: gateReport.decision === "approved" ? "#3fa66a" : "#d1495b" }}
              >
                Peer Review decision:{" "}
                {gateReport.decision === "approved" ? "✅ APPROVED" : "🚫 BLOCKED"}
              </Text>

              {gateReport.findings.length > 0 ? (
                <div style={{ marginTop: 16 }}>
                  <Text size={300} weight="semibold" style={{ display: "block", marginBottom: 8 }}>
                    Findings - select which ones to fix
                  </Text>
                  {applyFixesError ? (
                    <MessageBar intent="error" layout="multiline" style={{ marginBottom: 8 }}>
                      <MessageBarBody>
                        <MessageBarTitle>Failed to apply fixes</MessageBarTitle>
                        {applyFixesError}
                      </MessageBarBody>
                    </MessageBar>
                  ) : null}
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
                    {gateReport.findings.map((finding) => (
                      <Checkbox
                        key={finding.id}
                        label={`[${GATE_LABELS[finding.gate]} · ${finding.severity}] ${finding.description}${finding.recommendation ? ` — Recommendation: ${finding.recommendation}` : ""}`}
                        checked={selectedFindings.has(finding.id)}
                        onChange={(_, chData) => toggleFinding(finding.id, Boolean(chData.checked))}
                      />
                    ))}
                  </div>
                  <Button
                    appearance="primary"
                    disabled={applyingFixes || selectedFindings.size === 0}
                    onClick={() => void handleApplySelectedFixes()}
                  >
                    {applyingFixes
                      ? "Applying selected fixes..."
                      : `Apply Selected Fixes (${selectedFindings.size})`}
                  </Button>
                </div>
              ) : null}
            </SectionCard>
          ) : null}

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
              <MessageBar intent="warning" layout="multiline" style={{ marginBottom: 12 }}>
                <MessageBarBody>
                  <MessageBarTitle>Prototype notice</MessageBarTitle>
                  Genie is an early-stage system. AI-generated recommendations, code, and
                  governance verdicts may contain mistakes. Review everything above carefully
                  before deploying.
                </MessageBarBody>
              </MessageBar>

              {deployError ? (
                <MessageBar intent="error" layout="multiline" style={{ marginBottom: 12 }}>
                  <MessageBarBody>
                    <MessageBarTitle>Failed to resume the workflow</MessageBarTitle>
                    {deployError}
                  </MessageBarBody>
                </MessageBar>
              ) : null}

              {isBlocked && !riskAccepted ? (
                <div style={{ marginBottom: 12 }}>
                  <Text size={300} style={{ display: "block", marginBottom: 8, color: "#d1495b" }}>
                    Peer Review has blocked this build. Apply fixes above, or explicitly accept the
                    residual risk with a justification to unlock deployment anyway.
                  </Text>
                  {riskAcceptanceError ? (
                    <MessageBar intent="error" layout="multiline" style={{ marginBottom: 8 }}>
                      <MessageBarBody>
                        <MessageBarTitle>Failed to record risk acceptance</MessageBarTitle>
                        {riskAcceptanceError}
                      </MessageBarBody>
                    </MessageBar>
                  ) : null}
                  <Textarea
                    value={justification}
                    onChange={(_, dataEv) => setJustification(dataEv.value)}
                    rows={3}
                    placeholder="Justify why it is acceptable to deploy despite the blocked Peer Review verdict..."
                    style={{ width: "100%", marginBottom: 8 }}
                  />
                  <Button
                    disabled={submittingRiskAcceptance || justification.trim().length === 0}
                    onClick={() => void handleAcceptRisk()}
                  >
                    {submittingRiskAcceptance ? "Recording..." : "Accept Risk & Unlock Deploy"}
                  </Button>
                </div>
              ) : null}

              <Text size={300} style={{ display: "block", marginBottom: 8, opacity: 0.8 }}>
                Governance has reviewed the build above. Approving provisions access control and
                deploys the UI and agent workflow.
              </Text>
              <Checkbox
                label="I understand this is a prototype and AI can make mistakes."
                checked={acknowledgedRisk}
                onChange={(_, chData) => setAcknowledgedRisk(Boolean(chData.checked))}
                style={{ marginBottom: 8 }}
              />
              <div>
                <Button
                  appearance="primary"
                  disabled={deploying || !canDeploy}
                  onClick={() => void handleApproveDeploy()}
                >
                  {deploying ? "Deploying..." : "Proceed to Deploy"}
                </Button>
              </div>
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

