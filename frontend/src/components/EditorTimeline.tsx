// Editing timeline under the player: transport controls, a scrubbable ruler, the video filmstrip with
// cut points, a caption track and an overlay track. Trims, cuts, teasers and the opening are virtual
// (hooksVirtual.ts): instant in the preview, rendered once on approve.
import { ReactNode, RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronFirst, ChevronLast, Pause, Play, Repeat, Scissors, SkipBack, SkipForward, Undo2, Volume2,
  VolumeX, ZoomIn, ZoomOut,
} from "lucide-react";
import type { Clip } from "../api";
import type { Piece, VirtualPlayer } from "../hooksVirtual";
import { STYLES, captionChunks, type OverlayEdits } from "./OverlayPreview";

/** Every virtual time at which draft time `b` plays (a teaser can show the same footage twice). */
function toVirtual(b: number, pieces: Piece[]): number[] {
  return pieces.filter((p) => b >= p.b0 - 0.005 && b < p.b1).map((p) => p.v0 + (b - p.b0));
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];

function fmtTime(t: number, tenths = true) {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${(tenths ? s.toFixed(1) : Math.floor(s).toString()).padStart(tenths ? 4 : 2, "0")}`;
}

/** Space under the player the page reserves for this timeline (used to size the phone preview). */
export const TIMELINE_HEIGHT = 190;

/** Phone preview width so the player and the timeline below it fit on this screen. Pass a ref to the
 *  element the player sits in; its position on the page decides how much height is left. */
export function usePlayerWidth(anchor: RefObject<HTMLElement>, key?: unknown) {
  const [w, setW] = useState(300);
  useEffect(() => {
    const calc = () => {
      const el = anchor.current;
      const scroller = el?.closest("main");
      const top = el ? el.getBoundingClientRect().top + (scroller?.scrollTop || 0) : 120;
      const avail = window.innerHeight - top - TIMELINE_HEIGHT - 36;
      setW(Math.round(Math.max(220, Math.min(340, (avail * 9) / 19.5))));
    };
    calc();
    const id = window.setTimeout(calc, 200);           // after late layout (thumbnail strip, fonts)
    window.addEventListener("resize", calc);
    return () => { window.clearTimeout(id); window.removeEventListener("resize", calc); };
  }, [anchor, key]);
  return w;
}

export function EditorTimeline({ clip, video, vp, edits, onTrim, busy, extra }: {
  clip: Clip; video: RefObject<HTMLVideoElement>; vp: VirtualPlayer; edits?: OverlayEdits;
  onTrim?: (trim: { start: number; end: number }) => void; busy?: boolean; extra?: ReactNode;
}) {
  const spec = clip.overlay_spec;
  const fps = spec?.fps || 30;
  const draftDur = spec?.duration || clip.duration_s || 0;
  const duration = vp.dur || draftDur;
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState<number | null>(null);
  const [trimIn, setTrimIn] = useState<number | null>(null);
  const [trimOut, setTrimOut] = useState<number | null>(null);
  const [sel, setSel] = useState<[number, number] | null>(null);
  const tracks = useRef<HTMLDivElement>(null);
  const drag = useRef<{ kind: "seek" | "in" | "out" | "select"; wasPlaying: boolean; from?: number } | null>(null);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = video.current;
      if (v) {
        setT(vp.clock.current.v);
        setPlaying(!v.paused);
        if (v.playbackRate !== rate) v.playbackRate = rate;
        if (v.volume !== volume) v.volume = volume;
        if (v.muted !== muted && !drag.current) setMuted(v.muted);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [video, vp.clock, rate, volume, muted]);

  useEffect(() => { setTrimIn(null); setTrimOut(null); setSel(null); }, [clip.id, clip.duration_s, vp.pieces]);

  const seek = useCallback((to: number) => vp.seek(to), [vp]);
  const toggle = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused) { v.muted = muted; v.play().catch(() => {}); } else v.pause();
  }, [video, muted]);

  // Keyboard: space play, arrows seek (shift = 5 s), , . frame step, Home / End, L loop, Delete cuts the selection.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      const v = video.current;
      if (!v) return;
      const now = vp.clock.current.v;
      if (e.key === " ") { e.preventDefault(); toggle(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); seek(now - (e.shiftKey ? 5 : 1)); }
      else if (e.key === "ArrowRight") { e.preventDefault(); seek(now + (e.shiftKey ? 5 : 1)); }
      else if (e.key === ",") { v.pause(); seek(now - 1 / fps); }
      else if (e.key === ".") { v.pause(); seek(now + 1 / fps); }
      else if (e.key === "Home") { e.preventDefault(); seek(0); }
      else if (e.key === "End") { e.preventDefault(); seek(duration); }
      else if (e.key === "l" || e.key === "L") vp.setLoop(!vp.loop);
      else if ((e.key === "Delete" || e.key === "Backspace") && sel) { e.preventDefault(); vp.cutOut(sel[0], sel[1]); }
      else if (e.key === "Escape") setSel(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [video, toggle, seek, fps, duration, vp, sel]);

  const timeAt = (clientX: number) => {
    const el = tracks.current;
    if (!el || !duration) return 0;
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(duration, ((clientX - r.left + el.scrollLeft) / (r.width * zoom)) * duration));
  };

  // Trim handles snap to the nearest word edge (start of a word for the in point, end for the out point).
  const vWords = useMemo(() => {
    const out: [number, string, number, number, number][] = [];
    for (const w of clip.words || []) for (const v of toVirtual(w[2], vp.pieces)) out.push([w[0], w[1], v, v + (w[3] - w[2]), w[4]]);
    return out.sort((a, b) => a[2] - b[2]);
  }, [clip.words, vp.pieces]);
  function snap(x: number, edge: "start" | "end") {
    let best = x, bd = 0.6;
    for (const w of vWords) {
      const at = edge === "start" ? w[2] - 0.04 : w[3] + 0.08;
      const d = Math.abs(at - x);
      if (d < bd) { bd = d; best = at; }
    }
    return Math.max(0, Math.min(duration, best));
  }

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      const x = timeAt(e.clientX);
      if (d.kind === "seek") seek(x);
      else if (d.kind === "in") setTrimIn(Math.min(e.altKey ? x : snap(x, "start"), (trimOut ?? duration) - 1));
      else if (d.kind === "out") setTrimOut(Math.max(e.altKey ? x : snap(x, "end"), (trimIn ?? 0) + 1));
      else if (d.kind === "select" && d.from !== undefined) setSel([Math.min(d.from, x), Math.max(d.from, x)]);
    };
    const up = () => {
      const d = drag.current;
      drag.current = null;
      if (!d) return;
      if (d.kind === "seek" && d.wasPlaying) video.current?.play().catch(() => {});
      if ((d.kind === "in" || d.kind === "out") && (trimIn !== null || trimOut !== null)) {
        vp.trim(trimIn ?? 0, trimOut ?? duration);         // instant: no render
        setTrimIn(null); setTrimOut(null);
      }
      if (d.kind === "select") setSel((s) => (s && s[1] - s[0] < 0.25 ? null : s));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  });

  function startSeek(e: React.PointerEvent) {
    if (e.shiftKey) {                       // Shift-drag selects a range to cut or move
      const x = timeAt(e.clientX);
      drag.current = { kind: "select", wasPlaying: false, from: x };
      setSel([x, x]);
      return;
    }
    const v = video.current;
    drag.current = { kind: "seek", wasPlaying: !!v && !v.paused };
    v?.pause();
    seek(timeAt(e.clientX));
  }

  const draftCuts: number[] = clip.render_stats?.boundaries || [];
  const style = STYLES[edits?.caption_style || clip.caption_style] || STYLES.clean;
  const chunks = useMemo(() => (vWords.length ? captionChunks(vWords, style) : []), [vWords, style]);
  const textOf = (i: number) => (edits?.caption_edits?.[String(vWords[i][0])] ?? vWords[i][1]);
  const hookEnd = edits?.hook_style === "none" ? 0 : spec?.hook_seconds || 0;
  const cards = (clip.render_stats?.cards || []).flatMap((c: any) => toVirtual(c.start, vp.pieces).map((v) => (
    { start: v, end: v + (c.end - c.start), kind: c.kind || "card", text: c.text || c.label || "" })));
  const emoji: [number, string][] = edits?.emoji === false ? [] : (spec?.emoji || []).flatMap(([et, e]) => toVirtual(et, vp.pieces).map((v) => [v, e] as [number, string]));
  const cuts = [
    ...vp.pieces.slice(1).map((p) => ({ at: p.v0, join: true })),
    ...draftCuts.flatMap((c) => toVirtual(c, vp.pieces)).map((at) => ({ at, join: false })),
  ];
  const pct = (x: number) => `${(duration ? (x / duration) * 100 : 0).toFixed(3)}%`;
  const tickStep = duration / zoom > 40 ? 5 : duration / zoom > 15 ? 2 : 1;
  const ticks = Array.from({ length: Math.floor(duration / tickStep) + 1 }, (_, k) => k * tickStep);
  const filmstrip = `/api/clips/${clip.id}/filmstrip?v=${encodeURIComponent(String(clip.render_stats?.render_seconds ?? clip.duration_s))}`;

  return (
    <div data-timeline className="card select-none p-3 text-xs">
      {/* transport */}
      <div className="mb-2 flex flex-wrap items-center gap-1">
        <IconBtn title="Start (Home)" onClick={() => seek(0)}><ChevronFirst size={15} /></IconBtn>
        <IconBtn title="Back 5 s (Shift ←)" onClick={() => seek(t - 5)}><SkipBack size={15} /></IconBtn>
        <IconBtn title="Previous frame (,)" onClick={() => { video.current?.pause(); seek(t - 1 / fps); }}><span className="font-mono">‹</span></IconBtn>
        <button type="button" onClick={toggle} title="Play / pause (Space)"
          className="mx-1 grid h-9 w-9 place-items-center rounded-full bg-brand text-brand-ink">
          {playing ? <Pause size={16} /> : <Play size={16} className="ml-0.5" />}</button>
        <IconBtn title="Next frame (.)" onClick={() => { video.current?.pause(); seek(t + 1 / fps); }}><span className="font-mono">›</span></IconBtn>
        <IconBtn title="Forward 5 s (Shift →)" onClick={() => seek(t + 5)}><SkipForward size={15} /></IconBtn>
        <IconBtn title="End (End)" onClick={() => seek(duration)}><ChevronLast size={15} /></IconBtn>
        <span className="ml-2 font-mono tabular-nums text-ink">{fmtTime(t)}</span>
        <span className="font-mono tabular-nums text-mute">/ {fmtTime(duration)}</span>
        {extra && <div className="ml-3 flex items-center gap-1 border-l border-line pl-3">{extra}</div>}
        {vp.edited && (
          <span className="ml-2 flex items-center gap-1 rounded-full bg-brand/15 px-2 py-0.5 text-brand" title="Your cut plays instantly here and is rendered once when you approve">
            edits not rendered yet
            <button type="button" className="rounded-full p-0.5 hover:bg-brand/20" title="Reset to the automatic cut" onClick={vp.reset}><Undo2 size={12} /></button>
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <select className="rounded-lg border border-line bg-transparent px-1.5 py-1 text-xs" value={rate} title="Speed"
            onChange={(e) => setRate(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}</select>
          <IconBtn title="Loop (L)" active={vp.loop} onClick={() => vp.setLoop(!vp.loop)}><Repeat size={14} /></IconBtn>
          <IconBtn title={muted ? "Unmute" : "Mute"} onClick={() => { const v = video.current; if (v) v.muted = !muted; setMuted(!muted); }}>
            {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}</IconBtn>
          <input type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} title="Volume" className="w-16 accent-[var(--brand)]"
            onChange={(e) => { const x = Number(e.target.value); setVolume(x); if (x > 0 && muted) { setMuted(false); if (video.current) video.current.muted = false; } }} />
          <IconBtn title="Zoom out" onClick={() => setZoom((z) => Math.max(1, z / 2))} disabled={zoom <= 1}><ZoomOut size={14} /></IconBtn>
          <IconBtn title="Zoom in" onClick={() => setZoom((z) => Math.min(8, z * 2))} disabled={zoom >= 8}><ZoomIn size={14} /></IconBtn>
        </div>
      </div>

      {/* tracks */}
      <div className="relative">
      <div className="relative flex overflow-hidden rounded-lg border border-line bg-raised">
      <div className="w-[68px] shrink-0 border-r border-line text-[9px] font-medium uppercase tracking-wider text-mute">
        <div className="h-5 border-b border-line" />
        <div className="flex h-12 items-center px-2">Video</div>
        <div className="flex h-6 items-center border-t border-line px-2">Captions</div>
        <div className="flex h-6 items-center border-t border-line px-2">Overlays</div>
      </div>
      <div ref={tracks} className="relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
        onPointerMove={(e) => setHover(timeAt(e.clientX))} onPointerLeave={() => setHover(null)}>
        <div className="relative" style={{ width: `${zoom * 100}%` }}>
          <div className="relative h-5 cursor-pointer border-b border-line" onPointerDown={startSeek}>
            {ticks.map((x) => (
              <div key={x} className="absolute top-0 h-full border-l border-line/80 pl-1 text-[10px] leading-5 text-mute" style={{ left: pct(x) }}>{fmtTime(x, false)}</div>
            ))}
          </div>

          {/* video track: one filmstrip slice per piece of the cut */}
          <div className="relative h-12 cursor-pointer" onPointerDown={startSeek}>
            {vp.pieces.map((p, k) => {
              const len = p.b1 - p.b0;
              return (
                <div key={k} className={`absolute inset-y-0 bg-no-repeat ${p.kind === "cold_open" ? "ring-1 ring-inset ring-brand" : ""}`}
                  style={{ left: pct(p.v0), width: pct(len), backgroundImage: `url(${filmstrip})`,
                    backgroundSize: `${(draftDur / Math.max(len, 0.01)) * 100}% 100%`,
                    backgroundPosition: `${draftDur - len > 0.01 ? (p.b0 / (draftDur - len)) * 100 : 0}% 0` }}
                  title={p.kind === "cold_open" ? "teaser" : undefined} />
              );
            })}
            {cuts.map((c, k) => <div key={k} className={`absolute inset-y-0 w-px ${c.join ? "bg-brand" : "bg-white/60"}`} style={{ left: pct(c.at) }} />)}
            {trimIn !== null && <div className="absolute inset-y-0 left-0 bg-black/70" style={{ width: pct(trimIn) }} />}
            {trimOut !== null && <div className="absolute inset-y-0 right-0 bg-black/70" style={{ left: pct(trimOut) }} />}
            <Handle side="in" at={pct(trimIn ?? 0)} onDown={() => { drag.current = { kind: "in", wasPlaying: false }; }} />
            <Handle side="out" at={pct(trimOut ?? duration)} onDown={() => { drag.current = { kind: "out", wasPlaying: false }; }} />
          </div>

          <Lane label="Captions">
            {chunks.map((c, k) => (
              <button key={k} type="button"
                onPointerDown={(e) => { e.stopPropagation(); if (e.shiftKey) setSel([c.start, c.end]); else seek(c.start + 0.01); }}
                onDoubleClick={() => setSel([Math.max(0, c.start - 0.05), Math.min(duration, c.end)])}
                title={`${c.idx.map(textOf).join(" ")} (double-click to select)`}
                className={`absolute inset-y-1 overflow-hidden whitespace-nowrap rounded px-1 text-left text-[10px] leading-[18px] ${t >= c.start && t < c.end ? "bg-brand text-brand-ink" : "bg-hover text-dim hover:text-ink"}`}
                style={{ left: pct(c.start), width: pct(Math.max(0.15, c.end - c.start)) }}>
                {edits?.hide_captions ? "" : c.idx.map(textOf).join(" ")}
              </button>
            ))}
          </Lane>

          <Lane label="Overlays">
            {hookEnd > 0 && (
              <div className="absolute inset-y-1 rounded bg-[#ff2e63]/80 px-1 text-[10px] leading-[18px] text-white" style={{ left: 0, width: pct(hookEnd) }}
                title={`Hook: ${edits?.hook ?? clip.hook_text}`}>hook</div>
            )}
            {cards.map((c: { start: number; end: number; kind: string; text: string }, k: number) => (
              <div key={k} className="absolute inset-y-1 overflow-hidden whitespace-nowrap rounded bg-[#3b82f6]/80 px-1 text-[10px] leading-[18px] text-white"
                style={{ left: pct(c.start), width: pct(Math.max(0.2, c.end - c.start)) }} title={`${c.kind}: ${c.text}`}>{c.text || c.kind}</div>
            ))}
            {emoji.map(([et, e], k) => (
              <button key={k} type="button" className="absolute top-0.5 text-[13px]" style={{ left: pct(et) }}
                onPointerDown={(ev) => { ev.stopPropagation(); seek(et); }} title={`emoji at ${fmtTime(et)}`}>{e}</button>
            ))}
          </Lane>

          {sel && <div className="pointer-events-none absolute inset-y-0 border-x-2 border-[#60a5fa] bg-[#60a5fa]/20" style={{ left: pct(sel[0]), width: pct(sel[1] - sel[0]) }} />}
          <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-brand" style={{ left: pct(t) }}>
            <div className="absolute -left-[5px] -top-px h-0 w-0 border-x-[6px] border-t-[7px] border-x-transparent border-t-[var(--brand)]" />
          </div>
          {hover !== null && <div className="pointer-events-none absolute inset-y-0 w-px bg-white/30" style={{ left: pct(hover) }} />}
        </div>
      </div>
      </div>
      {hover !== null && (
        <div className="pointer-events-none absolute -top-5 z-20 -translate-x-1/2 rounded bg-black/80 px-1.5 py-0.5 font-mono text-[10px] text-white"
          style={{ left: `calc(68px + (100% - 68px) * ${(hover / Math.max(duration, 0.01)) * zoom} - ${tracks.current?.scrollLeft || 0}px)` }}>{fmtTime(hover)}</div>
      )}
      {/* actions float in the ruler row */}
      <div className="absolute right-1.5 top-[2px] z-20 flex items-center gap-1 text-[10px] leading-4">
        {sel ? (
          <>
            <span className="rounded bg-black/75 px-1.5 text-white">{fmtTime(sel[0])} – {fmtTime(sel[1])}</span>
            <button type="button" className="rounded bg-[#ef4444] px-1.5 font-semibold text-white" onClick={() => vp.cutOut(sel[0], sel[1])} title="Remove this section (Delete)"><Scissors size={10} className="inline" /> Cut</button>
            <button type="button" className="rounded bg-brand px-1.5 font-semibold text-brand-ink" onClick={() => vp.teaser(sel[0], sel[1])} title="Show this section first as a teaser">Move to start</button>
            <button type="button" className="rounded bg-black/75 px-1.5 text-white" onClick={() => setSel(null)} title="Clear selection (Esc)">×</button>
          </>
        ) : onTrim ? (
          <>
            <span className="rounded bg-black/60 px-1.5 text-white/80">Shift-drag to select</span>
            <button type="button" className="rounded bg-black/70 px-1.5 text-white hover:bg-black disabled:opacity-50" disabled={busy} onClick={() => onTrim({ start: (clip.meta?.trim?.start || 0) - 2, end: clip.meta?.trim?.end || 0 })} title="Bring in 2 s of earlier footage (re-cuts the draft)">−2 s start</button>
            <button type="button" className="rounded bg-black/70 px-1.5 text-white hover:bg-black disabled:opacity-50" disabled={busy} onClick={() => onTrim({ start: clip.meta?.trim?.start || 0, end: (clip.meta?.trim?.end || 0) + 2 })} title="Bring in 2 s of later footage (re-cuts the draft)">+2 s end</button>
          </>
        ) : null}
      </div>
      </div>
    </div>
  );
}

function Lane({ children }: { label: string; children: React.ReactNode }) {
  return <div className="relative h-6 border-t border-line">{children}</div>;
}

function Handle({ side, at, onDown }: { side: "in" | "out"; at: string; onDown: () => void }) {
  return (
    <div role="slider" aria-label={side === "in" ? "Trim start" : "Trim end"} tabIndex={-1}
      onPointerDown={(e) => { e.stopPropagation(); onDown(); }}
      className={`absolute inset-y-0 z-10 w-2.5 cursor-ew-resize bg-white shadow ${side === "in" ? "rounded-l" : "-translate-x-full rounded-r"}`}
      style={{ left: at }}>
      <div className="absolute inset-y-3 left-1/2 w-px -translate-x-1/2 bg-black/40" />
    </div>
  );
}

function IconBtn({ children, title, onClick, active, disabled }: { children: React.ReactNode; title: string; onClick: () => void; active?: boolean; disabled?: boolean }) {
  return (
    <button type="button" title={title} aria-label={title} onClick={onClick} disabled={disabled}
      className={`grid h-7 min-w-7 place-items-center rounded-lg px-1 disabled:opacity-30 ${active ? "bg-hover text-ink" : "text-dim hover:bg-hover hover:text-ink"}`}>{children}</button>
  );
}
