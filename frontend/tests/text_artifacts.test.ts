import { describe, expect, it } from "vitest";
import { splitIntoNamedSections } from "@/utils/textArtifacts";

/**
 * Regression coverage for a real bug: the Architecture Designer agent
 * sometimes writes each agent's bullet with indented sub-bullets for its
 * responsibility fields (e.g. "Fulfills"/"Inputs"/"Outputs"/"Handoffs")
 * instead of one flowing sentence. Because splitIntoNamedSections used to
 * trim every line before checking whether it looked like a header, those
 * indented sub-bullets were indistinguishable from a genuine top-level
 * bullet and became their own sibling sections - producing a diagram node
 * (and an agent-exclusion checkbox) per label instead of per agent.
 */
describe("splitIntoNamedSections", () => {
  it("keeps indented sub-bullets nested in the parent section instead of splitting them out", () => {
    const text = [
      "- **Document Processing Orchestrator Agent**: coordinates the pipeline",
      "  - Fulfills: coordinates the end-to-end pipeline",
      "  - Inputs: uploaded document package",
      "  - Outputs: normalized record",
      "  - Handoffs: Ingestion & Packaging Agent",
      "- **Ingestion & Packaging Agent**: classifies and packages incoming files",
      "  - Fulfills: classifies and packages incoming files",
      "  - Inputs: raw uploaded files",
      "  - Outputs: packaged batch",
      "  - Handoffs: Classification & Routing Agent",
    ].join("\n");

    const sections = splitIntoNamedSections(text);

    expect(sections.map((section) => section.title)).toEqual([
      "Document Processing Orchestrator Agent",
      "Ingestion & Packaging Agent",
    ]);
    expect(sections[0].body).toContain("Fulfills: coordinates the end-to-end pipeline");
    expect(sections[0].body).toContain("Handoffs: Ingestion & Packaging Agent");
  });

  it("still splits genuine top-level bold-label bullets that aren't indented", () => {
    const text = ["- **Agent One**: does the first thing", "- **Agent Two**: does the second thing"].join("\n");

    const sections = splitIntoNamedSections(text);

    expect(sections.map((section) => section.title)).toEqual(["Agent One", "Agent Two"]);
    expect(sections[0].body).toBe("does the first thing");
    expect(sections[1].body).toBe("does the second thing");
  });

  it("folds detail-field labels (Fulfills/Inputs/Outputs/Handoffs) into the previous agent even when not indented", () => {
    const text = [
      "- **Document Processing Orchestrator Agent**: coordinates the pipeline",
      "- Fulfills: coordinates the end-to-end pipeline",
      "- Inputs: uploaded document package",
      "- Outputs: normalized record",
      "- Handoffs: Ingestion & Packaging Agent",
      "- **Ingestion & Packaging Agent**: classifies and packages incoming files",
      "- Fulfills: classifies and packages incoming files",
    ].join("\n");

    const sections = splitIntoNamedSections(text);

    expect(sections.map((section) => section.title)).toEqual([
      "Document Processing Orchestrator Agent",
      "Ingestion & Packaging Agent",
    ]);
    expect(sections[0].body).toContain("Fulfills: coordinates the end-to-end pipeline");
    expect(sections[0].body).toContain("Handoffs: Ingestion & Packaging Agent");
  });

  it("still splits markdown headers as before", () => {
    const text = ["## First", "some body", "## Second", "other body"].join("\n");

    const sections = splitIntoNamedSections(text);

    expect(sections.map((section) => section.title)).toEqual(["First", "Second"]);
  });
});
