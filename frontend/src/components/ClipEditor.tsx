import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Check, CheckCircle2, ChevronDown, ChevronUp, Loader2, RefreshCw, Scissors, X, XCircle } from "lucide-react";
import { api, fmt, type Account, type Clip } from "../api";
import { Platform, RightsBadge, Score, StatusPill, Toggle, useToast } from "./ui";
import { VoicePicker } from "./VoicePicker";
import type { OverlayEdits } from "./OverlayPreview";

const CAPTION_STYLES = ["clean", "bold", "karaoke", "pop", "boxed", "minimal"];
const PRESETS = [
  { id: "clean", label: "Clean (default)" },
  { id: "hype", label: "Hype" },
  { id: "minimal", label: "Minimal" },
];
const OPENINGS = [
  { id: "straight", label: "Straight in" },
  { id: "cold_open", label: "Cold open (tease the punchline)" },
  { id: "narrated", label: "Narrated intro (local voice)" },
];

/** Everything you can change about a clip before approving it. */
export function ClipEditor({ clip, accounts, onChanged, onApprove, onReject, live, video }: {
  clip: Clip; accounts: Account[]; onChanged: (c: Clip) => void;
  onApprove: (ids: number[], accounts: number[], at?: string) => void; onReject: (ids: number[]) => void;
  live?: { edits: OverlayEdits; update: (p: OverlayEdits) => void; saving: boolean; live: boolean };
  video?: React.RefObject<HTMLVideoElement>;
}) {
  const toast = useToast();
  const [hook, setHook] = useState(clip.hook_text);
  const [edits, setEdits] = useState<Record<string, string>>(clip.caption_edits || {});
  const [editing, setEditing] = useState<number | null>(null);
  const [targets, setTargets] = useState<number[]>(clip.target_accounts?.length ? clip.target_accounts : []);
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [showWords, setShowWords] = useState(false);
  const [ytTitle, setYtTitle] = useState(clip.meta?.youtube_title || "");
  const [igCaption, setIgCaption] = useState(clip.meta?.instagram_caption || "");
  const [voice, setVoice] = useState(clip.voice || "design:documentary");
  const [hearing, setHearing] = useState(false);
  const [tab, setTab] = useState<"text" | "edit" | "post" | "checks">("text");
  const [titles, setTitles] = useState<string[]>(clip.meta?.title_options || []);
  const [titling, setTitling] = useState(false);

  // The local LLM writes 10 options in the background; poll the clip until they land.
  async function suggestTitles() {
    setTitling(true);
    setTitles([]);
    try {
      await api.post(`/clips/${clip.id}/titles`);
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        const c = await api.get<Clip>(`/clips/${clip.id}`);
        if (c.meta?.title_options?.length) { setTitles(c.meta.title_options); break; }
      }
    } catch (e) { toast("err", (e as Error).message); } finally { setTitling(false); }
  }
  const voiceAudio = useRef<HTMLAudioElement | null>(null);

  // Plays the chosen voice reading this clip's opening, over the muted video: judge it in context, no render.
  async function hearInVideo() {
    setHearing(true);
    try {
      const text = (clip.words || []).slice(0, 40).map((w) => w[1]).join(" ");
      const r = await api.post<{ url: string }>("/voices/preview", { voice, text });
      voiceAudio.current?.pause();
      const a = new Audio(r.url);
      voiceAudio.current = a;
      const v = video?.current;
      if (v) { v.muted = true; v.currentTime = 0; v.play().catch(() => {}); }
      a.onended = () => { if (v) v.muted = false; };
      await a.play();
    } catch (e) { toast("err", (e as Error).message); } finally { setHearing(false); }
  }

  useEffect(() => {
    setHook(clip.hook_text);
    setEdits(clip.caption_edits || {});
    setTargets(clip.target_accounts?.length ? clip.target_accounts : []);
    setYtTitle(clip.meta?.youtube_title || "");
    setIgCaption(clip.meta?.instagram_caption || "");
    setTitles(clip.meta?.title_options || []);
    setEditing(null);
  }, [clip.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const fits = (clip.render_stats?.fits || {}) as Record<string, boolean>;
  const usable = accounts.filter((a) => a.active && clip.rights.platforms.includes(a.platform) && fits[a.platform] !== false);
  const rendering = clip.status === "rendering";

  async function patch(body: Record<string, unknown>, msg = "Re-rendering with your changes…") {
    setBusy(true);
    try {
      const c = await api.patch<Clip>(`/clips/${clip.id}`, body);
      onChanged(c);
      if (body.rerender !== false) toast("info", msg);
    } catch (e) {
      toast("err", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const scores = clip.moment?.scores || {};
  const failed = clip.qc.filter((q) => !q.passed);
  const words = clip.words || [];
  const stats = clip.render_stats || {};

  return (
    <div className="flex h-full min-h-0 flex-col">
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={clip.status} />
          <RightsBadge rights={clip.rights} />
          <span className="chip">Variant {clip.variant}</span>
          <span className="chip">{fmt.dur(clip.duration_s)}</span>
        </div>
        <h2 className="mt-3 text-lg font-semibold leading-snug">{clip.moment?.title || clip.hook_text}</h2>
        <div className="mt-1 text-xs text-dim">
          from <Link className="hover:underline" to={`/videos/${clip.video_id}`}>{clip.video_title}</Link> · {clip.source_name}
        </div>
        {clip.siblings && clip.siblings.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {clip.siblings.map((s) => (
              <Link key={s.id} to={`/clips/${s.id}`} className="chip hover:text-ink">Variant {s.variant} · {s.status}</Link>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl bg-raised px-3 py-2.5">
          <div className="text-[11px] uppercase tracking-wide text-mute">Predicted</div>
          <div className="mt-0.5 text-xl font-semibold">{clip.predicted_score.toFixed(1)}<span className="text-sm text-mute">/10</span></div>
          {clip.predicted_views ? <div className="text-xs text-dim">≈ {fmt.n(clip.predicted_views)} views in 72h</div> : null}
        </div>
        <div className="rounded-xl bg-raised px-3 py-2.5">
          <div className="text-[11px] uppercase tracking-wide text-mute">Edit</div>
          <div className="mt-0.5 text-sm">{fmt.label(stats.preset || clip.edit_preset || "clean")} · {clip.caption_style}</div>
          <div className="text-xs text-dim">{stats.framings ?? 0} reframes · {stats.zooms ?? 0} punch-ins · {(stats.cards || []).length} cards</div>
        </div>
      </div>

      <div className="sticky -top-5 z-10 -mx-5 flex gap-1 border-b border-line bg-surface px-5 pt-1">
        {([["text", "Text"], ["edit", "Edit"], ["post", "Post"], ["checks", "Checks"]] as const).map(([k, l]) => (
          <button key={k} type="button" onClick={() => setTab(k)}
            className={`relative -mb-px border-b-2 px-3 py-2 text-sm ${tab === k ? "border-brand text-ink" : "border-transparent text-dim hover:text-ink"}`}>
            {l}{k === "checks" && failed.length > 0 && <span className="absolute right-1 top-1.5 h-1.5 w-1.5 rounded-full bg-bad" />}
          </button>
        ))}
      </div>

      {tab === "text" && <>
      <div>
        <span className="label flex items-center justify-between"><span>Hook (opening text)</span>
          {live?.live && (
            <span className="flex rounded-lg border border-line p-0.5 normal-case tracking-normal">
              {([["native", "Native"], ["bold", "Bold"], ["dark", "Dark"], ["none", "Off"]] as const).map(([k, l]) => {
                const cur = live.edits.hook_style ?? clip.overlay_spec?.hook_style ?? "native";
                return (
                  <button key={k} type="button" onClick={() => live.update({ hook_style: k })}
                    className={`rounded-md px-2 py-0.5 text-[11px] ${cur === k ? "bg-hover text-ink" : "text-dim hover:text-ink"}`}>{l}</button>
                );
              })}
            </span>
          )}
        </span>
        {live?.live ? (
          <input className="input" value={live.edits.hook ?? clip.hook_text} onChange={(e) => live.update({ hook: e.target.value })} />
        ) : (
          <div className="flex gap-2">
            <input className="input" value={hook} onChange={(e) => setHook(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && hook !== clip.hook_text && patch({ hook_text: hook })} />
            <button className="btn-outline shrink-0" disabled={busy || hook === clip.hook_text || rendering} onClick={() => patch({ hook_text: hook })}>Apply</button>
          </div>
        )}
        {clip.moment?.hooks && clip.moment.hooks.length > 1 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {clip.moment.hooks.filter((h) => h.text !== (live?.edits.hook ?? hook)).map((h) => (
              <button key={h.text} className="chip hover:text-ink" onClick={() => live?.live ? live.update({ hook: h.text }) : setHook(h.text)} title={h.style}>{h.text}</button>
            ))}
          </div>
        )}
      </div>

      {live?.live && (
        <div className="space-y-3 rounded-xl border border-line p-3">
          <div className="flex items-center justify-between">
            <span className="label !mb-0">Text & captions · instant</span>
            <span className="text-[11px] text-mute">{live.saving ? "saving…" : clip.needs_burn ? "baked in when you approve" : "saved"}</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label><span className="label">Caption style</span>
              <select className="input" value={live.edits.caption_style ?? clip.caption_style} onChange={(e) => live.update({ caption_style: e.target.value })}>
                {CAPTION_STYLES.map((c) => <option key={c} value={c}>{fmt.label(c)}</option>)}
              </select></label>
            <label><span className="label">Caption size</span>
              <input type="range" min={0.7} max={1.4} step={0.05} className="w-full accent-[var(--brand)]" value={live.edits.caption_scale ?? 1}
                onChange={(e) => live.update({ caption_scale: Number(e.target.value) })} /></label>
          </div>
          <label className="block"><span className="label flex justify-between"><span>Caption position</span>
            <button type="button" className="text-[11px] normal-case text-dim hover:text-ink" onClick={() => live.update({ caption_y: null })}>auto (follows face)</button></span>
            <input type="range" min={0.12} max={0.86} step={0.01} className="w-full accent-[var(--brand)]" value={live.edits.caption_y ?? 0.68}
              onChange={(e) => live.update({ caption_y: Number(e.target.value) })} />
            <span className="text-[11px] text-mute">Tip: drag the captions up or down on the video.</span></label>
          <label><span className="label">Title at the top</span>
            <input className="input" value={live.edits.headline ?? clip.overlay_spec?.headline ?? ""} onChange={(e) => live.update({ headline: e.target.value })} placeholder="(none)" /></label>
          <label><span className="label">End call to action</span>
            <input className="input" value={live.edits.cta ?? clip.overlay_spec?.cta ?? ""} onChange={(e) => live.update({ cta: e.target.value })} /></label>
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <Toggle checked={(live.edits.progress_bar ?? clip.overlay_spec?.progress_bar) !== false} onChange={(v) => live.update({ progress_bar: v })} label="Progress bar" />
            <Toggle checked={live.edits.emoji !== false} onChange={(v) => live.update({ emoji: v })} label="Emoji" />
            <Toggle checked={(live.edits.end_card ?? clip.overlay_spec?.end_card) !== false} onChange={(v) => live.update({ end_card: v })} label="End card" />
            <Toggle checked={!live.edits.hide_captions} onChange={(v) => live.update({ hide_captions: !v })} label="Captions" />
          </div>
          <label className="flex items-center gap-2 text-sm"><span className="label !mb-0">Brand colour</span>
            <input type="color" className="h-7 w-12 rounded border border-line bg-transparent" value={live.edits.brand ?? clip.overlay_spec?.brand ?? "#FFD400"} onChange={(e) => live.update({ brand: e.target.value })} /></label>
        </div>
      )}

      </>}

      {tab === "edit" && <>
      <p className="-mb-2 text-[11px] text-mute">These re-cut the footage from the clip's master (about 15 seconds). Text stays editable.</p>
      <div className="grid grid-cols-2 gap-3">
        <label><span className="label">Edit style</span>
          <select className="input" value={clip.edit_preset || "clean"} disabled={rendering} onChange={(e) => patch({ edit_preset: e.target.value })}>
            {PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select></label>
        {!live?.live && <label><span className="label">Captions</span>
          <select className="input" value={clip.caption_style} disabled={rendering} onChange={(e) => patch({ caption_style: e.target.value })}>
            {CAPTION_STYLES.map((c) => <option key={c} value={c}>{fmt.label(c)}</option>)}
          </select></label>}
        <label><span className="label">Opening</span>
          <select className="input" value={clip.opening} disabled={rendering} onChange={(e) => patch({ opening: e.target.value })}>
            {OPENINGS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </select></label>
        <label><span className="label">Layout</span>
          <select className="input" value={clip.meta?.layout_override || "auto"} disabled={rendering} onChange={(e) => patch({ layout: e.target.value })}>
            {["auto", "follow", "split", "screencast", "fit"].map((l) => <option key={l} value={l}>{l === "auto" ? "Auto (follow speaker)" : fmt.label(l)}</option>)}
          </select></label>
      </div>

      {clip.faceless && (
        <div>
          <span className="label">Voice</span>
          <VoicePicker value={voice} onChange={setVoice} />
          <button className="btn-ghost mt-2 w-full" disabled={hearing} onClick={hearInVideo}>
            {hearing ? "Preparing…" : "▶ Hear this voice in the video"}
          </button>
          <button className="btn-outline mt-1 w-full" disabled={rendering || voice === clip.voice}
            onClick={async () => { await api.post(`/videos/${clip.video_id}/revoice`, { voice }); toast("info", "Re-voicing: same script and scenes, new voice. Takes a few minutes."); onChanged({ ...clip, status: "rendering" }); }}>
            Re-voice this video
          </button>
        </div>
      )}

      <div>
        <span className="label flex items-center gap-1"><Scissors size={12} /> Trim</span>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-dim">Start</span>
          <button className="btn-outline !px-2 !py-1" disabled={rendering} onClick={() => patch({ trim_start: (clip.meta?.trim?.start || 0) - 1 })}>−1s</button>
          <button className="btn-outline !px-2 !py-1" disabled={rendering} onClick={() => patch({ trim_start: (clip.meta?.trim?.start || 0) + 1 })}>+1s</button>
          <span className="ml-3 text-dim">End</span>
          <button className="btn-outline !px-2 !py-1" disabled={rendering} onClick={() => patch({ trim_end: (clip.meta?.trim?.end || 0) - 1 })}>−1s</button>
          <button className="btn-outline !px-2 !py-1" disabled={rendering} onClick={() => patch({ trim_end: (clip.meta?.trim?.end || 0) + 1 })}>+1s</button>
          {clip.meta?.trim && <span className="text-xs text-mute">({clip.meta.trim.start || 0}s / {clip.meta.trim.end || 0}s)</span>}
        </div>
      </div>
      {clip.faceless && <Link to={`/faceless/${clip.video_id}`} className="btn-outline w-full">Open the storyboard (edit scenes, redraw pictures)</Link>}
      </>}

      {tab === "text" && <div>
        <button className="label flex items-center gap-1 hover:text-ink" onClick={() => setShowWords(!showWords)}>
          Captions text {showWords ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>
        {showWords && (
          <div className="rounded-xl border border-line p-3">
            <p className="text-sm leading-7">
              {words.map(([idx, text]) => {
                const key = String(idx);
                const shown = edits[key] ?? text;
                return editing === idx ? (
                  <input key={idx} autoFocus className="input !inline-block !w-28 !px-1.5 !py-0.5" defaultValue={shown}
                    onBlur={(e) => {
                      const next = { ...edits, [key]: e.target.value };
                      setEdits(next); setEditing(null);
                      if (live?.live) live.update({ caption_edits: next });
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
                ) : (
                  <button key={idx} onClick={() => setEditing(idx)}
                    className={`mr-1 rounded px-0.5 hover:bg-hover ${edits[key] !== undefined ? "text-brand underline" : ""}`}>{shown}</button>
                );
              })}
            </p>
            <div className="mt-2 flex justify-end">
              {live?.live ? <span className="text-[11px] text-mute">Edits show instantly in the preview.</span> : (
                <button className="btn-outline !py-1 text-xs" disabled={busy || rendering} onClick={() => patch({ caption_edits: edits })}>
                  <RefreshCw size={12} /> Re-render captions
                </button>)}
            </div>
          </div>
        )}
      </div>}

      {tab === "post" && <>
      <div className="grid gap-3">
        <label><span className="label flex items-center justify-between"><span>YouTube title</span>
          <button type="button" className="text-[11px] normal-case text-dim hover:text-ink disabled:opacity-50" disabled={titling} onClick={suggestTitles}>
            {titling ? "writing 10 options…" : "✨ Suggest 10 titles"}</button></span>
          <input className="input" value={ytTitle} onChange={(e) => setYtTitle(e.target.value)} onBlur={() => ytTitle !== (clip.meta?.youtube_title || "") && patch({ youtube_title: ytTitle, rerender: false })} /></label>
        {titles.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {titles.map((t) => (
              <button key={t} type="button" className={`chip text-left hover:text-ink ${t === ytTitle ? "!border-brand text-ink" : ""}`}
                onClick={() => { setYtTitle(t); patch({ youtube_title: t, rerender: false }); }}>{t}</button>
            ))}
          </div>
        )}
        <label><span className="label">Instagram caption</span>
          <textarea className="input min-h-[84px]" value={igCaption} onChange={(e) => setIgCaption(e.target.value)} onBlur={() => igCaption !== (clip.meta?.instagram_caption || "") && patch({ instagram_caption: igCaption, rerender: false })} /></label>
      </div>

      <div>
        <span className="label">Post to</span>
        {usable.length ? (
          <div className="flex flex-wrap gap-2">
            {usable.map((a) => {
              const on = targets.includes(a.id);
              return (
                <button key={a.id} onClick={() => setTargets(on ? targets.filter((x) => x !== a.id) : [...targets, a.id])}
                  className={`chip !py-1.5 ${on ? "!border-brand text-ink" : ""}`}>
                  <Platform p={a.platform} size={13} /> {a.name} {on && <Check size={12} />}
                </button>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-dim">No connected pages can take this clip yet. <Link className="underline" to="/pages">Connect pages</Link>.</p>
        )}
        <p className="mt-1.5 text-xs text-mute">Nothing selected = every matching page. Each page gets a different variant where possible.</p>
        <div className="mt-3 flex items-center gap-2">
          <input type="datetime-local" className="input !w-auto" value={when} onChange={(e) => setWhen(e.target.value)} />
          <span className="text-xs text-mute">{when ? "custom time" : "or leave empty for the page's best slot"}</span>
        </div>
      </div>
      </>}

      {tab === "checks" && <>
      {clip.moment?.reason && <p className="rounded-xl border border-line px-3 py-2.5 text-sm text-dim">“{clip.moment.reason}”</p>}
      <div>
        <span className="label">Quality checks</span>
        <ul className="space-y-1.5 text-sm">
          {clip.qc.map((q) => (
            <li key={q.name} className="flex items-start gap-2">
              {q.passed ? <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-good" /> : <XCircle size={15} className="mt-0.5 shrink-0 text-bad" />}
              <span><span className="font-medium">{fmt.label(q.name)}</span> <span className="text-dim">— {q.detail}</span></span>
            </li>
          ))}
          {rendering && <li className="flex items-center gap-2 text-dim"><Loader2 size={14} className="animate-spin" /> rendering…</li>}
        </ul>
        {failed.length > 0 && <p className="mt-2 text-xs text-mute">Failed clips never reach review automatically; you're seeing this one because you opened it.</p>}
      </div>

      {Object.keys(scores).length > 0 && (
        <div>
          <span className="label">Why it was picked (local LLM scores)</span>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            {Object.entries(scores).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-2"><span className="text-dim">{fmt.label(k)}</span><Score value={Number(v)} /></div>
            ))}
          </div>
        </div>
      )}
      </>}
    </div>
      <div className="flex shrink-0 gap-2 border-t border-line bg-surface px-5 py-3">
        <button className="btn-primary flex-1" disabled={rendering || !clip.rights.ok} onClick={() => onApprove([clip.id], targets, when ? new Date(when).toISOString() : undefined)}>
          <Check size={16} /> Approve <kbd>A</kbd>
        </button>
        <button className="btn-danger" onClick={() => onReject([clip.id])}><X size={16} /> Reject <kbd>R</kbd></button>
        <button className="btn-ghost" title="Render again with the latest editor" disabled={rendering}
          onClick={async () => { onChanged(await api.post<Clip>(`/clips/${clip.id}/rerender`)); toast("info", "Re-rendering…"); }}><RefreshCw size={16} /></button>
      </div>
    </div>
  );
}

export function useAccounts() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  useEffect(() => { api.get<Account[]>("/accounts").then(setAccounts).catch(() => {}); }, []);
  return useMemo(() => accounts, [accounts]);
}
