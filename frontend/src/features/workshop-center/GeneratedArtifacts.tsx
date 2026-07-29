import { useEffect, useMemo, useState } from "react";
import { Button, Text, Textarea } from "@fluentui/react-components";
import {
  extractAgentLabel,
  extractCodeBlocks,
  splitIntoNamedSections,
  stripCodeBlocks,
} from "@/utils/textArtifacts";

interface Artifact {
  key: string;
  icon: string;
  title: string;
  kind: "code" | "narrative";
  /** Drives the accent color/heading of each artifact's own bounded
   * section, so the UI block, every specialist agent's block, the
   * Orchestrator Agent block, and any leftover narrative are always
   * visually distinct rather than blending together. */
  variant: "ui" | "orchestrator" | "agent" | "narrative";
  content: string;
  /** For `kind === "code"` artifacts only: the matching agent's own
   * description, lifted from the trailing "## Multi-Agent Workflow"
   * section so it renders inline above the code instead of in a
   * disconnected card at the bottom. */
  description?: string;
}

const UI_CODE_LANGUAGES = new Set(["tsx", "jsx", "typescript", "javascript", "ts", "js"]);
const AGENT_CODE_LANGUAGES = new Set(["python", "py"]);

/** Accent color per artifact variant - shown as a left border stripe and a
 * tinted header background so each of this mission's own generated
 * components (UI, each specialist agent, the Orchestrator Agent) reads as
 * its own clearly bounded, colour-coded section instead of one
 * undifferentiated list. */
const VARIANT_ACCENTS: Record<Artifact["variant"], string> = {
  ui: "#2f83e0",
  orchestrator: "#c9a227",
  agent: "#3fa66a",
  narrative: "#6b7686",
};

const UI_DESCRIPTION_FALLBACK =
  "The customer-facing UI for this mission - the single entry point every screen calls into, which in turn calls only the Orchestrator Agent below.";

/** Labels each fenced code block using the build-generation-v1 contract's
 * `# agent: <name>` / `// agent: ui` first-line comment when present - "ui"
 * -> Generated UI Code, "orchestrator" -> Generated Orchestrator Agent
 * Code, any other name -> Generated Agent Code for that specialist agent -
 * so a response with one UI block, one block per specialist agent, and
 * one Orchestrator Agent block are each titled precisely instead of all
 * being lumped under one generic label. Falls back to a language-only
 * label (older/unlabeled responses) rather than guessing when no `agent:`
 * comment is present. */
function labelForCodeBlock(
  language: string,
  agentLabel: string | null,
): { icon: string; title: string; variant: Artifact["variant"] } {
  if (agentLabel) {
    const normalized = agentLabel.toLowerCase();
    if (normalized === "ui") {
      return { icon: "🖥️", title: `Generated UI Code (${language})`, variant: "ui" };
    }
    if (normalized === "orchestrator") {
      return {
        icon: "🧭",
        title: `Generated Orchestrator Agent Code (${language})`,
        variant: "orchestrator",
      };
    }
    return {
      icon: "🤖",
      title: `Generated Agent Code - ${agentLabel} (${language})`,
      variant: "agent",
    };
  }
  const normalized = language.toLowerCase();
  if (UI_CODE_LANGUAGES.has(normalized)) {
    return { icon: "🖥️", title: `Generated UI Code (${language})`, variant: "ui" };
  }
  if (AGENT_CODE_LANGUAGES.has(normalized)) {
    return { icon: "🤖", title: `Generated Agent Code (${language})`, variant: "agent" };
  }
  return { icon: "📄", title: `Generated Code (${language})`, variant: "agent" };
}

function normalizeAgentName(name: string): string {
  return name.trim().toLowerCase().replace(/\*\*/g, "");
}

function buildArtifacts(outputText: string): Artifact[] {
  const artifacts: Artifact[] = [];
  const codeBlocks = extractCodeBlocks(outputText);
  const remainder = stripCodeBlocks(outputText);
  const narrativeSections = splitIntoNamedSections(remainder);
  const usedSectionIndices = new Set<number>();

  const findAgentSection = (agentLabel: string): { index: number; body: string } | null => {
    const normalizedLabel = normalizeAgentName(agentLabel);
    const isOrchestrator = normalizedLabel === "orchestrator";
    for (let index = 0; index < narrativeSections.length; index += 1) {
      if (usedSectionIndices.has(index)) continue;
      const normalizedTitle = normalizeAgentName(narrativeSections[index].title);
      const matches = isOrchestrator
        ? normalizedTitle.includes("orchestrator")
        : normalizedTitle === normalizedLabel ||
          normalizedTitle.includes(normalizedLabel) ||
          normalizedLabel.includes(normalizedTitle);
      if (matches) {
        return { index, body: narrativeSections[index].body };
      }
    }
    return null;
  };

  codeBlocks.forEach((block, index) => {
    const agentLabel = extractAgentLabel(block.code);
    const { icon, title, variant } = labelForCodeBlock(block.language, agentLabel);
    let description: string | undefined;
    if (agentLabel) {
      if (agentLabel.toLowerCase() === "ui") {
        description = UI_DESCRIPTION_FALLBACK;
      } else {
        const found = findAgentSection(agentLabel);
        if (found) {
          usedSectionIndices.add(found.index);
          description = found.body || undefined;
        }
      }
    }
    artifacts.push({
      key: `code-${index}`,
      icon,
      title,
      kind: "code",
      variant,
      content: block.code,
      description,
    });
  });

  if (narrativeSections.length > 0) {
    narrativeSections.forEach((section, index) => {
      if (usedSectionIndices.has(index)) return;
      if (section.body.trim().length === 0) return;
      artifacts.push({
        key: `agent-${index}`,
        icon: "🤖",
        title: section.title,
        kind: "narrative",
        variant: "narrative",
        content: section.body,
      });
    });
  } else if (remainder.trim().length > 0) {
    artifacts.push({
      key: "workflow-design",
      icon: "🤖",
      title: "Multi-Agent Workflow Design",
      kind: "narrative",
      variant: "narrative",
      content: remainder,
    });
  }

  return artifacts;
}

/**
 * Turns the Build Agent's single free-text output into distinct artifact
 * cards (generated UI code, one card per specialist/orchestrator agent
 * code block - each with its own agent's description shown inline above
 * the code - plus any leftover multi-agent workflow narrative) revealed
 * one at a time on a short stagger - instead of dumping one giant text
 * blob, this reads as "watch the agent's artifacts appear" per the user's
 * request. The underlying generation already completed by the time this
 * data arrives (there is no live token stream from the backend), so the
 * stagger is a presentational reveal of real, already-produced content -
 * never fabricated or simulated text.
 *
 * When `onRegenerateArtifact` is supplied, every code artifact also gets
 * an Edit control (inline textarea, kept only in local state/Copy output)
 * and a Regenerate control (submits a free-text instruction back to the
 * Build Agent for just that artifact; the parent is responsible for
 * refetching the workflow run afterwards, which will naturally replace
 * `outputText` with the freshly regenerated content).
 */
export function GeneratedArtifacts({
  outputText,
  onRegenerateArtifact,
}: {
  outputText: string;
  onRegenerateArtifact?: (artifactTitle: string, instruction: string) => Promise<void>;
}): JSX.Element {
  const artifacts = useMemo(() => buildArtifacts(outputText), [outputText]);
  const codeOrdinals = useMemo(() => {
    const ordinals: Record<string, number> = {};
    let counter = 0;
    artifacts.forEach((artifact) => {
      if (artifact.kind === "code") {
        counter += 1;
        ordinals[artifact.key] = counter;
      }
    });
    return ordinals;
  }, [artifacts]);
  const totalCodeArtifacts = useMemo(
    () => artifacts.filter((artifact) => artifact.kind === "code").length,
    [artifacts],
  );
  const [revealCount, setRevealCount] = useState(0);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [contentOverrides, setContentOverrides] = useState<Record<string, string>>({});
  const [regeneratingKey, setRegeneratingKey] = useState<string | null>(null);
  const [instructionDraft, setInstructionDraft] = useState("");
  const [regenerateBusyKey, setRegenerateBusyKey] = useState<string | null>(null);
  const [regenerateErrorKey, setRegenerateErrorKey] = useState<string | null>(null);

  useEffect(() => {
    setRevealCount(0);
    if (artifacts.length === 0) return;
    const timers = artifacts.map((_, index) =>
      window.setTimeout(() => setRevealCount((prev) => Math.max(prev, index + 1)), index * 350),
    );
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [artifacts]);

  useEffect(() => {
    // Fresh generated output (e.g. after a regenerate completes) supersedes
    // any local edit drafts/overrides and open edit/regenerate panels.
    setContentOverrides({});
    setEditingKey(null);
    setRegeneratingKey(null);
    setRegenerateErrorKey(null);
  }, [outputText]);

  if (artifacts.length === 0) {
    return (
      <Text size={300} style={{ opacity: 0.7 }}>
        The Build Agent hasn&apos;t produced any artifacts yet.
      </Text>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {artifacts.slice(0, revealCount).map((artifact) => {
        const isEditing = editingKey === artifact.key;
        const isRegenerating = regeneratingKey === artifact.key;
        const isBusy = regenerateBusyKey === artifact.key;
        const displayContent = contentOverrides[artifact.key] ?? artifact.content;
        const accent = VARIANT_ACCENTS[artifact.variant];
        const ordinal = codeOrdinals[artifact.key];

        return (
          <div
            key={artifact.key}
            className="genie-fade-in"
            style={{
              border: `1px solid ${accent}55`,
              borderLeft: `4px solid ${accent}`,
              borderRadius: 10,
              backgroundColor: "#10151c",
              boxShadow: "0 1px 4px rgba(0, 0, 0, 0.35)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 8,
                padding: "10px 14px",
                backgroundColor: `${accent}1a`,
                borderBottom: `1px solid ${accent}40`,
              }}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {artifact.kind === "code" ? (
                  <Text
                    size={100}
                    style={{ textTransform: "uppercase", letterSpacing: 0.6, opacity: 0.65 }}
                  >
                    Component {ordinal} of {totalCodeArtifacts}
                  </Text>
                ) : null}
                <Text weight="semibold" size={300}>
                  {artifact.icon} {artifact.title}
                </Text>
              </div>
              {artifact.kind === "code" ? (
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  {isEditing ? (
                    <>
                      <Button
                        size="small"
                        appearance="primary"
                        onClick={() => {
                          setContentOverrides((prev) => ({ ...prev, [artifact.key]: editDraft }));
                          setEditingKey(null);
                        }}
                      >
                        Save
                      </Button>
                      <Button size="small" appearance="subtle" onClick={() => setEditingKey(null)}>
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <Button
                      size="small"
                      appearance="subtle"
                      onClick={() => {
                        setEditDraft(displayContent);
                        setEditingKey(artifact.key);
                        setRegeneratingKey(null);
                      }}
                    >
                      Edit
                    </Button>
                  )}
                  {onRegenerateArtifact ? (
                    <Button
                      size="small"
                      appearance="subtle"
                      disabled={isBusy}
                      onClick={() => {
                        if (isRegenerating) {
                          setRegeneratingKey(null);
                        } else {
                          setInstructionDraft("");
                          setRegenerateErrorKey(null);
                          setRegeneratingKey(artifact.key);
                          setEditingKey(null);
                        }
                      }}
                    >
                      Regenerate
                    </Button>
                  ) : null}
                  <Button
                    size="small"
                    appearance="subtle"
                    onClick={() => {
                      void navigator.clipboard.writeText(displayContent);
                      setCopiedKey(artifact.key);
                      window.setTimeout(
                        () => setCopiedKey((prev) => (prev === artifact.key ? null : prev)),
                        1500,
                      );
                    }}
                  >
                    {copiedKey === artifact.key ? "Copied!" : "Copy"}
                  </Button>
                </div>
              ) : null}
            </div>

            <div style={{ padding: "10px 14px" }}>
              {artifact.kind === "code" && artifact.description ? (
                <Text size={200} style={{ display: "block", marginBottom: 8, opacity: 0.75 }}>
                  {artifact.description}
                </Text>
              ) : null}

              {artifact.kind === "code" ? (
                isEditing ? (
                  <Textarea
                    value={editDraft}
                    onChange={(_, data) => setEditDraft(data.value)}
                    style={{ width: "100%" }}
                    textarea={{ style: { fontFamily: "monospace", fontSize: 11, minHeight: 220 } }}
                  />
                ) : (
                  <pre
                    style={{
                      fontSize: 11,
                      whiteSpace: "pre-wrap",
                      maxHeight: 260,
                      overflowY: "auto",
                      fontFamily: "monospace",
                      opacity: 0.9,
                      backgroundColor: "#0b0f14",
                      border: "1px solid #232a33",
                      borderRadius: 6,
                      padding: "8px 10px",
                      margin: 0,
                    }}
                  >
                    {displayContent}
                  </pre>
                )
              ) : (
                <Text size={300} style={{ whiteSpace: "pre-wrap", display: "block", opacity: 0.85 }}>
                  {artifact.content || "(no additional detail provided)"}
                </Text>
              )}

              {artifact.kind === "code" && isRegenerating ? (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                  <Textarea
                    value={instructionDraft}
                    onChange={(_, data) => setInstructionDraft(data.value)}
                    placeholder={`Describe how to change the ${artifact.title}...`}
                    style={{ width: "100%" }}
                  />
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <Button
                      size="small"
                      appearance="primary"
                      disabled={!instructionDraft.trim() || isBusy}
                    onClick={() => {
                      if (!onRegenerateArtifact) return;
                      const instruction = instructionDraft.trim();
                      setRegenerateBusyKey(artifact.key);
                      setRegenerateErrorKey(null);
                      void onRegenerateArtifact(artifact.title, instruction)
                        .then(() => {
                          setRegeneratingKey(null);
                        })
                        .catch(() => {
                          setRegenerateErrorKey(artifact.key);
                        })
                        .finally(() => {
                          setRegenerateBusyKey((prev) => (prev === artifact.key ? null : prev));
                        });
                    }}
                  >
                    {isBusy ? "Regenerating..." : "Submit"}
                  </Button>
                  <Button
                    size="small"
                    appearance="subtle"
                    disabled={isBusy}
                    onClick={() => setRegeneratingKey(null)}
                  >
                    Cancel
                  </Button>
                </div>
                {regenerateErrorKey === artifact.key ? (
                  <Text size={200} style={{ color: "#e5484d" }}>
                    Regeneration failed. Please try again.
                  </Text>
                ) : null}
              </div>
            ) : null}
            </div>
          </div>
        );
      })}
      {revealCount < artifacts.length ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, opacity: 0.6 }}>
          <span className="genie-live-dot" aria-label="Generating" />
          <Text size={200}>Generating next artifact...</Text>
        </div>
      ) : null}
    </div>
  );
}

