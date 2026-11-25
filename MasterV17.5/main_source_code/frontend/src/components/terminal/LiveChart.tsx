import { marketNow } from "@/lib/marketClock";
import { memo, useCallback, useEffect, useId, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { AreaSeries, BarSeries, CandlestickSeries, ColorType, CrosshairMode, LineSeries, LineStyle, createChart, createSeriesMarkers, type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type IPriceLine, type SeriesType, type Time, type UTCTimestamp } from "lightweight-charts";
import { Activity, BarChart3, CandlestickChart, Grip, LineChart, Maximize2, Minimize2, MousePointer2, RefreshCw, Waves, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { apiGet } from "@/lib/api";
import CountryFlags from "./CountryFlags";
import { marketDisplayName, type Market } from "@/lib/markets";
import { sourceFor, symbolFor, useMarketState, useTelemetryStream, type LiveCandle, type TelemetrySnapshot } from "@/hooks/useMarketBackend";
import { useSignalCheck } from "@/hooks/useLiveSignals";
import { useTickStream, type Tick } from "@/hooks/useTickStream";
import { bollinger, ema, heikinAshi, rsi } from "@/lib/indicators";
import { analyseChart, type ChartEvent } from "@/lib/chartAnalysis";

type ChartPair = Pick<Market, "id" | "label" | "shortLabel" | "base" | "precision" | "symbol" | "session" | "category" | "countries" | "derivMarket" | "derivSymbol" | "sourceSymbol">;
type ChartKind = "Candles" | "Heikin Ashi" | "Bars" | "Area";
const TIMEFRAMES = ["1s", "5s", "15s", "1m", "5m", "10m", "15m", "30m", "1h"];
const KINDS: { name: ChartKind; icon: typeof BarChart3 }[] = [{ name: "Candles", icon: CandlestickChart }, { name: "Heikin Ashi", icon: Waves }, { name: "Bars", icon: BarChart3 }, { name: "Area", icon: LineChart }];
const UP = "#16c784", DOWN = "#f6465d";
const seconds = (tf: string) => Number(tf.slice(0, -1)) * ({ s: 1, m: 60, h: 3600 } as Record<string, number>)[tf.slice(-1)];
type MemoryEvent = { type: string; epoch: number; timeframe: string };
type ChartHover = { candle: LiveCandle; x: number; y: number; date: string };
type ReplayPoint = { label: string; x: number; y: number; detected: boolean };
type ExtendedTrendLine = { kind: "support" | "resistance"; x1: number; y1: number; x2: number; y2: number };
type PanelPosition = { left: number; top: number };
const TREND_COLORS = { support: "#80b69b", resistance: "#c5a06e" };
const REPLAY_PATTERNS = [
  { label: "TRAP", test: /^(BULL|BEAR)_TRAP$/, price: (bar: LiveCandle) => (bar.high + bar.low) / 2 },
  { label: "PIN", test: /^PIN_(BULL|BEAR)$/, price: (bar: LiveCandle) => (bar.high + bar.low) / 2 },
  { label: "ENGULF", test: /^(BULL|BEAR)_ENGULF$/, price: (bar: LiveCandle) => bar.close },
  { label: "BOS", test: /^BOS_(UP|DOWN)$/, price: (bar: LiveCandle) => bar.close },
];
const MEMORY_MARKER: Record<string, { position: "aboveBar" | "belowBar"; color: string; shape: "square" | "circle"; text: string }> = {
  BULL_TRAP: { position: "aboveBar", color: "#f6465d", shape: "square", text: "BULL TRAP" },
  BEAR_TRAP: { position: "belowBar", color: "#16c784", shape: "square", text: "BEAR TRAP" },
  GAP_UP: { position: "belowBar", color: "#3b9cff", shape: "circle", text: "GAP UP" },
  GAP_DOWN: { position: "aboveBar", color: "#ff9f43", shape: "circle", text: "GAP DOWN" },
  STOP_HUNT: { position: "aboveBar", color: "#f5c451", shape: "circle", text: "STOP HUNT" },
};
const ANALYSIS_MARKER: Partial<Record<ChartEvent["type"], { position: "aboveBar" | "belowBar"; color: string; shape: "square" | "circle"; text: string }>> = {
  GAP_UP: { position: "belowBar", color: "#3b9cff", shape: "circle", text: "GAP ↑" },
  GAP_DOWN: { position: "aboveBar", color: "#ff9f43", shape: "circle", text: "GAP ↓" },
  BULL_TRAP: { position: "aboveBar", color: "#f6465d", shape: "square", text: "TRAP" },
  BEAR_TRAP: { position: "belowBar", color: "#16c784", shape: "square", text: "TRAP" },
  BULL_ENGULF: { position: "belowBar", color: "#16c784", shape: "circle", text: "ENGULF" },
  BEAR_ENGULF: { position: "aboveBar", color: "#f6465d", shape: "circle", text: "ENGULF" },
  PIN_BULL: { position: "belowBar", color: "#16c784", shape: "circle", text: "PIN" },
  PIN_BEAR: { position: "aboveBar", color: "#f6465d", shape: "circle", text: "PIN" },
  BOS_UP: { position: "belowBar", color: "#4fe0ff", shape: "square", text: "BOS ↑" },
  BOS_DOWN: { position: "aboveBar", color: "#ff9f43", shape: "square", text: "BOS ↓" },
};
const localTime = (time: Time) => new Date(Number(time) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

function addMainSeries(chart: IChartApi, kind: ChartKind, precision: number): ISeriesApi<SeriesType> {
  const priceFormat = { type: "price" as const, precision, minMove: 1 / 10 ** precision };
  if (kind === "Area") return chart.addSeries(AreaSeries, { lineColor: "#3b9cff", topColor: "rgba(59,156,255,.35)", bottomColor: "rgba(59,156,255,0)", lineWidth: 2, priceFormat });
  if (kind === "Bars") return chart.addSeries(BarSeries, { upColor: UP, downColor: DOWN, thinBars: false, priceFormat });
  return chart.addSeries(CandlestickSeries, { upColor: UP, downColor: DOWN, borderVisible: true, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN, priceFormat });
}

function LiveChartInner({ pair, online, controls, tradeMarker, telemetry, onTimeframeChange }: { pair: ChartPair; online: boolean; controls?: ReactNode; tradeMarker?: { price: number; direction: "UP" | "DOWN" }; telemetry?: TelemetrySnapshot | null; onTimeframeChange?: (value: string) => void }) {
  const [timeframe, setTimeframe] = useState("1m");
  const [kind, setKind] = useState<ChartKind>("Candles");
  const [indicators, setIndicators] = useState({ ema: true, bb: false, rsi: false, mem: true });
  const [focus, setFocus] = useState(false);
  const [legend, setLegend] = useState<LiveCandle | null>(null);
  const [hover, setHover] = useState<ChartHover | null>(null);
  const [chartSize, setChartSize] = useState({ width: 0, height: 0 });
  const [analysisVisible, setAnalysisVisible] = useState(true);
  const [analysisPosition, setAnalysisPosition] = useState<PanelPosition | null>(null);
  const [replayStage, setReplayStage] = useState<number | null>(null);
  const [chartViewVersion, setChartViewVersion] = useState(0);
  const [nowSec, setNowSec] = useState(() => marketNow() / 1000);
  const replayClipId = useId().replaceAll(":", "");
  const host = useRef<HTMLDivElement>(null);
  const canvasContainer = useRef<HTMLDivElement>(null);
  const analysisPanel = useRef<HTMLElement>(null);
  const panelDrag = useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
  const chart = useRef<IChartApi | null>(null);
  const main = useRef<ISeriesApi<SeriesType> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const overlays = useRef<ISeriesApi<"Line">[]>([]);
  const structureSeries = useRef<ISeriesApi<"Line">[]>([]);
  const levelLines = useRef<IPriceLine[]>([]);
  const tradeLine = useRef<IPriceLine | null>(null);
  const renderSignature = useRef("");
  const renderedTail = useRef<number | null>(null);
  const renderedFirst = useRef<number | null>(null);
  const renderedCount = useRef(0);
  const markerSignature = useRef("");
  // 0-lag tick path: refs only (no React state), coalesced to one series.update() per animation frame.
  const lastTick = useRef<Tick | null>(null);
  const lastRaw = useRef<LiveCandle | null>(null);
  const rafId = useRef<number | null>(null);
  const kindRef = useRef<ChartKind>(kind);
  kindRef.current = kind;
  const timeframeRef = useRef(timeframe);
  timeframeRef.current = timeframe;
  const priceEl = useRef<HTMLElement>(null);
  const closeEl = useRef<HTMLSpanElement>(null);
  const highEl = useRef<HTMLSpanElement>(null);
  const lowEl = useRef<HTMLSpanElement>(null);
  const market = useMarketState(pair, timeframe, online);
  const check = useSignalCheck();
  const chartSource = sourceFor(pair);
  const chartSymbol = symbolFor(pair);
  const telemetryMatches = telemetry?.source === chartSource && telemetry.symbol === chartSymbol && telemetry.timeframe === timeframe;
  const telemetryFeed = useTelemetryStream(chartSource, chartSymbol, timeframe, online && !telemetryMatches);
  const liveTelemetry = telemetryMatches ? telemetry : telemetryFeed.data;
  const memorySymbol = chartSymbol;
  const memory = useQuery({
    queryKey: ["chart-memory-events", memorySymbol, timeframe],
    queryFn: () => apiGet<{ items: MemoryEvent[] }>(`/v1/memory/events?symbol=${encodeURIComponent(memorySymbol)}&timeframe=${timeframe}&limit=100`),
    enabled: online && indicators.mem && !!memorySymbol,
    refetchInterval: 20000,
  });
  const useTelemetry = liveTelemetry && liveTelemetry.timeframe === timeframe && liveTelemetry.candles.length > 0;
  const raw = useMemo(() => {
    const rows = (useTelemetry ? liveTelemetry!.candles : market.data?.candles ?? []).filter(c => Number.isFinite(c.close) && Number.isFinite(c.open));
    const step = seconds(timeframe);
    const unique = new Map<number, LiveCandle>();
    rows.forEach(c => { const epoch = Math.floor(c.epoch / step) * step; unique.set(epoch, { ...c, epoch }); });
    return [...unique.values()].sort((a, b) => a.epoch - b.epoch);
  }, [useTelemetry, liveTelemetry, market.data?.candles, timeframe]);
  const livePrice = (useTelemetry ? liveTelemetry!.price : market.data?.price) ?? raw.at(-1)?.close ?? null;
  // Forming candle = last stored candle merged with the newest tick (the tick ref is fresher than the 1 Hz snapshot).
  const mergeTick = useCallback((last: LiveCandle, tick: Tick | null, step: number): LiveCandle => {
    if (!tick) return last;
    const bucket = Math.floor(tick.epoch / step) * step;
    if (bucket > last.epoch) return { epoch: bucket, open: tick.price, high: tick.price, low: tick.price, close: tick.price };
    if (bucket < last.epoch) return last;
    return { ...last, close: tick.price, high: Math.max(last.high, tick.price), low: Math.min(last.low, tick.price) };
  }, []);
  const candles = useMemo(() => {
    if (!raw.length) return raw;
    const last = raw.at(-1)!;
    const price = livePrice ?? last.close;
    const merged = mergeTick({ ...last, close: price, high: Math.max(last.high, price), low: Math.min(last.low, price) }, lastTick.current, seconds(timeframe));
    return merged.epoch === last.epoch ? [...raw.slice(0, -1), merged] : [...raw, merged];
  }, [raw, livePrice, timeframe, mergeTick]);
  lastRaw.current = candles.at(-1) ?? null;
  const priceAction = useMemo(() => analyseChart(raw), [raw]);
  const extendedTrendLines: ExtendedTrendLine[] = (priceAction?.trendLines ?? []).flatMap(line => {
    const [start, end] = line.points;
    const timeScale = chart.current?.timeScale();
    const startX = timeScale?.timeToCoordinate(start.epoch as UTCTimestamp);
    const endX = timeScale?.timeToCoordinate(end.epoch as UTCTimestamp);
    const startY = main.current?.priceToCoordinate(start.value);
    const endY = main.current?.priceToCoordinate(end.value);
    if (startX == null || endX == null || startY == null || endY == null || endX <= startX) return [];
    const slope = (endY - startY) / (endX - startX);
    return [{ kind: line.kind, x1: 0, y1: endY - endX * slope, x2: endX, y2: endY }];
  });
  void chartViewVersion;
  const replayTargets = useMemo<ReplayPoint[]>(() => {
    if (!candles.length) return [];
    const coordinate = (epoch: number, price: number, fallbackX: number, fallbackY: number) => {
      const x = chart.current?.timeScale().timeToCoordinate(epoch as UTCTimestamp);
      const y = main.current?.priceToCoordinate(price);
      return x !== null && x !== undefined && y !== null && y !== undefined
        && x >= 0 && x <= chartSize.width && y >= 0 && y <= chartSize.height
        ? { x, y }
        : { x: chartSize.width * fallbackX, y: chartSize.height * fallbackY };
    };
    const patterns = REPLAY_PATTERNS.map((pattern, index) => {
      const event = priceAction?.events.filter(item => pattern.test.test(item.type)).at(-1);
      const candleIndex = Math.max(0, Math.min(candles.length - 1, Math.floor(candles.length * (0.36 + index * 0.14))));
      const candle = (event && candles.find(item => item.epoch === event.epoch)) ?? candles[candleIndex];
      return {
        ...coordinate(candle.epoch, pattern.price(candle), 0.37 + index * 0.13, 0.58 - (index % 2) * 0.12),
        label: pattern.label,
        detected: !!event && candles.some(item => item.epoch === event.epoch),
      };
    });
    const actualLine = priceAction?.trendLines.at(-1);
    const fallbackStart = candles[Math.max(0, candles.length - 20)];
    const fallbackEnd = candles.at(-1)!;
    const start = actualLine?.points[0] ?? { epoch: fallbackStart.epoch, value: fallbackStart.close };
    const end = actualLine?.points[1] ?? { epoch: fallbackEnd.epoch, value: fallbackEnd.close };
    const lineStart = coordinate(start.epoch, start.value, 0.22, 0.68);
    const lineEnd = coordinate(end.epoch, end.value, 0.82, 0.36);
    return [...patterns, { ...lineEnd, label: "TREND LINE", detected: !!actualLine }, { ...lineStart, label: "TREND LINE START", detected: !!actualLine }];
  }, [candles, chartSize, priceAction]);
  const replayLineStart = replayTargets[5];
  const replayLineEnd = replayTargets[4];
  const replayPointer = replayStage === null ? null : replayStage <= 3 ? replayTargets[replayStage] : replayStage === 4 ? replayLineStart : replayLineEnd;
  const replayLabel = replayStage === null ? "" : replayStage >= 4 ? "TREND LINE" : replayTargets[replayStage]?.label ?? "";
  const replayLabelIsDemo = replayStage !== null && (replayStage >= 4 ? !replayLineEnd?.detected : !replayPointer?.detected);
  const tickSymbol = pair.derivSymbol ?? (pair.symbol.startsWith("frx") || /^(R_|1HZ|BOOM|CRASH|JD|cry|stp|WLD|RDB)/.test(pair.symbol) ? pair.symbol : null);
  // Ultra-smooth path: every tick is tweened (ease-out, ~320 ms) from the currently drawn close to the new price,
  // so the forming candle glides instead of jumping once per second. A new candle bucket snaps immediately.
  const tween = useRef<{ from: number; to: number; start: number } | null>(null);
  const drawnClose = useRef<number | null>(null);
  const TWEEN_MS = 320;
  const paintTick = useCallback((frameTime?: number) => {
    rafId.current = null;
    const series = main.current, tick = lastTick.current, last = lastRaw.current;
    if (!series || !tick || !last) return;
    const step = seconds(timeframeRef.current);
    const bucket = Math.floor(tick.epoch / step) * step;
    if (bucket < last.epoch) return;
    const now = frameTime ?? performance.now();
    let close = tick.price;
    if (bucket === last.epoch && tween.current) {
      const t = Math.min(1, (now - tween.current.start) / TWEEN_MS);
      const eased = 1 - (1 - t) ** 3;
      close = tween.current.from + (tween.current.to - tween.current.from) * eased;
      if (t >= 1) tween.current = null;
    } else {
      tween.current = null;
    }
    const candle = bucket > last.epoch
      ? { epoch: bucket, open: tick.price, high: tick.price, low: tick.price, close: tick.price }
      : { ...last, close, high: Math.max(last.high, close, tick.price), low: Math.min(last.low, close, tick.price) };
    drawnClose.current = close;
    lastRaw.current = { ...candle, close: tick.price };
    if (candle.epoch > (renderedTail.current ?? 0)) renderedTail.current = candle.epoch;
    const k = kindRef.current;
    if (k !== "Heikin Ashi") series.update(k === "Area" ? { time: candle.epoch as UTCTimestamp, value: candle.close } : { time: candle.epoch as UTCTimestamp, open: candle.open, high: candle.high, low: candle.low, close: candle.close });
    const digits = pair.precision;
    if (priceEl.current) priceEl.current.textContent = close.toFixed(digits);
    if (closeEl.current) closeEl.current.textContent = close.toFixed(digits);
    if (highEl.current) highEl.current.textContent = candle.high.toFixed(digits);
    if (lowEl.current) lowEl.current.textContent = candle.low.toFixed(digits);
    if (tween.current && rafId.current === null) rafId.current = window.requestAnimationFrame(paintTick);
  }, [pair.precision]);
  useTickStream(tickSymbol, online, useCallback((tick: Tick) => {
    const last = lastRaw.current;
    const step = seconds(timeframeRef.current);
    const sameBucket = last && Math.floor(tick.epoch / step) * step === last.epoch;
    const from = drawnClose.current ?? last?.close ?? tick.price;
    tween.current = sameBucket && Number.isFinite(from) && from !== tick.price ? { from, to: tick.price, start: performance.now() } : null;
    lastTick.current = tick;
    if (rafId.current === null) rafId.current = window.requestAnimationFrame(paintTick);
  }, [paintTick]));
  useEffect(() => { lastTick.current = null; tween.current = null; drawnClose.current = null; return () => { if (rafId.current !== null) window.cancelAnimationFrame(rafId.current); rafId.current = null; }; }, [pair.id, timeframe]);

  useEffect(() => { const id = window.setInterval(() => setNowSec(marketNow() / 1000), 500); return () => clearInterval(id); }, []);
  useEffect(() => {
    if (!host.current) return;
    const instance = createChart(host.current, {
      autoSize: true,
      layout: { attributionLogo: false, background: { type: ColorType.Solid, color: "rgba(15,20,32,0)" }, textColor: "#c9d3e6", fontSize: 12, fontFamily: "'JetBrains Mono Variable', monospace", panes: { separatorColor: "#26304a" } },
      grid: { vertLines: { color: "rgba(64,78,110,.12)" }, horzLines: { color: "rgba(64,78,110,.12)" } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: "#7d8db0", labelBackgroundColor: "#2b3a5c" }, horzLine: { color: "#7d8db0", labelBackgroundColor: "#2b3a5c" } },
      rightPriceScale: { borderColor: "#26304a", scaleMargins: { top: 0.12, bottom: 0.12 } },
      timeScale: { borderColor: "#26304a", timeVisible: true, secondsVisible: true, rightOffset: 8, barSpacing: 9, tickMarkFormatter: (time: Time) => localTime(time).slice(0, 5) },
      localization: { timeFormatter: localTime },
    });
    chart.current = instance;
    instance.subscribeCrosshairMove(param => {
      const point = main.current ? param.seriesData.get(main.current) as { open?: number; high?: number; low?: number; close?: number; value?: number } | undefined : undefined;
      if (point && param.time && param.point) {
        const candle = { epoch: Number(param.time), open: point.open ?? point.value ?? 0, high: point.high ?? point.value ?? 0, low: point.low ?? point.value ?? 0, close: point.close ?? point.value ?? 0 };
        const x = param.point.x > host.current!.clientWidth - 250 ? param.point.x - 250 : param.point.x + 14;
        const y = param.point.y > host.current!.clientHeight - 115 ? param.point.y - 115 : param.point.y + 14;
        setLegend(candle);
        setHover({ candle, x: Math.max(8, x), y: Math.max(8, y), date: new Date(candle.epoch * 1000).toLocaleString([], { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) });
      } else {
        setLegend(null);
        setHover(null);
      }
    });
    const resizeObserver = new ResizeObserver(entries => {
      const bounds = entries[0]?.contentRect;
      if (bounds) setChartSize({ width: bounds.width, height: bounds.height });
    });
    if (host.current) resizeObserver.observe(host.current);
    const updateVisibleRange = () => setChartViewVersion(version => version + 1);
    instance.timeScale().subscribeVisibleLogicalRangeChange(updateVisibleRange);
    return () => {
      instance.timeScale().unsubscribeVisibleLogicalRangeChange(updateVisibleRange);
      resizeObserver.disconnect();
      instance.remove();
      chart.current = null;
      main.current = null;
      markers.current = null;
      overlays.current = [];
      tradeLine.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    if (main.current) {
      levelLines.current.forEach(line => main.current?.removePriceLine(line));
      levelLines.current = [];
      structureSeries.current.forEach(line => instance.removeSeries(line));
      structureSeries.current = [];
      instance.removeSeries(main.current);
    }
    main.current = addMainSeries(instance, kind, pair.precision);
    renderSignature.current = "";
    renderedTail.current = null;
    renderedFirst.current = null;
    renderedCount.current = 0;
    markerSignature.current = "";
    markers.current = createSeriesMarkers(main.current, []);
    tradeLine.current = null;
  }, [kind, pair.precision]);

  useEffect(() => {
    const instance = chart.current, series = main.current;
    if (!instance || !series) return;
    const shown = kind === "Heikin Ashi" ? heikinAshi(candles) : candles;
    const toPoint = (c: LiveCandle) => kind === "Area" ? { time: c.epoch as UTCTimestamp, value: c.close } : { time: c.epoch as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close };
    const closes = candles.map(c => c.close);
    const overlayDefs: { values: (number | null)[]; color: string; pane: number; width: 1 | 2 }[] = [];
    if (indicators.ema) { overlayDefs.push({ values: ema(closes, 20), color: "#f5c451", pane: 0, width: 2 }, { values: ema(closes, 50), color: "#b07cff", pane: 0, width: 2 }); }
    if (indicators.bb) { const band = bollinger(closes, 20, 2); overlayDefs.push({ values: band.upper, color: "rgba(80,180,255,.8)", pane: 0, width: 1 }, { values: band.mid, color: "rgba(80,180,255,.45)", pane: 0, width: 1 }, { values: band.lower, color: "rgba(80,180,255,.8)", pane: 0, width: 1 }); }
    if (indicators.rsi) overlayDefs.push({ values: rsi(closes, 14), color: "#ff9f43", pane: 1, width: 2 });
    // Smooth render: the history signature only changes when the series itself changes (pair, timeframe, kind, indicators,
    // or a reconnect that replaced history). A live tick or a newly opened candle is applied with series.update() in place.
    const signature = `${kind}|${timeframe}|${JSON.stringify(indicators)}|${pair.id}`;
    // history arrived / was prepended (first bar now older than what is drawn) -> full redraw; a sliding window -> incremental
    const historyGrew = shown.length > 0 && (renderedFirst.current === null || shown[0].epoch < renderedFirst.current || shown.length > renderedCount.current + 3);
    const tail = shown.at(-1);
    const lastRendered = renderedTail.current;
    const incremental = signature === renderSignature.current && !historyGrew && tail && lastRendered !== null && tail.epoch >= lastRendered && (tail.epoch - lastRendered) <= seconds(timeframe) * 2 && overlays.current.length === overlayDefs.length;
    if (incremental) {
      const fresh = lastTick.current && Math.floor(lastTick.current.epoch / seconds(timeframe)) * seconds(timeframe) === tail.epoch ? { ...tail, close: drawnClose.current ?? lastTick.current.price, high: Math.max(tail.high, lastTick.current.price), low: Math.min(tail.low, lastTick.current.price) } : tail;
      series.update(toPoint(fresh));
      overlays.current.forEach((line, i) => { const v = overlayDefs[i].values.at(-1); if (v !== null && v !== undefined) line.update({ time: tail.epoch as UTCTimestamp, value: v }); });
      renderedTail.current = tail.epoch;
      renderedCount.current = shown.length;
      renderedFirst.current = shown[0]?.epoch ?? renderedFirst.current;
    } else {
      renderSignature.current = signature;
      renderedTail.current = tail?.epoch ?? null;
      renderedFirst.current = shown[0]?.epoch ?? null;
      renderedCount.current = shown.length;
      series.setData(shown.map(toPoint));
      overlays.current.forEach(line => instance.removeSeries(line));
      overlays.current = overlayDefs.map(def => {
        const s = instance.addSeries(LineSeries, { color: def.color, lineWidth: def.width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, def.pane);
        s.setData(def.values.flatMap((v, i) => v === null ? [] : [{ time: candles[i].epoch as UTCTimestamp, value: v }]));
        return s;
      });
    }
    const entries = (liveTelemetry?.signals ?? []).filter(s => s.timeframe === timeframe && candles.some(c => c.epoch === s.entryEpoch));
    const signalMarkers = entries.slice(0, 20).map(s => ({ time: s.entryEpoch as UTCTimestamp, position: s.direction === "CALL" ? "belowBar" as const : "aboveBar" as const, color: s.direction === "CALL" ? UP : DOWN, shape: s.direction === "CALL" ? "arrowUp" as const : "arrowDown" as const, text: `${s.direction} ${Math.round(s.confidence)}%` }));
    const epochs = new Set(candles.map(c => c.epoch));
    const memoryMarkers = indicators.mem ? (memory.data?.items ?? []).filter(e => e.timeframe === timeframe && MEMORY_MARKER[e.type] && epochs.has(e.epoch)).map(e => ({ time: e.epoch as UTCTimestamp, ...MEMORY_MARKER[e.type] })) : [];
    const analysisMarkers = indicators.mem ? (priceAction?.events ?? [])
      .filter(event => ANALYSIS_MARKER[event.type] && epochs.has(event.epoch))
      .slice(-30)
      .map(event => ({ time: event.epoch as UTCTimestamp, ...ANALYSIS_MARKER[event.type]! })) : [];
    const uniqueMarkers = new Map<string, (typeof signalMarkers)[number] | (typeof memoryMarkers)[number] | (typeof analysisMarkers)[number]>();
    [...signalMarkers, ...memoryMarkers, ...analysisMarkers].forEach(marker => uniqueMarkers.set(`${Number(marker.time)}-${marker.text}`, marker));
    const nextMarkers = [...uniqueMarkers.values()].sort((a, b) => Number(a.time) - Number(b.time));
    const markerKey = JSON.stringify(nextMarkers);
    if (markerKey !== markerSignature.current) { markerSignature.current = markerKey; markers.current?.setMarkers(nextMarkers); }
  }, [candles, kind, indicators, liveTelemetry?.signals, timeframe, memory.data, pair.id, priceAction]);

  useEffect(() => {
    const series = main.current;
    if (!series) return;
    levelLines.current.forEach(line => series.removePriceLine(line));
    levelLines.current = (priceAction?.levels ?? []).map(level => series.createPriceLine({
      price: level.price,
      color: TREND_COLORS[level.kind],
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: true,
      title: `${level.kind === "support" ? "SUPPORT" : "RESISTANCE"} · ${level.touches}x`,
    }));
  }, [priceAction, pair.id, kind]);

  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    structureSeries.current.forEach(line => instance.removeSeries(line));
    structureSeries.current = [];
    if (!indicators.mem || !priceAction) return;
    structureSeries.current = priceAction.trendLines.map(line => {
      const trend = instance.addSeries(LineSeries, {
        color: TREND_COLORS[line.kind],
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      trend.setData(line.points.map(point => ({ time: point.epoch as UTCTimestamp, value: point.value })));
      return trend;
    });
  }, [priceAction, indicators.mem, pair.id, kind]);

  useEffect(() => {
    const series = main.current;
    if (!series) return;
    if (tradeLine.current) { series.removePriceLine(tradeLine.current); tradeLine.current = null; }
    if (tradeMarker) tradeLine.current = series.createPriceLine({ price: tradeMarker.price, color: tradeMarker.direction === "UP" ? UP : DOWN, lineWidth: 2, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: `${tradeMarker.direction} ENTRY` });
  }, [tradeMarker, kind]);

  useEffect(() => {
    if (replayStage === null) return;
    const timer = window.setTimeout(
      () => setReplayStage(replayStage >= 6 ? null : replayStage + 1),
      replayStage >= 5 ? 1000 : 900,
    );
    return () => window.clearTimeout(timer);
  }, [replayStage]);

  const startPanelDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest("button")) return;
    const parent = canvasContainer.current?.getBoundingClientRect();
    const panel = analysisPanel.current?.getBoundingClientRect();
    if (!parent || !panel) return;
    event.preventDefault();
    const left = panel.left - parent.left;
    const top = panel.top - parent.top;
    setAnalysisPosition({ left, top });
    panelDrag.current = { pointerId: event.pointerId, offsetX: event.clientX - panel.left, offsetY: event.clientY - panel.top };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const movePanel = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = panelDrag.current;
    const parent = canvasContainer.current?.getBoundingClientRect();
    const panel = analysisPanel.current?.getBoundingClientRect();
    if (!drag || drag.pointerId !== event.pointerId || !parent || !panel) return;
    const maxLeft = Math.max(8, parent.width - panel.width - 8);
    const maxTop = Math.max(82, parent.height - panel.height - 48);
    setAnalysisPosition({
      left: Math.max(8, Math.min(maxLeft, event.clientX - parent.left - drag.offsetX)),
      top: Math.max(82, Math.min(maxTop, event.clientY - parent.top - drag.offsetY)),
    });
  };
  const stopPanelDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (panelDrag.current?.pointerId !== event.pointerId) return;
    panelDrag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  useEffect(() => { chart.current?.timeScale().scrollToRealTime(); }, [pair.id, timeframe]);
  const tfSeconds = seconds(timeframe);
  const remaining = Math.max(0, Math.ceil((Math.floor(nowSec / tfSeconds) + 1) * tfSeconds - nowSec));
  const derivAge = check.data?.deriv?.ageSeconds;
  const feedLive = check.data?.deriv?.state === "DATA_RECEIVING" && derivAge !== undefined && derivAge <= (check.data?.freshnessSeconds ?? 60);
  const last = candles.at(-1);
  const shownLegend = legend ?? last ?? null;
  const change = last && candles[0] ? (last.close - candles[0].open) / candles[0].open * 100 : 0;
  const fmt = (n?: number) => n === undefined ? "—" : n.toFixed(pair.precision);
  return <section data-testid="chart-workspace" className={`chart-workspace live-chart ${focus ? "chart-focused" : ""}`}>
    <div data-testid="chart-header" className="chart-header">
      <div data-testid="active-pair-heading" className="active-pair-heading"><CountryFlags pairId={pair.id} markets={[pair as Market]} /><div><h1 data-testid="active-pair-name">{marketDisplayName(pair)}</h1><div data-testid="active-pair-price" className="active-pair-price"><strong ref={priceEl}>{livePrice === null ? "—" : livePrice.toFixed(pair.precision)}</strong><span data-testid="active-pair-change" className={change >= 0 ? "text-positive" : "text-negative"}>{last ? `${change >= 0 ? "+" : ""}${change.toFixed(2)}%` : "waiting for Deriv"}</span></div></div></div>
      <div data-testid="chart-stream-state" className={`stream-state ${feedLive && online ? "" : "offline"}`}><i />{!online ? "PAUSED" : feedLive ? "DERIV LIVE" : "FEED OFFLINE"}</div>
      <div data-testid="candle-countdown" className="candle-countdown" title="Time until current candle closes"><Activity size={13} />{String(Math.floor(remaining / 60)).padStart(2, "0")}:{String(remaining % 60).padStart(2, "0")}</div>
      {controls}
      <Button data-testid="chart-refresh-button" variant="ghost" size="icon-sm" aria-label="Refresh chart" onClick={() => void market.refetch()}><RefreshCw size={14} /></Button>
      <Button data-testid="chart-expand-button" variant="ghost" size="icon-sm" aria-label={focus ? "Exit chart focus" : "Expand chart"} aria-pressed={focus} onClick={() => setFocus(!focus)}>{focus ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</Button>
    </div>
    <div ref={canvasContainer} data-testid="chart-canvas-container" className="chart-canvas-container">
      <div data-testid="chart-ohlc" className="chart-ohlc">O <span>{fmt(shownLegend?.open)}</span> H <span ref={legend ? undefined : highEl}>{fmt(shownLegend?.high)}</span> L <span ref={legend ? undefined : lowEl}>{fmt(shownLegend?.low)}</span> C <span ref={legend ? undefined : closeEl}>{fmt(shownLegend?.close)}</span></div>
      {indicators.ema && <div className="chart-indicator-legend" data-testid="chart-indicator-legend"><i style={{ background: "#f5c451" }} />EMA 20 <i style={{ background: "#b07cff" }} />EMA 50</div>}
      <div ref={host} data-testid="candlestick-chart-canvas" className="live-chart-host" />
      {hover && <div className="chart-hover-tooltip" data-testid="chart-hover-tooltip" role="status" style={{ left: hover.x, top: hover.y }}>
        <div className="chart-hover-heading"><strong>{marketDisplayName(pair)}</strong><time>{hover.date}</time></div>
        <dl>{([["Open", hover.candle.open], ["High", hover.candle.high], ["Low", hover.candle.low], ["Close", hover.candle.close]] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{fmt(value)}</dd></div>)}</dl>
      </div>}
      {!!extendedTrendLines.length && <svg className="chart-structure-extensions" data-testid="chart-trendline-extensions" aria-hidden="true">
        {extendedTrendLines.map((line, index) => <line key={`${line.kind}-${index}`} x1={line.x1} y1={line.y1} x2={line.x2} y2={line.y2} stroke={TREND_COLORS[line.kind]} />)}
      </svg>}
      {replayStage !== null && replayPointer && replayLineStart && replayLineEnd && <svg className="chart-analysis-replay" data-testid="chart-analysis-replay" viewBox={`0 0 ${Math.max(1, chartSize.width)} ${Math.max(1, chartSize.height)}`} preserveAspectRatio="none" aria-label="Simulated chart analysis replay">
        {replayStage >= 4 && <defs><clipPath id={replayClipId}><rect x="0" y="0" width={replayStage >= 5 ? chartSize.width : 0} height={chartSize.height} className="chart-replay-line-reveal" /></clipPath></defs>}
        {replayStage >= 4 && <path d={`M ${replayLineStart.x} ${replayLineStart.y} L ${replayLineEnd.x} ${replayLineEnd.y}`} clipPath={`url(#${replayClipId})`} className="chart-replay-trend-line" />}
        <g style={{ transform: `translate(${replayPointer.x}px, ${replayPointer.y}px)` }} className="chart-replay-pointer">
          <circle r="14" className="chart-replay-focus" />
          <path d="M -2 -12 L -2 8 L 3 3 L 7 11 L 11 9 L 7 1 L 14 1 Z" className="chart-replay-cursor" />
          <text x="15" y="-13">{replayLabel}{replayLabelIsDemo ? " · DEMO" : ""}</text>
        </g>
      </svg>}
      {!candles.length && <div data-testid="chart-empty-state" className="chart-empty-state">{market.isLoading ? "Loading Deriv candles…" : "No live candles for this market yet (closed session or warming up)."}</div>}
      {analysisVisible ? <aside ref={analysisPanel} className={`chart-ai-overlay ${analysisPosition ? "chart-ai-overlay-positioned" : ""}`} data-testid="chart-ai-overlay" aria-label="Live backend analysis telemetry" style={analysisPosition ? { left: analysisPosition.left, top: analysisPosition.top, right: "auto" } : undefined}>
        <div className="chart-ai-heading" onPointerDown={startPanelDrag} onPointerMove={movePanel} onPointerUp={stopPanelDrag} onPointerCancel={stopPanelDrag}>
          <Grip className="chart-ai-drag-handle" size={13} aria-hidden="true" />
          <i className={liveTelemetry?.status === "ONLINE" ? "connected" : ""} />BACKEND EVIDENCE<span>{liveTelemetry?.status ?? (telemetryFeed.connected ? "CONNECTING" : "WAITING")}</span>
          <button type="button" className="chart-ai-hide" aria-label="Hide analysis panel" data-testid="chart-analysis-hide-button" onClick={() => setAnalysisVisible(false)}><X size={13} /></button>
        </div>
        {liveTelemetry ? <>
          <div className="chart-ai-direction"><strong>{liveTelemetry.evidence.direction}</strong><span>AGREEMENT {Math.round(liveTelemetry.evidence.agreement * 100)}%</span></div>
          <div className="chart-ai-meta">Feed {liveTelemetry.feed.ageSeconds == null ? "—" : `${Math.round(liveTelemetry.feed.ageSeconds)}s`} · Agents {liveTelemetry.registry.active}/{liveTelemetry.registry.maximum}</div>
          <div className="chart-ai-modules">{liveTelemetry.modules.slice(0, 3).map(module => <span key={module.id} title={module.summary}><i className={`tone-${module.tone}`} />{module.name}: {module.value}</span>)}</div>
        </> : <p>{telemetryFeed.connected ? "Waiting for analysis snapshot…" : "No live backend telemetry"}</p>}
      </aside> : <button type="button" className="chart-analysis-restore" data-testid="chart-analysis-show-button" onClick={() => setAnalysisVisible(true)}>Show analysis</button>}
      <Button data-testid="chart-analysis-replay-button" className="chart-analysis-replay-button" variant="ghost" size="sm" disabled={!candles.length || replayStage !== null} onClick={() => setReplayStage(0)}><MousePointer2 size={13} />{replayStage === null ? "Simulate agent review" : `Reviewing ${replayLabel.toLowerCase()}`}</Button>
    </div>
    <div data-testid="chart-toolbar" className="chart-toolbar">
      <div data-testid="timeframe-controls" className="timeframe-controls">{TIMEFRAMES.map(t => <button data-testid={`timeframe-${t}-button`} key={t} aria-pressed={timeframe === t} className={timeframe === t ? "selected" : ""} onClick={() => { setTimeframe(t); onTimeframeChange?.(t); }}>{t}</button>)}</div>
      <div data-testid="chart-mode-controls" className="chart-mode-controls">{KINDS.map(({ name, icon: Icon }) => <button key={name} data-testid={`chart-mode-${name.toLowerCase().replace(" ", "-")}-button`} title={name} aria-pressed={kind === name} className={kind === name ? "selected" : ""} onClick={() => setKind(name)}><Icon size={15} /></button>)}</div>
      <div data-testid="indicator-controls" className="indicator-controls">{([["ema", "EMA"], ["bb", "BB"], ["rsi", "RSI"], ["mem", "TRAPS"]] as const).map(([key, label]) => <button key={key} data-testid={`indicator-${key}-button`} aria-pressed={indicators[key]} className={indicators[key] ? "selected" : ""} onClick={() => setIndicators(current => ({ ...current, [key]: !current[key] }))}>{label}</button>)}</div>
    </div>
    <div data-testid="chart-source-footer" className="chart-source-footer"><span>{chartSource === "deriv" ? "DERIV" : "QUOTEX OBSERVER"} · {chartSymbol} · {candles.length} candles</span><span>{timeframe} · {kind}</span></div>
    {priceAction && <div className="chart-analysis-summary" data-testid="chart-analysis-summary" aria-label="Informational chart structure analysis">
      <span>Chart structure</span><b>{priceAction.trend}</b><span>{priceAction.structure}</span>
      <span>RSI {priceAction.rsi === null ? "—" : priceAction.rsi.toFixed(1)}</span>
      <strong className={`bias-${priceAction.bias.toLowerCase()}`}>{priceAction.bias} bias</strong>
      <small>Informational only · not a trade recommendation</small>
    </div>}
  </section>;
}

const LiveChart = memo(LiveChartInner);
export default LiveChart;
