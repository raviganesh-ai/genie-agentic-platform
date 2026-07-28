import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Dropdown, Option, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from "@fluentui/react-components";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { useSessionContext } from "@/state/SessionContext";
import { useUploadAction, useUploads } from "@/hooks/useUploads";
import { useWorkflowRun } from "@/hooks/useWorkflowRun";
import { DISCOVERY_WORKFLOW_ID } from "@/config/discoveryWorkflow";
import type { UploadType } from "@/types/upload";

const UPLOAD_TYPES: UploadType[] = ["transcript", "audio", "video", "supporting_document"];

export function UploadPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId, setWorkflowRunId, setMissionStartedAt } = useSessionContext();
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
    // Marks the mission as started for the Agent Triage panel (global, in
    // AppShell) so it can show the live "clicked -> orchestrator engaged"
    // mission console itself - this page no longer renders its own copy.
    setMissionStartedAt(Date.now());
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
      setMissionStartedAt(null);
    }
  }, [run, setWorkflowRunId, setMissionStartedAt, navigate]);

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
            🕹️ Live mission progress is in the Agent Triage panel on the right
          </Text>
        ) : null}
      </div>
      {clicked && finishingMessage ? (
        <Text size={200} className="genie-fade-in" style={{ display: "block", marginTop: 10, opacity: 0.85 }}>
          🏁 {finishingMessage}
        </Text>
      ) : null}
    </div>
  );
}
