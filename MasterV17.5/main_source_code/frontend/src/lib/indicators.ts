import type { LiveCandle } from "@/hooks/useMarketBackend";

export function ema(values: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  let prev: number | null = null;
  return values.map((value, i) => {
    if (i < period - 1) return null;
    if (prev === null) prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
    else prev = value * k + prev * (1 - k);
    return prev;
  });
}

export function bollinger(values: number[], period: number, width: number) {
  const mid: (number | null)[] = [], upper: (number | null)[] = [], lower: (number | null)[] = [];
  values.forEach((_, i) => {
    if (i < period - 1) { mid.push(null); upper.push(null); lower.push(null); return; }
    const slice = values.slice(i - period + 1, i + 1);
    const mean = slice.reduce((a, b) => a + b, 0) / period;
    const sd = Math.sqrt(slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period);
    mid.push(mean); upper.push(mean + width * sd); lower.push(mean - width * sd);
  });
  return { mid, upper, lower };
}

export function rsi(values: number[], period: number): (number | null)[] {
  let gain = 0, loss = 0;
  return values.map((value, i) => {
    if (i === 0) return null;
    const delta = value - values[i - 1];
    if (i <= period) {
      gain += Math.max(0, delta); loss += Math.max(0, -delta);
      if (i < period) return null;
      gain /= period; loss /= period;
    } else {
      gain = (gain * (period - 1) + Math.max(0, delta)) / period;
      loss = (loss * (period - 1) + Math.max(0, -delta)) / period;
    }
    return loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  });
}

export function heikinAshi(candles: LiveCandle[]): LiveCandle[] {
  const out: LiveCandle[] = [];
  candles.forEach((c, i) => {
    const close = (c.open + c.high + c.low + c.close) / 4;
    const open = i === 0 ? (c.open + c.close) / 2 : (out[i - 1].open + out[i - 1].close) / 2;
    out.push({ epoch: c.epoch, open, close, high: Math.max(c.high, open, close), low: Math.min(c.low, open, close) });
  });
  return out;
}
