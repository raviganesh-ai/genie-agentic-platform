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
 * Guards against invented/mock production data creeping into `src/`. Per
 * the build instructions: "Allowed: ... test data inside frontend/tests
 * only" - anything resembling seeded demo/mock business content belongs in
 * frontend/tests fixtures, never in application source.
 */
const FORBIDDEN_PATTERNS: RegExp[] = [
  /\bmockSession(s)?\b/i,
  /\bdemoData\b/i,
  /\bfakeAgent(s)?\b/i,
  /\bsampleCustomer\b/i,
  /\bAcme\s?Corp\b/i,
  /\bJohn\s?Doe\b/i,
];

describe("no hardcoded demo data in application source", () => {
  it("does not reference mock/demo/sample business data outside of tests", () => {
    const files = collectSourceFiles(SRC_ROOT);
    const offenders: string[] = [];

    for (const file of files) {
      const content = readFileSync(file, "utf-8");
      for (const pattern of FORBIDDEN_PATTERNS) {
        if (pattern.test(content)) {
          offenders.push(`${file} matches ${pattern}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });
});
