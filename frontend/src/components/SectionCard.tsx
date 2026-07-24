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
    <FluentCard style={{ padding: 16 }}>
      <CardHeader header={<Text weight="semibold">{title}</Text>} action={action} />
      <div style={{ marginTop: 12 }}>{children}</div>
    </FluentCard>
  );
}
