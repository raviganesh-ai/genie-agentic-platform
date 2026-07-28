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

export interface ParsedCodeBlock {
  language: string;
  code: string;
}

/** Extracts every fenced ```lang ... ``` code block from free text. */
export function extractCodeBlocks(text: string): ParsedCodeBlock[] {
  const blocks: ParsedCodeBlock[] = [];
  const regex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    blocks.push({ language: match[1] || "text", code: match[2].trim() });
  }
  return blocks;
}

/** Returns the text with every fenced code block removed (surrounding whitespace collapsed). */
export function stripCodeBlocks(text: string): string {
  return text
    .replace(/```[a-zA-Z0-9_-]*\n[\s\S]*?```/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
