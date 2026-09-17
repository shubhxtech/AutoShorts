import { useMemo } from "react";
import { CalendarClock, ExternalLink, Send, X } from "lucide-react";
import { api, fmt, type Post } from "../api";
import { useApi } from "../hooks";
import { Empty, Page, Platform, StatusPill, useToast } from "../components/ui";

export default function Schedule() {
  const toast = useToast();
  const { data, reload } = useApi<Post[]>("/posts?days=14", 10000);
  const posts = data || [];

  const days = useMemo(() => {
    const by: Record<string, Post[]> = {};
    for (const p of posts) {
      const d = new Date(p.scheduled_at).toDateString();
      (by[d] ||= []).push(p);
    }
    return Object.entries(by).sort((a, b) => new Date(a[0]).getTime() - new Date(b[0]).getTime());
  }, [posts]);

  async function act(path: string, msg: string) {
    try {
      await api.post(path);
      toast("ok", msg);
      reload();
    } catch (e) {
      toast("err", (e as Error).message);
    }
  }

  return (
    <Page title="Schedule" subtitle="Approved clips go out at each page's best times, spaced apart, with platform-specific titles, captions and credit.">
      {!posts.length ? (
        <Empty icon={<CalendarClock size={28} />} title="Nothing scheduled">Approve clips in Review and they'll be placed into your pages' time slots.</Empty>
      ) : (
        <div className="space-y-6">
          {days.map(([day, list]) => (
            <section key={day}>
              <h2 className="mb-2 text-sm font-semibold text-dim">{new Date(day).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}</h2>
              <div className="card divide-y divide-line">
                {list.map((p) => (
                  <div key={p.id} className="flex flex-wrap items-center gap-4 px-5 py-3">
                    <div className="tabular w-16 text-sm font-medium">{new Date(p.scheduled_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</div>
                    {p.thumb_url ? <img src={p.thumb_url} className="h-14 w-8 rounded object-cover" alt="" /> : <div className="h-14 w-8 rounded bg-hover" />}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-sm"><Platform p={p.platform} /> <span className="font-medium">{p.account_name}</span></div>
                      <div className="truncate text-sm text-dim">{p.title || p.caption.split("\n")[0] || p.hook_text}</div>
                      {p.error && <div className="truncate text-xs text-bad" title={p.error}>{p.error}</div>}
                    </div>
                    <StatusPill status={p.status} />
                    {p.status === "published" && (
                      <div className="text-right text-xs text-dim"><div className="tabular text-sm text-ink">{fmt.n(p.views)} views</div>{fmt.ago(p.published_at)}</div>
                    )}
                    <div className="flex gap-1">
                      {p.url && <a className="btn-ghost !p-2" href={p.url} target="_blank" rel="noreferrer" title="Open"><ExternalLink size={15} /></a>}
                      {(p.status === "scheduled" || p.status === "failed") && (
                        <>
                          <button className="btn-ghost !p-2" title="Post now" onClick={() => act(`/posts/${p.id}/now`, "Posting now")}><Send size={15} /></button>
                          <button className="btn-ghost !p-2" title="Cancel" onClick={() => act(`/posts/${p.id}/cancel`, "Canceled")}><X size={15} /></button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </Page>
  );
}
