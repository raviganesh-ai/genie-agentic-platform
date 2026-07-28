import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Dropdown, Option, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow } from "@fluentui/react-components";
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

  const handleGeneratePrototype = useCallback(async () => {
    const result = await run(DISCOVERY_WORKFLOW_ID);
    setWorkflowRunId(result.workflow_run_id);
    navigate("/requirements");
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

      <div style={{ marginTop: 24, display: "flex", gap: 12 }}>
        <Button
          appearance="primary"
          disabled={running || !uploads || uploads.length === 0}
          onClick={() => void handleGeneratePrototype()}
        >
          {running ? "Starting Mission..." : "Start Mission"}
        </Button>
      </div>
    </div>
  );
}
