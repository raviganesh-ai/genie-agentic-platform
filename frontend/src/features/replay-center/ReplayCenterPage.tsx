import { useNavigate } from "react-router-dom";
import { Badge, Button, Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useReplay } from "@/hooks/useReplay";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import type { ReplayTimelineEntry } from "@/types/replay";

const KIND_LABELS: Record<ReplayTimelineEntry["kind"], string> = {
  governance: "Governance",
  approval: "Approval",
  lineage: "Recommendation",
};

const KIND_META: Record<ReplayTimelineEntry["kind"], { icon: string; accent: string }> = {
  governance: { icon: "📜", accent: "#2f83e0" },
  approval: { icon: "✅", accent: "#c98a2c" },
  lineage: { icon: "🧩", accent: "#8a63d2" },
};

export function ReplayCenterPage(): JSX.Element {
  const navigate = useNavigate();
  const { sessionId } = useSessionContext();
  const { data, loading, error, refresh } = useReplay(sessionId);

  if (!sessionId) {
    return (
      <div>
        <PageHeader title="Replay Center" subtitle="No active session yet." />
        <Button appearance="primary" onClick={() => navigate("/")}>
          Start a session
        </Button>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Replay Center"
        subtitle="Step-by-step reconstruction of every governed decision in this session."
      />
      {loading && !data ? <LoadingState label="Loading replay..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      {data ? (
        <SectionCard
          title="Replay-Ready Timeline"
          action={<Badge appearance="tint">{data.timeline.length} events</Badge>}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 520, overflowY: "auto" }}>
            {data.timeline.length === 0 ? (
              <Text size={300} style={{ opacity: 0.7 }}>
                No replay events recorded yet for this session.
              </Text>
            ) : (
              data.timeline.map((entry) => {
                const meta = KIND_META[entry.kind];
                return (
                  <div key={entry.id} style={{ borderLeft: `2px solid ${meta.accent}`, paddingLeft: 10 }}>
                    <Text size={200} style={{ opacity: 0.6, display: "block" }}>
                      {new Date(entry.timestamp).toLocaleString()} · {KIND_LABELS[entry.kind]}
                      {entry.agentId ? ` · ${entry.agentId}` : ""}
                    </Text>
                    <Text size={300}>
                      {meta.icon} {entry.label}
                    </Text>
                  </div>
                );
              })
            )}
          </div>
        </SectionCard>
      ) : null}
    </div>
  );
}
