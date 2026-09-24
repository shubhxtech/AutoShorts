import { useState } from "react";
import { AlertTriangle, Check, MessageSquare, RefreshCw, X } from "lucide-react";
import { api, fmt, type Comment } from "../api";
import { useApi } from "../hooks";
import { Empty, Page, Platform, useToast } from "../components/ui";

export default function Comments() {
  const toast = useToast();
  const [tab, setTab] = useState("drafted,new");
  const { data, reload, setData } = useApi<Comment[]>(`/comments?status=${tab}`, 20000);
  const [sel, setSel] = useState<number[]>([]);
  const list = data || [];

  async function save(c: Comment, patch: Partial<Comment>) {
    const res = await api.patch<Comment>(`/comments/${c.id}`, patch);
    setData((d) => (d || []).map((x) => (x.id === c.id ? { ...x, ...res } : x)));
  }

  return (
    <Page title="Comments" subtitle="Comments on your own posts, sorted by the local LLM with replies drafted in each page's voice. Keyword comments get your link by DM automatically."
      actions={<>
        <div className="flex rounded-xl border border-line p-0.5">{[["drafted,new", "Waiting"], ["posted,dm_sent", "Replied"], ["hidden", "Hidden"], ["skipped", "Skipped"]].map(([k, l]) => (
          <button key={k} onClick={() => { setTab(k); setSel([]); }} className={`rounded-lg px-3 py-1.5 text-sm ${tab === k ? "bg-hover text-ink" : "text-dim"}`}>{l}</button>))}</div>
        <button className="btn-outline" onClick={async () => { await api.post("/comments/refresh"); toast("ok", "Fetching new comments"); }}><RefreshCw size={15} /> Fetch</button>
        {tab.startsWith("drafted") && <button className="btn-primary" disabled={!sel.length} onClick={async () => { await api.post("/comments/approve", { ids: sel }); toast("ok", `Posting ${sel.length} replies`); setSel([]); reload(); }}><Check size={15} /> Approve {sel.length || ""}</button>}
      </>}>
      {!list.length ? <Empty icon={<MessageSquare size={28} />} title="Inbox zero">New comments are fetched every 30 minutes from your published posts.</Empty> : (
        <div className="space-y-3">
          {list.map((c) => (
            <div key={c.id} className={`card p-4 ${c.needs_attention ? "ring-1 ring-warn" : ""}`}>
              <div className="flex items-start gap-3">
                {tab.startsWith("drafted") && <input type="checkbox" className="mt-1 h-4 w-4 accent-[var(--brand)]" checked={sel.includes(c.id)} onChange={(e) => setSel(e.target.checked ? [...sel, c.id] : sel.filter((x) => x !== c.id))} />}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-mute">
                    <Platform p={c.platform} size={12} /> {c.account_name} · <span className="font-medium text-dim">@{c.author}</span> · {fmt.ago(c.created_at)}
                    <span className="chip">{fmt.label(c.category || "other")}</span>
                    {c.needs_attention && <span className="chip text-warn"><AlertTriangle size={11} /> needs you</span>}
                  </div>
                  <p className="mt-1.5 text-sm">{c.text}</p>
                  {c.post_title && <p className="mt-1 truncate text-xs text-mute">on “{c.post_title}”</p>}
                  {tab.startsWith("drafted") ? (
                    <div className="mt-3 flex gap-2">
                      <input className="input" defaultValue={c.draft_reply} placeholder="Write a reply…" onBlur={(e) => e.target.value !== c.draft_reply && save(c, { draft_reply: e.target.value })} />
                      <button className="btn-outline shrink-0" title="Approve" onClick={() => save(c, { reply_status: "approved" }).then(() => setData((d) => (d || []).filter((x) => x.id !== c.id)))}><Check size={15} /></button>
                      <button className="btn-ghost shrink-0" title="Skip" onClick={() => save(c, { reply_status: "skipped" }).then(() => setData((d) => (d || []).filter((x) => x.id !== c.id)))}><X size={15} /></button>
                    </div>
                  ) : c.draft_reply ? <p className="mt-2 rounded-lg bg-raised px-3 py-2 text-sm text-dim">↳ {c.draft_reply}</p> : null}
                  {c.error && <p className="mt-1 text-xs text-bad">{c.error}</p>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Page>
  );
}
