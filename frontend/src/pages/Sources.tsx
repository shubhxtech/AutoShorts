import { useState } from "react";
import { Plus, RefreshCw, ShieldCheck, Upload } from "lucide-react";
import { api, fmt, type Source } from "../api";
import { useApi } from "../hooks";
import { Empty, Field, Modal, Page, RightsBadge, Toggle, useToast } from "../components/ui";

const KINDS: Record<string, string> = {
  youtube_channel: "YouTube channel", youtube_playlist: "YouTube playlist", youtube_video: "Single video",
  rss: "Podcast RSS feed", drive: "Google Drive (shared link)", local_folder: "Folder on this computer",
  direct_url: "Direct file link", campaign: "Clipping campaign",
};
const RIGHTS: Record<string, string> = {
  own: "My own content", licensed: "Licensed / Creative Commons", creator_permission: "Creator permission",
  clipping_campaign: "Clipping campaign",
};

const BLANK: Partial<Source> = {
  name: "", kind: "youtube_channel", url: "", extra_urls: [], rights_type: "own", proof_kind: "email", proof_url: "",
  proof_notes: "", allowed_platforms: ["youtube", "instagram"], credit_text: "", credit_link: "", revenue_terms: "",
  payout_per_1k: null, expires_at: null, active: true, niche: "", language: "auto", caption_script: "native",
  watch: true, watch_interval_hours: 6, auto_ingest: true, min_viral_score: 1.5, max_video_age_days: 30,
  max_ingest_per_check: 2, min_duration_s: 300,
};

export default function Sources() {
  const toast = useToast();
  const { data, reload } = useApi<Source[]>("/sources", 15000);
  const [form, setForm] = useState<Partial<Source> | null>(null);
  const [proofFile, setProofFile] = useState<File | null>(null);

  async function save() {
    if (!form) return;
    try {
      const body = { ...BLANK, ...form, extra_urls: (form.extra_urls || []).filter(Boolean) };
      const saved = form.id ? await api.put<Source>(`/sources/${form.id}`, body) : await api.post<Source>("/sources", body);
      if (proofFile) await api.upload(`/sources/${saved.id}/proof`, proofFile);
      toast("ok", "Saved");
      setForm(null);
      setProofFile(null);
      reload();
    } catch (e) {
      toast("err", (e as Error).message);
    }
  }
  const set = (k: keyof Source, v: unknown) => setForm((f) => ({ ...(f || {}), [k]: v }));

  return (
    <Page title="Rights registry" subtitle="Every source AutoShorts may clip from, with proof of permission. Nothing is downloaded or posted without a valid entry."
      actions={<button className="btn-primary" onClick={() => setForm({ ...BLANK })}><Plus size={16} /> Add source</button>}>
      {!data?.length ? (
        <Empty icon={<ShieldCheck size={28} />} title="No sources yet">
          Links you paste on the Today page are registered here automatically. Add channels, podcast feeds or clipping campaigns to have AutoShorts watch them for new viral videos.
        </Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.map((s) => (
            <div key={s.id} className={`card p-5 ${s.active ? "" : "opacity-50"}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-semibold">{s.name}</div>
                  <div className="truncate text-xs text-mute">{KINDS[s.kind] || s.kind} · {s.url}</div>
                </div>
                <RightsBadge rights={s.rights} />
              </div>
              <dl className="mt-4 grid grid-cols-2 gap-y-1.5 text-xs">
                <dt className="text-mute">Rights</dt><dd>{RIGHTS[s.rights_type]}</dd>
                <dt className="text-mute">Platforms</dt><dd>{s.allowed_platforms.join(", ") || "none"}</dd>
                <dt className="text-mute">Credit</dt><dd className="truncate">{s.credit_text || "–"}</dd>
                <dt className="text-mute">Expires</dt><dd>{s.expires_at ? new Date(s.expires_at).toLocaleDateString() : "never"}</dd>
                <dt className="text-mute">Videos</dt><dd>{s.processed_count} processed / {s.video_count} seen</dd>
                <dt className="text-mute">Watching</dt><dd>{s.watch ? `every ${s.watch_interval_hours}h · checked ${fmt.ago(s.last_checked_at)}` : "off"}</dd>
                {s.payout_per_1k ? (<><dt className="text-mute">Payout</dt><dd>${s.payout_per_1k}/1k views</dd></>) : null}
              </dl>
              <div className="mt-4 flex gap-2">
                <button className="btn-outline !py-1.5 text-xs" onClick={() => setForm({ ...s })}>Edit</button>
                {s.watch && <button className="btn-ghost !py-1.5 text-xs" onClick={() => api.post(`/sources/${s.id}/check`).then(() => toast("ok", "Checking for new videos"))}><RefreshCw size={13} /> Check now</button>}
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal open={!!form} onClose={() => setForm(null)} title={form?.id ? "Edit source" : "Add source"} wide>
        {form && (
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Name"><input className="input" value={form.name || ""} onChange={(e) => set("name", e.target.value)} /></Field>
            <Field label="Type"><select className="input" value={form.kind} onChange={(e) => set("kind", e.target.value)}>
              {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label={form.kind === "local_folder" ? "Folder path" : "URL"}><input className="input" value={form.url || ""} onChange={(e) => set("url", e.target.value)} /></Field>
            {form.kind === "campaign" && (
              <Field label="Creator channels / asset links covered (one per line)">
                <textarea className="input min-h-[70px]" value={(form.extra_urls || []).join("\n")} onChange={(e) => set("extra_urls", e.target.value.split("\n"))} /></Field>
            )}
            <Field label="Rights type"><select className="input" value={form.rights_type} onChange={(e) => set("rights_type", e.target.value)}>
              {Object.entries(RIGHTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Proof link (email, contract, campaign page)"><input className="input" value={form.proof_url || ""} onChange={(e) => set("proof_url", e.target.value)} /></Field>
            <Field label="Proof document" hint={form.proof_file ? "A document is attached." : "PDF or screenshot of the permission"}>
              <label className="btn-outline w-full cursor-pointer"><Upload size={14} /> {proofFile ? proofFile.name : "Attach file"}
                <input type="file" className="hidden" onChange={(e) => setProofFile(e.target.files?.[0] || null)} /></label></Field>
            <Field label="Notes"><input className="input" value={form.proof_notes || ""} onChange={(e) => set("proof_notes", e.target.value)} /></Field>
            <Field label="Credit text (end card & captions)"><input className="input" value={form.credit_text || ""} onChange={(e) => set("credit_text", e.target.value)} /></Field>
            <Field label="Credit / full episode link"><input className="input" value={form.credit_link || ""} onChange={(e) => set("credit_link", e.target.value)} /></Field>
            <Field label="Rights expire on"><input type="date" className="input" value={form.expires_at ? form.expires_at.slice(0, 10) : ""} onChange={(e) => set("expires_at", e.target.value ? `${e.target.value}T23:59:00` : null)} /></Field>
            <Field label="Campaign payout ($ per 1,000 views)"><input type="number" step="0.1" className="input" value={form.payout_per_1k ?? ""} onChange={(e) => set("payout_per_1k", e.target.value ? Number(e.target.value) : null)} /></Field>
            <Field label="Allowed platforms">
              <div className="flex gap-4 pt-1">{["youtube", "instagram"].map((p) => (
                <label key={p} className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[var(--brand)]" checked={(form.allowed_platforms || []).includes(p)}
                  onChange={(e) => set("allowed_platforms", e.target.checked ? [...(form.allowed_platforms || []), p] : (form.allowed_platforms || []).filter((x) => x !== p))} />{fmt.label(p)}</label>))}</div>
            </Field>
            <Field label="Niche"><input className="input" value={form.niche || ""} onChange={(e) => set("niche", e.target.value)} /></Field>
            <Field label="Language"><select className="input" value={form.language} onChange={(e) => set("language", e.target.value)}>
              {[["auto", "Detect"], ["en", "English"], ["hi", "Hindi"], ["es", "Spanish"], ["pt", "Portuguese"], ["ar", "Arabic"]].map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Hindi captions"><select className="input" value={form.caption_script} onChange={(e) => set("caption_script", e.target.value)}>
              <option value="native">Devanagari</option><option value="latin">Hinglish (Latin script)</option></select></Field>
            <div className="space-y-3 md:col-span-2">
              <Toggle checked={!!form.watch} onChange={(v) => set("watch", v)} label="Watch for new videos" />
              {form.watch && (
                <div className="grid gap-3 md:grid-cols-4">
                  <Field label="Check every (hours)"><input type="number" className="input" value={form.watch_interval_hours} onChange={(e) => set("watch_interval_hours", Number(e.target.value))} /></Field>
                  <Field label="Only if viral score ≥" hint="views vs channel median"><input type="number" step="0.1" className="input" value={form.min_viral_score} onChange={(e) => set("min_viral_score", Number(e.target.value))} /></Field>
                  <Field label="Max age (days)"><input type="number" className="input" value={form.max_video_age_days} onChange={(e) => set("max_video_age_days", Number(e.target.value))} /></Field>
                  <Field label="Videos per check"><input type="number" className="input" value={form.max_ingest_per_check} onChange={(e) => set("max_ingest_per_check", Number(e.target.value))} /></Field>
                </div>
              )}
              <Toggle checked={!!form.auto_ingest} onChange={(v) => set("auto_ingest", v)} label="Clip new viral videos automatically" />
              <Toggle checked={!!form.active} onChange={(v) => set("active", v)} label="Active" />
            </div>
            <div className="flex justify-end gap-2 md:col-span-2">
              <button className="btn-ghost" onClick={() => setForm(null)}>Cancel</button>
              <button className="btn-primary" onClick={save}>Save</button>
            </div>
          </div>
        )}
      </Modal>
    </Page>
  );
}
