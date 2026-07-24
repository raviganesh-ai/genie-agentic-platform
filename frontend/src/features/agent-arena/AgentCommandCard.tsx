import { Badge, Text } from "@fluentui/react-components";
import { SectionCard } from "@/components/SectionCard";
import { AgentStatusBadge } from "@/components/StatusBadge";
import type { AgentArenaCard } from "@/hooks/useAgentArena";

/**
 * Agent Command Card: one agent's identity, live status, contribution
 * score, and earned achievement badges - the Agent Arena's core
 * "professional gamification" unit.
 */
export function AgentCommandCard({ card }: { card: AgentArenaCard }): JSX.Element {
  const { agent, status, contributionScore, achievements } = card;
  return (
    <SectionCard title={agent.name} action={<AgentStatusBadge status={status} />}>
      <Text size={300} style={{ display: "block", opacity: 0.8, marginBottom: 8 }}>
        {agent.role}
      </Text>
      <Text size={200} style={{ display: "block", opacity: 0.7, marginBottom: 12 }}>
        {agent.description}
      </Text>

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <Text weight="semibold" size={500}>
          {contributionScore}
        </Text>
        <Text size={200} style={{ opacity: 0.7 }}>
          contribution points
        </Text>
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {achievements
          .filter((a) => a.earned)
          .map((achievement) => (
            <Badge key={achievement.id} appearance="tint" color="brand">
              {achievement.label}
            </Badge>
          ))}
      </div>
    </SectionCard>
  );
}
