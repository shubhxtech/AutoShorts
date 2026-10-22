import { fmt } from "../api";
import { useApi } from "../hooks";
import { BarList, Columns, PipelineSteps, TrendChart } from "./charts";
import { Card } from "./ui";

type I = {
  funnel: { label: string; value: number; note?: string }[]; qc_failures: { label: string; value: number }[];
  score_bins: { label: string; value: number }[]; per_day: { day: string; clips: number; passed: number }[];
  edit_mix: { label: string; value: number }[]; structures: { label: string; value: number }[];
  avg_duration: number; pass_rate: number;
};

/** Pipeline charts that are useful before anything has been posted. */
export function Insights({ days = 14 }: { days?: number }) {
  const { data } = useApi<I>(`/insights?days=${days}`, 30000);
  if (!data || !data.funnel[0].value) return null;
  const passed = data.per_day.reduce((n, d) => n + d.passed, 0);
  return (
    <div className="space-y-6">
      <Card title={`Pipeline · last ${days} days`}>
        <PipelineSteps steps={data.funnel.map((f) => ({ label: f.label, value: f.value }))} />
        <p className="mt-4 text-xs text-mute">Arrows show the share that made it past the previous step. {data.pass_rate}% of rendered clips pass quality checks · average length {data.avg_duration}s.</p>
      </Card>
      <div className="grid gap-6 lg:grid-cols-2 xl:grid-cols-3">
        <Card title="Predicted clip scores"><Columns data={data.score_bins} format={(v) => `${v} clips`} />
          <p className="mt-2 text-xs text-mute">How strong the local model thinks each clip is, 0 to 10.</p></Card>
        <Card title="Why clips fail checks"><BarList data={data.qc_failures.map((q) => ({ label: fmt.label(q.label), value: q.value }))} empty="Nothing failing" /></Card>
        <Card title="Clips that passed, per day">{data.per_day.length >= 3
          ? <TrendChart data={data.per_day.map((d) => ({ x: d.day, y: d.passed }))} height={150} />
          : <div className="py-4"><div className="tabular text-3xl font-semibold">{passed}</div>
              <p className="mt-1 text-sm text-dim">passed so far. A daily trend appears after three days of activity.</p></div>}</Card>
        <Card title="What the clips are"><BarList data={data.structures.map((s) => ({ label: fmt.label(s.label), value: s.value }))} /></Card>
        <Card title="Edit styles that passed"><BarList data={data.edit_mix.map((s) => ({ label: fmt.label(s.label), value: s.value }))} /></Card>
      </div>
    </div>
  );
}
