import { BrainCircuit, Play } from "lucide-react";
import { api, fmt } from "../api";
import { useApi } from "../hooks";
import { BarList } from "../components/charts";
import { Card, Page, useToast } from "../components/ui";

type Arm = { arm: string; win_rate: number; pulls: number };
type O = {
  bandit: Record<string, Arm[]>;
  report: { published: number; views: number; median_72h: number; best: Record<string, Arm>; best_hours: number[] };
  model: { samples?: number; r2?: number; top_features?: { feature: string; weight: number }[] };
  score_weights: Record<string, number>;
  last_learn: { at: string; matured: number; trained: boolean; samples: number; needed?: number } | null;
};

const DIMS: Record<string, string> = {
  edit_preset: "Edit style", hook_style: "Hook style", caption_style: "Caption style", opening: "Opening",
  length_bucket: "Length", slot_hour: "Posting hour",
};

export default function Optimizer() {
  const toast = useToast();
  const { data, reload } = useApi<O>("/optimizer", 60000);
  const m = data?.model;
  return (
    <Page title="Optimizer" subtitle="AutoShorts learns which choices beat each page's median views and makes more of them, while still testing alternatives."
      actions={<button className="btn-outline" onClick={async () => { await api.post("/optimizer/run"); toast("ok", "Learning from the latest results"); setTimeout(reload, 4000); }}><Play size={15} /> Learn now</button>}>
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Prediction model" className="lg:col-span-1">
          {m?.samples ? (
            <div className="space-y-3 text-sm">
              <div>Trained on <b>{m.samples}</b> clips · fit R² {m.r2}</div>
              <div className="label">Biggest drivers</div>
              <ul className="space-y-1">{(m.top_features || []).slice(0, 8).map((f) => (
                <li key={f.feature} className="flex justify-between"><span className="text-dim">{fmt.label(f.feature.replace("=", ": "))}</span>
                  <span className={`tabular ${f.weight >= 0 ? "text-good" : "text-bad"}`}>{f.weight >= 0 ? "+" : ""}{f.weight.toFixed(2)}</span></li>))}</ul>
            </div>
          ) : (
            <p className="text-sm text-dim"><BrainCircuit size={16} className="mb-2 text-mute" />The model trains once about 12 clips have 72 hours of data{data?.last_learn?.samples !== undefined ? ` (now ${data.last_learn.samples})` : ""}. Until then the local LLM's ratings rank the clips.</p>
          )}
        </Card>
        <Card title="Moment scoring weights" className="lg:col-span-2">
          <BarList data={Object.entries(data?.score_weights || {}).map(([k, v]) => ({ label: fmt.label(k), value: Math.round(v * 100) }))} format={(v) => `${v}%`} />
          <p className="mt-3 text-xs text-mute">How much each quality counts when picking moments. These shift toward whatever performs on your pages.</p>
        </Card>
        {Object.entries(DIMS).map(([dim, label]) => (
          <Card key={dim} title={`${label}: chance to beat the median`}>
            <BarList data={(data?.bandit?.[dim] || []).map((a) => ({ label: fmt.label(a.arm) + (dim === "slot_hour" ? ":00" : ""), value: Math.round(a.win_rate * 100), note: `${a.pulls} posts so far` }))}
              format={(v) => `${v}%`} empty="No data yet" />
          </Card>
        ))}
      </div>
    </Page>
  );
}
