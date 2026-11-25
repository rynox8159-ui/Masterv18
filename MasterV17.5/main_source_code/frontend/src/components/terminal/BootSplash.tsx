import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/lib/api";
import MasterCandleLogo from "./MasterCandleLogo";

const STEPS = ["Connecting backend", "Syncing Deriv feed", "Loading AI models", "Arming signal engine"];

export default function BootSplash() {
  const health = useQuery({ queryKey: ["backend-health"], queryFn: () => apiGet<{ status: string }>("/health"), retry: 0, refetchInterval: 4000 });
  const [step, setStep] = useState(0);
  const [phase, setPhase] = useState<"show" | "leave" | "gone">(() => sessionStorage.getItem("mc.booted") ? "gone" : "show");
  useEffect(() => {
    if (phase !== "show") return;
    const tick = window.setInterval(() => setStep(value => Math.min(STEPS.length - 1, value + 1)), 420);
    const maxWait = window.setTimeout(() => setPhase("leave"), 3200);
    return () => { window.clearInterval(tick); window.clearTimeout(maxWait); };
  }, [phase]);
  useEffect(() => {
    if (phase === "show" && health.isSuccess && step >= STEPS.length - 1) setPhase("leave");
  }, [phase, health.isSuccess, step]);
  useEffect(() => {
    if (phase !== "leave") return;
    sessionStorage.setItem("mc.booted", "1");
    const timer = window.setTimeout(() => setPhase("gone"), 520);
    return () => window.clearTimeout(timer);
  }, [phase]);
  if (phase === "gone") return null;
  return <div className={`mc-boot ${phase === "leave" ? "leaving" : ""}`} data-testid="boot-splash" role="status" aria-live="polite">
    <div className="mc-boot-core">
      <div className="mc-boot-logo"><span className="mc-boot-ring" /><MasterCandleLogo size={96} animated /></div>
      <h1 data-testid="boot-splash-title">MASTER CANDLE</h1>
      <p>WEB TRADING TERMINAL · V17.3</p>
      <div className="mc-boot-bar"><i style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></div>
      <ul className="mc-boot-steps">{STEPS.map((label, index) => <li key={label} data-testid={`boot-step-${index}`} className={index < step ? "done" : index === step ? "active" : ""}>{label}</li>)}</ul>
    </div>
  </div>;
}
