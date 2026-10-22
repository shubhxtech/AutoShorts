import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { KeyRound, Plus, Users } from "lucide-react";
import { api, fmt, type Account } from "../api";
import { useApi } from "../hooks";
import { Empty, Field, Modal, Page, Platform, StatusPill, Toggle, useToast } from "../components/ui";

const CATEGORIES = ["wants_full_episode", "praise", "question", "criticism"];

export default function Pages() {
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const { data, reload } = useApi<Account[]>("/accounts", 20000);
  const [igOpen, setIgOpen] = useState(false);
  const [token, setToken] = useState("");
  const [igId, setIgId] = useState("");
  const [edit, setEdit] = useState<Account | null>(null);

  useEffect(() => {
    const err = params.get("error");
    const ok = params.get("connected");
    if (err) toast("err", err);
    if (ok) toast("ok", `Connected ${ok} page${ok === "1" ? "" : "s"}`);
    if (err || ok) setParams({});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function connectIG() {
    try {
      await api.post("/accounts/instagram", { access_token: token, ig_user_id: igId });
      toast("ok", "Instagram connected");
      setIgOpen(false);
      setToken("");
      reload();
    } catch (e) {
      toast("err", (e as Error).message);
    }
  }

  async function saveEdit() {
    if (!edit) return;
    try {
      await api.patch(`/accounts/${edit.id}`, edit);
      toast("ok", "Saved");
      setEdit(null);
      reload();
    } catch (e) {
      toast("err", (e as Error).message);
    }
  }

  return (
    <Page title="Pages" subtitle="Your YouTube channels and Instagram Professional accounts. Each page has its own niche, voice and posting times."
      actions={
        <>
          <a className="btn-outline" href="/api/oauth/youtube/start"><Platform p="youtube" /> Connect YouTube</a>
          <button className="btn-outline" onClick={() => setIgOpen(true)}><Platform p="instagram" /> Connect Instagram</button>
        </>
      }>
      {!data?.length ? (
        <Empty icon={<Users size={28} />} title="No pages connected">
          YouTube needs a Google OAuth client (Settings → Publishing). Instagram needs a Professional account and a token from your Meta app. Posting uses only the official APIs.
        </Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.map((a) => (
            <div key={a.id} className={`card p-5 ${a.active ? "" : "opacity-50"}`}>
              <div className="flex items-center gap-3">
                {a.avatar ? <img src={a.avatar} className="h-11 w-11 rounded-full" alt="" /> : <div className="h-11 w-11 rounded-full bg-hover" />}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 font-semibold"><Platform p={a.platform} /> <span className="truncate">{a.name}</span></div>
                  <div className="truncate text-xs text-mute">{a.handle} · {a.niche || "no niche set"}</div>
                </div>
                <StatusPill status={a.status} />
              </div>
              {a.status_detail && <p className="mt-2 text-xs text-bad">{a.status_detail}</p>}
              <div className="mt-4 grid grid-cols-4 gap-2 text-center">
                {[["Posts", a.published], ["Views", fmt.n(a.views)], ["Followers", fmt.n(a.followers_gained)], ["Queued", a.scheduled]].map(([l, v]) => (
                  <div key={String(l)} className="rounded-lg bg-raised py-2"><div className="text-sm font-semibold">{v}</div><div className="text-[10px] uppercase text-mute">{l}</div></div>
                ))}
              </div>
              <div className="mt-3 text-xs text-dim">
                {a.cadence_per_day}/day at {a.time_slots.join(", ")} ({a.timezone}){a.learn_slots ? " · learning best times" : ""}
                {a.platform === "youtube" && a.quota_left !== undefined && <> · quota left today: {a.quota_left}</>}
                {a.token_expires_at && <> · token renews before {new Date(a.token_expires_at).toLocaleDateString()}</>}
              </div>
              <div className="mt-4 flex gap-2">
                <button className="btn-outline !py-1.5 text-xs" onClick={() => setEdit({ ...a })}>Settings</button>
                {a.status === "expired" && (a.platform === "youtube"
                  ? <a className="btn-primary !py-1.5 text-xs" href="/api/oauth/youtube/start">Reconnect</a>
                  : <button className="btn-primary !py-1.5 text-xs" onClick={() => setIgOpen(true)}>Reconnect</button>)}
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal open={igOpen} onClose={() => setIgOpen(false)} title="Connect Instagram">
        <div className="space-y-4 text-sm">
          <ol className="list-decimal space-y-1.5 pl-5 text-dim">
            <li>Switch the Instagram account to Professional (Creator or Business).</li>
            <li>In developers.facebook.com create an app with the Instagram product and generate a long-lived token.</li>
            <li>Facebook Login tokens upload reels straight from your computer. Instagram Login tokens need <code>cloudflared</code> installed (a temporary tunnel opens while Instagram fetches each video).</li>
          </ol>
          <Field label="Access token"><input className="input" value={token} onChange={(e) => setToken(e.target.value)} placeholder="IGAA… or EAA…" /></Field>
          <Field label="Instagram user ID (optional)"><input className="input" value={igId} onChange={(e) => setIgId(e.target.value)} /></Field>
          <div className="flex items-center justify-between">
            <a className="text-xs text-dim underline" href="/api/oauth/instagram/start">or sign in with Instagram (needs app ID + secret in Settings)</a>
            <button className="btn-primary" disabled={!token.trim()} onClick={connectIG}><KeyRound size={15} /> Connect</button>
          </div>
        </div>
      </Modal>

      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit ? `${edit.name} settings` : ""} wide>
        {edit && (
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Niche" hint="Clips from sources with the same niche go to this page"><input className="input" value={edit.niche} onChange={(e) => setEdit({ ...edit, niche: e.target.value })} /></Field>
            <Field label="Language"><input className="input" value={edit.language} onChange={(e) => setEdit({ ...edit, language: e.target.value })} /></Field>
            <Field label="Posts per day"><input type="number" min={0} className="input" value={edit.cadence_per_day} onChange={(e) => setEdit({ ...edit, cadence_per_day: Number(e.target.value) })} /></Field>
            <Field label="Time slots (local, comma separated)"><input className="input" value={edit.time_slots.join(", ")} onChange={(e) => setEdit({ ...edit, time_slots: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></Field>
            <Field label="Time zone"><input className="input" value={edit.timezone} onChange={(e) => setEdit({ ...edit, timezone: e.target.value })} /></Field>
            <Field label="Minimum gap between posts (minutes)"><input type="number" className="input" value={edit.min_gap_minutes} onChange={(e) => setEdit({ ...edit, min_gap_minutes: Number(e.target.value) })} /></Field>
            {edit.platform === "youtube" && (
              <Field label="Visibility"><select className="input" value={edit.privacy_status} onChange={(e) => setEdit({ ...edit, privacy_status: e.target.value })}>
                {["public", "unlisted", "private"].map((p) => <option key={p}>{p}</option>)}</select></Field>
            )}
            <Field label="Default hashtags"><input className="input" value={edit.default_hashtags} onChange={(e) => setEdit({ ...edit, default_hashtags: e.target.value })} placeholder="podcast, motivation" /></Field>
            <Field label="Reply voice" hint="How the local LLM writes comment replies for this page">
              <textarea className="input min-h-[70px]" value={edit.voice} onChange={(e) => setEdit({ ...edit, voice: e.target.value })} /></Field>
            <div className="space-y-3">
              <Toggle checked={edit.learn_slots} onChange={(v) => setEdit({ ...edit, learn_slots: v })} label="Learn the best posting times" />
              <Toggle checked={edit.auto_hide_spam} onChange={(v) => setEdit({ ...edit, auto_hide_spam: v })} label="Hide spam and abuse automatically" />
              <div>
                <span className="label">Auto-reply (safe categories only)</span>
                <div className="flex flex-wrap gap-2">{CATEGORIES.slice(0, 2).map((c) => {
                  const on = edit.auto_reply_categories.includes(c);
                  return <button key={c} type="button" className={`chip !py-1 ${on ? "!border-brand text-ink" : ""}`}
                    onClick={() => setEdit({ ...edit, auto_reply_categories: on ? edit.auto_reply_categories.filter((x) => x !== c) : [...edit.auto_reply_categories, c] })}>{fmt.label(c)}</button>;
                })}</div>
              </div>
              <Toggle checked={edit.active} onChange={(v) => setEdit({ ...edit, active: v })} label="Active" />
            </div>
            <div className="flex justify-between md:col-span-2">
              <button className="btn-danger" onClick={async () => { if (confirm("Disconnect this page? Scheduled posts are canceled.")) { await api.del(`/accounts/${edit.id}`); setEdit(null); reload(); } }}>Disconnect</button>
              <button className="btn-primary" onClick={saveEdit}><Plus size={15} className="hidden" /> Save</button>
            </div>
          </div>
        )}
      </Modal>
    </Page>
  );
}
