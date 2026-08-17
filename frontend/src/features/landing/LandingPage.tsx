import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Dropdown, Input, Option, Text } from "@fluentui/react-components";
import { sessionApi } from "@/services/sessionApi";
import { modelCatalogApi } from "@/services/modelCatalogApi";
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
  const { setSessionId, setSelectedModelDeploymentRef } = useSessionContext();
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<SafeError | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(true);
  const [modelsError, setModelsError] = useState<SafeError | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoadingModels(true);
    modelCatalogApi
      .getAvailable()
      .then((catalog) => {
        if (!mounted) return;
        setModels(catalog.available_models);
        setSelectedModel(catalog.default_model);
      })
      .catch((err) => {
        if (!mounted) return;
        setModelsError(err instanceof ApiError ? err : { message: "Unable to load available models." });
      })
      .finally(() => {
        if (mounted) setLoadingModels(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const handleCreate = useCallback(async () => {
    setCreating(true);
    setError(null);
    try {
      const session = await sessionApi.create(title.trim());
      setSessionId(session.id);
      setSelectedModelDeploymentRef(selectedModel);
      navigate("/upload");
    } catch (err) {
      setError(err instanceof ApiError ? err : { message: "Unable to create a session." });
    } finally {
      setCreating(false);
    }
  }, [title, selectedModel, setSelectedModelDeploymentRef, setSessionId, navigate]);

  return (
    <div className="genie-fade-in" style={{ maxWidth: 680, margin: "8vh auto", textAlign: "center" }}>
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
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) auto",
          gap: 12,
          justifyContent: "center",
          alignItems: "center",
          marginBottom: 16,
          padding: 20,
          borderRadius: 12,
          border: "1px solid #232a33",
          backgroundColor: "rgba(19, 25, 33, 0.55)",
          boxShadow: "0 8px 30px rgba(0, 0, 0, 0.25)",
        }}
      >
        <Input
          placeholder="Give this session a name"
          value={title}
          onChange={(_, data) => setTitle(data.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !creating && title.trim()) void handleCreate();
          }}
          style={{ width: "100%" }}
          required
        />
        <Button
          appearance="primary"
          disabled={creating || !title.trim()}
          onClick={() => void handleCreate()}
          style={{ whiteSpace: "nowrap" }}
        >
          {creating ? "Creating your session..." : "✨ Start New Session"}
        </Button>
        <div
          style={{
            gridColumn: "1 / -1",
            display: "flex",
            flexDirection: "column",
            alignItems: "stretch",
            gap: 6,
            minWidth: 0,
            textAlign: "left",
          }}
        >
          <Text size={200} style={{ opacity: 0.7 }}>
            Generation model
          </Text>
          <Dropdown
            placeholder={loadingModels ? "Loading models..." : "Select a model"}
            value={selectedModel ?? ""}
            selectedOptions={selectedModel ? [selectedModel] : []}
            disabled={loadingModels || models.length === 0 || creating}
            onOptionSelect={(_, data) => setSelectedModel(data.optionValue ?? null)}
            style={{ width: "100%" }}
          >
            {models.map((model) => (
              <Option key={model} value={model}>
                {model}
              </Option>
            ))}
          </Dropdown>
        </div>
      </div>

      {modelsError ? <ErrorState error={modelsError} /> : null}
      {error ? <ErrorState error={error} onRetry={() => void handleCreate()} /> : null}
    </div>
  );
}
