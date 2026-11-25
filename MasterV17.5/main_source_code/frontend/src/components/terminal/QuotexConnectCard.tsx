import { Activity, Loader2, LogIn, MonitorSmartphone } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ObserverState { state: string; log?: string[] }

export default function QuotexConnectCard({ observer, busy, onStart }: { observer?: ObserverState; busy: boolean; onStart: () => void }) {
  const running = observer?.state === "RUNNING";
  return <section className="quotex-connect" data-testid="quotex-connect-card">
    <div className="quotex-connect-icon"><MonitorSmartphone size={30} /></div>
    <h2 data-testid="quotex-connect-title">{running ? "Quotex window is open – log in to start streaming" : "Binary mode uses Quotex pairs"}</h2>
    <p>Click <b>Playwright Observer</b> (left menu) or the button below to open Quotex in a new tab on this computer, then log in. If this app uses a remote backend (for example in Codespaces), that server cannot use this tab or its login session; run the observer on this PC to send its market data.</p>
    <ol>
      <li>First time only: <code>python -m playwright install chromium</code> (setup script does this).</li>
      <li>Optional auto-login: add <code>QX_EMAIL</code> / <code>QX_PASSWORD</code> to <code>main_source_code/backend/.env</code>.</li>
      <li>The observer must run on the same PC as the backend (it needs a visible browser window).</li>
    </ol>
    <Button data-testid="quotex-start-observer-button" onClick={onStart} disabled={busy || running}>
      {busy ? <Loader2 className="animate-spin" size={16} /> : running ? <Activity size={16} /> : <LogIn size={16} />}
      {running ? "Observer running · waiting for Quotex pairs" : "Open Quotex & log in"}
    </Button>
    {!!observer?.log?.length && <pre data-testid="quotex-observer-log" className="quotex-observer-log">{observer.log.join("\n")}</pre>}
  </section>;
}
