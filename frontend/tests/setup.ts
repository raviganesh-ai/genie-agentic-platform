import "@testing-library/jest-dom/vitest";

/**
 * jsdom does not implement ResizeObserver, but both Recharts
 * (ConfidenceGauge) and React Flow (CollaborationGraphPage) rely on it to
 * measure their containers. Without this polyfill, mounting those
 * components throws an uncaught `ResizeObserver is not defined` error that
 * unmounts the whole test render tree.
 */
class ResizeObserverPolyfill {
  observe(): void {
    // no-op: jsdom has no real layout engine to observe.
  }

  unobserve(): void {
    // no-op
  }

  disconnect(): void {
    // no-op
  }
}

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverPolyfill as unknown as typeof ResizeObserver;
}
