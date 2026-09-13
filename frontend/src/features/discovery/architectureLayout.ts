import dagre from "@dagrejs/dagre";
import type { ArchitectureEdge, ArchitectureNode } from "@/types/discovery";

export const ARCHITECTURE_NODE_WIDTH = 228;
export const ARCHITECTURE_NODE_HEIGHT = 118;

export interface ArchitectureNodePosition {
  id: string;
  x: number;
  y: number;
}

export function layoutArchitecture(
  nodes: ArchitectureNode[],
  edges: ArchitectureEdge[],
): ArchitectureNodePosition[] {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: "LR",
    nodesep: 56,
    ranksep: 112,
    marginx: 32,
    marginy: 32,
  });

  nodes.forEach((node) => {
    graph.setNode(node.id, {
      width: ARCHITECTURE_NODE_WIDTH,
      height: ARCHITECTURE_NODE_HEIGHT,
    });
  });
  edges.forEach((edge) => {
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
      graph.setEdge(edge.source, edge.target);
    }
  });
  dagre.layout(graph);

  return nodes.map((node) => {
    const position = graph.node(node.id) as { x: number; y: number };
    return {
      id: node.id,
      x: position.x - ARCHITECTURE_NODE_WIDTH / 2,
      y: position.y - ARCHITECTURE_NODE_HEIGHT / 2,
    };
  });
}