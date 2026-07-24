import { SectionCard } from "@/components/SectionCard";
import { ConfidenceGauge } from "@/components/ConfidenceGauge";
import type { MissionControlSnapshot } from "@/types/missionControl";

/**
 * "Professional gamification": executive scoreboard of real, backend-provided
 * scores (business value / risk / readiness) rendered as confidence gauges -
 * no invented metrics.
 */
export function ExecutiveScoreboard({
  snapshot,
}: {
  snapshot: MissionControlSnapshot;
}): JSX.Element {
  return (
    <SectionCard title="Executive Scoreboard">
      <div style={{ display: "flex", gap: 24, justifyContent: "space-around" }}>
        <ConfidenceGauge value={snapshot.business_value_score} label="Business Value" />
        <ConfidenceGauge value={100 - snapshot.risk_score} label="Risk Confidence" />
        <ConfidenceGauge value={snapshot.readiness_score} label="Readiness" />
      </div>
    </SectionCard>
  );
}
