import { useSessionContext } from "@/state/SessionContext";
import { useAgentArena } from "@/hooks/useAgentArena";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { AgentCommandCard } from "./AgentCommandCard";

const POLL_MS = Number(import.meta.env.VITE_AGENT_ARENA_POLL_MS ?? 5000);

export function AgentArenaPage(): JSX.Element {
  const { sessionId } = useSessionContext();
  const { data, loading, error, refresh } = useAgentArena(sessionId, POLL_MS);

  return (
    <div>
      <PageHeader
        title="Agent Arena"
        subtitle="Live status, contribution, and achievements for every agent in this session."
      />
      {loading && !data ? <LoadingState label="Loading agents..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      {data ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 16,
          }}
        >
          {data.cards.map((card) => (
            <AgentCommandCard key={card.agent.id} card={card} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
