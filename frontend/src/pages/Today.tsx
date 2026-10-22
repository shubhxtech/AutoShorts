import { Link } from "react-router-dom";
import { ArrowRight, Clapperboard } from "lucide-react";
import { api, fmt, type EventItem, type Summary, type Video } from "../api";
import { useApi } from "../hooks";
import { PasteLink } from "../components/PasteLink";
import { Insights } from "../components/Insights";
import { Card, Empty, LevelIcon, Page, Stat, StatusPill } from "../components/ui";

const BUSY = "queued,downloading,transcribing,analyzing,mining,rendering";

export default function Today({ summary }: { summary: Summary | null }) {
  const { data: events, reload: reloadEvents } = useApi<EventItem[]>("/events?limit=25", 10000);
  const { data: working, reload } = useApi<Video[]>(`/videos?status=${BUSY}`, 4000);
  const s = summary;

  return (
    <Page title="Today" subtitle={s ? `${s.ingested} videos ingested · ${s.generated} clips generated · ${s.passed} passed quality checks · ${s.review} waiting for you` : "Loading…"}
      actions={s?.review ? <Link to="/review" className="btn-primary"><Clapperboard size={16} /> Review {s.review} clips <ArrowRight size={14} /></Link> : null}>
      <PasteLink onQueued={reload} />

      {!!working?.length && (
        <Card title="Working on" className="mt-6" pad={false}>
          <ul className="divide-y divide-line">
            {working.map((v) => (
              <li key={v.id} className="flex items-center gap-4 px-5 py-3">
                {v.thumbnail ? <img src={v.thumbnail} className="h-12 w-20 rounded-lg object-cover" alt="" /> : <div className="h-12 w-20 rounded-lg bg-hover" />}
                <div className="min-w-0 flex-1">
                  <Link to={`/videos/${v.id}`} className="block truncate text-sm font-medium hover:underline">{v.title || "Fetching video…"}</Link>
                  <div className="mt-1 flex items-center gap-2 text-xs text-dim">
                    <StatusPill status={v.status} />
                    <span className="truncate">{v.stage_message}</span>
                  </div>
                  <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-hover">
                    <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${Math.round((v.progress || 0) * 100)}%` }} />
                  </div>
                </div>
                <button className="btn-danger !px-2.5 !py-1.5 text-xs" onClick={async () => { await api.post(`/videos/${v.id}/cancel`); reload(); }}>Cancel</button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Waiting for review" value={s?.review ?? "–"} accent hint={<Link to="/review" className="hover:underline">Open review queue →</Link>} />
        <Stat label="Scheduled" value={s?.scheduled ?? "–"} hint={<Link to="/schedule" className="hover:underline">See schedule →</Link>} />
        <Stat label="Posted today" value={s?.published_today ?? "–"} hint={s?.failed_posts ? <span className="text-bad">{s.failed_posts} failed</span> : "no failures"} />
        <Stat label="Views (24h posts)" value={fmt.n(s?.views_24h)} hint={`${fmt.n(s?.views_72h)} across the last 3 days`} />
        <Stat label="Replies waiting" value={s?.replies_waiting ?? "–"} hint={<Link to="/comments" className="hover:underline">Approve replies →</Link>} />
      </div>

      <div className="mt-6"><Insights /></div>

      <Card title="Activity" className="mt-6" pad={false}
        actions={<button className="btn-ghost !py-1 text-xs" onClick={async () => { await api.post("/events/read"); reloadEvents(); }}>Mark all read</button>}>
        {!events?.length ? (
          <div className="p-5"><Empty title="Nothing yet">Paste a video link above. AutoShorts transcribes it, finds the most viral moments, edits each one into vertical clips, and checks quality before anything reaches you.</Empty></div>
        ) : (
          <ul className="divide-y divide-line">
            {events.map((e) => (
              <li key={e.id} className={`flex gap-3 px-5 py-3 ${e.read ? "" : "bg-raised/40"}`}>
                <div className="pt-0.5"><LevelIcon level={e.level} /></div>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{e.title}</div>
                  {e.body && <div className="mt-0.5 whitespace-pre-line text-sm text-dim">{e.body}</div>}
                </div>
                <div className="shrink-0 text-xs text-mute">{fmt.ago(e.created_at)}</div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </Page>
  );
}
