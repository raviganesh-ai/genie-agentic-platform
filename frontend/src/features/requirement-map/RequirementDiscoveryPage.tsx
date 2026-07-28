import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { useRequirements, useRequirementsQualification } from "@/hooks/useRequirements";
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

/**
 * The analyst's reviewed output (requirements-extraction-v1 prompt) now
 * includes a "Critical path:" heading line ahead of the ordered subset of
 * requirements that must be delivered first. Detected purely by text so
 * that subset can be rendered as its own highlighted "Scope of
 * Prototyping" section instead of an indistinguishable row.
 */
function isCriticalPathHeading(line: string): boolean {
  return /^critical path\b/i.test(line.trim());
}

/** Stop parsing once the trailing structured qualification block starts - those two lines are never part of the requirements text itself. */
function isQualificationBoundary(line: string): boolean {
  return /^AGENTIC_WORKFLOW_QUALIFICATION\s*:/i.test(line.trim());
}

interface RequirementGroup {
  key: string;
  label: string;
  icon: string;
  accent: string;
  items: string[];
}

interface ParsedRequirements {
  groups: RequirementGroup[];
  criticalPath: string[];
}

/**
 * Category headings the requirements-extraction-v1 prompt is instructed to
 * emit (in this order) ahead of its "Critical path:" section. Detected
 * purely by heading text - not hardcoded per-item logic - so parsing stays
 * correct even as the prompt template evolves independently of this UI.
 */
const CATEGORY_DEFS: { key: string; heading: RegExp; icon: string; accent: string; label: string }[] = [
  { key: "goals", heading: /^goals\s*:?$/i, icon: "🎯", accent: "#3fa66a", label: "Goals" },
  {
    key: "functional",
    heading: /^functional requirements\s*:?$/i,
    icon: "⚙️",
    accent: "#2f83e0",
    label: "Functional Requirements",
  },
  {
    key: "non_functional",
    heading: /^non-?functional requirements\s*:?$/i,
    icon: "🛡️",
    accent: "#8a63d2",
    label: "Non-Functional Requirements",
  },
  { key: "risks", heading: /^risks\s*:?$/i, icon: "⚠️", accent: "#d1495b", label: "Risks" },
  { key: "assumptions", heading: /^assumptions\s*:?$/i, icon: "🧩", accent: "#17a2b8", label: "Assumptions" },
  { key: "constraints", heading: /^constraints\s*:?$/i, icon: "🚧", accent: "#d99a2b", label: "Constraints" },
];

/**
 * Groups the analyst's reviewed, summarized output (requirements-extraction-v1)
 * into one collapsible section per category heading it was instructed to
 * emit, plus the "Critical path:" subset kept separate so it can be shown
 * as its own "Scope of Prototyping" callout instead of buried inside a
 * category list. Falls back to a single "Requirements" bucket for any
 * lines that appear before the first recognized heading (e.g. older runs
 * recorded before this grouping existed) so nothing the agent produced is
 * ever silently dropped.
 */
function parseGroupedRequirements(text: string): ParsedRequirements {
  const groupItems: Record<string, string[]> = {};
  const criticalPath: string[] = [];
  const fallback: string[] = [];
  let current: string | "critical" | null = null;

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    if (isQualificationBoundary(line)) break;

    const categoryDef = CATEGORY_DEFS.find((def) => def.heading.test(line));
    if (categoryDef) {
      current = categoryDef.key;
      groupItems[categoryDef.key] ??= [];
      continue;
    }
    if (isCriticalPathHeading(line)) {
      current = "critical";
      continue;
    }

    const item = stripMarker(line);
    if (!item) continue;
    if (current === "critical") {
      criticalPath.push(item);
    } else if (current) {
      groupItems[current].push(item);
    } else {
      fallback.push(item);
    }
  }

  const groups: RequirementGroup[] = CATEGORY_DEFS.filter((def) => groupItems[def.key]?.length).map((def) => ({
    key: def.key,
    label: def.label,
    icon: def.icon,
    accent: def.accent,
    items: groupItems[def.key],
  }));
  if (fallback.length > 0) {
    groups.push({ key: "other", label: "Requirements", icon: "📄", accent: "#5c6572", items: fallback });
  }

  return { groups, criticalPath };
}

/**
 * Serializes the (possibly edited) grouped requirements back into the same
 * heading-based free text the architecture step expects as its
 * `approved_requirements` input.
 */
function serializeGroupedRequirements(parsed: ParsedRequirements): string {
  const lines: string[] = [];
  for (const group of parsed.groups) {
    if (group.items.length === 0) continue;
    lines.push(`${group.label}:`);
    for (const item of group.items) lines.push(`- ${item}`);
    lines.push("");
  }
  if (parsed.criticalPath.length > 0) {
    lines.push("Critical path:");
    for (const item of parsed.criticalPath) lines.push(`- ${item}`);
  }
  return lines.join("\n").trim();
}

const CLASSIFICATION_META: Record<string, { icon: string; accent: string }> = {
  requirement: { icon: "📋", accent: "#2f83e0" },
  goal: { icon: "🎯", accent: "#3fa66a" },
  constraint: { icon: "🚧", accent: "#d99a2b" },
  risk: { icon: "⚠️", accent: "#d1495b" },
  assumption: { icon: "🧩", accent: "#8a63d2" },
};

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
  const [policiesByRequest, setPoliciesByRequest] = useState<Record<string, string>>({});
  const [requirementOverrides, setRequirementOverrides] = useState<ParsedRequirements | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [showRawText, setShowRawText] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [resumingRequestId, setResumingRequestId] = useState<string | null>(null);

  // The analyst's discovered requirements are free text (the workflow run's
  // analyze-requirements step output), separate from the (currently
  // unpopulated - Shared Memory is never written to) structured records
  // above. Parsed into one collapsible category group per heading the
  // requirements-extraction-v1 prompt is instructed to emit - plus a
  // separate "Critical path" subset shown as its own Scope of Prototyping
  // section - so the user can scan, edit, remove, and add requirements at
  // the group level before approving the design-architecture gate.
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
  const parsedRequirements = useMemo(
    () => parseGroupedRequirements(analyzedRequirementsText),
    [analyzedRequirementsText],
  );
  const effectiveRequirements = requirementOverrides ?? parsedRequirements;
  const effectiveRequirementsDraft = useMemo(
    () => serializeGroupedRequirements(effectiveRequirements),
    [effectiveRequirements],
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

  const withOverrides = useCallback(
    (prev: ParsedRequirements | null): ParsedRequirements =>
      prev ?? {
        groups: parsedRequirements.groups.map((group) => ({ ...group, items: [...group.items] })),
        criticalPath: [...parsedRequirements.criticalPath],
      },
    [parsedRequirements],
  );

  const updateGroupItem = useCallback(
    (groupKey: string, index: number, value: string) => {
      setRequirementOverrides((prev) => {
        const base = withOverrides(prev);
        return {
          ...base,
          groups: base.groups.map((group) =>
            group.key === groupKey
              ? { ...group, items: group.items.map((item, i) => (i === index ? value : item)) }
              : group,
          ),
        };
      });
    },
    [withOverrides],
  );
  const removeGroupItem = useCallback(
    (groupKey: string, index: number) => {
      setRequirementOverrides((prev) => {
        const base = withOverrides(prev);
        return {
          ...base,
          groups: base.groups.map((group) =>
            group.key === groupKey ? { ...group, items: group.items.filter((_, i) => i !== index) } : group,
          ),
        };
      });
    },
    [withOverrides],
  );
  const addGroupItem = useCallback(
    (groupKey: string) => {
      setRequirementOverrides((prev) => {
        const base = withOverrides(prev);
        return {
          ...base,
          groups: base.groups.map((group) =>
            group.key === groupKey ? { ...group, items: [...group.items, ""] } : group,
          ),
        };
      });
    },
    [withOverrides],
  );

  const updateCriticalPathItem = useCallback(
    (index: number, value: string) => {
      setRequirementOverrides((prev) => {
        const base = withOverrides(prev);
        return { ...base, criticalPath: base.criticalPath.map((item, i) => (i === index ? value : item)) };
      });
    },
    [withOverrides],
  );
  const removeCriticalPathItem = useCallback(
    (index: number) => {
      setRequirementOverrides((prev) => {
        const base = withOverrides(prev);
        return { ...base, criticalPath: base.criticalPath.filter((_, i) => i !== index) };
      });
    },
    [withOverrides],
  );
  const addCriticalPathItem = useCallback(() => {
    setRequirementOverrides((prev) => {
      const base = withOverrides(prev);
      return { ...base, criticalPath: [...base.criticalPath, ""] };
    });
  }, [withOverrides]);

  const toggleGroupCollapsed = useCallback((groupKey: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.add(groupKey);
      return next;
    });
  }, []);


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
  const navigate = useNavigate();

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
        // Approving the requirement-discovery checkpoint kicks off
        // design-architecture in the same resume call above - jump the user
        // straight to Architecture Studio so the newly produced
        // recommendation is visible immediately, instead of leaving them on
        // this page wondering whether anything happened.
        if (subjectId === "design-architecture") {
          navigate("/architecture-studio");
        }
      } catch (err) {
        setResumeError((err as ApiError).message ?? "Failed to resume the workflow.");
      } finally {
        setResumingRequestId(null);
      }
    },
[sessionId, workflowRunId, policiesByRequest, effectiveRequirementsDraft, refresh, refreshApprovals, navigate],
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
        <div style={{ marginBottom: 8 }}>
          <Button appearance="subtle" size="small" onClick={() => setShowRawText((prev) => !prev)}>
            {showRawText ? "Hide raw analyst output" : "🔍 View raw analyst output"}
          </Button>
          {showRawText ? (
            <Textarea
              value={analyzedRequirementsText}
              readOnly
              rows={10}
              style={{ width: "100%", fontFamily: "monospace", fontSize: 12, marginTop: 8, opacity: 0.8 }}
            />
          ) : null}
        </div>
      ) : null}

      {effectiveRequirements.criticalPath.length > 0 ? (
        <SectionCard
          title={
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 18 }}>🎯</span>
              <span>Scope of Prototyping</span>
              <Text size={200} style={{ opacity: 0.6, fontWeight: 400 }}>
                (Critical Path)
              </Text>
            </span>
          }
          action={
            <Button appearance="secondary" size="small" onClick={addCriticalPathItem}>
              + Add item
            </Button>
          }
        >
          <Text size={200} style={{ display: "block", marginBottom: 12, opacity: 0.8 }}>
            These requirements are what the initial prototype will actually build first - everything
            else depends on them being in place.
          </Text>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {effectiveRequirements.criticalPath.map((item, index) => (
              <div
                key={index}
                className="genie-fade-in genie-req-item"
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "flex-start",
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid #d99a2b55",
                  backgroundColor: "rgba(217, 154, 43, 0.08)",
                }}
              >
                <span
                  style={{
                    flexShrink: 0,
                    width: 22,
                    height: 22,
                    marginTop: 4,
                    borderRadius: "50%",
                    backgroundImage: "linear-gradient(135deg, #d99a2b, #e0b354)",
                    color: "#1a1200",
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
                  onChange={(_, dataEv) => updateCriticalPathItem(index, dataEv.value)}
                  resize="vertical"
                  style={{ flex: 1 }}
                />
                <Button
                  appearance="subtle"
                  size="small"
                  shape="circular"
                  title="Remove this requirement"
                  aria-label="Remove this requirement"
                  onClick={() => removeCriticalPathItem(index)}
                >
                  ✕
                </Button>
              </div>
            ))}
          </div>
        </SectionCard>
      ) : null}

      {effectiveRequirements.groups.map((group) => {
        const collapsed = collapsedGroups.has(group.key);
        return (
          <SectionCard
            key={group.key}
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
                    backgroundColor: `${group.accent}22`,
                    border: `1px solid ${group.accent}66`,
                  }}
                >
                  {group.icon}
                </span>
                <span>{group.label}</span>
                <Badge shape="rounded" style={{ backgroundColor: `${group.accent}22`, color: group.accent }}>
                  {group.items.length}
                </Badge>
              </span>
            }
            action={
              <Button appearance="subtle" size="small" onClick={() => toggleGroupCollapsed(group.key)}>
                {collapsed ? "▸ Expand" : "▾ Collapse"}
              </Button>
            }
          >
            {collapsed ? null : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {group.items.length === 0 ? (
                  <Text size={200} style={{ opacity: 0.6 }}>
                    No items yet - add one below.
                  </Text>
                ) : (
                  group.items.map((item, index) => (
                    <div
                      key={index}
                      className="genie-fade-in genie-req-item"
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "flex-start",
                        padding: "8px 10px",
                        borderRadius: 8,
                        border: "1px solid #232a33",
                        backgroundColor: "#161c24",
                      }}
                    >
                      <span
                        style={{
                          flexShrink: 0,
                          width: 22,
                          height: 22,
                          marginTop: 4,
                          borderRadius: "50%",
                          backgroundColor: group.accent,
                          color: "#0b0f14",
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
                        onChange={(_, dataEv) => updateGroupItem(group.key, index, dataEv.value)}
                        resize="vertical"
                        style={{ flex: 1 }}
                      />
                      <Button
                        appearance="subtle"
                        size="small"
                        shape="circular"
                        title="Remove this requirement"
                        aria-label="Remove this requirement"
                        onClick={() => removeGroupItem(group.key, index)}
                      >
                        ✕
                      </Button>
                    </div>
                  ))
                )}
                <Button
                  appearance="secondary"
                  size="small"
                  onClick={() => addGroupItem(group.key)}
                  style={{ alignSelf: "flex-start" }}
                >
                  + Add item
                </Button>
              </div>
            )}
          </SectionCard>
        );
      })}

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
