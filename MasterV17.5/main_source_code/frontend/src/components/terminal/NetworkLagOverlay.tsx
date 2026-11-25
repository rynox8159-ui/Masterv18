import { useRuntime } from "@/hooks/useMarketBackend";
import MasterCandleLogo from "./MasterCandleLogo";

export default function NetworkLagOverlay() {
  const runtime = useRuntime(true);
  const lagging = runtime.isError || (runtime.isFetching && runtime.failureCount > 0);
  if (!lagging) return null;
  return <div className="network-lag" data-testid="network-lag-overlay" role="status">
    <MasterCandleLogo size={30} animated />
    <div><b>Reconnecting to backend…</b><small>{runtime.failureCount > 0 ? `attempt ${runtime.failureCount} · network lag` : "network lag"}</small></div>
  </div>;
}
