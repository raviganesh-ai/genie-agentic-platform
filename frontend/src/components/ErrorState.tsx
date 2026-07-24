import { Button, MessageBar, MessageBarBody, MessageBarTitle } from "@fluentui/react-components";
import type { SafeError } from "@/types/common";

export function ErrorState({
  error,
  onRetry,
}: {
  error: SafeError;
  onRetry?: () => void;
}): JSX.Element {
  return (
    <MessageBar intent="error" layout="multiline">
      <MessageBarBody>
        <MessageBarTitle>Something went wrong</MessageBarTitle>
        {error.message}
        {error.correlationId ? (
          <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
            Correlation ID: {error.correlationId}
          </div>
        ) : null}
        {onRetry ? (
          <div style={{ marginTop: 8 }}>
            <Button size="small" onClick={onRetry}>
              Retry
            </Button>
          </div>
        ) : null}
      </MessageBarBody>
    </MessageBar>
  );
}
