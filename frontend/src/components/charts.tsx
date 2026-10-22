// Small SVG charts: one measure each, so a single series hue and no legend (the title names it).
import { useMemo, useRef, useState } from "react";

type Bar = { label: string; value: number; note?: string };

/** Horizontal bars for comparing categories (magnitude). Hover shows the exact value. */
export function BarList({ data, format = (v) => v.toLocaleString(), empty = "No data yet" }: { data: Bar[]; format?: (v: number) => string; empty?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.value));
  if (!data.length) return <div className="py-6 text-center text-sm text-mute">{empty}</div>;
  return (
    <ul className="space-y-2.5" role="list">
      {data.map((d, i) => (
        <li key={d.label} className="relative" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
            <span className="truncate text-dim">{d.label}</span>
            <span className="tabular text-ink">{format(d.value)}</span>
          </div>
          <div className="h-2 w-full rounded-full" style={{ background: "var(--grid)" }}>
            <div className="h-2 rounded-r-[4px] rounded-l-full transition-[width]" style={{ width: `${Math.max(1.5, (d.value / max) * 100)}%`, background: "var(--series-1)", opacity: hover === null || hover === i ? 1 : 0.55 }} />
          </div>
          {hover === i && d.note && (
            <div className="pointer-events-none absolute right-0 top-full z-10 mt-1 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-xs shadow-lg">{d.note}</div>
          )}
        </li>
      ))}
    </ul>
  );
}

type Point = { x: string; y: number };

/** Area/line over time with a crosshair tooltip. */
export function TrendChart({ data, height = 180, format = (v) => v.toLocaleString() }: { data: Point[]; height?: number; format?: (v: number) => string }) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 640;
  const H = height;
  const pad = { l: 44, r: 12, t: 12, b: 24 };
  const max = Math.max(1, ...data.map((d) => d.y));
  const niceMax = useMemo(() => {
    const p = Math.pow(10, Math.floor(Math.log10(max)));
    return Math.ceil(max / p) * p;
  }, [max]);
  const x = (i: number) => pad.l + (data.length <= 1 ? 0 : (i / (data.length - 1)) * (W - pad.l - pad.r));
  const y = (v: number) => pad.t + (1 - v / niceMax) * (H - pad.t - pad.b);
  if (!data.length) return <div className="py-10 text-center text-sm text-mute">Views will appear here once posts are live.</div>;
  const line = data.map((d, i) => `${i ? "L" : "M"}${x(i)},${y(d.y)}`).join(" ");
  const area = `${line} L${x(data.length - 1)},${y(0)} L${x(0)},${y(0)} Z`;
  const ticks = [0, 0.5, 1].map((f) => niceMax * f);

  function onMove(e: React.MouseEvent) {
    const r = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    data.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
    setHover(best);
  }

  return (
    <div className="relative">
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="w-full" onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label="Views over time">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--axis)" : "var(--grid)"} strokeWidth={1} />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)" className="tabular">{format(t)}</text>
          </g>
        ))}
        <path d={area} fill="var(--series-1)" opacity={0.14} />
        <path d={line} fill="none" stroke="var(--series-1)" strokeWidth={2} strokeLinejoin="round" />
        {[0, data.length - 1].filter((v, i, a) => a.indexOf(v) === i).map((i) => (
          <text key={i} x={x(i)} y={H - 6} textAnchor={i === 0 ? "start" : "end"} fontSize="11" fill="var(--text-muted)">{data[i].x.slice(5)}</text>
        ))}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={y(0)} stroke="var(--axis)" strokeWidth={1} />
            <circle cx={x(hover)} cy={y(data[hover].y)} r={5} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-2 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-xs shadow-lg"
          style={{ left: `calc(${(x(hover) / W) * 100}% + 8px)`, transform: x(hover) > W * 0.7 ? "translateX(calc(-100% - 16px))" : undefined }}>
          <div className="text-mute">{data[hover].x}</div>
          <div className="tabular font-semibold">{format(data[hover].y)} views</div>
        </div>
      )}
    </div>
  );
}

/** Retention-style sparkline (0..1+ ratio over clip time). */
export function Sparkline({ points, width = 120, height = 32 }: { points: [number, number][]; width?: number; height?: number }) {
  if (!points.length) return null;
  const maxY = Math.max(1, ...points.map((p) => p[1]));
  const d = points.map(([px, py], i) => `${i ? "L" : "M"}${px * width},${height - (py / maxY) * height}`).join(" ");
  return (
    <svg width={width} height={height} aria-hidden>
      <path d={d} fill="none" stroke="var(--series-1)" strokeWidth={2} />
    </svg>
  );
}

/** Pipeline funnel: each step's bar, with the conversion from the step before. */
export function Funnel({ steps }: { steps: { label: string; value: number; note?: string }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...steps.map((s) => s.value));
  return (
    <ul className="space-y-2" role="list">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].value : 0;
        const conv = i && prev ? Math.round((s.value / prev) * 100) : null;
        return (
          <li key={s.label} className="grid grid-cols-[150px_1fr_92px] items-center gap-3 text-xs" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <span className="truncate text-dim">{s.label}</span>
            <div className="h-6 rounded-md" style={{ background: "var(--grid)" }}>
              <div className="flex h-6 items-center rounded-l-md rounded-r-[4px] px-2 text-[11px] font-semibold text-white transition-[width]"
                style={{ width: `${Math.max(2, (s.value / max) * 100)}%`, background: "var(--series-1)", opacity: hover === null || hover === i ? 1 : 0.6 }}>
                <span className="tabular">{s.value}</span>
              </div>
            </div>
            <span className="tabular whitespace-nowrap text-right text-mute">{s.note ?? (conv !== null ? `${Math.min(conv, 100)}%` : "")}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** A pipeline as one line of steps: big number, label, and the share that made it from the step before.
 *  Reads at a glance and doesn't need bars (the numbers are the point). */
export function PipelineSteps({ steps }: { steps: { label: string; value: number; note?: string }[] }) {
  return (
    <ol className="grid grid-cols-2 gap-y-4 sm:grid-cols-3 lg:flex lg:items-stretch" role="list">
      {steps.map((s, i) => {
        const prev = i ? steps[i - 1].value : 0;
        const conv = i && prev ? Math.min(100, Math.round((s.value / prev) * 100)) : null;
        return (
          <li key={s.label} className="flex min-w-0 flex-1 items-center">
            {i > 0 && (
              <div className="mx-2 hidden shrink-0 flex-col items-center text-[10px] text-mute lg:flex" aria-hidden>
                <span className="tabular">{s.note && !conv ? "" : conv !== null ? `${conv}%` : ""}</span>
                <span className="text-base leading-none">→</span>
              </div>
            )}
            <div className="min-w-0">
              <div className="tabular text-2xl font-semibold leading-tight">{s.value.toLocaleString()}</div>
              <div className="truncate text-xs text-dim">{s.label}</div>
              {s.note && <div className="truncate text-[11px] text-mute">{s.note}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Vertical columns (distribution). */
export function Columns({ data, height = 120, format = (v) => String(v) }: { data: { label: string; value: number }[]; height?: number; format?: (v: number) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <div>
      <div className="relative flex items-end gap-1.5" style={{ height }}>
        {data.map((d, i) => (
          <div key={d.label} className="relative flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <div className="w-full rounded-t-[4px]" style={{ height: `${(d.value / max) * 100}%`, minHeight: d.value ? 3 : 0, background: "var(--series-1)", opacity: hover === null || hover === i ? 1 : 0.55 }} />
            {hover === i && (
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 -translate-x-1/2 whitespace-nowrap rounded-lg border border-line bg-raised px-2 py-1 text-xs shadow-lg">
                {d.label}: <b className="tabular">{format(d.value)}</b>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1.5 border-t pt-1 text-[10px] text-mute" style={{ borderColor: "var(--axis)" }}>
        {data.map((d, i) => (
          <span key={d.label} className="flex-1 text-center">
            {i === 0 || i === data.length - 1 || i === Math.floor(data.length / 2) ? d.label : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Where in the episode each clip came from, over the episode's audio energy. */
export function EpisodeTimeline({ duration, energy, hop, moments, laughs = [] }: {
  duration: number; energy: number[]; hop: number; laughs?: number[];
  moments: { id: number; start_s: number; end_s: number; title: string; final_score: number }[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 1000, H = 90;
  if (!duration) return null;
  const lo = Math.min(...energy), hi = Math.max(...energy);
  const pts = energy.map((e, i) => `${(i * hop / duration) * W},${H - 12 - ((e - lo) / Math.max(1, hi - lo)) * (H - 30)}`);
  const x = (t: number) => (t / duration) * W;
  const fmt = (t: number) => `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}`;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" preserveAspectRatio="none" role="img" aria-label="Episode timeline with clip positions">
        <polyline points={pts.join(" ")} fill="none" stroke="var(--axis)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        {laughs.map((t, i) => <line key={i} x1={x(t)} x2={x(t)} y1={H - 10} y2={H - 4} stroke="var(--warning)" strokeWidth={2} vectorEffect="non-scaling-stroke" />)}
        {moments.map((m, i) => (
          <rect key={m.id} x={x(m.start_s)} y={6} width={Math.max(3, x(m.end_s) - x(m.start_s))} height={H - 20} rx={3}
            fill="var(--series-1)" opacity={hover === null || hover === i ? 0.45 : 0.2} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
        <line x1={0} x2={W} y1={H - 10} y2={H - 10} stroke="var(--axis)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-mute"><span>0:00</span><span>{fmt(duration / 2)}</span><span>{fmt(duration)}</span></div>
      {hover !== null && (
        <div className="pointer-events-none absolute top-0 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-xs shadow-lg"
          style={{ left: `min(calc(${(x(moments[hover].start_s) / W) * 100}% + 4px), calc(100% - 220px))` }}>
          <div className="font-medium">{moments[hover].title}</div>
          <div className="text-mute">{fmt(moments[hover].start_s)}–{fmt(moments[hover].end_s)} · score {moments[hover].final_score.toFixed(1)}</div>
        </div>
      )}
      <p className="mt-1 text-[11px] text-mute">Blue = clips · line = loudness · orange ticks = laughter</p>
    </div>
  );
}
