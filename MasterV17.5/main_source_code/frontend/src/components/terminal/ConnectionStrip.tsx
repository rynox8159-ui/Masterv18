import { useQuery } from "@tanstack/react-query";
import { Database, Radio, Server, Wifi, type LucideIcon } from "lucide-react";
import { apiGet, ApiError } from "@/lib/api";
import { useRuntime } from "@/hooks/useMarketBackend";

type Tone = "ok" | "warn" | "bad";
interface Health { status: string; database: string; deriv?: { state: string; pairs: number; error?: string | null }; sniffer?: string; version?: string }
interface Proof { state: string; framesTotal: number; framesPerSecond: number; lastFrameAgeSeconds: number | null }

export function describeBackendError(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.body && typeof error.body === "object" && "detail" in error.body
      ? error.body.detail
      : undefined;
    if (error.status === 401 && detail === "SESSION_REQUIRED") return "Backend session missing or expired · reconnect with the access key";
    if (error.status === 401 && detail === "INVALID_CREDENTIAL") return "Backend access key rejected · check the configured key";
    if (error.status === 401) return "Backend authorization required · reconnect with the access key";
    if (error.status === 503 && detail === "AUTH_NOT_CONFIGURED") return "Backend access key is not configured · set BACKEND_API_KEY and restart";
    if (error.status === 502 || error.status === 503 || error.status === 504) return `Gateway ${error.status} · backend process is down or restarting`;
    return `Backend answered HTTP ${error.status}`;
  }
  if (error instanceof TypeError) return `Network error · ${import.meta.env.VITE_BACKEND_URL || window.location.origin}/api not reachable (server stopped, wrong URL or CORS)`;
  return error instanceof Error ? error.message : "Unknown error";
}

function Pill({ icon: Icon, label, value, tone, testId, title }: { icon: LucideIcon; label: string; value: string; tone: Tone; testId: string; title?: string }) {
  return <span className={`conn-pill tone-${tone}`} data-testid={testId} title={title}><Icon size={12} /><span>{label}</span><b data-testid={`${testId}-value`}>{value}</b></span>;
}

export default function ConnectionStrip() {
  const health = useQuery({ queryKey: ["backend-health"], queryFn: () => apiGet<Health>("/health"), refetchInterval: 4000, retry: 0 });
  const runtime = useRuntime(true);
  const proof = useQuery({ queryKey: ["sniffer-proof"], queryFn: () => apiGet<Proof>("/v1/observation/proof"), refetchInterval: 5000, retry: 0 });
  const backendOk = health.isSuccess && health.data.status === "ok";
  const deriv = runtime.data?.providers.find(p => p.source === "deriv");
  const derivAge = deriv?.ageSeconds;
  const derivLive = deriv?.state === "DATA_RECEIVING" && (derivAge ?? 999) <= 45;
  const derivTone: Tone = derivLive ? "ok" : deriv && (deriv.state === "CONNECTING" || deriv.state === "CONNECTED" || deriv.state === "STALE") ? "warn" : "bad";
  const sniffer = proof.data;
  const snifferTone: Tone = sniffer?.state === "RECEIVING" ? "ok" : sniffer?.state === "STALE" ? "warn" : "bad";
  return <div className="connection-strip" data-testid="connection-strip" aria-label="Live connection status">
    <Pill icon={Server} label="Backend" tone={backendOk ? "ok" : health.isLoading ? "warn" : "bad"} testId="conn-backend" title={health.error ? describeBackendError(health.error) : `Version ${health.data?.version ?? "…"}`}
      value={backendOk ? "ONLINE" : health.isLoading ? "…" : "UNREACHABLE"} />
    <Pill icon={Database} label="Database" tone={backendOk ? "ok" : "bad"} testId="conn-database" value={backendOk ? health.data.database.toUpperCase() : "—"} />
    <Pill icon={Radio} label="Deriv" tone={derivTone} testId="conn-deriv" title={deriv?.error ?? undefined}
      value={deriv ? `${derivLive ? "LIVE" : deriv.state} · ${deriv.acceptedCount ?? 0}/${deriv.requestedCount ?? 0}` : runtime.isError ? "NO BACKEND" : "…"} />
    <Pill icon={Wifi} label="Quotex sniffer" tone={snifferTone} testId="conn-sniffer" title={sniffer ? `${sniffer.framesTotal} frames · ${sniffer.framesPerSecond}/s` : undefined}
      value={sniffer ? (sniffer.state === "RECEIVING" ? `LIVE · ${sniffer.framesPerSecond}/s` : sniffer.state === "STALE" ? `STALE · ${Math.round(sniffer.lastFrameAgeSeconds ?? 0)}s` : "WAITING") : "—"} />
  </div>;
}
