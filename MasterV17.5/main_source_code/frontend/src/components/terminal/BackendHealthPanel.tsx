import { useQuery } from "@tanstack/react-query";
import { Activity, BrainCircuit, Database, KeyRound, Radio, Siren, Zap, type LucideIcon } from "lucide-react";
import { apiGet } from "@/lib/api";
import { useMlStatus, useSignalCheck } from "@/hooks/useLiveSignals";
import { OPEN_API_KEY_EVENT } from "./ApiKeyGate";

type Tone = "ok" | "warn" | "bad";
interface MemorySummary { events: Record<string, number>; notes: number; algorithms: number }
interface Health { status: string; database: string }

function Row({ icon: Icon, label, value, tone, testId }: { icon: LucideIcon; label: string; value: string; tone: Tone; testId: string }) {
  return <div className={`health-row tone-${tone}`} data-testid={testId}><Icon size={15} /><span>{label}</span><b data-testid={`${testId}-value`}>{value}</b><i aria-hidden="true" /></div>;
}

export default function BackendHealthPanel({ compact = false }: { compact?: boolean }) {
  const health = useQuery({ queryKey: ["backend-health"], queryFn: () => apiGet<Health>("/health"), refetchInterval: 4000, retry: 0 });
  const check = useSignalCheck();
  const ml = useMlStatus();
  const memory = useQuery({ queryKey: ["memory-summary"], queryFn: () => apiGet<MemorySummary>("/v1/memory/summary"), refetchInterval: 15000 });
  const apiOk = health.isSuccess && health.data.status === "ok";
  const deriv = check.data?.deriv;
  const derivLive = deriv?.state === "DATA_RECEIVING" && (deriv.ageSeconds ?? 99) <= (check.data?.freshnessSeconds ?? 60);
  const engine = check.data?.engine;
  const trained = Object.values(ml.data?.models ?? {}).filter(model => model.trained).length;
  const totalModels = Object.keys(ml.data?.models ?? {}).length;
  const training = engine?.automaticModelBootstrap?.currentMarket;
  const lastSignal = check.data?.sources?.deriv?.lastSignal;
  const verdict = check.data?.verdict ?? "NOT_CONNECTED";
  const signalTone: Tone = verdict === "RECEIVING_SIGNALS_ACTIVE" ? "ok" : verdict.startsWith("RECEIVING") ? "warn" : "bad";
  const events = memory.data?.events ?? {};
  const traps = (events.BULL_TRAP ?? 0) + (events.BEAR_TRAP ?? 0);
  const gaps = (events.GAP_UP ?? 0) + (events.GAP_DOWN ?? 0);
  return <section className={`backend-health ${compact ? "compact" : ""}`} data-testid="backend-health-panel">
    <header><Siren size={15} /><h3>Backend health</h3><span data-testid="backend-health-verdict" className={`health-verdict tone-${signalTone}`}>{verdict.replaceAll("_", " ")}</span>
      <button data-testid="backend-api-key-button" title="Change backend API key" onClick={() => window.dispatchEvent(new Event(OPEN_API_KEY_EVENT))}><KeyRound size={13} /></button></header>
    <Row icon={Zap} label="API server" value={apiOk ? "ONLINE" : health.isLoading ? "…" : "OFFLINE"} tone={apiOk ? "ok" : "bad"} testId="health-api" />
    <Row icon={Database} label="MongoDB" value={apiOk ? health.data.database.toUpperCase() : "—"} tone={apiOk ? "ok" : "bad"} testId="health-mongo" />
    <Row icon={Radio} label="Deriv feed" value={deriv ? `${derivLive ? "LIVE" : deriv.state} · ${deriv.acceptedCount ?? 0} pairs` : "—"} tone={derivLive ? "ok" : deriv ? "warn" : "bad"} testId="health-deriv" />
    <Row icon={Activity} label="Signal engine" value={engine ? `${engine.enabled ? "RUNNING" : "OFF"} · ${engine.cycles} cycles` : "—"} tone={engine?.enabled && !engine.error ? "ok" : "warn"} testId="health-engine" />
    <Row icon={BrainCircuit} label="ML / DL training" value={training ? `TRAINING ${training.symbol} ${training.timeframe}` : totalModels && !trained ? "WARMING UP · loading models" : `${trained}/${totalModels} adapters · ${ml.data?.deepModels?.readyMarketCount ?? 0} deep`} tone={trained || ml.data?.deepModels?.readyMarketCount ? "ok" : "warn"} testId="health-training" />
    {!compact && <Row icon={Database} label="Market memory" value={`${traps} traps · ${gaps} gaps · ${memory.data?.notes ?? 0} notes`} tone={memory.isSuccess ? "ok" : "warn"} testId="health-memory" />}
    <div className="health-last-signal" data-testid="health-last-signal">{lastSignal ? <><span>Last signal</span><b className={lastSignal.direction === "CALL" ? "text-positive" : "text-negative"}>{lastSignal.label} · {lastSignal.timeframe} · {lastSignal.direction} · {Math.round(lastSignal.confidence)}%</b><small>{lastSignal.status}</small></> : <span>No qualified signal yet · engine keeps scanning live candles</span>}</div>
  </section>;
}
