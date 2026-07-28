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
    <div className="genie-fade-in" style={{ maxWidth: 600, margin: "8vh auto", textAlign: "center" }}>
      <Text
        weight="bold"
        size={900}
        style={{
          display: "block",
          backgroundImage: "linear-gradient(135deg, #6ba3ea 0%, #a3c4f3 100%)",
          backgroundClip: "text",
          WebkitBackgroundClip: "text",
          color: "transparent",
          letterSpacing: -0.5,
        }}
      >
        Genie
      </Text>
      <Text size={400} style={{ display: "block", opacity: 0.85, marginTop: 6, marginBottom: 8 }}>
        Your Agentic Experience Center
      </Text>
      <Text size={300} style={{ display: "block", opacity: 0.65, marginBottom: 32 }}>
        Upload a transcript and watch requirement discovery, agent collaboration, architecture
        design, and governance decisions unfold live - front-row seats to your own AI-built
        solution.
      </Text>

      <div
        style={{
          display: "flex",
          gap: 8,
          justifyContent: "center",
          marginBottom: 16,
          padding: 20,
          borderRadius: 12,
          border: "1px solid #232a33",
          backgroundColor: "rgba(19, 25, 33, 0.55)",
          boxShadow: "0 8px 30px rgba(0, 0, 0, 0.25)",
        }}
      >
        <Input
          placeholder="Give this session a name (optional)"
          value={title}
          onChange={(_, data) => setTitle(data.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !creating) void handleCreate();
          }}
          style={{ width: 320 }}
        />
        <Button appearance="primary" disabled={creating} onClick={() => void handleCreate()}>
          {creating ? "Creating your session..." : "✨ Start New Session"}
        </Button>
      </div>

      {error ? <ErrorState error={error} onRetry={() => void handleCreate()} /> : null}
    </div>
  );
}
