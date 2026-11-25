import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, RefreshCw, ShieldCheck, ShieldOff } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { apiGet, apiPost } from "@/lib/api";
import { errorText } from "@/lib/errors";

interface PromotionRow {
  symbol: string; label: string; timeframe: string; winRate: number | null; samples: number; wilsonLowerBound: number; breakEvenRate: number;
  payoutPercent: number; expectancyPerUnit: number | null; promoted: boolean; reason: string; lastTrainedAt: number; candles?: number;
}
interface PromotionReport {
  items: PromotionRow[]; promotedCount: number; total: number; running: boolean; done: number; total_markets?: number;
  current: { symbol: string; timeframe: string } | null; minWinRate: number; minSamples: number; startedAt: number | null; finishedAt: number | null;
}

const when = (epoch: number | null | undefined) => epoch ? new Date(epoch * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "never";

export default function AccuracyReport() {
  const client = useQueryClient();
  const [filter, setFilter] = useState<"all" | "promoted" | "not">("all");
  const report = useQuery({ queryKey: ["promotion-report"], queryFn: () => apiGet<PromotionReport>("/v1/promotion/report"), refetchInterval: 10000 });
  const run = useMutation({
    mutationFn: () => apiPost("/v1/promotion/run", {}),
    onSuccess: () => { toast.success("Walk-forward validation started"); void client.invalidateQueries({ queryKey: ["promotion-report"] }); },
    onError: (error) => toast.error(errorText(error)),
  });
  const rows = useMemo(() => (report.data?.items ?? []).filter(r => filter === "all" || (filter === "promoted") === r.promoted), [report.data, filter]);
  const data = report.data;
  return <section className="accuracy-report" data-testid="accuracy-report">
    <div className="accuracy-summary" data-testid="accuracy-summary">
      <div data-testid="accuracy-promoted-count"><small>Promoted pairs</small><strong className="text-positive">{data?.promotedCount ?? "—"}</strong><span>of {data?.total ?? "—"} pair/timeframes</span></div>
      <div data-testid="accuracy-rule"><small>Promotion rule</small><strong>≥ {data?.minWinRate ?? 58}% win rate</strong><span>over ≥ {data?.minSamples ?? 300} out-of-sample trades · Wilson 95% lower bound above payout break-even</span></div>
      <div data-testid="accuracy-run-state"><small>Validation run</small><strong>{data?.running ? `${data.done} done · ${data.current?.symbol ?? ""} ${data.current?.timeframe ?? ""}` : "idle"}</strong><span>last finished {when(data?.finishedAt)}</span></div>
      <div className="accuracy-actions">
        <div className="accuracy-filter" role="group" aria-label="Filter">{(["all", "promoted", "not"] as const).map(f => <button key={f} type="button" data-testid={`accuracy-filter-${f}`} aria-pressed={filter === f} className={filter === f ? "selected" : ""} onClick={() => setFilter(f)}>{f === "all" ? "All" : f === "promoted" ? "Promoted" : "Not promoted"}</button>)}</div>
        <Button data-testid="accuracy-run-button" variant="ghost" disabled={run.isPending || data?.running} onClick={() => run.mutate()}><Play size={14} /> Run walk-forward now</Button>
        <Button data-testid="accuracy-refresh-button" variant="ghost" size="icon-sm" aria-label="Refresh" onClick={() => void report.refetch()}><RefreshCw size={14} /></Button>
      </div>
    </div>
    <div className="workspace-table-wrap">
      <table data-testid="accuracy-table">
        <thead><tr><th>Pair</th><th>TF</th><th>Status</th><th>Win rate (OOS)</th><th>Samples</th><th>Wilson LB</th><th>Break-even</th><th>Payout</th><th>Expectancy / $1</th><th>Reason</th><th>Last training</th></tr></thead>
        <tbody>
          {rows.map(r => <tr key={`${r.symbol}-${r.timeframe}`} data-testid={`accuracy-row-${r.symbol}-${r.timeframe}`} className={r.promoted ? "promoted" : ""}>
            <td><b>{r.label}</b><small className="block text-slate-500">{r.symbol}</small></td>
            <td className="font-mono">{r.timeframe}</td>
            <td>{r.promoted ? <span className="accuracy-badge on" data-testid={`accuracy-status-${r.symbol}-${r.timeframe}`}><ShieldCheck size={12} /> PROMOTED</span> : <span className="accuracy-badge off" data-testid={`accuracy-status-${r.symbol}-${r.timeframe}`}><ShieldOff size={12} /> NOT PROMOTED · signal off</span>}</td>
            <td className={`font-mono ${r.winRate !== null && r.winRate >= (data?.minWinRate ?? 58) ? "text-positive" : "text-negative"}`}>{r.winRate === null ? "—" : `${r.winRate.toFixed(1)}%`}</td>
            <td className={`font-mono ${r.samples >= (data?.minSamples ?? 300) ? "" : "text-negative"}`}>{r.samples} / {data?.minSamples ?? 300}</td>
            <td className="font-mono">{r.wilsonLowerBound?.toFixed(1)}%</td>
            <td className="font-mono">{r.breakEvenRate?.toFixed(1)}%</td>
            <td className="font-mono">{r.payoutPercent}%</td>
            <td className={`font-mono ${(r.expectancyPerUnit ?? 0) > 0 ? "text-positive" : "text-negative"}`}>{r.expectancyPerUnit === null ? "—" : r.expectancyPerUnit.toFixed(3)}</td>
            <td className="text-xs">{r.reason.replaceAll("_", " ")}</td>
            <td className="font-mono text-xs">{when(r.lastTrainedAt)}</td>
          </tr>)}
        </tbody>
      </table>
      {report.isLoading && <p className="workspace-empty" data-testid="accuracy-loading">Loading…</p>}
      {!report.isLoading && rows.length === 0 && <p className="workspace-empty" data-testid="accuracy-empty">No walk-forward results yet. The first validation runs automatically a few minutes after start (or press “Run walk-forward now”).</p>}
    </div>
  </section>;
}
