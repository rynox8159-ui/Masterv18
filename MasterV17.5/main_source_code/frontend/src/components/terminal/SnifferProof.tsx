import { useQuery } from "@tanstack/react-query";
import { Activity, ExternalLink, Wifi } from "lucide-react";
import { apiGet } from "@/lib/api";

interface Tick { symbol: string; price: number; at: number; receivedAt: number; candle?: { open: number; high: number; low: number; close: number; timeframe: string } }
interface Frame { kind: string; symbol: string; method: string; receivedAt: number; price?: number | null }
interface Proof {
  state: string; framesTotal: number; byKind: Record<string, number>; byMethod: Record<string, number>; framesLastMinute: number; framesPerSecond: number;
  lastFrameAgeSeconds: number | null; activeSessions: number; symbolsSeen: number; lastTicks: Tick[]; lastFrames: Frame[]; observedInstruments: number;
  observerStatus?: { state?: string; lastReceived?: number; verification?: string } | null;
}

const clock = (epoch?: number | null) => epoch ? new Date(epoch * 1000).toLocaleTimeString() : "—";

export default function SnifferProof() {
  const proof = useQuery({ queryKey: ["sniffer-proof"], queryFn: () => apiGet<Proof>("/v1/observation/proof"), refetchInterval: 2000, retry: 0 });
  const d = proof.data;
  const tone = d?.state === "RECEIVING" ? "ok" : d?.state === "STALE" ? "warn" : "bad";
  return <section className="sniffer-proof" data-testid="sniffer-proof-panel">
    <header className="training-head">
      <div><p className="workspace-eyebrow">WS SNIFFER · PLAYWRIGHT OBSERVER</p><h2><Wifi size={18} /> Quotex data proof</h2></div>
      <span className={`conn-pill tone-${tone}`} data-testid="sniffer-state"><Activity size={12} /><b>{d ? d.state.replaceAll("_", " ") : proof.isError ? "BACKEND UNREACHABLE" : "…"}</b></span>
    </header>
    <div className="workspace-note sniffer-howto" data-testid="sniffer-howto">
      <b>Your own Chrome tab (recommended · manual login)</b>
      <ol className="observer-local-steps">
        <li>Run <code>scripts\local_pc\start-chrome-debug.bat</code> — opens <em>your</em> Chrome with remote debugging on port 9222.</li>
        <li>Log in to Quotex in that Chrome window yourself (captcha / 2FA is fine; the login is remembered).</li>
        <li>Press <b>WS Sniffer</b> in the left menu or run <code>scripts\local_pc\start-sniffer.bat</code>. The sniffer attaches to that tab, reads its WebSocket feed and tracks <b>all open Quotex pairs</b> (list, payout, ticks, 1m candles). Read-only: it never places trades.</li>
      </ol>
      <span className="at-muted">Alternative: <code>start-observer.bat</code> opens a separate Chromium window (<code>start-observer-cloud.bat</code> for this cloud backend). Your password is never stored by the backend.</span>
      <a className="workspace-download" href="https://quotex.com/en/sign-in" target="_blank" rel="noreferrer" data-testid="open-quotex-link"><ExternalLink size={13} /> Open Quotex</a>
    </div>
    <div className="sniffer-stats" data-testid="sniffer-stats">
      <div><small>Frames received</small><b data-testid="sniffer-frames-total">{d?.framesTotal ?? 0}</b></div>
      <div><small>Frames / second</small><b data-testid="sniffer-fps">{d?.framesPerSecond ?? 0}</b></div>
      <div><small>Last minute</small><b data-testid="sniffer-last-minute">{d?.framesLastMinute ?? 0}</b></div>
      <div><small>Last frame age</small><b data-testid="sniffer-age">{d?.lastFrameAgeSeconds == null ? "—" : `${Math.round(d.lastFrameAgeSeconds)}s`}</b></div>
      <div><small>Pairs seen</small><b data-testid="sniffer-symbols">{d?.symbolsSeen ?? 0}</b></div>
      <div><small>Active sessions</small><b data-testid="sniffer-sessions">{d?.activeSessions ?? 0}</b></div>
      <div><small>Observed instruments (DB)</small><b data-testid="sniffer-instruments">{d?.observedInstruments ?? 0}</b></div>
      <div><small>Capture methods</small><b data-testid="sniffer-methods">{d && Object.keys(d.byMethod).length ? Object.entries(d.byMethod).map(([k, v]) => `${k}:${v}`).join(" · ") : "—"}</b></div>
    </div>
    <div className="training-grid">
      <div className="training-card" data-testid="sniffer-last-ticks">
        <h3>Latest tick per pair</h3>
        <div className="training-table-wrap"><table className="training-table"><thead><tr><th>Pair</th><th>Price</th><th>Broker time</th><th>Received</th></tr></thead><tbody>
          {(d?.lastTicks ?? []).map(t => <tr key={t.symbol} data-testid={`sniffer-tick-${t.symbol}`}><td>{t.symbol}</td><td className="mono">{t.price}</td><td className="mono">{clock(t.at)}</td><td className="mono">{clock(t.receivedAt)}</td></tr>)}
        </tbody></table>{!d?.lastTicks.length && <p className="workspace-empty" data-testid="sniffer-no-ticks">No Quotex frames yet. Start the observer on your PC and log in.</p>}</div>
      </div>
      <div className="training-card" data-testid="sniffer-last-frames">
        <h3>Raw frame log (latest 25)</h3>
        <div className="training-table-wrap"><table className="training-table"><thead><tr><th>Kind</th><th>Pair</th><th>Method</th><th>Price</th><th>Received</th></tr></thead><tbody>
          {(d?.lastFrames ?? []).map((f, i) => <tr key={`${f.receivedAt}-${i}`}><td>{f.kind}</td><td>{f.symbol}</td><td>{f.method}</td><td className="mono">{f.price ?? "—"}</td><td className="mono">{clock(f.receivedAt)}</td></tr>)}
        </tbody></table></div>
      </div>
    </div>
  </section>;
}
