export type MarketCategory = "Currencies" | "Crypto" | "Commodities" | "Stocks" | "Indices" | "Synthetics" | "Baskets";
export interface Market {
  id: string; symbol: string; label: string; shortLabel: string; base: number; precision: number;
  category: MarketCategory; session: "Regular" | "OTC"; payout: string; payoutPercent: number;
  change: string; up: boolean; countries: [string, string]; isOTC: boolean; derivSymbol?: string;
  derivMarket?: string; derivSubmarket?: string; sourceSymbol?: string; broker?: "Deriv" | "Quotex";
}
export interface DerivInstrument { latestPrice?: number; symbol: string; label?: string; market?: string; submarket?: string; available?: boolean; isOpen?: boolean; marketDataEligible?: boolean; signalEligible?: boolean; isOTC?: boolean; is_otc?: boolean }
export interface ObservedInstrument { symbol: string; label?: string; available?: boolean; isOpen?: boolean; is_otc?: boolean; payoutPercent?: number; latestPrice?: number }
export type ActiveMarketMode = "FOREX" | "BINARY";

const currencyFlags: Record<string, string> = { EUR: "eu", USD: "us", GBP: "gb", AUD: "au", CAD: "ca", CHF: "ch", JPY: "jp", NZD: "nz", RUB: "ru", BRL: "br", INR: "in", CLP: "cl", ARS: "ar", BDT: "bd", CNY: "cn", DZD: "dz", EGP: "eg", IDR: "id", MXN: "mx", NGN: "ng", PKR: "pk", PHP: "ph", SGD: "sg", THB: "th", TRY: "tr", ZAR: "za", AED: "ae", SAR: "sa", KRW: "kr", NOK: "no", SEK: "se", DKK: "dk", PLN: "pl" };
const forex: [string, number, number][] = [
  ["EUR/USD",1.08428,5],["GBP/USD",1.27145,5],["USD/JPY",153.428,3],["AUD/USD",.65842,5],["USD/CAD",1.3842,5],["USD/CHF",.89361,5],["NZD/USD",.61245,5],
  ["EUR/GBP",.8528,5],["EUR/JPY",166.367,3],["EUR/AUD",1.6468,5],["EUR/CAD",1.501,5],["EUR/CHF",.96892,5],["EUR/NZD",1.7701,5],
  ["GBP/JPY",195.077,3],["GBP/AUD",1.9314,5],["GBP/CAD",1.7596,5],["GBP/CHF",1.1361,5],["GBP/NZD",2.076,5],
  ["AUD/JPY",101.019,3],["AUD/CAD",.90824,5],["AUD/CHF",.55842,5],["AUD/NZD",1.10284,5],["CAD/JPY",110.843,3],["CAD/CHF",.60428,5],["CHF/JPY",171.693,3],["NZD/JPY",93.966,3],["NZD/CAD",.8477,5],["NZD/CHF",.5473,5],
  ["USD/RUB",91.842,3],["USD/BRL",5.7428,4],["USD/INR",86.742,3],["USD/CLP",954.22,3],["USD/ARS",1046.25,2],["USD/BDT",121.68,3],["USD/CNY",7.2458,4],["USD/DZD",134.24,3],["USD/EGP",50.73,3],["USD/IDR",16318,2],["USD/MXN",20.318,4],["USD/NGN",1526.2,2],["USD/PKR",279.5,3],["USD/PHP",58.142,3],["USD/SGD",1.3428,5],["USD/THB",34.64,3],["USD/TRY",36.424,4],["USD/ZAR",18.468,4],["USD/AED",3.6725,4],["USD/SAR",3.7502,4],["USD/KRW",1441.2,2],["USD/NOK",10.915,4],["USD/SEK",10.771,4],["USD/DKK",6.878,4],["USD/PLN",3.852,4],
];
const otherAssets: [string, number, MarketCategory][] = [
  ["Bitcoin",84350,"Crypto"],["Ethereum",2248.5,"Crypto"],["Litecoin",102.4,"Crypto"],["Ripple",2.45,"Crypto"],["Solana",145.82,"Crypto"],["Dogecoin",.2148,"Crypto"],["Cardano",.7142,"Crypto"],["TRON",.2418,"Crypto"],["Bitcoin Cash",344.2,"Crypto"],["Avalanche",24.82,"Crypto"],
  ["Gold",2918.4,"Commodities"],["Silver",32.15,"Commodities"],["UKBrent",53.122,"Commodities"],["USCrude",69.84,"Commodities"],["Natural Gas",3.92,"Commodities"],
  ["Apple",229.72,"Stocks"],["Microsoft",412.84,"Stocks"],["Amazon",214.62,"Stocks"],["Meta",668.1,"Stocks"],["Tesla",282.84,"Stocks"],["NVIDIA",128.45,"Stocks"],["Alphabet",176.25,"Stocks"],["Intel",23.62,"Stocks"],["Boeing",171.46,"Stocks"],["McDonald's",309.22,"Stocks"],["Coca-Cola",71.23,"Stocks"],["American Express",287.4,"Stocks"],["Johnson & Johnson",165.72,"Stocks"],["Pfizer",26.25,"Stocks"],
  ["S&P 500",5948.72,"Indices"],["NASDAQ 100",20975.5,"Indices"],["Dow Jones",43840.9,"Indices"],["DAX",22612.4,"Indices"],["FTSE 100",8738.2,"Indices"],["Composite",1842.4,"Indices"],
];
const slug = (symbol: string) => symbol === "UKBrent" ? "uk-brent" : symbol.toLowerCase().replaceAll("&", "").replaceAll("'", "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const definitions = [
  ...forex.map(([symbol, base, precision]) => ({ symbol, base, precision, category: "Currencies" as MarketCategory })),
  ...otherAssets.map(([symbol, base, category]) => ({ symbol, base, category, precision: symbol === "UKBrent" ? 3 : base < 1 ? 5 : 2 })),
];
// Illustrative catalog, not a verified list of a broker's current asset availability.
export const markets: Market[] = definitions.flatMap((definition, index) => (["Regular", "OTC"] as const).map(session => {
  const [baseCurrency, quoteCurrency] = definition.symbol.split("/");
  const payoutPercent = (session === "OTC" ? 80 : 72) + index % (session === "OTC" ? 14 : 19);
  const ticker = definition.category === "Currencies" ? `frx${definition.symbol.replace("/", "")}` : undefined;
  return { ...definition, id: `${slug(definition.symbol)}${session === "Regular" ? "-regular" : ""}`, label: `${definition.symbol}${session === "OTC" ? " (OTC)" : ""}`, shortLabel: definition.symbol === "UKBrent" ? "UKBrent..." : definition.symbol, session, isOTC: session === "OTC", derivSymbol: session === "Regular" ? ticker : undefined, payoutPercent, payout: `${payoutPercent}%`, change: `${index % 5 === 0 ? "-" : "+"}${(.08 + index % 29 / 100).toFixed(2)}%`, up: index % 5 !== 0, countries: [currencyFlags[baseCurrency] ?? "gb", currencyFlags[quoteCurrency] ?? "us"] as [string, string] };
}));
export const MARKET_BY_ID = Object.fromEntries(markets.map(market => [market.id, market])) as Record<string, Market>;
export const DEFAULT_PAIR_IDS = ["uk-brent", "usd-rub", "usd-jpy", "aud-chf", "usd-cad", "usd-brl", "usd-inr", "aud-cad", "usd-clp", "aud-nzd", "composite", "cad-chf"];
export const marketCategories: MarketCategory[] = ["Currencies", "Crypto", "Commodities", "Stocks", "Indices", "Synthetics", "Baskets"];
export const marketPairFilters = ["All", "Forex", "Crypto", "Commodities", "Stocks", "Indices", "Synthetics", "Baskets", "Binary Options / Volatility"] as const;
export type MarketPairFilter = typeof marketPairFilters[number];
export function isActiveDerivInstrument(instrument: DerivInstrument): boolean {
  return instrument.isOpen === true && instrument.marketDataEligible !== false;
}
export function isRealDerivForexInstrument(instrument: DerivInstrument): boolean {
  return instrument.market === "forex"
    && /^frx[A-Z]{6}$/i.test(instrument.symbol)
    && instrument.marketDataEligible !== false
    && instrument.isOTC !== true
    && instrument.is_otc !== true
    && (instrument.submarket ?? "").toLowerCase() !== "otc"
    && !/(?:^|[^a-z0-9])otc(?:$|[^a-z0-9])/i.test(instrument.label ?? "");
}
export function isActiveObservedInstrument(instrument: ObservedInstrument): boolean {
  return instrument.isOpen === true && instrument.available !== false;
}
export function getMarket(id: string | null | undefined, derivMarkets: readonly Market[] = []): Market { return MARKET_BY_ID[id ?? ""] ?? derivMarkets.find(market => market.id === id) ?? MARKET_BY_ID["uk-brent"]; }

const CRYPTO_PAIR_LABELS: Record<string, string> = {
  Bitcoin: "BTC/USD", Ethereum: "ETH/USD", Litecoin: "LTC/USD", Ripple: "XRP/USD",
  Solana: "SOL/USD", Dogecoin: "DOGE/USD", Cardano: "ADA/USD", TRON: "TRX/USD",
  "Bitcoin Cash": "BCH/USD", Avalanche: "AVAX/USD",
};

function pairFromProviderSymbol(symbol: string, prefix: "frx" | "cry"): string | undefined {
  const ticker = symbol.replace(new RegExp(`^${prefix}`, "i"), "").toUpperCase();
  const quote = ["USDT", "USDC", "USD", "BTC", "ETH", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF"].find(
    candidate => ticker.endsWith(candidate) && ticker.length > candidate.length,
  );
  if (!quote) return undefined;
  return `${ticker.slice(0, -quote.length)}/${quote}`;
}

function syntheticIndexLabel(market: Pick<Market, "derivSymbol" | "label" | "symbol">): string | undefined {
  const key = market.derivSymbol ?? market.symbol;
  const volatility = key.match(/^(?:R_(\d+)|1HZ(\d+)V)$/i);
  if (volatility) {
    const level = volatility[1] ?? volatility[2];
    return `Volatility ${level}${/^1HZ/i.test(key) ? " (1s)" : ""} Index`;
  }
  const namedIndex = key.match(/^(BOOM|CRASH)(\d+)$/i);
  if (namedIndex) return `${namedIndex[1][0]}${namedIndex[1].slice(1).toLowerCase()} ${namedIndex[2]} Index`;
  return market.label
    .replace(/_/g, " ")
    .replace(/\s*\(\s*1s\s*\)/gi, " (1s)")
    .replace(/\bvolatility\s+(\d+)\b/i, "Volatility $1")
    .trim();
}

/** Presentation-only pair name; market.symbol and derivSymbol remain provider identities. */
export function marketDisplayName(market: Pick<Market, "category" | "derivMarket" | "derivSymbol" | "label" | "symbol">): string {
  if (market.derivSymbol?.startsWith("frx")) {
    if (market.label.trim() && market.label.toLowerCase() !== market.derivSymbol.toLowerCase()) {
      return market.label;
    }
    return pairFromProviderSymbol(market.derivSymbol, "frx") ?? market.label;
  }
  if (market.derivSymbol?.startsWith("cry")) {
    return pairFromProviderSymbol(market.derivSymbol, "cry") ?? market.label;
  }
  if (market.category === "Crypto") return CRYPTO_PAIR_LABELS[market.symbol] ?? market.label;
  if (market.derivMarket === "synthetic_index") {
    if (market.label.trim() && market.label.toLowerCase() !== market.derivSymbol?.toLowerCase()) {
      return market.label;
    }
    return syntheticIndexLabel(market) ?? market.label;
  }
  return market.label;
}

export function marketMatchesPairFilter(market: Market, filter: MarketPairFilter): boolean {
  if (filter === "All") return true;
  if (filter === "Forex") return market.category === "Currencies";
  if (filter === "Binary Options / Volatility" || filter === "Synthetics") return market.category === "Synthetics" || market.derivMarket === "synthetic_index";
  if (filter === "Crypto" || filter === "Commodities" || filter === "Stocks" || filter === "Indices" || filter === "Baskets") {
    return market.category === filter;
  }
  return false;
}

const precisionFor = (price?: number) => price === undefined ? 5 : price >= 1000 ? 2 : price >= 100 ? 3 : price >= 10 ? 4 : 5;
export function marketFromDerivInstrument(instrument: DerivInstrument): Market {
  const symbol = instrument.symbol;
  const existing = markets.find(market => market.derivSymbol?.toUpperCase() === symbol.toUpperCase());
  const derivMarket = instrument.market ?? "";
  const label = instrument.label || existing?.label || symbol;
  const assetClass = `${derivMarket} ${instrument.submarket ?? ""}`.toLowerCase();
  const category: MarketCategory = derivMarket === "forex" ? "Currencies"
    : assetClass.includes("crypto") ? "Crypto"
      : assetClass.includes("commodit") ? "Commodities"
        : assetClass.includes("synthetic") ? "Synthetics"
          : assetClass.includes("basket") ? "Baskets"
            : assetClass.includes("stock") ? "Stocks"
              : "Indices";
  const currencyCodes = symbol.replace(/^frx/i, "").match(/^[A-Z]{3}[A-Z]{3}$/i)?.[0].toUpperCase();
  return {
    ...(existing ?? {
      id: slug(symbol), symbol, label, shortLabel: label,
      base: instrument.latestPrice ?? 1, precision: precisionFor(instrument.latestPrice), category, session: "Regular" as const, payout: "—", payoutPercent: 0,
      change: "0.00%", up: true, countries: [currencyCodes ? currencyFlags[currencyCodes.slice(0, 3)] ?? "gb" : "gb", currencyCodes ? currencyFlags[currencyCodes.slice(3)] ?? "us" : "us"] as [string, string], isOTC: false,
    }),
    label,
    shortLabel: label,
    derivSymbol: symbol,
    derivMarket,
    derivSubmarket: instrument.submarket ?? "",
  };
}
export function marketFromObservedOtcInstrument(instrument: ObservedInstrument): Market | undefined {
  if (instrument.is_otc !== true || !isActiveObservedInstrument(instrument)) return undefined;
  const label = (instrument.label || instrument.symbol).trim();
  if ([instrument.symbol, label].some(value => /(?:^|[^a-z0-9])(?:testonly|test|demo|mock)(?:[^a-z0-9]|$)/i.test(value))) return undefined;
  const baseSymbol = label.replace(/(?:[\s_-]*\(?OTC\)?)\s*$/i, "").trim();
  const normalized = baseSymbol.toLowerCase().replace(/[^a-z0-9]/g, "");
  const existing = markets.find(market => market.session === "OTC"
    && market.symbol.toLowerCase().replace(/[^a-z0-9]/g, "") === normalized);
  const assetKey = (baseSymbol.match(/^[A-Z0-9]+/i)?.[0] ?? "").toUpperCase();
  const otcCryptoSymbols = new Set(["BTC", "ETH", "LTC", "XRP", "AVAX", "DOT", "SOL", "ZEC", "ATOM", "BNB", "ETC", "TON", "AXS", "LINK", "DASH", "BCH"]);
  const otcCommoditySymbols = new Set(["GOLD", "UKBRENT", "USCRUDE", "SILVER"]);
  const currencyCodes = baseSymbol.replace(/[^a-z]/gi, "").toUpperCase().match(/^[A-Z]{6}$/)?.[0];
  const category = existing?.category
    ?? (otcCryptoSymbols.has(assetKey) ? "Crypto" : otcCommoditySymbols.has(assetKey) ? "Commodities" : currencyCodes ? "Currencies" : "Indices");
  const symbolLabel = baseSymbol || instrument.symbol;
  const fallback: Market = {
    id: `${slug(symbolLabel)}-otc-observed`,
    symbol: symbolLabel,
    label: `${symbolLabel} (OTC)`,
    shortLabel: symbolLabel,
    base: 1,
    precision: 5,
    category,
    session: "OTC",
    payout: "—",
    payoutPercent: 0,
    change: "0.00%",
    up: true,
    countries: [currencyCodes ? currencyFlags[currencyCodes.slice(0, 3)] ?? "gb" : "gb", currencyCodes ? currencyFlags[currencyCodes.slice(3)] ?? "us" : "us"],
    isOTC: true,
  };
  const payoutPercent = instrument.payoutPercent ?? 0;
  return {
    ...(existing ?? fallback),
    id: `${slug(instrument.symbol)}-qx`,
    label: /\bOTC\b|[_-]OTC$/i.test(label) ? label : `${label} (OTC)`,
    isOTC: true,
    session: "OTC",
    sourceSymbol: instrument.symbol,
    broker: "Quotex",
    precision: instrument.latestPrice !== undefined ? precisionFor(instrument.latestPrice) : (existing ?? fallback).precision,
    payoutPercent,
    payout: payoutPercent ? `${Math.round(payoutPercent)}%` : "—",
  };
}
export function marketsForMode(mode: ActiveMarketMode, derivMarkets: readonly Market[] = []): Market[] {
  const categories: Record<ActiveMarketMode, MarketCategory[] | null> = {
    FOREX: ["Currencies"],
    BINARY: ["Currencies"],
  };
  return derivMarkets
    .filter(market => (categories[mode] === null || categories[mode]?.includes(market.category)) && !market.isOTC && market.broker !== "Quotex")
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function isBinarySignalTimeframe(timeframe: string): boolean {
  return timeframe === "1m" || timeframe === "5m";
}

export function signalMatchesMarketMode(
  signal: { label?: string; symbol?: string; timeframe?: string },
  mode: ActiveMarketMode,
  modeMarkets: readonly Market[],
): boolean {
  if (mode === "BINARY" && !isBinarySignalTimeframe(signal.timeframe ?? "")) return false;
  const market = marketForSignal(signal, modeMarkets);
  return market !== undefined && modeMarkets.some(candidate => candidate.id === market.id);
}
export function marketForSignal(signal: { label?: string; symbol?: string }, derivMarkets: readonly Market[] = []): Market | undefined {
  const identifiers = [signal.label, signal.symbol].filter((value): value is string => Boolean(value)).map(value => value.toLowerCase());
  return [...derivMarkets, ...markets].find(market =>
    [market.id, market.label, market.symbol, market.derivSymbol]
      .some(value => value && identifiers.includes(value.toLowerCase())),
  );
}


export function marketFromObservedRealInstrument(instrument: ObservedInstrument & { mappingStatus?: string }): Market | undefined {
  if (instrument.is_otc === true || instrument.mappingStatus === "MAPPED") return undefined;
  const base = marketFromObservedOtcInstrument({ ...instrument, is_otc: true });
  if (!base) return undefined;
  const label = (instrument.label || instrument.symbol).trim();
  return { ...base, id: `${slug(instrument.symbol)}-qx-real`, label, shortLabel: label, symbol: label, isOTC: false, session: "Regular" };
}
