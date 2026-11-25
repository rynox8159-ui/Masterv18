import { marketNow } from "@/lib/marketClock";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from '@tanstack/react-query';
import { toast, Toaster } from "sonner";
import { apiGet, apiUrl } from '@/lib/api';
import { Button } from "@/components/ui/button";
import CountryFlags from "@/components/terminal/CountryFlags";
import LiveChart from "@/components/terminal/LiveChart";
import Brand from "@/components/terminal/Brand";
import MarketRail from "@/components/terminal/MarketRail";
import AgentRegistry from "@/components/terminal/AgentRegistry";
import FutureSignals from "@/components/terminal/FutureSignals";
import PreSignalNotification from "@/components/terminal/PreSignalNotification";
import EntryAlert from "@/components/terminal/EntryAlert";
import QuotexProxyDialog from "@/components/terminal/QuotexProxyDialog";
import RemoteBrowserDialog from "@/components/terminal/RemoteBrowserDialog";
import { MobileNavigation } from '@/components/terminal/MobileNavigation';
import BackendHealthPanel from "@/components/terminal/BackendHealthPanel";
import { SystemScanCards } from '@/components/terminal/SystemObservability';
import { getMarket, isActiveDerivInstrument, isBinarySignalTimeframe, isRealDerivForexInstrument, marketForSignal, marketFromDerivInstrument, markets, marketsForMode, signalMatchesMarketMode, type ActiveMarketMode, type Market } from "@/lib/markets";
import { useAccumulatorSignals, useLiveSignals, type AccumulatorBoard, type AccumulatorSignal } from '@/hooks/useLiveSignals';
import { analyzePair, sourceFor, symbolFor, useBackendEvents, useDerivInstruments, useLatestAnalysis, useRuntime, useTelemetryStream } from '@/hooks/useMarketBackend';
import {
  Activity,
  BarChart3,
  Bell,
  Bot,
  BrainCircuit,
  ChevronDown,
  CircleHelp,
  Clock3,
  DatabaseZap,
  Gauge,
  GitBranch,
  LayoutDashboard,
  Menu,
  PanelLeftClose,
  PanelLeft,
  PanelRightClose,
  PanelRight,
  Radio,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Terminal,
  TrendingDown,
  TrendingUp,
  Wifi,
  Zap,
  type LucideIcon,
} from "lucide-react";

type ProviderId = 'deriv' | 'ws_sniffer' | 'playwright_observer';
type ProviderRuntime = { state: string; configuration: Record<string, unknown> };
type ProviderControlStatus = { controlConfigured: boolean; controlKeyRequired?: boolean; providers: Record<ProviderId, ProviderRuntime & { log?: string[]; detail?: string }> };
const DISPLAY_TIERS = new Set(['LIVE_VALIDATED']);
const TIER_CAPTION: Record<string, string> = { LIVE_VALIDATED: 'LIVE-VALIDATED SYSTEM SIGNAL', PRECISION_SHADOW: 'SYSTEM SIGNAL · PRECISION CHECK OBSERVED', NOT_PROMOTED_SHADOW: 'SYSTEM SIGNAL · PROMOTION GATE OBSERVED', OBSERVATION_ONLY: 'OBSERVED BINARY SIGNAL' };
const fmtUtc = (epoch?: number) => epoch === undefined ? '—' : new Date(epoch * 1000).toISOString().slice(11, 19) + ' UTC';
const fmtLocal = (epoch?: number) => epoch === undefined ? '—' : new Date(epoch * 1000).toLocaleTimeString([], { hour12: false });

type MonitorModule = {
  id: string;
  name: string;
  description: string;
  state: string;
  status: "LIVE" | "IDLE" | "WARN" | "ERROR";
  icon: LucideIcon;
  metrics: string[];
};

interface SignalPreview {
  id: string;
  pairId: string;
  pair: string;
  duration: string;
  time: string;
  direction: "CALL" | "PUT";
  sequence: number;
  live: boolean;
  validationTier?: string;
  trackRecord?: { wins: number; losses: number; winRate: number | null } | null;
  confidence?: number;
  entryEpoch?: number;
  expiryEpoch?: number;
  price?: number;
  countdownSeconds?: number;
  generatedAt?: number;
  status?: string;
  agreeing?: string[];
  opposing?: string[];
  provenance?: string;
  source?: string;
  marketType?: string;
  contractType?: string;
  symbol?: string;
}

const isLiveAccumulatorSignal = (signal?: Partial<AccumulatorSignal> | null) => {
  if (!signal) return false;
  const validation = signal.validationReport;
  return signal.status === 'PENDING'
    && signal.validationTier === 'LIVE_VALIDATED'
    && signal.strategyTier === 'LIVE_VALIDATED'
    && validation?.status === 'VALIDATED'
    && validation.readyForLive === true
    && validation.source === signal.source
    && validation.symbol === signal.symbol
    && validation.timeframe === signal.timeframe;
};

function AccumulatorOutput({ signal, candidate, symbol, marketType, loading, error, scanned, unvalidatedCandidates, supportedPairCount, diagnostics }: { signal?: AccumulatorSignal; candidate?: AccumulatorSignal; symbol: string; marketType: string; loading: boolean; error?: string | null; scanned?: number; unvalidatedCandidates?: number; supportedPairCount: number; diagnostics?: AccumulatorBoard['scanDiagnostics'] }) {
  const displaySignal = isLiveAccumulatorSignal(signal) ? signal : undefined;
  const displayCandidate = candidate?.validationTier === 'PAPER_SHADOW' && candidate.validationReport?.readyForLive === false ? candidate : undefined;
  const status = displaySignal ? 'pending' : 'void';
  const statusText = displaySignal ? 'DEMO-VALIDATED' : displayCandidate ? 'SHADOW ONLY' : loading ? 'SCANNING' : error ? 'FEED ERROR' : supportedPairCount === 0 ? 'NO ACCU PAIRS' : 'NO LIVE SETUP';
  return <div data-testid="signal-output-card" className={`signal-output-card signal-output-accumulator ${displaySignal ? 'signal-accumulator' : 'signal-no_signal'}`}>
    <div className="signal-preview-caption"><span data-testid="signal-preview-caption">DERIV ACCUMULATOR SETUP</span><span className={`signal-status-badge status-${status}`}>{statusText}</span></div>
    <div className="signal-pair-heading"><div><span className="signal-field-label">Pair · {displaySignal?.market ?? marketType}</span><strong data-testid="signal-result-pair">{displaySignal?.label ?? symbol}</strong><small>{displaySignal?.symbol ?? 'Waiting for a provider-confirmed ACCU market'}</small></div></div>
    {displaySignal ? <>
      <div className="generated-signal-direction"><Zap size={23} /><strong>ACCU</strong><span>DEMO-VALIDATED SETUP</span><b className="brain-confidence">{displaySignal.stabilityScore}/100 stability</b></div>
      <div className="signal-detail-grid">
        <div><span className="signal-field-label">Growth rate</span><strong>{displaySignal.growthRatePercent}%</strong></div>
        <div><span className="signal-field-label">Timeframe</span><strong>1m</strong></div>
        <div><span className="signal-field-label">Take profit</span><strong>{displaySignal.takeProfitPercent}% of stake</strong></div>
        <div><span className="signal-field-label">Stop loss</span><strong>{displaySignal.stopLossPercent}% of stake</strong></div>
      </div>
      <p className="signal-preview-disclaimer">{displaySignal.reason} · stability is a heuristic, not a probability or profit guarantee. Demo validation is not a guarantee of live results.</p>
    </> : displayCandidate ? <>
      <div className="generated-signal-direction"><Activity size={21} /><strong>SHADOW CANDIDATE</strong><span data-testid="accumulator-candidate-warning">NOT LIVE-VALIDATED · NOT A TRADE SIGNAL</span><b className="brain-confidence">{displayCandidate.stabilityScore}/100 stability heuristic</b></div>
      <div className="signal-detail-grid">
        <div><span className="signal-field-label">Growth rate</span><strong>{displayCandidate.growthRatePercent}%</strong></div>
        <div><span className="signal-field-label">Timeframe</span><strong>1m</strong></div>
        <div><span className="signal-field-label">Take profit</span><strong>{displayCandidate.takeProfitPercent}% of stake</strong></div>
        <div><span className="signal-field-label">Stop loss</span><strong>{displayCandidate.stopLossPercent}% of stake</strong></div>
      </div>
      <p data-testid="accumulator-candidate-validation" className="signal-preview-disclaimer">Demo validation: {displayCandidate.validationReport?.samples ?? 0}/{displayCandidate.validationReport?.minSamples ?? 300} settled outcomes · {displayCandidate.validationReport?.reason?.replaceAll('_', ' ') ?? 'validation pending'}. This observation cannot trigger auto-trading.</p>
    </> : <p data-testid="signal-output-reason" className="signal-preview-disclaimer">{error ? `Accumulator scanner unavailable: ${error}` : loading ? 'Checking Deriv-confirmed ACCU contracts and verified candles…' : supportedPairCount === 0 ? 'Deriv currently reports no open markets with an ACCU contract. The pair list updates from the provider WebSocket; no unsupported symbols are added.' : diagnostics?.freshQuotes === 0 ? `No fresh quotes are available for the ${supportedPairCount} supported markets. Check that the Deriv feed is connected and markets are open.` : diagnostics?.closedCandleWindows === 0 ? 'The scanner is waiting for at least 30 closed, provider-verified 1-minute candles and a fresh quote before it can assess a setup.' : unvalidatedCandidates ? `${unvalidatedCandidates} recent shadow candidate${unvalidatedCandidates === 1 ? '' : 's'} exist on other markets. Select a market with a candidate to inspect it; none is a live signal until demo validation passes.` : `Scanned ${scanned ?? 0} supported markets; none passed the accumulator stability checks in this scan.`}</p>}
  </div>;
}

const marketTypeOf = (market: Market | undefined, mode?: ActiveMarketMode) => mode === 'BINARY' ? 'Deriv · Forex digital options' : !market ? 'Deriv' : market.category === 'Synthetics' ? 'Deriv · Synthetic index' : market.category === 'Currencies' ? 'Deriv · Forex' : market.category === 'Crypto' ? 'Deriv · Crypto' : market.category === 'Commodities' ? 'Deriv · Commodities' : market.category === 'Baskets' ? 'Deriv · Basket' : 'Deriv · Stock index';

function localEntryTime(epoch: number) {
  const date = new Date(epoch * 1000);
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const hours = String(Math.floor(Math.abs(offset) / 60)).padStart(2, '0');
  const minutes = String(Math.abs(offset) % 60).padStart(2, '0');
  return `${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })} UTC${sign}${hours}:${minutes}`;
}

const fallbackMonitorBlueprint = [
  { id: 'source-sync', name: 'Step 1 · Source sync' },
  { id: 'tick-ingest', name: 'Step 2 · Tick ingest' },
  { id: 'observer-bridge', name: 'Step 3 · Observer bridge' },
  { id: 'data-normalize', name: 'Step 4 · Data normalize' },
  { id: 'pattern-scan', name: 'Step 5 · Pattern scan' },
  { id: 'quality-gate', name: 'Step 6 · Quality gate' },
  { id: 'behaviour-rank', name: 'Step 7 · Behaviour rank' },
  { id: 'model-ensemble', name: 'Step 8 · Model ensemble' },
  { id: 'master-selection', name: 'Step 9 · Master selection' },
  { id: 'signal-output', name: 'Step 10 · Signal output' },
  { id: 'risk-control', name: 'Step 11 · Risk control' },
];

const monitorIcons: LucideIcon[] = [DatabaseZap, Radio, Wifi, GitBranch, ShieldCheck, Bot, BrainCircuit, Gauge, Activity, Zap, TrendingUp];

export default function Home() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [activePairId, setActivePairId] = useState(() => getMarket(params.get("pair") || 'eur-usd-regular').id);
  const [activeMarketMode, setActiveMarketMode] = useState<ActiveMarketMode>("FOREX");
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [systemOnline, setSystemOnline] = useState(true);
  const [signalPreview, setSignalPreview] = useState<SignalPreview | null>(null);
  const previewSequence = useRef(0);
  const [logFilter, setLogFilter] = useState("ALL");
  const [mobilePanel, setMobilePanel] = useState("chart");
  const [leftPanelVisible, setLeftPanelVisible] = useState(true);
  const [rightPanelVisible, setRightPanelVisible] = useState(true);
  const [timeframe, setTimeframe] = useState('1m');
  const [logHeight, setLogHeight] = useState(() => Number(localStorage.getItem('mc.eventLogHeight')) || 150);
  const startLogResize = (event: React.PointerEvent<HTMLDivElement>) => {
    const startY = event.clientY, startHeight = logHeight;
    let next = startHeight;
    const move = (e: PointerEvent) => { next = Math.min(Math.max(90, startHeight - (e.clientY - startY)), Math.round(window.innerHeight * 0.6)); setLogHeight(next); };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); localStorage.setItem('mc.eventLogHeight', String(next)); document.body.classList.remove('mc-resizing'); };
    document.body.classList.add('mc-resizing');
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  };
  const [signalNow, setSignalNow] = useState(() => marketNow() / 1000);
  const [activeCarouselId, setActiveCarouselId] = useState<string | null>(null);
  const carouselRef = useRef<HTMLDivElement>(null);
  const carouselCardsRef = useRef<SignalPreview[]>([]);
  const activeCarouselIdRef = useRef<string | null>(null);
  const carouselDrag = useRef<{ x: number; scrollLeft: number; moved: boolean } | null>(null);
  const [signalReason, setSignalReason] = useState('Waiting for analysis');
  const [dismissedPreSignalId, setDismissedPreSignalId] = useState<string | null>(null);
  const [remoteBrowserOpen, setRemoteBrowserOpen] = useState(false);
  const [remoteOperatorKey, setRemoteOperatorKey] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const requestId = useRef(0);
  const runtime = useRuntime(systemOnline);
  const derivInstruments = useDerivInstruments(systemOnline);
  const derivMarkets = useMemo(() => (derivInstruments.data?.items ?? [])
    .filter(isActiveDerivInstrument)
    .map(marketFromDerivInstrument), [derivInstruments.data]);
  const signalBoard = useLiveSignals('deriv', systemOnline);
  const accumulatorBoard = useAccumulatorSignals(systemOnline);
  const binaryPairCount = useMemo(() => new Set(
    (derivInstruments.data?.items ?? [])
      .filter(isRealDerivForexInstrument)
      .map(instrument => instrument.symbol.toUpperCase()),
  ).size, [derivInstruments.data]);
  const accumulatorSupportedCount = accumulatorBoard.data?.supportedSymbols.length;
  const forexCounts = useMemo(() => {
    const forex = (derivInstruments.data?.items ?? []).filter(instrument =>
      instrument.market === 'forex' && instrument.isOTC !== true && instrument.is_otc !== true,
    );
    return {
      open: forex.filter(instrument => instrument.isOpen === true).length,
      closed: forex.filter(instrument => instrument.isOpen === false).length,
    };
  }, [derivInstruments.data]);
  const marketUniverse = derivMarkets;
  const activePair = getMarket(activePairId, marketUniverse);
  const providerControl = useQuery({ queryKey: ['provider-controls'], queryFn: () => apiGet<ProviderControlStatus>('/v1/providers/status'), refetchInterval: 5000 });
  const railUniverse = derivMarkets;
  const accumulatorMarkets = useMemo(() => {
    const supportedSymbols = new Set((accumulatorBoard.data?.supportedSymbols ?? []).map(pair => pair.symbol.toUpperCase()));
    return railUniverse.filter(market => supportedSymbols.has((market.derivSymbol ?? market.symbol).toUpperCase()));
  }, [accumulatorBoard.data?.supportedSymbols, railUniverse]);
  const modeMarkets = useMemo(
    () => activeMarketMode === 'FOREX' ? accumulatorMarkets : marketsForMode(activeMarketMode, railUniverse),
    [activeMarketMode, accumulatorMarkets, railUniverse],
  );
  const modeMarketIds = useMemo(() => new Set(modeMarkets.map(market => market.id)), [modeMarkets]);
  useEffect(() => {
    if (modeMarkets.length && !modeMarketIds.has(activePairId)) setActivePairId(modeMarkets[0].id);
  }, [activePairId, modeMarketIds, modeMarkets]);
  const [providerControlBusy, setProviderControlBusy] = useState<ProviderId | null>(null);
  const [proxyOpen, setProxyOpen] = useState(false);
  const analysis = useLatestAnalysis(activePair, timeframe);
  const events = useBackendEvents();
  const telemetryStream = useTelemetryStream(sourceFor(activePair), symbolFor(activePair), timeframe, systemOnline);
  const chartControls = useMemo(() => <div className="chart-panel-controls" data-testid="chart-panel-controls"><Button data-testid="toggle-left-panel-button" variant="ghost" size="icon-sm" aria-label="Toggle system monitor" aria-pressed={leftPanelVisible} title="Show / hide system monitor" onClick={() => setLeftPanelVisible(value => !value)}><PanelLeft size={15} /></Button><Button data-testid="toggle-right-panel-button" variant="ghost" size="icon-sm" aria-label="Toggle intelligence engine" aria-pressed={rightPanelVisible} title="Show / hide intelligence engine" onClick={() => setRightPanelVisible(value => !value)}><PanelRight size={15} /></Button></div>, [leftPanelVisible, rightPanelVisible]);
  const telemetry = telemetryStream.data;
  const activeDerivSymbol = activePair.derivSymbol ?? activePair.sourceSymbol ?? activePair.symbol;
  const activeAccumulatorSignal = (accumulatorBoard.data?.items ?? [])
    .filter(signal => isLiveAccumulatorSignal(signal) && signal.symbol === activeDerivSymbol && signal.expiresAt >= signalNow)
    .sort((a, b) => b.generatedAt - a.generatedAt)[0];
  const activeAccumulatorCandidate = (accumulatorBoard.data?.paperCandidates ?? [])
    .filter(candidate => candidate.validationTier === 'PAPER_SHADOW' && candidate.symbol === activeDerivSymbol && candidate.expiresAt >= signalNow)
    .sort((a, b) => b.generatedAt - a.generatedAt)[0];
  const openRemoteBrowser = () => {
    const keyRequired = providerControl.data?.controlKeyRequired === true;
    const key = keyRequired ? window.prompt('Operator key (PROVIDER_CONTROL_KEY from backend/.env)') : '';
    if (keyRequired && !key) return;
    setRemoteOperatorKey(key ?? '');
    setRemoteBrowserOpen(true);
  };
  const toggleProvider = async (provider: ProviderId) => {
    const currentlyRunning = providerControl.data?.providers[provider]?.state === 'RUNNING';
    const action = currentlyRunning ? 'stop' : 'start';
    if (provider === 'playwright_observer' && action === 'start') {
      const configuredTarget = providerControl.data?.providers.playwright_observer.configuration.targetUrl;
      const targetUrl = typeof configuredTarget === 'string' && /^https?:\/\//i.test(configuredTarget)
        ? configuredTarget
        : 'https://market-qx.info/en/trade';
      const loginWindow = window.open(targetUrl, '_blank');
      if (!loginWindow) {
        toast.error('Browser tab was blocked', { description: 'Allow pop-ups for this app, then click Playwright Observer again.' });
        return;
      }
      loginWindow.opener = null;
    }
    const keyRequired = providerControl.data?.controlKeyRequired === true;
    localStorage.setItem('operatorKeyRequired', keyRequired ? '1' : '0');
    const controlKey = keyRequired ? window.prompt('Operator key (PROVIDER_CONTROL_KEY from main_source_code/backend/.env)') : '';
    if (keyRequired && !controlKey) return;
    setProviderControlBusy(provider);
    try {
      const response = await fetch(apiUrl(`/v1/providers/${provider}/${action}`), {
        method: 'POST',
        credentials: 'include',
        headers: controlKey ? { 'X-Provider-Control-Key': controlKey } : {},
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || `Provider request failed (${response.status})`);
      await providerControl.refetch();
      toast.success(provider === 'playwright_observer' && action === 'start' ? 'Quotex opened; observer start requested' : `${provider.replaceAll('_', ' ')} ${action} requested`, { description: provider === 'playwright_observer' && action === 'start' ? 'Log in in the new tab. If this app uses a remote backend (such as Codespaces), that server cannot use the tab or its login session; run the observer on your PC to send its market data.' : undefined });
      if (provider !== 'deriv' && action === 'start') window.setTimeout(async () => {
        const status = (await providerControl.refetch()).data?.providers[provider];
        if (status?.state === 'FAILED') toast.error(`${provider.replaceAll('_', ' ')} stopped`, { description: (status.log ?? []).slice(-3).join(' · ') || 'See backend/logs for details.' });
      }, 6000);
    } catch (error) {
      toast.error('Provider control failed', { description: error instanceof Error ? error.message : 'Backend unavailable' });
    } finally {
      setProviderControlBusy(null);
    }
  };
  useEffect(() => {
    const timer = window.setInterval(() => setSignalNow(marketNow() / 1000), 500);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => { requestId.current += 1; setAnalyzing(false); }, [activePairId, timeframe]);
  useEffect(() => { setTimeframe('1m'); }, [activePairId]);
  useEffect(() => { setSignalPreview(null); setSignalReason(analysis.data?.signal.reason.replaceAll('_', ' ') || 'Waiting for analysis'); }, [activePairId, analysis.data]);
  useEffect(() => {
    setSignalPreview(null);
    setSignalReason(activeMarketMode === "BINARY" ? "Waiting for Deriv Forex digital-option signals" : "Waiting for Deriv market analysis");
  }, [activeMarketMode]);
  const runAction = async (label: string) => {
    const started = performance.now();
    const result = await runtime.refetch();
    const elapsed = Math.round(performance.now() - started);
    if (result.isError) { toast.error('Backend connection unavailable'); return; }
    const data = result.data;
    const errors = [data?.analysisError, ...data?.providers.map(p => p.error) || []].filter(Boolean);
    toast(label.includes('latency') ? `API response · ${elapsed} ms` : label.includes('error') ? 'Connection diagnostics' : 'Data refreshed', { description: label.includes('error') ? errors.join(' · ') || 'No current connection errors.' : `${data?.database} · ${data?.analysisCycles} analysis cycles · ${data?.agents.active} active agents` });
  };
  const actualLogs = events.data?.items.map(row => ({ ...row, time: row.time.slice(11, 23) })) || [];
  const filteredLogs = logFilter === "ALL" ? actualLogs : actualLogs.filter((row) => row.level === logFilter);
  const generateSignalPreview = async (_requestedDirection?: "CALL" | "PUT") => {
    const id = ++requestId.current;
    setAnalyzing(true);
    try {
      const result = await analyzePair(activePair, timeframe);
      if (id !== requestId.current) return;
      setSignalReason(result.signal.reason.replaceAll('_', ' '));
      setSignalPreview(null);
      if (result.signal.direction !== 'NO_SIGNAL') {
        previewSequence.current += 1;
        setSignalPreview({ id: `preview-${previewSequence.current}`, pairId: activePair.id, pair: activePair.label, duration: timeframe, time: new Date().toLocaleTimeString(), direction: result.signal.direction, sequence: previewSequence.current, live: false });
      }
      await analysis.refetch();
    } catch { setSignalReason('Backend analysis unavailable'); toast.error('Analysis unavailable'); }
    finally { if (id === requestId.current) setAnalyzing(false); }
  };
  const signalProbability = Math.min(99, Math.max(0, Number(telemetry?.evidence.confidence ?? analysis.data?.signal.modelAgreement ?? analysis.data?.quality?.score ?? 0)));
  const liveCountdown = telemetry?.signal ? Math.max(0, Math.ceil(telemetry.signal.expiryEpoch - (telemetry.timestamp || marketNow() / 1000))) : 0;
  const runtimeDeriv = runtime.data?.providers.find(provider => provider.source === 'deriv');
  const telemetryModules = telemetry?.modules?.length ? telemetry.modules : fallbackMonitorBlueprint.map((module, index) => {
    const derivConnected = runtimeDeriv?.state === 'CONNECTED' || runtimeDeriv?.state === 'DATA_RECEIVING';
    const snapshotError = telemetry?.feed.error;
    const waitingState = snapshotError
      ? `SNAPSHOT ERROR · ${snapshotError}`
      : telemetryStream.connected
        ? 'WAITING FOR SNAPSHOT'
        : derivConnected
          ? 'TELEMETRY RECONNECTING'
          : runtime.isError
            ? 'BACKEND UNAVAILABLE'
            : 'WAITING FOR FEED';
    const waitingTone = snapshotError || runtime.isError
      ? 'warn' as const
      : telemetryStream.connected || derivConnected
        ? 'live' as const
        : 'idle' as const;

    if (index === 0 && runtimeDeriv) {
      return {
        id: module.id,
        name: module.name,
        state: runtimeDeriv.state,
        tone: derivConnected ? 'live' as const : 'warn' as const,
        summary: `Deriv provider ${derivConnected ? 'connected' : 'not receiving'} · waiting for market telemetry`,
        value: `${runtimeDeriv.acceptedCount ?? 0}/${runtimeDeriv.requestedCount ?? 0} subscriptions`,
        metrics: [
          `Ticks ${runtimeDeriv.acceptedCount ?? 0}/${runtimeDeriv.requestedCount ?? 0}`,
          `Age ${runtimeDeriv.ageSeconds === undefined ? '—' : `${Math.max(0, runtimeDeriv.ageSeconds).toFixed(1)}s`}`,
          `Rejected ${runtimeDeriv.rejectedCount ?? 0}`,
        ],
      };
    }
    if (index === 1 && runtime.data) {
      return {
        id: module.id,
        name: module.name,
        state: runtime.data.ticksReceived > 0 ? 'RECEIVING' : waitingState,
        tone: runtime.data.ticksReceived > 0 ? 'live' as const : waitingTone,
        summary: runtime.data.ticksReceived > 0 ? 'Backend has ingested live market ticks' : 'Waiting for the first backend tick',
        value: `${runtime.data.ticksReceived} ticks received`,
        metrics: [
          `Ticks ${runtime.data.ticksReceived}`,
          `Cycles ${runtime.data.analysisCycles}`,
          `Streams ${runtimeDeriv?.acceptedCount ?? 0}`,
        ],
      };
    }
    return {
      id: module.id,
      name: module.name,
      state: waitingState,
      tone: waitingTone,
      summary: snapshotError
        ? `Telemetry snapshot failed · ${snapshotError}`
        : telemetryStream.connected
          ? 'Telemetry stream connected · waiting for module snapshot'
          : derivConnected
            ? 'Deriv feed is live · reconnecting the telemetry stream'
            : 'Waiting for backend and telemetry connection',
      value: 'Telemetry snapshot pending',
      metrics: [
        `Backend ${runtime.data?.database ?? (runtime.isError ? 'ERROR' : 'CHECKING')}`,
        `Deriv ${runtimeDeriv?.state ?? 'CHECKING'}`,
        `Cycles ${runtime.data?.analysisCycles ?? '—'}`,
      ],
    };
  });
  const liveModules: MonitorModule[] = telemetryModules.map((step, index) => ({
    id: step.id,
    name: step.name,
    description: step.summary,
    state: step.state,
    status: step.tone === 'warn' ? 'WARN' : step.tone === 'live' ? 'LIVE' : 'IDLE',
    icon: monitorIcons[index % monitorIcons.length],
    metrics: step.metrics,
  }));
  const probabilityCard: MonitorModule = {
    id: 'live-probability',
    name: 'Probability & timer',
    description: 'Live signal confidence · expiry countdown',
    state: telemetry?.evidence.gate ?? 'WAITING',
    status: telemetry?.status === 'ONLINE' ? 'LIVE' : telemetry?.status === 'DATA_MISMATCH' || telemetry?.status === 'GAP_DETECTED' ? 'WARN' : 'IDLE',
    icon: Gauge,
    metrics: [`${signalProbability.toFixed(0)}% confidence`, `${liveCountdown.toString().padStart(2, '0')}s to expiry`, `${telemetry?.evidence.direction ?? 'NO_SIGNAL'}`],
  };
  const monitoringCards: MonitorModule[] = [...liveModules, probabilityCard];
  const globalPreSignal = activeMarketMode === 'BINARY'
    ? (signalBoard.data?.preSignals ?? []).find(item => item.id !== dismissedPreSignalId && item.entryEpoch > signalNow && marketForSignal(item, marketUniverse)?.id === activePair.id && signalMatchesMarketMode(item, activeMarketMode, modeMarkets)) ?? null
    : null;
  const resetSignalPreview = () => {
    setSignalPreview(null);
    previewSequence.current = 0;
  };
  const streamSignal = activeMarketMode === 'BINARY' && DISPLAY_TIERS.has(telemetry?.signal?.validationTier ?? '') && telemetry?.signal && modeMarketIds.has(activePair.id) && signalMatchesMarketMode(telemetry.signal, activeMarketMode, modeMarkets) && marketForSignal(telemetry.signal, marketUniverse)?.id === activePair.id ? {
    id: telemetry.signal.id ?? `telemetry-${telemetry.signal.entryEpoch}`,
    pairId: activePair.id,
    pair: activePair.label,
    duration: telemetry.signal.timeframe,
    time: new Date(telemetry.signal.entryEpoch * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC'),
    direction: telemetry.signal.direction,
    sequence: 0,
    live: true,
    validationTier: telemetry.signal.validationTier,
    entryEpoch: telemetry.signal.entryEpoch,
    expiryEpoch: telemetry.signal.expiryEpoch,
    price: telemetry.price ?? undefined,
    countdownSeconds: Math.max(0, Math.ceil(telemetry.signal.entryEpoch - signalNow)),
    confidence: telemetry.signal.confidence,
    status: telemetry.signal.status,
    source: telemetry.signal.source,
    symbol: telemetry.signal.symbol,
    marketType: marketTypeOf(activePair, activeMarketMode),
    contractType: activeMarketMode === 'BINARY' ? 'Digital Options · Rise/Fall' : 'Rise/Fall',
    ...(() => { const row = [...(signalBoard.data?.upcoming ?? []), ...(signalBoard.data?.recent ?? [])].find(item => item.id === telemetry.signal?.id); return row ? { generatedAt: row.generatedAt, agreeing: row.agreeing, opposing: row.opposing, provenance: row.provenance, trackRecord: row.trackRecord } : {}; })(),
  } as SignalPreview : null;
  const displayedSignal = streamSignal ?? (modeMarketIds.has(activePair.id) && signalPreview?.pairId === activePair.id && (activeMarketMode !== 'BINARY' || isBinarySignalTimeframe(signalPreview.duration)) ? signalPreview : null);
  const upcomingSignalCards: SignalPreview[] = (signalBoard.data?.upcoming ?? [])
    .filter(item => {
      const market = marketForSignal(item, marketUniverse);
      return activeMarketMode === 'BINARY' && DISPLAY_TIERS.has(item.validationTier ?? '') && item.status === 'PENDING' && item.expiryEpoch > signalNow && market?.id === activePair.id && modeMarketIds.has(market.id) && signalMatchesMarketMode(item, activeMarketMode, modeMarkets);
    })
    .map(item => ({
      id: item.id,
      pairId: marketForSignal(item, marketUniverse)?.id ?? activePair.id,
      pair: item.label,
      duration: item.timeframe,
      time: new Date(item.entryEpoch * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC'),
      direction: item.direction,
      sequence: 0,
      live: true,
      validationTier: item.validationTier,
      trackRecord: item.trackRecord,
      confidence: item.confidence,
      entryEpoch: item.entryEpoch,
      expiryEpoch: item.expiryEpoch,
      countdownSeconds: Math.max(0, Math.ceil(item.entryEpoch - signalNow)),
      price: undefined,
      generatedAt: item.generatedAt, status: item.status, agreeing: item.agreeing, opposing: item.opposing, provenance: item.provenance, source: item.source, symbol: item.symbol,
      marketType: marketTypeOf(marketForSignal(item, marketUniverse), activeMarketMode), contractType: activeMarketMode === 'BINARY' ? 'Digital Options · Rise/Fall' : 'Rise/Fall',
    }));
  const recentSignalCards: SignalPreview[] = (signalBoard.data?.recent ?? [])
    .filter(item => { const market = marketForSignal(item, marketUniverse); return activeMarketMode === 'BINARY' && DISPLAY_TIERS.has(item.validationTier ?? '') && item.status !== 'PENDING' && signalNow - item.expiryEpoch < 15 * 60 && market?.id === activePair.id && modeMarketIds.has(market.id) && signalMatchesMarketMode(item, activeMarketMode, modeMarkets); })
    .slice(0, 3)
    .map(item => ({
      id: item.id, pairId: marketForSignal(item, marketUniverse)?.id ?? activePair.id, pair: item.label, duration: item.timeframe,
      time: new Date(item.entryEpoch * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC'), direction: item.direction, sequence: 0, live: true,
      validationTier: item.validationTier, trackRecord: item.trackRecord, confidence: item.confidence, entryEpoch: item.entryEpoch, expiryEpoch: item.expiryEpoch,
      countdownSeconds: 0, price: undefined, generatedAt: item.generatedAt, status: item.status, agreeing: item.agreeing, opposing: item.opposing, provenance: item.provenance,
      source: item.source, symbol: item.symbol, marketType: marketTypeOf(marketForSignal(item, marketUniverse), activeMarketMode), contractType: activeMarketMode === 'BINARY' ? 'Digital Options · Rise/Fall' : 'Rise/Fall',
    }));
  const queueCards = [...(streamSignal ? [streamSignal] : []), ...upcomingSignalCards.filter(card => card.id !== streamSignal?.id), ...recentSignalCards.filter(card => card.id !== streamSignal?.id)];
  const carouselCards = queueCards.length ? queueCards.slice(0, 4) : displayedSignal ? [displayedSignal] : [];
  const carouselSignature = carouselCards.map(card => card.id).join('|');
  carouselCardsRef.current = carouselCards;
  activeCarouselIdRef.current = activeCarouselId;
  const activeCarouselIndex = Math.max(0, carouselCards.findIndex(card => card.id === activeCarouselId));
  useEffect(() => {
    const cards = carouselCardsRef.current;
    const selectedId = activeCarouselIdRef.current;
    const nextIndex = Math.max(0, cards.findIndex(card => card.id === selectedId));
    const nextCard = cards[nextIndex];
    if (nextCard && nextCard.id !== selectedId) {
      activeCarouselIdRef.current = nextCard.id;
      setActiveCarouselId(nextCard.id);
    }
    carouselRef.current?.scrollTo({ left: (carouselRef.current?.clientWidth ?? 0) * nextIndex, behavior: 'auto' });
  }, [carouselSignature]);
  const syncCarouselIndex = () => {
    const viewport = carouselRef.current;
    if (!viewport || !carouselCards.length) return;
    const index = Math.max(0, Math.min(carouselCards.length - 1, Math.round(viewport.scrollLeft / Math.max(1, viewport.clientWidth))));
    setActiveCarouselId(carouselCards[index].id);
  };
  const selectCarouselCard = (index: number) => {
    const card = carouselCards[index];
    if (!card) return;
    setActiveCarouselId(card.id);
    carouselRef.current?.scrollTo({ left: carouselRef.current.clientWidth * index, behavior: 'smooth' });
  };
  useEffect(() => {
    const viewport = carouselRef.current;
    if (!viewport) return;
    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      event.preventDefault();
      viewport.scrollLeft += event.deltaY;
    };
    viewport.addEventListener('wheel', onWheel, { passive: false });
    return () => viewport.removeEventListener('wheel', onWheel);
  }, [carouselCards.length]);
  const hidePanel = (side: "left" | "right") => {
    if (window.matchMedia("(max-width: 980px)").matches) {
      setMobilePanel("chart");
      document.querySelector<HTMLButtonElement>('[data-testid="mobile-panel-chart"]')?.focus();
      return;
    }
    if (side === "left") setLeftPanelVisible(false);
    else setRightPanelVisible(false);
    document.querySelector<HTMLButtonElement>(`[data-testid="toggle-${side}-panel-button"]`)?.focus();
  };

  return (
    <div data-testid="terminal-app" className="terminal-shell">
      <Toaster position="bottom-right" richColors theme="dark" />
      {(['ws_sniffer', 'playwright_observer'] as const).map(id => {
        const provider = providerControl.data?.providers[id];
        if (!provider || (provider.state !== 'RUNNING' && provider.state !== 'FAILED')) return null;
        const warn = provider.state === 'FAILED' || /CLOUDFLARE|LOGIN|NOT reachable|REGION BLOCKED/i.test(provider.detail ?? '');
        return <div key={id} data-testid={`observer-status-${id.replace('_', '-')}`} className={`observer-status-banner ${warn ? 'warn' : provider.detail?.startsWith('streaming') ? 'ok' : ''}`}>
          <b>{id === 'ws_sniffer' ? 'WS Sniffer' : 'Playwright Observer'}</b>
          <span>{provider.state === 'FAILED' ? (provider.log ?? []).filter(line => /Error|error/.test(line)).slice(-1)[0]?.slice(0, 180) ?? 'stopped with an error' : provider.detail ?? 'starting…'}</span>
          {id === 'playwright_observer' && provider.state === 'RUNNING' && <button
            type="button"
            data-testid="open-remote-browser-button"
            title="View the observer browser (log in to Quotex)"
            onClick={openRemoteBrowser}
          >View browser</button>}
          {id === 'playwright_observer' && <button type="button" data-testid="open-qx-proxy-button" onClick={() => setProxyOpen(true)}>{/REGION BLOCKED/i.test(provider.detail ?? '') ? 'Set proxy' : 'Proxy'}</button>}
        </div>;
      })}
      {activeMarketMode === 'BINARY' && <PreSignalNotification notification={telemetryStream.preSignal && !isBinarySignalTimeframe(telemetryStream.preSignal.timeframe) ? globalPreSignal : telemetryStream.preSignal ?? globalPreSignal} onDismiss={() => { telemetryStream.dismissPreSignal(); if (globalPreSignal) setDismissedPreSignalId(globalPreSignal.id); }} />}
      <EntryAlert cards={[...(streamSignal ? [streamSignal] : []), ...upcomingSignalCards]} now={signalNow} />
      <MobileNavigation open={mobileMenuOpen} onOpenChange={setMobileMenuOpen} />
      <QuotexProxyDialog open={proxyOpen} onOpenChange={setProxyOpen} />

      <header data-testid="terminal-header" className="terminal-header">
        <button data-testid="mobile-menu-button" aria-label="Open navigation" aria-expanded={mobileMenuOpen} className="rounded-lg p-2 text-slate-400 transition hover:bg-[#1f2635] hover:text-white md:hidden" onClick={() => setMobileMenuOpen(true)}>
          <Menu size={18} />
        </button>
        <Brand />
        <div data-testid="header-spacer" className="flex-1" />
        <div data-testid="header-command-bar" className="header-command-bar">
        <div data-testid="terminal-mode-controls" className="panel-visibility-controls mode-controls" role="group" aria-label="Agent registry and trading mode">
            <Button data-testid="auto-trade-mode-button" variant="ghost" className="panel-visibility-button" onClick={() => navigate("/autotrade")}><Bot size={16} /><span>Auto Trade</span></Button>
            <Button data-testid="open-agents-button" variant="ghost" className="panel-visibility-button" title="Active Deriv streams / requested instruments; configured slots are not independent AI agents" aria-expanded={agentsOpen} onClick={() => setAgentsOpen(true)}><Bot size={16} /><span>Agents</span><b>{runtime.data?.providers.find(provider => provider.source === 'deriv')?.acceptedCount ?? 0}/{runtime.data?.providers.find(provider => provider.source === 'deriv')?.requestedCount ?? 0}</b></Button>
            <Button data-testid="trade-mode-button" variant="ghost" className="panel-visibility-button trade-mode-button" onClick={() => navigate(`/trade?pair=${encodeURIComponent(activePair.id)}`)}><BarChart3 size={16} /><span>Trade</span><span className="panel-toggle-track" aria-hidden="true"><i /></span></Button>
          </div>
          <div data-testid="market-mode-switch" className="market-mode-switch" role="group" aria-label="Market view">
            {([{ id: "FOREX", label: "Deriv", description: "Deriv markets and Accumulator setups" }, { id: "BINARY", label: "Binary", description: "Fixed-time Binary signals from Deriv" }] as const).map(mode => <button key={mode.id} type="button" data-testid={`market-mode-${mode.id.toLowerCase()}-button`} aria-label={mode.description} title={mode.description} aria-pressed={activeMarketMode === mode.id} className={activeMarketMode === mode.id ? "selected" : ""} onClick={() => { setActiveMarketMode(mode.id); if (mode.id === "BINARY") setTimeframe("1m"); }}>{mode.label}</button>)}
          </div>
          <button data-testid="header-notification-button" aria-label="Notifications" className="notification-button" onClick={() => navigate("/logs")}>
            <Bell size={17} />
            
          </button>
          <button data-testid="header-account-button" className="account-button" onClick={() => runAction("Workspace panel opened")}>
            <span data-testid="account-avatar" className="grid h-6 w-6 place-items-center rounded-full bg-[#1f8a78] text-[10px] font-bold">AM</span>
            <span data-testid="account-copy"><span className="block text-[9px] text-[#00c278]">WORKSPACE</span><span className="block text-xs font-semibold text-white">alpha-monitor</span></span>
            <ChevronDown size={14} className="text-slate-500" />
          </button>
        </div>
        <div data-testid="header-system-state" className={`header-system-state ${systemOnline && telemetryStream.status !== 'ONLINE' ? "offline" : ""}`}>
          <span className="tiny-dot" /> {systemOnline ? telemetryStream.status : 'DISCONNECTED'}
        </div>
      </header>

      <div className="terminal-body">
        <aside data-testid="left-navigation-rail" className="navigation-rail">
          <div data-testid="nav-primary-group" className="flex w-full flex-col items-center gap-2">
            <NavButton icon={LayoutDashboard} label="Terminal" active testId="nav-terminal-button" onClick={() => runAction("Terminal view selected")} />
            <NavButton icon={Activity} label="Binary" ariaLabel="Binary signals" testId="nav-signals-button" onClick={() => navigate('/signals')} />
            <NavButton icon={Bot} label="Auto Trade" testId="nav-auto-trade-button" onClick={() => navigate('/autotrade')} />
            <NavButton icon={BarChart3} label="Analytics" testId="nav-analytics-button" onClick={() => navigate('/analytics')} />
            <NavButton icon={GitBranch} label="Flow map" testId="nav-flow-button" onClick={() => navigate('/flow')} />
          </div>
          <div data-testid="nav-divider" className="my-2 h-px w-8 bg-[#222a3b]" />
          <div data-testid="nav-secondary-group" className="flex w-full flex-col items-center gap-2">
            <NavButton icon={BrainCircuit} label="Training" testId="nav-training-button" onClick={() => navigate('/training')} />
            <NavButton icon={ShieldCheck} label="Accuracy" testId="nav-accuracy-button" onClick={() => navigate('/accuracy')} />
            <NavButton icon={Terminal} label="Logs" testId="nav-logs-button" onClick={() => navigate('/logs')} />
            <NavButton icon={Settings2} label="Settings" testId="nav-settings-button" onClick={() => navigate('/settings')} />
            <NavButton icon={Radio} label="Deriv" active={providerControl.data?.providers.deriv.state === 'RUNNING'} title="Toggle Deriv public feed" disabled={providerControlBusy === 'deriv'} pressed={providerControl.data?.providers.deriv.state === 'RUNNING'} testId="nav-provider-deriv-button" onClick={() => void toggleProvider('deriv')} />
            <NavButton icon={Wifi} label="WS Sniffer" active={providerControl.data?.providers.ws_sniffer.state === 'RUNNING'} title={providerControl.data?.providers.ws_sniffer.detail ?? "WS Sniffer: attaches to your Chrome (port 9222) or opens one, reads Quotex websocket"} disabled={providerControlBusy === 'ws_sniffer'} pressed={providerControl.data?.providers.ws_sniffer.state === 'RUNNING'} testId="nav-provider-ws-sniffer-button" onClick={() => void toggleProvider('ws_sniffer')} />
            <NavButton icon={Activity} label="Observer" ariaLabel="Playwright Observer" active={providerControl.data?.providers.playwright_observer.state === 'RUNNING'} title={providerControl.data?.providers.playwright_observer.detail ?? "Playwright Observer: open Chromium on the backend PC, log in to Quotex, and stream observed pairs and prices"} disabled={providerControlBusy === 'playwright_observer'} pressed={providerControl.data?.providers.playwright_observer.state === 'RUNNING'} testId="nav-provider-playwright-observer-button" onClick={() => void toggleProvider('playwright_observer')} />
          </div>
          <div data-testid="nav-footer" className="mt-auto flex flex-col items-center gap-3">
            <button data-testid="nav-help-button" className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 transition hover:bg-[#1f2635] hover:text-white" onClick={() => toast("Master Candle", { description: "Live Deriv analysis. No real-money execution. Trade mode uses virtual funds with live Deriv prices." })}><CircleHelp size={17} /></button>
            <div data-testid="nav-build-version" className="font-mono text-[9px] text-slate-600">v17.1</div>
          </div>
        </aside>

        <main data-testid="terminal-workspace" className="terminal-workspace">
          <div data-testid="mobile-nav-strip" className="mobile-panel-tabs">
            {[{ id: "infrastructure", label: "Data & connection" }, { id: "chart", label: "Trading chart" }, { id: "analysis", label: "Analysis & output" }].map(item => <button key={item.id} data-testid={`mobile-panel-${item.id}`} className={mobilePanel === item.id ? "selected" : ""} onClick={() => setMobilePanel(item.id)}>{item.label}</button>)}
          </div>

          <MarketRail activePairId={activePairId} onSelect={setActivePairId} online={systemOnline} live marketMode={activeMarketMode} derivMarkets={railUniverse} pairOptions={activeMarketMode === 'FOREX' ? accumulatorMarkets : undefined} livePrices={derivInstruments.data?.items} showClosedTabs={activeMarketMode === 'BINARY'} emptyText={activeMarketMode === 'FOREX' ? 'Waiting for provider-confirmed ACCU markets' : 'Waiting for live Deriv Forex instruments'} />
          <div data-testid="main-terminal-grid" style={{ '--event-log-height': `${logHeight}px` } as React.CSSProperties} className={`main-terminal-grid mobile-show-${mobilePanel} ${leftPanelVisible ? "" : "left-panel-hidden"} ${rightPanelVisible ? "" : "right-panel-hidden"}`}>
            <aside id="infrastructure-panel" data-testid="left-monitoring-rail" className="monitor-panel infrastructure-panel">
              <div data-testid="infrastructure-header" className="panel-heading"><span className="panel-heading-icon"><DatabaseZap size={17} /></span><div><h2 data-testid="infrastructure-title">System monitor</h2><p data-testid="infrastructure-subtitle">INFRASTRUCTURE & INGESTION</p></div><Button data-testid="hide-left-panel-button" variant="ghost" size="icon-sm" className="panel-collapse-button" aria-label="Hide system monitor" title="Hide panel to enlarge chart" onClick={() => hidePanel("left")}><PanelLeftClose size={17} /></Button></div>
              <BackendHealthPanel />
              <div data-testid="monitoring-summary-strip" className="monitor-summary"><SummaryMetric value={String(monitoringCards.length)} label="modules" tone="blue" /><SummaryMetric value={systemOnline ? String(monitoringCards.filter(m => m.status === 'LIVE').length) : "00"} label="streaming" tone="green" /><SummaryMetric value={systemOnline ? String(monitoringCards.filter(m => m.status !== 'LIVE').length) : String(monitoringCards.length)} label="not-ready" tone="amber" /></div>
              <div data-testid="left-monitoring-cards-list" className="monitor-cards-list">{monitoringCards.slice(0, 4).map(module => <MonitorCard key={module.id} module={module} systemOnline={systemOnline} />)}</div>
              <SystemScanCards side="feed" />
              <div data-testid="data-route-card" className="data-route-card"><h3 data-testid="data-route-title"><GitBranch size={13} /> Chart data path</h3><div data-testid="data-route-steps" className={systemOnline ? "data-route-steps" : "data-route-steps offline"}><span>Provider</span><i /><span>Storage</span><i /><span>Chart</span></div><p data-testid="data-route-note">Source-isolated candles feed the analysis pipeline.</p></div>
              <div data-testid="system-control-panel" className="system-control-panel"><div data-testid="system-control-heading" className="control-heading"><Zap size={12} /> DISPLAY UPDATES</div><Button data-testid="system-online-toggle" className={`system-toggle ${systemOnline ? "" : "offline"}`} onClick={() => { setSystemOnline(!systemOnline); toast(systemOnline ? "Display updates paused" : "Display updates resumed", { description: "Backend collection continues independently" }); }}><span className="tiny-dot" />{systemOnline ? "Updates enabled" : "Updates paused"}<span className="switch-track"><i /></span></Button><p data-testid="system-control-note">Backend collection remains active</p></div>
            </aside>
            <LiveChart key={activePair.id} pair={activePair} online={systemOnline} telemetry={telemetry} onTimeframeChange={setTimeframe} controls={chartControls} />

            <aside id="analysis-panel" data-testid="monitoring-rail" className="monitor-panel analysis-panel">
              <div data-testid="monitoring-header" className="panel-heading"><span className="panel-heading-icon"><BrainCircuit size={18} /></span><div><h2 data-testid="monitoring-title">Intelligence engine</h2><p data-testid="monitoring-subtitle">ANALYSIS & DECISION OUTPUT</p></div><Button data-testid="hide-right-panel-button" variant="ghost" size="icon-sm" className="panel-collapse-button" aria-label="Hide intelligence engine" title="Hide panel to enlarge chart" onClick={() => hidePanel("right")}><PanelRightClose size={17} /></Button></div>
              <div data-testid="signal-dock" className="signal-dock">
                <div data-testid="signal-dock-heading" className="signal-heading"><Activity size={13} /><h3 data-testid="decision-output-title" className="decision-output-title">{activeMarketMode === 'FOREX' ? 'ACCU output' : 'Binary signal output'}</h3>{activeMarketMode === 'BINARY' ? <><Button data-testid="generate-signal-button" className="generate-signal-button" size="xs" disabled={analyzing || modeMarkets.length === 0} aria-label="Analyze current market" onClick={() => void generateSignalPreview()}><Zap size={12} /> {analyzing ? 'Wait' : 'Signal'}</Button><Button data-testid="signal-reset-button" variant="ghost" size="icon-xs" aria-label="Reset signal" onClick={resetSignalPreview}><RefreshCw size={12} /></Button></> : <span className="signal-status-badge" data-testid="accumulator-scan-status">{accumulatorBoard.isError ? 'FEED ERROR' : accumulatorBoard.isLoading ? 'SCANNING' : accumulatorSupportedCount ? 'SCANNER ACTIVE' : 'NO ACCU MARKETS'}</span>}</div>
                <div className="signal-result-region" aria-live="polite" aria-atomic="true">
                  {activeMarketMode === 'FOREX' ? <AccumulatorOutput signal={activeAccumulatorSignal} candidate={activeAccumulatorCandidate} symbol={activeDerivSymbol} marketType={marketTypeOf(activePair)} loading={accumulatorBoard.isLoading} error={accumulatorBoard.data?.error} scanned={accumulatorBoard.data?.scanned} unvalidatedCandidates={accumulatorBoard.data?.unvalidatedCandidates} supportedPairCount={accumulatorSupportedCount ?? 0} diagnostics={accumulatorBoard.data?.scanDiagnostics} /> : carouselCards.length ? <>
                    <div
                      ref={carouselRef}
                      className="signal-carousel-viewport"
                      data-testid="signal-carousel-viewport"
                      onScroll={syncCarouselIndex}
                      onPointerDown={event => { if (event.button !== 0) return; carouselDrag.current = { x: event.clientX, scrollLeft: event.currentTarget.scrollLeft, moved: false }; event.currentTarget.setPointerCapture(event.pointerId); }}
                      onPointerMove={event => { const drag = carouselDrag.current; if (!drag) return; const distance = event.clientX - drag.x; if (Math.abs(distance) > 6) drag.moved = true; if (drag.moved) { event.preventDefault(); event.currentTarget.scrollLeft = drag.scrollLeft - distance; } }}
                      onPointerUp={() => { carouselDrag.current = null; syncCarouselIndex(); }}
                      onPointerCancel={() => { carouselDrag.current = null; }}
                      onLostPointerCapture={() => { carouselDrag.current = null; }}
                    >
                      {carouselCards.map((card, index) => <div className="signal-carousel-slide" key={card.id} aria-hidden={index !== activeCarouselIndex}>
                        <div key={card.sequence} data-testid={index === activeCarouselIndex ? 'signal-output-card' : undefined} className={`signal-output-card generated-signal-card signal-${card.direction.toLowerCase()}`}>
                          <div className="signal-preview-caption"><span data-testid={index === activeCarouselIndex ? 'signal-preview-caption' : undefined}>{card.live ? (TIER_CAPTION[card.validationTier ?? ''] ?? 'SYSTEM SIGNAL') : 'ANALYSIS PREVIEW'}</span><span data-testid={index === activeCarouselIndex ? 'signal-mocked-badge' : undefined} className={`signal-status-badge status-${(card.status ?? 'PENDING').toLowerCase()}`}>{card.status && card.status !== 'PENDING' ? card.status : card.live ? 'LIVE' : 'RESEARCH'}</span></div>
                          <div className="signal-pair-heading"><CountryFlags pairId={card.pairId} markets={marketUniverse} /><div><span data-testid={index === activeCarouselIndex ? 'signal-pair-label' : undefined} className="signal-field-label">Pair · {card.marketType ?? 'Deriv'}</span><strong data-testid={index === activeCarouselIndex ? 'signal-result-pair' : undefined}>{card.pair}</strong>{card.live && card.price !== undefined && <small className="signal-live-price">Deriv {card.price.toFixed(activePair.precision)}</small>}</div></div>
                          <div data-testid={index === activeCarouselIndex ? 'signal-output-state' : undefined} className="generated-signal-direction">{card.direction === "CALL" ? <TrendingUp size={23} /> : <TrendingDown size={23} />}<strong data-testid={index === activeCarouselIndex ? 'signal-result-direction' : undefined}>{card.direction}</strong><span data-testid={index === activeCarouselIndex ? 'signal-result-direction-label' : undefined}>{card.direction === "CALL" ? "RISE · UP" : "FALL · DOWN"}</span>{card.confidence !== undefined && <b className="brain-confidence" data-testid={index === activeCarouselIndex ? 'signal-result-confidence' : undefined}>{Math.round(card.confidence)}%</b>}</div>
                          <div className="signal-detail-grid" data-testid={index === activeCarouselIndex ? 'signal-detail-grid' : undefined}>
                            <div><span className="signal-field-label">Contract</span><strong data-testid={index === activeCarouselIndex ? 'signal-result-duration' : undefined}>{card.contractType ?? 'Rise/Fall'} · {card.duration}</strong></div>
                            <div><span className="signal-field-label">Alert time</span><strong>{fmtUtc(card.generatedAt)}</strong><small>{fmtLocal(card.generatedAt)} local</small></div>
                            <div><span className="signal-field-label"><Clock3 size={11} /> Entry</span><strong data-testid={index === activeCarouselIndex ? 'signal-result-time' : undefined}>{fmtUtc(card.entryEpoch)}</strong><small>{card.entryEpoch !== undefined ? `${localEntryTime(card.entryEpoch)} local` : card.time}</small></div>
                            <div><span className="signal-field-label">Close</span><strong>{fmtUtc(card.expiryEpoch)}</strong><small>{fmtLocal(card.expiryEpoch)} local</small></div>
                          </div>
                          {card.live && card.countdownSeconds !== undefined && <p data-testid={index === activeCarouselIndex ? 'signal-result-countdown' : undefined} className="signal-countdown-strong signal-countdown-line">{card.status && card.status !== 'PENDING' ? `Settled · ${card.status}` : card.countdownSeconds > 0 ? `Entry in ${card.countdownSeconds}s` : card.expiryEpoch && card.expiryEpoch > signalNow ? `Running · closes in ${Math.ceil(card.expiryEpoch - signalNow)}s` : 'Verifying result'}</p>}
                          <p data-testid={index === activeCarouselIndex ? 'signal-track-record' : undefined} className="signal-track-record">{card.agreeing ? <>{card.agreeing.length} indicators agree · {card.opposing?.length ?? 0} oppose · </> : null}{card.trackRecord && (card.trackRecord.wins + card.trackRecord.losses) > 0 ? <>pair record <b>{card.trackRecord.wins}W / {card.trackRecord.losses}L</b> · {card.trackRecord.winRate}%</> : 'pair record building'}</p>
                          <p className="signal-pipeline-line" data-testid={index === activeCarouselIndex ? 'signal-pipeline-line' : undefined}>System flow · {telemetry?.modules?.filter(m => ['ONLINE', 'ACTIVE', 'READY', 'OK'].includes(String(m.state).toUpperCase())).length ?? 0}/{telemetry?.modules?.length ?? 0} modules · model acc {runtime.data?.training?.accuracy != null ? `${Math.round(Number(runtime.data.training.accuracy) * (Number(runtime.data.training.accuracy) <= 1 ? 100 : 1))}%` : '—'} · agents {runtime.data?.agents?.active ?? runtime.data?.agents?.registered ?? 0}/{runtime.data?.agents?.configuredSlots ?? 0} · {card.provenance?.replaceAll('_', ' ') ?? 'LIVE DERIV'}</p>
                          <p data-testid={index === activeCarouselIndex ? 'signal-output-reason' : undefined} className="signal-preview-disclaimer">{/^(BOOM|CRASH|cry)/.test(card.symbol ?? '') ? 'Analysis only · Deriv offers Rise/Fall on this market with 1-day+ duration only' : 'Analytical signal only · no trade is placed.'}</p>
                        </div>
                      </div>)}
                    </div>
                    {carouselCards.length > 1 && <div className="signal-carousel-dots" data-testid="signal-carousel-dots" role="tablist" aria-label="Active pair signals">
                      {carouselCards.map((card, index) => <button key={card.id} type="button" role="tab" aria-label={`Show ${card.pair} signal`} aria-selected={index === activeCarouselIndex} className={index === activeCarouselIndex ? 'active' : ''} data-testid={`signal-carousel-dot-${index}`} onClick={() => selectCarouselCard(index)} />)}
                    </div>}
                  </> : <div data-testid="signal-output-card" className="signal-output-card signal-no_signal">
                    <div data-testid="signal-output-state" className="signal-state"><span className="tiny-dot" />NO TRADE<span data-testid="signal-output-time">{telemetry?.evidence.gate ?? 'GATED'}</span></div>
                    <p data-testid="signal-output-reason">{activeMarketMode === 'BINARY' && forexCounts.open === 0 && forexCounts.closed > 0 ? `All ${forexCounts.closed} provider-listed real Forex pairs are CLOSED. Fixed-time signals need open, fresh Deriv Forex data; scanning will resume after the market reopens. No fabricated signal is shown.` : signalReason}</p>
                    <p data-testid="signal-preview-hint" className="signal-preview-hint">System pipeline · last signal {signalBoard.data?.engine?.lastSignalAt ? `${Math.max(0, Math.round(signalNow - signalBoard.data.engine.lastSignalAt))}s ago` : 'none yet'} · {signalBoard.data?.engine?.marketEvaluation?.targets ?? '—'} markets scanned{telemetry?.evidence.uncertainty ? ` · uncertainty ${telemetry.evidence.uncertainty}` : ''}</p>
                  </div>}
                </div>
                {activeMarketMode === 'BINARY' ? <>
                  <div data-testid="signal-gates" className="signal-gates"><span>Evidence <b>{telemetry?.evidence.agreeing?.length ?? 0} / {telemetry?.evidence.agreeing?.length && telemetry?.evidence.opposing?.length !== undefined ? telemetry.evidence.agreeing.length + telemetry.evidence.opposing.length : 3}</b></span><span>Model <b>{telemetry?.agents.find(agent => agent.id === 'A7')?.state ?? 'WAITING'}</b></span></div>
                  <div data-testid="signal-action-row" className="signal-action-row"><Button data-testid="trade-call-button" className="call-button" disabled={analyzing || modeMarkets.length === 0} onClick={() => void generateSignalPreview("CALL")}><span>CALL <small>Check signal</small></span><TrendingUp size={18} /></Button><Button data-testid="trade-put-button" className="put-button" disabled={analyzing || modeMarkets.length === 0} onClick={() => void generateSignalPreview("PUT")}><span>PUT <small>Check signal</small></span><TrendingDown size={18} /></Button></div>
                </> : <p data-testid="signal-action-row" className="signal-preview-hint">Accumulator setups are scanned from verified Deriv candles. Binary CALL/PUT controls are available in Deriv Binary mode.</p>}
                </div>
                <FutureSignals online={systemOnline} onSelectPair={setActivePairId} marketMode={activeMarketMode} derivMarkets={railUniverse} modeMarkets={modeMarkets} closedForexCount={forexCounts.closed} supportedForexCount={binaryPairCount} activePairId={activePair.id} activeSymbol={activeDerivSymbol} />
                <div data-testid="monitoring-cards-list" className="monitor-cards-list">{monitoringCards.slice(4).map(module => <MonitorCard key={module.id} module={module} systemOnline={systemOnline} />)}</div>
                <SystemScanCards side="engine" />
              <div data-testid="model-readiness-note" className="model-readiness-note"><ShieldCheck size={13} /><span>ML forecaster · {telemetry?.agents.find(agent => agent.id === 'A7')?.state ?? 'WAITING'}<br />Signal execution remains blocked.</span></div>
            </aside>
            <section data-testid="event-log-panel" className="event-log-panel"><div data-testid="event-log-resize-handle" className="log-resize-handle" role="separator" aria-orientation="horizontal" aria-label="Drag to resize chart and event stream" title="Drag up / down to resize" onPointerDown={startLogResize} onDoubleClick={() => { setLogHeight(150); localStorage.setItem('mc.eventLogHeight', '150'); }}><i /></div><div data-testid="event-log-header" className="event-log-header"><h2 data-testid="event-log-title"><Terminal size={13} /> Event stream <span data-testid="event-log-live-badge">BACKEND</span></h2><div data-testid="log-filter-controls" className="log-filter-controls">{["ALL", "INFO", "WARN", "SIGNAL"].map(filter => <button key={filter} data-testid={`log-filter-${filter.toLowerCase()}-button`} aria-pressed={logFilter === filter} className={logFilter === filter ? "selected" : ""} onClick={() => setLogFilter(filter)}>{filter}</button>)}</div></div><div data-testid="terminal-log-container" className="terminal-log-container">{filteredLogs.map((row, index) => <div key={`${row.time}-${index}`} data-testid={`log-row-${row.level.toLowerCase()}-${index}`} className="log-row"><span data-testid={`log-time-${index}`} className="log-time">{row.time}</span><span data-testid={`log-level-${index}`} className={`log-level level-${row.level.toLowerCase()}`}>{row.level}</span><span data-testid={`log-source-${index}`} className="log-source">{row.source}</span><span data-testid={`log-message-${index}`} className="log-message" title={row.message}>{row.message}</span></div>)}</div></section>
          </div>
          <footer data-testid="terminal-footer" className="terminal-footer"><span data-testid="prototype-disclaimer"><span className="tiny-dot" /> LIVE DATA · ANALYTICAL ONLY · NO REAL TRADES</span><span data-testid="terminal-footer-status">Feed {telemetry?.feed.verification ?? telemetryStream.status} · Vector search {telemetry?.validation.vectorSearch === 'DISABLED' ? 'off (PostgreSQL disabled)' : telemetry?.validation.vectorSearch ?? 'unavailable'} <span>Master Candle V17.3</span></span></footer>
          <AgentRegistry open={agentsOpen} onOpenChange={setAgentsOpen} online={systemOnline} live />
        </main>
      </div>
      <RemoteBrowserDialog
        open={remoteBrowserOpen}
        onOpenChange={open => {
          setRemoteBrowserOpen(open);
          if (!open) setRemoteOperatorKey("");
        }}
        observerDetail={providerControl.data?.providers.playwright_observer.detail}
        operatorKey={remoteOperatorKey}
      />
    </div>
  );
}

function NavButton({ icon: Icon, label, ariaLabel, active = false, title, disabled = false, pressed, testId, onClick }: { icon: LucideIcon; label: string; ariaLabel?: string; active?: boolean; title?: string; disabled?: boolean; pressed?: boolean; testId: string; onClick: () => void }) {
  return <button data-testid={testId} title={title} aria-label={ariaLabel} aria-pressed={pressed} disabled={disabled} className={`nav-button ${active ? "active" : ""}`} onClick={onClick}><Icon size={19} /><span>{label}</span></button>;
}

function SummaryMetric({ value, label, tone }: { value: string; label: string; tone: "blue" | "green" | "amber" }) {
  return <div data-testid={`summary-metric-${label}`} className={`summary-metric tone-${tone}`}><strong data-testid={`summary-value-${label}`}>{value}</strong><span data-testid={`summary-label-${label}`}>{label.replaceAll("-", " ")}</span></div>;
}

function MonitorCard({ module, systemOnline }: { module: MonitorModule; systemOnline: boolean }) {
  const Icon = module.icon;
  const effectiveStatus = systemOnline ? module.status : "IDLE";
  return <article data-testid={`monitor-card-${module.id}`} className={`monitor-card status-${effectiveStatus.toLowerCase()}`}>
    <div className="monitor-card-top"><span data-testid={`monitor-card-icon-${module.id}`} className="monitor-icon"><Icon size={14} /></span><h3 data-testid={`monitor-card-name-${module.id}`}>{module.name}</h3><span className="monitor-activity" aria-hidden="true"><i /><i /><i /><i /><i /></span></div>
    <div data-testid={`module-status-badge-${module.id}`} className="module-status"><span data-testid={`status-dot-${module.id}`} className="tiny-dot" />{systemOnline ? module.state : "DISPLAY PAUSED"}</div>
    <p className="module-description" data-testid={`monitor-card-description-${module.id}`}>{systemOnline ? module.description : 'Last telemetry snapshot · live display updates paused'}</p>
    <div data-testid={`monitor-card-metrics-${module.id}`} className="monitor-metrics">{module.metrics.map((metric, index) => <span key={`${module.id}-metric-${index}-${metric}`} data-testid={`monitor-metric-${module.id}-${index}`}>{metric}</span>)}</div>
  </article>;
}