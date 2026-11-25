import { useCallback, useEffect, useState, type ReactNode } from "react";
import { KeyRound, Loader2, PlugZap, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AUTH_EVENT, apiGet, apiPost } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";

export const OPEN_API_KEY_EVENT = "naqso:open-api-key";
type GateState = "checking" | "ready" | "need-key" | "offline" | "unconfigured";

export default function ApiKeyGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>("checking");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const connect = useCallback(async (attempt = 0): Promise<void> => {
    try {
      const status = await apiGet<{ configured: boolean }>("/v1/auth/status");
      if (!status.configured) { setState("unconfigured"); return; }
      const session = await apiGet<{ authenticated: boolean }>("/v1/auth/session");
      setState(session.authenticated ? "ready" : "need-key");
    } catch {
      if (attempt < 4) { await new Promise(resolve => setTimeout(resolve, 1500)); return connect(attempt + 1); }
      setState("offline");
    }
  }, []);

  useEffect(() => { void connect(); }, [connect]);
  useEffect(() => {
    if (state !== "offline") return;
    const id = window.setInterval(() => void connect(4), 5000);
    return () => clearInterval(id);
  }, [state, connect]);
  useEffect(() => {
    const ask = () => { setDraft(""); setState("need-key"); };
    window.addEventListener(AUTH_EVENT, ask);
    window.addEventListener(OPEN_API_KEY_EVENT, ask);
    return () => { window.removeEventListener(AUTH_EVENT, ask); window.removeEventListener(OPEN_API_KEY_EVENT, ask); };
  }, []);

  const submit = async () => {
    setBusy(true); setError("");
    try {
      await apiPost("/v1/auth/session", { credential: draft });
      setDraft("");
      await queryClient.invalidateQueries();
      setState("ready");
    } catch (reason) {
      const detail = (reason as { body?: { detail?: unknown } })?.body?.detail;
      setError(detail === "INVALID_CREDENTIAL" ? "Access key was rejected." : detail ? String(detail) : "Could not establish a backend session.");
    } finally {
      setBusy(false);
    }
  };

  return <>
    {children}
    {state === "checking" && <div className="api-connection-toast" role="status" data-testid="api-connection-status"><Loader2 className="animate-spin" size={14} /> Checking backend session</div>}
    {state !== "ready" && state !== "checking" && <aside className="api-gate" data-testid="api-key-gate" role="dialog" aria-modal="false" aria-labelledby="api-gate-title">
      <div className="api-gate-card">
        <div className="api-gate-icon">{state === "offline" ? <PlugZap size={20} /> : <KeyRound size={20} />}</div>
        <h2 id="api-gate-title" data-testid="api-gate-title">{state === "offline" ? "Backend not reachable" : state === "unconfigured" ? "Backend access is not configured" : "Connect to backend"}</h2>
        {state === "offline" && <><p data-testid="api-gate-offline-text">The data feed is unavailable. Check that the backend is running, then retry.</p><Button data-testid="api-gate-retry-button" onClick={() => { setState("checking"); void connect(); }}>Retry connection</Button></>}
        {state === "unconfigured" && <><p data-testid="api-gate-unconfigured-text">Set a random BACKEND_API_KEY of at least 32 characters in the backend .env, restart the backend, then retry.</p><Button data-testid="api-gate-configured-retry-button" onClick={() => { setState("checking"); void connect(); }}>Retry connection</Button></>}
        {state === "need-key" && <form onSubmit={event => { event.preventDefault(); void submit(); }}>
          <p>Enter the server-only access key. It is used to establish a short-lived secure session and is not stored in this browser.</p>
          <Input data-testid="api-key-input" type="password" autoComplete="current-password" autoFocus placeholder="Backend access key" value={draft} onChange={event => setDraft(event.target.value)} />
          {error && <p role="alert" data-testid="api-key-error" className="api-gate-error">{error}</p>}
          <Button data-testid="api-key-submit-button" type="submit" disabled={busy || !draft.trim()}>{busy ? <Loader2 className="animate-spin" size={16} /> : <ShieldCheck size={16} />} Connect</Button>
        </form>}
      </div>
    </aside>}
  </>;
}
