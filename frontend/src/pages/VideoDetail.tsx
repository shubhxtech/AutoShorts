import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Laugh, Play, Scissors, Search } from "lucide-react";
import { api, fmt, type Clip, type Video } from "../api";
import { useApi } from "../hooks";
import { Card, Empty, Field, Modal, Page, RightsBadge, Score, StatusPill, useToast } from "../components/ui";
import { EpisodeTimeline } from "../components/charts";

type Transcript = { duration: number; sentences: { i: number; s: number; e: number; text: string; energy: number; laughs: number; speaker?: string }[]; energy: number[]; hop: number; laughs: number[] };

export default function VideoDetail() {
  const { id } = useParams();
  const toast = useToast();
  const { data: v } = useApi<Video & { moments: any[] }>(`/videos/${id}`, 5000);
  const { data: clips } = useApi<Clip[]>(`/clips?status=&video_id=${id}`, 6000);
  const [q, setQ] = useState("");
  const { data: tr } = useApi<Transcript>(v?.status === "ready" || v?.status === "rendering" ? `/videos/${id}/transcript?q=${encodeURIComponent(q)}` : null);
  const [sel, setSel] = useState<number[]>([]);
  const [remine, setRemine] = useState(false);
  const [lang, setLang] = useState("");
  const [script, setScript] = useState("");

  const range = useMemo(() => {
    if (!tr || sel.length === 0) return null;
    const ss = tr.sentences.filter((s) => sel.includes(s.i));
    return { start: Math.min(...ss.map((s) => s.s)), end: Math.max(...ss.map((s) => s.e)) };
  }, [sel, tr]);

  async function clipRange() {
    if (!range) return;
    try {
      await api.post("/moments/custom", { video_id: Number(id), start: range.start, end: range.end, hook: "" });
      toast("ok", "Rendering your hand-picked clip");
      setSel([]);
    } catch (e) {
      toast("err", (e as Error).message);
    }
  }

  if (!v) return <Page title="Video"><div className="text-dim">Loading…</div></Page>;
  return (
    <Page title={v.title || "Video"} subtitle={<span className="flex flex-wrap items-center gap-2"><StatusPill status={v.status} /> <RightsBadge rights={v.rights} /> {v.channel} · {fmt.dur(v.duration_s)} {v.stage_message && `· ${v.stage_message}`}</span>}
      actions={<>
        {["queued", "downloading", "transcribing", "analyzing", "mining", "rendering"].includes(v.status) &&
          <button className="btn-danger" onClick={() => api.post(`/videos/${id}/cancel`).then(() => toast("ok", "Canceled"))}>Cancel processing</button>}
        {["failed", "skipped", "canceled"].includes(v.status) &&
          <button className="btn-outline" onClick={() => api.post(`/videos/${id}/process`).then(() => toast("ok", "Queued again"))}>Retry</button>}
        {["ready", "failed", "canceled"].includes(v.status) && <button className="btn-outline" onClick={() => setRemine(true)}>Find clips again</button>}
        <button className="btn-ghost" onClick={() => { if (confirm("Delete this video and its unpublished clips?")) api.del(`/videos/${id}`).then(() => history.back()); }}>Delete</button>
      </>}>
      <Modal open={remine} onClose={() => setRemine(false)} title="Find clips again">
        <div className="space-y-4 text-sm">
          <p className="text-dim">Re-runs clip finding and editing with the latest engine. Unposted clips from this video are replaced; the download is kept.</p>
          <Field label="Spoken language" hint="Fix a wrong detection (e.g. Hindi heard as Urdu). Changing it re-transcribes the video.">
            <select className="input" value={lang} onChange={(e) => setLang(e.target.value)}>
              <option value="">Keep ({v.language || "auto"})</option><option value="hi">Hindi</option><option value="en">English</option><option value="auto">Detect again</option>
            </select></Field>
          <Field label="Hindi captions written in">
            <select className="input" value={script} onChange={(e) => setScript(e.target.value)}>
              <option value="">Keep current</option><option value="native">Hindi script (Devanagari)</option><option value="latin">Hinglish (Latin letters)</option>
            </select></Field>
          <div className="flex justify-end gap-2">
            <button className="btn-ghost" onClick={() => setRemine(false)}>Cancel</button>
            <button className="btn-primary" onClick={async () => {
              await api.post(`/videos/${id}/remine`, { retranscribe: !!(lang || script), language: lang || null, caption_script: script || null });
              setRemine(false); toast("ok", "Finding clips again");
            }}>Find clips again</button>
          </div>
        </div>
      </Modal>
      {v.error && <div className="mb-4 rounded-xl border border-bad/40 bg-bad/10 px-4 py-3 text-sm text-bad">{v.error}</div>}
      {tr && v.moments?.length ? (
        <Card title="Where the clips come from" className="mb-6">
          <EpisodeTimeline duration={tr.duration} energy={tr.energy} hop={tr.hop} laughs={tr.laughs} moments={v.moments} />
        </Card>
      ) : null}
      <div className="grid gap-6 xl:grid-cols-[1fr_420px]">
        <div className="space-y-6">
          <Card title={`Moments found (${v.moments?.length || 0})`} pad={false}>
            {!v.moments?.length ? <div className="p-5 text-sm text-dim">Moments appear once the video has been transcribed and scored.</div> : (
              <ul className="divide-y divide-line">
                {v.moments.map((m) => (
                  <li key={m.id} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-medium">{m.title}</div>
                        <div className="mt-0.5 text-xs text-mute">{fmt.dur(m.start_s)} – {fmt.dur(m.end_s)} · {m.hook_style?.replace(/_/g, " ")}</div>
                        <p className="mt-1.5 line-clamp-2 text-sm text-dim">{m.text}</p>
                      </div>
                      <Score value={m.final_score} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Transcript" actions={
            <div className="relative"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-mute" />
              <input className="input !w-56 !py-1.5 !pl-8" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} /></div>}>
            {!tr ? <div className="text-sm text-dim">Available after transcription.</div> : (
              <>
                <p className="mb-3 text-xs text-mute">Select lines to cut your own clip.</p>
                <div className="max-h-[560px] space-y-1 overflow-y-auto pr-2">
                  {tr.sentences.map((s) => {
                    const on = sel.includes(s.i);
                    return (
                      <button key={s.i} onClick={() => setSel(on ? sel.filter((x) => x !== s.i) : [...sel, s.i])}
                        className={`flex w-full gap-3 rounded-lg px-2 py-1.5 text-left text-sm ${on ? "bg-brand/15" : "hover:bg-hover"}`}>
                        <span className="tabular w-12 shrink-0 text-xs text-mute">{fmt.dur(s.s)}</span>
                        <span className="flex-1">{s.text}</span>
                        {s.laughs > 0 && <Laugh size={14} className="shrink-0 text-warn" aria-label="laughter" />}
                      </button>
                    );
                  })}
                </div>
                {range && (
                  <div className="mt-3 flex items-center justify-between rounded-xl bg-raised px-3 py-2 text-sm">
                    <span>{fmt.dur(range.start)} – {fmt.dur(range.end)} ({Math.round(range.end - range.start)}s)</span>
                    <button className="btn-primary !py-1.5" onClick={clipRange} disabled={range.end - range.start < 8}><Scissors size={14} /> Make clip</button>
                  </div>
                )}
              </>
            )}
          </Card>
        </div>
        <Card title={`Clips (${clips?.length || 0})`}>
          {!clips?.length ? <Empty title="No clips yet" /> : (
            <div className="grid grid-cols-3 gap-3">
              {clips.map((c) => (
                <Link key={c.id} to={`/clips/${c.id}`} className="group">
                  <div className="relative aspect-[9/16] overflow-hidden rounded-xl bg-black">
                    {c.thumb_url && <img src={c.thumb_url} className="h-full w-full object-cover transition-transform group-hover:scale-105" alt="" />}
                    <Play size={22} className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-90" />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-xs"><StatusPill status={c.status} /><span className="text-mute">{c.variant}</span></div>
                </Link>
              ))}
            </div>
          )}
        </Card>
      </div>
    </Page>
  );
}
