import { useMemo } from "react";
import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import type { DecisionGraph, DecisionNodeType } from "@/types/collaboration";

const ROW_Y: Record<DecisionNodeType, number> = {
  agent: 20,
  recommendation: 160,
  approval: 300,
  memory_record: 440,
  evidence: 580,
};

const NODE_COLOR: Record<DecisionNodeType, string> = {
  agent: "#2f83e0",
  recommendation: "#5aa16c",
  approval: "#c98a2c",
  memory_record: "#8a63d2",
  evidence: "#5c6572",
};

/**
 * Renders the session's real ``DecisionGraph`` (agent nodes, recommendation
 * nodes, approval nodes, memory/evidence nodes, and the edges connecting
 * them) as an actual node/edge diagram - a genuine visual representation of
 * the UI and Agent Flow for this mission, not just a text list. Nodes are
 * grouped into rows by type since the backend does not compute layout
 * positions itself.
 */
export function ArchitectureFlowGraph({
  graph,
  onNodeClick,
  onEdgeClick,
}: {
  graph: DecisionGraph | null;
  onNodeClick?: (nodeId: string) => void;
  onEdgeClick?: (edgeId: string) => void;
}): JSX.Element {
  const { nodes, edges } = useMemo(() => {
    if (!graph || graph.nodes.length === 0) {
      return { nodes: [] as Node[], edges: [] as Edge[] };
    }
    const columnByType: Partial<Record<DecisionNodeType, number>> = {};
    const builtNodes: Node[] = graph.nodes.map((node) => {
      const column = columnByType[node.node_type] ?? 0;
      columnByType[node.node_type] = column + 1;
      return {
        id: node.id,
        position: { x: column * 220, y: ROW_Y[node.node_type] },
        data: { label: node.label },
        style: {
          borderRadius: 8,
          border: `2px solid ${NODE_COLOR[node.node_type]}`,
          background: "#151b23",
          color: "#e6e9ee",
          fontSize: 12,
          padding: 8,
          width: 190,
        },
      };
    });
    const builtEdges: Edge[] = graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source_id,
      target: edge.target_id,
      label: edge.edge_type.replace(/_/g, " "),
      animated: true,
      style: { stroke: "#3a4552" },
      labelStyle: { fill: "#c7cdd6", fontSize: 10 },
    }));
    return { nodes: builtNodes, edges: builtEdges };
  }, [graph]);

  if (nodes.length === 0) {
    return (
      <div style={{ opacity: 0.7, fontSize: 13, padding: 12 }}>
        No agent flow graph recorded yet for this mission.
      </div>
    );
  }

  return (
    <div style={{ height: 380, border: "1px solid #232a33", borderRadius: 8 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        onNodeClick={onNodeClick ? (_, node) => onNodeClick(node.id) : undefined}
        onEdgeClick={onEdgeClick ? (_, edge) => onEdgeClick(edge.id) : undefined}
      >
        <Background color="#232a33" gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
