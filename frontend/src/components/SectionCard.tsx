import type { ReactNode } from "react";
import { Card as FluentCard, CardHeader, Text } from "@fluentui/react-components";

export function SectionCard({
  title,
  action,
  children,
}: {
  title: string;
  action?: JSX.Element;
  children: ReactNode;
}): JSX.Element {
  return (
    <FluentCard
      className="genie-fade-in"
      style={{ padding: 16, marginBottom: 12, transition: "transform 150ms ease, box-shadow 150ms ease" }}
      onMouseEnter={(event) => {
        event.currentTarget.style.transform = "translateY(-2px)";
        event.currentTarget.style.boxShadow = "0 8px 24px rgba(0, 0, 0, 0.25)";
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.transform = "none";
        event.currentTarget.style.boxShadow = "none";
      }}
    >
      <CardHeader header={<Text weight="semibold">{title}</Text>} action={action} />
      <div style={{ marginTop: 12 }}>{children}</div>
    </FluentCard>
  );
}
