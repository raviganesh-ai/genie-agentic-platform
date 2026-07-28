import type { ReactNode } from "react";
import { Card as FluentCard, CardHeader, Text } from "@fluentui/react-components";

export function SectionCard({
  title,
  action,
  children,
  highlight,
}: {
  title: ReactNode;
  action?: JSX.Element;
  children: ReactNode;
  /** Wraps the card in an animated brand-gradient glow border - reserved
   * for the handful of sections that should visually pop (e.g. Architecture
   * Studio's requirement-derived UI Design / Multi-Agent Workflow), not a
   * general-purpose emphasis toggle. */
  highlight?: boolean;
}): JSX.Element {
  return (
    <FluentCard
      className={`genie-fade-in${highlight ? " genie-section-card-highlight" : ""}`}
      style={{ padding: 16, marginBottom: 12, transition: "transform 150ms ease, box-shadow 150ms ease" }}
      onMouseEnter={(event) => {
        event.currentTarget.style.transform = "translateY(-2px)";
        if (!highlight) event.currentTarget.style.boxShadow = "0 8px 24px rgba(0, 0, 0, 0.25)";
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.transform = "none";
        if (!highlight) event.currentTarget.style.boxShadow = "none";
      }}
    >
      <CardHeader header={<Text weight="semibold">{title}</Text>} action={action} />
      <div style={{ marginTop: 12 }}>{children}</div>
    </FluentCard>
  );
}
