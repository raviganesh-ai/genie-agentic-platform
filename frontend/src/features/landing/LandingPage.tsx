import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Input, Text } from "@fluentui/react-components";
import { sessionApi } from "@/services/sessionApi";
import { ApiError } from "@/services/httpClient";
import { useSessionContext } from "@/state/SessionContext";
import { ErrorState } from "@/components/ErrorState";
import type { SafeError } from "@/types/common";

/**
 * Landing page: create or resume a Genie session. This is the single entry
 * point that establishes `sessionId` in SessionContext for every other page.
 */
export function LandingPage(): JSX.Element {
  const navigate = useNavigate();
  const { setSessionId } = useSessionContext();
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<SafeError | null>(null);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    setError(null);
    try {
      const session = await sessionApi.create(title.trim() || "Untitled Discovery Session");
      setSessionId(session.id);
      navigate("/upload");
    } catch (err) {
      setError(err instanceof ApiError ? err : { message: "Unable to create a session." });
    } finally {
      setCreating(false);
    }
  }, [title, setSessionId, navigate]);

  return (
    <div style={{ maxWidth: 560, margin: "10vh auto", textAlign: "center" }}>
      <Text weight="bold" size={900} style={{ display: "block" }}>
        Genie
      </Text>
      <Text size={400} style={{ display: "block", opacity: 0.75, marginBottom: 32 }}>
        Agentic Experience Center — watch requirement discovery, agent collaboration,
        architecture design, and governance unfold in real time.
      </Text>

      <div style={{ display: "flex", gap: 8, justifyContent: "center", marginBottom: 16 }}>
        <Input
          placeholder="Session title (optional)"
          value={title}
          onChange={(_, data) => setTitle(data.value)}
          style={{ width: 320 }}
        />
        <Button appearance="primary" disabled={creating} onClick={() => void handleCreate()}>
          {creating ? "Creating..." : "Start New Session"}
        </Button>
      </div>

      {error ? <ErrorState error={error} onRetry={() => void handleCreate()} /> : null}
    </div>
  );
}
