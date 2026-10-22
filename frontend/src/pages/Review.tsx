import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckSquare, Clapperboard, Eye, EyeOff, Grid3x3 } from "lucide-react";
import { api, fmt, type Clip } from "../api";
import { useApi, useKey } from "../hooks";
import { PhoneFrame } from "../components/PhoneFrame";
import { ClipEditor, useAccounts } from "../components/ClipEditor";
import { OverlayPreview } from "../components/OverlayPreview";
import { EditorTimeline, usePlayerWidth } from "../components/EditorTimeline";
import { useLiveEdits } from "../hooksLive";
import { useVirtualPlayer } from "../hooksVirtual";
import { Empty, Page, useToast } from "../components/ui";

const TABS = [
  { id: "review", label: "To review" },
  { id: "approved,scheduled", label: "Approved" },
  { id: "rendering", label: "Rendering" },
  { id: "qc_failed,failed", label: "Failed checks" },
  { id: "rejected", label: "Rejected" },
];

export default function Review() {
  const toast = useToast();
  const [tab, setTab] = useState("review");
  const { data, setData } = useApi<Clip[]>(`/clips?status=${tab}`, 6000);
  const accounts = useAccounts();
  const [idx, setIdx] = useState(0);
  const [bulk, setBulk] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [muted, setMuted] = useState(false);
  const [safe, setSafe] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const touch = useRef<{ x: number; y: number } | null>(null);
  const clips = data || [];
  const clip = clips[Math.min(idx, Math.max(0, clips.length - 1))];
  const playerSlot = useRef<HTMLDivElement>(null);
  const pw = usePlayerWidth(playerSlot, clip?.id);
  const live = useLiveEdits(clip);
  const vp = useVirtualPlayer(clip, video);

  useEffect(() => setIdx(0), [tab]);

  async function approve(ids: number[], accs: number[] = [], at?: string) {
    try {
      const res = await api.post<{ clip_id: number; posts: unknown[] }[]>("/clips/approve", { ids, accounts: accs, at });
      const n = res.reduce((s, r) => s + r.posts.length, 0);
      toast("ok", n ? `Approved · ${n} post${n === 1 ? "" : "s"} scheduled` : "Approved (connect pages to schedule posts)");
      setData((d) => (d || []).filter((c) => !ids.includes(c.id)));
      setSelected([]);
    } catch (e) {
      toast("err", (e as Error).message);
    }
  }

  async function trim(c: Clip, tr: { start: number; end: number }) {
    try {
      const updated = await api.patch<Clip>(`/clips/${c.id}`, { trim_start: tr.start, trim_end: tr.end, cut: [] });
      setData((d) => (d || []).map((x) => (x.id === updated.id ? updated : x)));
      toast("info", "Trimming: re-cutting from the clip's master (about 15 seconds)");
    } catch (e) { toast("err", (e as Error).message); }
  }

  async function reject(ids: number[]) {
    await api.post("/clips/reject", { ids });
    setData((d) => (d || []).filter((c) => !ids.includes(c.id)));
    setSelected([]);
    toast("info", "Rejected");
  }

  useKey((e) => {
    if (bulk || !clip) return;
    if (e.key === "a" || e.key === "A") approve([clip.id]);
    else if (e.key === "r" || e.key === "R") reject([clip.id]);
    // ← → Space , . belong to the timeline (scrub / play / frame step); J K ↑ ↓ switch clips.
    else if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(clips.length - 1, i + 1)); }
    else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === "m" || e.key === "M") setMuted((m) => !m);
  }, [clip, clips.length, bulk]);

  function onTouchEnd(e: React.TouchEvent) {
    const start = touch.current;
    touch.current = null;
    if (!start || !clip) return;
    const dx = e.changedTouches[0].clientX - start.x;
    const dy = e.changedTouches[0].clientY - start.y;
    if (Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dy)) (dx > 0 ? approve([clip.id]) : reject([clip.id]));
    else if (Math.abs(dy) > 80) setIdx((i) => Math.max(0, Math.min(clips.length - 1, i + (dy < 0 ? 1 : -1))));
  }

  return (
    <Page title="Review"
      actions={
        <>
          <div className="flex rounded-xl border border-line p-0.5">
            {TABS.map((t) => (
              <button key={t.id} onClick={() => setTab(t.id)} className={`rounded-lg px-3 py-1.5 text-sm ${tab === t.id ? "bg-hover text-ink" : "text-dim hover:text-ink"}`}>{t.label}</button>
            ))}
          </div>
          <button className={`btn-outline ${bulk ? "!border-brand" : ""}`} onClick={() => { setBulk(!bulk); setSelected([]); }}><Grid3x3 size={15} /> Bulk</button>
        </>
      }>
      {!clips.length ? (
        <Empty icon={<Clapperboard size={28} />} title={tab === "review" ? "Nothing to review" : "Nothing here"}>
          {tab === "review" ? <>New clips land here after they pass quality checks. <Link to="/" className="underline">Paste a video link</Link> to make some.</> : null}
        </Empty>
      ) : bulk ? (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <button className="btn-outline" onClick={() => setSelected(selected.length === clips.length ? [] : clips.map((c) => c.id))}><CheckSquare size={15} /> {selected.length === clips.length ? "Clear" : "Select all"}</button>
            <button className="btn-primary" disabled={!selected.length} onClick={() => approve(selected)}>Approve {selected.length || ""}</button>
            <button className="btn-danger" disabled={!selected.length} onClick={() => reject(selected)}>Reject {selected.length || ""}</button>
            <span className="text-sm text-dim">Approved clips go to every matching page at its best times.</span>
          </div>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6">
            {clips.map((c) => {
              const on = selected.includes(c.id);
              return (
                <button key={c.id} onClick={() => setSelected(on ? selected.filter((x) => x !== c.id) : [...selected, c.id])}
                  className={`card overflow-hidden text-left ${on ? "ring-2 ring-brand" : ""}`}>
                  <div className="relative aspect-[9/16] bg-black">
                    {c.thumb_url && <img src={c.thumb_url} className="h-full w-full object-cover" alt="" />}
                    <span className="absolute left-2 top-2 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px]">{c.predicted_score.toFixed(1)}</span>
                    <span className="absolute right-2 top-2 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px]">{fmt.dur(c.duration_s)}</span>
                  </div>
                  <div className="p-2.5 text-xs"><div className="line-clamp-2 font-medium">{c.hook_text}</div><div className="mt-1 text-mute">{c.caption_style} · {c.edit_preset}</div></div>
                </button>
              );
            })}
          </div>
        </>
      ) : (
        <>
        {/* Narrower windows: a thumbnail strip instead of the side list, so the editor keeps its width. */}
        <div className="mb-3 flex gap-1.5 overflow-x-auto pb-1 2xl:hidden">
          {clips.map((c, i) => (
            <button key={c.id} onClick={() => setIdx(i)} title={c.hook_text}
              className={`h-[52px] w-[29px] shrink-0 overflow-hidden rounded-md bg-black ${c.id === clip?.id ? "ring-2 ring-brand" : "opacity-70 hover:opacity-100"}`}>
              {c.thumb_url && <img src={c.thumb_url} className="h-full w-full object-cover" alt="" />}
            </button>
          ))}
        </div>
        <div className="grid gap-6 md:grid-cols-[auto_minmax(0,1fr)] 2xl:grid-cols-[210px_auto_minmax(0,1fr)]">
          <ul className="hidden max-h-[calc(100vh-180px)] space-y-2 overflow-y-auto pr-1 2xl:block">
            {clips.map((c, i) => (
              <li key={c.id}>
                <button onClick={() => setIdx(i)} className={`flex w-full gap-2.5 rounded-xl p-2 text-left ${c.id === clip?.id ? "bg-hover" : "hover:bg-hover/60"}`}>
                  <div className="h-20 w-[45px] shrink-0 overflow-hidden rounded-md bg-black">{c.thumb_url && <img src={c.thumb_url} className="h-full w-full object-cover" alt="" />}</div>
                  <div className="min-w-0 text-xs">
                    <div className="line-clamp-3 font-medium leading-snug">{c.hook_text}</div>
                    <div className="mt-1 text-mute">{c.predicted_score.toFixed(1)} · {fmt.dur(c.duration_s)} · {c.variant}</div>
                  </div>
                </button>
              </li>
            ))}
          </ul>

          <div ref={playerSlot} onTouchStart={(e) => (touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY })} onTouchEnd={onTouchEnd}>
            {clip && (
              <PhoneFrame ref={video} src={live.live ? clip.base_url! : clip.url} poster={live.live ? clip.base_poster || undefined : clip.thumb_url} muted={muted} safeZones={safe} maxWidth={pw}
                caption={clip.meta?.instagram_caption || clip.hook_text}
                overlay={live.live && clip.overlay_spec ? <OverlayPreview spec={clip.overlay_spec} words={clip.words} edits={live.edits} video={video}
                  onDragCaption={(y) => live.update({ caption_y: Math.round(y * 100) / 100 })} clock={vp.clock} /> : null} />
            )}
          </div>

          <div className="card flex min-h-[480px] flex-col overflow-hidden" style={{ height: Math.round((pw * 19.5) / 9) }}>
            {clip && (
              <ClipEditor clip={clip} accounts={accounts} live={live} video={video} vp={vp}
                onChanged={(c) => setData((d) => (d || []).map((x) => (x.id === c.id ? c : x)))}
                onApprove={approve} onReject={reject} />
            )}
          </div>
        </div>
        {clip && (
          <div className="mt-3">
            <EditorTimeline clip={clip} video={video} vp={vp} edits={live.edits} busy={clip.status === "rendering"}
              onTrim={(tr) => trim(clip, tr)}
              extra={<>
                <button type="button" className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setSafe(!safe)}>{safe ? <EyeOff size={13} /> : <Eye size={13} />} Safe zones</button>
                <span className="px-1 text-mute" title="J / K or ↑ ↓: next / previous clip · A approve · R reject">{Math.min(idx, clips.length - 1) + 1} / {clips.length}</span>
              </>} />
          </div>
        )}
        </>
      )}
    </Page>
  );
}
