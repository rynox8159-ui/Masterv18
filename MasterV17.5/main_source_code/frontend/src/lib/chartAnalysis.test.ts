import { describe, expect, it } from "vitest";
import { analyseChart, type Bar } from "./chartAnalysis";

function risingBars(count: number): Bar[] {
  return Array.from({ length: count }, (_, index) => {
    const close = 100 + index * 0.15 + Math.sin(index / 2) * 0.4;
    const open = close - 0.05;
    return {
      epoch: index * 60,
      open,
      high: Math.max(open, close) + 0.2,
      low: Math.min(open, close) - 0.2,
      close,
    };
  });
}

describe("analyseChart", () => {
  it("waits for sufficient candle history", () => {
    expect(analyseChart(risingBars(29))).toBeNull();
  });

  it("returns finite price-action measurements for a valid candle series", () => {
    const analysis = analyseChart(risingBars(80));

    expect(analysis).not.toBeNull();
    expect(analysis?.atr).toBeGreaterThan(0);
    expect(Number.isFinite(analysis?.rsi)).toBe(true);
    expect(analysis?.bias).toMatch(/^(CALL|PUT|NEUTRAL)$/);
    expect(analysis?.levels.every(level => Number.isFinite(level.price))).toBe(true);
  });

  it("rejects malformed prices instead of returning misleading analysis", () => {
    const bars = risingBars(40);
    bars[10].close = Number.NaN;
    expect(analyseChart(bars)).toBeNull();
  });
});
