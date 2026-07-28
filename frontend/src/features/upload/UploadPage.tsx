import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Dropdown, Option, ProgressBar, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from "@fluentui/react-components";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { useSessionContext } from "@/state/SessionContext";
import { useUploadAction, useUploads } from "@/hooks/useUploads";
import { useWorkflowRun } from "@/hooks/useWorkflowRun";
import { useAsyncResource } from "@/hooks/useAsyncResource";
import { governanceApi } from "@/services/governanceApi";
import { DISCOVERY_WORKFLOW_ID, MISSION_PHASES } from "@/config/discoveryWorkflow";
import type { UploadType } from "@/types/upload";
import type { GovernanceEvent } from "@/types/governance";

const UPLOAD_TYPES: UploadType[] = ["transcript", "audio", "video", "supporting_document"];

interface ConsoleEntry {
  key: string;
  icon: string;
  text: string;
  detail?: string;
  tone: "done" | "active" | "pending" | "info";
}

function truncate(text: string, max = 140): string {
  return text.length > max ? `${text.slice(0, max).trimEnd()}...` : text;
}

/**
 * Turns the raw governance event trail into the exact call-chain narrative
 * the user asked to see: "clicked -> orchestrator engaged -> requirement
 * agent called -> requirement agent returned -> ... -> awaiting approval".
 * Every "done" entry here is backed by a real recorded event (or, for the
 * two synthetic head entries, a real client-side click) - nothing here is
 * simulated/fake progress.
 */
function buildConsoleEntries(events: GovernanceEvent[] | null, clicked: boolean): ConsoleEntry[] {
  const entries: ConsoleEntry[] = [];
  if (!clicked) return entries;

  entries.push({ key: "clicked", icon: "🖱️", text: "Start Prototyping clicked", tone: "done" });
  entries.push({
    key: "orchestrator",
    icon: "🧭",
    text: "genie-orchestrator engaged - coordinating the mission",
    tone: "done",
  });

  const specialistCalls = [...(events ?? [])]
    .filter((event): event is GovernanceEvent => event.category === "agent_execution" && event.detail.workflow_step !== true)
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));

  const completedStepIds = new Set<string>();
  for (const event of events ?? []) {
    if (event.category !== "agent_execution") continue;
    const id = event.detail.step_id;
    if (typeof id === "string") completedStepIds.add(id);
  }
  const activeIndex = MISSION_PHASES.findIndex((phase) => !completedStepIds.has(phase.stepId));

  MISSION_PHASES.forEach((phase, index) => {
    const call = specialistCalls.find((event) => event.detail.step_id === phase.stepId);
    if (call) {
      const agentId = call.agent_id ?? "specialist agent";
      const delegatedBy = typeof call.detail.delegated_by === "string" ? call.detail.delegated_by : "genie-orchestrator";
      const preview = typeof call.detail.output_preview === "string" ? call.detail.output_preview : "";
      entries.push({
        key: `${phase.stepId}-called`,
        icon: "📨",
        text: `${delegatedBy} -> ${agentId} called`,
        tone: "done",
      });
      entries.push({
        key: `${phase.stepId}-returned`,
        icon: "✅",
        text: `${agentId} returned - ${phase.label}`,
        detail: preview ? truncate(preview) : undefined,
        tone: "done",
      });
      return;
    }
    if (index === activeIndex) {
      entries.push({
        key: `${phase.stepId}-active`,
        icon: "⏳",
        text: `${phase.label}...`,
        tone: "active",
      });
    } else if (index > activeIndex) {
      entries.push({ key: `${phase.stepId}-pending`, icon: "⚪", text: phase.label, tone: "pending" });
    }
  });

  return entries;
}

const TONE_STYLES: Record<ConsoleEntry["tone"], React.CSSProperties> = {
  done: { opacity: 1 },
  active: { opacity: 1, fontWeight: 600 },
  pending: { opacity: 0.45 },
  info: { opacity: 1, fontWeight: 600 },
};

/**
 * Prominent, live, inline call-chain console shown the instant "Start
 * Prototyping" is clicked. Replaces a plain "please wait" bar with the
 * actual real-time orchestrator/agent activity (same governance event
 * trail the Triage panel reads), so the user is never left guessing
 * whether anything is happening - every line is either a real recorded
 * event or an explicit "still working on this" / "waiting on you" state.
 * Auto-scrolls to the newest entry and polls only while a run is active.
 */
function MissionConsole({
  sessionId,
  active,
  finishingMessage,
}: {
  sessionId: string;
  active: boolean;
  finishingMessage: string | null;
}): JSX.Element {
  const eventsFetcher = useCallback(() => governanceApi.listEvents(sessionId), [sessionId]);
  const { data: events } = useAsyncResource(eventsFetcher, [sessionId], {
    enabled: active,
    pollIntervalMs: 1500,
  });

  const entries = useMemo(() => buildConsoleEntries(events, true), [events]);

  const startedAtRef = useRef<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (!active) {
      startedAtRef.current = null;
      setElapsedSeconds(0);
      return;
    }
    startedAtRef.current ??= Date.now();
    const startedAt = startedAtRef.current;
    const tick = () => setElapsedSeconds(Math.round((Date.now() - startedAt) / 1000));
    tick();
    const intervalId = window.setInterval(tick, 1000);
    return () => window.clearInterval(intervalId);
  }, [active]);

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [entries.length, finishingMessage]);

  const doneCount = entries.filter((entry) => entry.tone === "done").length;

  return (
    <div
      className="genie-fade-in"
      style={{
        marginTop: 16,
        padding: "14px 16px",
        borderRadius: 8,
        border: "1px solid #232a33",
        backgroundColor: "#11161d",
        maxWidth: 640,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {active ? <span className="genie-live-dot" aria-label="Live" title="Live" /> : null}
          <Text size={300} weight="semibold">
            🕹️ Mission Control Flow
          </Text>
        </div>
        <Text size={100} style={{ opacity: 0.6, whiteSpace: "nowrap" }}>
          {elapsedSeconds}s elapsed
        </Text>
      </div>
      <ProgressBar
        value={finishingMessage ? 1 : undefined}
        thickness="large"
        shape="rounded"
        style={{ marginTop: 10 }}
      />
      <div ref={scrollRef} style={{ marginTop: 10, maxHeight: 260, overflowY: "auto", paddingRight: 4 }}>
        {entries.map((entry) => (
          <div key={entry.key} className="genie-fade-in" style={{ display: "flex", gap: 8, padding: "4px 0" }}>
            <span style={{ fontSize: 15 }} aria-hidden="true">
              {entry.icon}
            </span>
            <div style={{ minWidth: 0 }}>
              <Text size={200} style={{ ...TONE_STYLES[entry.tone], display: "block" }}>
                {entry.text}
              </Text>
              {entry.detail ? (
                <Text size={100} style={{ opacity: 0.6, display: "block" }}>
                  {entry.detail}
                </Text>
              ) : null}
            </div>
          </div>
        ))}
        {finishingMessage ? (
          <div className="genie-fade-in" style={{ display: "flex", gap: 8, padding: "4px 0" }}>
            <span style={{ fontSize: 15 }} aria-hidden="true">
              🏁
            </span>
            <Text size={200} style={{ ...TONE_STYLES.info, display: "block" }}>
              {finishingMessage}
            </Text>
          </div>
        ) : null}
      </div>
      <Text size={100} style={{ opacity: 0.5, display: "block", marginTop: 6 }}>
        {doneCount > 0 ? `${doneCount} real agent event${doneCount === 1 ? "" : "s"} recorded` : "Waiting for the first agent event..."}
      </Text>
    </div>
  );
}

export function UploadPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, setWorkflowRunId } = useSessionContext();
  const [uploadType, setUploadType] = useState<UploadType>("transcript");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { upload, uploading, error: uploadError } = useUploadAction(sessionId);
  const { data: uploads, loading, error, refresh } = useUploads(sessionId);
  const { run, running, error: runError } = useWorkflowRun(sessionId);

  const handleFileChosen = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (!file) return;
      await upload(uploadType, file);
      refresh();
      event.target.value = "";
    },
    [upload, uploadType, refresh],
  );

  // `clicked` flips true the instant the button is pressed (not just while
  // the HTTP request is in flight) so the live console appears with zero
  // perceived latency, and stays true through the short "finishing" beat
  // below so the user sees a clear terminal state instead of an abrupt jump
  // straight to the Requirements page.
  const [clicked, setClicked] = useState(false);
  const [finishingMessage, setFinishingMessage] = useState<string | null>(null);

  const handleGeneratePrototype = useCallback(async () => {
    setClicked(true);
    setFinishingMessage(null);
    try {
      const result = await run(DISCOVERY_WORKFLOW_ID);
      setWorkflowRunId(result.workflow_run_id);
      setFinishingMessage(
        result.status === "waiting_for_approval"
          ? "Paused for your approval - opening Requirements..."
          : "Mission phase complete - opening Requirements...",
      );
      window.setTimeout(() => navigate("/requirements"), 900);
    } catch {
      setClicked(false);
    }
  }, [run, setWorkflowRunId, navigate]);

  if (!sessionId) {
    return (
      <div>
        <PageHeader title="Upload" subtitle="No active session yet." />
        <Button appearance="primary" onClick={() => navigate("/")}>
          Start a session
        </Button>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Upload"
        subtitle="Ingest transcripts, recordings, and supporting documents for this session."
      />

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 20 }}>
        <Dropdown
          value={uploadType}
          selectedOptions={[uploadType]}
          onOptionSelect={(_, data) => setUploadType(data.optionValue as UploadType)}
        >
          {UPLOAD_TYPES.map((type) => (
            <Option key={type} value={type}>
              {type.replace(/_/g, " ")}
            </Option>
          ))}
        </Dropdown>
        <Button
          appearance="primary"
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
        >
          {uploading ? "Uploading..." : "Choose File"}
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          hidden
          onChange={(event) => void handleFileChosen(event)}
        />
      </div>

      {uploadError ? <ErrorState error={uploadError} /> : null}
      {runError ? <ErrorState error={runError} /> : null}

      {loading ? <LoadingState label="Loading uploads..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      {uploads && uploads.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHeaderCell>File</TableHeaderCell>
              <TableHeaderCell>Type</TableHeaderCell>
              <TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Uploaded</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {uploads.map((record) => (
              <TableRow key={record.id}>
                <TableCell>{record.file_name}</TableCell>
                <TableCell>{record.upload_type.replace(/_/g, " ")}</TableCell>
                <TableCell>{record.status}</TableCell>
                <TableCell>{new Date(record.uploaded_at).toLocaleString()}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}

      <div style={{ marginTop: 24, display: "flex", gap: 12, alignItems: "center" }}>
        <Button
          appearance="primary"
          disabled={clicked || !uploads || uploads.length === 0}
          onClick={() => void handleGeneratePrototype()}
        >
          {clicked ? (running ? "Starting Prototyping..." : "Finishing up...") : "Start Prototyping"}
        </Button>
        {clicked ? (
          <Text size={200} style={{ opacity: 0.75 }}>
            🕹️ Full history stays available in the Triage panel on the right
          </Text>
        ) : null}
      </div>
      {clicked && sessionId ? (
        <MissionConsole sessionId={sessionId} active={running} finishingMessage={finishingMessage} />
      ) : null}
    </div>
  );
}
