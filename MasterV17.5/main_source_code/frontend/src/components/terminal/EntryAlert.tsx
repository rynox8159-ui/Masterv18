import { useEffect, useRef } from "react";
import { BellRing } from "lucide-react";
import { playChime, showNotification } from "@/lib/feedAlerts";

export const ENTRY_ALERT_SECONDS = 15;

interface AlertCard {
  id: string;
  pair: string;
  direction: "CALL" | "PUT";
  duration: string;
  entryEpoch?: number;
  confidence?: number;
}

export default function EntryAlert({ cards, now }: { cards: AlertCard[]; now: number }) {
  const fired = useRef(new Set<string>());
  const due = cards
    .filter(card => card.entryEpoch !== undefined && card.entryEpoch - now > 0 && card.entryEpoch - now <= ENTRY_ALERT_SECONDS)
    .sort((a, b) => (a.entryEpoch ?? 0) - (b.entryEpoch ?? 0))[0];
  useEffect(() => {
    if (!due || fired.current.has(due.id)) return;
    fired.current.add(due.id);
    void playChime(0.6);
    showNotification(`${due.pair} ${due.direction} in ${ENTRY_ALERT_SECONDS}s`, `${due.duration} trade · get ready to enter`);
  }, [due]);
  if (!due || due.entryEpoch === undefined) return null;
  const left = Math.max(0, Math.ceil(due.entryEpoch - now));
  return <aside className={`entry-alert entry-alert-${due.direction.toLowerCase()}`} role="alert" aria-live="assertive" data-testid="entry-alert-15s">
    <div className="entry-alert-ring" style={{ ["--pct" as string]: `${(left / ENTRY_ALERT_SECONDS) * 100}%` }}><b data-testid="entry-alert-seconds">{left}</b><small>sec</small></div>
    <div className="entry-alert-copy">
      <span><BellRing size={12} /> TRADE ALERT · 15s</span>
      <strong data-testid="entry-alert-pair">{due.pair} · {due.direction === "CALL" ? "▲ CALL" : "▼ PUT"}</strong>
      <small>{due.duration} expiry{due.confidence ? ` · ${due.confidence.toFixed(0)}% confidence` : ""} · enter at candle open</small>
    </div>
  </aside>;
}
