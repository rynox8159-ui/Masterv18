import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bot, ChevronLeft, ChevronRight, Radio, Search, ShieldCheck, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { apiGet } from "@/lib/api";

type LiveAgent = { id: string; source: string; symbol: string; timeframe: string; status: "Online" | "Idle" | "Waiting"; lastSeenAge: number | null; evaluations: number; qualified: number; wins: number; losses: number; winRate: number | null; edge?: { proven: boolean; oosHitRate: number | null; oosSamples: number; validated: string[] } | null; last?: { direction: string; confidence: number; gate: string; regime?: string; edge?: number } | null };
type AgentBoard = { maxAgents: number; active: number; provenEdge?: number; online: number; systemWinRate: number | null; items: LiveAgent[] };

// V16.3: real per-pair agents from the signal engine (one per Deriv pair x timeframe).
export default function AgentRegistry({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void; online?: boolean; live?: boolean }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("All");
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const board = useQuery({ queryKey: ["agents-live"], queryFn: () => apiGet<AgentBoard>("/v1/agents/live"), enabled: open, refetchInterval: 5000 });
  const registry = board.data?.items ?? [];
  const filtered = registry.filter(agent => (status === "All" || agent.status === status) && `${agent.symbol} ${agent.timeframe}`.toLowerCase().includes(query.toLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / 10));
  const currentPage = Math.min(page, pages - 1);
  const selected = registry.find(agent => agent.id === selectedId) ?? registry[0];
  if (!open) return null;
  return <Dialog open onOpenChange={onOpenChange}><DialogContent data-testid="agent-registry-dialog" showCloseButton={false} className="agent-registry-dialog">
    <DialogHeader><DialogTitle data-testid="agent-registry-title" className="agent-title"><Bot size={23} /> Agent control center <span>{board.data?.active ?? 0} / {board.data?.maxAgents ?? 500}</span></DialogTitle>
      <DialogDescription data-testid="agent-registry-description">Each agent tracks one Deriv pair × timeframe, runs the algorithm layer every cycle and keeps its own settled win-rate.</DialogDescription></DialogHeader>
    <Button data-testid="agent-registry-close" className="pair-picker-close" variant="ghost" size="icon-sm" aria-label="Close agent registry" onClick={() => onOpenChange(false)}><X size={17} /></Button>
    <div className="agent-summary" data-testid="agent-summary">
      <div><span>Active agents</span><strong data-testid="agent-count-total">{registry.length}</strong></div>
      {(["Online", "Idle", "Waiting"] as const).map(value => <button key={value} data-testid={`agent-summary-${value.toLowerCase()}`} className={`agent-${value === "Waiting" ? "offline" : value.toLowerCase()}`} onClick={() => { setStatus(status === value ? "All" : value); setPage(0); }}><span>{value}</span><strong data-testid={`agent-count-${value.toLowerCase()}`}>{registry.filter(agent => agent.status === value).length}</strong></button>)}
    </div>
    <div className="agent-source-note" data-testid="agent-source-notice"><ShieldCheck size={15} /><span><b>System accuracy (settled signals): {board.data?.systemWinRate ?? "—"}% · {board.data?.provenEdge ?? 0} markets with proven edge</b> · signals are only released on markets whose own history proves an out-of-sample edge (walk-forward test).</span></div>
    <div className="agent-heatmap" data-testid="agent-heatmap">{registry.map(agent => <button key={agent.id} data-testid={`agent-cell-${agent.id}`} title={`${agent.symbol} ${agent.timeframe} · ${agent.status} · ${agent.winRate ?? "—"}%`} className={`agent-cell agent-${agent.status === "Waiting" ? "offline" : agent.status.toLowerCase()} ${selected?.id === agent.id ? "selected" : ""}`} onClick={() => setSelectedId(agent.id)} />)}</div>
    <div className="agent-selected" data-testid="agent-selected-details"><Bot size={18} />{selected ? <><strong data-testid="agent-selected-name">{selected.symbol} · {selected.timeframe}</strong><span data-testid="agent-selected-pair">{selected.last ? `${selected.last.direction} · ${selected.last.regime ?? "—"} · gate ${selected.last.gate || "—"}` : "waiting for first evaluation"}</span><span data-testid="agent-selected-record">{selected.wins}W / {selected.losses}L · {selected.winRate ?? "—"}%</span><span className={`agent-state agent-${selected.status === "Waiting" ? "offline" : selected.status.toLowerCase()}`} data-testid="agent-selected-status">{selected.status}</span></> : <strong data-testid="agent-selected-name">{board.isLoading ? "Loading agents…" : "Agents start after the first signal cycle"}</strong>}</div>
    <div className="agent-filters"><label className="catalog-search"><Search size={14} /><Input data-testid="agent-search-input" placeholder="Search pair…" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} /></label></div>
    <div className="agent-table-wrap"><table className="agent-table" data-testid="agent-table"><thead><tr><th>Pair</th><th>TF</th><th>Last decision</th><th>Evaluations</th><th>Proven edge</th><th>Win-rate</th><th>Status</th></tr></thead><tbody>{filtered.slice(currentPage * 10, currentPage * 10 + 10).map(agent => <tr key={agent.id} data-testid={`agent-row-${agent.id}`} onClick={() => setSelectedId(agent.id)}><td>{agent.symbol}</td><td>{agent.timeframe}</td><td>{agent.last ? `${agent.last.direction} ${Math.round(agent.last.confidence)}%` : "—"}<small>{agent.last?.regime ?? ""}</small></td><td>{agent.evaluations} <small>{agent.qualified} qualified</small></td><td data-testid={`agent-edge-${agent.id}`}>{agent.edge ? <span className={agent.edge.proven ? "agent-state agent-online" : "agent-state agent-offline"}>{agent.edge.proven ? "PROVEN" : "NO EDGE"}</span> : "testing…"}<small>{agent.edge?.oosHitRate != null ? `${Math.round(agent.edge.oosHitRate * 100)}% out-of-sample · ${agent.edge.oosSamples}` : ""}</small></td><td>{agent.winRate === null ? "—" : `${agent.winRate}%`}<small>{agent.wins}W/{agent.losses}L</small></td><td><span className={`agent-state agent-${agent.status === "Waiting" ? "offline" : agent.status.toLowerCase()}`}><Radio size={10} />{agent.status}</span></td></tr>)}</tbody></table>{!filtered.length && <p data-testid="agent-empty-state" className="agent-empty-state">No agents match your filters yet.</p>}</div>
    <div className="agent-pagination"><span data-testid="agent-result-count">{filtered.length} agents · Page {currentPage + 1} of {pages}</span><Button data-testid="agent-previous-page" variant="ghost" size="icon-sm" aria-label="Previous agent page" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={16} /></Button><Button data-testid="agent-next-page" variant="ghost" size="icon-sm" aria-label="Next agent page" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}><ChevronRight size={16} /></Button></div>
  </DialogContent></Dialog>;
}
