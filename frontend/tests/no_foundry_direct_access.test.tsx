import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "src");

function collectSourceFiles(dir: string): string[] {
  const entries = readdirSync(dir);
  const files: string[] = [];
  for (const entry of entries) {
    const fullPath = join(dir, entry);
    const stats = statSync(fullPath);
    if (stats.isDirectory()) {
      files.push(...collectSourceFiles(fullPath));
    } else if (/\.(ts|tsx)$/.test(entry)) {
      files.push(fullPath);
    }
  }
  return files;
}

/**
 * Enforces the architecture rule: "The React frontend must never call
 * Azure AI Foundry directly." Scans every non-comment line of `src/` for
 * an Azure AI Foundry SDK import or a direct call to a Foundry-shaped
 * hostname (`*.services.ai.azure.com`, `*.openai.azure.com`,
 * `*.cognitiveservices.azure.com`). Explanatory comments that merely
 * mention "Foundry" (e.g. documenting why a call is NOT made) are allowed;
 * only executable references are flagged.
 */
const FORBIDDEN_IMPORT_PATTERNS: RegExp[] = [
  /["']@azure\/ai-projects["']/,
  /["']azure-ai-projects["']/,
];

const FORBIDDEN_URL_PATTERNS: RegExp[] = [
  /services\.ai\.azure\.com/i,
  /openai\.azure\.com/i,
  /cognitiveservices\.azure\.com/i,
];

function stripComments(content: string): string {
  return content
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .map((line) => line.replace(/\/\/.*/, ""))
    .join("\n");
}

describe("no direct Azure AI Foundry access from the frontend", () => {
  it("never imports an Azure AI Foundry SDK or references a Foundry endpoint in executable code", () => {
    const files = collectSourceFiles(SRC_ROOT);
    const offenders: string[] = [];

    for (const file of files) {
      const executableContent = stripComments(readFileSync(file, "utf-8"));
      for (const pattern of [...FORBIDDEN_IMPORT_PATTERNS, ...FORBIDDEN_URL_PATTERNS]) {
        if (pattern.test(executableContent)) {
          offenders.push(`${file} matches ${pattern}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it("routes every HTTP call through the shared Genie httpClient module", () => {
    const files = collectSourceFiles(SRC_ROOT).filter(
      (file) => !file.endsWith(join("services", "httpClient.ts")),
    );
    const offenders: string[] = [];

    for (const file of files) {
      const executableContent = stripComments(readFileSync(file, "utf-8"));
      if (/\bfetch\s*\(/.test(executableContent)) {
        offenders.push(file);
      }
    }

    expect(offenders).toEqual([]);
  });
});
