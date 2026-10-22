import { useState } from "react";
import { Cpu, Loader2 } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { useToast } from "./ui";

type Mem = {
  worker: { rss_mb?: number; models?: string[]; busy?: boolean; pending_free?: boolean };
  last_free: { freed?: string[]; before_mb?: number; after_mb?: number; why?: string };
  idle_minutes: number; machine: { total_gb?: number; used_pct?: number };
};

/** What the engine holds in RAM, with a one-click way to give it back (saves battery). */
export function MemoryPanel() {
  const toast = useToast();
  const { data, reload } = useApi<Mem>("/system/memory", 5000);
  const [asked, setAsked] = useState(false);
  const w = data?.worker || {};
  const models = Array.from(new Set(w.models || []));
  const gb = ((w.rss_mb || 0) / 1000).toFixed(1);

  async function free() {
    setAsked(true);
    try {
      await api.post("/system/free-memory");
      toast("info", w.busy ? "Will free memory as soon as the current job finishes." : "Freeing memory…");
      window.setTimeout(() => { reload(); setAsked(false); }, 4000);
    } catch (e) { toast("err", (e as Error).message); setAsked(false); }
  }

  return (
    <div className="rounded-xl border border-line px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <Cpu size={14} className="text-dim" />
        <span className="whitespace-nowrap"><span className="text-mute">AI memory</span> <span className="font-medium">{gb} GB</span></span>
        <button type="button" onClick={free} disabled={asked || (!models.length && !w.pending_free)}
          title={models.length ? "Unload AI models now" : "Nothing loaded"}
          className="ml-auto rounded-lg px-2 py-0.5 text-dim hover:bg-hover hover:text-ink disabled:opacity-40">
          {asked ? <Loader2 size={12} className="animate-spin" /> : "Free"}</button>
      </div>
      <div className="mt-1 truncate text-mute" title={models.join(", ")}>
        {w.pending_free ? "freeing after the current job" : models.length ? models.join(", ") : "No models loaded"}
        {data?.machine.used_pct != null && <span> · RAM {Math.round(data.machine.used_pct)}%</span>}
      </div>
    </div>
  );
}
