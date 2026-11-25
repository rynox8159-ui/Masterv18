import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, Plus, Search, Star, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { DEFAULT_PAIR_IDS, getMarket, marketDisplayName, marketFromDerivInstrument, marketsForMode, type ActiveMarketMode, type DerivInstrument, type Market } from "@/lib/markets";
import CountryFlags from "./CountryFlags";
import "./v171.css";

const tabName = (pair: Market) => marketDisplayName(pair).replace(/^Volatility\s+(\d+)\s*(\(1s\))?\s*Index$/i, (_m, n, s) => `Vol ${n}${s ? " (1s)" : ""}`).replace(/\s+Index$/i, "");
const WATCHLIST_KEY = "master-candle.watchlist";
const FAVORITES_KEY = "master-candle.favorites";
const readList = (key: string, fallback: readonly string[]) => { try { const raw = localStorage.getItem(key); const parsed = raw ? JSON.parse(raw) as unknown : null; return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [...fallback]; } catch { return [...fallback]; } };
const writeList = (key: string, value: readonly string[]) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable */ } };

// Deriv-style top-level groups (same order as the Deriv trader market picker)
const DERIV_GROUPS = ["All", "Derived", "Forex", "Stocks & indices", "Commodities", "Crypto"] as const;
type DerivGroup = typeof DERIV_GROUPS[number];
type AssetGroup = Exclude<DerivGroup, "All">;
const groupOf = (pair: Market): AssetGroup => pair.category === "Synthetics" || pair.category === "Baskets" ? "Derived" : pair.category === "Currencies" ? "Forex" : pair.category === "Crypto" ? "Crypto" : pair.category === "Commodities" ? "Commodities" : "Stocks & indices";
const submarketOf = (pair: Market) => {
  const sub = (pair.derivSubmarket ?? "").replace(/_/g, " ").trim();
  if (sub) return sub.replace(/\b\w/g, c => c.toUpperCase());
  const s = pair.derivSymbol ?? pair.symbol;
  if (/^(R_|1HZ)/.test(s)) return "Volatility Indices";
  if (/^(BOOM|CRASH)/.test(s)) return "Crash/Boom Indices";
  if (/^JD/.test(s)) return "Jump Indices";
  if (/^stp/.test(s)) return "Step Indices";
  if (/^(RDB|RB)/.test(s)) return "Daily Reset Indices";
  if (/^WLD/.test(s)) return "Baskets";
  return pair.category;
};
const closedGroupOf = (i: DerivInstrument): AssetGroup => { const cls = `${i.market ?? ""} ${i.submarket ?? ""}`.toLowerCase(); return i.market === "forex" ? "Forex" : cls.includes("crypto") ? "Crypto" : cls.includes("commodit") ? "Commodities" : cls.includes("synthetic") || cls.includes("basket") ? "Derived" : "Stocks & indices"; };
const fmt = (value: number | undefined, precision: number) => value === undefined || !Number.isFinite(value) ? "—" : value.toFixed(Math.min(precision, 5));

type Props = { activePairId: string; onSelect: (id: string) => void; online?: boolean; live?: boolean; marketMode?: ActiveMarketMode; derivMarkets?: readonly Market[]; pairOptions?: readonly Market[]; emptyText?: string; livePrices?: readonly DerivInstrument[]; showClosedTabs?: boolean };

export default function MarketRail({ activePairId, onSelect, online = true, marketMode, derivMarkets = [], pairOptions, emptyText, livePrices = [], showClosedTabs = false }: Props) {
  const rail = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState<DerivGroup | "Favourites">("All");
  const [watchlist, setWatchlist] = useState<string[]>(() => readList(WATCHLIST_KEY, DEFAULT_PAIR_IDS));
  const [favorites, setFavorites] = useState<string[]>(() => readList(FAVORITES_KEY, []));
  const [hiddenPairIds, setHiddenPairIds] = useState<Set<string>>(() => new Set());
  const prevPrice = useRef<Record<string, number>>({});
  const [, forceTick] = useState(0);
  useEffect(() => { writeList(WATCHLIST_KEY, watchlist); }, [watchlist]);
  useEffect(() => { writeList(FAVORITES_KEY, favorites); }, [favorites]);
  useEffect(() => { setQuery(""); setGroup(marketMode === "BINARY" ? "Forex" : "All"); }, [marketMode]);
  const toggleFavorite = (id: string) => setFavorites(current => current.includes(id) ? current.filter(v => v !== id) : [id, ...current]);

  const marketChoices = pairOptions ?? (marketMode ? marketsForMode(marketMode, derivMarkets) : derivMarkets);
  const marketChoiceIds = new Set(marketChoices.map(market => market.id));
  const closedMarkets = useMemo(() => pairOptions ? [] : livePrices.filter(i =>
    i.isOpen === false
    && !marketChoices.some(m => (m.derivSymbol ?? m.symbol).toUpperCase() === i.symbol.toUpperCase())
    && (marketMode !== "BINARY" || closedGroupOf(i) === "Forex")
    && (marketMode !== "FOREX" || closedGroupOf(i) === "Forex")
  ), [livePrices, marketChoices, marketMode, pairOptions]);
  const closedTabMarkets = useMemo(
    () => showClosedTabs ? closedMarkets.map(marketFromDerivInstrument) : [],
    [closedMarkets, showClosedTabs],
  );
  const tabMarkets = [...marketChoices, ...closedTabMarkets.filter(market => !marketChoiceIds.has(market.id))];
  const tabMarketIds = new Set(tabMarkets.map(market => market.id));
  const modeWatchlist = [...new Set([...(marketMode ? tabMarkets.map(m => m.id) : []), ...watchlist])].filter(id => !hiddenPairIds.has(id));
  const ids = (modeWatchlist.includes(activePairId) ? modeWatchlist : [activePairId, ...modeWatchlist]).filter(id => tabMarketIds.has(id));
  const priceBySymbol = useMemo(() => Object.fromEntries(livePrices.map(i => [i.symbol.toUpperCase(), i])), [livePrices]);
  const priceOf = (pair: Market) => priceBySymbol[(pair.derivSymbol ?? pair.symbol).toUpperCase()]?.latestPrice;
  const isClosed = (pair: Market) => !marketChoiceIds.has(pair.id);
  // direction colour: compare with the previously rendered price for this pair
  const dirOf = (pair: Market) => { const p = priceOf(pair); if (p === undefined) return "flat"; const prev = prevPrice.current[pair.id]; return prev === undefined || prev === p ? "flat" : p > prev ? "up" : "down"; };
  useEffect(() => { const id = window.setInterval(() => { ids.forEach(pid => { const p = priceOf(getMarket(pid, tabMarkets)); if (p !== undefined) prevPrice.current[pid] = p; }); forceTick(v => v + 1); }, 2500); return () => window.clearInterval(id); });
  useEffect(() => { const el = rail.current?.querySelector<HTMLElement>(`[data-testid="pair-tab-${activePairId}"]`); el?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" }); }, [activePairId]);

  const select = (id: string) => {
    setHiddenPairIds(current => { if (!current.has(id)) return current; const next = new Set(current); next.delete(id); return next; });
    if (!watchlist.includes(id)) setWatchlist(current => [id, ...current].slice(0, 40));
    onSelect(id); setOpen(false); setQuery("");
  };
  const closePair = (id: string) => {
    if (ids.length <= 1) return;
    const remaining = ids.filter(pairId => pairId !== id);
    setHiddenPairIds(current => new Set(current).add(id));
    setWatchlist(current => current.filter(pairId => pairId !== id));
    if (activePairId === id && remaining[0]) onSelect(remaining[0]);
  };
  const q = query.trim().toLowerCase();
  const matches = (pair: Market) => !q || `${marketDisplayName(pair)} ${pair.label} ${pair.derivSymbol ?? ""} ${submarketOf(pair)}`.toLowerCase().includes(q);
  const groupCounts = Object.fromEntries(DERIV_GROUPS.map(g => [g, g === "All" ? marketChoices.length : marketChoices.filter(m => groupOf(m) === g).length])) as Record<DerivGroup, number>;
  const visible = marketChoices.filter(m => matches(m) && (q ? true : group === "Favourites" ? favorites.includes(m.id) : group === "All" || groupOf(m) === group));
  const sections = useMemo(() => { const map = new Map<string, Market[]>(); visible.forEach(m => { const key = submarketOf(m); map.set(key, [...(map.get(key) ?? []), m]); }); return [...map.entries()]; }, [visible]);
  const visibleClosedMarkets = group === "All" ? closedMarkets : closedMarkets.filter(i => group !== "Favourites" && closedGroupOf(i) === group);

  return <>
    <div className={`instrument-rail-wrap mc-rail-wrap ${online ? "pairs-tracking" : "pairs-paused"}`} data-testid="market-rail-wrapper">
      <Button data-testid="add-pair-button" aria-label="Select market pair" className="instrument-add mc-add" onClick={() => setOpen(true)}><Plus size={22} /></Button>
      <div ref={rail} data-testid="pair-selector-bar" className="mc-rail">
        {ids.map(id => { const pair = getMarket(id, tabMarkets); const price = priceOf(pair); const dir = dirOf(pair); const active = activePairId === id; const closed = isClosed(pair); return <div key={id} className={`mc-tab-wrap ${active ? "active" : ""}`}>
          <button data-testid={`pair-tab-${id}`} aria-pressed={active} disabled={closed} title={`${marketDisplayName(pair)} · ${pair.broker ?? "Deriv"}${closed ? " · Market closed" : ""}`} className={`mc-tab ${active ? "mc-tab-active" : ""} ${closed ? "mc-tab-closed" : ""} dir-${dir}`} onClick={() => onSelect(id)}>
            <span data-testid={`pair-icon-${id}`} className="mc-tab-logo"><CountryFlags pairId={id} markets={tabMarkets} /></span>
            <span data-testid={`pair-copy-${id}`} className="mc-tab-copy"><span data-testid={`pair-name-${id}`} className="mc-tab-name">{tabName(pair)}</span><span data-testid={`pair-price-${id}`} className={`mc-tab-price dir-${dir}`}>{closed ? "CLOSED" : fmt(price, pair.precision)}</span></span>
            <span data-testid={`pair-tracking-${id}`} className={`mc-tab-dot ${closed ? "closed" : online ? "live" : ""}`} aria-label={closed ? "Market closed" : online ? "Live market stream" : "Display paused"} />
          </button>
          {!closed && <button type="button" data-testid={`close-pair-tab-${id}`} className="mc-tab-close" aria-label={`Close ${marketDisplayName(pair)} tab`} onClick={() => closePair(id)}><X size={11} /></button>}
        </div>; })}
        {ids.length === 0 && <span data-testid="market-mode-empty-state" className="market-mode-empty-state">{emptyText ?? "Waiting for live markets…"}</span>}
      </div>
      <button data-testid="pair-scroll-next-button" aria-label="More instruments" className="mc-rail-next" onClick={() => rail.current?.scrollBy({ left: 320, behavior: "smooth" })}><ChevronRight size={17} /></button>
    </div>
    {open && <Dialog open onOpenChange={setOpen}><DialogContent data-testid="pair-picker-dialog" showCloseButton={false} className="pair-picker-dialog mc-picker">
      <DialogHeader className="mc-picker-head"><DialogTitle data-testid="pair-picker-title">DERIV markets</DialogTitle><DialogDescription data-testid="pair-picker-description">{pairOptions ? `${marketChoices.length} provider-confirmed ACCU markets` : `${marketChoices.length} open instruments · ${closedMarkets.length} closed pairs shown separately`}</DialogDescription></DialogHeader>
      <Button data-testid="pair-picker-close-button" className="pair-picker-close" variant="ghost" size="icon-sm" aria-label="Close market selector" onClick={() => setOpen(false)}><X size={16} /></Button>
      <div className="mc-picker-search"><Search size={15} /><Input data-testid="pair-search-input" aria-label="Search market pairs" placeholder="Search by market name" value={query} onChange={event => setQuery(event.target.value)} /></div>
      <div className="mc-picker-groups" data-testid="catalog-category-tabs">
        {DERIV_GROUPS.map(value => <button key={value} data-testid={`catalog-category-${value.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`} aria-pressed={!q && group === value} onClick={() => { setGroup(value); setQuery(""); }}>{value}<small>{groupCounts[value] || (value !== "All" && closedMarkets.some(i => closedGroupOf(i) === value) ? "closed" : 0)}</small></button>)}
        <button data-testid="catalog-category-favorites" aria-pressed={!q && group === "Favourites"} onClick={() => { setGroup("Favourites"); setQuery(""); }}><Star size={11} /> Favourites<small>{favorites.filter(id => marketChoiceIds.has(id)).length}</small></button>
      </div>
      <div className="mc-picker-list" data-testid="pair-picker-list">
        {sections.map(([title, rows]) => <section key={title} className="mc-picker-section"><h4>{title}<span>{rows.length}</span></h4>
          {rows.map(pair => { const price = priceOf(pair); return <button key={pair.id} data-testid={`pair-picker-${pair.id}`} className={`mc-picker-row ${ids.includes(pair.id) ? "open" : ""}`} onClick={() => select(pair.id)}>
            <CountryFlags pairId={pair.id} markets={marketChoices} />
            <span className="mc-picker-name" data-testid={`catalog-label-${pair.id}`}>{marketDisplayName(pair)}<small>{pair.derivSymbol ?? pair.symbol}{ids.includes(pair.id) ? " · open in tab" : ""}</small></span>
            <span className="mc-picker-price" data-testid={`catalog-price-${pair.id}`}>{fmt(price, pair.precision)}<small className="live">● live</small></span>
            <span role="button" tabIndex={0} aria-label={favorites.includes(pair.id) ? "Remove from favourites" : "Add to favourites"} data-testid={`catalog-fav-${pair.id}`} className={`mc-picker-fav ${favorites.includes(pair.id) ? "on" : ""}`} onClick={event => { event.stopPropagation(); toggleFavorite(pair.id); }} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); toggleFavorite(pair.id); } }}><Star size={13} fill={favorites.includes(pair.id) ? "currentColor" : "none"} /></span>
          </button>; })}
        </section>)}
        {!q && visibleClosedMarkets.length > 0 && <section className="mc-picker-section closed"><h4>Closed now<span>{visibleClosedMarkets.length}</span></h4>{visibleClosedMarkets.slice(0, 80).map(i => <div key={i.symbol} className="mc-picker-row disabled" data-testid={`catalog-closed-${i.symbol}`}><span className="mc-picker-name">{i.label ?? i.symbol}<small>{i.symbol}</small></span><span className="mc-picker-price">{fmt(i.latestPrice, 5)}<small className="closed">Market closed</small></span></div>)}</section>}
        {sections.length === 0 && visibleClosedMarkets.length === 0 && <p data-testid="pair-picker-empty" className="mc-picker-empty">{q ? "No matching markets." : group === "Favourites" ? "No favourites yet · tap the star on any market." : "No open markets in this group right now."}</p>}
      </div>
    </DialogContent></Dialog>}
  </>;
}
