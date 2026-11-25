import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, Database, Download, Loader2, Play, Square, Zap } from "lucide-react";
import { toast } from "sonner";
import { apiGet, apiPost } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useMlStatus } from "@/hooks/useLiveSignals";
import { errorText } from "@/lib/errors";

interface PipelineRow { symbol: string; timeframe: string; status?: string; reason?: string; tier?: string | null; candles?: number; accuracy?: number | null; winRate?: number | null; brier?: number | null; legacy?: string; at: number }
interface Pipeline { running: boolean; phase: string; total: number; done: number; eligibleMarkets: number; errors: number; current: { symbol: string; timeframe: string } | null; startedAt: number | null; finishedAt: number | null; results: PipelineRow[]; timeframes: string[]; dailyRetrainSeconds: number; parallelMarkets?: number; backfillCandles?: number; incrementalSeconds: number; incremental?: { runs: number; markets: string[]; lastRunAt: number | null }; lastRun: { phase: string; done: number; trained: number; finishedAt: number } | null }
interface HistoryRow { symbol: string; timeframe: string; status: string; promotionTier?: string | null; generatedAt: number; closedProviderCandles?: number; test?: { accuracy?: number; winRate?: number; brier?: number }; legacyAdapters?: { status?: string } }
interface FeatureStatus { rows: number; featureColumns: string[]; lastRefreshAt: number | null; lastRefresh: { markets: number; rowsWritten: number; durationMs: number }; timeframes: Record<string, { rows: number; symbols: number }> }
interface AdapterStatus { trained: boolean; status: string; backend?: string | null; training_samples: number; last_fit_at?: number | null; persisted?: { savedAt?: number; market?: string } | null }

const pct = (value?: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const when = (epoch?: number | null) => epoch ? new Date(epoch * 1000).toLocaleTimeString() : "—";

export default function TrainingPanel() {
  const client = useQueryClient();
  const pipeline = useQuery({ queryKey: ["ml-pipeline"], queryFn: () => apiGet<Pipeline>("/v1/ml/pipeline"), refetchInterval: 3000 });
  const history = useQuery({ queryKey: ["ml-model-history"], queryFn: () => apiGet<{ items: HistoryRow[] }>("/v1/ml/model-history?limit=80"), refetchInterval: 15000 });
  const features = useQuery({ queryKey: ["feature-store"], queryFn: () => apiGet<FeatureStatus>("/v1/features/status"), refetchInterval: 15000 });
  const ml = useMlStatus();
  const refresh = () => void client.invalidateQueries({ queryKey: ["ml-pipeline"] });
  const useRun = (path: string, ok: string) => useMutation({ mutationFn: (body?: Record<string, unknown>) => apiPost(path, body ?? {}), onSuccess: () => { toast.success(ok); refresh(); }, onError: error => toast.error(errorText(error)) });
  const backfill = useRun("/v1/ml/backfill-all", "Fetching Deriv history for every pair");
  const train = useRun("/v1/ml/train-all", "Training all ML / DL / DS models for every pair");
  const stop = useRun("/v1/ml/pipeline/stop", "Pipeline stopped");
  const retrainActive = useRun("/v1/ml/retrain-active", "Heavy retrain started for the markets with recent signals");
  const p = pipeline.data;
  const busy = Boolean(p?.running);
  const progress = p && p.total ? Math.round((100 * p.done) / p.total) : 0;
  const adapters = (ml.data?.models ?? {}) as unknown as Record<string, AdapterStatus>;
  const deep = ml.data?.deepModels;
  const trainedRows = (p?.results ?? []).filter(r => r.legacy === "TRAINED" || r.tier);
  return <section className="training-panel" data-testid="training-panel">
    <header className="training-head">
      <div><p className="workspace-eyebrow">ML · DL · DS TRAINING PIPELINE</p><h2><BrainCircuit size={18} /> Model training</h2></div>
      <div className="training-actions">
        <Button data-testid="fetch-history-button" variant="ghost" disabled={busy || backfill.isPending} onClick={() => backfill.mutate({})}><Download size={15} /> Fetch History</Button>
        <Button data-testid="train-all-button" className="at-start" disabled={busy || train.isPending} onClick={() => train.mutate({ backfill: true })}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Train All</Button>
        <Button data-testid="retrain-active-button" variant="ghost" disabled={busy || retrainActive.isPending} title="Heavy retrain of the markets that produced signals in the last 24h" onClick={() => retrainActive.mutate({ hours: 24, backfill: true })}><Zap size={15} /> Retrain Active</Button>
        {busy && <Button data-testid="stop-training-button" variant="ghost" className="at-stop" onClick={() => stop.mutate({})}><Square size={15} /> Stop</Button>}
      </div>
    </header>
    <div className="training-progress" data-testid="training-progress">
      <div className="training-progress-bar"><i style={{ width: `${progress}%` }} /></div>
      <div className="training-progress-meta">
        <b data-testid="training-phase">{p ? p.phase.replaceAll("_", " ") : "…"}</b>
        <span data-testid="training-count">{p ? p.running ? `${p.done} / ${p.total} market-timeframes · ${p.eligibleMarkets} eligible markets` : `${p.eligibleMarkets} eligible markets${p.total ? ` · last run ${p.done} / ${p.total}` : ""}` : ""}</span>
        <span data-testid="training-current">{p?.current ? `now: ${p.current.symbol} ${p.current.timeframe}` : p?.lastRun ? `last run: ${p.lastRun.trained} trained · ${when(p.lastRun.finishedAt)}` : "no run yet"}</span>
        <span data-testid="training-errors">{p?.errors ? `${p.errors} errors` : ""}</span>
        <span className="training-schedule" data-testid="training-schedule">{p ? `full retrain every ${Math.round(p.dailyRetrainSeconds / 3600)}h · ${p.parallelMarkets ?? 1} markets in parallel · ${p.backfillCandles ?? 5000} candles · incremental retrain every ${Math.round(p.incrementalSeconds / 60)} min${p.incremental?.runs ? ` (${p.incremental.runs} runs · last: ${p.incremental.markets.slice(0, 4).join(", ")})` : ""} · ${p.timeframes.join(", ")}` : "loading schedule…"}</span>
      </div>
    </div>
    <div className="training-grid">
      <div className="training-card" data-testid="adapter-stack">
        <h3>ML adapters (Step 8 stack)</h3>
        <table className="training-table"><thead><tr><th>Model</th><th>Status</th><th>Backend</th><th>Samples</th><th>Saved</th></tr></thead><tbody>
          {Object.entries(adapters).map(([name, row]) => <tr key={name} data-testid={`adapter-${name}`}><td>{name}</td><td className={row.trained ? "tone-ok" : "tone-warn"}>{row.status}</td><td>{row.backend ?? "numpy"}</td><td className="mono">{row.training_samples}</td><td className="mono">{row.persisted?.savedAt ? when(row.persisted.savedAt) : "—"}</td></tr>)}
        </tbody></table>
      </div>
      <div className="training-card" data-testid="deep-model-summary">
        <h3>DL deep models (PyTorch LSTM) · DS feature store</h3>
        <ul className="training-kv">
          <li><span>Live-tier markets</span><b data-testid="deep-live-count">{deep?.readyMarketCount ?? 0}</b></li>
          <li><span>Paper-tier markets</span><b data-testid="deep-paper-count">{deep?.paperReadyMarketCount ?? 0}</b></li>
          <li><span>Evaluated / expected</span><b>{deep?.evaluation ? `${deep.evaluation.evaluated} / ${deep.evaluation.expectedModels}` : "—"}</b></li>
          <li><span><Database size={12} /> Feature rows</span><b data-testid="feature-rows">{features.data?.rows ?? 0}</b></li>
          <li><span>Feature columns</span><b>{features.data?.featureColumns.length ?? 0}</b></li>
          <li><span>Last feature refresh</span><b>{features.data ? `${when(features.data.lastRefreshAt)} · ${features.data.lastRefresh.rowsWritten} rows` : "—"}</b></li>
        </ul>
      </div>
    </div>
    <div className="training-card" data-testid="training-results">
      <h3>Per-pair results {p?.results.length ? `· ${trainedRows.length} trained of ${p.results.length}` : ""}</h3>
      <div className="training-table-wrap"><table className="training-table"><thead><tr><th>Pair</th><th>TF</th><th>Status</th><th>Tier</th><th>Candles</th><th>Test accuracy</th><th>Brier</th><th>Adapters</th><th>At</th></tr></thead><tbody>
        {(p?.results.length ? [...p.results].reverse() : (history.data?.items ?? []).map(h => ({ symbol: h.symbol, timeframe: h.timeframe, status: h.status, tier: h.promotionTier, candles: h.closedProviderCandles, accuracy: h.test?.accuracy, brier: h.test?.brier, legacy: h.legacyAdapters?.status, at: h.generatedAt } as PipelineRow))).slice(0, 120).map((row, index) => (
          <tr key={`${row.symbol}-${row.timeframe}-${index}`} data-testid={`training-row-${row.symbol}-${row.timeframe}`}>
            <td>{row.symbol}</td><td>{row.timeframe}</td>
            <td className={row.status === "FAILED" ? "tone-bad" : row.tier ? "tone-ok" : "tone-warn"}>{(row.status ?? "—").replaceAll("_", " ")}{row.reason ? <small> · {row.reason.replaceAll("_", " ")}</small> : null}</td>
            <td>{row.tier ?? "—"}</td><td className="mono">{row.candles ?? "—"}</td><td className="mono">{pct(row.accuracy)}</td><td className="mono">{row.brier == null ? "—" : row.brier.toFixed(4)}</td><td>{row.legacy ?? "—"}</td><td className="mono">{when(row.at)}</td>
          </tr>))}
      </tbody></table>{!p?.results.length && !history.data?.items.length && <p className="workspace-empty" data-testid="training-empty">No training run yet. Click Fetch History, then Train All.</p>}</div>
    </div>
  </section>;
}
