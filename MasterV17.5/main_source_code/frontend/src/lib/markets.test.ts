import { describe, expect, it } from "vitest";
import { isActiveDerivInstrument, isActiveObservedInstrument, isBinarySignalTimeframe, isRealDerivForexInstrument, marketDisplayName, marketForSignal, marketFromDerivInstrument, marketFromObservedOtcInstrument, marketMatchesPairFilter, marketPairFilters, marketsForMode, markets, signalMatchesMarketMode } from "./markets";

describe("Deriv market labels", () => {
  it("preserves the complete broker label for volatility indices", () => {
    const standard = marketFromDerivInstrument({
      symbol: "R_10",
      label: "Volatility 10 Index",
      market: "synthetic_index",
    });
    const oneSecond = marketFromDerivInstrument({
      symbol: "1HZ100V",
      label: "Volatility 100 (1s) Index",
      market: "synthetic_index",
    });

    expect(standard.label).toBe("Volatility 10 Index");
    expect(standard.shortLabel).toBe("Volatility 10 Index");
    expect(oneSecond.label).toBe("Volatility 100 (1s) Index");
    expect(oneSecond.shortLabel).toBe("Volatility 100 (1s) Index");
  });

  it("preserves regular forex labels", () => {
    const market = marketFromDerivInstrument({
      symbol: "frxEURUSD",
      label: "EUR/USD",
      market: "forex",
    });

    expect(market.shortLabel).toBe("EUR/USD");
    expect(marketDisplayName(market)).toBe("EUR/USD");
  });

  it("uses the broker-provided Forex and binary instrument names for display", () => {
    const forex = marketFromDerivInstrument({
      symbol: "frxEURUSD",
      label: "EUR/USD",
      market: "forex",
    });
    const binary = marketFromDerivInstrument({
      symbol: "1HZ100V",
      label: "Volatility 100 (1s) Index",
      market: "synthetic_index",
    });

    expect(marketDisplayName(forex)).toBe("EUR/USD");
    expect(marketDisplayName(binary)).toBe("Volatility 100 (1s) Index");
    expect(binary.shortLabel).toBe("Volatility 100 (1s) Index");
  });

  it("formats Deriv keys as presentation labels without changing provider symbols", () => {
    const forex = marketFromDerivInstrument({ symbol: "frxGBPJPY", market: "forex", isOpen: true });
    const crypto = marketFromDerivInstrument({ symbol: "cryBTCUSD", market: "cryptocurrency" });
    const synthetic = marketFromDerivInstrument({ symbol: "R_10", market: "synthetic_index" });
    const oneSecond = marketFromDerivInstrument({ symbol: "1HZ10V", market: "synthetic_index" });

    expect(marketDisplayName(forex)).toBe("GBP/JPY");
    expect(marketDisplayName(crypto)).toBe("BTC/USD");
    expect(marketDisplayName(synthetic)).toBe("Volatility 10 Index");
    expect(marketDisplayName(oneSecond)).toBe("Volatility 10 (1s) Index");
    expect(forex.symbol).toBe("GBP/JPY");
    expect(forex.derivSymbol).toBe("frxGBPJPY");
    expect(crypto.symbol).toBe("cryBTCUSD");
    expect(synthetic.derivSymbol).toBe("R_10");
  });

  it("shows conventional crypto pairs for static catalog markets", () => {
    expect(marketDisplayName(markets.find(market => market.symbol === "Bitcoin")!)).toBe("BTC/USD");
    expect(marketDisplayName(markets.find(market => market.symbol === "Solana")!)).toBe("SOL/USD");
  });

  it("exposes the requested functional market filter groups", () => {
    expect(marketPairFilters).toEqual([
      "All",
      "Forex",
      "Crypto",
      "Commodities",
      "Stocks",
      "Indices",
      "Synthetics",
      "Baskets",
      "Binary Options / Volatility",
    ]);
    const forex = marketFromDerivInstrument({ symbol: "frxEURUSD", market: "forex", isOpen: true });
    const crypto = marketFromDerivInstrument({ symbol: "cryBTCUSD", market: "cryptocurrency" });
    const volatility = marketFromDerivInstrument({ symbol: "1HZ10V", market: "synthetic_index" });
    const commodities = marketFromObservedOtcInstrument({
      symbol: "GOLD_OTC",
      label: "Gold (OTC)",
      is_otc: true,
      isOpen: true,
    });

    expect(marketMatchesPairFilter(forex, "Forex")).toBe(true);
    expect(marketMatchesPairFilter(crypto, "Crypto")).toBe(true);
    expect(marketMatchesPairFilter(volatility, "Binary Options / Volatility")).toBe(true);
    expect(marketMatchesPairFilter(commodities!, "Commodities")).toBe(true);
    expect(marketMatchesPairFilter(forex, "Binary Options / Volatility")).toBe(false);
  });

  it("renders only instruments explicitly marked open by the backend", () => {
    expect(isActiveDerivInstrument({ symbol: "frxEURUSD", isOpen: true })).toBe(true);
    expect(isActiveDerivInstrument({ symbol: "frxGBPUSD", isOpen: false })).toBe(false);
    expect(isActiveDerivInstrument({ symbol: "1HZ10V" })).toBe(false);
    expect(isActiveDerivInstrument({ symbol: "cryBTCUSD", isOpen: true, marketDataEligible: false })).toBe(false);
    expect(isActiveObservedInstrument({ symbol: "EUR/USD (OTC)", isOpen: true })).toBe(true);
    expect(isActiveObservedInstrument({ symbol: "EUR/USD (OTC)", isOpen: false })).toBe(false);
  });

  it("counts real provider-listed Forex pairs regardless of open status", () => {
    expect(isRealDerivForexInstrument({ symbol: "frxEURUSD", market: "forex", isOpen: false })).toBe(true);
    expect(isRealDerivForexInstrument({ symbol: "frxGBPUSD", market: "forex", isOpen: true, isOTC: true })).toBe(false);
    expect(isRealDerivForexInstrument({ symbol: "R_100", market: "synthetic_index", isOpen: true })).toBe(false);
    expect(isRealDerivForexInstrument({ symbol: "frxEURUSD", market: "forex", marketDataEligible: false })).toBe(false);
  });

  it("limits both signal modes to real Deriv Forex pairs and excludes observed OTC markets", () => {
    const forex = marketFromDerivInstrument({
      symbol: "frxEURUSD",
      label: "EUR/USD",
      market: "forex",
    });
    const crypto = marketFromDerivInstrument({
      symbol: "cryBTCUSD",
      label: "BTC/USD",
      market: "cryptocurrency",
    });
    const index = marketFromDerivInstrument({
      symbol: "US500",
      label: "US 500",
      market: "indices",
    });
    const synthetic = marketFromDerivInstrument({
      symbol: "R_100",
      label: "Volatility 100 Index",
      market: "synthetic_index",
    });
    const commodity = marketFromDerivInstrument({
      symbol: "frxXAUUSD",
      label: "Gold/USD",
      market: "commodities",
    });
    const stock = marketFromDerivInstrument({
      symbol: "AAPL",
      label: "Apple",
      market: "stock",
    });
    const basket = marketFromDerivInstrument({
      symbol: "WLDA",
      label: "Derived basket",
      market: "basket_index",
    });
    const otc = marketFromObservedOtcInstrument({
      symbol: "EUR/USD (OTC)",
      label: "EUR/USD (OTC)",
      is_otc: true,
      isOpen: true,
    });

    expect(otc).toBeDefined();
    expect(marketsForMode("FOREX", [index, crypto, otc!, forex, synthetic, commodity, stock, basket]))
      .toEqual([forex]);
  });

  it("limits Binary mode to real Deriv Forex pairs", () => {
    const forex = marketFromDerivInstrument({
      symbol: "frxEURUSD",
      label: "EUR/USD",
      market: "forex",
    });
    const crypto = marketFromObservedOtcInstrument({
      symbol: "BTC_OTC",
      label: "BTC/USD (OTC)",
      is_otc: true,
      isOpen: true,
    });
    const commodity = marketFromObservedOtcInstrument({
      symbol: "GOLD_OTC",
      label: "Gold (OTC)",
      is_otc: true,
      isOpen: true,
    });
    const tickerOnly = marketFromObservedOtcInstrument({
      symbol: "BNB_OTC",
      is_otc: true,
      isOpen: true,
    });
    const otcForex = marketFromObservedOtcInstrument({
      symbol: "EUR/USD (OTC)",
      label: "EUR/USD (OTC)",
      is_otc: true,
      isOpen: true,
    });
    const otcIndex = marketFromObservedOtcInstrument({
      symbol: "US500_OTC",
      label: "US 500 (OTC)",
      is_otc: true,
      isOpen: true,
    });

    expect(crypto?.category).toBe("Crypto");
    expect(commodity?.category).toBe("Commodities");
    expect(tickerOnly?.category).toBe("Crypto");
    expect(tickerOnly?.label).toBe("BNB_OTC");
    expect(marketsForMode("BINARY", [forex, crypto!, commodity!, tickerOnly!, otcForex!, otcIndex!]))
      .toEqual([forex]);
  });

  it("excludes synthetic and observed OTC instruments from Binary-mode Forex signals", () => {
    const forex = marketFromDerivInstrument({
      symbol: "frxEURUSD",
      label: "EUR/USD",
      market: "forex",
    });
    const crypto = marketFromDerivInstrument({
      symbol: "cryBTCUSD",
      label: "BTC/USD",
      market: "cryptocurrency",
    });
    const synthetic = marketFromDerivInstrument({
      symbol: "1HZ100V",
      label: "Volatility 100 (1s) Index",
      market: "synthetic_index",
    });
    const observed = marketFromObservedOtcInstrument({
      symbol: "EUR/USD (OTC)",
      label: "EUR/USD (OTC)",
      is_otc: true,
      isOpen: true,
    });

    expect(marketsForMode("BINARY", [forex, crypto, synthetic, observed!])).toEqual([forex]);
  });

  it("allows 1- and 5-minute Binary signals on real Deriv Forex pairs only", () => {
    const forex = marketFromDerivInstrument({
      symbol: "frxEURUSD",
      label: "EUR/USD",
      market: "forex",
    });
    const synthetic = marketFromDerivInstrument({
      symbol: "R_100",
      label: "Volatility 100 Index",
      market: "synthetic_index",
    });
    const binaryMarkets = marketsForMode("BINARY", [forex, synthetic]);

    expect(signalMatchesMarketMode({ symbol: "frxEURUSD", timeframe: "1m" }, "BINARY", binaryMarkets)).toBe(true);
    expect(signalMatchesMarketMode({ symbol: "frxEURUSD", timeframe: "5m" }, "BINARY", binaryMarkets)).toBe(true);
    expect(signalMatchesMarketMode({ symbol: "frxEURUSD", timeframe: "15m" }, "BINARY", binaryMarkets)).toBe(false);
    expect(signalMatchesMarketMode({ symbol: "R_100", timeframe: "1m" }, "BINARY", binaryMarkets)).toBe(false);
    expect(isBinarySignalTimeframe("1m")).toBe(true);
    expect(isBinarySignalTimeframe("5m")).toBe(true);
    expect(isBinarySignalTimeframe("15m")).toBe(false);
  });

  it("resolves identical Forex labels to live Deriv instruments before the illustrative catalog", () => {
    const liveForex = marketFromDerivInstrument({
      symbol: "frxEURUSD",
      label: "EUR/USD",
      market: "forex",
    });

    expect(marketForSignal({ label: "EUR/USD", symbol: "frxEURUSD" }, [liveForex])).toEqual(liveForex);
    expect(signalMatchesMarketMode({ label: "EUR/USD", symbol: "frxEURUSD", timeframe: "1m" }, "BINARY", [liveForex])).toBe(true);
  });

  it("does not surface test or demo OTC observations as live pairs", () => {
    expect(marketFromObservedOtcInstrument({
      symbol: "TESTONLY_EUR/USD (OTC)",
      label: "TESTONLY_EUR/USD (OTC)",
      is_otc: true,
      isOpen: true,
    })).toBeUndefined();
  });
});
