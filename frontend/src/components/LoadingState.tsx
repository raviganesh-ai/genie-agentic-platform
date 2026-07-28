import { Spinner, Text } from "@fluentui/react-components";

export function LoadingState({ label = "Loading..." }: { label?: string }): JSX.Element {
  return (
    <div className="genie-fade-in" style={{ display: "flex", alignItems: "center", gap: 12, padding: 24 }}>
      <Spinner size="small" />
      <Text size={300} style={{ opacity: 0.8 }}>
        {label}
      </Text>
    </div>
  );
}
