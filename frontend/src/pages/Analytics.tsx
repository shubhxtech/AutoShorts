import { useState } from "react";
import { BarChart3, ExternalLink } from "lucide-react";
import { fmt, type Post } from "../api";
import { useApi } from "../hooks";
import { BarList, TrendChart } from "../components/charts";
import { Card, Empty, Page, Stat } from "../components/ui";
import { Insights } from "../components/Insights";

type Group = Record<string, { posts: number; views: number; median_72h: number | null; followers: number; earnings: number; avg_view_pct: number | null }>;
type A = {
  totals: { posts: number; views: number; followers: number; earnings: number };
  by_page: Group; by_source: Group; by_hook_style: Group; by_caption_style: Group; by_length: Group; by_hour: Group; by_opening: Group;
  series: { day: string; views: number }[]; top: Post[]; losers: Post[];
};

function bars(g: Group, key: "median_72h" | "views" = "median_72h") {
  return Object.entries(g || {})
    .map(([label, v]) => ({ label: fmt.label(label), value: (key === "median_72h" ? v.median_72h ?? 0 : v.views), note: `${v.posts} posts · ${fmt.n(v.views)} views${v.avg_view_pct ? ` · ${v.avg_view_pct}% watched` : ""}` }))
    .sort((a, b) => b.value - a.value);
}

function PostRow({ p }: { p: Post }) {
  return (
    <li className="flex items-center gap-3 py-2">
      {p.thumb_url ? <img src={p.thumb_url} className="h-12 w-7 rounded object-cover" alt="" /> : <div className="h-12 w-7 rounded bg-hover" />}
      <div className="min-w-0 flex-1"><div className="truncate text-sm">{p.title || p.caption?.split("\n")[0] || p.hook_text}</div><div className="text-xs text-mute">{p.account_name} · {fmt.ago(p.published_at)}</div></div>
      <div className="tabular text-sm">{fmt.n(p.views)}</div>
      {p.url && <a href={p.url} target="_blank" rel="noreferrer" className="text-mute hover:text-ink"><ExternalLink size={14} /></a>}
    </li>
  );
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const { data } = useApi<A>(`/analytics?days=${days}`, 60000);
  const t = data?.totals;
  return (
    <Page title="Analytics" subtitle="What's working, by page, source, hook style, caption style, length and posting hour. Bars show median views at 72 hours."
      actions={<div className="flex rounded-xl border border-line p-0.5">{[7, 30, 90].map((d) => (
        <button key={d} onClick={() => setDays(d)} className={`rounded-lg px-3 py-1.5 text-sm ${days === d ? "bg-hover text-ink" : "text-dim"}`}>{d} days</button>))}</div>}>
      <div className="mb-6"><Insights days={days} /></div>
      {!t?.posts ? (
        <Empty icon={<BarChart3 size={28} />} title="No published clips yet">Views, retention and earnings charts show up here once posts go live. Metrics refresh every hour.</Empty>
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Posts" value={t.posts} /><Stat label="Views" value={fmt.n(t.views)} />
            <Stat label="Followers gained" value={fmt.n(t.followers)} /><Stat label="Earnings" value={fmt.money(t.earnings)} hint="campaign payouts (est.)" />
          </div>
          <Card title="Views of new posts per day"><TrendChart data={(data?.series || []).map((s) => ({ x: s.day, y: s.views }))} format={fmt.n} /></Card>
          <div className="grid gap-6 lg:grid-cols-2 xl:grid-cols-3">
            <Card title="By page"><BarList data={bars(data!.by_page)} format={fmt.n} /></Card>
            <Card title="By hook style"><BarList data={bars(data!.by_hook_style)} format={fmt.n} /></Card>
            <Card title="By caption style"><BarList data={bars(data!.by_caption_style)} format={fmt.n} /></Card>
            <Card title="By length"><BarList data={bars(data!.by_length)} format={fmt.n} /></Card>
            <Card title="By opening"><BarList data={bars(data!.by_opening)} format={fmt.n} /></Card>
            <Card title="By posting hour"><BarList data={bars(data!.by_hour).sort((a, b) => Number(a.label) - Number(b.label)).map((b) => ({ ...b, label: `${b.label}:00` }))} format={fmt.n} /></Card>
            <Card title="By source"><BarList data={bars(data!.by_source)} format={fmt.n} /></Card>
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Top clips"><ul className="divide-y divide-line">{data!.top.map((p) => <PostRow key={p.id} p={p} />)}</ul></Card>
            <Card title="Lowest 72h views"><ul className="divide-y divide-line">{data!.losers.map((p) => <PostRow key={p.id} p={p} />)}</ul></Card>
          </div>
        </div>
      )}
    </Page>
  );
}
