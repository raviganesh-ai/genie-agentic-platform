import { ProgressBar, Text } from "@fluentui/react-components";
import { SectionCard } from "@/components/SectionCard";
import type { MissionControlSnapshot } from "@/types/missionControl";

export function MissionProgressTracker({
  snapshot,
}: {
  snapshot: MissionControlSnapshot;
}): JSX.Element {
  const progress = Math.max(0, Math.min(100, snapshot.mission_progress));
  return (
    <SectionCard title="Mission Progress">
      <ProgressBar value={progress / 100} />
      <Text size={300} style={{ display: "block", marginTop: 8, opacity: 0.8 }}>
        {progress}% complete
        {snapshot.current_workflow_step ? ` · Current step: ${snapshot.current_workflow_step}` : ""}
      </Text>
    </SectionCard>
  );
}
