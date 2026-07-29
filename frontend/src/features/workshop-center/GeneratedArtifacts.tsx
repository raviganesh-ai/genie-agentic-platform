import { useEffect, useMemo, useState } from "react";
import { Button, Text } from "@fluentui/react-components";
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
  content: string;
}

const UI_CODE_LANGUAGES = new Set(["tsx", "jsx", "typescript", "javascript", "ts", "js"]);
const AGENT_CODE_LANGUAGES = new Set(["python", "py"]);

/** Labels each fenced code block using the build-generation-v1 contract's
 * `# agent: <name>` / `// agent: ui` first-line comment when present - "ui"
 * -> Generated UI Code, "orchestrator" -> Generated Orchestrator Agent
 * Code, any other name -> Generated Agent Code for that specialist agent -
 * so a response with one UI block, one block per specialist agent, and
 * one Orchestrator Agent block are each titled precisely instead of all
 * being lumped under one generic label. Falls back to a language-only
 * label (older/unlabeled responses) rather than guessing when no `agent:`
 * comment is present. */
function labelForCodeBlock(language: string, agentLabel: string | null): { icon: string; title: string } {
  if (agentLabel) {
    const normalized = agentLabel.toLowerCase();
    if (normalized === "ui") {
      return { icon: "🖥️", title: `Generated UI Code (${language})` };
    }
    if (normalized === "orchestrator") {
      return { icon: "🧭", title: `Generated Orchestrator Agent Code (${language})` };
    }
    return { icon: "🤖", title: `Generated Agent Code - ${agentLabel} (${language})` };
  }
  const normalized = language.toLowerCase();
  if (UI_CODE_LANGUAGES.has(normalized)) {
    return { icon: "🖥️", title: `Generated UI Code (${language})` };
  }
  if (AGENT_CODE_LANGUAGES.has(normalized)) {
    return { icon: "🤖", title: `Generated Agent Code (${language})` };
  }
  return { icon: "📄", title: `Generated Code (${language})` };
}

function buildArtifacts(outputText: string): Artifact[] {
  const artifacts: Artifact[] = [];
  const codeBlocks = extractCodeBlocks(outputText);
  codeBlocks.forEach((block, index) => {
    const { icon, title } = labelForCodeBlock(block.language, extractAgentLabel(block.code));
    artifacts.push({
      key: `code-${index}`,
      icon,
      title,
      kind: "code",
      content: block.code,
    });
  });

  const remainder = stripCodeBlocks(outputText);
  const sections = splitIntoNamedSections(remainder);
  if (sections.length > 0) {
    sections.forEach((section, index) => {
      artifacts.push({
        key: `agent-${index}`,
        icon: "🤖",
        title: section.title,
        kind: "narrative",
        content: section.body,
      });
    });
  } else if (remainder.trim().length > 0) {
    artifacts.push({
      key: "workflow-design",
      icon: "🤖",
      title: "Multi-Agent Workflow Design",
      kind: "narrative",
      content: remainder,
    });
  }

  return artifacts;
}

/**
 * Turns the Build Agent's single free-text output into distinct artifact
 * cards (generated UI code, multi-agent workflow design sections) revealed
 * one at a time on a short stagger - instead of dumping one giant text
 * blob, this reads as "watch the agent's artifacts appear" per the user's
 * request. The underlying generation already completed by the time this
 * data arrives (there is no live token stream from the backend), so the
 * stagger is a presentational reveal of real, already-produced content -
 * never fabricated or simulated text.
 */
export function GeneratedArtifacts({ outputText }: { outputText: string }): JSX.Element {
  const artifacts = useMemo(() => buildArtifacts(outputText), [outputText]);
  const [revealCount, setRevealCount] = useState(0);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  useEffect(() => {
    setRevealCount(0);
    if (artifacts.length === 0) return;
    const timers = artifacts.map((_, index) =>
      window.setTimeout(() => setRevealCount((prev) => Math.max(prev, index + 1)), index * 350),
    );
    return () => timers.forEach((id) => window.clearTimeout(id));
  }, [artifacts]);

  if (artifacts.length === 0) {
    return (
      <Text size={300} style={{ opacity: 0.7 }}>
        The Build Agent hasn&apos;t produced any artifacts yet.
      </Text>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {artifacts.slice(0, revealCount).map((artifact) => (
        <div
          key={artifact.key}
          className="genie-fade-in"
          style={{
            border: "1px solid #232a33",
            borderRadius: 8,
            padding: "10px 12px",
            backgroundColor: "#161c24",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <Text weight="semibold" size={300}>
              {artifact.icon} {artifact.title}
            </Text>
            {artifact.kind === "code" ? (
              <Button
                size="small"
                appearance="subtle"
                onClick={() => {
                  void navigator.clipboard.writeText(artifact.content);
                  setCopiedKey(artifact.key);
                  window.setTimeout(
                    () => setCopiedKey((prev) => (prev === artifact.key ? null : prev)),
                    1500,
                  );
                }}
              >
                {copiedKey === artifact.key ? "Copied!" : "Copy"}
              </Button>
            ) : null}
          </div>
          {artifact.kind === "code" ? (
            <pre
              style={{
                fontSize: 11,
                whiteSpace: "pre-wrap",
                marginTop: 8,
                maxHeight: 260,
                overflowY: "auto",
                fontFamily: "monospace",
                opacity: 0.9,
              }}
            >
              {artifact.content}
            </pre>
          ) : (
            <Text size={300} style={{ whiteSpace: "pre-wrap", display: "block", marginTop: 6, opacity: 0.85 }}>
              {artifact.content || "(no additional detail provided)"}
            </Text>
          )}
        </div>
      ))}
      {revealCount < artifacts.length ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, opacity: 0.6 }}>
          <span className="genie-live-dot" aria-label="Generating" />
          <Text size={200}>Generating next artifact...</Text>
        </div>
      ) : null}
    </div>
  );
}
