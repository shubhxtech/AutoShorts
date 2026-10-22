import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, CalendarClock, CheckCircle2, Clapperboard, MessageSquare, Wand2, X,
} from "lucide-react";
import { api, fmt, type Account, type EventItem, type Post, type Summary, type Video } from "../api";
import { useApi } from "../hooks";
import { PasteLink } from "../components/PasteLink";
import { PipelineSteps } from "../components/charts";
import { Card, LevelIcon, Page, Platform, StatusPill } from "../components/ui";

const BUSY = "queued,downloading,transcribing,analyzing,mining,rendering";

type Insights = { funnel: { label: string; value: number; note?: string }[]; pass_rate: number; avg_duration: number };

/** Home: start something, see what needs you, what's running, and what's posting next. Charts live in Analytics. */
export default function Today({ summary }: { summary: Summary | null }) {
  const { data: events, reload: reloadEvents } = useApi<EventItem[]>("/events?limit=6", 10000);
  const { data: working, reload } = useApi<Video[]>(`/videos?status=${BUSY}`, 4000);
  const { data: posts } = useApi<Post[]>("/posts?days=14", 15000);
  const { data: accounts } = useApi<Account[]>("/accounts");
  const { data: ins } = useApi<Insights>("/insights?days=14", 30000);
  const s = summary;
  const next = (posts || []).filter((p) => p.status === "scheduled")
    .sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at)).slice(0, 4);
  const needs = [
    s?.review ? { to: "/review", icon: Clapperboard, text: `${s.review} clip${s.review > 1 ? "s" : ""} to review`, tone: "brand" } : null,
    s?.replies_waiting ? { to: "/comments", icon: MessageSquare, text: `${s.replies_waiting} repl${s.replies_waiting > 1 ? "ies" : "y"} to approve`, tone: "plain" } : null,
    s?.failed_posts ? { to: "/schedule", icon: AlertTriangle, text: `${s.failed_posts} post${s.failed_posts > 1 ? "s" : ""} failed`, tone: "bad" } : null,
  ].filter(Boolean) as { to: string; icon: typeof Clapperboard; text: string; tone: string }[];

  return (
    <Page title="Today" subtitle={summaryLine(s)}
      actions={<>
        <Link to="/faceless" className="btn-ghost"><Wand2 size={15} /> Faceless video</Link>
        {s?.review ? <Link to="/review" className="btn-primary"><Clapperboard size={16} /> Review {s.review} <ArrowRight size={14} /></Link> : null}
      </>}>
      <PasteLink onQueued={reload} />

      {/* what needs you */}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        {needs.length ? needs.map((n) => (
          <Link key={n.to} to={n.to} className={`inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm transition-colors ${
            n.tone === "brand" ? "border-brand/40 bg-brand/10 text-ink hover:bg-brand/20" : n.tone === "bad" ? "border-bad/40 bg-bad/10 text-bad hover:bg-bad/20" : "border-line hover:bg-hover"}`}>
            <n.icon size={15} /> {n.text} <ArrowRight size={13} className="opacity-60" />
          </Link>
        )) : s && (
          <span className="inline-flex items-center gap-2 text-sm text-dim"><CheckCircle2 size={15} className="text-good" /> All caught up. Nothing needs you right now.</span>
        )}
      </div>

      {/* in progress */}
      {!!working?.length && (
        <Card title={`In progress · ${working.length}`} className="mt-5" pad={false}>
          <ul className="divide-y divide-line">
            {working.map((v) => (
              <li key={v.id} className="flex items-center gap-4 px-5 py-3">
                {v.thumbnail ? <img src={v.thumbnail} className="h-10 w-16 rounded-md object-cover" alt="" /> : <div className="h-10 w-16 rounded-md bg-hover" />}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Link to={v.external_id?.startsWith("faceless:") ? `/faceless/${v.id}` : `/videos/${v.id}`} className="truncate text-sm font-medium hover:underline">{v.title || "Fetching video…"}</Link>
                    <StatusPill status={v.status} />
                  </div>
                  <div className="mt-1.5 flex items-center gap-3">
                    <div className="h-1 flex-1 overflow-hidden rounded-full bg-hover">
                      <div className="h-full rounded-full bg-brand transition-[width]" style={{ width: `${Math.round((v.progress || 0) * 100)}%` }} />
                    </div>
                    <span className="w-64 shrink-0 truncate text-xs text-mute">{v.stage_message}</span>
                  </div>
                </div>
                <button className="btn-ghost !p-1.5 text-mute hover:text-bad" title="Cancel" aria-label="Cancel"
                  onClick={async () => { await api.post(`/videos/${v.id}/cancel`); reload(); }}><X size={15} /></button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* up next */}
        <Card title="Up next" pad={false} actions={<Link to="/schedule" className="text-xs text-dim hover:text-ink">Schedule →</Link>}>
          {next.length ? (
            <ul className="divide-y divide-line">
              {next.map((p) => (
                <li key={p.id} className="flex items-center gap-3 px-5 py-2.5">
                  {p.thumb_url ? <img src={p.thumb_url} className="h-12 w-[27px] rounded object-cover" alt="" /> : <div className="h-12 w-[27px] rounded bg-hover" />}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm">{p.title || p.hook_text}</div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-xs text-dim"><Platform p={p.platform} size={12} /> {p.account_name}</div>
                  </div>
                  <div className="shrink-0 text-right text-xs text-dim">{whenLabel(p.scheduled_at)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="flex items-start gap-3 px-5 py-5 text-sm text-dim">
              <CalendarClock size={18} className="mt-0.5 shrink-0 text-mute" />
              {accounts && !accounts.length
                ? <span>Nothing is scheduled. <Link to="/pages" className="text-ink underline">Connect a YouTube or Instagram page</Link> and approved clips go out at its best times.</span>
                : <span>Nothing is scheduled. Approve clips in <Link to="/review" className="text-ink underline">Review</Link> and they're placed into your pages' best time slots.</span>}
            </div>
          )}
        </Card>

        {/* recent activity */}
        <Card title="Recent activity" pad={false}
          actions={events?.some((e) => !e.read) ? <button className="text-xs text-dim hover:text-ink" onClick={async () => { await api.post("/events/read"); reloadEvents(); }}>Mark read</button> : null}>
          {!events?.length ? (
            <p className="px-5 py-5 text-sm text-dim">Paste a video link above. AutoShorts finds the best moments, edits them into vertical clips and checks them before anything reaches you.</p>
          ) : (
            <ul className="divide-y divide-line">
              {events.map((e) => (
                <li key={e.id} className="flex items-start gap-3 px-5 py-2.5">
                  <div className="pt-0.5"><LevelIcon level={e.level} /></div>
                  <div className="min-w-0 flex-1">
                    <div className={`truncate text-sm ${e.read ? "text-dim" : "font-medium"}`}>{e.title}</div>
                    {e.body && <div className="truncate text-xs text-mute">{e.body}</div>}
                  </div>
                  <div className="shrink-0 text-xs text-mute">{fmt.ago(e.created_at)}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* the last two weeks, as one line */}
      {!!ins?.funnel?.[0]?.value && (
        <Card title="Last 14 days" className="mt-5" actions={<Link to="/analytics" className="text-xs text-dim hover:text-ink">Analytics →</Link>}>
          <PipelineSteps steps={ins.funnel.map((f) => ({ label: f.label, value: f.value }))} />
          <p className="mt-4 text-xs text-mute">{ins.pass_rate}% of rendered clips pass quality checks · average length {ins.avg_duration}s</p>
        </Card>
      )}
    </Page>
  );
}

function summaryLine(s: Summary | null) {
  if (!s) return "Loading…";
  const parts = [];
  if (s.processing) parts.push(`${s.processing} video${s.processing > 1 ? "s" : ""} processing`);
  if (s.generated) parts.push(`${s.generated} clips made in the last 24 h`);
  if (s.scheduled) parts.push(`${s.scheduled} scheduled`);
  if (s.published_today) parts.push(`${s.published_today} posted today`);
  if (s.views_24h) parts.push(`${fmt.n(s.views_24h)} views from today's posts`);
  return parts.length ? parts.join(" · ") : "Paste a link to make clips, or start a faceless video.";
}

function whenLabel(iso: string) {
  const d = new Date(iso);
  const now = new Date();
  const days = Math.round((new Date(d.toDateString()).getTime() - new Date(now.toDateString()).getTime()) / 86400000);
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return days === 0 ? `Today ${time}` : days === 1 ? `Tomorrow ${time}` : `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}
