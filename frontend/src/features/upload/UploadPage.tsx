import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Dropdown, Option, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow } from "@fluentui/react-components";
import { Delete24Regular } from "@fluentui/react-icons";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { useSessionContext } from "@/state/SessionContext";
import { useUploadAction, useUploads } from "@/hooks/useUploads";
import { useWorkflowRun } from "@/hooks/useWorkflowRun";
import { DISCOVERY_WORKFLOW_ID } from "@/config/discoveryWorkflow";
import { ApiError } from "@/services/httpClient";
import type { SafeError } from "@/types/common";
import type { UploadType } from "@/types/upload";

const UPLOAD_TYPES: UploadType[] = ["transcript", "audio", "video", "supporting_document"];
const DOCUMENT_ACCEPT = [
  ".txt",
  ".md",
  ".pdf",
  ".docx",
  "text/plain",
  "text/markdown",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
].join(",");
const UPLOAD_TYPE_ACCEPT: Record<UploadType, string> = {
  transcript: DOCUMENT_ACCEPT,
  audio: "audio/*",
  video: "video/*",
  supporting_document: DOCUMENT_ACCEPT,
};

export function UploadPage(): JSX.Element {
  const navigate = useNavigate();
  const {
    sessionId,
    selectedModelDeploymentRef,
    setWorkflowRunId,
    setMissionStartedAt,
    setMissionError,
  } = useSessionContext();
  const [uploadType, setUploadType] = useState<UploadType>("transcript");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    upload,
    remove: removeUpload,
    uploading,
    removingUploadIds,
    error: uploadError,
  } = useUploadAction(sessionId);
  const { data: uploads, loading, error, refresh } = useUploads(sessionId);
  // Errors from this run are surfaced via SessionContext's missionError (see
  // handleGeneratePrototype below) since this page navigates away before
  // the run promise settles - there is no local error state to show here.
  const { run } = useWorkflowRun(sessionId);

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

  const handleRemoveUpload = useCallback(
    async (uploadId: string) => {
      try {
        await removeUpload(uploadId);
        refresh();
      } catch {
        return;
      }
    },
    [refresh, removeUpload],
  );

  // `clicked` flips true the instant the button is pressed so the button
  // disables and the live console appears with zero perceived latency.
  const [clicked, setClicked] = useState(false);

  // Navigates to Requirements immediately on click - the workflow run
  // itself (which can take a while, since analyze-requirements is a real
  // agent call) is kicked off in the background rather than awaited here,
  // so the Requirements page's own AgentActivityAnimation is what the user
  // watches while the agent works, instead of staring at a disabled button
  // on this page. The fetch is not tied to this component's lifecycle, so
  // it keeps running (and still updates the shared session context) even
  // after Upload unmounts.
  const handleGeneratePrototype = useCallback(async () => {
    setClicked(true);
    // Marks the mission as started for the Agent Triage panel (global, in
    // AppShell) so it can show the live "clicked -> orchestrator engaged"
    // mission console itself - this page no longer renders its own copy.
    setMissionStartedAt(Date.now());
    setMissionError(null);
    navigate("/requirements");
    try {
      const result = await run(DISCOVERY_WORKFLOW_ID, selectedModelDeploymentRef ?? undefined);
      setWorkflowRunId(result.workflow_run_id);
    } catch (err) {
      setMissionStartedAt(null);
      const safe: SafeError = err instanceof ApiError ? err : { message: "Unable to start the workflow." };
      setMissionError(safe);
    }
  }, [
    navigate,
    run,
    selectedModelDeploymentRef,
    setMissionError,
    setMissionStartedAt,
    setWorkflowRunId,
  ]);

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
          accept={UPLOAD_TYPE_ACCEPT[uploadType]}
          hidden
          onChange={(event) => void handleFileChosen(event)}
        />
      </div>

      {uploadError ? <ErrorState error={uploadError} /> : null}

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
              <TableHeaderCell>Actions</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {uploads.map((record) => (
              <TableRow key={record.id}>
                <TableCell>{record.file_name}</TableCell>
                <TableCell>{record.upload_type.replace(/_/g, " ")}</TableCell>
                <TableCell>{record.status}</TableCell>
                <TableCell>{new Date(record.uploaded_at).toLocaleString()}</TableCell>
                <TableCell>
                  <Button
                    appearance="subtle"
                    icon={<Delete24Regular />}
                    aria-label={`Remove ${record.file_name}`}
                    title={`Remove ${record.file_name}`}
                    disabled={removingUploadIds.has(record.id)}
                    onClick={() => void handleRemoveUpload(record.id)}
                  />
                </TableCell>
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
          {clicked ? "Opening Requirements..." : "Start Prototyping"}
        </Button>
      </div>
    </div>
  );
}
