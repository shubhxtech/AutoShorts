import { useEffect, useState } from "react";
import { CheckCircle2, Download, Save, Upload } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { Card, Field, Page, Toggle, useToast } from "../components/ui";

type S = Record<string, any>;
type Models = { llm: { id: string; size_gb: number; note: string; downloaded: boolean }[]; whisper: { id: string; size_gb: number; note: string; downloaded: boolean }[] };

export default function Settings() {
  const toast = useToast();
  const { data } = useApi<S>("/settings");
  const { data: models } = useApi<Models>("/models");
  const [s, setS] = useState<S>({});
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  useEffect(() => { if (data) setS(data); }, [data]);
  const set = (k: string, v: unknown) => { setS((x) => ({ ...x, [k]: v })); setDirty(true); };
  // Auto-save a moment after the last change.
  useEffect(() => {
    if (!dirty) return;
    const t = window.setTimeout(() => { save(true); }, 900);
    return () => window.clearTimeout(t);
  }, [s, dirty]); // eslint-disable-line react-hooks/exhaustive-deps
  const num = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => set(k, Number(e.target.value));
  const str = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => set(k, e.target.value);

  async function save(quiet = false) {
    try {
      const { _paths, _oauth_redirects, youtube_client_secrets, ...body } = s;
      void _paths; void _oauth_redirects; void youtube_client_secrets;
      await api.put<S>("/settings", body);
      setDirty(false);
      setSavedAt(Date.now());
      if (!quiet) toast("ok", "Settings saved");
    } catch (e) { toast("err", (e as Error).message); }
  }

  async function uploadClient(f: File | undefined) {
    if (!f) return;
    try { await api.upload("/settings/youtube-client?project=default", f); toast("ok", "Google OAuth client added. Now connect channels under Pages."); }
    catch (e) { toast("err", (e as Error).message); }
  }

  if (!data) return <Page title="Settings"><div className="text-dim">Loading…</div></Page>;
  return (
    <Page title="Settings" subtitle={`Everything runs on this Mac. Data lives in ${data._paths?.home}`}
      actions={<span className="flex items-center gap-2 text-sm text-dim">{dirty ? "Saving…" : savedAt ? <><CheckCircle2 size={15} className="text-good" /> Saved</> : "Changes save automatically"}
        <button className="btn-outline !py-1.5" onClick={() => save()}><Save size={14} /> Save now</button></span>}>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Local AI models">
          <div className="space-y-4">
            <Field label="Language model (scoring, hooks, edit plans, replies)">
              <select className="input" value={s.llm_model} onChange={(e) => { set("llm_model", e.target.value); set("llm_fast_model", e.target.value); }}>
                {models?.llm.map((m) => <option key={m.id} value={m.id}>{m.id.split("/")[1]} · {m.size_gb} GB · {m.note}{m.downloaded ? " ✓" : ""}</option>)}
              </select></Field>
            <Field label="Transcription (Whisper)">
              <select className="input" value={s.whisper_model} onChange={str("whisper_model")}>
                {models?.whisper.map((m) => <option key={m.id} value={m.id}>{m.id.split("/")[1]} · {m.size_gb} GB · {m.note}{m.downloaded ? " ✓" : ""}</option>)}
              </select></Field>
            <p className="text-xs text-mute"><Download size={11} className="inline" /> Models download automatically on first use. ✓ = already on this Mac.</p>
            <Toggle checked={!!s.llm_enabled} onChange={(v) => set("llm_enabled", v)} label="Use the local LLM (off = faster heuristics only)" />
          </div>
        </Card>

        <Card title="Finding clips">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Moments per video"><input type="number" className="input" value={s.moments_per_video} onChange={num("moments_per_video")} /></Field>
            <Field label="Variants per moment"><input type="number" className="input" value={s.variants_per_moment} onChange={num("variants_per_moment")} /></Field>
            <Field label="Shortest clip (s)"><input type="number" className="input" value={s.min_moment_s} onChange={num("min_moment_s")} /></Field>
            <Field label="Longest clip (s)"><input type="number" className="input" value={s.max_moment_s} onChange={num("max_moment_s")} /></Field>
            <Field label="Candidates the LLM scores"><input type="number" className="input" value={s.llm_candidates_per_video} onChange={num("llm_candidates_per_video")} /></Field>
            <Field label="Minimum score to keep (0-10)"><input type="number" step="0.5" className="input" value={s.min_final_score} onChange={num("min_final_score")} /></Field>
          </div>
        </Card>

        <Card title="Editing">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Edit style"><select className="input" value={s.edit_preset} onChange={str("edit_preset")}>
              {[["auto", "Auto (test styles, learn the best)"], ["clean", "Clean"], ["explainer", "Explainer (graphics on top)"], ["hype", "Hype"], ["minimal", "Minimal"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
            <Field label="Brand colour"><input type="color" className="input !h-10 !p-1" value={s.brand_color} onChange={str("brand_color")} /></Field>
            <Field label="Hook on screen (s)"><input type="number" step="0.1" className="input" value={s.hook_seconds} onChange={num("hook_seconds")} /></Field>
            <Field label="Cut pauses longer than (s)"><input type="number" step="0.05" className="input" value={s.max_pause_s} onChange={num("max_pause_s")} /></Field>
            <Field label="Caption font (optional)" hint="PostScript name, e.g. Montserrat-ExtraBold"><input className="input" value={s.caption_font} onChange={str("caption_font")} /></Field>
            <Field label="AI b-roll per clip" hint="Cutaways drawn on this Mac for things the speaker describes (~1 min each, once)">
              <select className="input" value={s.ai_broll} onChange={(e) => set("ai_broll", Number(e.target.value))}>
                <option value={0}>Off</option><option value={1}>1 cutaway</option><option value={2}>2 cutaways</option></select></Field>
            <Field label="Faceless image quality"><select className="input" value={s.faceless_quality} onChange={str("faceless_quality")}>
              <option value="best">Best (768×1344, ~1 min/scene)</option><option value="fast">Fast (576×1024, ~2× quicker)</option></select></Field>
            <Field label="Narration voice (macOS)" hint="for narrated intros"><input className="input" value={s.tts_voice} onChange={str("tts_voice")} placeholder="Samantha" /></Field>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3">
            {[["jump_cuts", "Remove pauses and filler words"], ["punch_zooms", "Punch-ins on stressed words"], ["progress_bar", "Progress bar"], ["color_grade", "Colour grade"],
              ["sfx", "Sound effects (sparing)"], ["end_card", "Credit / CTA end card"], ["narrated_intros", "Test narrated intros"], ["music_enabled", "Background music"]].map(([k, l]) => (
              <Toggle key={k} checked={!!s[k]} onChange={(v) => set(k, v)} label={l} />))}
          </div>
          {s.music_enabled && <p className="mt-3 text-xs text-mute">Put licensed tracks in {data._paths?.music}, tagged by mood in the name: chill_*, hype_*, emotional_*, suspense_*, inspiring_*, funny_*.</p>}
        </Card>

        <Card title="Quality checks">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Loudness target (LUFS)"><input type="number" className="input" value={s.target_lufs} onChange={num("target_lufs")} /></Field>
            <Field label="Shortest allowed (s)"><input type="number" className="input" value={s.min_duration} onChange={num("min_duration")} /></Field>
            <Field label="YouTube max (s)"><input type="number" className="input" value={s.max_duration_youtube} onChange={num("max_duration_youtube")} /></Field>
            <Field label="Instagram max (s)"><input type="number" className="input" value={s.max_duration_instagram} onChange={num("max_duration_instagram")} /></Field>
          </div>
        </Card>

        <Card title="Publishing">
          <div className="space-y-4">
            <Field label="Google OAuth client (client_secret.json)" hint={<>Redirect URI to register: <code>{data._oauth_redirects?.youtube}</code></>}>
              <label className="btn-outline w-full cursor-pointer"><Upload size={14} /> {Object.keys(data.youtube_client_secrets || {}).length ? <><CheckCircle2 size={14} className="text-good" /> Client added · replace</> : "Upload client_secret.json"}
                <input type="file" accept=".json" className="hidden" onChange={(e) => uploadClient(e.target.files?.[0])} /></label></Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Instagram app ID"><input className="input" value={s.instagram_app_id} onChange={str("instagram_app_id")} /></Field>
              <Field label="Instagram app secret"><input className="input" value={s.instagram_app_secret} onChange={str("instagram_app_secret")} /></Field>
              <Field label="Graph API version"><input className="input" value={s.instagram_graph_version} onChange={str("instagram_graph_version")} /></Field>
              <Field label="Instagram posts / day limit"><input type="number" className="input" value={s.instagram_daily_limit} onChange={num("instagram_daily_limit")} /></Field>
            </div>
            <Field label="Public URL (optional tunnel)" hint="Only for Instagram Login accounts if cloudflared isn't installed"><input className="input" value={s.public_base_url} onChange={str("public_base_url")} /></Field>
            <Field label="YouTube Data API key (optional, faster viral stats)"><input className="input" value={s.youtube_api_key} onChange={str("youtube_api_key")} /></Field>
            <Field label="Browser cookies for downloads (if YouTube asks you to sign in)"><select className="input" value={s.ytdlp_cookies_browser} onChange={str("ytdlp_cookies_browser")}>
              {["", "chrome", "safari", "firefox", "brave", "edge"].map((b) => <option key={b} value={b}>{b || "none"}</option>)}</select></Field>
            <Toggle checked={!!s.auto_schedule_on_approve} onChange={(v) => set("auto_schedule_on_approve", v)} label="Schedule to matching pages as soon as I approve" />
          </div>
        </Card>

        <Card title="Notifications & housekeeping">
          <div className="space-y-4">
            <Field label="Telegram bot token" hint="Create a bot with @BotFather, paste the token, then send /start to your bot"><input className="input" value={s.telegram_bot_token} onChange={str("telegram_bot_token")} /></Field>
            <Field label="Viral alert thresholds (views)"><input className="input" value={(s.viral_thresholds || []).join(", ")} onChange={(e) => set("viral_thresholds", e.target.value.split(",").map((x) => Number(x.trim())).filter(Boolean))} /></Field>
            <div className="grid grid-cols-3 gap-4">
              <Field label="Keep originals (days)"><input type="number" className="input" value={s.keep_source_days} onChange={num("keep_source_days")} /></Field>
              <Field label="Keep rejected (days)"><input type="number" className="input" value={s.keep_rejected_days} onChange={num("keep_rejected_days")} /></Field>
              <Field label="Keep published (days)"><input type="number" className="input" value={s.keep_published_days} onChange={num("keep_published_days")} /></Field>
            </div>
          </div>
        </Card>
      </div>
    </Page>
  );
}
