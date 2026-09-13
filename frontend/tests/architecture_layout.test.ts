import { describe, expect, it } from "vitest";
import {
  ARCHITECTURE_NODE_HEIGHT,
  ARCHITECTURE_NODE_WIDTH,
  layoutArchitecture,
} from "@/features/discovery/architectureLayout";
import type { ArchitectureEdge, ArchitectureNode } from "@/types/discovery";

describe("layoutArchitecture", () => {
  it("creates a left-to-right Azure topology without overlapping service nodes", () => {
    const nodes: ArchitectureNode[] = Array.from({ length: 9 }, (_, index) => ({
      id: `service-${index}`,
      service_name: `Azure Service ${index}`,
      azure_icon_key: "azure app service",
      purpose: `Service purpose ${index}`,
      x: 0,
      y: 0,
    }));
    const edges: ArchitectureEdge[] = [
      { id: "e-0-1", source: "service-0", target: "service-1", label: "Routes" },
      { id: "e-0-2", source: "service-0", target: "service-2", label: "Routes" },
      { id: "e-1-3", source: "service-1", target: "service-3", label: "Calls" },
      { id: "e-1-4", source: "service-1", target: "service-4", label: "Calls" },
      { id: "e-2-5", source: "service-2", target: "service-5", label: "Calls" },
      { id: "e-3-6", source: "service-3", target: "service-6", label: "Emits" },
      { id: "e-4-7", source: "service-4", target: "service-7", label: "Stores" },
      { id: "e-5-8", source: "service-5", target: "service-8", label: "Observes" },
    ];

    const positions = layoutArchitecture(nodes, edges);
    const positionsWithDifferentModelCoordinates = layoutArchitecture(
      nodes.map((node, index) => ({ ...node, x: index * 10_000, y: -index * 10_000 })),
      edges,
    );

    expect(positionsWithDifferentModelCoordinates).toEqual(positions);

    for (let leftIndex = 0; leftIndex < positions.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < positions.length; rightIndex += 1) {
        const left = positions[leftIndex];
        const right = positions[rightIndex];
        const overlapsHorizontally = left.x < right.x + ARCHITECTURE_NODE_WIDTH
          && left.x + ARCHITECTURE_NODE_WIDTH > right.x;
        const overlapsVertically = left.y < right.y + ARCHITECTURE_NODE_HEIGHT
          && left.y + ARCHITECTURE_NODE_HEIGHT > right.y;
        expect(overlapsHorizontally && overlapsVertically).toBe(false);
      }
    }

    const byId = new Map(positions.map((position) => [position.id, position]));
    edges.forEach((edge) => {
      expect(byId.get(edge.target)!.x).toBeGreaterThan(byId.get(edge.source)!.x);
    });
  });
});