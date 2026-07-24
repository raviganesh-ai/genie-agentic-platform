import { describe, expect, it } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useDecisionGraph } from "@/hooks/useDecisionGraph";
import { buildDecisionGraph } from "./fixtures";

describe("useDecisionGraph", () => {
  it("selects a node and clears the edge selection", () => {
    const graph = buildDecisionGraph();
    const { result } = renderHook(() => useDecisionGraph(graph));

    expect(result.current.selectedNode).toBeNull();

    act(() => result.current.selectNode("node-2"));
    expect(result.current.selectedNode?.label).toBe("Recommendation A");
    expect(result.current.selectedEdge).toBeNull();
  });

  it("selects an edge and clears the node selection", () => {
    const graph = buildDecisionGraph();
    const { result } = renderHook(() => useDecisionGraph(graph));

    act(() => result.current.selectNode("node-1"));
    act(() => result.current.selectEdge("edge-1"));

    expect(result.current.selectedEdge?.edge_type).toBe("agent_to_agent");
    expect(result.current.selectedNode).toBeNull();
  });

  it("clears selection entirely", () => {
    const graph = buildDecisionGraph();
    const { result } = renderHook(() => useDecisionGraph(graph));

    act(() => result.current.selectNode("node-1"));
    act(() => result.current.clearSelection());

    expect(result.current.selectedNode).toBeNull();
    expect(result.current.selectedEdge).toBeNull();
  });
});
