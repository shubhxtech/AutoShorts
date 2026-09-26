import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Radar, Search, ShieldAlert } from "lucide-react";
import { api, fmt, type Video } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, Page, useToast } from "../components/ui";

type Lead = { id: number; title: string; channel: string; url: string; thumbnail: string; view_count: number; viral_score: number; velocity: number; published_at: string | null; duration_s: number; covered_by: string | null };
type D = { covered: Video[]; leads: Lead[]; queries: string[] };

export default function Discover() {
  const toast = useToast();
  const { data, reload } = useApi<D>("/discover", 20000);
  const [q, setQ] = useState("");
  useEffect(() => { if (data && !q) setQ((data.queries || []).join(", ")); }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  async function scout() {
    await api.post("/discover/scout", { queries: q.split(",").map((x) => x.trim()).filter(Boolean) });
    toast("ok", "Scouting YouTube; results appear in a minute");
    setTimeout(reload, 30000);
  }

  return (
    <Page title="Viral finder" subtitle="Videos outperforming their channel right now. Covered = a rights record lets you clip it; leads need permission first.">
      <Card title="Videos from your covered sources" pad={false}>
        {!data?.covered.length ? <div className="p-5 text-sm text-dim">Add channels or clipping campaigns under Rights and ClipForge watches them for breakout videos.</div> : (
          <ul className="divide-y divide-line">{data.covered.map((v) => (
            <li key={v.id} className="flex items-center gap-3 px-5 py-3">
              {v.thumbnail && <img src={v.thumbnail} className="h-12 w-20 rounded-md object-cover" alt="" />}
              <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{v.title}</div><div className="text-xs text-mute">{v.source_name} · {fmt.n(v.view_count)} views · {fmt.ago(v.published_at)}</div></div>
              <span className="chip text-brand">{v.viral_score.toFixed(1)}× channel</span>
              <button className="btn-primary !py-1.5 text-xs" onClick={() => api.post(`/videos/${v.id}/process`).then(() => toast("ok", "Making clips"))}>Clip it</button>
            </li>))}</ul>
        )}
      </Card>
      <Card title="Trend scout" className="mt-6">
        <div className="flex gap-2">
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Niches to scan, comma separated: podcast money, fitness motivation, startup advice" />
          <button className="btn-outline shrink-0" onClick={scout}><Search size={15} /> Scan</button>
        </div>
        <div className="mt-5">
          {!data?.leads.length ? <Empty icon={<Radar size={28} />} title="No leads yet">Scan a few niches. ClipForge ranks recent long videos by how fast they're gaining views.</Empty> : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{data.leads.map((d) => (
              <div key={d.id} className="rounded-xl border border-line p-3">
                <a href={d.url} target="_blank" rel="noreferrer">{d.thumbnail && <img src={d.thumbnail} className="aspect-video w-full rounded-lg object-cover" alt="" />}</a>
                <div className="mt-2 line-clamp-2 text-sm font-medium">{d.title}</div>
                <div className="mt-1 text-xs text-mute">{d.channel} · {fmt.n(d.view_count)} views · {fmt.n(d.velocity)}/day</div>
                <div className="mt-3 flex items-center justify-between">
                  {d.covered_by ? <span className="chip text-good">covered by {d.covered_by}</span> : <span className="chip text-warn"><ShieldAlert size={11} /> needs permission</span>}
                  <div className="flex gap-1">
                    {d.covered_by ? <button className="btn-primary !py-1 text-xs" onClick={() => api.post(`/discover/${d.id}/queue`).then(() => toast("ok", "Making clips"))}>Clip it</button>
                      : <Link to="/sources" className="btn-outline !py-1 text-xs">Add rights</Link>}
                    <button className="btn-ghost !py-1 text-xs" onClick={() => api.post(`/discover/${d.id}/dismiss`).then(reload)}>Dismiss</button>
                  </div>
                </div>
              </div>))}</div>
          )}
        </div>
      </Card>
    </Page>
  );
}
