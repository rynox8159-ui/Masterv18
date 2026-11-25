import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Activity, ArrowLeft, Bot, CircleAlert, CircleCheck, LockKeyhole, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Play, ShieldCheck, Square, TrendingDown, TrendingUp } from "lucide-react";
import Brand from "@/components/terminal/Brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAccumulatorSignals, type AccumulatorSignal } from "@/hooks/useLiveSignals";
import { apiGet, apiPost } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { marketNow } from "@/lib/marketClock";
import "./autotrade.css";

interface EngineSettings {
  baseStake: number;
  maxStake: number;
  dailyLossLimit: number;
  maxDailyTrades: number;
  maxConsecutiveLosses: number;
  signalSource: string;
  accumulatorEnabled: boolean;
  accumulatorShadowValidationEnabled: boolean;
  accumulatorGrowthRateMode: "auto" | "manual";
  accumulatorGrowthRate: number;
  accumulatorTakeProfitPercent: number;
  accumulatorMaxHoldTicks: number;
  accumulatorMaxHoldMinutes: number;
  martingaleEnabled: boolean;
}

interface AutoTradeState {
  wins: number;
  losses: number;
  ties: number;
  dailyPnl: number;
}

interface AutoTradeStatus {
  running: boolean;
  mode: string;
  phase: string;
  stopReason: string | null;
  settings: EngineSettings;
  state: AutoTradeState;
  account: { balance: number | null; currency: string };
  credentials: { appId: boolean; demoToken: boolean; realToken: boolean };
  webLiveEnabled: boolean;
  feedConnected: boolean;
  currentTrade: AutoTradeRecord | null;
  openTrades: AutoTradeRecord[];
}

interface AutoTradeRecord {
  id: string;
  symbol: string;
  label?: string;
  executionType?: string;
  status: string;
  result: string | null;
  stake: number;
  profit?: number | null;
  growthRatePercent?: number | null;
  openedAt?: string;
  closedAt?: string;
  closeAtEpoch?: number;
}

const money = (value: number | null | undefined, currency = "USD") =>
  value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency }).format(value);

const passesAccumulatorValidation = (signal: AccumulatorSignal, now: number) =>
  signal.status === "PENDING"
  && signal.validationTier === "LIVE_VALIDATED"
  && signal.strategyTier === "LIVE_VALIDATED"
  && signal.validationReport?.status === "VALIDATED"
  && signal.validationReport.readyForLive
  && signal.validationReport.validationMethod === "DEMO_SHADOW"
  && signal.validationReport.source === "deriv"
  && signal.validationReport.symbol === signal.symbol
  && signal.validationReport.timeframe === "1m"
  && signal.expiresAt >= now;

export default function AutoTrade() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tradeMode, setTradeMode] = useState<"demo" | "real">("demo");
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [stake, setStake] = useState("1");
  const [growthMode, setGrowthMode] = useState<"auto" | "manual">("auto");
  const [appIdInput, setAppIdInput] = useState("");
  const [realTokenInput, setRealTokenInput] = useState("");
  const [realControlKey, setRealControlKey] = useState("");
  const [realConfirmation, setRealConfirmation] = useState("");
  const [notice, setNotice] = useState("");
  const [now, setNow] = useState(() => marketNow() / 1000);
  const syncedRunningMode = useRef(false);
  const engineQuery = useQuery({
    queryKey: ["autotrade-status"],
    queryFn: () => apiGet<AutoTradeStatus>("/v1/autotrade/status"),
    refetchInterval: 4000,
  });
  const tradesQuery = useQuery({
    queryKey: ["autotrade-trades", tradeMode],
    queryFn: () => apiGet<{ items: AutoTradeRecord[] }>(`/v1/autotrade/trades?mode=${tradeMode}&limit=500`),
    refetchInterval: 5000,
  });
  const accumulatorQuery = useAccumulatorSignals(true, 200);
  const engine = engineQuery.data;
  const settings = engine?.settings;
  const maxStake = settings?.maxStake ?? 100;
  const supportedPairs = useMemo(
    () => [...(accumulatorQuery.data?.supportedSymbols ?? [])].sort((a, b) => a.label.localeCompare(b.label)),
    [accumulatorQuery.data?.supportedSymbols],
  );
  const passedSignals = useMemo(
    () => (accumulatorQuery.data?.items ?? []).filter(signal => passesAccumulatorValidation(signal, now)),
    [accumulatorQuery.data?.items, now],
  );
  const shadowCandidates = accumulatorQuery.data?.paperCandidates ?? [];
  const shadowCandidateSymbols = new Set(shadowCandidates.map(signal => signal.symbol));
  const selectedShadowCandidate = shadowCandidates
    .filter(signal => signal.symbol === selectedSymbol)
    .sort((a, b) => b.generatedAt - a.generatedAt)[0];
  const passedBySymbol = useMemo(
    () => new Map(passedSignals.map(signal => [signal.symbol, signal])),
    [passedSignals],
  );
  const selectedPair = supportedPairs.find(pair => pair.symbol === selectedSymbol);
  const selectedSignal = passedBySymbol.get(selectedSymbol);
  const modeTrades = (tradesQuery.data?.items ?? []).filter(trade => trade.executionType === "ACCUMULATOR");
  const settledTrades = modeTrades.filter(trade => trade.result === "WIN" || trade.result === "LOSS");
  const historyTrades = modeTrades.filter(trade => trade.result === "WIN" || trade.result === "LOSS" || trade.result === "TIE").slice(0, 12);
  const wins = settledTrades.filter(trade => trade.result === "WIN").length;
  const losses = settledTrades.filter(trade => trade.result === "LOSS").length;
  const winRate = settledTrades.length ? (wins / settledTrades.length) * 100 : null;
  const openTrades = modeTrades.filter(trade => trade.status === "OPEN" || trade.status === "PENDING_ORDER");
  const credentialsReady = !!engine?.credentials.appId && !!engine.credentials.demoToken;
  const realCredentialsReady = !!engine?.credentials.appId && !!engine.credentials.realToken;
  const riskLimitsReady = !!settings
    && settings.dailyLossLimit > 0
    && settings.maxStake > 0
    && settings.maxConsecutiveLosses > 0
    && settings.maxDailyTrades > 0;
  const canStart = tradeMode === "demo"
    ? credentialsReady
    : !!engine?.webLiveEnabled && realCredentialsReady && riskLimitsReady && !!realControlKey.trim() && realConfirmation.trim() === "REAL";
  const lockModeSelection = !!engine?.running && engine.mode !== "demo";
  const canUpdateRealToken = !engine?.running || engine.mode === "demo";

  useEffect(() => {
    const timer = window.setInterval(() => setNow(marketNow() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!engine?.running) {
      syncedRunningMode.current = false;
      return;
    }
    if (!syncedRunningMode.current && (engine.mode === "demo" || engine.mode === "real")) {
      setTradeMode(engine.mode);
      syncedRunningMode.current = true;
    }
  }, [engine?.mode, engine?.running]);

  useEffect(() => {
    if (!selectedSymbol || !supportedPairs.some(pair => pair.symbol === selectedSymbol)) {
      setSelectedSymbol(passedSignals[0]?.symbol ?? shadowCandidates[0]?.symbol ?? supportedPairs[0]?.symbol ?? "");
    }
  }, [passedSignals, selectedSymbol, shadowCandidates, supportedPairs]);

  useEffect(() => {
    if (settings) {
      setStake(String(settings.baseStake));
      setGrowthMode(settings.accumulatorGrowthRateMode);
    }
  }, [settings?.baseStake, settings?.accumulatorGrowthRateMode]);

  const settingsMutation = useMutation({
    mutationFn: (patch: Partial<EngineSettings>) => apiPost<EngineSettings>("/v1/autotrade/settings", patch),
    onSuccess: async () => {
      setNotice(`${tradeMode === "real" ? "Real" : "Demo"} ACCU settings saved.`);
      await queryClient.invalidateQueries({ queryKey: ["autotrade-status"] });
    },
  });

  const credentialsMutation = useMutation({
    mutationFn: (credentials: { appId?: string; realToken: string }) =>
      apiPost<{ appId: boolean; realToken: boolean }>("/v1/autotrade/credentials", credentials),
    onSuccess: async () => {
      setAppIdInput("");
      setRealTokenInput("");
      setNotice("Real credentials saved in backend memory only; browser fields cleared.");
      await queryClient.invalidateQueries({ queryKey: ["autotrade-status"] });
    },
  });

  const controlMutation = useMutation({
    mutationFn: async ({ start, mode }: { start: boolean; mode: "demo" | "real" }) => {
      if (!start) return apiPost<AutoTradeStatus>("/v1/autotrade/stop", {});
      const amount = Number(stake);
      if (!Number.isFinite(amount) || amount < 0.35 || amount > maxStake) {
        throw new Error(`Stake must be between $0.35 and the configured max stake (${money(maxStake)}).`);
      }
      await apiPost("/v1/autotrade/settings", {
        signalSource: "accumulator",
        accumulatorEnabled: true,
        accumulatorShadowValidationEnabled: mode === "demo" && settings?.accumulatorShadowValidationEnabled === true,
        accumulatorGrowthRateMode: growthMode,
        accumulatorGrowthRate: 4,
        accumulatorTakeProfitPercent: 5,
        accumulatorMaxHoldTicks: 5,
        accumulatorMaxHoldMinutes: 5,
        martingaleEnabled: false,
        baseStake: amount,
      });
      return mode === "real"
        ? apiPost<AutoTradeStatus>(
          "/v1/autotrade/start",
          { mode, confirmReal: true, confirmText: "REAL" },
          { "X-Autotrade-Real-Key": realControlKey.trim() },
        )
        : apiPost<AutoTradeStatus>("/v1/autotrade/start", { mode });
    },
    onSuccess: async (_data, { start, mode }) => {
      const shadowValidationEnabled = mode === "demo" && settings?.accumulatorShadowValidationEnabled === true;
      setNotice(start
        ? shadowValidationEnabled
          ? "Demo Auto Trade started. Shadow validation may place demo-only contracts for unvalidated candidates; real orders remain disabled."
          : `${mode === "real" ? "REAL" : "Demo"} Auto Trade started. Only validated ACCU signals can trigger orders.`
        : "Auto Trade stop requested.");
      if (start && mode === "real") setRealConfirmation("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["autotrade-status"] }),
        queryClient.invalidateQueries({ queryKey: ["autotrade-trades"] }),
      ]);
    },
    onSettled: (_data, _error, { start, mode }) => {
      if (start && mode === "real") setRealControlKey("");
    },
  });
  const busy = engineQuery.isLoading || controlMutation.isPending || settingsMutation.isPending || credentialsMutation.isPending;

  const saveSettings = () => {
    setNotice("");
    if (engine?.running) {
      setNotice("Stop Auto Trade before changing settings.");
      return;
    }
    const amount = Number(stake);
    if (!Number.isFinite(amount) || amount < 0.35 || amount > maxStake) {
      setNotice(`Enter an amount from $0.35 to ${money(maxStake)}.`);
      return;
    }
    settingsMutation.mutate({
      signalSource: "accumulator",
      accumulatorEnabled: true,
      accumulatorShadowValidationEnabled: settings?.accumulatorShadowValidationEnabled ?? false,
      accumulatorGrowthRateMode: growthMode,
      accumulatorGrowthRate: 4,
      accumulatorTakeProfitPercent: 5,
      accumulatorMaxHoldTicks: 5,
      accumulatorMaxHoldMinutes: 5,
      martingaleEnabled: false,
      baseStake: amount,
    });
  };

  const saveRealCredentials = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setNotice("");
    if (engine?.running && (engine.mode !== "demo" || appIdInput.trim())) {
      setNotice("While Demo Auto Trade is running, only the inactive real token can be changed. Stop it to change the App ID or an active token.");
      return;
    }
    if (!realTokenInput.trim()) {
      setNotice("Enter the Deriv real-account API token.");
      return;
    }
    credentialsMutation.mutate({
      ...(appIdInput.trim() ? { appId: appIdInput.trim() } : {}),
      realToken: realTokenInput.trim(),
    });
  };

  const requestToggle = () => {
    setNotice("");
    if (engine?.running) {
      controlMutation.mutate({ start: false, mode: engine.mode === "real" ? "real" : "demo" });
      return;
    }
    if (tradeMode === "real" && !engine?.webLiveEnabled) {
      setNotice("Real trading is disabled by the backend administrator. Enable the server live-trading gate first.");
      return;
    }
    if (tradeMode === "real" && !realCredentialsReady) {
      setNotice("Save a Deriv real-account token before starting.");
      return;
    }
    if (tradeMode === "real" && !riskLimitsReady) {
      setNotice("Required real-trading safety limits are not configured.");
      return;
    }
    if (tradeMode === "real" && !realControlKey.trim()) {
      setNotice("Enter the separate server-side real-trading control key.");
      return;
    }
    if (tradeMode === "real" && realConfirmation.trim() !== "REAL") {
      setNotice('Type "REAL" to confirm real-money trading.');
      return;
    }
    if (tradeMode === "demo" && !credentialsReady) {
      setNotice("Demo account is not configured. Add a Deriv demo token to the backend configuration.");
      return;
    }
    if (!engine?.feedConnected) {
      setNotice("Deriv feed is not connected; Auto Trade cannot start.");
      return;
    }
    controlMutation.mutate({ start: true, mode: tradeMode });
  };

  const error = engineQuery.error ?? tradesQuery.error ?? accumulatorQuery.error ?? settingsMutation.error ?? controlMutation.error ?? credentialsMutation.error;
  const livePositions = [
    ...openTrades,
    ...(engine?.mode === tradeMode && engine.currentTrade?.executionType === "ACCUMULATOR" && !openTrades.some(trade => trade.id === engine.currentTrade?.id)
      ? [engine.currentTrade]
      : []),
  ].slice(0, 8);

  return <div className="terminal-shell auto-trade-screen" data-testid="auto-trade-screen">
    <header className="terminal-header auto-trade-header">
      <Brand />
      <div className="auto-trade-header-title"><Bot size={17} /><span>ACCUMULATOR AUTO TRADE</span><b>{tradeMode === "real" ? "REAL MODE" : "DEMO MODE"}</b></div>
      <span className="flex-1" />
      <Button data-testid="auto-trade-back-button" variant="ghost" onClick={() => navigate("/")}>
        <ArrowLeft size={15} /> Terminal
      </Button>
    </header>

    <main className={`auto-trade-layout ${leftCollapsed ? "left-collapsed" : ""} ${rightCollapsed ? "right-collapsed" : ""}`}>
      <aside className={`auto-trade-sidebar auto-trade-left ${leftCollapsed ? "collapsed" : ""}`} aria-label="Account and pair selection">
        <div className="auto-trade-sidebar-togglebar">
          {!leftCollapsed && <span>ACCOUNT &amp; PAIRS</span>}
          <Button type="button" variant="ghost" data-testid="autotrade-left-sidebar-toggle" aria-label={leftCollapsed ? "Expand left sidebar" : "Collapse left sidebar"} aria-expanded={!leftCollapsed} onClick={() => setLeftCollapsed(value => !value)}>
            {leftCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </Button>
        </div>
        {!leftCollapsed && <div className="auto-trade-sidebar-content">
        <section className="auto-trade-panel">
          <div className="auto-trade-panel-heading"><span>ACCOUNT MODE</span><ShieldCheck size={15} /></div>
          <div className="auto-trade-mode-buttons">
            <button type="button" className={tradeMode === "demo" ? "selected" : ""} data-testid="autotrade-demo-mode" aria-pressed={tradeMode === "demo"} disabled={lockModeSelection} onClick={() => { setTradeMode("demo"); setRealConfirmation(""); setNotice(""); }}>
              <CircleCheck size={15} /><span>Demo Trade</span><small>Virtual funds</small>
            </button>
            <button type="button" className={tradeMode === "real" ? "selected real" : "real"} data-testid="autotrade-real-mode" aria-pressed={tradeMode === "real"} disabled={lockModeSelection} onClick={() => { setTradeMode("real"); setNotice(engine?.running ? "Demo Auto Trade is still running. Save the inactive real token now; stop Demo before starting Real Auto Trade." : ""); }}>
              <LockKeyhole size={15} /><span>Real Trade</span><small>{engine?.webLiveEnabled ? "Live orders" : "Admin locked"}</small>
            </button>
          </div>
          <p className="auto-trade-muted">{tradeMode === "real"
            ? realCredentialsReady ? "Real account token configured" : "Real account token required"
            : credentialsReady ? "Demo account connected" : "Demo token not configured on backend"}</p>
          {tradeMode === "real" && <form className="auto-trade-credential-form" onSubmit={saveRealCredentials} autoComplete="off">
            <label className="auto-trade-field">Deriv App ID · optional if configured
              <Input data-testid="autotrade-app-id" type="text" inputMode="numeric" autoComplete="off" maxLength={20} value={appIdInput} disabled={busy || !!engine?.running} onChange={event => setAppIdInput(event.target.value)} />
            </label>
            <label className="auto-trade-field">Real-account API token
              <Input data-testid="autotrade-real-token" type="password" autoComplete="new-password" spellCheck={false} maxLength={256} value={realTokenInput} disabled={busy || !canUpdateRealToken} onChange={event => setRealTokenInput(event.target.value)} />
            </label>
            <Button data-testid="autotrade-save-real-credentials" type="submit" variant="ghost" disabled={busy || !canUpdateRealToken || !!appIdInput.trim() && !!engine?.running || !realTokenInput.trim()}>
              {credentialsMutation.isPending ? "Saving…" : "Save real credentials"}
            </Button>
            {engine?.running && engine.mode === "demo" && <p className="auto-trade-muted">Demo is active. Real token can be saved now, but App ID changes and starting Real mode require Demo to stop first.</p>}
            <p className="auto-trade-safety">Token is sent to the backend and kept in process memory only; it is not saved in browser storage or on disk.</p>
          </form>}
          {tradeMode === "real" && !engine?.webLiveEnabled && <p className="auto-trade-safety">Live trading is disabled by the backend administrator. The token can be saved, but real orders cannot start.</p>}
        </section>

        <section className="auto-trade-panel auto-trade-performance">
          <div className="auto-trade-panel-heading"><span>{tradeMode.toUpperCase()} AUTO TRADE ACCURACY</span><Activity size={15} /></div>
          <div className="auto-trade-kpi"><small>Last {modeTrades.length} {tradeMode} ACCU trades</small><strong>{winRate === null ? "—" : `${winRate.toFixed(1)}%`}</strong></div>
          <div className="auto-trade-win-loss">
            <span><TrendingUp size={14} /> Wins <b>{wins}</b></span>
            <span><TrendingDown size={14} /> Losses <b>{losses}</b></span>
          </div>
          <p className="auto-trade-muted">Calculated from settled {tradeMode} Accumulator trades only.</p>
        </section>

        <section className="auto-trade-panel auto-trade-pairs">
          <div className="auto-trade-panel-heading"><span>ACCU PAIRS</span><b>{supportedPairs.length}</b></div>
          <p className="auto-trade-muted">Only live-validated signals can pass to {tradeMode} trading.</p>
          <div className="auto-trade-pair-list" role="list" aria-label="Accumulator pairs">
            {supportedPairs.map(pair => {
              const signal = passedBySymbol.get(pair.symbol);
              const hasShadowCandidate = shadowCandidateSymbols.has(pair.symbol);
              return <button
                type="button"
                key={pair.symbol}
                role="listitem"
                data-testid={`autotrade-pair-${pair.symbol}`}
                aria-pressed={pair.symbol === selectedSymbol}
                className={`auto-trade-pair-tab ${pair.symbol === selectedSymbol ? "active" : ""}`}
                onClick={() => setSelectedSymbol(pair.symbol)}
              >
                <span><b>{pair.label}</b><small>{pair.symbol}</small></span>
                <em className={signal ? "passed" : hasShadowCandidate ? "shadow" : ""}>{signal ? "PASSED" : hasShadowCandidate ? "SHADOW" : "WAIT"}</em>
              </button>;
            })}
            {!supportedPairs.length && <p className="auto-trade-empty">Loading provider-supported ACCU pairs…</p>}
          </div>
        </section>
        </div>}
      </aside>

      <section className="auto-trade-center" aria-label="Validated Accumulator signals">
        <div className="auto-trade-hero auto-trade-panel">
          <div>
            <span className="auto-trade-eyebrow">DERIV · ACCUMULATOR · {tradeMode.toUpperCase()} EXECUTION</span>
            <h1>Auto Trade</h1>
            <p>Pairs are eligible only after the Accumulator validation gate passes.</p>
          </div>
          <div className={`auto-trade-engine-state ${engine?.running && engine.mode === tradeMode ? "running" : ""}`}>
            <i />{engine?.running ? `${engine.mode.toUpperCase()} · ${engine.phase}` : "OFF"}
          </div>
          <Button
            data-testid="autotrade-toggle"
            className={`auto-trade-toggle ${engine?.running ? "stop" : tradeMode === "real" ? "real-start" : "start"}`}
            disabled={busy || (!engine?.running && (!canStart || !engine?.feedConnected))}
            aria-pressed={!!engine?.running && engine.mode === tradeMode}
            onClick={requestToggle}
          >
            {engine?.running ? <Square size={15} /> : tradeMode === "real" ? <LockKeyhole size={15} /> : <Play size={15} />}
            {engine?.running ? `Stop ${engine.mode.toUpperCase()} Auto Trade` : `Start ${tradeMode.toUpperCase()} Auto Trade`}
          </Button>
          <p className="auto-trade-feed">
            Feed {engine?.feedConnected ? "connected" : "not connected"} · {tradeMode === "real" ? "Real" : "Demo"} balance {money(engine?.mode === tradeMode ? engine.account.balance : null, engine?.account.currency)}
          </p>
        </div>

        {notice && <p className="auto-trade-notice" role="status">{notice}</p>}
        {error && <p className="auto-trade-error" role="alert"><CircleAlert size={15} /> {errorText(error)}</p>}

        <div className="auto-trade-panel auto-trade-selected-signal">
          <div className="auto-trade-panel-heading"><span>SELECTED PAIR DETAILS</span><span>{selectedPair?.market ?? "DERIV ACCU"}</span></div>
          {selectedSignal ? <>
            <div className="auto-trade-selected-title"><div><h2>{selectedSignal.label}</h2><small>{selectedSignal.symbol} · 1 minute source candles</small></div><b className="passed">VALIDATED</b></div>
            <div className="auto-trade-signal-details">
              <div><small>Signal status</small><strong>Passed validation</strong></div>
              <div><small>Growth rate</small><strong>{selectedSignal.growthRatePercent}%</strong></div>
              <div><small>Take profit</small><strong>{selectedSignal.takeProfitPercent}% of stake</strong></div>
              <div><small>Stability heuristic</small><strong>{selectedSignal.stabilityScore}/100</strong></div>
            </div>
            <p className="auto-trade-muted">{selectedSignal.reason} · heuristic score is not a win probability or guarantee.</p>
          </> : <div className="auto-trade-empty auto-trade-no-signal">
            <Activity size={19} />
            <div><b>{selectedPair?.label ?? "Accumulator pairs"}</b><p>No validated signal for this pair right now. Unvalidated candidates are excluded from Auto Trade.</p></div>
          </div>}
        </div>

        <div className="auto-trade-panel">
          <div className="auto-trade-panel-heading"><span>VALIDATED SIGNALS</span><b>{passedSignals.length} passed</b></div>
          <div className="auto-trade-signal-list">
            {passedSignals.map(signal => <button
              type="button"
              key={signal.id}
              data-testid={`autotrade-signal-${signal.symbol}`}
              className={`auto-trade-signal-row ${signal.symbol === selectedSymbol ? "active" : ""}`}
              onClick={() => setSelectedSymbol(signal.symbol)}
            >
              <span><b>{signal.label}</b><small>{signal.symbol} · ACCU · 1m</small></span>
              <span className="auto-trade-signal-metrics">{signal.growthRatePercent}% growth · TP {signal.takeProfitPercent}%</span>
              <em className="passed">PASSED</em>
            </button>)}
            {!passedSignals.length && <p className="auto-trade-empty" data-testid="autotrade-no-signals">
              {accumulatorQuery.isError ? "Accumulator feed unavailable. Check the backend connection." : "No Accumulator signal has passed validation yet. Scanner is waiting; no paper candidates are shown."}
            </p>}
          </div>
        </div>
      </section>

      <aside className={`auto-trade-sidebar auto-trade-right ${rightCollapsed ? "collapsed" : ""}`} aria-label={`${tradeMode} trading settings and positions`}>
        <div className="auto-trade-sidebar-togglebar">
          {!rightCollapsed && <span>TRADING CONTROLS</span>}
          <Button type="button" variant="ghost" data-testid="autotrade-right-sidebar-toggle" aria-label={rightCollapsed ? "Expand right sidebar" : "Collapse right sidebar"} aria-expanded={!rightCollapsed} onClick={() => setRightCollapsed(value => !value)}>
            {rightCollapsed ? <PanelRightOpen size={16} /> : <PanelRightClose size={16} />}
          </Button>
        </div>
        {!rightCollapsed && <div className="auto-trade-sidebar-content">
        <section className="auto-trade-panel">
          <div className="auto-trade-panel-heading"><span>{tradeMode.toUpperCase()} TRADE SETTINGS</span><ShieldCheck size={15} /></div>
          <label className="auto-trade-field">Trade type
            <select value="accumulator" disabled aria-label="Trade type"><option value="accumulator">Accumulator (ACCU)</option></select>
          </label>
          <label className="auto-trade-field">Amount · USD
            <Input data-testid="autotrade-stake" type="number" min="0.35" max={maxStake} step="0.01" value={stake} disabled={!!engine?.running || controlMutation.isPending || settingsMutation.isPending} onChange={event => setStake(event.target.value)} />
            <small>Flat stake · Martingale disabled · max stake {money(maxStake)}</small>
          </label>
          <label className="auto-trade-field">Growth rate
            <select data-testid="autotrade-growth-mode" value={growthMode} disabled={!!engine?.running || controlMutation.isPending || settingsMutation.isPending} onChange={event => setGrowthMode(event.target.value as "auto" | "manual")}>
              <option value="auto">Auto · demo-validated rate</option>
              <option value="manual">Manual · 4% validated policy</option>
            </select>
          </label>
          <label className="auto-trade-shadow-validation" data-testid="autotrade-shadow-validation">
            <input
              data-testid="autotrade-shadow-validation-toggle"
              type="checkbox"
              checked={settings?.accumulatorShadowValidationEnabled ?? false}
              disabled={tradeMode !== "demo" || !!engine?.running || busy || !settings}
              onChange={event => settingsMutation.mutate({ accumulatorShadowValidationEnabled: event.currentTarget.checked })}
            />
            <span><strong>Collect Demo-only validation samples</strong><small>Runs unvalidated candidates on the Deriv Demo account only. Start Demo Auto Trade to collect settled outcomes; this never enables real orders.</small></span>
          </label>
          <p className="auto-trade-shadow-progress" data-testid="autotrade-shadow-progress">
            {selectedShadowCandidate
              ? `Selected pair: ${selectedShadowCandidate.validationReport?.samples ?? 0}/${selectedShadowCandidate.validationReport?.minSamples ?? 300} settled outcomes · Demo shadow candidate`
              : `No recent shadow candidate for ${selectedSymbol || "this pair"}. The live scanner is still running.`}
          </p>
          <div className="auto-trade-locked-fields">
            <label className="auto-trade-field">Take profit<input value="5% · validated policy" readOnly aria-label="Take profit" /></label>
            <label className="auto-trade-field">Stop loss<input value="5% · validated policy" readOnly aria-label="Stop loss" /></label>
            <label className="auto-trade-field">Accumulator range<input value="5 minutes · validated policy" readOnly aria-label="Accumulator range" /></label>
          </div>
          <Button data-testid="autotrade-save-settings" variant="ghost" disabled={!!engine?.running || settingsMutation.isPending || controlMutation.isPending} onClick={saveSettings}>
            {settingsMutation.isPending ? "Saving…" : `Save ${tradeMode} settings`}
          </Button>
          <p className="auto-trade-muted">Risk values are locked to the demo-validated strategy. Changing them requires a matching validation run.</p>
          {tradeMode === "real" && <>
            <div className="auto-trade-risk-limits">
              <span>Max stake <b>{money(settings?.maxStake)}</b></span>
              <span>Daily loss stop <b>{money(settings?.dailyLossLimit)}</b></span>
              <span>Daily trade cap <b>{settings?.maxDailyTrades ?? "—"}</b></span>
              <span>Loss-streak stop <b>{settings?.maxConsecutiveLosses ?? "—"}</b></span>
            </div>
            <label className="auto-trade-field">Server real-trading control key
              <Input data-testid="autotrade-real-control-key" type="password" autoComplete="new-password" spellCheck={false} value={realControlKey} disabled={!engine?.webLiveEnabled || !!engine?.running || busy} onChange={event => setRealControlKey(event.target.value)} />
              <small>Separate from the Deriv API token; used only to authorize the start request.</small>
            </label>
            <label className="auto-trade-field">Type REAL to confirm live orders
              <Input data-testid="autotrade-real-confirmation" type="text" autoComplete="off" value={realConfirmation} disabled={!engine?.webLiveEnabled || !!engine?.running || busy} onChange={event => setRealConfirmation(event.target.value)} />
            </label>
            {!riskLimitsReady && <p className="auto-trade-safety">Required risk limits are missing; real trading cannot start.</p>}
            <p className="auto-trade-safety">Real orders use real funds. Only validated signals pass the trading gate; starting does not guarantee a signal or profit.</p>
          </>}
        </section>

        <section className="auto-trade-panel auto-trade-positions">
          <div className="auto-trade-panel-heading"><span>LIVE POSITIONS · {tradeMode.toUpperCase()}</span><b>{livePositions.length}</b></div>
          {livePositions.map(trade => <article key={trade.id} className="auto-trade-position" data-testid={`autotrade-position-${trade.id}`}>
            <div><b>{trade.label ?? trade.symbol}</b><span>{trade.status === "OPEN" ? "OPEN" : "PENDING"}</span></div>
            <small>{trade.symbol} · {money(trade.stake)} · {trade.growthRatePercent ?? 4}% growth</small>
          </article>)}
          {!livePositions.length && <p className="auto-trade-empty">No open {tradeMode} Accumulator positions.</p>}
        </section>

        <section className="auto-trade-panel auto-trade-recent">
          <div className="auto-trade-panel-heading"><span>WIN / LOSS HISTORY · {tradeMode.toUpperCase()}</span><b>{historyTrades.length}</b></div>
          {historyTrades.map(trade => <article key={trade.id} className="auto-trade-result" data-testid={`autotrade-result-${trade.id}`}>
            <span><b>{trade.label ?? trade.symbol}</b><small>{trade.symbol} · {trade.closedAt ?? trade.openedAt ?? trade.status}</small></span>
            <strong className={trade.result === "WIN" ? "positive" : trade.result === "LOSS" ? "negative" : ""}>{trade.result} · {money(trade.profit)}</strong>
          </article>)}
          {!historyTrades.length && <p className="auto-trade-empty">No settled {tradeMode} Accumulator results yet.</p>}
        </section>
        </div>}
      </aside>
    </main>
    <footer className={`auto-trade-footer ${tradeMode === "real" ? "real" : ""}`}><LockKeyhole size={13} /> {tradeMode === "real" ? "REAL-MONEY MODE · SERVER SAFETY GATES AND EXPLICIT REAL CONFIRMATION REQUIRED" : "DEMO MODE · NO REAL-MONEY ORDERS"}</footer>
  </div>;
}
