import { useCallback, useEffect, useMemo, useState } from "react";
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
import { workflowApi } from "@/services/workflowApi";
import { getTraceId } from "@/state/traceRegistry";
import type { WorkflowStepInput } from "@/types/workflow";
import { ApiError } from "@/services/httpClient";
import type { SafeError } from "@/types/common";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { LiveWorkflowPulse } from "@/components/LiveWorkflowPulse";
import { AgentActivityAnimation } from "@/components/AgentActivityAnimation";
import { useWorkflowEventStream } from "@/hooks/useWorkflowEventStream";

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
 * includes a "Critical path:" heading line containing the implementation
 * order. It never narrows the approved prototype scope.
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

const REQUIREMENT_ID_PATTERN = /\bREQ-\d{3,}\b/i;
const REQUIREMENT_GROUP_KEYS = new Set([
  "must_have",
  "nice_to_have",
  "functional",
  "non_functional",
  "other",
]);

function requirementId(item: string): string | null {
  return item.match(REQUIREMENT_ID_PATTERN)?.[0].toUpperCase() ?? null;
}

function preserveRequirementId(current: string, next: string): string {
  const currentId = requirementId(current);
  if (!currentId || requirementId(next)) return next;
  return `[${currentId}] ${next}`;
}

function nextRequirementItem(parsed: ParsedRequirements): string {
  const ids = [
    ...parsed.groups.flatMap((group) => group.items),
    ...parsed.criticalPath,
  ]
    .map(requirementId)
    .filter((id): id is string => id !== null)
    .map((id) => Number(id.slice(4)))
    .filter(Number.isFinite);
  const nextNumber = Math.max(0, ...ids) + 1;
  return `[REQ-${String(nextNumber).padStart(3, "0")}] `;
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
    key: "must_have",
    heading: /^must-?have functional requirements\s*:?$/i,
    icon: "⚙️",
    accent: "#2f83e0",
    label: "Must-Have Functional Requirements",
  },
  {
    key: "nice_to_have",
    heading: /^nice-?to-?have functional requirements\s*:?$/i,
    icon: "✨",
    accent: "#17a2b8",
    label: "Nice-to-Have Functional Requirements",
  },
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
 * emit, plus the "Critical path:" ordering kept separate so it can be shown
 * as its own implementation-order callout instead of buried inside a
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

export function RequirementDiscoveryPage(): JSX.Element {
  const { sessionId, workflowRunId, missionStartedAt, missionError, setMissionError } = useSessionContext();

  const navigate = useNavigate();
  const { data, loading, error, refresh } = useRequirements(sessionId, workflowRunId, POLL_MS);
  const { data: qualification } = useRequirementsQualification(sessionId, workflowRunId, POLL_MS);
  const [requirementOverrides, setRequirementOverrides] = useState<ParsedRequirements | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [showRawText, setShowRawText] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const [proceeding, setProceeding] = useState(false);
  const [rerunningRequirements, setRerunningRequirements] = useState(false);
  const [rerunRequirementsError, setRerunRequirementsError] = useState<string | null>(null);
  // Defaults to a clean, read-only list so reviewing requirements doesn't
  // look like a wall of form fields - the always-editable boxes/Textareas
  // below are only shown once the user opts into "Edit Requirements".
  const [editMode, setEditMode] = useState(false);

  // The analyst's discovered requirements are free text (the workflow run's
  // analyze-requirements step output), separate from the (currently
  // unpopulated - Shared Memory is never written to) structured records
  // above. Parsed into one collapsible category group per heading the
  // requirements-extraction-v1 prompt is instructed to emit - plus a
  // separate "Critical path" ordering shown as its own implementation-order
  // section - so the user can scan, edit, remove, and add requirements at
  // the group level before approving the design-architecture gate.
  const runFetcher = useCallback(
    () =>
      sessionId && workflowRunId
        ? workflowApi.getRun(sessionId, workflowRunId)
        : Promise.reject(new Error("No active workflow run")),
    [sessionId, workflowRunId],
  );
  const { data: run, refresh: refreshRun } = useAsyncResource(runFetcher, [sessionId, workflowRunId], {
    enabled: Boolean(sessionId && workflowRunId),
  });
  const { events: liveEvents, connected: liveConnected } = useWorkflowEventStream(sessionId);
  const lastLiveEvent = liveEvents[liveEvents.length - 1] ?? null;
  useEffect(() => {
    if (lastLiveEvent?.event_type === "step_completed" || lastLiveEvent?.event_type === "step_failed") {
      void refresh();
      void refreshRun();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastLiveEvent]);
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

  // Live per-category counts driving the hero banner's stat pills below -
  // derived from the same parsed/edited groups rendered on this page
  // (Shared Memory's classification records are never populated for this
  // step, so counting those always showed a dimmed row of zeros).
  const groupCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const group of effectiveRequirements.groups) counts[group.key] = group.items.length;
    return counts;
  }, [effectiveRequirements]);

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
              ? {
                  ...group,
                  items: group.items.map((item, i) =>
                    i === index && REQUIREMENT_GROUP_KEYS.has(groupKey)
                      ? preserveRequirementId(item, value)
                      : i === index
                        ? value
                        : item,
                  ),
                }
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
        const group = base.groups.find((candidate) => candidate.key === groupKey);
        const removedId = group ? requirementId(group.items[index] ?? "") : null;
        return {
          ...base,
          groups: base.groups.map((group) =>
            group.key === groupKey ? { ...group, items: group.items.filter((_, i) => i !== index) } : group,
          ),
          criticalPath: removedId
            ? base.criticalPath.filter((item) => requirementId(item) !== removedId)
            : base.criticalPath,
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
            group.key === groupKey
              ? {
                  ...group,
                  items: [
                    ...group.items,
                    REQUIREMENT_GROUP_KEYS.has(groupKey) ? nextRequirementItem(base) : "",
                  ],
                }
              : group,
          ),
        };
      });
    },
    [withOverrides],
  );
  // Adds an approved requirement to implementation order without removing
  // it from its category; Critical Path is sequencing, never scope reduction.
  const moveGroupItemToCriticalPath = useCallback(
    (groupKey: string, index: number) => {
      setRequirementOverrides((prev) => {
        const base = withOverrides(prev);
        const group = base.groups.find((candidate) => candidate.key === groupKey);
        const item = group?.items[index];
        if (item === undefined) return base;
        const itemId = requirementId(item);
        if (
          itemId &&
          base.criticalPath.some((candidate) => requirementId(candidate) === itemId)
        ) {
          return base;
        }
        return {
          groups: base.groups,
          criticalPath: [...base.criticalPath, item],
        };
      });
    },
    [withOverrides],
  );

  const updateCriticalPathItem = useCallback(
    (index: number, value: string) => {
      setRequirementOverrides((prev) => {
        const base = withOverrides(prev);
        return {
          ...base,
          criticalPath: base.criticalPath.map((item, i) =>
            i === index ? preserveRequirementId(item, value) : item,
          ),
        };
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
  const toggleGroupCollapsed = useCallback((groupKey: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.add(groupKey);
      return next;
    });
  }, []);


  // Once analyze-requirements has produced its output, design-architecture
  // is this workflow's next `requires_human_proceed` step (config/workflows/
  // registry.yaml) - it only runs once a resume call's own step_inputs
  // explicitly names it, which is exactly what clicking Proceed below does.
  // There is no separate ApprovalRequest to create/decide here.
  const proceedToArchitecture = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    setResumeError(null);
    setProceeding(true);
    try {
      const traceId = getTraceId(workflowRunId) ?? undefined;
      const stepInputs: Record<string, WorkflowStepInput> = {
        "design-architecture": {
          step_id: "design-architecture",
          variables: { approved_requirements: effectiveRequirementsDraft },
        },
      };
      // Kicks off design-architecture (a real Architecture Designer agent
      // call, which can take a while) in the resume call below - jump
      // straight to Architecture Studio so its agent-activity animation is
      // visible right away, instead of awaiting the whole step here first
      // and arriving with the work already done (and the animation never
      // getting a chance to show).
      setMissionError(null);
      navigate("/architecture-studio");
      workflowApi.resumeRun(sessionId, workflowRunId, traceId, stepInputs).catch((err) => {
        const safe: SafeError =
          err instanceof ApiError ? err : { message: "Failed to resume the workflow." };
        setMissionError(safe);
      });
    } finally {
      setProceeding(false);
    }
  }, [sessionId, workflowRunId, effectiveRequirementsDraft, navigate, setMissionError]);

  const handleRerunRequirementsStage = useCallback(async () => {
    if (!sessionId || !workflowRunId) return;
    setRerunningRequirements(true);
    setRerunRequirementsError(null);
    try {
      const traceId = getTraceId(workflowRunId) ?? undefined;
      await workflowApi.resumeRun(sessionId, workflowRunId, traceId, {
        "analyze-requirements": {
          step_id: "analyze-requirements",
          variables: {},
        },
      });
      await Promise.all([refresh(), refreshRun()]);
    } catch (err) {
      setRerunRequirementsError(
        (err as ApiError).message ?? "Failed to re-run Requirement Discovery.",
      );
    } finally {
      setRerunningRequirements(false);
    }
  }, [sessionId, workflowRunId, refresh, refreshRun]);

  if (!workflowRunId) {
    // A mission was just kicked off from Upload, which navigates here
    // immediately (ahead of the workflow run finishing) so the user sees
    // the Requirements Analyst agent working in real time rather than a
    // static page - the run just hasn't minted a workflow_run_id to poll
    // yet. Show the same live agent activity animation the rest of this
    // page shows while waiting on analyzedRequirementsText.
    if (missionError) {
      return (
        <div className="genie-fade-in" style={{ maxWidth: 560, margin: "10vh auto" }}>
          <ErrorState
            error={missionError}
            onRetry={() => {
              setMissionError(null);
              navigate("/upload");
            }}
          />
        </div>
      );
    }
    if (missionStartedAt) {
      return (
        <div className="genie-fade-in" style={{ maxWidth: 720, margin: "10vh auto" }}>
          <AgentActivityAnimation
            label="Genie is working with the Requirements Analyst agent to extract your requirements..."
            events={liveEvents}
          />
        </div>
      );
    }
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
            Goals, requirements, constraints, risks, and assumptions extracted by the Requirements
            Analyst agent - reviewed and refined here before the architecture gate opens.
          </Text>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {effectiveRequirements.criticalPath.length > 0 ? (
            <div
              title="Implementation Order"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 12px",
                borderRadius: 999,
                border: "1px solid #d99a2b55",
                backgroundColor: "#d99a2b1a",
              }}
            >
              <span style={{ fontSize: 15 }}>🎯</span>
              <Text size={200} weight="semibold" style={{ color: "#d99a2b" }}>
                {effectiveRequirements.criticalPath.length}
              </Text>
              <Text size={100} style={{ opacity: 0.7 }}>
                Implementation Order
              </Text>
            </div>
          ) : null}
          {CATEGORY_DEFS.map((def) => {
            const count = groupCounts[def.key] ?? 0;
            return (
              <div
                key={def.key}
                title={def.label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 12px",
                  borderRadius: 999,
                  border: `1px solid ${def.accent}55`,
                  backgroundColor: `${def.accent}1a`,
                  opacity: count > 0 ? 1 : 0.45,
                }}
              >
                <span style={{ fontSize: 15 }}>{def.icon}</span>
                <Text size={200} weight="semibold" style={{ color: def.accent }}>
                  {count}
                </Text>
                <Text size={100} style={{ opacity: 0.7 }}>
                  {def.label}
                </Text>
              </div>
            );
          })}
        </div>
      </div>
      <LiveWorkflowPulse connected={liveConnected} events={liveEvents} />
      {analyzedRequirementsText ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <Button
            size="small"
            disabled={rerunningRequirements}
            onClick={() => void handleRerunRequirementsStage()}
          >
            {rerunningRequirements ? "Re-running Requirement Discovery..." : "Re-run Requirement Discovery"}
          </Button>
          {rerunRequirementsError ? (
            <Text size={200} style={{ color: "#d1495b" }}>
              {rerunRequirementsError}
            </Text>
          ) : null}
        </div>
      ) : null}
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

      {!analyzedRequirementsText && missionError ? (
        <ErrorState
          error={missionError}
          onRetry={() => {
            setMissionError(null);
            navigate("/upload");
          }}
        />
      ) : null}

      {!analyzedRequirementsText && !missionError ? (
        <AgentActivityAnimation
          label="Genie is working with the Requirements Analyst agent to extract your requirements..."
          events={liveEvents}
        />
      ) : null}

      {analyzedRequirementsText ? (
        <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Button appearance="subtle" size="small" onClick={() => setShowRawText((prev) => !prev)}>
            {showRawText ? "Hide raw analyst output" : "🔍 View raw analyst output"}
          </Button>
          <Button
            appearance={editMode ? "primary" : "outline"}
            size="small"
            onClick={() => setEditMode((prev) => !prev)}
          >
            {editMode ? "✓ Done Editing" : "✏️ Edit Requirements"}
          </Button>
        </div>
      ) : null}
      {analyzedRequirementsText && showRawText ? (
        <Textarea
          value={analyzedRequirementsText}
          readOnly
          rows={10}
          style={{ width: "100%", fontFamily: "monospace", fontSize: 12, marginBottom: 16, opacity: 0.8 }}
        />
      ) : null}

      {effectiveRequirements.criticalPath.length > 0 ? (
        <SectionCard
          title={
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 18 }}>🎯</span>
              <span>Implementation Order</span>
              <Text size={200} style={{ opacity: 0.6, fontWeight: 400 }}>
                (Critical Path)
              </Text>
            </span>
          }
        >
          <Text size={200} style={{ display: "block", marginBottom: 12, opacity: 0.8 }}>
            This orders implementation only. Every approved requirement below remains in prototype scope.
          </Text>
          {editMode ? (
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
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {effectiveRequirements.criticalPath.map((item, index) => (
                <div key={index} className="genie-fade-in" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <span
                    style={{
                      flexShrink: 0,
                      width: 20,
                      height: 20,
                      marginTop: 2,
                      borderRadius: "50%",
                      backgroundImage: "linear-gradient(135deg, #d99a2b, #e0b354)",
                      color: "#1a1200",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 10,
                      fontWeight: 700,
                    }}
                  >
                    {index + 1}
                  </span>
                  <Text size={300} style={{ lineHeight: 1.5 }}>
                    {item}
                  </Text>
                </div>
              ))}
            </div>
          )}
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
              <div style={{ display: "flex", flexDirection: "column", gap: editMode ? 8 : 6 }}>
                {group.items.length === 0 ? (
                  <Text size={200} style={{ opacity: 0.6 }}>
                    {editMode ? "No items yet - add one below." : "No items yet."}
                  </Text>
                ) : editMode ? (
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
                        title="Add to Implementation Order"
                        aria-label="Add to Implementation Order"
                        onClick={() => moveGroupItemToCriticalPath(group.key, index)}
                      >
                        Add to Order
                      </Button>
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
                ) : (
                  group.items.map((item, index) => (
                    <div key={index} className="genie-fade-in" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                      <span
                        style={{
                          flexShrink: 0,
                          width: 20,
                          height: 20,
                          marginTop: 2,
                          borderRadius: "50%",
                          backgroundColor: group.accent,
                          color: "#0b0f14",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: 10,
                          fontWeight: 700,
                        }}
                      >
                        {index + 1}
                      </span>
                      <Text size={300} style={{ lineHeight: 1.5 }}>
                        {item}
                      </Text>
                    </div>
                  ))
                )}
                {editMode ? (
                  <Button
                    appearance="secondary"
                    size="small"
                    onClick={() => addGroupItem(group.key)}
                    style={{ alignSelf: "flex-start" }}
                  >
                    + Add item
                  </Button>
                ) : null}
              </div>
            )}
          </SectionCard>
        );
      })}

      <div style={analyzedRequirementsText ? { position: "sticky", bottom: 12, zIndex: 5 } : undefined}>
      <SectionCard title="✅ Ready to proceed?">
        {resumeError ? (
          <MessageBar intent="error" layout="multiline" style={{ marginBottom: 12 }}>
            <MessageBarBody>
              <MessageBarTitle>Failed to resume the workflow</MessageBarTitle>
              {resumeError}
            </MessageBarBody>
          </MessageBar>
        ) : null}
        {analyzedRequirementsText ? (
          <>
            <Text size={300} style={{ display: "block", marginBottom: 8, opacity: 0.8 }}>
              Review/edit the discovered requirements above, then proceed to have the
              Architecture Designer propose this mission's UI + multi-agent design.
            </Text>
            <Button
              appearance="primary"
              disabled={proceeding}
              onClick={() => void proceedToArchitecture()}
            >
              {proceeding ? "Proceeding..." : "Proceed to Architecture"}
            </Button>
          </>
        ) : (
          <Text size={300} style={{ opacity: 0.7 }}>
            Waiting for requirement discovery to finish...
          </Text>
        )}
      </SectionCard>
      </div>
    </div>
  );
}

