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
    const line = rawLine.trim();
    const headerMatch =
      line.match(/^#{1,4}\s+(.+)$/) ??
      line.match(/^\*\*(.+?)\*\*:?\s*(.*)$/) ??
      line.match(/^(?:[-*]|\d+[.)])\s+\*?\*?([^:\n]{2,70}?)\*?\*?:\s*(.*)$/);

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

/** Extracts every fenced ```lang ... ``` code block from free text. */
export function extractCodeBlocks(text: string): ParsedCodeBlock[] {
  const blocks: ParsedCodeBlock[] = [];
  const regex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
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
  const fenceRegex = /```([a-zA-Z0-9_-]*)\n?/g;
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

/** Returns the text with every fenced code block removed (surrounding whitespace collapsed). */
export function stripCodeBlocks(text: string): string {
  return text
    .replace(/```[a-zA-Z0-9_-]*\n[\s\S]*?```/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
