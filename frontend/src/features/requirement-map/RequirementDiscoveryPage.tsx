import { useCallback, useMemo, useState } from "react";
import {
  Badge,
  Button,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Text,
  Textarea,
} from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import {
  useRequirementActions,
  useRequirements,
  useRequirementsQualification,
} from "@/hooks/useRequirements";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { approvalApi } from "@/services/approvalApi";
import { workflowApi } from "@/services/workflowApi";
import { getTraceId } from "@/state/traceRegistry";
import type { WorkflowStepInput } from "@/types/workflow";
import type { ApiError } from "@/services/httpClient";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";

const POLL_MS = Number(import.meta.env.VITE_REQUIREMENTS_POLL_MS ?? 0);

/** Strip a leading bullet/number marker ("- ", "* ", "1. ", "2) ") off one line. */
function stripMarker(line: string): string {
  return line
    .replace(/^\s*[-*•]\s*/, "")
    .replace(/^\s*\d+[.)]\s*/, "")
    .trim();
}

/** Splits the analyst's free-text output into one editable item per line. */
function parseRequirementItems(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map(stripMarker)
    .filter((line) => line.length > 0);
}

/**
 * The analyst's reviewed output (requirements-extraction-v1 prompt) now
 * includes a "Critical path:" heading line ahead of the ordered subset of
 * requirements that must be delivered first. Detected purely by text so
 * the checklist can render that heading and the requirements under it as
 * their own highlighted section instead of an indistinguishable row.
 */
function isCriticalPathHeading(line: string): boolean {
  return /^critical path\b/i.test(line.trim());
}

const CLASSIFICATION_META: Record<string, { icon: string; accent: string }> = {
  requirement: { icon: "📋", accent: "#2f83e0" },
  goal: { icon: "🎯", accent: "#3fa66a" },
  constraint: { icon: "🚧", accent: "#d99a2b" },
  risk: { icon: "⚠️", accent: "#d1495b" },
  assumption: { icon: "🧩", accent: "#8a63d2" },
};

function classificationMeta(classification: string): { icon: string; accent: string } {
  return CLASSIFICATION_META[classification] ?? { icon: "📄", accent: "#5c6572" };
}

const APPROVAL_STATUS_META: Record<string, { icon: string; accent: string }> = {
  approved: { icon: "✅", accent: "#3fa66a" },
  pending: { icon: "⏳", accent: "#d99a2b" },
  rejected: { icon: "⛔", accent: "#d1495b" },
  challenged: { icon: "⚠️", accent: "#d99a2b" },
};
const DEFAULT_APPROVAL_META = { icon: "•", accent: "#5c6572" };

function approvalStatusMeta(status: string): { icon: string; accent: string } {
  return APPROVAL_STATUS_META[status] ?? DEFAULT_APPROVAL_META;
}

export function RequirementDiscoveryPage(): JSX.Element {
  const { sessionId, workflowRunId } = useSessionContext();
  const { data, loading, error, refresh } = useRequirements(sessionId, workflowRunId, POLL_MS);
  const { data: qualification } = useRequirementsQualification(sessionId, workflowRunId, POLL_MS);
  const { challenge } = useRequirementActions();
  const [rationaleByKey, setRationaleByKey] = useState<Record<string, string>>({});
  const [policiesByRequest, setPoliciesByRequest] = useState<Record<string, string>>({});
  const [requirementItems, setRequirementItems] = useState<string[] | null>(null);
  const [showRawText, setShowRawText] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [resumingRequestId, setResumingRequestId] = useState<string | null>(null);

  // The analyst's discovered requirements are free text (the workflow run's
  // analyze-requirements step output), separate from the (currently
  // unpopulated - Shared Memory is never written to) structured records
  // above. Parsed into one editable item per line - rather than one large
  // textarea - so the user can scan, edit, remove, and add requirements as
  // a checklist before approving the design-architecture gate.
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
  const analyzedRequirementsText = useMemo(
    () =>
      run?.step_results.find((result) => result.step_id === "analyze-requirements")?.output_text ?? "",
    [run],
  );
  const effectiveRequirementItems = useMemo(
    () => requirementItems ?? parseRequirementItems(analyzedRequirementsText),
    [requirementItems, analyzedRequirementsText],
  );
  const effectiveRequirementsDraft = useMemo(
    () => effectiveRequirementItems.map((item) => `- ${item}`).join("\n"),
    [effectiveRequirementItems],
  );
  const criticalPathHeadingIndex = useMemo(
    () => effectiveRequirementItems.findIndex((item) => isCriticalPathHeading(item)),
    [effectiveRequirementItems],
  );

  // Live breakdown of discovered items by classification, driving the hero
  // banner's stat pills below - purely derived from real Shared Memory
  // records, never a hardcoded/synthetic count.
  const classificationCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const { record } of data?.items ?? []) {
      counts[record.classification] = (counts[record.classification] ?? 0) + 1;
    }
    return counts;
  }, [data]);

  const updateRequirementItem = useCallback(
    (index: number, value: string) => {
      setRequirementItems((prev) => {
        const base = prev ?? parseRequirementItems(analyzedRequirementsText);
        const next = [...base];
        next[index] = value;
        return next;
      });
    },
    [analyzedRequirementsText],
  );
  const removeRequirementItem = useCallback(
    (index: number) => {
      setRequirementItems((prev) => {
        const base = prev ?? parseRequirementItems(analyzedRequirementsText);
        return base.filter((_, i) => i !== index);
      });
    },
    [analyzedRequirementsText],
  );
  const addRequirementItem = useCallback(() => {
    setRequirementItems((prev) => {
      const base = prev ?? parseRequirementItems(analyzedRequirementsText);
      return [...base, ""];
    });
  }, [analyzedRequirementsText]);


  // Real ApprovalRequest entries for this session (subject_type is
  // currently always "workflow_step" per backend/app/orchestration/
  // workflow_runtime.py - there is no per-requirement approval subject yet,
  // so approve/reject is shown as its own section rather than invented
  // per-requirement buttons that would call a nonexistent request id.
  const approvalsFetcher = useCallback(
    () => (sessionId ? approvalApi.list(sessionId) : Promise.reject(new Error("No session"))),
    [sessionId],
  );
  const {
    data: approvals,
    loading: approvalsLoading,
    error: approvalsError,
    refresh: refreshApprovals,
  } = useAsyncResource(approvalsFetcher, [sessionId], { enabled: Boolean(sessionId) });

  // Approving an ApprovalRequest only records the decision - it never
  // resumes the gated workflow run on its own (backend/app/api/
  // approvals.py's decide_approval is intentionally decision-only). Without
  // this, the run permanently freezes at "waiting_for_approval" once
  // approved. For the governance-review step specifically, its `policies`
  // prompt variable is deliberately never auto-derived from the transcript
  // (config/workflows/registry.yaml) - a human must supply it explicitly as
  // a step_input, so we collect it here before resuming.
  const approveAndResume = useCallback(
    async (requestId: string, subjectId: string) => {
      if (!sessionId || !workflowRunId) return;
      setResumeError(null);
      setResumingRequestId(requestId);
      try {
        await approvalApi.decide(sessionId, requestId, "approved");
        await refreshApprovals();
        const traceId = getTraceId(workflowRunId) ?? undefined;
        const stepInputs: Record<string, WorkflowStepInput> | undefined =
          subjectId === "design-architecture"
            ? {
                "design-architecture": {
                  step_id: "design-architecture",
                  variables: { approved_requirements: effectiveRequirementsDraft },
                },
              }
            : subjectId === "build-solution"
              ? {
                  "governance-review": {
                    step_id: "governance-review",
                    variables: { policies: policiesByRequest[requestId] ?? "" },
                  },
                }
              : undefined;
        await workflowApi.resumeRun(sessionId, workflowRunId, traceId, stepInputs);
        // Resuming can complete further steps that raise their own new
        // approval requests (e.g. final-output-approval) - refetch so any
        // newly pending request appears without requiring a manual reload.
        await Promise.all([refresh(), refreshApprovals()]);
      } catch (err) {
        setResumeError((err as ApiError).message ?? "Failed to resume the workflow.");
      } finally {
        setResumingRequestId(null);
      }
    },
[sessionId, workflowRunId, policiesByRequest, effectiveRequirementsDraft, refresh, refreshApprovals],
  );

  if (!workflowRunId) {
    return (
      <div className="genie-fade-in" style={{ maxWidth: 560, margin: "10vh auto", textAlign: "center" }}>
        <span className="genie-sparkle" style={{ fontSize: 48, display: "block", marginBottom: 12 }}>
          🧭
        </span>
        <Text weight="bold" size={600} style={{ display: "block", marginBottom: 8 }}>
          Requirement Discovery Map
        </Text>
        <Text
          size={300}
          style={{
            opacity: 0.75,
            display: "block",
            padding: 20,
            borderRadius: 12,
            border: "1px solid #232a33",
            backgroundColor: "rgba(19, 25, 33, 0.55)",
          }}
        >
          Start a workflow run from Upload to begin discovering requirements.
        </Text>
      </div>
    );
  }

  return (
    <div>
      <div
        className="genie-fade-in"
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 16,
          marginBottom: 24,
          padding: "20px 24px",
          borderRadius: 16,
          border: "1px solid #232a33",
          backgroundImage:
            "linear-gradient(135deg, rgba(47, 131, 224, 0.16) 0%, rgba(138, 99, 210, 0.10) 100%)",
          backgroundColor: "rgba(19, 25, 33, 0.6)",
          boxShadow: "0 8px 30px rgba(0, 0, 0, 0.25)",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="genie-sparkle" style={{ fontSize: 28 }}>
              🧭
            </span>
            <Text
              weight="bold"
              size={700}
              style={{
                backgroundImage: "linear-gradient(135deg, #6ba3ea 0%, #a3c4f3 100%)",
                backgroundClip: "text",
                WebkitBackgroundClip: "text",
                color: "transparent",
              }}
            >
              Requirement Discovery Map
            </Text>
          </div>
          <Text size={300} style={{ opacity: 0.75, display: "block", marginTop: 6, maxWidth: 520 }}>
            Goals, requirements, constraints, risks, and assumptions surfaced from Shared
            Collaboration Memory - reviewed and refined here before the architecture gate opens.
          </Text>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(CLASSIFICATION_META).map(([key, meta]) => {
            const count = classificationCounts[key] ?? 0;
            return (
              <div
                key={key}
                title={key.replace(/_/g, " ")}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 12px",
                  borderRadius: 999,
                  border: `1px solid ${meta.accent}55`,
                  backgroundColor: `${meta.accent}1a`,
                  opacity: count > 0 ? 1 : 0.45,
                }}
              >
                <span style={{ fontSize: 15 }}>{meta.icon}</span>
                <Text size={200} weight="semibold" style={{ color: meta.accent }}>
                  {count}
                </Text>
                <Text size={100} style={{ opacity: 0.7, textTransform: "capitalize" }}>
                  {key.replace(/_/g, " ")}
                </Text>
              </div>
            );
          })}
        </div>
      </div>
      {loading && !data ? <LoadingState label="Loading requirements..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      {qualification?.status === "not_qualified" ? (
        <MessageBar intent="warning" layout="multiline" style={{ marginBottom: 16 }}>
          <MessageBarBody>
            <MessageBarTitle>This requirement doesn&apos;t currently qualify for an agentic AI workflow</MessageBarTitle>
            {qualification.reason ??
              "The Requirements Analyst agent determined a simpler, non-agentic solution is more appropriate here."}
          </MessageBarBody>
        </MessageBar>
      ) : null}

      {analyzedRequirementsText ? (
        <SectionCard
          title="📝 Requirements Checklist (edit before approving)"
          action={
            <Button appearance="subtle" size="small" onClick={() => setShowRawText((prev) => !prev)}>
              {showRawText ? "Hide raw output" : "View raw analyst output"}
            </Button>
          }
        >
          <Text size={300} style={{ display: "block", marginBottom: 12, opacity: 0.8 }}>
            Edit, remove, or add requirements below - this is what the architecture design step will
            use once you approve.
          </Text>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
            {effectiveRequirementItems.length === 0 ? (
              <Text size={200} style={{ opacity: 0.6 }}>
                No requirements yet - add one below.
              </Text>
            ) : (
              effectiveRequirementItems.map((item, index) => {
                const isHeading = isCriticalPathHeading(item);
                const isCritical = criticalPathHeadingIndex !== -1 && index > criticalPathHeadingIndex;

                if (isHeading) {
                  return (
                    <div
                      key={index}
                      className="genie-fade-in"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "8px 12px",
                        marginTop: index > 0 ? 6 : 0,
                        borderRadius: 8,
                        border: "1px solid #d99a2b66",
                        backgroundColor: "rgba(217, 154, 43, 0.12)",
                        animationDelay: `${Math.min(index, 12) * 30}ms`,
                      }}
                    >
                      <span style={{ fontSize: 16 }}>🎯</span>
                      <Text
                        size={200}
                        weight="bold"
                        style={{ color: "#d99a2b", textTransform: "uppercase", letterSpacing: 1 }}
                      >
                        Critical Path
                      </Text>
                      <Button
                        appearance="subtle"
                        size="small"
                        shape="circular"
                        title="Remove this heading"
                        aria-label="Remove this heading"
                        onClick={() => removeRequirementItem(index)}
                        style={{ marginLeft: "auto" }}
                      >
                        ✕
                      </Button>
                    </div>
                  );
                }

                return (
                  <div
                    key={index}
                    className="genie-fade-in genie-req-item"
                    style={{
                      display: "flex",
                      gap: 8,
                      alignItems: "flex-start",
                      padding: "8px 10px",
                      borderRadius: 8,
                      border: isCritical ? "1px solid #d99a2b55" : "1px solid #232a33",
                      backgroundColor: isCritical ? "rgba(217, 154, 43, 0.06)" : "#161c24",
                      animationDelay: `${Math.min(index, 12) * 30}ms`,
                    }}
                  >
                    <span
                      style={{
                        flexShrink: 0,
                        width: 22,
                        height: 22,
                        marginTop: 4,
                        borderRadius: "50%",
                        backgroundImage: isCritical
                          ? "linear-gradient(135deg, #d99a2b, #e0b354)"
                          : "linear-gradient(135deg, #2f83e0, #8a63d2)",
                        color: "#f4f7fb",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 11,
                        fontWeight: 700,
                      }}
                    >
                      {index + 1}
                    </span>
                    <Textarea
                      value={item}
                      onChange={(_, dataEv) => updateRequirementItem(index, dataEv.value)}
                      resize="vertical"
                      style={{ flex: 1 }}
                    />
                    {isCritical ? (
                      <Badge
                        shape="rounded"
                        style={{ backgroundColor: "rgba(217, 154, 43, 0.18)", color: "#d99a2b", flexShrink: 0 }}
                      >
                        🎯 Critical
                      </Badge>
                    ) : null}
                    <Button
                      appearance="subtle"
                      size="small"
                      shape="circular"
                      title="Remove this requirement"
                      aria-label="Remove this requirement"
                      onClick={() => removeRequirementItem(index)}
                    >
                      ✕
                    </Button>
                  </div>
                );
              })
            )}
          </div>
          <Button appearance="secondary" size="small" onClick={addRequirementItem}>
            + Add requirement
          </Button>

          {showRawText ? (
            <Textarea
              value={analyzedRequirementsText}
              readOnly
              rows={10}
              style={{ width: "100%", fontFamily: "monospace", fontSize: 12, marginTop: 12, opacity: 0.8 }}
            />
          ) : null}
        </SectionCard>
      ) : null}

      {data ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
          {data.items.length === 0 ? (
            <Text size={300} style={{ opacity: 0.7 }}>
              No requirements discovered yet.
            </Text>
          ) : (
            data.items.map(({ record }) => {
              const meta = classificationMeta(record.classification);
              const confidencePct = Math.round(record.lineage.confidence_score * 100);
              const approvalMeta = approvalStatusMeta(record.lineage.approval_status);
              return (
                <SectionCard
                  key={record.id}
                  title={
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          width: 26,
                          height: 26,
                          borderRadius: "50%",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: 13,
                          backgroundColor: `${meta.accent}22`,
                          border: `1px solid ${meta.accent}66`,
                        }}
                      >
                        {meta.icon}
                      </span>
                      <span style={{ textTransform: "capitalize" }}>
                        {record.classification.replace(/_/g, " ")}
                      </span>
                    </span>
                  }
                  action={<Badge appearance="tint">v{record.version}</Badge>}
                >
                  <div style={{ borderLeft: `3px solid ${meta.accent}`, paddingLeft: 12 }}>
                    <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", marginBottom: 8, marginTop: 0 }}>
                      {JSON.stringify(record.content, null, 2)}
                    </pre>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                      <Badge
                        shape="rounded"
                        style={{ backgroundColor: "#232a33", color: "#9aa4b2" }}
                        title="Confidence score"
                      >
                        📈 {confidencePct}% confidence
                      </Badge>
                      <Badge
                        shape="rounded"
                        style={{ backgroundColor: `${approvalMeta.accent}22`, color: approvalMeta.accent }}
                      >
                        {approvalMeta.icon} {record.lineage.approval_status.replace(/_/g, " ")}
                      </Badge>
                    </div>
                    <Textarea
                      placeholder="Rationale for challenging this item"
                      value={rationaleByKey[record.id] ?? ""}
                      onChange={(_, dataEv) =>
                        setRationaleByKey((prev) => ({ ...prev, [record.id]: dataEv.value }))
                      }
                      style={{ marginBottom: 8, width: "100%" }}
                    />
                    <Button
                      size="small"
                      onClick={() => {
                        const traceId = getTraceId(workflowRunId);
                        if (!sessionId || !traceId) return;
                        void challenge(
                          sessionId,
                          workflowRunId,
                          traceId,
                          record.id,
                          rationaleByKey[record.id] ?? "",
                        ).then(refresh);
                      }}
                    >
                      Challenge
                    </Button>
                  </div>
                </SectionCard>
              );
            })
          )}
        </div>
      ) : null}

      <SectionCard title="🔑 Pending Approvals">
        {approvalsLoading && !approvals ? <LoadingState label="Loading approvals..." /> : null}
        {approvalsError ? <ErrorState error={approvalsError} onRetry={refreshApprovals} /> : null}
        {resumeError ? (
          <MessageBar intent="error" layout="multiline" style={{ marginBottom: 12 }}>
            <MessageBarBody>
              <MessageBarTitle>Failed to resume the workflow</MessageBarTitle>
              {resumeError}
            </MessageBarBody>
          </MessageBar>
        ) : null}
        {approvals && approvals.length === 0 ? (
          <Text size={300} style={{ opacity: 0.7 }}>
            No pending approvals.
          </Text>
        ) : null}
        {approvals?.map((request) => {
          const statusMeta = approvalStatusMeta(request.status);
          return (
          <div
            key={request.id}
            className="genie-fade-in"
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 12,
              borderRadius: 8,
              border: "1px solid #232a33",
              borderLeft: `3px solid ${statusMeta.accent}`,
              backgroundColor: "#161c24",
              padding: "10px 12px",
              marginBottom: 8,
            }}
          >
            <div>
              <Text size={300} weight="semibold" style={{ display: "block" }}>
                🗂️ {request.subject_type.replace(/_/g, " ")} · {request.subject_id}
              </Text>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
                <Text size={200} style={{ opacity: 0.7 }}>
                  Requested by {request.requested_by_agent_id}
                </Text>
                <Badge
                  shape="rounded"
                  style={{ backgroundColor: `${statusMeta.accent}22`, color: statusMeta.accent }}
                >
                  {statusMeta.icon} {request.status}
                </Badge>
              </div>
            </div>
            {request.status === "pending" && sessionId ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-end" }}>
                {request.subject_id === "build-solution" ? (
                  <Textarea
                    placeholder="Policies to review against (required to resume this step)"
                    value={policiesByRequest[request.id] ?? ""}
                    onChange={(_, dataEv) =>
                      setPoliciesByRequest((prev) => ({ ...prev, [request.id]: dataEv.value }))
                    }
                    style={{ width: "100%" }}
                  />
                ) : null}
                <div style={{ display: "flex", gap: 8 }}>
                  <Button
                    size="small"
                    appearance="primary"
                    disabled={
                      resumingRequestId === request.id ||
                      (request.subject_id === "build-solution" &&
                        !policiesByRequest[request.id]?.trim())
                    }
                    onClick={() => void approveAndResume(request.id, request.subject_id)}
                  >
                    {resumingRequestId === request.id ? "Approving..." : "Approve"}
                  </Button>
                  <Button
                    size="small"
                    onClick={() =>
                      void approvalApi
                        .decide(sessionId, request.id, "rejected")
                        .then(refreshApprovals)
                    }
                  >
                    Reject
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
          );
        })}
      </SectionCard>
    </div>
  );
}
