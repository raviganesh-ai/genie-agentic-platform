import { Badge, MessageBar, MessageBarBody, MessageBarTitle, Text } from "@fluentui/react-components";
import { SectionCard } from "@/components/SectionCard";
import type { RequirementEvidenceStatus, RequirementFidelityReport } from "@/types/deployLaunch";

const FIDELITY_STATUS_LABELS: Record<RequirementFidelityReport["status"], string> = {
  pending: "Awaiting tests",
  testing: "Testing",
  repairing: "Repairing gaps",
  passed: "100% verified",
  failed: "Blocked",
};

const EVIDENCE_STATUS_COLORS: Record<RequirementEvidenceStatus, string> = {
  pending: "#8a8f98",
  covered: "#2f83e0",
  passed: "#3fa66a",
  failed: "#d1495b",
  missing: "#d99a2b",
};

function FidelityMetric({ label, value, tone }: { label: string; value: string; tone: string }): JSX.Element {
  return (
    <div style={{ minWidth: 150, flex: "1 1 150px", borderLeft: `3px solid ${tone}`, padding: "6px 10px" }}>
      <Text size={100} style={{ display: "block", opacity: 0.65, textTransform: "uppercase" }}>
        {label}
      </Text>
      <Text size={500} weight="bold" style={{ color: tone }}>
        {value}
      </Text>
    </div>
  );
}

/**
 * Renders the real, deterministic per-requirement executable-test coverage
 * and passing-evidence gate (backed by `RequirementFidelityReport`) -
 * shared between Deploy & Launch's inline view and the standalone
 * Requirement Fidelity Gate popup.
 */
export function RequirementFidelityDashboard({ report }: { report: RequirementFidelityReport }): JSX.Element {
  const statusColor =
    report.status === "passed"
      ? "#3fa66a"
      : report.status === "failed"
        ? "#d1495b"
        : report.status === "repairing"
          ? "#d99a2b"
          : "#2f83e0";
  return (
    <SectionCard
      title="Requirement Fidelity Gate"
      action={
        <Badge shape="rounded" style={{ backgroundColor: statusColor, color: "#0b0f14" }}>
          {FIDELITY_STATUS_LABELS[report.status]}
        </Badge>
      }
    >
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        <FidelityMetric
          label="Approved Requirements"
          value={String(report.total_requirements)}
          tone="#a3c4f3"
        />
        <FidelityMetric
          label="Executable Coverage"
          value={`${report.coverage_percent}%`}
          tone={report.coverage_percent === 100 ? "#3fa66a" : "#d99a2b"}
        />
        <FidelityMetric
          label="Passing Evidence"
          value={`${report.pass_percent}%`}
          tone={report.pass_percent === 100 ? "#3fa66a" : "#d1495b"}
        />
        <FidelityMetric
          label="Automatic Repairs"
          value={`${report.repair_attempts}/${report.max_repair_attempts}`}
          tone={report.status === "failed" ? "#d1495b" : "#2f83e0"}
        />
      </div>

      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: 720 }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "100px minmax(220px, 1.5fr) minmax(150px, 1fr) minmax(220px, 1.2fr)",
              gap: 12,
              padding: "8px 10px",
              borderBottom: "1px solid #313a46",
            }}
          >
            {['Requirement', 'Approved statement', 'Acceptance tests', 'Observed evidence'].map((label) => (
              <Text key={label} size={100} weight="semibold" style={{ opacity: 0.65, textTransform: "uppercase" }}>
                {label}
              </Text>
            ))}
          </div>
          {report.requirements.map((item) => (
            <div
              key={item.requirement_id}
              style={{
                display: "grid",
                gridTemplateColumns: "100px minmax(220px, 1.5fr) minmax(150px, 1fr) minmax(220px, 1.2fr)",
                gap: 12,
                padding: "10px",
                borderBottom: "1px solid #252d37",
                alignItems: "start",
              }}
            >
              <Text size={200} weight="bold" style={{ color: EVIDENCE_STATUS_COLORS[item.status] }}>
                {item.requirement_id}
              </Text>
              <Text size={200}>{item.statement}</Text>
              <Text size={100} style={{ fontFamily: "monospace", whiteSpace: "pre-wrap" }}>
                {item.test_names.length > 0 ? item.test_names.join("\n") : "Missing"}
              </Text>
              <Text size={200} style={{ color: EVIDENCE_STATUS_COLORS[item.status] }}>
                {item.evidence || item.status}
              </Text>
            </div>
          ))}
        </div>
      </div>

      {report.gaps.length > 0 ? (
        <MessageBar intent="error" layout="multiline" style={{ marginTop: 14 }}>
          <MessageBarBody>
            <MessageBarTitle>Launch blocked by requirement gaps</MessageBarTitle>
            {report.gaps.join("; ")}
          </MessageBarBody>
        </MessageBar>
      ) : null}
    </SectionCard>
  );
}
