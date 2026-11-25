import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Keyboard, Loader2, MonitorSmartphone, RotateCw, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { apiUrl } from "@/lib/api";

interface RemoteState {
  available: boolean;
  frameAgeSeconds: number | null;
  url: string;
  title: string;
  viewport: { width: number; height: number };
}

const KEYS = new Set(["Enter", "Backspace", "Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Delete", "Home", "End"]);

export default function RemoteBrowserDialog({ open, onOpenChange, observerDetail, operatorKey = "" }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  observerDetail?: string;
  operatorKey?: string;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<RemoteState | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const frameRef = useRef<HTMLDivElement>(null);
  const headers = useMemo<Record<string, string>>(
    () => {
      const result: Record<string, string> = {};
      if (operatorKey) result["X-Provider-Control-Key"] = operatorKey;
      return result;
    },
    [operatorKey],
  );

  useEffect(() => {
    if (!open) return;
    let alive = true;
    let timer = 0;
    let lastUrl: string | null = null;
    const tick = async () => {
      try {
        const [stateResponse, frameResponse] = await Promise.all([
          fetch(apiUrl("/v1/observer/remote/state"), { headers, credentials: "include" }),
          fetch(apiUrl("/v1/observer/remote/frame"), { headers, credentials: "include", cache: "no-store" }),
        ]);
        if (!stateResponse.ok) throw new Error(`Browser state failed (${stateResponse.status})`);
        if (!frameResponse.ok && frameResponse.status !== 204) throw new Error(`Browser frame failed (${frameResponse.status})`);
        const current = await stateResponse.json() as RemoteState;
        if (!alive) return;
        setState(current);
        setError("");
        if (frameResponse.status === 200) {
          const nextUrl = URL.createObjectURL(await frameResponse.blob());
          if (!alive) {
            URL.revokeObjectURL(nextUrl);
            return;
          }
          setSrc(nextUrl);
          if (lastUrl) URL.revokeObjectURL(lastUrl);
          lastUrl = nextUrl;
        }
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : "Remote browser unavailable");
      } finally {
        if (alive) timer = window.setTimeout(() => void tick(), 700);
      }
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
      if (lastUrl) URL.revokeObjectURL(lastUrl);
    };
  }, [open, headers]);

  const send = async (event: Record<string, unknown>) => {
    const response = await fetch(apiUrl("/v1/observer/remote/input"), {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(event),
    });
    if (!response.ok) throw new Error(`Browser input failed (${response.status})`);
  };
  const sendSafely = (event: Record<string, unknown>) => {
    void send(event).catch(cause => {
      const message = cause instanceof Error ? cause.message : "Remote browser input failed";
      setError(message);
      toast.error("Remote browser input failed", { description: message });
    });
  };
  const click = (event: MouseEvent<HTMLImageElement>, type: "click" | "dblclick") => {
    const rect = event.currentTarget.getBoundingClientRect();
    const viewport = state?.viewport ?? { width: 1366, height: 768 };
    sendSafely({
      type,
      x: ((event.clientX - rect.left) / rect.width) * viewport.width,
      y: ((event.clientY - rect.top) / rect.height) * viewport.height,
    });
    frameRef.current?.focus();
  };
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).tagName === "INPUT") return;
    if (KEYS.has(event.key)) {
      event.preventDefault();
      sendSafely({ type: "key", key: event.key });
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      sendSafely({ type: "type", text: event.key });
    }
  };
  const blocked = /REGION BLOCKED/i.test(observerDetail ?? "");
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="remote-browser-dialog" data-testid="remote-browser-dialog" showCloseButton={false}>
      <DialogHeader>
        <DialogTitle><MonitorSmartphone size={16} /> Quotex login · Playwright browser</DialogTitle>
        <DialogDescription>This is the observer's browser. Click and type here to log in manually. Do not use this remote view on a shared or untrusted device.</DialogDescription>
      </DialogHeader>
      <Button data-testid="remote-browser-close" variant="ghost" size="icon-sm" className="project-zip-close" aria-label="Close" onClick={() => onOpenChange(false)}><X size={16} /></Button>
      <div className="remote-browser-bar">
        <span className={`remote-dot ${state?.available ? "on" : ""}`} />
        <span className="remote-url" data-testid="remote-browser-url">{state?.url || "starting Chromium…"}</span>
        <button type="button" data-testid="remote-browser-reload" onClick={() => sendSafely({ type: "reload" })}><RotateCw size={13} /> Reload</button>
      </div>
      {(error || observerDetail || blocked) && <p className={`remote-status ${blocked || error ? "bad" : ""}`} data-testid="remote-browser-status">{error || observerDetail}{blocked ? " — the cloud server may be blocked by Quotex; run the observer on your PC instead." : ""}</p>}
      <div ref={frameRef} className="remote-frame" tabIndex={0} onKeyDown={onKey} data-testid="remote-browser-frame">
        {src ? <img src={src} alt="Remote Quotex browser" draggable={false} onClick={event => click(event, "click")} onWheel={event => sendSafely({ type: "scroll", dy: event.deltaY })} />
          : <div className="remote-wait"><Loader2 className="animate-spin" size={22} /> Waiting for the first browser frame…</div>}
      </div>
      <div className="remote-type">
        <Keyboard size={15} />
        <input data-testid="remote-browser-text" value={text} placeholder="Type login text, then Send" onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && text) { sendSafely({ type: "type", text }); setText(""); } }} />
        <Button data-testid="remote-browser-send" size="sm" disabled={!text} onClick={() => { sendSafely({ type: "type", text }); setText(""); }}>Send</Button>
        <Button data-testid="remote-browser-enter" size="sm" variant="secondary" onClick={() => sendSafely({ type: "key", key: "Enter" })}>Enter ↵</Button>
      </div>
    </DialogContent>
  </Dialog>;
}
