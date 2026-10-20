import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowDown, ArrowLeft, ArrowUp, Clapperboard, Copy, ImageOff, Loader2, Plus, RefreshCw, Save, Trash2, Wand2,
} from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { Card, Field, Page, StatusPill, useToast } from "../components/ui";
import { VoicePicker } from "../components/VoicePicker";
import { FORMAT_ICON, STYLE_LABEL, type Project } from "./Faceless";

type Scene = {
  narration: string; visual: string; visual2: string; kind: string; heading: string; number: number | null;
  options: string[]; answer: number | null; image?: string; image2?: string; seconds?: number | null;
};
type Board = {
  video_id: number; project: { format: string; style: string; voice: string; seed: number; language: string };
  script: { title: string; hook: string; character: string; setting: string; mood: string; youtube_title: string; instagram_caption: string };
  scenes: Scene[];
};
const KINDS: Record<string, string[]> = {
  story: ["beat"], text_story: ["intro", "beat"], quote: ["beat"], countdown: ["intro", "item", "outro"],
  facts: ["intro", "item", "outro"], quiz: ["intro", "question", "answer", "outro"], would_you_rather: ["intro", "choice", "outro"],
};
const MOODS = ["suspense", "inspiring", "emotional", "hype", "chill", "funny"];

export default function FacelessProject() {
  const { id } = useParams();
  const toast = useToast();
  const { data: board, reload } = useApi<Board>(`/faceless/${id}/storyboard`);
  const { data: projects } = useApi<Project[]>("/faceless/projects", 3000);
  const { data: opts } = useApi<{ styles: { id: string }[] }>("/faceless/options");
  const proj = projects?.find((p) => String(p.id) === id);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [meta, setMeta] = useState<Board["script"] | null>(null);
  const [style, setStyle] = useState("");
  const [voice, setVoice] = useState("");
  const [redraw, setRedraw] = useState<Set<number>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const building = !!proj && !["ready", "draft", "failed", "canceled"].includes(proj.status);

  // Load (and refresh while building so new pictures appear), unless the user has unsaved edits.
  useEffect(() => {
    if (!board || dirty) return;
    setScenes(board.scenes);
    setMeta(board.script);
    setStyle(board.project.style);
    setVoice(board.project.voice);
  }, [board]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!building) { reload(); return; }
    const t = window.setInterval(reload, 4000);
    return () => window.clearInterval(t);
  }, [building]); // eslint-disable-line react-hooks/exhaustive-deps

  const fmt = board?.project.format || "story";
  const Icon = FORMAT_ICON[fmt] || Wand2;
  const total = useMemo(() => scenes.reduce((n, s) => n + (s.seconds || s.narration.split(/\s+/).length / 2.5), 0), [scenes]);

  function edit(k: number, patch: Partial<Scene>) {
    setScenes((cur) => cur.map((s, i) => (i === k ? { ...s, ...patch } : s)));
    setDirty(true);
  }
  function move(k: number, d: number) {
    const j = k + d;
    if (j < 0 || j >= scenes.length) return;
    const next = [...scenes];
    [next[k], next[j]] = [next[j], next[k]];
    setScenes(next);
    setDirty(true);
  }
  function remove(k: number) { setScenes(scenes.filter((_, i) => i !== k)); setDirty(true); }
  function insert(k: number) {
    const base = scenes[k];
    const blank: Scene = { narration: "", visual: "", visual2: "", kind: base?.kind || "beat", heading: "", number: null, options: [], answer: null };
    setScenes([...scenes.slice(0, k + 1), blank, ...scenes.slice(k + 1)]);
    setDirty(true);
  }
  function toggleRedraw(k: number) {
    const n = new Set(redraw);
    n.has(k) ? n.delete(k) : n.add(k);
    setRedraw(n);
  }

  async function save(build: boolean) {
    setSaving(true);
    try {
      await api.put(`/faceless/${id}/storyboard`, {
        ...meta, scenes: scenes.filter((s) => s.narration.trim()), style, voice, redraw: [...redraw], build,
      });
      setDirty(false);
      setRedraw(new Set());
      reload();
      toast("ok", build ? "Rebuilding. Only changed lines are re-voiced and only changed shots redrawn." : "Saved");
    } catch (e) { toast("err", (e as Error).message); } finally { setSaving(false); }
  }

  if (!board || !meta) return <Page title="Storyboard"><Loader2 className="animate-spin text-dim" /></Page>;
  const firstClip = proj?.clips.find((c) => c.status === "review") || proj?.clips[0];

  return (
    <Page title={meta.title || "Storyboard"}
      subtitle={<span className="inline-flex flex-wrap items-center gap-2"><Link to="/faceless" className="inline-flex items-center gap-1 hover:text-ink"><ArrowLeft size={13} /> Studio</Link>
        <span className="chip"><Icon size={11} /> {fmt.replace(/_/g, " ")}</span>{proj && <StatusPill status={proj.status} />}
        <span>{scenes.length} scenes · ~{Math.round(total)} s</span>{proj?.stage_message && <span className="text-mute">{proj.stage_message}</span>}</span>}
      actions={<>
        {firstClip && <Link to={`/clips/${firstClip.id}`} className="btn-outline"><Clapperboard size={15} /> Open the edit</Link>}
        <button className="btn-outline" disabled={saving || !dirty} onClick={() => save(false)}><Save size={15} /> Save</button>
        <button className="btn-primary" disabled={saving || building} onClick={() => save(true)}>
          {saving || building ? <Loader2 size={15} className="animate-spin" /> : <Wand2 size={15} />}
          {proj?.status === "draft" ? "Build the video" : building ? "Building…" : "Save and rebuild"}{redraw.size ? ` (${redraw.size} redraw)` : ""}
        </button>
      </>}>
      {building && proj && (
        <div className="mb-4 h-1.5 overflow-hidden rounded bg-raised"><div className="h-full bg-brand transition-all" style={{ width: `${Math.round((proj.progress || 0) * 100)}%` }} /></div>
      )}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-3">
          {scenes.map((s, k) => (
            <SceneRow key={k} k={k} s={s} fmt={fmt} marked={redraw.has(k)} onEdit={(p) => edit(k, p)} onMove={(d) => move(k, d)}
              onRemove={() => remove(k)} onInsert={() => insert(k)} onRedraw={() => toggleRedraw(k)} last={k === scenes.length - 1} />
          ))}
          <button className="btn-ghost w-full border border-dashed border-line" onClick={() => insert(scenes.length - 1)}><Plus size={15} /> Add scene</button>
        </div>

        <div className="space-y-4 xl:sticky xl:top-6 xl:self-start">
          <Card title="Video">
            <div className="space-y-3">
              <Field label="On-screen hook (first 2 s)"><input className="input" value={meta.hook} onChange={(e) => { setMeta({ ...meta, hook: e.target.value }); setDirty(true); }} /></Field>
              <Field label="Title"><input className="input" value={meta.title} onChange={(e) => { setMeta({ ...meta, title: e.target.value }); setDirty(true); }} /></Field>
              {(fmt === "story" || fmt === "text_story") && <>
                <Field label="Main character" hint="Repeated in every picture for a consistent look"><textarea className="input min-h-[52px]" value={meta.character} onChange={(e) => { setMeta({ ...meta, character: e.target.value }); setDirty(true); }} /></Field>
                <Field label="World / setting"><input className="input" value={meta.setting} onChange={(e) => { setMeta({ ...meta, setting: e.target.value }); setDirty(true); }} /></Field>
              </>}
              <Field label="Music mood">
                <select className="input" value={meta.mood} onChange={(e) => { setMeta({ ...meta, mood: e.target.value }); setDirty(true); }}>
                  {MOODS.map((m) => <option key={m} value={m}>{m}</option>)}</select></Field>
            </div>
          </Card>
          <Card title="Look and voice">
            <Field label="Visual style" hint="Changing it redraws every shot">
              <select className="input" value={style} onChange={(e) => { setStyle(e.target.value); setDirty(true); }}>
                {(opts?.styles || []).map((x) => <option key={x.id} value={x.id}>{STYLE_LABEL[x.id] || x.id}</option>)}</select></Field>
            <div className="mt-3"><span className="label">Voice</span><VoicePicker value={voice} onChange={(v) => { setVoice(v); setDirty(true); }} /></div>
          </Card>
        </div>
      </div>
    </Page>
  );
}

function SceneRow({ k, s, fmt, marked, last, onEdit, onMove, onRemove, onInsert, onRedraw }: {
  k: number; s: Scene; fmt: string; marked: boolean; last: boolean; onEdit: (p: Partial<Scene>) => void; onMove: (d: number) => void;
  onRemove: () => void; onInsert: () => void; onRedraw: () => void;
}) {
  const kinds = KINDS[fmt] || ["beat"];
  const needsOptions = s.kind === "question" || s.kind === "choice";
  const nOpts = s.kind === "choice" ? 2 : 3;
  return (
    <div className={`card flex flex-col gap-4 p-4 sm:flex-row ${marked ? "ring-1 ring-brand" : ""}`}>
      <div className="flex shrink-0 gap-2 sm:flex-col">
        <Thumb src={s.image} label={`${k + 1}`} />
        {s.image2 && <Thumb src={s.image2} label="B" small />}
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {kinds.length > 1 && (
            <select className="input !w-auto !py-1 text-xs" value={s.kind} onChange={(e) => onEdit({ kind: e.target.value })}>
              {kinds.map((x) => <option key={x} value={x}>{x}</option>)}</select>
          )}
          {s.number != null && <span className="chip">#{s.number}</span>}
          {s.seconds ? <span className="text-xs text-mute">{s.seconds}s</span> : null}
          <div className="ml-auto flex items-center gap-0.5">
            <IconBtn title="Move up" onClick={() => onMove(-1)} disabled={k === 0}><ArrowUp size={14} /></IconBtn>
            <IconBtn title="Move down" onClick={() => onMove(1)} disabled={last}><ArrowDown size={14} /></IconBtn>
            <IconBtn title="Duplicate below" onClick={onInsert}><Copy size={14} /></IconBtn>
            <IconBtn title={marked ? "Keep this picture" : "Draw a new picture"} onClick={onRedraw} active={marked}><RefreshCw size={14} /></IconBtn>
            <IconBtn title="Delete scene" onClick={onRemove}><Trash2 size={14} /></IconBtn>
          </div>
        </div>
        <textarea className="input min-h-[56px] text-sm" value={s.narration} placeholder="What the narrator says"
          onChange={(e) => onEdit({ narration: e.target.value })} />
        {(s.heading || ["item", "question", "intro"].includes(s.kind) || fmt === "quote") && (
          <input className="input text-sm font-semibold" value={s.heading} placeholder={s.kind === "question" ? "Question on screen" : "On-screen text"}
            onChange={(e) => onEdit({ heading: e.target.value })} />
        )}
        {needsOptions && (
          <div className="grid gap-2 sm:grid-cols-3">
            {Array.from({ length: nOpts }).map((_, j) => (
              <label key={j} className="flex items-center gap-2">
                {s.kind === "question" && <input type="radio" checked={s.answer === j} onChange={() => onEdit({ answer: j })} title="Correct answer" />}
                <input className="input text-sm" value={s.options[j] || ""} placeholder={`${"ABC"[j]}`}
                  onChange={(e) => { const o = [...s.options]; o[j] = e.target.value; onEdit({ options: o }); }} />
              </label>
            ))}
          </div>
        )}
        <details>
          <summary className="cursor-pointer text-xs text-dim">Picture{s.visual2 || s.kind === "choice" ? "s" : ""}: <span className="text-mute">{s.visual.slice(0, 90)}</span></summary>
          <div className="mt-2 space-y-2">
            <textarea className="input min-h-[48px] text-xs" value={s.visual} placeholder="What the picture shows" onChange={(e) => onEdit({ visual: e.target.value })} />
            <textarea className="input min-h-[40px] text-xs" value={s.visual2} placeholder={s.kind === "choice" ? "Picture for option B" : "Second shot (optional): another angle or a close-up"}
              onChange={(e) => onEdit({ visual2: e.target.value })} />
          </div>
        </details>
      </div>
    </div>
  );
}

function Thumb({ src, label, small = false }: { src?: string; label: string; small?: boolean }) {
  return (
    <div className={`relative overflow-hidden rounded-lg bg-raised ${small ? "h-24 w-[54px]" : "h-36 w-[81px]"}`}>
      {src ? <img src={src} alt="" className="h-full w-full object-cover" loading="lazy" /> : <div className="grid h-full place-items-center text-mute"><ImageOff size={16} /></div>}
      <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] font-semibold text-white">{label}</span>
    </div>
  );
}

function IconBtn({ children, title, onClick, disabled, active }: { children: React.ReactNode; title: string; onClick: () => void; disabled?: boolean; active?: boolean }) {
  return (
    <button type="button" title={title} aria-label={title} onClick={onClick} disabled={disabled}
      className={`rounded-lg p-1.5 disabled:opacity-30 ${active ? "bg-brand text-brand-ink" : "text-dim hover:bg-hover hover:text-ink"}`}>{children}</button>
  );
}
