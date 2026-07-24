import { useCallback, useMemo, useState } from "react";
import type { DecisionEdge, DecisionGraph, DecisionNode } from "@/types/collaboration";

export interface DecisionGraphInspector {
  selectedNode: DecisionNode | null;
  selectedEdge: DecisionEdge | null;
  selectNode: (nodeId: string | null) => void;
  selectEdge: (edgeId: string | null) => void;
  clearSelection: () => void;
}

/**
 * UI-state hook for the click-to-inspect Decision Graph Explorer (used by
 * both the Collaboration Graph page and the Requirement/Architecture
 * decision graphs, since all three render the same `DecisionGraph` shape).
 * Deliberately holds no server data of its own - it operates on whichever
 * `DecisionGraph` the caller already fetched.
 */
export function useDecisionGraph(graph: DecisionGraph | null): DecisionGraphInspector {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);

  const selectedNode = useMemo(
    () => graph?.nodes.find((n) => n.id === selectedNodeId) ?? null,
    [graph, selectedNodeId],
  );
  const selectedEdge = useMemo(
    () => graph?.edges.find((e) => e.id === selectedEdgeId) ?? null,
    [graph, selectedEdgeId],
  );

  const selectNode = useCallback((nodeId: string | null) => {
    setSelectedNodeId(nodeId);
    setSelectedEdgeId(null);
  }, []);

  const selectEdge = useCallback((edgeId: string | null) => {
    setSelectedEdgeId(edgeId);
    setSelectedNodeId(null);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }, []);

  return { selectedNode, selectedEdge, selectNode, selectEdge, clearSelection };
}
