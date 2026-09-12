import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Badge,
  Button,
  Dropdown,
  Input,
  MessageBar,
  Option,
  Spinner,
  Text,
} from "@fluentui/react-components";
import {
  ArrowRight24Regular,
  Delete24Regular,
  DocumentAdd24Regular,
  Lightbulb24Regular,
} from "@fluentui/react-icons";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import "./DiscoveryPage.css";
import { ErrorState } from "@/components/ErrorState";
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
import { getAzureServiceIcon } from "./azureIconManifest";

const UPLOAD_TYPES: UploadType[] = ["transcript", "audio", "video", "supporting_document"];

interface AzureNodeData {
  label: string;
  service: string;
  icon?: string;
}

function AzureServiceNode({ data }: NodeProps<AzureNodeData>): JSX.Element {
  return (
    <div className="discovery-node">
      <Handle type="target" position={Position.Left} />
      {data.icon ? <img src={data.icon} alt="" /> : <span>{data.service.slice(0, 2)}</span>}
      <div>
        <strong>{data.label}</strong>
        <span className="discovery-muted">{data.service}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const NODE_TYPES = { azureService: AzureServiceNode };

function ArchitectureDiagram({ solution }: { solution: ProposedSolution }): JSX.Element {
  const nodes: Node<AzureNodeData>[] = solution.architecture_nodes.map((node: ArchitectureNode) => ({
    id: node.id,
    type: "azureService",
    position: { x: node.x, y: node.y },
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
    animated: true,
    style: { stroke: "#5a8fb8" },
    labelStyle: { fill: "#d7e3ed", fontSize: 10 },
  }));
  return (
    <div className="discovery-architecture">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#293744" gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
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
      <div className="discovery-cost">
        <Text weight="semibold">Estimated Azure run rate</Text>
        <Text weight="bold">
          {cost.monthly_amount === null
            ? "Unavailable"
            : new Intl.NumberFormat("en-US", { style: "currency", currency: cost.currency_code }).format(cost.monthly_amount) + "/month"}
        </Text>
        <Text className="discovery-muted">Retail Prices coverage</Text>
        <Badge appearance="outline">{cost.coverage}</Badge>
      </div>
      {cost.assumptions.length > 0 ? (
        <Text size={200} className="discovery-muted">{cost.assumptions.join(" · ")}</Text>
      ) : null}
      {cost.source_urls.map((url) => (
        <a key={url} href={url} target="_blank" rel="noreferrer">Azure Retail Prices source</a>
      ))}
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
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { data: uploads, loading: loadingUploads, refresh } = useUploads(sessionId);
  const { upload, uploading, error: uploadError } = useUploadAction(sessionId);

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
    const file = event.target.files?.[0];
    if (!file) return;
    await upload(uploadType, file);
    refresh();
    event.target.value = "";
  }, [refresh, upload, uploadType]);

  const analyze = useCallback(async () => {
    if (!sessionId || !uploads?.length) return;
    await perform("analyze customer material", async () => {
      await discoveryApi.createOrResume(
        sessionId,
        uploads.map((item) => item.id),
        selectedModelDeploymentRef ?? undefined,
      );
      return discoveryApi.analyze(sessionId);
    });
  }, [perform, selectedModelDeploymentRef, sessionId, uploads]);

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

  if (!sessionId) {
    return (
      <div>
        <PageHeader title="Discovery" subtitle="No active session yet." />
        <Button appearance="primary" onClick={() => navigate("/")}>Start a session</Button>
      </div>
    );
  }

  const selectedPersona = discoveryCase?.personas.find(
    (persona) => persona.id === discoveryCase.selected_persona_id,
  );
  const visibleQuestions = discoveryCase?.qa_mode === "interactive"
    ? discoveryCase.questions.filter((question) =>
        ["pending", "recommendation_offered"].includes(question.status),
      ).slice(0, 1)
    : discoveryCase?.questions ?? [];
  const progress = discoveryCase?.proposed_solutions.length
    ? 5
    : discoveryCase?.questions.length
      ? 4
      : discoveryCase?.selected_persona_id
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
      <div className="discovery-progress" aria-label={`Discovery progress, step ${progress} of 5`}>
        {[1, 2, 3, 4, 5].map((step) => <span key={step} className={step <= progress ? "active" : ""} />)}
      </div>
      <div className="discovery-toolbar">
        <Text className="discovery-muted">
          {discoveryCase ? `Revision ${discoveryCase.analysis_revision} · Saved ${new Date(discoveryCase.updated_at).toLocaleString()}` : "Not analyzed yet"}
        </Text>
        {discoveryCase ? (
          <Button
            appearance="subtle"
            icon={<Delete24Regular />}
            disabled={Boolean(busy) || discoveryCase.status === "build_started"}
            onClick={() => void deleteDiscovery()}
          >
            Delete Discovery
          </Button>
        ) : null}
      </div>

      {error ? <ErrorState error={error} /> : null}
      {discoveryCase?.last_error ? <MessageBar intent="warning">{discoveryCase.last_error}</MessageBar> : null}
      {uploadError ? <ErrorState error={uploadError} /> : null}
      {busy || loadingCase ? <Spinner label={busy ? `${busy}...` : "Loading Discovery..."} /> : null}

      <section className="discovery-section" aria-labelledby="discovery-evidence">
        <div className="discovery-section-header">
          <div>
            <h2 id="discovery-evidence" className="discovery-section-heading">1. Customer evidence</h2>
            <Text className="discovery-muted">Add transcripts, recordings, and supporting documents at any time.</Text>
          </div>
          <Button
            appearance="primary"
            icon={<DocumentAdd24Regular />}
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
          >
            {uploading ? "Uploading..." : "Add material"}
          </Button>
        </div>
        <div className="discovery-upload-row">
          <Dropdown
            value={uploadType.replace(/_/g, " ")}
            selectedOptions={[uploadType]}
            onOptionSelect={(_, data) => setUploadType(data.optionValue as UploadType)}
          >
            {UPLOAD_TYPES.map((type) => <Option key={type} value={type}>{type.replace(/_/g, " ")}</Option>)}
          </Dropdown>
          <input ref={fileInputRef} type="file" hidden onChange={(event) => void handleUpload(event)} />
          <Text>{loadingUploads ? "Loading material..." : `${uploads?.length ?? 0} source file(s)`}</Text>
          <Button appearance="secondary" disabled={Boolean(busy) || !uploads?.length} onClick={() => void analyze()}>
            {discoveryCase?.analysis_revision ? "Analyze new revision" : "Find personas"}
          </Button>
        </div>
      </section>

      {discoveryCase?.personas.length ? (
        <section className="discovery-section" aria-labelledby="discovery-personas">
          <div className="discovery-section-header">
            <div>
              <h2 id="discovery-personas" className="discovery-section-heading">2. Personas</h2>
              <Text className="discovery-muted">Choose whose problem Genie should investigate deeply.</Text>
            </div>
          </div>
          <div className="discovery-persona-grid">
            {discoveryCase.personas.map((persona) => (
              <article key={persona.id} className={`discovery-card${persona.id === discoveryCase.selected_persona_id ? " selected" : ""}`}>
                <h3>{persona.name}</h3>
                <Text className="discovery-muted">{persona.description}</Text>
                <Text weight="semibold" style={{ display: "block", marginTop: 14 }}>Pain points</Text>
                <ul>{persona.pain_points.map((pain) => <li key={pain}>{pain}</li>)}</ul>
                <Button
                  appearance={persona.id === discoveryCase.selected_persona_id ? "primary" : "secondary"}
                  disabled={Boolean(busy)}
                  onClick={() => void perform("analyze persona", () => discoveryApi.selectPersona(sessionId, persona.id))}
                >
                  {persona.id === discoveryCase.selected_persona_id ? "Selected" : "Investigate persona"}
                </Button>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {discoveryCase && selectedPersona && discoveryCase.gap_analysis ? (
        <section className="discovery-section" aria-labelledby="discovery-gaps">
          <div>
            <h2 id="discovery-gaps" className="discovery-section-heading">3. Pain points and gaps</h2>
            <Text className="discovery-muted">Focused on {selectedPersona.name}.</Text>
          </div>
          <ul>{discoveryCase.deep_dive_findings.map((finding) => <li key={finding}>{finding}</li>)}</ul>
          <div className="discovery-gap-grid">
            {([
              ["known facts", discoveryCase.gap_analysis.known_facts],
              ["information gaps", discoveryCase.gap_analysis.information_gaps],
              ["assumptions", discoveryCase.gap_analysis.assumptions],
              ["evidence", discoveryCase.gap_analysis.evidence_references],
            ] as Array<[string, string[]]>).map(([label, items]) => (
              <div key={label} className="discovery-gap">
                <h3>{label}</h3>
                <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            ))}
          </div>
          <Text className="discovery-muted">
            Analysis confidence: {Math.round(discoveryCase.gap_analysis.confidence_score * 100)}%
          </Text>
          {!discoveryCase.qa_mode ? (
            <div className="discovery-choice-row">
              <Text weight="semibold">How should Genie ask?</Text>
              {(["interactive", "batch"] as DiscoveryQaMode[]).map((mode) => (
                <Button key={mode} disabled={Boolean(busy)} onClick={() => void perform("start Q&A", () => discoveryApi.setQaMode(sessionId, mode))}>
                  {mode === "interactive" ? "One at a time" : "Show all questions"}
                </Button>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {discoveryCase?.qa_mode ? (
        <section className="discovery-section" aria-labelledby="discovery-questions">
          <div>
            <h2 id="discovery-questions" className="discovery-section-heading">4. Clarify what matters</h2>
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
                  <Input
                    aria-label={`Answer: ${question.text}`}
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
          <div>
            <h2 id="discovery-solutions" className="discovery-section-heading">5. Probable solutions</h2>
            <Text className="discovery-muted">Compare architecture, tradeoffs, AI feasibility, and verified Azure price coverage.</Text>
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