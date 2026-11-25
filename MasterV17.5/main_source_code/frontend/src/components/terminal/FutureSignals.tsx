import { marketNow } from "@/lib/marketClock";
import { useEffect, useState } from 'react';
import { Clock3, Radar, TrendingDown, TrendingUp, Zap } from 'lucide-react';
import CountryFlags from '@/components/terminal/CountryFlags';
import { marketForSignal, marketsForMode, signalMatchesMarketMode, type ActiveMarketMode, type Market } from '@/lib/markets';
import { clock, countdown, sourceName, useSignalSource } from '@/lib/signalSource';
import { useAccumulatorSignals, useLiveSignals, type LiveSignal } from '@/hooks/useLiveSignals';

/** Match a backend signal label (e.g. `EUR/USD (OTC)`) to the catalog pair id so the flags render. */
const pairIdFor = (signal: LiveSignal, derivMarkets: readonly Market[]) => marketForSignal(signal, derivMarkets)?.id ?? (signal.label.includes('OTC') ? 'eur-usd' : 'eur-usd-regular');

function useNow(active: boolean) {
  const [now, setNow] = useState(() => marketNow() / 1000);
  useEffect(() => { if (!active) return; const id = setInterval(() => setNow(marketNow() / 1000), 1000); return () => clearInterval(id); }, [active]);
  return now;
}

export function SignalLine({ signal, marketMode }: { signal: LiveSignal; marketMode?: ActiveMarketMode }) {
  const platforms = signal.platformCompatibility ?? (signal.source === 'deriv' ? ['deriv', 'generic_binary_options'] : ['generic_binary_options']);
  const expiry = signal.targetExpiry ?? signal.timeframe;
  return <span className="future-signal-line" data-testid={`future-signal-line-${signal.id}`}>
    <span className="future-signal-asset"><b>{signal.label}</b><small>{signal.symbol}</small></span>
    <span className="future-signal-expiry" aria-label={`Expiry ${expiry}`}>{expiry}</span>
    <b className={`future-signal-direction ${signal.direction === 'CALL' ? 'text-positive' : 'text-negative'}`}>{signal.direction === 'CALL' ? 'CALL / RISE' : 'PUT / FALL'}</b>
    <span className="future-signal-confidence" title="Setup confidence; not a measured win probability">Confidence {Math.round(signal.confidence)}%</span>
    <span className="future-signal-platforms" aria-label="Signal compatibility">{platforms.map(platform => <small key={platform}>{platform === 'generic_binary_options' ? marketMode === 'BINARY' ? 'FIXED-TIME BINARY' : 'BINARY' : platform.toUpperCase()}</small>)}</span>
    <span className="future-signal-time">{clock(signal.entryEpoch)}</span>
  </span>;
}

export default function FutureSignals({ online = true, onSelectPair, marketMode, derivMarkets = [], modeMarkets: providedModeMarkets, closedForexCount = 0, supportedForexCount, activePairId, activeSymbol }: { online?: boolean; onSelectPair?: (pairId: string) => void; marketMode?: ActiveMarketMode; derivMarkets?: readonly Market[]; modeMarkets?: readonly Market[]; closedForexCount?: number; supportedForexCount?: number; activePairId?: string; activeSymbol?: string }) {
  const [source] = useSignalSource();
  const board = useLiveSignals(source, online);
  const accumulators = useAccumulatorSignals(online, 20);
  const now = useNow(online);
  const data = board.data;
  const accumulatorMode = marketMode === 'FOREX';
  const modeMarkets = providedModeMarkets ?? (marketMode ? marketsForMode(marketMode, derivMarkets) : null);
  const forexClosed = !accumulatorMode && modeMarkets?.length === 0 && closedForexCount > 0;
  const inMarketMode = (signal: LiveSignal) => !modeMarkets || !marketMode || signalMatchesMarketMode(signal, marketMode, modeMarkets);
  const displayTiers = new Set(['LIVE_VALIDATED']);
  const upcoming = accumulatorMode ? [] : (data?.upcoming ?? []).filter(signal => displayTiers.has(signal.validationTier ?? '') && signal.expiryEpoch >= now - 3 && inMarketMode(signal) && (!activePairId || marketForSignal(signal, derivMarkets)?.id === activePairId));
  const recent = accumulatorMode ? [] : (data?.recent ?? []).filter(signal => displayTiers.has(signal.validationTier ?? '') && inMarketMode(signal) && (!activePairId || marketForSignal(signal, derivMarkets)?.id === activePairId)).slice(0, 6);
  const activeAccumulators = (accumulators.data?.items ?? []).filter(signal => signal.validationTier === 'LIVE_VALIDATED' && signal.strategyTier === 'LIVE_VALIDATED' && signal.validationReport?.status === 'VALIDATED' && signal.validationReport.readyForLive && signal.expiresAt >= now && (!activeSymbol || signal.symbol === activeSymbol));
  const noAccumulatorMarkets = !!accumulators.data && accumulators.data.supportedSymbols.length === 0;
  const engine = data?.engine;
  const binaryTimeframes = engine?.settings.timeframes.filter(tf => tf === '1m' || tf === '5m') ?? ['1m', '5m'];
  const heartbeat = accumulatorMode
    ? online && !accumulators.isError && !!accumulators.data && accumulators.data.supportedSymbols.length > 0
    : online && !forexClosed && !!engine && !engine.error && (engine.lastCycle ? now - engine.lastCycle < 20 : false);
  const idle = engine?.idleSeconds;
  const signalAvailability = accumulatorMode
    ? !online
      ? 'PAUSED'
      : accumulators.isError
        ? 'FEED UNAVAILABLE'
        : !accumulators.data
          ? 'CHECKING DERIV FEED'
          : accumulators.data.error
            ? 'ACCUMULATOR SCANNER ERROR'
              : noAccumulatorMarkets
                ? 'NO OPEN ACCU CONTRACTS'
                : activeAccumulators.length
                  ? `${activeAccumulators.length} LIVE-VALIDATED ACCU SETUP${activeAccumulators.length === 1 ? '' : 'S'}`
                  : 'NO VALIDATED ACCU SETUP · SCANNING'
    : !online
      ? 'PAUSED'
      : board.isError
        ? 'FEED UNAVAILABLE'
        : !data
          ? 'CHECKING FEED'
          : engine?.error
            ? 'ENGINE ERROR'
            : !engine?.enabled
              ? 'ENGINE DISABLED'
              : forexClosed
                ? `FOREX MARKET CLOSED · ${closedForexCount} PAIRS · WAITING FOR REOPEN`
              : modeMarkets?.length === 0
                ? 'NO ACTIVE MARKETS'
                : upcoming.length
                  ? `${upcoming.length} ACTIVE SIGNAL${upcoming.length === 1 ? '' : 'S'}`
                  : 'NO SIGNAL · SCANNING';
  return (
    <section className={`future-signals ${heartbeat ? 'engine-live' : ''}`} data-testid="future-signals-board" aria-live="polite">
      <div className="future-signals-heading">
        <span className="engine-heartbeat" aria-hidden="true"><i /><i /><i /></span>
        <h3 data-testid="future-signals-title">{accumulatorMode ? 'Deriv Accumulator setups' : 'Deriv Binary signals'}</h3>
        <span className="future-signals-state" data-testid="future-signals-engine-state">{!online ? 'PAUSED' : accumulatorMode ? accumulators.isError ? 'OFFLINE' : accumulators.data ? (heartbeat ? 'LIVE' : 'WAITING') : 'CONNECTING' : board.isError ? 'OFFLINE' : !engine ? 'CONNECTING' : engine.error ? `ERROR ${engine.error}` : engine.enabled ? (heartbeat ? 'LIVE' : 'WAITING') : 'DISABLED'}</span>
      </div>
      <p className="future-signals-availability" data-testid="signal-availability" role="status">{signalAvailability}</p>
      <div className="source-switch" aria-label="Signal source" data-testid="signal-source-switch"><span className="selected" data-testid="signal-source-deriv">{accumulatorMode ? `ACCU · ${accumulators.data?.supportedSymbols.length ?? '—'} supported pairs` : `Binary · ${supportedForexCount ?? modeMarkets?.length ?? '—'} real Forex pairs · fixed-time`}</span></div>
      {marketMode === 'BINARY' && <p className="future-signals-empty" data-testid="binary-signal-usage-note"><strong>Quotex: NOT CONNECTED · manual reference only</strong><br />These signals use Deriv Forex prices. Quotex prices, OTC pairs, and trade execution are not verified; only consider a signal if Quotex offers the same pair and expiry and its live quote agrees.</p>}
      {accumulatorMode ? <div className="future-signals-meta" data-testid="future-signals-meta">
        <span>ACCU markets <b>{accumulators.data?.supportedSymbols.length ?? '—'}</b></span>
        <span>Scanned <b>{accumulators.data?.scanned ?? '—'}</b></span>
        <span>Score <b>Volatility heuristic</b></span>
      </div> : <div className="future-signals-meta" data-testid="future-signals-meta">
        <span>Real Forex pairs <b>{supportedForexCount ?? modeMarkets?.length ?? '—'}</b></span>
        <span title="Setup score threshold; not a measured win probability">Setup score <b>{engine?.settings.threshold ?? '—'}/100</b></span>
        <span>Timeframes <b>{binaryTimeframes.join(' / ') || 'Not enabled'}</b></span>
        <span>Scan <b>{engine ? (engine.marketEvaluation ? `${engine.marketEvaluation.freshMarkets}/${engine.marketEvaluation.targets} mkts` : `${engine.evaluatedLastCycle} mkts`) : '—'}</b></span>
      </div>}
      {!accumulatorMode && <div className="future-signals-list" data-testid="future-signals-upcoming">
        {upcoming.length === 0 && <div className="future-signals-empty" data-testid="future-signals-empty">
          <Radar size={15} />
          <div>
            <strong>No qualified signal yet</strong>
            <p>{forexClosed ? `All ${closedForexCount} provider-listed real Forex pairs are closed. Fixed-time signal scanning resumes when Deriv reopens; no fabricated signals are shown.` : `Scanning real Deriv Forex pairs for ${binaryTimeframes.length ? binaryTimeframes.join(' and ') : 'no configured'} Rise/Fall signals.`}</p>
            {engine?.deepScan.active && <p className="future-signals-deep"><Zap size={10} /> Deep scan {idle !== null && idle !== undefined ? `· idle ${countdown(idle)}` : 'armed'}{engine.deepScan.lastResult ? ` · scanned ${engine.deepScan.lastResult.scanned}` : ''}</p>}
          </div>
        </div>}
        {upcoming.map(signal => {
          const toEntry = signal.entryEpoch - now;
          const phase = toEntry > 0 ? 'ENTRY IN' : signal.expiryEpoch - now > 0 ? 'RUNNING' : 'VERIFYING';
          const remaining = toEntry > 0 ? toEntry : Math.max(0, signal.expiryEpoch - now);
          return <button type="button" key={signal.id} data-testid={`future-signal-${signal.id}`} className={`future-signal-row signal-${signal.direction.toLowerCase()} ${phase === 'RUNNING' ? 'running' : ''}`} onClick={() => onSelectPair?.(pairIdFor(signal, derivMarkets))} title={`Agree: ${signal.agreeing.join(', ') || '—'} · Oppose: ${signal.opposing.join(', ') || '—'} · ${sourceName(signal.source)}`}>
            <CountryFlags pairId={pairIdFor(signal, derivMarkets)} markets={modeMarkets ?? derivMarkets} />
            <div className="future-signal-copy">
              <SignalLine signal={signal} marketMode={marketMode} />
              <small>{sourceName(signal.source)} · {signal.mode === 'DEEP_SCAN' ? 'Deep scan' : 'Standard'} · {signal.agreeing.length}/{signal.agreeing.length + signal.opposing.length} votes · {signal.trackRecord && signal.trackRecord.wins + signal.trackRecord.losses > 0 ? `${signal.trackRecord.wins}W/${signal.trackRecord.losses}L · ` : ''}LIVE MODEL VALIDATED</small>
            </div>
            <div className="future-signal-side">
              <span className="signal-countdown"><Clock3 size={9} /> {phase} {countdown(remaining)}</span>
            </div>
          </button>;
        })}
      </div>}
      {!accumulatorMode && recent.length > 0 && <div className="future-signals-recent" data-testid="future-signals-recent">
        <span className="future-signals-recent-title">Recent results · measured</span>
        {recent.map(signal => <div key={signal.id} className={`future-signal-result result-${signal.status.toLowerCase()}`} data-testid={`recent-signal-${signal.id}`}>
          <SignalLine signal={signal} marketMode={marketMode} />
          <b>{signal.status}</b>
        </div>)}
      </div>}
      {accumulatorMode && <section className="future-signals-recent" data-testid="accumulator-signals-board">
        <span className="future-signals-recent-title">Accumulator setups · {accumulators.data?.supportedSymbols.length ?? '—'} provider-confirmed markets</span>
        <p className="future-signals-empty">Volatility heuristic only; not a measured win probability or guarantee. Growth rate adapts to recent range.</p>
        {accumulators.isError && <p className="future-signals-empty" role="status">Accumulator feed unavailable. Check Deriv market data.</p>}
        {!accumulators.isError && activeAccumulators.length === 0 && <p className="future-signals-empty" data-testid="accumulator-signals-empty">{accumulators.data?.error ? `Scanner error: ${accumulators.data.error}` : noAccumulatorMarkets ? "Deriv currently reports no open markets with an ACCU contract. The list updates from the provider WebSocket." : "No demo-validated ACCU setup yet. Unvalidated candidates are hidden."}</p>}
        {activeAccumulators.map(signal => <div key={signal.id} className="future-signal-result" data-testid={`accumulator-signal-${signal.id}`}>
          <span><b>{signal.label}</b><small>{signal.symbol} · {signal.market ?? "Deriv"} · 1m · stability {signal.stabilityScore}/100 (not probability)</small></span>
          <span>ACCU · {signal.growthRatePercent}% growth · TP {signal.takeProfitPercent}% / SL {signal.stopLossPercent}% · {signal.reason}</span>
        </div>)}
      </section>}
    </section>
  );
}
