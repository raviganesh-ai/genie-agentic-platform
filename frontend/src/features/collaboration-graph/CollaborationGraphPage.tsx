import { useMemo } from "react";
import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { Text } from "@fluentui/react-components";
import { useSessionContext } from "@/state/SessionContext";
import { useCollaborationGraph } from "@/hooks/useCollaborationGraph";
import { useDecisionGraph } from "@/hooks/useDecisionGraph";
import { PageHeader } from "@/layouts/AppShell";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { SectionCard } from "@/components/SectionCard";
import type { DecisionGraph } from "@/types/collaboration";

const NODE_COLORS: Record<string, string> = {
  agent: "#2f83e0",
  recommendation: "#4e93e5",
  approval: "#d8a325",
  memory_record: "#3fa66a",
  evidence: "#8a8f98",
};

function toFlowElements(graph: DecisionGraph): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = graph.nodes.map((node, index) => ({
    id: node.id,
    data: { label: node.label },
    position: { x: (index % 6) * 200, y: Math.floor(index / 6) * 140 },
    style: {
      background: NODE_COLORS[node.node_type] ?? "#2f83e0",
      color: "#0b0f14",
      borderRadius: 8,
      fontSize: 12,
    },
  }));

  const edges: Edge[] = graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source_id,
    target: edge.target_id,
    label: edge.edge_type.replace(/_/g, " "),
    animated: edge.edge_type === "agent_to_agent",
  }));

  return { nodes, edges };
}

const POLL_MS = Number(import.meta.env.VITE_COLLABORATION_GRAPH_POLL_MS ?? 5000);

export function CollaborationGraphPage(): JSX.Element {
  const { sessionId } = useSessionContext();
  const { data: graph, loading, error, refresh } = useCollaborationGraph(sessionId, POLL_MS);
  const inspector = useDecisionGraph(graph ?? null);

  const { nodes, edges } = useMemo(() => (graph ? toFlowElements(graph) : { nodes: [], edges: [] }), [graph]);

  return (
    <div>
      <PageHeader
        title="Collaboration Graph"
        subtitle="Agent-to-agent handoffs, recommendation and approval dependencies for this session."
      />
      {loading && !graph ? <LoadingState label="Loading collaboration graph..." /> : null}
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}

      {graph ? (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16 }}>
          <div style={{ height: 560, border: "1px solid #232a33", borderRadius: 8 }}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodeClick={(_, node) => inspector.selectNode(node.id)}
              onEdgeClick={(_, edge) => inspector.selectEdge(edge.id)}
              fitView
            >
              <Background />
              <Controls />
            </ReactFlow>
          </div>
          <SectionCard title="Inspector">
            {inspector.selectedNode ? (
              <div>
                <Text weight="semibold" style={{ display: "block" }}>
                  {inspector.selectedNode.label}
                </Text>
                <Text size={200} style={{ opacity: 0.7, display: "block", marginBottom: 8 }}>
                  {inspector.selectedNode.node_type}
                </Text>
                <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>
                  {JSON.stringify(inspector.selectedNode.metadata, null, 2)}
                </pre>
              </div>
            ) : inspector.selectedEdge ? (
              <div>
                <Text weight="semibold" style={{ display: "block" }}>
                  {inspector.selectedEdge.edge_type.replace(/_/g, " ")}
                </Text>
                <Text size={200} style={{ opacity: 0.7, display: "block", marginBottom: 8 }}>
                  {inspector.selectedEdge.source_id} → {inspector.selectedEdge.target_id}
                </Text>
                <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>
                  {JSON.stringify(inspector.selectedEdge.metadata, null, 2)}
                </pre>
              </div>
            ) : (
              <Text size={300} style={{ opacity: 0.7 }}>
                Select a node or edge to inspect its details.
              </Text>
            )}
          </SectionCard>
        </div>
      ) : null}
    </div>
  );
}
