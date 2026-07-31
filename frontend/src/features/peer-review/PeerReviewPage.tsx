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
import { deriveComplianceState, useGovernanceTrace } from "@/hooks/useGovernanceTrace";
import { useAsyncResource } from "@/hooks/useAsyncResource";
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
import { TestCoverageChart } from "@/components/TestCoverageChart";
import { useWorkflowEventStream, workflowStepDeltaKey } from "@/hooks/useWorkflowEventStream";
import type {
  AgentAssessment,
  GateName,
  GovernanceEventCategory,
  GovernanceFinding,
} from "@/types/governance";
import type { WorkflowStreamEvent } from "@/types/workflowEvents";

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

/** Shared "select findings to fix" checklist, reused across the Security
 * Assessment, Test Coverage, and consolidated Peer Review sections so a
 * customer can select any real finding from any of those agents' own
 * outputs and apply fixes for it through one unified action. */
function FindingsChecklist({
  findings,
  selectedFindings,
  onToggle,
}: {
  findings: GovernanceFinding[];
  selectedFindings: Set<string>;
  onToggle: (findingId: string, checked: boolean) => void;
}): JSX.Element {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {findings.map((finding) => (
        <Checkbox
          key={finding.id}
          label={`[${GATE_LABELS[finding.gate]} · ${finding.severity}] ${finding.description}${finding.recommendation ? ` — Recommendation: ${finding.recommendation}` : ""}`}
          checked={selectedFindings.has(finding.id)}
          onChange={(_, chData) => onToggle(finding.id, Boolean(chData.checked))}
        />
      ))}
    </div>
  );
}

const GATE_STATUS_COLORS: Record<"pass" | "fail", string> = {
  pass: "#3fa66a",
  fail: "#d1495b",
};

/** One specialist agent's (Security Assessment Agent, or Test Generation
 * Agent) own early single-gate assessment section - available as soon as
 * that agent's own step completes, without waiting for the Peer Review
 * Agent's slower consolidated verdict. */
function AgentAssessmentSection({
  title,
  waitingLabel,
  assessment,
  liveEvents,
  liveText,
  gateLabel,
  extra,
  selectedFindings,
  onToggleFinding,
  applyFixesError,
  applyingFixes,
  onApplyFixes,
}: {
  title: string;
  waitingLabel: string;
  assessment: AgentAssessment | undefined;
  liveEvents: WorkflowStreamEvent[];
  /** This agent's own real streamed output so far (see stepDeltaText on
   * useWorkflowEventStream) - shown in place of the generic activity
   * animation as soon as any real content has streamed in, since "nothing
   * shows for minutes" was the exact complaint this mirrors the Build
   * Agent fix for. */
  liveText: string;
  gateLabel: string;
  extra?: JSX.Element;
  selectedFindings: Set<string>;
  onToggleFinding: (findingId: string, checked: boolean) => void;
  applyFixesError: string | null;
  applyingFixes: boolean;
  onApplyFixes: () => void;
}): JSX.Element {
  if (!assessment || assessment.status === "pending") {
    return (
      <SectionCard title={title}>
        {liveText ? (
          <Text size={200} style={{ whiteSpace: "pre-wrap", display: "block", opacity: 0.85 }}>
            {liveText}
          </Text>
        ) : (
          <AgentActivityAnimation label={waitingLabel} events={liveEvents} />
        )}
      </SectionCard>
    );
  }

  if (assessment.status === "undetermined") {
    return (
      <SectionCard title={title}>
        <Text size={300} style={{ opacity: 0.7 }}>
          This agent's step completed, but its output did not contain a parseable verdict.
        </Text>
      </SectionCard>
    );
  }

  const selectedCount = assessment.findings.filter((finding) => selectedFindings.has(finding.id)).length;

  return (
    <SectionCard title={title}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <div
          style={{
            border: `1px solid ${assessment.gate ? GATE_STATUS_COLORS[assessment.gate] : "#8a8f98"}`,
            borderRadius: 6,
            padding: "6px 10px",
          }}
        >
          <Text
            size={200}
            weight="semibold"
            style={{ color: assessment.gate ? GATE_STATUS_COLORS[assessment.gate] : "#8a8f98" }}
          >
            {gateLabel}: {assessment.gate ? (assessment.gate === "pass" ? "✅ PASS" : "❌ FAIL") : "— unknown"}
          </Text>
        </div>
        {assessment.assessed_by_agent_id ? (
          <Text size={200} style={{ opacity: 0.6 }}>
            assessed by {assessment.assessed_by_agent_id}
          </Text>
        ) : null}
      </div>

      {extra}

      {assessment.findings.length > 0 ? (
        <div style={{ marginTop: 12 }}>
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
          <div style={{ marginBottom: 10 }}>
            <FindingsChecklist
              findings={assessment.findings}
              selectedFindings={selectedFindings}
              onToggle={onToggleFinding}
            />
          </div>
          <Button appearance="primary" disabled={applyingFixes || selectedCount === 0} onClick={onApplyFixes}>
            {applyingFixes ? "Applying selected fixes..." : `Apply Selected Fixes (${selectedCount})`}
          </Button>
        </div>
      ) : (
        <Text size={300} style={{ opacity: 0.7, display: "block", marginTop: 8 }}>
          No findings raised.
        </Text>
      )}

      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", opacity: 0.8, fontSize: 13 }}>
          View full agent output
        </summary>
        <Text size={200} style={{ whiteSpace: "pre-wrap", display: "block", marginTop: 8, opacity: 0.85 }}>
          {assessment.summary}
        </Text>
      </details>
    </SectionCard>
  );
}

export function PeerReviewPage(): JSX.Element {
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

  const agentAssessmentsFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? governanceApi.getAgentAssessments(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { data: agentAssessments, refresh: refreshAgentAssessments } = useAsyncResource(
    agentAssessmentsFetcher,
    [sessionId, workflowRunId],
    { enabled: Boolean(sessionId && workflowRunId) },
  );

  const { events: liveEvents, connected: liveConnected, stepDeltaText } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refresh();
      refreshGateReport();
      refreshAgentAssessments();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
  const peerReviewText = useMemo(
    () => run?.step_results.find((result) => result.step_id === "peer-review")?.output_text ?? "",
    [run],
  );
  // Each specialist's own real streamed output so far (never
  // genie-orchestrator's later verbatim echo of the same text - see
  // _stream_and_publish_deltas in orchestration_tools.py) - shown in each
  // section below in place of a generic "waiting" animation while that
  // specialist's step is still running, mirroring the Build Agent live
  // streaming fix on WorkshopPage.
  const liveSecurityAssessmentText =
    stepDeltaText[workflowStepDeltaKey("security-assessment", "security-assessment-agent")] ?? "";
  const liveTestGenerationText =
    stepDeltaText[workflowStepDeltaKey("test-generation", "test-generation-agent")] ?? "";
  const livePeerReviewText =
    stepDeltaText[workflowStepDeltaKey("peer-review", "peer-review-agent")] ?? "";

  const riskAccepted = useMemo(
    () =>
      data?.events.some(
        (event) =>
          event.category === "risk_accepted" &&
          (event.detail as { workflow_run_id?: string }).workflow_run_id === workflowRunId,
      ) ?? false,
    [data, workflowRunId],
  );

  // The prominent "Overall status" badge must reflect the Peer Review
  // Agent's own real verdict (gateReport), not just approval bookkeeping -
  // deriveComplianceState only ever reports "compliant" once gateReport is
  // actually "reviewed" with an "approved" decision, so this correctly
  // shows "Reviewing..." while genie-orchestrator is still working the
  // peer-review step instead of a premature default.
  const complianceState = useMemo(
    () => deriveComplianceState(data?.events ?? [], data?.approvals ?? [], gateReport, riskAccepted),
    [data, gateReport, riskAccepted],
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

  // Findings can come from three real, independent sources - the Security
  // Assessment Agent's own early verdict, the Test Generation Agent's own
  // early verdict, and the Peer Review Agent's later consolidated verdict.
  // "Apply Selected Fixes" must work no matter which section a customer
  // checked a finding in, so every known finding is indexed here by its own
  // id, regardless of source.
  const allKnownFindings = useMemo(() => {
    const byId = new Map<string, GovernanceFinding>();
    for (const finding of gateReport?.findings ?? []) byId.set(finding.id, finding);
    for (const finding of agentAssessments?.security_assessment.findings ?? []) byId.set(finding.id, finding);
    for (const finding of agentAssessments?.test_generation.findings ?? []) byId.set(finding.id, finding);
    return byId;
  }, [gateReport, agentAssessments]);

  const handleApplySelectedFixes = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    const descriptions = [...allKnownFindings.values()]
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
      refreshAgentAssessments();
      await refresh();
    } catch (err) {
      setApplyFixesError((err as ApiError).message ?? "Failed to apply the selected fixes.");
    } finally {
      setApplyingFixes(false);
    }
  }, [sessionId, workflowRunId, allKnownFindings, selectedFindings, refresh, refreshGateReport, refreshAgentAssessments]);

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

  const [acknowledgedRisk, setAcknowledgedRisk] = useState(false);
  const isBlocked = gateReport?.decision === "blocked";
  const reviewComplete = Boolean(gateReport && gateReport.status === "reviewed");
  const canProceedToDeploy = reviewComplete && acknowledgedRisk && (!isBlocked || riskAccepted);

  if (!sessionId) {
    return (
      <div>
        <PageHeader title="Peer Review" subtitle="No active session yet." />
        <Button appearance="primary" onClick={() => navigate("/")}>
          Start a session
        </Button>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Peer Review"
        subtitle="Independent code review, security assessment, and test coverage verdict for this build."
      />
      {loading && !data ? <LoadingState label="Loading peer review trace..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      <LiveWorkflowPulse connected={liveConnected} events={liveEvents} />

      {data ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Text weight="semibold" size={500}>
              Overall status
            </Text>
            <GovernanceStatusBadge state={complianceState} />
          </div>

          <AgentAssessmentSection
            title="🔒 Security Assessment"
            waitingLabel="Genie is working with the Security Assessment Agent to review the build for security issues..."
            assessment={agentAssessments?.security_assessment}
            liveEvents={liveEvents}
            liveText={liveSecurityAssessmentText}
            gateLabel="Security Gate"
            selectedFindings={selectedFindings}
            onToggleFinding={toggleFinding}
            applyFixesError={applyFixesError}
            applyingFixes={applyingFixes}
            onApplyFixes={() => void handleApplySelectedFixes()}
          />

          <AgentAssessmentSection
            title="🧪 Test Coverage"
            waitingLabel="Genie is working with the Test Generation Agent to generate and assess test coverage..."
            assessment={agentAssessments?.test_generation}
            liveEvents={liveEvents}
            liveText={liveTestGenerationText}
            gateLabel="Test Coverage Gate"
            extra={
              agentAssessments?.test_generation.status === "reviewed" ? (
                <TestCoverageChart
                  testsGenerated={agentAssessments.test_generation.tests_generated}
                  coverageGapsFound={agentAssessments.test_generation.findings.length}
                />
              ) : undefined
            }
            selectedFindings={selectedFindings}
            onToggleFinding={toggleFinding}
            applyFixesError={applyFixesError}
            applyingFixes={applyingFixes}
            onApplyFixes={() => void handleApplySelectedFixes()}
          />

          {gateReport && gateReport.status === "reviewed" ? (
            <SectionCard title="🛡️ Peer Review Gate Verdict — Architecture & Code Quality (Consolidated)">
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
                  <div style={{ marginBottom: 10 }}>
                    <FindingsChecklist
                      findings={gateReport.findings}
                      selectedFindings={selectedFindings}
                      onToggle={toggleFinding}
                    />
                  </div>
                  <Button
                    appearance="primary"
                    disabled={
                      applyingFixes ||
                      gateReport.findings.filter((finding) => selectedFindings.has(finding.id)).length === 0
                    }
                    onClick={() => void handleApplySelectedFixes()}
                  >
                    {applyingFixes
                      ? "Applying selected fixes..."
                      : `Apply Selected Fixes (${gateReport.findings.filter((finding) => selectedFindings.has(finding.id)).length})`}
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

          {peerReviewText ? (
            <SectionCard title="Peer Review">
              <Text size={300} style={{ whiteSpace: "pre-wrap" }}>
                {peerReviewText}
              </Text>
            </SectionCard>
          ) : livePeerReviewText ? (
            <SectionCard title="Peer Review">
              <Text size={200} style={{ whiteSpace: "pre-wrap", opacity: 0.85 }}>
                {livePeerReviewText}
              </Text>
            </SectionCard>
          ) : (
            <AgentActivityAnimation
              label="Genie is working with the Peer Review agent to evaluate this build..."
              events={liveEvents}
            />
          )}

          {reviewComplete ? (
            <SectionCard title="Proceed to Deploy & Launch">
              <MessageBar intent="warning" layout="multiline" style={{ marginBottom: 12 }}>
                <MessageBarBody>
                  <MessageBarTitle>Prototype notice</MessageBarTitle>
                  Genie is an early-stage system. AI-generated recommendations, code, and review
                  verdicts may contain mistakes. Review everything above carefully before
                  deploying.
                </MessageBarBody>
              </MessageBar>

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
                Peer Review has reviewed the build above. Proceeding takes you to Deploy & Launch,
                which provisions access control, agents, backend/frontend, and runs full testing
                and a security scan before minting the customer-facing launch link.
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
                  disabled={!canProceedToDeploy}
                  onClick={() => navigate("/outputs")}
                >
                  Proceed to Deploy & Launch
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
