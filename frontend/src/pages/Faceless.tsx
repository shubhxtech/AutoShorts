import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  BookOpen, Brain, Download, FileText, HelpCircle, ListOrdered, Loader2, MessageSquareText, Quote, Sparkles, Split,
  Upload, Wand2, Zap,
} from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { Card, Field, Page, StatusPill, Toggle, useToast } from "../components/ui";
import { VoicePicker } from "../components/VoicePicker";

export type FormatInfo = { id: string; name: string; blurb: string; example: string; style: string; mood: string; seconds: number; captions: boolean };
export type Project = {
  id: number; title: string; status: string; stage_message: string; progress: number; format: string; style: string;
  voice: string; cover: string; created_at: string; clips: { id: number; status: string; variant: string }[];
};
type Options = { styles: { id: string; prompt: string }[]; voices: Record<string, string>; template: string; formats: FormatInfo[] };
type Mode = "topic" | "script" | "remix";
type Parsed = { title: string; hook: string; scenes: { narration: string; visual: string }[] };

export const FORMAT_ICON: Record<string, typeof BookOpen> = {
  story: BookOpen, countdown: ListOrdered, facts: Zap, quiz: HelpCircle, would_you_rather: Split,
  text_story: MessageSquareText, quote: Quote,
};
export const STYLE_LABEL: Record<string, string> = {
  doodle: "Doodle", cinematic: "Cinematic", anime: "Anime", "3d": "3D cartoon", realistic: "Photo", comic: "Comic",
  watercolor: "Watercolor", clay: "Claymation", noir: "Film noir", papercut: "Paper cut", vintage: "Vintage 70s",
  flat: "Flat vector", dark_fantasy: "Dark fantasy",
};
// A swatch per style so the picker is visual without needing sample renders.
const STYLE_SWATCH: Record<string, string> = {
  doodle: "linear-gradient(135deg,#fafafa,#e9e4d8)", cinematic: "linear-gradient(135deg,#0f2027,#c0743a)",
  anime: "linear-gradient(135deg,#ff9a9e,#6a82fb)", "3d": "linear-gradient(135deg,#f6d365,#fda085)",
  realistic: "linear-gradient(135deg,#606c38,#dda15e)", comic: "linear-gradient(135deg,#ffd60a,#e63946)",
  watercolor: "linear-gradient(135deg,#a8dadc,#f1faee)", clay: "linear-gradient(135deg,#e9c46a,#e76f51)",
  noir: "linear-gradient(135deg,#111,#888)", papercut: "linear-gradient(135deg,#2a9d8f,#e9c46a)",
  vintage: "linear-gradient(135deg,#b08968,#ede0d4)", flat: "linear-gradient(135deg,#4361ee,#4cc9f0)",
  dark_fantasy: "linear-gradient(135deg,#1b0036,#7b2cbf)",
};

export default function Faceless() {
  const toast = useToast();
  const nav = useNavigate();
  const { data: opts } = useApi<Options>("/faceless/options");
  const { data: projects, reload } = useApi<Project[]>("/faceless/projects", 4000);
  const [mode, setMode] = useState<Mode>("topic");
  const [format, setFormat] = useState("story");
  const [f, setF] = useState({ topic: "", notes: "", grounding: true, script_text: "", reference_url: "", style: "cinematic",
    voice: "design:documentary", seconds: 45, language: "English", niche: "", cta_keyword: "", count: 1, auto_publish: false,
    review_script: true });
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [parseErr, setParseErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const fmt = opts?.formats.find((x) => x.id === format);

  function pickFormat(id: string) {
    setFormat(id);
    const x = opts?.formats.find((o) => o.id === id);
    if (x) setF((cur) => ({ ...cur, style: x.style, seconds: x.seconds }));
  }

  useEffect(() => {
    if (mode !== "script" || !f.script_text.trim()) { setParsed(null); setParseErr(""); return; }
    const t = window.setTimeout(async () => {
      try { setParsed(await api.post<Parsed>("/faceless/parse", { text: f.script_text })); setParseErr(""); }
      catch (e) { setParsed(null); setParseErr((e as Error).message); }
    }, 400);
    return () => window.clearTimeout(t);
  }, [f.script_text, mode]);

  async function loadFile(file?: File) {
    if (!file) return;
    set("script_text", await file.text());
    toast("info", `Loaded ${file.name}`);
  }

  function downloadTemplate() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([opts?.template || ""], { type: "text/plain" }));
    a.download = "clipforge-script-template.txt";
    a.click();
  }

  const ready = mode === "topic" ? !!f.topic.trim() : mode === "script" ? !!parsed?.scenes.length : !!f.reference_url.trim();

  async function go() {
    setBusy(true);
    try {
      const body = { ...f, format, topic: mode === "topic" ? f.topic : "", script_text: mode === "script" ? f.script_text : "",
        reference_url: mode === "remix" ? f.reference_url : "", notes: mode === "topic" ? f.notes : "" };
      const r = await api.post<{ videos: number[] }>("/faceless", body);
      toast("ok", f.review_script ? "Writing the script. You'll review the storyboard before anything is drawn." : "Queued. Everything is made on this Mac.");
      reload();
      if (r.videos.length === 1) nav(`/faceless/${r.videos[0]}`);
    } catch (e) { toast("err", (e as Error).message); } finally { setBusy(false); }
  }

  const words = parsed ? parsed.scenes.reduce((n, s) => n + s.narration.split(/\s+/).length, 0) : 0;

  return (
    <Page title="Faceless studio" subtitle="Original videos with no camera: pick a format, give it a topic or your script, review the storyboard, done. Everything runs on this Mac.">
      {/* 1. Format */}
      <section className="mb-6">
        <h2 className="label mb-2">1 · Format</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
          {(opts?.formats || []).map((x) => {
            const Icon = FORMAT_ICON[x.id] || Sparkles;
            const on = x.id === format;
            return (
              <button key={x.id} type="button" onClick={() => pickFormat(x.id)}
                className={`card flex flex-col items-start gap-2 p-4 text-left transition ${on ? "ring-2 ring-brand" : "hover:bg-hover"}`}>
                <span className={`grid h-9 w-9 place-items-center rounded-xl ${on ? "bg-brand text-brand-ink" : "bg-raised text-dim"}`}><Icon size={18} /></span>
                <span className="text-sm font-semibold">{x.name}</span>
                <span className="text-xs leading-snug text-dim">{x.blurb}</span>
              </button>
            );
          })}
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-6">
          {/* 2. Content */}
          <Card title="2 · Content">
            <div className="mb-4 flex w-fit rounded-xl border border-line p-0.5 text-sm">
              {([["topic", "Topic"], ["script", "My script"], ["remix", "Remix an idea"]] as [Mode, string][]).map(([k, l]) => (
                <button key={k} type="button" onClick={() => setMode(k)} className={`rounded-lg px-3 py-1.5 ${mode === k ? "bg-hover text-ink" : "text-dim hover:text-ink"}`}>{l}</button>
              ))}
            </div>

            {mode === "topic" && (
              <div className="space-y-4">
                <Field label="Topic" hint={fmt ? <>Try: <button type="button" className="underline decoration-dotted" onClick={() => set("topic", fmt.example)}>{fmt.example}</button></> : undefined}>
                  <textarea className="input min-h-[72px]" value={f.topic} onChange={(e) => set("topic", e.target.value)} placeholder="What is the video about?" /></Field>
                <details className="rounded-xl border border-line px-3 py-2">
                  <summary className="cursor-pointer text-sm text-dim">Facts and sources</summary>
                  <div className="mt-3 space-y-3">
                    <Field label="Source notes (optional)" hint="Paste facts or an article and the script will only use these.">
                      <textarea className="input min-h-[64px]" value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
                    <Toggle checked={f.grounding} onChange={(v) => set("grounding", v)} label="Look the topic up on Wikipedia and fact-check the script" />
                  </div>
                </details>
              </div>
            )}

            {mode === "script" && (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <label className="btn-outline cursor-pointer"><Upload size={14} /> Upload .txt / .md / .json
                    <input type="file" accept=".txt,.md,.json,text/plain,application/json" className="hidden" onChange={(e) => loadFile(e.target.files?.[0])} /></label>
                  <button type="button" className="btn-ghost" onClick={downloadTemplate}><Download size={14} /> Template</button>
                  <button type="button" className="btn-ghost" onClick={() => set("script_text", opts?.template || "")}><FileText size={14} /> Example</button>
                </div>
                <textarea className="input min-h-[200px] font-mono text-xs leading-5" value={f.script_text} onChange={(e) => set("script_text", e.target.value)}
                  placeholder={"One paragraph per scene. Optional lines:\nTITLE: …   HOOK: …   CHARACTER: …   SETTING: …\n[visual: what the picture shows]"} />
                <details className="rounded-xl border border-line px-3 py-2 text-xs text-dim">
                  <summary className="cursor-pointer font-medium text-ink">Script format</summary>
                  <ul className="mt-2 list-disc space-y-1 pl-4">
                    <li><b>Plain text</b>: one paragraph per scene. Narration is read word for word.</li>
                    <li><b>Tags</b> at the top: <code>TITLE:</code> <code>HOOK:</code> (on-screen, ≤8 words) <code>CHARACTER:</code> <code>SETTING:</code>.</li>
                    <li><b>Per scene</b>: <code>[visual: …]</code> under a paragraph picks its picture; otherwise the local LLM writes one.</li>
                    <li><b>JSON</b> for quiz / countdown / would-you-rather: scenes take <code>kind</code>, <code>heading</code>, <code>number</code>, <code>options</code>, <code>answer</code>, <code>visual2</code>.</li>
                    <li>About 2.5 spoken words per second: 110 words ≈ 45 s.</li>
                  </ul>
                </details>
                {parseErr && <p className="text-xs text-bad">{parseErr}</p>}
                {parsed && (
                  <p className="text-xs text-dim"><b className="text-ink">{parsed.scenes.length}</b> scenes · <b className="text-ink">{words}</b> words ≈ {Math.round(words / 2.5)} s
                    {parsed.title && <> · {parsed.title}</>}</p>
                )}
              </div>
            )}

            {mode === "remix" && (
              <Field label="Video to learn from" hint="Only its idea and structure are used. The script, voice and visuals are new, so the result is original.">
                <input className="input" value={f.reference_url} onChange={(e) => set("reference_url", e.target.value)} placeholder="https://youtube.com/watch?v=…" /></Field>
            )}

            {mode !== "script" && (
              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                <Field label={`Length · ${f.seconds} s`}>
                  <input type="range" min={20} max={90} step={5} value={f.seconds} onChange={(e) => set("seconds", Number(e.target.value))} className="w-full accent-[var(--brand)]" /></Field>
                <Field label="Language">
                  <div className="flex w-fit rounded-xl border border-line p-0.5 text-sm">
                    {["English", "Hindi"].map((l) => (
                      <button key={l} type="button" onClick={() => set("language", l)} className={`rounded-lg px-3 py-1.5 ${f.language === l ? "bg-hover text-ink" : "text-dim hover:text-ink"}`}>{l}</button>
                    ))}
                  </div>
                </Field>
              </div>
            )}
          </Card>

          {/* 3. Look & sound */}
          <Card title="3 · Look and voice">
            <span className="label">Visual style</span>
            <div className="mb-5 mt-1 grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-7">
              {(opts?.styles || []).map((s) => (
                <button key={s.id} type="button" title={s.prompt} onClick={() => set("style", s.id)}
                  className={`overflow-hidden rounded-xl border text-left text-xs ${f.style === s.id ? "border-brand ring-1 ring-brand" : "border-line hover:border-dim"}`}>
                  <span className="block h-10" style={{ background: STYLE_SWATCH[s.id] || "var(--surface-3)" }} />
                  <span className="block truncate px-2 py-1.5">{STYLE_LABEL[s.id] || s.id}</span>
                </button>
              ))}
            </div>
            <span className="label">Voice</span>
            <VoicePicker value={f.voice} onChange={(v) => set("voice", v)} />
            <details className="mt-5 rounded-xl border border-line px-3 py-2">
              <summary className="cursor-pointer text-sm text-dim">More options</summary>
              <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Niche"><input className="input" value={f.niche} onChange={(e) => set("niche", e.target.value)} placeholder="history, finance…" /></Field>
                <Field label="Comment keyword" hint="Adds 'Comment KEYWORD' and auto-DM"><input className="input" value={f.cta_keyword} onChange={(e) => set("cta_keyword", e.target.value)} placeholder="LINK" /></Field>
                {mode !== "script" && <Field label="Versions" hint="Different scripts on the same topic"><input type="number" min={1} max={10} className="input" value={f.count} onChange={(e) => set("count", Number(e.target.value))} /></Field>}
                <div className="flex items-end pb-2"><Toggle checked={f.auto_publish} onChange={(v) => set("auto_publish", v)} label="Post when ready" /></div>
              </div>
            </details>
          </Card>

          <div className="card flex flex-wrap items-center justify-between gap-4 p-4">
            <Toggle checked={f.review_script} onChange={(v) => set("review_script", v)}
              label={<span>Review the storyboard before drawing <span className="text-mute">(fix lines first, saves minutes)</span></span>} />
            <button className="btn-primary" disabled={busy || !ready} onClick={go}>
              {busy ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />} {f.review_script ? "Write the script" : "Make the video"}</button>
          </div>
        </div>

        <Card title="Projects" pad={false}>
          {!projects?.length ? <p className="p-5 text-sm text-dim">Your faceless projects appear here. Open one to edit scenes, redraw shots or change the voice.</p> : (
            <ul className="divide-y divide-line">{projects.map((p) => <ProjectRow key={p.id} p={p} />)}</ul>
          )}
        </Card>
      </div>
    </Page>
  );
}

function ProjectRow({ p }: { p: Project }) {
  const Icon = FORMAT_ICON[p.format] || Brain;
  const busy = !["ready", "draft", "failed", "canceled", "deleted"].includes(p.status);
  return (
    <li>
      <Link to={`/faceless/${p.id}`} className="flex gap-3 p-3 hover:bg-hover">
        <div className="h-20 w-12 shrink-0 overflow-hidden rounded-lg bg-raised">
          {p.cover ? <img src={p.cover} alt="" className="h-full w-full object-cover" loading="lazy" /> : <div className="grid h-full place-items-center text-mute"><Icon size={16} /></div>}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{p.title}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-dim">
            <span className="chip"><Icon size={11} /> {p.format.replace(/_/g, " ")}</span>
            <StatusPill status={p.status} />
          </div>
          <div className="mt-1 truncate text-xs text-mute">{p.stage_message}</div>
          {busy && <div className="mt-1.5 h-1 overflow-hidden rounded bg-raised"><div className="h-full bg-brand transition-all" style={{ width: `${Math.round((p.progress || 0) * 100)}%` }} /></div>}
        </div>
      </Link>
    </li>
  );
}
