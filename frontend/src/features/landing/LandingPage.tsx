import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Dropdown, Input, Option, Text } from "@fluentui/react-components";
import { Delete24Regular } from "@fluentui/react-icons";
import { sessionApi } from "@/services/sessionApi";
import { modelCatalogApi } from "@/services/modelCatalogApi";
import { discoveryApi } from "@/services/discoveryApi";
import { ApiError } from "@/services/httpClient";
import { useSessionContext } from "@/state/SessionContext";
import { ErrorState } from "@/components/ErrorState";
import type { SafeError } from "@/types/common";
import type { DiscoveryCase } from "@/types/discovery";

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
  const [savedDiscoveries, setSavedDiscoveries] = useState<DiscoveryCase[]>([]);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);

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

  useEffect(() => {
    let mounted = true;
    discoveryApi
      .list()
      .then((cases) => {
        if (mounted) setSavedDiscoveries(cases);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  const handleCreate = useCallback(async (destination: "/upload" | "/discovery") => {
    setCreating(true);
    setError(null);
    try {
      const session = await sessionApi.create(title.trim());
      setSessionId(session.id);
      setSelectedModelDeploymentRef(selectedModel);
      navigate(destination);
    } catch (err) {
      setError(err instanceof ApiError ? err : { message: "Unable to create a session." });
    } finally {
      setCreating(false);
    }
  }, [title, selectedModel, setSelectedModelDeploymentRef, setSessionId, navigate]);

  const handleDeleteDiscovery = useCallback(async (item: DiscoveryCase) => {
    setDeletingSessionId(item.session_id);
    setError(null);
    try {
      await discoveryApi.delete(item.session_id);
      setSavedDiscoveries((cases) => cases.filter((saved) => saved.id !== item.id));
    } catch (err) {
      setError(err instanceof ApiError ? err : { message: "Unable to delete Discovery." });
    } finally {
      setDeletingSessionId(null);
    }
  }, []);

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
          gridTemplateColumns: "minmax(0, 1fr)",
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
            if (event.key === "Enter" && !creating && title.trim()) void handleCreate("/upload");
          }}
          style={{ width: "100%" }}
          required
        />
        <div
          style={{
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
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
          <Button
            appearance="primary"
            disabled={creating || !title.trim()}
            onClick={() => void handleCreate("/upload")}
          >
            {creating ? "Creating session..." : "Start Prototype"}
          </Button>
          <Button
            appearance="secondary"
            disabled={creating || !title.trim()}
            onClick={() => void handleCreate("/discovery")}
          >
            Start Discovery
          </Button>
        </div>
      </div>

      {modelsError ? <ErrorState error={modelsError} /> : null}
      {error ? <ErrorState error={error} /> : null}

      {savedDiscoveries.length > 0 ? (
        <section style={{ marginTop: 32, textAlign: "left", borderTop: "1px solid #232a33", paddingTop: 20 }}>
          <Text weight="semibold" size={400}>Resume Discovery</Text>
          <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
            {savedDiscoveries.map((item) => (
              <div
                key={item.id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "minmax(0, 1fr) auto auto",
                  alignItems: "center",
                  gap: 12,
                  padding: "12px 0",
                  borderBottom: "1px solid #232a33",
                }}
              >
                <div>
                  <Text weight="semibold" style={{ display: "block" }}>
                    Discovery revision {item.analysis_revision}
                  </Text>
                  <Text size={200} style={{ opacity: 0.7 }}>
                    Updated {new Date(item.updated_at).toLocaleString()} · {item.source_upload_ids.length} source file(s)
                  </Text>
                </div>
                <Badge appearance="outline">{item.status.replace(/_/g, " ")}</Badge>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Button
                    disabled={deletingSessionId === item.session_id}
                    onClick={() => {
                      setSessionId(item.session_id);
                      setSelectedModelDeploymentRef(item.model_deployment_ref);
                      navigate("/discovery");
                    }}
                  >
                    Resume
                  </Button>
                  <Button
                    appearance="subtle"
                    shape="circular"
                    icon={<Delete24Regular />}
                    aria-label={`Delete Discovery revision ${item.analysis_revision}`}
                    title={`Delete Discovery revision ${item.analysis_revision}`}
                    disabled={deletingSessionId !== null}
                    onClick={() => void handleDeleteDiscovery(item)}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
