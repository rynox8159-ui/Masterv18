import { useEffect, useRef } from "react";
import { streamUrl } from "@/lib/api";

export type Tick = { price: number; epoch: number };

// Raw Deriv ticks over SSE, delivered to a callback (no React state → no re-render per tick).
export function useTickStream(symbol: string | null, enabled: boolean, onTick: (tick: Tick) => void) {
  const handler = useRef(onTick);
  handler.current = onTick;
  useEffect(() => {
    if (!symbol || !enabled) return;
    let source: EventSource | null = null;
    let retry: number | null = null;
    let closed = false;
    const open = () => {
      source = new EventSource(streamUrl(`/telemetry/ticks?${new URLSearchParams({ symbol })}`), { withCredentials: true });
      source.addEventListener("tick", event => {
        const data = JSON.parse((event as MessageEvent).data) as { price: number | null; epoch: number | null };
        if (data.price !== null && data.epoch !== null && Number.isFinite(data.price)) handler.current({ price: data.price, epoch: data.epoch });
      });
      source.onerror = () => {
        source?.close();
        if (!closed) retry = window.setTimeout(open, 1500);
      };
    };
    open();
    return () => { closed = true; if (retry) window.clearTimeout(retry); source?.close(); };
  }, [symbol, enabled]);
}
