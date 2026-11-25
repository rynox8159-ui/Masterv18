import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { apiGet, apiPost, promptOperatorControlHeaders } from "@/lib/api";

type ProxyTest = { ok: boolean; status: number | null; ip: string | null; message: string };
type ProxyStatus = { configured: boolean; proxy: string | null; lastTest: ProxyTest | null };
type SaveResult = { saved: boolean; configured: boolean; proxy: string | null; test: ProxyTest | null };

export default function QuotexProxyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const client = useQueryClient();
  const status = useQuery({ queryKey: ["qx-proxy"], queryFn: () => apiGet<ProxyStatus>("/v1/providers/qx-proxy"), enabled: open });
  const [proxy, setProxy] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProxyTest | null>(null);
  const save = async (value: string) => {
    const headers = promptOperatorControlHeaders();
    if (headers === null) return;
    setBusy(true);
    try {
      const response = await apiPost<SaveResult>("/v1/providers/qx-proxy", { proxy: value }, headers);
      setResult(response.test);
      if (response.saved) {
        toast.success(value ? "Proxy saved · observer restarted" : "Proxy removed");
        setProxy("");
      } else toast.error(response.test?.message ?? "Proxy test failed");
      void client.invalidateQueries({ queryKey: ["qx-proxy"] });
      void client.invalidateQueries({ queryKey: ["provider-controls"] });
    } catch (error) {
      const detail = (error as { body?: { detail?: unknown } }).body?.detail;
      toast.error(typeof detail === "string" ? detail : "Could not save proxy");
    } finally {
      setBusy(false);
    }
  };
  const test = result ?? status.data?.lastTest ?? null;
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="observer-local-dialog" data-testid="qx-proxy-dialog">
      <DialogHeader>
        <DialogTitle><Globe size={16} /> Quotex proxy (QX_PROXY)</DialogTitle>
        <DialogDescription>Quotex blocks this server's country. Use a residential proxy from a country where Quotex works (e.g. Bangladesh, India). It is tested, saved to backend/.env and used after every restart.</DialogDescription>
      </DialogHeader>
      <p className="at-muted" data-testid="qx-proxy-current">Current: {status.data?.proxy ?? "not set"}</p>
      <label className="at-field">
        <span>Proxy URL</span>
        <input data-testid="qx-proxy-input" value={proxy} placeholder="http://user:pass@host:port" onChange={event => setProxy(event.target.value)} />
      </label>
      {test && <p className={test.ok ? "at-pos" : "at-warn"} data-testid="qx-proxy-result">{test.message}{test.ip ? ` · exit IP ${test.ip}` : ""}</p>}
      <div className="observer-local-actions">
        <Button data-testid="qx-proxy-save" disabled={busy || !proxy.trim()} onClick={() => void save(proxy.trim())}>{busy ? <Loader2 size={14} className="animate-spin" /> : null} Test &amp; save</Button>
        {status.data?.configured && <Button data-testid="qx-proxy-remove" variant="secondary" disabled={busy} onClick={() => void save("")}>Remove proxy</Button>}
      </div>
    </DialogContent>
  </Dialog>;
}
