import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/services/httpClient";
import type { SafeError } from "@/types/common";

export interface AsyncResourceState<T> {
  data: T | null;
  loading: boolean;
  error: SafeError | null;
  refresh: () => void;
}

export interface AsyncResourceOptions {
  /**
   * Polling interval in milliseconds. When omitted or 0, no polling occurs
   * (single fetch + manual refresh only). Callers must pass this in from
   * configuration (e.g. import.meta.env) - never hardcode a production
   * polling interval in page/component source.
   */
  pollIntervalMs?: number;
  enabled?: boolean;
}

/**
 * Shared internal data-fetching hook used by every domain-specific hook
 * (useArchitectureStudio, useRequirementDiscovery, etc.). Not part of the
 * user-specified hook list, but factors out identical loading/error/refresh/
 * polling logic so each domain hook stays a thin, readable wrapper around
 * its API client.
 */
export function useAsyncResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  options: AsyncResourceOptions = {},
): AsyncResourceState<T> {
  const { pollIntervalMs = 0, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<SafeError | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [refreshToken, setRefreshToken] = useState(0);

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err
              : { message: "An unexpected error occurred. Please try again." },
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();

    let intervalId: ReturnType<typeof setInterval> | undefined;
    if (pollIntervalMs > 0) {
      intervalId = setInterval(() => void load(), pollIntervalMs);
    }

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, pollIntervalMs, refreshToken, ...deps]);

  return { data, loading, error, refresh };
}
