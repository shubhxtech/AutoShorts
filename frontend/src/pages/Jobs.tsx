import { useState } from "react";
import { CheckCircle2, RotateCcw, Workflow, X, XCircle } from "lucide-react";
import { api, fmt, type Job } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, Page, StatusPill, useToast } from "../components/ui";

type Health = { ok: boolean; checks: { name: string; ok: boolean; detail: string; level: string }[]; disk: Record<string, number> };

export default function Jobs() {
  const toast = useToast();
  const [tab, setTab] = useState("queued,running,failed");
  const { data, reload } = useApi<Job[]>(`/jobs?status=${tab}`, 3000);
  const { data: health } = useApi<Health>("/health", 15000);

  async function act(path: string, msg: string) {
    try { await api.post(path); toast("ok", msg); reload(); } catch (e) { toast("err", (e as Error).message); }
  }

  return (
    <Page title="Activity" subtitle="Everything the automation is doing right now, plus system health."
      actions={<div className="flex rounded-xl border border-line p-0.5">{[["queued,running,failed", "Active"], ["done", "Done"], ["canceled", "Canceled"]].map(([k, l]) => (
        <button key={k} onClick={() => setTab(k)} className={`rounded-lg px-3 py-1.5 text-sm ${tab === k ? "bg-hover text-ink" : "text-dim"}`}>{l}</button>))}</div>}>
      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div>
          {!data?.length ? <Empty icon={<Workflow size={28} />} title="Nothing running" /> : (
            <div className="card divide-y divide-line">
              {data.map((j) => (
                <div key={j.id} className="flex items-center gap-4 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-sm font-medium">{fmt.label(j.kind)} <span className="text-xs font-normal text-mute">#{j.id} · {j.lane}</span></div>
                    <div className="truncate text-xs text-dim">{j.message || JSON.stringify(j.payload)}</div>
                    {j.status === "running" && <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-hover"><div className="h-full rounded-full bg-accent" style={{ width: `${j.progress * 100}%` }} /></div>}
                    {j.error && <div className="mt-1 truncate text-xs text-bad" title={j.error}>{j.error}</div>}
                  </div>
                  <StatusPill status={j.status} />
                  <span className="w-16 text-right text-xs text-mute">{fmt.ago(j.started_at || j.created_at)}</span>
                  {(j.status === "queued" || j.status === "running") && <button className="btn-ghost !p-1.5" title="Cancel" onClick={() => act(`/jobs/${j.id}/cancel`, "Canceled")}><X size={15} /></button>}
                  {(j.status === "failed" || j.status === "canceled") && <button className="btn-ghost !p-1.5" title="Retry" onClick={() => act(`/jobs/${j.id}/retry`, "Queued again")}><RotateCcw size={15} /></button>}
                </div>
              ))}
            </div>
          )}
        </div>
        <Card title="System health">
          <ul className="space-y-2.5 text-sm">
            {(health?.checks || []).map((c) => (
              <li key={c.name} className="flex items-start gap-2">
                {c.ok ? <CheckCircle2 size={15} className="mt-0.5 text-good" /> : <XCircle size={15} className={`mt-0.5 ${c.level === "error" ? "text-bad" : "text-warn"}`} />}
                <div><div className="font-medium">{fmt.label(c.name)}</div><div className="text-xs text-dim">{c.detail}</div></div>
              </li>
            ))}
          </ul>
          {health?.disk && <p className="mt-4 text-xs text-mute">Library {health.disk.library_mb} MB · clips {health.disk.clips_mb} MB · {health.disk.disk_free_gb} GB free</p>}
        </Card>
      </div>
    </Page>
  );
}
