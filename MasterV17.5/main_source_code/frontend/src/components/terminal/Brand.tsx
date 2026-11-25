import MasterCandleLogo from "./MasterCandleLogo";

export default function Brand() {
  return (
    <div data-testid="brand-lockup" className="brand-lockup brand-premium">
      <div data-testid="brand-mark" className="brand-mark brand-mark-logo">
        <MasterCandleLogo size={30} />
      </div>
      <div data-testid="brand-copy" className="brand-copy">
        <div data-testid="brand-name" className="master-brand-name">MASTER CANDLE</div>
        <div data-testid="brand-tagline">WEB TRADING TERMINAL</div>
      </div>
    </div>
  );
}
