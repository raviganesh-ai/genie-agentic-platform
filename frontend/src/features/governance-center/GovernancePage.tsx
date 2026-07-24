import { Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useGovernanceTrace } from "@/hooks/useGovernanceTrace";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import { GovernanceStatusBadge } from "@/components/StatusBadge";

const POLL_MS = Number(import.meta.env.VITE_GOVERNANCE_POLL_MS ?? 5000);

export function GovernancePage(): JSX.Element {
  const { sessionId } = useSessionContext();
  const { data, loading, error, refresh } = useGovernanceTrace(sessionId, POLL_MS);

  return (
    <div>
      <PageHeader title="Governance Center" subtitle="Policy checks, authorization decisions, and compliance status." />
      {loading && !data ? <LoadingState label="Loading governance trace..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      {data ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Text weight="semibold" size={500}>
              Overall status
            </Text>
            <GovernanceStatusBadge state={data.complianceState} />
          </div>

          <SectionCard title="Governance Events Timeline">
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 360, overflowY: "auto" }}>
              {[...data.events]
                .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
                .map((event) => (
                  <div key={event.id} style={{ borderLeft: "2px solid #2f83e0", paddingLeft: 10 }}>
                    <Text size={200} style={{ opacity: 0.6, display: "block" }}>
                      {new Date(event.timestamp).toLocaleString()}
                      {event.agent_id ? ` · ${event.agent_id}` : ""}
                    </Text>
                    <Text size={300}>{event.category.replace(/_/g, " ")}</Text>
                  </div>
                ))}
              {data.events.length === 0 ? (
                <Text size={300} style={{ opacity: 0.7 }}>
                  No governance events recorded yet.
                </Text>
              ) : null}
            </div>
          </SectionCard>

          <SectionCard title="Approvals">
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {data.approvals.map((approval) => (
                <Text key={approval.id} size={300}>
                  {approval.subject_type.replace(/_/g, " ")} · {approval.subject_id} ·{" "}
                  {approval.status}
                </Text>
              ))}
              {data.approvals.length === 0 ? (
                <Text size={300} style={{ opacity: 0.7 }}>
                  No approvals recorded yet.
                </Text>
              ) : null}
            </div>
          </SectionCard>
        </div>
      ) : null}
    </div>
  );
}
