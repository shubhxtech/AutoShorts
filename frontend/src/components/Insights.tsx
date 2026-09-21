import { fmt } from "../api";
import { useApi } from "../hooks";
import { BarList, Columns, Funnel, TrendChart } from "./charts";
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
  return (
    <div className="grid gap-6 lg:grid-cols-2 xl:grid-cols-3">
      <Card title={`Pipeline (last ${days} days)`} className="lg:col-span-2 xl:col-span-2"><Funnel steps={data.funnel} />
        <p className="mt-3 text-xs text-mute">Right column: share that made it past the previous step. {data.pass_rate}% of rendered clips pass quality checks · average length {data.avg_duration}s.</p></Card>
      <Card title="Predicted clip scores"><Columns data={data.score_bins} format={(v) => `${v} clips`} />
        <p className="mt-2 text-xs text-mute">How strong the local LLM and model think each clip is (0–10).</p></Card>
      <Card title="Why clips fail quality checks"><BarList data={data.qc_failures.map((q) => ({ label: fmt.label(q.label), value: q.value }))} empty="Nothing failing" /></Card>
      <Card title="Clips that passed, per day">{data.per_day.length >= 3
        ? <TrendChart data={data.per_day.map((d) => ({ x: d.day, y: d.passed }))} height={150} />
        : <Columns data={data.per_day.map((d) => ({ label: d.day.slice(5), value: d.passed }))} format={(v) => `${v} clips`} />}</Card>
      <Card title="What the clips are"><BarList data={data.structures.map((s) => ({ label: fmt.label(s.label), value: s.value }))} />
        <div className="mt-4 border-t border-line pt-3"><div className="label">Edit styles that passed</div>
          <BarList data={data.edit_mix.map((s) => ({ label: fmt.label(s.label), value: s.value }))} /></div></Card>
    </div>
  );
}
