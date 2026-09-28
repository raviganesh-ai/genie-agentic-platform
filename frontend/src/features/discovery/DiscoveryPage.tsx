import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Badge,
  Button,
  Dropdown,
  Input,
  MessageBar,
  Option,
  Radio,
  RadioGroup,
  Spinner,
  Switch,
  Text,
} from "@fluentui/react-components";
import {
  ArrowRight24Regular,
  Delete24Regular,
  DocumentAdd24Regular,
  DocumentPdf24Regular,
  Lightbulb24Regular,
} from "@fluentui/react-icons";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import "./DiscoveryPage.css";
import { ErrorState } from "@/components/ErrorState";
import { DOCUMENT_EVIDENCE_ACCEPT } from "@/config/evidenceFormats";
import { PageHeader } from "@/layouts/AppShell";
import { useUploadAction, useUploads } from "@/hooks/useUploads";
import { ApiError } from "@/services/httpClient";
import { discoveryApi } from "@/services/discoveryApi";
import { useSessionContext } from "@/state/SessionContext";
import type { SafeError } from "@/types/common";
import type {
  ArchitectureNode,
  DiscoveryCase,
  DiscoveryQaMode,
  ProposedSolution,
} from "@/types/discovery";
import type { UploadType } from "@/types/upload";
import { layoutArchitecture } from "./architectureLayout";
import { getAzureServiceIcon } from "./azureIconManifest";

const UPLOAD_TYPES: UploadType[] = ["transcript", "audio", "video", "supporting_document"];
const UPLOAD_TYPE_LABELS: Record<UploadType, string> = {
  transcript: "Transcript",
  audio: "Audio recording",
  video: "Video recording",
  supporting_document: "Supporting document",
};
const UPLOAD_TYPE_ACCEPT: Record<UploadType, string> = {
  transcript: DOCUMENT_EVIDENCE_ACCEPT,
  audio: "audio/*",
  video: "video/*",
  supporting_document: DOCUMENT_EVIDENCE_ACCEPT,
};

interface AzureNodeData {
  label: string;
  service: string;
  icon?: string;
}

interface PendingUpload {
  id: string;
  fileName: string;
  status: "queued" | "processing";
}

function AzureServiceNode({ data }: NodeProps<AzureNodeData>): JSX.Element {
  return (
    <div className="discovery-node" role="group" aria-label={`${data.service}: ${data.label}`}>
      <Handle type="target" position={Position.Left} />
      <div className="discovery-node-service">
        {data.icon
          ? <img src={data.icon} alt="" />
          : <span className="discovery-node-fallback">Az</span>}
        <strong title={data.service}>{data.service}</strong>
      </div>
      <span className="discovery-node-purpose" title={data.label}>{data.label}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const NODE_TYPES = { azureService: AzureServiceNode };

function ArchitectureDiagram({ solution }: { solution: ProposedSolution }): JSX.Element {
  const positions = new Map(
    layoutArchitecture(solution.architecture_nodes, solution.architecture_edges)
      .map((position) => [position.id, position]),
  );
  const nodes: Node<AzureNodeData>[] = solution.architecture_nodes.map((node: ArchitectureNode) => ({
    id: node.id,
    type: "azureService",
    position: positions.get(node.id) ?? { x: 0, y: 0 },
    data: {
      label: node.purpose,
      service: node.service_name,
      icon: getAzureServiceIcon(node.azure_icon_key),
    },
  }));
  const edges: Edge[] = solution.architecture_edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#58a6c7" },
    style: { stroke: "#58a6c7", strokeWidth: 1.6 },
    labelStyle: { fill: "#dce9f2", fontSize: 11, fontWeight: 600 },
    labelBgStyle: { fill: "#101820", fillOpacity: 0.94 },
    labelBgPadding: [7, 4],
    labelBgBorderRadius: 3,
  }));
  return (
    <div className="discovery-architecture-shell">
      <div className="discovery-architecture-header">
        <div>
          <Text weight="semibold">Azure service architecture</Text>
          <Text size={200} className="discovery-muted">Service flow and integration boundaries</Text>
        </div>
        <Badge appearance="outline">{nodes.length} Azure services</Badge>
      </div>
      <div
        className="discovery-architecture"
        role="img"
        aria-label={`${solution.name} Azure service architecture`}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.18, minZoom: 0.45, maxZoom: 1 }}
          minZoom={0.35}
          maxZoom={1.4}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#273746" gap={20} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}

function SolutionCard({
  solution,
  selected,
  disabled,
  onSelect,
}: {
  solution: ProposedSolution;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}): JSX.Element {
  const cost = solution.cost_estimate;
  const currencyFormatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: cost.currency_code,
    maximumFractionDigits: 2,
  });
  const formatCost = (amount: number | null) => (
    amount === null ? "Unavailable" : currencyFormatter.format(amount)
  );
  return (
    <article className={`discovery-card${selected ? " selected" : ""}`}>
      <div>
        <Badge appearance="outline">AI feasibility: {solution.ai_feasibility.replace("_", " ")}</Badge>
        <h3 style={{ marginTop: 10 }}>{solution.name}</h3>
        <Text className="discovery-muted">{solution.summary}</Text>
      </div>
      <ArchitectureDiagram solution={solution} />
      <div className="discovery-two-column">
        <div>
          <Text weight="semibold">Advantages</Text>
          <ul>{solution.pros.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
        <div>
          <Text weight="semibold">Tradeoffs</Text>
          <ul>{solution.cons.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      </div>
      <Text>{solution.ai_feasibility_rationale}</Text>
      {(solution.evidence_references ?? []).length > 0 ? (
        <div>
          <Text weight="semibold">Evidence</Text>
          <ul>{(solution.evidence_references ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      ) : null}
      <section className="discovery-cost" aria-label="Estimated Azure solution cost">
        <div className="discovery-cost-header">
          <div>
            <Text weight="semibold">Estimated Azure solution cost</Text>
            <Text size={200} className="discovery-muted">
              Based on {solution.pricing_queries.length} pricing input{solution.pricing_queries.length === 1 ? "" : "s"} from this architecture in {cost.region}.
            </Text>
          </div>
          <Badge appearance="outline">{cost.coverage} retail pricing coverage</Badge>
        </div>
        <div className="discovery-cost-totals">
          <div>
            <Text size={200} className="discovery-muted">Estimated monthly</Text>
            <Text size={500} weight="bold">{formatCost(cost.monthly_amount)}</Text>
          </div>
          <div>
            <Text size={200} className="discovery-muted">Estimated annual</Text>
            <Text size={500} weight="bold">{formatCost(cost.annual_amount)}</Text>
          </div>
        </div>
        {solution.pricing_queries.length > 0 ? (
          <div className="discovery-cost-services">
            {solution.pricing_queries.map((query, index) => (
              <div key={`${query.service_name}-${query.sku_name ?? "consumption"}-${index}`}>
                <div>
                  <Text weight="semibold">{query.service_name}</Text>
                  <Text size={200} className="discovery-muted">
                    {[query.sku_name ?? "Consumption pricing", query.meter_name, query.unit_of_measure]
                      .filter(Boolean)
                      .join(" · ")} · {query.units_per_month.toLocaleString()} billable {query.units_per_month === 1 ? "unit" : "units"}/month
                  </Text>
                </div>
                <Text size={200} className="discovery-muted">{query.assumption}</Text>
              </div>
            ))}
          </div>
        ) : null}
        <Text size={200} className="discovery-muted">
          Azure consumption estimate only; implementation, support, taxes, and negotiated discounts are excluded.
        </Text>
        <div className="discovery-cost-sources">
          {cost.source_urls.map((url, index) => (
            <a key={url} href={url} target="_blank" rel="noreferrer">
              Azure Retail Prices source {cost.source_urls.length > 1 ? index + 1 : ""}
            </a>
          ))}
        </div>
      </section>
      <Button appearance={selected ? "primary" : "secondary"} disabled={disabled} onClick={onSelect}>
        {selected ? "Selected" : "Select solution"}
      </Button>
    </article>
  );
}

export function DiscoveryPage(): JSX.Element {
  const navigate = useNavigate();
  const {
    sessionId,
    selectedModelDeploymentRef,
    setWorkflowRunId,
    setMissionStartedAt,
    setMissionError,
  } = useSessionContext();
  const [discoveryCase, setDiscoveryCase] = useState<DiscoveryCase | null>(null);
  const [loadingCase, setLoadingCase] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<SafeError | null>(null);
  const [uploadType, setUploadType] = useState<UploadType>("transcript");
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([]);
  const [selectedPersonaIds, setSelectedPersonaIds] = useState<string[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { data: uploads, loading: loadingUploads, refresh } = useUploads(sessionId);
  const {
    upload,
    remove: removeUpload,
    removingUploadIds,
    error: uploadError,
  } = useUploadAction(sessionId);
  const readyUploads = useMemo(
    () => uploads?.filter((item) => item.status === "completed") ?? [],
    [uploads],
  );

  useEffect(() => {
    let mounted = true;
    if (!sessionId) {
      setLoadingCase(false);
      return;
    }
    discoveryApi
      .get(sessionId)
      .then((value) => {
        if (mounted) setDiscoveryCase(value);
      })
      .catch((caught) => {
        if (!mounted) return;
        if (caught instanceof ApiError && caught.status === 404) {
          void discoveryApi
            .createOrResume(
              sessionId,
              [],
              selectedModelDeploymentRef ?? undefined,
              false,
            )
            .then((value) => {
              if (mounted) setDiscoveryCase(value);
            })
            .catch((createError) => {
              if (mounted) {
                setError(
                  createError instanceof ApiError
                    ? createError
                    : { message: "Unable to start Discovery." },
                );
              }
            });
        } else {
          setError(caught instanceof ApiError ? caught : { message: "Unable to load Discovery." });
        }
      })
      .finally(() => {
        if (mounted) setLoadingCase(false);
      });
    return () => {
      mounted = false;
    };
  }, [selectedModelDeploymentRef, sessionId]);

  useEffect(() => {
    if (!discoveryCase) return;
    setSelectedPersonaIds(
      discoveryCase.selected_persona_ids?.length
        ? discoveryCase.selected_persona_ids
        : discoveryCase.selected_persona_id
          ? [discoveryCase.selected_persona_id]
          : [],
    );
  }, [discoveryCase]);

  const perform = useCallback(
    async (label: string, operation: () => Promise<DiscoveryCase>) => {
      setBusy(label);
      setError(null);
      try {
        setDiscoveryCase(await operation());
      } catch (caught) {
        setError(caught instanceof ApiError ? caught : { message: `Unable to ${label}.` });
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const handleUpload = useCallback(async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (!files.length) return;
    const selectedUploads = files.map((file, index) => ({
      id: `${Date.now()}-${index}-${file.name}`,
      fileName: file.name,
      status: "queued" as const,
    }));
    setPendingUploads(selectedUploads);
    event.target.value = "";
    try {
      for (const [index, file] of files.entries()) {
        const pendingId = selectedUploads[index].id;
        setPendingUploads((current) => current.map((item) => (
          item.id === pendingId ? { ...item, status: "processing" } : item
        )));
        try {
          await upload(uploadType, file);
        } finally {
          await refresh();
          setPendingUploads((current) => current.filter((item) => item.id !== pendingId));
        }
      }
    } catch {
      return;
    } finally {
      setPendingUploads([]);
    }
  }, [refresh, upload, uploadType]);

  const analyze = useCallback(async () => {
    if (!sessionId || !readyUploads.length) return;
    await perform("analyze customer material", async () => {
      await discoveryApi.createOrResume(
        sessionId,
        readyUploads.map((item) => item.id),
        selectedModelDeploymentRef ?? undefined,
        discoveryCase?.save_enabled ?? false,
      );
      return discoveryApi.analyze(sessionId);
    });
  }, [discoveryCase?.save_enabled, perform, readyUploads, selectedModelDeploymentRef, sessionId]);

  const updateSavePreference = useCallback(
    async (enabled: boolean) => {
      if (!sessionId) return;
      await perform(
        enabled ? "save Discovery" : "stop saving Discovery",
        () => discoveryApi.setSavePreference(sessionId, enabled),
      );
    },
    [perform, sessionId],
  );

  const runDiscovery = useCallback(async () => {
    if (!sessionId || !selectedPersonaIds.length) return;
    await perform(
      "run Discovery",
      () => discoveryApi.selectPersonas(sessionId, selectedPersonaIds),
    );
  }, [perform, selectedPersonaIds, sessionId]);

  const handleRemoveUpload = useCallback(
    async (uploadId: string) => {
      if (!sessionId) return;
      try {
        await removeUpload(uploadId);
        const [, updatedCase] = await Promise.all([
          refresh(),
          discoveryApi.get(sessionId),
        ]);
        setDiscoveryCase(updatedCase);
      } catch {
        return;
      }
    },
    [refresh, removeUpload, sessionId],
  );

  const startPrototype = useCallback(async () => {
    if (!sessionId) return;
    setMissionStartedAt(Date.now());
    setMissionError(null);
    await perform("start prototype build", async () => {
      const updated = await discoveryApi.startPrototype(sessionId);
      if (updated.build_workflow_run_id) {
        setWorkflowRunId(updated.build_workflow_run_id);
        navigate("/workshop");
      }
      return updated;
    });
  }, [navigate, perform, sessionId, setMissionError, setMissionStartedAt, setWorkflowRunId]);

  const deleteDiscovery = useCallback(async () => {
    if (!sessionId) return;
    setBusy("delete Discovery");
    setError(null);
    try {
      await discoveryApi.delete(sessionId);
      navigate("/");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : { message: "Unable to delete Discovery." });
      setBusy(null);
    }
  }, [navigate, sessionId]);

  const exportDiscovery = useCallback(() => {
    const originalTitle = document.title;
    const exportedAt = new Date().toISOString().slice(0, 10);
    document.title = `Genie-Discovery-${sessionId}-${exportedAt}`;
    window.print();
    document.title = originalTitle;
  }, [sessionId]);

  if (!sessionId) {
    return (
      <div>
        <PageHeader title="Discovery" subtitle="No active session yet." />
        <Button appearance="primary" onClick={() => navigate("/")}>Start a session</Button>
      </div>
    );
  }

  const selectedPersonas = discoveryCase?.personas.filter((persona) =>
    (discoveryCase.selected_persona_ids?.length
      ? discoveryCase.selected_persona_ids
      : discoveryCase.selected_persona_id
        ? [discoveryCase.selected_persona_id]
        : []).includes(persona.id),
  ) ?? [];
  const selectedPersonaNames = selectedPersonas.map((persona) => persona.name).join(", ");
  const visibleQuestions = discoveryCase?.qa_mode === "interactive"
    ? discoveryCase.questions.filter((question) =>
        ["pending", "recommendation_offered"].includes(question.status),
      ).slice(0, 1)
    : discoveryCase?.questions ?? [];
  const totalQuestionCount = discoveryCase?.questions.length ?? 0;
  const answeredQuestionCount = discoveryCase?.questions.filter((question) =>
    ["answered", "recommended"].includes(question.status),
  ).length ?? 0;
  const progress = discoveryCase?.proposed_solutions.length
    ? 6
    : discoveryCase?.qa_mode || discoveryCase?.questions.length
      ? 5
      : discoveryCase?.gap_analysis
        ? 4
      : selectedPersonas.length
        ? 3
        : discoveryCase?.personas.length
          ? 2
          : 1;

  return (
    <div className="discovery-page genie-fade-in">
      <PageHeader
        title="Discovery"
        subtitle="Turn customer evidence into a persona-led, costed Azure solution and runnable prototype."
      />
      <div className="discovery-progress" aria-label={`Discovery progress, step ${progress} of 6`}>
        {[1, 2, 3, 4, 5, 6].map((step) => <span key={step} className={step <= progress ? "active" : ""} />)}
      </div>
      <div className="discovery-toolbar">
        <Text className="discovery-muted">
          {discoveryCase?.save_enabled
            ? `Revision ${discoveryCase.analysis_revision} · Saved ${new Date(discoveryCase.updated_at).toLocaleString()}`
            : "Not saved · kept only for this active session"}
        </Text>
        <div className="discovery-toolbar-actions">
          {discoveryCase ? (
            <Button
              appearance="secondary"
              icon={<DocumentPdf24Regular />}
              disabled={Boolean(busy)}
              onClick={exportDiscovery}
            >
              Export PDF
            </Button>
          ) : null}
          {discoveryCase ? (
            <Switch
              label="Save discovery"
              checked={discoveryCase.save_enabled}
              disabled={Boolean(busy) || discoveryCase.status === "build_started"}
              onChange={(_, data) => void updateSavePreference(Boolean(data.checked))}
            />
          ) : null}
          {discoveryCase ? (
            <Button
              appearance="subtle"
              icon={<Delete24Regular />}
              disabled={Boolean(busy) || discoveryCase.status === "build_started"}
              onClick={() => void deleteDiscovery()}
            >
              {discoveryCase.save_enabled ? "Delete Discovery" : "Discard Discovery"}
            </Button>
          ) : null}
        </div>
      </div>

      {error ? <ErrorState error={error} /> : null}
      {discoveryCase?.last_error ? <MessageBar intent="warning">{discoveryCase.last_error}</MessageBar> : null}
      {uploadError ? <ErrorState error={uploadError} /> : null}
      {busy || loadingCase ? <Spinner label={busy ? `${busy}...` : "Loading Discovery..."} /> : null}

      <section className="discovery-section" aria-labelledby="discovery-evidence">
        <div className="discovery-section-header">
          <div>
            <h2 id="discovery-evidence" className="discovery-section-heading">1. Customer evidence</h2>
            <Text className="discovery-muted">Upload the customer material Genie should analyze.</Text>
          </div>
        </div>
        <div className="discovery-upload-panel">
          <div className="discovery-upload-row">
            <div className="discovery-upload-type">
              <label htmlFor="discovery-upload-type">
                <Text size={200} weight="semibold">Material type</Text>
              </label>
              <Dropdown
                id="discovery-upload-type"
                value={UPLOAD_TYPE_LABELS[uploadType]}
                selectedOptions={[uploadType]}
                disabled={pendingUploads.length > 0}
                onOptionSelect={(_, data) => setUploadType(data.optionValue as UploadType)}
              >
                {UPLOAD_TYPES.map((type) => <Option key={type} value={type}>{UPLOAD_TYPE_LABELS[type]}</Option>)}
              </Dropdown>
            </div>
            <div className="discovery-upload-prompt">
              <div>
                <Text weight="semibold" style={{ display: "block" }}>Add customer material</Text>
                <Text className="discovery-muted" size={200}>Select one or more files. You can add more at any time.</Text>
              </div>
            </div>
            <Button
              appearance="primary"
              icon={<DocumentAdd24Regular />}
              disabled={pendingUploads.length > 0}
              onClick={() => fileInputRef.current?.click()}
            >
              {pendingUploads.length > 0
                ? `Processing ${pendingUploads.length} ${pendingUploads.length === 1 ? "file" : "files"}...`
                : "Choose files"}
            </Button>
            <input
              ref={fileInputRef}
              aria-label="Choose customer material files"
              type="file"
              accept={UPLOAD_TYPE_ACCEPT[uploadType]}
              multiple
              hidden
              onChange={(event) => void handleUpload(event)}
            />
          </div>
          <div aria-live="polite">
            {uploads?.length || pendingUploads.length ? (
              <ul className="discovery-upload-list" aria-label="Uploaded customer material">
                {pendingUploads.map((item) => (
                  <li key={item.id} aria-busy="true">
                    <div className="discovery-upload-file">
                      <Text>{item.fileName}</Text>
                      <Text className="discovery-upload-progress" size={200}>
                        {item.status === "processing"
                          ? "Uploading and analyzing content..."
                          : "Waiting to upload..."}
                      </Text>
                    </div>
                    <div className="discovery-upload-actions">
                      {item.status === "processing" ? <Spinner size="tiny" /> : null}
                      <Badge appearance="outline">
                        {item.status === "processing" ? "processing" : "queued"}
                      </Badge>
                    </div>
                  </li>
                ))}
                {(uploads ?? []).map((item) => (
                  <li key={item.id}>
                    <div className="discovery-upload-file">
                      <Text>{item.file_name}</Text>
                      {item.status === "failed" && item.detail ? (
                        <Text className="discovery-upload-error" size={200}>{item.detail}</Text>
                      ) : null}
                    </div>
                    <div className="discovery-upload-actions">
                      <Badge appearance="outline">{item.status}</Badge>
                      <Button
                        appearance="subtle"
                        icon={<Delete24Regular />}
                        aria-label={`Remove ${item.file_name}`}
                        title={`Remove ${item.file_name}`}
                        disabled={Boolean(busy) || removingUploadIds.has(item.id)}
                        onClick={() => void handleRemoveUpload(item.id)}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <Text className="discovery-muted" size={200}>
                {loadingUploads ? "Loading uploaded material..." : "No files uploaded yet."}
              </Text>
            )}
          </div>
          <div className="discovery-upload-next">
            <Text>
              {readyUploads.length} {readyUploads.length === 1 ? "source file" : "source files"} ready
            </Text>
            <Button appearance="secondary" disabled={Boolean(busy) || !readyUploads.length} onClick={() => void analyze()}>
              {discoveryCase?.analysis_revision ? "Analyze new revision" : "Find people"}
            </Button>
          </div>
        </div>
      </section>

      {discoveryCase?.personas.length ? (
        <section className="discovery-section" aria-labelledby="discovery-personas">
          <div className="discovery-section-header">
            <div>
              <h2 id="discovery-personas" className="discovery-section-heading">2. Select the persona of your choice</h2>
              <Text className="discovery-muted">Choose one or more people whose perspective should shape Discovery.</Text>
            </div>
          </div>
          <div className="discovery-persona-picker">
            <Dropdown
              aria-label="Select personas"
              placeholder="Select people"
              multiselect
              selectedOptions={selectedPersonaIds}
              value={selectedPersonaIds.length
                ? discoveryCase.personas
                    .filter((persona) => selectedPersonaIds.includes(persona.id))
                    .map((persona) => persona.name)
                    .join(", ")
                : ""}
              disabled={Boolean(busy) || discoveryCase.status !== "awaiting_persona_selection"}
              onOptionSelect={(_, data) => setSelectedPersonaIds(data.selectedOptions)}
            >
              {discoveryCase.personas.map((persona) => (
                <Option key={persona.id} value={persona.id}>{persona.name}</Option>
              ))}
            </Dropdown>
            <Button
              appearance="primary"
              disabled={
                Boolean(busy)
                || !selectedPersonaIds.length
                || discoveryCase.status !== "awaiting_persona_selection"
              }
              onClick={() => void runDiscovery()}
            >
              Run Discovery
            </Button>
          </div>
        </section>
      ) : null}

      {discoveryCase && selectedPersonas.length > 0 && discoveryCase.gap_analysis ? (
        <>
        <section className="discovery-section" aria-labelledby="discovery-pain-points">
          <div>
            <h2 id="discovery-pain-points" className="discovery-section-heading">3. Pain points</h2>
            <Text className="discovery-muted">Based only on evidence attributable to {selectedPersonaNames}.</Text>
          </div>
          <div className="discovery-insight-list">
            {(discoveryCase.insight_sections?.length
              ? discoveryCase.insight_sections
              : [
                  {
                    title: "Key findings",
                    summary: discoveryCase.deep_dive_findings.join(" "),
                    evidence_references: discoveryCase.gap_analysis.evidence_references,
                  },
                  {
                    title: "Risks and contradictions",
                    summary: [
                      ...(discoveryCase.gap_analysis.risks ?? []),
                      ...(discoveryCase.gap_analysis.contradictions ?? []),
                    ].join(" "),
                    evidence_references: [],
                  },
                  {
                    title: "What remains unknown",
                    summary: discoveryCase.gap_analysis.information_gaps.join(" "),
                    evidence_references: [],
                  },
                ].filter((section) => section.summary)
            ).map((section) => (
              <article key={section.title} className="discovery-insight">
                <h3>{section.title}</h3>
                <div>
                  <Text>{section.summary}</Text>
                  {section.evidence_references.length ? (
                    <Text className="discovery-insight-evidence">
                      Evidence: {section.evidence_references.join(" · ")}
                    </Text>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
          <Text className="discovery-muted">
            Analysis confidence: {Math.round(discoveryCase.gap_analysis.confidence_score * 100)}%
          </Text>
        </section>
        <section className="discovery-section" aria-labelledby="discovery-gaps-assumptions">
          <div>
            <h2 id="discovery-gaps-assumptions" className="discovery-section-heading">4. Gaps and assumptions</h2>
            <Text className="discovery-muted">What remains unresolved and what still requires customer validation.</Text>
          </div>
          <div className="discovery-insight-list">
            <article className="discovery-insight">
              <h3>Gaps</h3>
              <Text>
                {discoveryCase.gap_summary
                  || discoveryCase.gap_analysis.information_gaps.join(" ")
                  || "No material information gaps were identified."}
              </Text>
            </article>
            <article className="discovery-insight">
              <h3>Assumptions</h3>
              <Text>
                {discoveryCase.assumption_summary
                  || discoveryCase.gap_analysis.assumptions.join(" ")
                  || "No material assumptions were identified."}
              </Text>
            </article>
          </div>
          {!discoveryCase.qa_mode && discoveryCase.questions.length ? (
            <div className="discovery-choice-row">
              <Text weight="semibold">How should Genie ask?</Text>
              {(["interactive", "batch"] as DiscoveryQaMode[]).map((mode) => (
                <Button key={mode} disabled={Boolean(busy)} onClick={() => void perform("start Q&A", () => discoveryApi.setQaMode(sessionId, mode))}>
                  {mode === "interactive" ? "One at a time" : "Show all questions"}
                </Button>
              ))}
            </div>
          ) : !discoveryCase.questions.length
            && discoveryCase.status === "ready_for_solutions"
            && !discoveryCase.proposed_solutions.length ? (
              <div className="discovery-actions">
                <MessageBar>No additional clarification is needed for this evidence.</MessageBar>
                <Button
                  appearance="primary"
                  icon={<ArrowRight24Regular />}
                  disabled={Boolean(busy)}
                  onClick={() => void perform("generate solutions", () => discoveryApi.generateSolutions(sessionId))}
                >
                  Generate probable solutions
                </Button>
              </div>
          ) : null}
        </section>
        </>
      ) : null}

      {discoveryCase?.qa_mode ? (
        <section className="discovery-section" aria-labelledby="discovery-questions">
          <div>
            <h2 id="discovery-questions" className="discovery-section-heading">5. Clarify what matters</h2>
            <Text className="discovery-muted" style={{ display: "block" }}>
              {totalQuestionCount} {totalQuestionCount === 1 ? "question" : "questions"} · {answeredQuestionCount} answered
            </Text>
            <Text className="discovery-muted">Every answer is saved. Leave and return without losing the conversation.</Text>
          </div>
          {visibleQuestions.map((question) => (
            <div className="discovery-question" key={question.id}>
              <div>
                <h3>{question.text}</h3>
                <Text className="discovery-muted">{question.category}</Text>
              </div>
              <Badge appearance="outline">{question.status.replace(/_/g, " ")}</Badge>
              {question.status === "pending" ? (
                <div className="discovery-question-answer">
                  {question.suggested_answers?.length ? (
                    <RadioGroup
                      aria-label={`Suggested answers: ${question.text}`}
                      value={question.suggested_answers.includes(answers[question.id] ?? "")
                        ? answers[question.id]
                        : ""}
                      onChange={(_, data) => setAnswers((current) => ({
                        ...current,
                        [question.id]: data.value,
                      }))}
                    >
                      {question.suggested_answers.map((answer) => (
                        <Radio key={answer} value={answer} label={answer} />
                      ))}
                    </RadioGroup>
                  ) : null}
                  <div className="discovery-custom-answer">
                    <Input
                      aria-label={`Your answer: ${question.text}`}
                      placeholder="Type a different answer"
                      value={answers[question.id] ?? ""}
                      onChange={(_, data) => setAnswers((current) => ({ ...current, [question.id]: data.value }))}
                    />
                    <Button
                      appearance="primary"
                      disabled={Boolean(busy) || !(answers[question.id] ?? "").trim()}
                      onClick={() => void perform("save answer", () => discoveryApi.answerQuestion(sessionId, question.id, answers[question.id]))}
                    >
                      Save answer
                    </Button>
                    <Button disabled={Boolean(busy)} onClick={() => void perform("skip question", () => discoveryApi.answerQuestion(sessionId, question.id, null))}>
                      Skip
                    </Button>
                  </div>
                </div>
              ) : null}
              {question.status === "recommendation_offered" ? (
                <div className="discovery-recommendation">
                  <Text weight="semibold">Would you like Genie to propose a Microsoft/Azure best-practice answer?</Text>
                  <div className="discovery-actions" style={{ marginTop: 10 }}>
                    <Button icon={<Lightbulb24Regular />} appearance="primary" disabled={Boolean(busy)} onClick={() => void perform("prepare recommendation", () => discoveryApi.respondToRecommendation(sessionId, question.id, true))}>Yes, recommend</Button>
                    <Button disabled={Boolean(busy)} onClick={() => void perform("decline recommendation", () => discoveryApi.respondToRecommendation(sessionId, question.id, false))}>No, leave unanswered</Button>
                  </div>
                </div>
              ) : null}
              {question.recommendation ? <MessageBar className="discovery-recommendation">{question.recommendation}</MessageBar> : null}
              {question.answer ? <Text>{question.answer}</Text> : null}
            </div>
          ))}
          {discoveryCase.status === "ready_for_solutions" ? (
            <div className="discovery-actions">
              <MessageBar>Discovery is saved. You can stop here and return later, or compare probable solutions now.</MessageBar>
              <Button appearance="primary" icon={<ArrowRight24Regular />} disabled={Boolean(busy)} onClick={() => void perform("generate solutions", () => discoveryApi.generateSolutions(sessionId))}>Generate probable solutions</Button>
            </div>
          ) : null}
        </section>
      ) : null}

      {discoveryCase?.proposed_solutions.length ? (
        <section className="discovery-section" aria-labelledby="discovery-solutions">
          <div className="discovery-section-header">
            <div>
              <h2 id="discovery-solutions" className="discovery-section-heading">6. Probable solutions</h2>
              <Text className="discovery-muted">Compare architecture, tradeoffs, AI feasibility, and verified Azure price coverage.</Text>
            </div>
            {discoveryCase.proposed_solutions.some(
              (solution) => solution.cost_estimate.coverage !== "complete",
            ) ? (
              <Button
                appearance="secondary"
                disabled={Boolean(busy)}
                onClick={() => void perform(
                  "refresh Azure pricing",
                  () => discoveryApi.refreshSolutionPricing(sessionId),
                )}
              >
                Refresh Azure pricing
              </Button>
            ) : null}
          </div>
          <div className="discovery-solution-grid">
            {discoveryCase.proposed_solutions.map((solution) => (
              <SolutionCard
                key={solution.id}
                solution={solution}
                selected={solution.id === discoveryCase.selected_solution_id}
                disabled={Boolean(busy)}
                onSelect={() => void perform("select solution", () => discoveryApi.selectSolution(sessionId, solution.id))}
              />
            ))}
          </div>
          {discoveryCase.status === "ready_to_prototype" ? (
            <div className="discovery-actions">
              <Button appearance="primary" icon={<ArrowRight24Regular />} disabled={Boolean(busy)} onClick={() => void startPrototype()}>Build selected solution</Button>
              <Text className="discovery-muted">Requirements and Architecture are already approved by this Discovery; Genie proceeds directly to Build.</Text>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}