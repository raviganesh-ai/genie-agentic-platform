import { useMemo, useState } from "react";
import { Text } from "@fluentui/react-components";

export interface FlowDiagramNode {
  id: string;
  title: string;
  icon: string;
  description: string;
}

interface InteractiveFlowDiagramProps {
  /**
   * When provided, renders a hub-and-spoke layout (one source fanning out
   * to many targets - e.g. Orchestrator -> individual agents). Omit for a
   * simple left-to-right chain (e.g. UI -> Agentic Workflow).
   */
  hub?: FlowDiagramNode;
  nodes: FlowDiagramNode[];
  emptyLabel?: string;
}

function FlowCard({
  node,
  isActive,
  isHub,
  showDescription,
  onHover,
  onLeave,
}: {
  node: FlowDiagramNode;
  isActive: boolean;
  isHub?: boolean;
  showDescription?: boolean;
  onHover: () => void;
  onLeave: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      className={`genie-flow-node${isActive ? " genie-flow-node-active" : ""}${isHub ? " genie-flow-hub" : ""}`}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
      onFocus={onHover}
      onBlur={onLeave}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 6,
        width: isHub ? 172 : showDescription ? 220 : 156,
        padding: isHub ? "18px 12px" : "14px 10px",
        borderRadius: 12,
        border: "1px solid #232a33",
        backgroundColor: "#161c24",
        color: "#e6e9ee",
        textAlign: "center",
        fontFamily: "inherit",
        flexShrink: 0,
      }}
    >
      <span style={{ fontSize: isHub ? 30 : 26 }} aria-hidden="true">
        {node.icon}
      </span>
      <Text size={200} weight="semibold" style={{ lineHeight: 1.25 }}>
        {node.title}
      </Text>
      {showDescription && node.description ? (
        <Text size={100} style={{ lineHeight: 1.3, opacity: 0.75 }}>
          {node.description}
        </Text>
      ) : null}
    </button>
  );
}

function Connector({ active, vertical }: { active: boolean; vertical?: boolean }): JSX.Element {
  return (
    <div
      style={{
        position: "relative",
        flex: vertical ? "none" : 1,
        width: vertical ? 3 : undefined,
        height: vertical ? "100%" : 20,
        minWidth: vertical ? undefined : 36,
      }}
    >
      <div className={`genie-flow-connector${active ? " genie-flow-connector-active" : ""}`} />
      {active ? <div className="genie-flow-particle" /> : null}
    </div>
  );
}

/**
 * A small, self-contained, hover-driven flow diagram: hovering (or
 * keyboard-focusing) any node highlights it, animates its connector, and
 * reveals that component's description in the detail panel below - so
 * scrubbing across the diagram surfaces what each piece is about instead
 * of a static, all-text-at-once layout. Falls back to the first node's
 * description when nothing is currently hovered/focused.
 */
export function InteractiveFlowDiagram({ hub, nodes, emptyLabel }: InteractiveFlowDiagramProps): JSX.Element {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const allNodes = useMemo(() => (hub ? [hub, ...nodes] : nodes), [hub, nodes]);
  // Default the highlighted node to the first spoke (e.g. the first UI
  // screen) rather than the hub, so the detail panel below leads with
  // what the user is looking at (a screen) instead of always defaulting
  // to the Orchestrator Agent's own description.
  const activeId = hoveredId ?? nodes[0]?.id ?? hub?.id ?? null;
  const activeNode = allNodes.find((node) => node.id === activeId) ?? null;

  if (allNodes.length === 0) {
    return (
      <Text size={300} style={{ opacity: 0.7 }}>
        {emptyLabel ?? "Nothing to show yet."}
      </Text>
    );
  }

  return (
    <div>
      {hub ? (
        <div style={{ display: "flex", alignItems: "center" }}>
          <FlowCard
            node={hub}
            isActive={activeId === hub.id}
            isHub
            onHover={() => setHoveredId(hub.id)}
            onLeave={() => setHoveredId(null)}
          />
          <Connector active={hoveredId === null || hoveredId === hub.id} />
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 14,
              borderLeft: "2px dashed rgba(47, 131, 224, 0.35)",
              paddingLeft: 0,
            }}
          >
            {nodes.map((node) => {
              const isActive = activeId === node.id;
              return (
                <div key={node.id} style={{ display: "flex", alignItems: "center" }}>
                  <div style={{ width: 28, position: "relative", height: 3 }}>
                    <div className={`genie-flow-connector${isActive ? " genie-flow-connector-active" : ""}`} />
                    {isActive ? <div className="genie-flow-particle" /> : null}
                  </div>
                  <FlowCard
                    node={node}
                    isActive={isActive}
                    showDescription
                    onHover={() => setHoveredId(node.id)}
                    onLeave={() => setHoveredId(null)}
                  />
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", rowGap: 16 }}>
          {nodes.map((node, index) => {
            const isActive = activeId === node.id;
            return (
              <div key={node.id} style={{ display: "flex", alignItems: "center" }}>
                <FlowCard
                  node={node}
                  isActive={isActive}
                  onHover={() => setHoveredId(node.id)}
                  onLeave={() => setHoveredId(null)}
                />
                {index < nodes.length - 1 ? (
                  <Connector active={isActive || activeId === nodes[index + 1]?.id} />
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {activeNode ? (
        <div
          key={activeNode.id}
          className="genie-fade-in genie-flow-detail-panel"
          style={{
            marginTop: 18,
            borderLeft: "3px solid #2f83e0",
            paddingLeft: 12,
            backgroundColor: "rgba(47, 131, 224, 0.06)",
            borderRadius: "0 8px 8px 0",
            padding: "10px 14px",
          }}
        >
          <Text weight="semibold" size={300} style={{ display: "block", marginBottom: 4 }}>
            {activeNode.icon} {activeNode.title}
          </Text>
          <Text size={300} style={{ opacity: 0.85 }}>
            {activeNode.description}
          </Text>
        </div>
      ) : null}
    </div>
  );
}
