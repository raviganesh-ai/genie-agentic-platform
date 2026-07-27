import { useCallback, useState } from "react";
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
import { getTraceId } from "@/state/traceRegistry";
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

  if (!workflowRunId) {
    return (
      <div>
        <PageHeader title="Requirement Discovery Map" />
        <Text size={300} style={{ opacity: 0.7 }}>
          Start a workflow run from Mission Control to begin discovering requirements.
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
              <div style={{ display: "flex", gap: 8 }}>
                <Button
                  size="small"
                  appearance="primary"
                  onClick={() =>
                    void approvalApi
                      .decide(sessionId, request.id, "approved")
                      .then(refreshApprovals)
                  }
                >
                  Approve
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
            ) : null}
          </div>
        ))}
      </SectionCard>
    </div>
  );
}
