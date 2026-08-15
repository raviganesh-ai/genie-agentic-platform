/**
 * Lightweight, heuristic parsers that turn an agent's free-text output into
 * structured, renderable pieces (named sections, fenced code blocks) so
 * pages can show a real diagram/checklist/timeline instead of one big
 * text box. These are presentational conveniences only - the underlying
 * source of truth is always the exact agent output text; nothing here
 * fabricates content, it only re-shapes what the agent actually returned.
 */

export interface ParsedSection {
  title: string;
  body: string;
}

/**
 * Detail-field labels an agent sometimes uses to break a single bulleted
 * item (most commonly one agent's responsibility in a "Multi-Agent
 * Workflow" list) out into its own sub-bullets - e.g. "Fulfills:",
 * "Inputs:", "Outputs:", "Handoffs:" - even when it doesn't actually
 * indent them under the parent bullet. These must always stay part of
 * the item they describe rather than becoming their own sibling section,
 * so they're recognized and folded into the current section's body no
 * matter how they're indented.
 */
const DETAIL_FIELD_LABEL =
  /^(?:responsibilit(?:y|ies)|fulfills?|inputs?|outputs?|handoffs?|receives?(?: from)?)$/i;

/**
 * Splits free text into named sections using common patterns an LLM uses
 * when asked to describe "each component"/"each item" with rationale:
 * markdown headers (`#`/`##`/`###`), bold labels (`**Name**:`), or a
 * numbered/bulleted list whose first line is a short label followed by a
 * colon. Falls back to an empty list (caller should render the raw text
 * instead) when nothing recognizable is found.
 */
export function splitIntoNamedSections(text: string): ParsedSection[] {
  const lines = text.split(/\r?\n/);
  const sections: ParsedSection[] = [];
  let currentTitle: string | null = null;
  let currentBody: string[] = [];

  const flush = () => {
    if (currentTitle && currentTitle.trim().length > 0) {
      sections.push({ title: currentTitle.trim(), body: currentBody.join("\n").trim() });
    }
  };

  for (const rawLine of lines) {
    // An indented line is always a sub-item nested under whatever
    // top-level bullet/header came before it (e.g. an agent's own
    // "Fulfills"/"Inputs"/"Outputs"/"Handoffs" detail bullets) - it must
    // never start a new top-level section itself, even though on its own
    // it can look exactly like a header/bold-label/numbered-bullet line.
    // Only lines flush at column 0 are real section boundaries.
    const isIndented = /^[ \t]+\S/.test(rawLine);
    const line = rawLine.trim();
    const headerMatch = isIndented
      ? null
      : (line.match(/^#{1,4}\s+(.+)$/) ??
        line.match(/^\*\*(.+?)\*\*:?\s*(.*)$/) ??
        line.match(/^(?:[-*]|\d+[.)])\s+\*?\*?([^:\n]{2,70}?)\*?\*?:\s*(.*)$/));

    // Even an unindented line can still just be a detail field describing
    // the previous bullet (an agent not indenting "Fulfills:"/"Inputs:"/
    // etc. under its own name) - treat it the same as an indented one:
    // fold into the current section instead of starting a new one.
    if (headerMatch && currentTitle !== null && DETAIL_FIELD_LABEL.test(headerMatch[1].trim())) {
      currentBody.push(rawLine);
      continue;
    }

    if (headerMatch) {
      flush();
      currentTitle = headerMatch[1];
      currentBody = headerMatch[2] ? [headerMatch[2]] : [];
      continue;
    }
    if (currentTitle !== null) {
      currentBody.push(rawLine);
    }
  }
  flush();

  return sections;
}

export interface TopLevelSection {
  title: string;
  /** Free text directly under the header, before its first bullet item. */
  summary: string;
  /** Everything under the header (summary + bullets) - feed this into
   * `splitIntoNamedSections` to get one card per bullet item. */
  body: string;
}

/**
 * Splits text into its top-level `## <Title>` sections - used for prompts
 * (e.g. architecture-recommendation-v1) that contract to return a small,
 * fixed set of named top-level sections (each optionally containing its
 * own bulleted sub-items). Unlike `splitIntoNamedSections`, this only
 * recognizes `##`-style markdown headers, so nested bullet items stay
 * part of their parent section's `body` instead of becoming their own
 * top-level entries.
 */
export function splitTopLevelSections(text: string): TopLevelSection[] {
  const lines = text.split(/\r?\n/);
  const sections: TopLevelSection[] = [];
  let currentTitle: string | null = null;
  let currentLines: string[] = [];

  const flush = () => {
    if (currentTitle && currentTitle.trim().length > 0) {
      const firstBulletIndex = currentLines.findIndex((line) =>
        /^\s*(?:[-*]|\d+[.)])\s+/.test(line),
      );
      const summaryLines = firstBulletIndex === -1 ? currentLines : currentLines.slice(0, firstBulletIndex);
      sections.push({
        title: currentTitle.trim(),
        summary: summaryLines.join("\n").trim(),
        body: currentLines.join("\n").trim(),
      });
    }
  };

  for (const rawLine of lines) {
    const headerMatch = rawLine.match(/^##\s+(.+)$/);
    if (headerMatch) {
      flush();
      currentTitle = headerMatch[1];
      currentLines = [];
      continue;
    }
    if (currentTitle !== null) {
      currentLines.push(rawLine);
    }
  }
  flush();

  return sections;
}

export interface UiScreenFlow {
  screen: string;
  orchestrator: string;
  description: string;
}

/**
 * Parses the architecture-recommendation-v1 UI Design section's bullet
 * contract - each line shaped like
 * `- **<Screen name>** --> **<Orchestrator Agent name>**: <description>` -
 * into structured `{screen, orchestrator, description}` entries, so the UI
 * can render an actual hub-and-spoke flow diagram (every screen fanning
 * into the single Orchestrator Agent) instead of raw prose. Returns an
 * empty array (caller should fall back to raw/diagrammed text) when no
 * line matches this exact contract.
 */
export function parseUiScreenFlows(text: string): UiScreenFlow[] {
  const flows: UiScreenFlow[] = [];
  const pattern = /^[-*]\s*\*\*(.+?)\*\*\s*-->\s*\*\*(.+?)\*\*\s*:\s*(.+)$/;
  for (const rawLine of text.split(/\r?\n/)) {
    const match = rawLine.trim().match(pattern);
    if (match) {
      flows.push({
        screen: match[1].trim(),
        orchestrator: match[2].trim(),
        description: match[3].trim(),
      });
    }
  }
  return flows;
}

export interface ParsedCodeBlock {
  language: string;
  code: string;
  /** Index, within the original text, immediately after this block's closing ``` fence. */
  endIndex: number;
}

/**
 * A ``` fence only counts when it OPENS ITS OWN LINE (optionally
 * indented). Generated code legitimately contains triple backticks inside
 * string literals - e.g. a Python agent that builds a markdown report with
 * `lines.append("```text")` - and treating those as real fences split the
 * block early, leaking the rest of the source into the narrative parser as
 * garbled fake sections. Anchoring to line start keeps such inline
 * backticks part of the code they belong to.
 */

/** Extracts every fenced ```lang ... ``` code block from free text. */
export function extractCodeBlocks(text: string): ParsedCodeBlock[] {
  const blocks: ParsedCodeBlock[] = [];
  const regex = /^[ \t]*```([a-zA-Z0-9_-]*)[ \t]*\r?\n([\s\S]*?)^[ \t]*```[ \t]*$/gm;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    blocks.push({
      language: match[1] || "text",
      code: match[2].trim(),
      endIndex: match.index + match[0].length,
    });
  }
  return blocks;
}

export interface OpenCodeBlock {
  language: string;
  /** The block's content typed so far (no closing fence yet). */
  code: string;
  /** Index, within the original text, where this block's opening ``` fence begins. */
  startIndex: number;
}

/**
 * While a component is still being generated, its fenced code block's
 * closing ``` hasn't streamed in yet - `extractCodeBlocks` correctly
 * ignores it (nothing to show as finished code yet), but naively treating
 * the rest of the text as narrative (`stripCodeBlocks` + `splitIntoNamedSections`)
 * would instead parse the raw, still-typing source code as bogus narrative
 * sections (e.g. a Python docstring line accidentally matching a header
 * pattern) - exactly the "noise" a live per-component build view must
 * avoid. Detects a trailing UNCLOSED fence (an odd number of ``` markers
 * in `text`) so the caller can exclude it from narrative parsing and show
 * a clean "generating this component" placeholder instead - see
 * `extractAgentLabel`, which only needs the block's first line and so
 * still works on this still-growing partial code.
 */
export function extractTrailingOpenCodeBlock(text: string): OpenCodeBlock | null {
  const fenceRegex = /^[ \t]*```([a-zA-Z0-9_-]*)[ \t]*(?:\r?\n|$)/gm;
  let match: RegExpExecArray | null;
  let count = 0;
  let openStart = -1;
  let openContentStart = -1;
  let openLanguage = "";
  while ((match = fenceRegex.exec(text)) !== null) {
    count += 1;
    if (count % 2 === 1) {
      openStart = match.index;
      openContentStart = match.index + match[0].length;
      openLanguage = match[1] || "text";
    }
  }
  if (count === 0 || count % 2 === 0) return null;
  return { language: openLanguage, code: text.slice(openContentStart), startIndex: openStart };
}

const AGENT_LABEL_COMMENT = /^\s*(?:\/\/|#)\s*agent:\s*(.+?)\s*$/i;

/**
 * Returns the `agent: <name>` label declared in a code block's first
 * line comment (`// agent: ui`, `# agent: orchestrator`, `# agent: <Agent
 * Name>`) - the build-generation-v1 prompt contract's way of identifying
 * which agent (or "ui") a given code block belongs to when a single
 * response contains many blocks (one UI block, one per specialist agent,
 * one for the Orchestrator Agent). Returns null when the code block
 * doesn't declare one (e.g. an older cached response), so callers can
 * fall back to a language-based label instead.
 */
export function extractAgentLabel(code: string): string | null {
  const firstLine = code.split(/\r?\n/, 1)[0] ?? "";
  const match = firstLine.match(AGENT_LABEL_COMMENT);
  return match ? match[1].trim() : null;
}

/**
 * Returns true once `text` contains a fully-closed `# agent: ui` /
 * `// agent: ui` code block - the UI component is always generated LAST
 * (see the backend's `_generate_build_by_component`: each specialist
 * agent -> the Orchestrator Agent -> the UI), so its closing fence
 * appearing means every real component has finished streaming, even
 * while genie-orchestrator's own outer Foundry run is still separately
 * regenerating a verbatim echo of the same text as its "final answer"
 * (see `WorkflowStepExecutor._run_agent` - that echo is what eventually
 * populates a workflow step's own `output_text`/marks it "completed"
 * server-side, which can take substantially longer than the real
 * generation this checks for). Callers that only need to know "is the
 * actual generated content done" (e.g. gating a review-and-approve
 * action) should use this instead of waiting for the step to be marked
 * completed server-side.
 */
export function isBuildOutputComplete(text: string): boolean {
  const openBlock = extractTrailingOpenCodeBlock(text);
  const closedText = openBlock ? text.slice(0, openBlock.startIndex) : text;
  return extractCodeBlocks(closedText).some(
    (block) => extractAgentLabel(block.code)?.toLowerCase() === "ui",
  );
}

/** Returns the text with every fenced code block removed (surrounding whitespace collapsed). */
export function stripCodeBlocks(text: string): string {
  return text
    .replace(/^[ \t]*```[a-zA-Z0-9_-]*[ \t]*\r?\n[\s\S]*?^[ \t]*```[ \t]*$/gm, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const MAX_OUTPUT_SUMMARY_LENGTH = 260;

/**
 * Produces a short, human-readable summary of an agent's real output text
 * for trace/timeline UIs (e.g. the Triage panel's mission trace) - never
 * dumps raw fenced code. A trailing still-open fence (an in-progress
 * step's partial output) is excluded first, same as `isBuildOutputComplete`.
 * When the (now-closed-only) text contains fenced code blocks - e.g.
 * build-solution's generated components - collapses them into a
 * "Generated N code artifact(s): <agent labels>" line, appended after any
 * narrative text that preceded them; otherwise returns the narrative text
 * itself, hard-truncated. Never fabricates content - only re-shapes what
 * the agent actually returned.
 */
export function summarizeAgentOutput(text: string): string {
  const openBlock = extractTrailingOpenCodeBlock(text);
  const closedText = openBlock ? text.slice(0, openBlock.startIndex) : text;
  const narrative = stripCodeBlocks(closedText);
  const codeBlocks = extractCodeBlocks(closedText);

  const truncate = (value: string, max: number): string =>
    value.length > max ? `${value.slice(0, max).trimEnd()}...` : value;

  if (codeBlocks.length === 0) {
    return narrative.length > 0 ? truncate(narrative, MAX_OUTPUT_SUMMARY_LENGTH) : "(no textual output)";
  }

  const labels = Array.from(
    new Set(codeBlocks.map((block) => extractAgentLabel(block.code) ?? block.language)),
  );
  const codeSummary = `Generated ${codeBlocks.length} code artifact${codeBlocks.length === 1 ? "" : "s"}${
    labels.length > 0 ? `: ${labels.join(", ")}` : ""
  }`;
  return narrative.length > 0 ? `${truncate(narrative, 160)} — ${codeSummary}` : codeSummary;
}
