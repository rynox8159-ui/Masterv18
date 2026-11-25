import { ema, rsi } from "@/lib/indicators";

export interface Bar { epoch: number; open: number; high: number; low: number; close: number }
export interface Level { price: number; touches: number; kind: "support" | "resistance" }
export interface TrendLine { kind: "support" | "resistance"; points: { epoch: number; value: number }[] }
export interface ChartEvent {
  epoch: number;
  type: "GAP_UP" | "GAP_DOWN" | "BULL_TRAP" | "BEAR_TRAP" | "BULL_ENGULF" | "BEAR_ENGULF" | "PIN_BULL" | "PIN_BEAR" | "BOS_UP" | "BOS_DOWN";
}
export interface ChartAnalysis {
  levels: Level[];
  trendLines: TrendLine[];
  events: ChartEvent[];
  trend: "UP" | "DOWN" | "RANGE";
  structure: string;
  rsi: number | null;
  atr: number;
  bias: "CALL" | "PUT" | "NEUTRAL";
  biasScore: number;
}

const atrOf = (bars: Bar[], period = 14) => {
  const ranges = bars.slice(1).map((bar, index) => Math.max(
    bar.high - bar.low,
    Math.abs(bar.high - bars[index].close),
    Math.abs(bar.low - bars[index].close),
  ));
  const tail = ranges.slice(-period);
  return tail.length ? tail.reduce((sum, value) => sum + value, 0) / tail.length : 0;
};

function pivots(bars: Bar[], span = 3) {
  const highs: number[] = [];
  const lows: number[] = [];
  for (let index = span; index < bars.length - span; index++) {
    const window = bars.slice(index - span, index + span + 1);
    if (window.every(bar => bar.high <= bars[index].high)) highs.push(index);
    if (window.every(bar => bar.low >= bars[index].low)) lows.push(index);
  }
  return { highs, lows };
}

function clusterLevels(prices: number[], tolerance: number, kind: Level["kind"]): Level[] {
  const sorted = [...prices].sort((a, b) => a - b);
  const groups: number[][] = [];
  sorted.forEach(price => {
    const group = groups.at(-1);
    if (group && price - group[group.length - 1] <= tolerance) group.push(price);
    else groups.push([price]);
  });
  return groups.map(group => ({
    price: group.reduce((sum, price) => sum + price, 0) / group.length,
    touches: group.length,
    kind,
  }));
}

export function analyseChart(bars: Bar[]): ChartAnalysis | null {
  if (bars.length < 30 || bars.some(bar => ![bar.epoch, bar.open, bar.high, bar.low, bar.close].every(Number.isFinite))) return null;
  const atr = atrOf(bars) || Math.abs(bars.at(-1)!.close) * 0.0005;
  const last = bars.at(-1)!;
  const { highs, lows } = pivots(bars);
  const tolerance = atr * 0.6;
  const resistances = clusterLevels(highs.map(index => bars[index].high), tolerance, "resistance")
    .filter(level => level.price > last.close)
    .sort((a, b) => a.price - b.price || b.touches - a.touches)
    .slice(0, 3);
  const supports = clusterLevels(lows.map(index => bars[index].low), tolerance, "support")
    .filter(level => level.price < last.close)
    .sort((a, b) => b.price - a.price || b.touches - a.touches)
    .slice(0, 3);

  const trendLines: TrendLine[] = [];
  const addTrendLine = (indices: number[], kind: TrendLine["kind"], pick: (bar: Bar) => number) => {
    if (indices.length < 2) return;
    const [start, end] = indices.slice(-2);
    const slope = (pick(bars[end]) - pick(bars[start])) / (end - start);
    if ((kind === "support" && slope <= 0) || (kind === "resistance" && slope >= 0)) return;
    const lastIndex = bars.length - 1;
    trendLines.push({
      kind,
      points: [
        { epoch: bars[start].epoch, value: pick(bars[start]) },
        { epoch: bars[lastIndex].epoch, value: pick(bars[start]) + slope * (lastIndex - start) },
      ],
    });
  };
  addTrendLine(lows, "support", bar => bar.low);
  addTrendLine(highs, "resistance", bar => bar.high);

  const events: ChartEvent[] = [];
  const allResistances = clusterLevels(highs.map(index => bars[index].high), tolerance, "resistance");
  const allSupports = clusterLevels(lows.map(index => bars[index].low), tolerance, "support");
  for (let index = Math.max(1, bars.length - 120); index < bars.length; index++) {
    const bar = bars[index];
    const previous = bars[index - 1];
    const body = Math.abs(bar.close - bar.open);
    const range = bar.high - bar.low || 1e-12;
    if (bar.open - previous.close > atr * 0.5) events.push({ epoch: bar.epoch, type: "GAP_UP" });
    else if (previous.close - bar.open > atr * 0.5) events.push({ epoch: bar.epoch, type: "GAP_DOWN" });
    if (allResistances.some(level => bar.high > level.price + atr * 0.1 && bar.close < level.price && level.touches >= 2)) events.push({ epoch: bar.epoch, type: "BULL_TRAP" });
    else if (allSupports.some(level => bar.low < level.price - atr * 0.1 && bar.close > level.price && level.touches >= 2)) events.push({ epoch: bar.epoch, type: "BEAR_TRAP" });
    else if (bar.close > bar.open && previous.close < previous.open && bar.close >= previous.open && bar.open <= previous.close && body > atr * 0.6) events.push({ epoch: bar.epoch, type: "BULL_ENGULF" });
    else if (bar.close < bar.open && previous.close > previous.open && bar.close <= previous.open && bar.open >= previous.close && body > atr * 0.6) events.push({ epoch: bar.epoch, type: "BEAR_ENGULF" });
    else if (body / range < 0.3 && Math.min(bar.open, bar.close) - bar.low > range * 0.6) events.push({ epoch: bar.epoch, type: "PIN_BULL" });
    else if (body / range < 0.3 && bar.high - Math.max(bar.open, bar.close) > range * 0.6) events.push({ epoch: bar.epoch, type: "PIN_BEAR" });
  }
  const lastHigh = highs.at(-1);
  const lastLow = lows.at(-1);
  if (lastHigh !== undefined && last.close > bars[lastHigh].high) events.push({ epoch: last.epoch, type: "BOS_UP" });
  else if (lastLow !== undefined && last.close < bars[lastLow].low) events.push({ epoch: last.epoch, type: "BOS_DOWN" });

  const closes = bars.map(bar => bar.close);
  const ema20 = ema(closes, 20).at(-1) ?? null;
  const ema50 = ema(closes, 50).at(-1) ?? null;
  const currentRsi = rsi(closes, 14).at(-1) ?? null;
  const higherHigh = highs.length >= 2 && bars[highs.at(-1)!].high > bars[highs.at(-2)!].high;
  const higherLow = lows.length >= 2 && bars[lows.at(-1)!].low > bars[lows.at(-2)!].low;
  const structure = higherHigh && higherLow
    ? "HH · HL"
    : !higherHigh && !higherLow && highs.length >= 2 && lows.length >= 2
      ? "LH · LL"
      : "Mixed";
  const trend = ema20 !== null && ema50 !== null
    ? ema20 > ema50 && last.close > ema20 ? "UP" : ema20 < ema50 && last.close < ema20 ? "DOWN" : "RANGE"
    : "RANGE";
  let score = trend === "UP" ? 2 : trend === "DOWN" ? -2 : 0;
  score += structure === "HH · HL" ? 1 : structure === "LH · LL" ? -1 : 0;
  if (currentRsi !== null) score += currentRsi > 55 && currentRsi < 72 ? 1 : currentRsi < 45 && currentRsi > 28 ? -1 : currentRsi >= 72 ? -1 : currentRsi <= 28 ? 1 : 0;
  const recentStart = bars[Math.max(0, bars.length - 4)].epoch;
  events.filter(event => event.epoch >= recentStart).forEach(event => {
    score += /BEAR_TRAP|BULL_ENGULF|PIN_BULL|BOS_UP|GAP_UP/.test(event.type) ? 1
      : /BULL_TRAP|BEAR_ENGULF|PIN_BEAR|BOS_DOWN|GAP_DOWN/.test(event.type) ? -1 : 0;
  });
  return {
    levels: [...resistances, ...supports],
    trendLines,
    events,
    trend,
    structure,
    rsi: currentRsi,
    atr,
    bias: score >= 2 ? "CALL" : score <= -2 ? "PUT" : "NEUTRAL",
    biasScore: score,
  };
}
