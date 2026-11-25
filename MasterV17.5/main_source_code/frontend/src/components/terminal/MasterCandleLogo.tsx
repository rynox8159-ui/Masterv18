import { useId } from "react";

export default function MasterCandleLogo({ size = 34, animated = false, className = "" }: { size?: number; animated?: boolean; className?: string }) {
  const glow = `mc-glow-${useId().replace(/:/g, "")}`;
  return <svg data-testid="master-candle-logo" className={`mc-logo ${animated ? "mc-logo-animated" : ""} ${className}`} width={size} height={size} viewBox="0 0 64 64" fill="none" aria-label="Master Candle" role="img">
    <defs><filter id={glow} x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="1.6" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs>
    <g filter={`url(#${glow})`} strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.4">
      <g stroke="#2bf07f"><path d="M15 33v6M15 53v6" /><rect x="10.5" y="39" width="9" height="14" rx="1.6" /></g>
      <g stroke="#39c8ff"><path d="M32 22v6M32 50v7" /><rect x="27.5" y="28" width="9" height="22" rx="1.6" /></g>
      <g stroke="#ff3d55"><path d="M49 13v7M49 46v7" /><rect x="44.5" y="20" width="9" height="26" rx="1.6" /></g>
      <path stroke="#4fe0ff" d="M6 30 L22 17 L31 23 L52 6 M43 5.5 L52.5 5.5 L51.5 14.5" />
    </g>
  </svg>;
}

export function LogoLoader({ label = "Loading Master Candle…", fullscreen = false }: { label?: string; fullscreen?: boolean }) {
  return <div className={`mc-loader ${fullscreen ? "mc-loader-full" : ""}`} data-testid="logo-loader" role="status">
    <MasterCandleLogo size={fullscreen ? 92 : 54} animated />
    <span>{label}</span>
  </div>;
}
