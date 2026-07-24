import { Spinner } from "@fluentui/react-components";

export function LoadingState({ label = "Loading..." }: { label?: string }): JSX.Element {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: 24 }}>
      <Spinner size="small" />
      <span>{label}</span>
    </div>
  );
}
