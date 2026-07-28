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
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";

const POLL_MS = Number(import.meta.env.VITE_REQUIREMENTS_POLL_MS ?? 0);

export function RequirementDiscoveryPage(): JSX.Element {
  const { sessionId, workflowRunId } = useSessionContext();
  const { data, loading, error, refresh } = useRequirements(sessionId, workflowRunId, POLL_MS);
  const { data: qualification } = useRequirementsQualification(sessionId, workflowRunId, POLL_MS);
  const { challenge } = useRequirementActions();
  const [rationaleByKey, setRationaleByKey] = useState<Record<string, string>>({});
  const [policiesByRequest, setPoliciesByRequest] = useState<Record<string, string>>({});
  const [requirementsDraft, setRequirementsDraft] = useState<string | null>(null);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [resumingRequestId, setResumingRequestId] = useState<string | null>(null);

  // The analyst's discovered requirements are free text (the workflow run's
  // analyze-requirements step output), separate from the (currently
  // unpopulated - Shared Memory is never written to) structured records
  // above. Editable here so the user can add/remove requirements before
  // approving the design-architecture gate.
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
  const effectiveRequirementsDraft = requirementsDraft ?? analyzedRequirementsText;

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
      <div>
        <PageHeader title="Requirement Discovery Map" />
        <Text size={300} style={{ opacity: 0.7 }}>
          Start a workflow run from Upload to begin discovering requirements.
        </Text>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Requirement Discovery Map"
        subtitle="Goals, requirements, constraints, risks, and assumptions surfaced from Shared Collaboration Memory."
      />
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
        <SectionCard title="Discovered Requirements (edit before approving)">
          <Text size={300} style={{ display: "block", marginBottom: 8, opacity: 0.8 }}>
            Add or remove requirements below - this text is what the architecture design step will
            use once you approve.
          </Text>
          <Textarea
            value={effectiveRequirementsDraft}
            onChange={(_, dataEv) => setRequirementsDraft(dataEv.value)}
            rows={12}
            style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
          />
        </SectionCard>
      ) : null}

      {data ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 24 }}>
          {data.items.length === 0 ? (
            <Text size={300} style={{ opacity: 0.7 }}>
              No requirements discovered yet.
            </Text>
          ) : (
            data.items.map(({ record }) => (
              <SectionCard
                key={record.id}
                title={record.classification.replace(/_/g, " ")}
                action={<Badge appearance="tint">v{record.version}</Badge>}
              >
                <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", marginBottom: 8 }}>
                  {JSON.stringify(record.content, null, 2)}
                </pre>
                <Text size={200} style={{ opacity: 0.7, display: "block", marginBottom: 8 }}>
                  Confidence: {Math.round(record.lineage.confidence_score * 100)}% · Approval:{" "}
                  {record.lineage.approval_status}
                </Text>
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
              </SectionCard>
            ))
          )}
        </div>
      ) : null}

      <SectionCard title="Pending Approvals">
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
        {approvals?.map((request) => (
          <div
            key={request.id}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderBottom: "1px solid #232a33",
              padding: "8px 0",
            }}
          >
            <div>
              <Text size={300} style={{ display: "block" }}>
                {request.subject_type.replace(/_/g, " ")} · {request.subject_id}
              </Text>
              <Text size={200} style={{ opacity: 0.7 }}>
                Requested by {request.requested_by_agent_id} · {request.status}
              </Text>
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
        ))}
      </SectionCard>
    </div>
  );
}
