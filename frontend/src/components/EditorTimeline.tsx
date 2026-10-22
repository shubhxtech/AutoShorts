// Editing timeline under the player: transport controls, a scrubbable ruler, the video filmstrip with
// cut points, a caption track and an overlay track, plus trim handles. Everything follows the <video>
// element, so the live text preview stays in sync while you scrub.
import { ReactNode, RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronFirst, ChevronLast, Pause, Play, Repeat, RotateCcw, Scissors, SkipBack, SkipForward, Volume2, VolumeX,
  ZoomIn, ZoomOut,
} from "lucide-react";
import type { Clip } from "../api";
import { STYLES, captionChunks, type OverlayEdits } from "./OverlayPreview";

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];

function fmtTime(t: number, tenths = true) {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${(tenths ? s.toFixed(1) : Math.floor(s).toString()).padStart(tenths ? 4 : 2, "0")}`;
}

/** Output time -> source time, through the clip's cut list (needed to turn a trim handle into a trim). */
function outToSrc(t: number, edl: [number, number, string, number][], cuts: number[]) {
  if (!edl?.length) return null;
  const starts = [0, ...cuts];
  let i = 0;
  while (i < starts.length - 1 && t >= starts[i + 1]) i++;
  const seg = edl[Math.min(i, edl.length - 1)];
  if (seg[2] === "still") return seg[0];
  return Math.min(seg[1], seg[0] + (t - starts[i]));
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

export function EditorTimeline({ clip, video, edits, onTrim, busy, extra }: {
  clip: Clip; video: RefObject<HTMLVideoElement>; edits?: OverlayEdits;
  onTrim?: (trim: { start: number; end: number }) => void; busy?: boolean; extra?: ReactNode;
}) {
  const spec = clip.overlay_spec;
  const fps = spec?.fps || 30;
  const duration = spec?.duration || clip.duration_s || 0;
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [loop, setLoop] = useState(true);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [hover, setHover] = useState<number | null>(null);
  const [trimIn, setTrimIn] = useState<number | null>(null);    // output seconds; null = untouched
  const [trimOut, setTrimOut] = useState<number | null>(null);
  const tracks = useRef<HTMLDivElement>(null);
  const drag = useRef<{ kind: "seek" | "in" | "out"; wasPlaying: boolean } | null>(null);

  // Follow the <video> element (it is replaced when the clip changes, so read the ref every frame).
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = video.current;
      if (v) {
        setT(v.currentTime);
        setPlaying(!v.paused);
        if (v.playbackRate !== rate) v.playbackRate = rate;
        if (v.loop !== loop) v.loop = loop;
        if (v.volume !== volume) v.volume = volume;
        if (v.muted !== muted && !drag.current) setMuted(v.muted);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [video, rate, loop, volume, muted]);

  useEffect(() => { setTrimIn(null); setTrimOut(null); }, [clip.id, clip.duration_s]);

  const seek = useCallback((to: number) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, Math.min(duration - 0.01, to));
    setT(v.currentTime);
  }, [video, duration]);
  const toggle = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused) { v.muted = muted; v.play().catch(() => {}); } else v.pause();
  }, [video, muted]);

  // Keyboard: space play, arrows seek (shift = 5 s), , . frame step, Home / End.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
      const v = video.current;
      if (!v) return;
      if (e.key === " ") { e.preventDefault(); toggle(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); seek(v.currentTime - (e.shiftKey ? 5 : 1)); }
      else if (e.key === "ArrowRight") { e.preventDefault(); seek(v.currentTime + (e.shiftKey ? 5 : 1)); }
      else if (e.key === ",") { v.pause(); seek(v.currentTime - 1 / fps); }
      else if (e.key === ".") { v.pause(); seek(v.currentTime + 1 / fps); }
      else if (e.key === "Home") { e.preventDefault(); seek(0); }
      else if (e.key === "End") { e.preventDefault(); seek(duration); }
      else if (e.key === "l" || e.key === "L") setLoop((x) => !x);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [video, toggle, seek, fps, duration]);

  // Pointer -> time on the tracks.
  const timeAt = (clientX: number) => {
    const el = tracks.current;
    if (!el || !duration) return 0;
    const r = el.getBoundingClientRect();
    return Math.max(0, Math.min(duration, ((clientX - r.left + el.scrollLeft) / (r.width * zoom)) * duration));
  };
  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (!drag.current) return;
      const x = timeAt(e.clientX);
      if (drag.current.kind === "seek") seek(x);
      else if (drag.current.kind === "in") setTrimIn(Math.min(e.altKey ? x : snap(x, "start"), (trimOut ?? duration) - 1));
      else setTrimOut(Math.max(e.altKey ? x : snap(x, "end"), (trimIn ?? 0) + 1));
    };
    const up = () => {
      if (drag.current?.kind === "seek" && drag.current.wasPlaying) video.current?.play().catch(() => {});
      drag.current = null;
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  });

  // Trim handles snap to the nearest word edge (start of a word for the in point, end for the out point),
  // so a trim never cuts a word in half. Hold Alt to place it freely.
  function snap(x: number, edge: "start" | "end") {
    const ws = clip.words || [];
    let best = x, bd = 0.6;
    for (const w of ws) {
      const at = edge === "start" ? w[2] - 0.04 : w[3] + 0.08;
      const d = Math.abs(at - x);
      if (d < bd) { bd = d; best = at; }
    }
    return Math.max(0, Math.min(duration, best));
  }

  function startSeek(e: React.PointerEvent) {
    const v = video.current;
    drag.current = { kind: "seek", wasPlaying: !!v && !v.paused };
    v?.pause();
    seek(timeAt(e.clientX));
  }

  const cuts: number[] = clip.render_stats?.boundaries || [];
  const edl = clip.render_stats?.edl || [];
  const style = STYLES[edits?.caption_style || clip.caption_style] || STYLES.clean;
  const chunks = useMemo(() => (clip.words?.length ? captionChunks(clip.words, style) : []), [clip.words, style]);
  const textOf = (i: number) => (edits?.caption_edits?.[String(clip.words[i][0])] ?? clip.words[i][1]);
  const hookEnd = spec?.hook_seconds || 0;
  const cards: { start: number; end: number; kind: string; text: string }[] = (clip.render_stats?.cards || [])
    .map((c: any) => ({ start: c.start, end: c.end, kind: c.kind || "card", text: c.text || c.label || "" }));
  const emoji: [number, string][] = edits?.emoji === false ? [] : spec?.emoji || [];
  const pct = (x: number) => `${(duration ? (x / duration) * 100 : 0).toFixed(3)}%`;
  const tickStep = duration / zoom > 40 ? 5 : duration / zoom > 15 ? 2 : 1;
  const ticks = Array.from({ length: Math.floor(duration / tickStep) + 1 }, (_, k) => k * tickStep);
  const trimmed = trimIn !== null || trimOut !== null;
  const curTrim = clip.meta?.trim || {};

  function applyTrim() {
    if (!onTrim || !clip.moment) return;
    const a = trimIn !== null ? outToSrc(trimIn, edl, cuts) : null;
    const b = trimOut !== null ? outToSrc(trimOut, edl, cuts) : null;
    onTrim({
      start: a !== null ? Math.round((a - clip.moment.start_s - 0.05) * 100) / 100 : curTrim.start || 0,
      end: b !== null ? Math.round((b - clip.moment.end_s + 0.1) * 100) / 100 : curTrim.end || 0,
    });
  }
  function extend(side: "start" | "end", by: number) {
    if (!onTrim) return;
    onTrim({ start: (curTrim.start || 0) + (side === "start" ? -by : 0), end: (curTrim.end || 0) + (side === "end" ? by : 0) });
  }

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
        <div className="ml-auto flex items-center gap-1">
          <select className="rounded-lg border border-line bg-transparent px-1.5 py-1 text-xs" value={rate} title="Speed"
            onChange={(e) => setRate(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}</select>
          <IconBtn title="Loop (L)" active={loop} onClick={() => setLoop(!loop)}><Repeat size={14} /></IconBtn>
          <IconBtn title={muted ? "Unmute" : "Mute"} onClick={() => { const v = video.current; if (v) v.muted = !muted; setMuted(!muted); }}>
            {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}</IconBtn>
          <input type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} title="Volume" className="w-16 accent-[var(--brand)]"
            onChange={(e) => { const x = Number(e.target.value); setVolume(x); if (x > 0 && muted) { setMuted(false); if (video.current) video.current.muted = false; } }} />
          <IconBtn title="Zoom out" onClick={() => setZoom((z) => Math.max(1, z / 2))} disabled={zoom <= 1}><ZoomOut size={14} /></IconBtn>
          <IconBtn title="Zoom in" onClick={() => setZoom((z) => Math.min(8, z * 2))} disabled={zoom >= 8}><ZoomIn size={14} /></IconBtn>
        </div>
      </div>

      {/* tracks */}
      <div className="relative flex overflow-hidden rounded-lg border border-line bg-raised">
      {/* fixed label column (stays put while the tracks scroll) */}
      <div className="w-[68px] shrink-0 border-r border-line text-[9px] font-medium uppercase tracking-wider text-mute">
        <div className="h-5 border-b border-line" />
        <div className="flex h-12 items-center px-2">Video</div>
        <div className="flex h-6 items-center border-t border-line px-2">Captions</div>
        <div className="flex h-6 items-center border-t border-line px-2">Overlays</div>
      </div>
      <div ref={tracks} className="relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
        onPointerMove={(e) => setHover(timeAt(e.clientX))} onPointerLeave={() => setHover(null)}>
        <div className="relative" style={{ width: `${zoom * 100}%` }}>
          {/* ruler */}
          <div className="relative h-5 cursor-pointer border-b border-line" onPointerDown={startSeek}>
            {ticks.map((x) => (
              <div key={x} className="absolute top-0 h-full border-l border-line/80 pl-1 text-[10px] leading-5 text-mute" style={{ left: pct(x) }}>{fmtTime(x, false)}</div>
            ))}
          </div>

          {/* video track: filmstrip + cut points + trim */}
          <div className="relative h-12 cursor-pointer" onPointerDown={startSeek}>
            <div className="absolute inset-0 bg-cover bg-left opacity-90"
              style={{ backgroundImage: `url(/api/clips/${clip.id}/filmstrip?v=${encodeURIComponent(String(clip.render_stats?.render_seconds ?? clip.duration_s))})`, backgroundSize: "100% 100%" }} />
            {cuts.map((c) => <div key={c} className="absolute inset-y-0 w-px bg-white/70" style={{ left: pct(c) }} title={`cut at ${fmtTime(c)}`} />)}
            {trimIn !== null && <div className="absolute inset-y-0 left-0 bg-black/70" style={{ width: pct(trimIn) }} />}
            {trimOut !== null && <div className="absolute inset-y-0 right-0 bg-black/70" style={{ left: pct(trimOut) }} />}
            {onTrim && <>
              <Handle side="in" at={pct(trimIn ?? 0)} onDown={() => { drag.current = { kind: "in", wasPlaying: false }; }} />
              <Handle side="out" at={pct(trimOut ?? duration)} onDown={() => { drag.current = { kind: "out", wasPlaying: false }; }} />
            </>}
          </div>

          {/* captions */}
          <Lane label="Captions">
            {chunks.map((c, k) => (
              <button key={k} type="button" onPointerDown={(e) => { e.stopPropagation(); seek(c.start + 0.01); }}
                title={c.idx.map(textOf).join(" ")}
                className={`absolute inset-y-1 overflow-hidden whitespace-nowrap rounded px-1 text-left text-[10px] leading-[18px] ${t >= c.start && t < c.end ? "bg-brand text-brand-ink" : "bg-hover text-dim hover:text-ink"}`}
                style={{ left: pct(c.start), width: pct(Math.max(0.15, c.end - c.start)) }}>
                {edits?.hide_captions ? "" : c.idx.map(textOf).join(" ")}
              </button>
            ))}
          </Lane>

          {/* overlays: hook, cards, emoji */}
          <Lane label="Overlays">
            {hookEnd > 0 && (
              <div className="absolute inset-y-1 rounded bg-[#ff2e63]/80 px-1 text-[10px] leading-[18px] text-white" style={{ left: 0, width: pct(hookEnd) }}
                title={`Hook: ${edits?.hook ?? clip.hook_text}`}>hook</div>
            )}
            {cards.map((c, k) => (
              <div key={k} className="absolute inset-y-1 overflow-hidden whitespace-nowrap rounded bg-[#3b82f6]/80 px-1 text-[10px] leading-[18px] text-white"
                style={{ left: pct(c.start), width: pct(Math.max(0.2, c.end - c.start)) }} title={`${c.kind}: ${c.text}`}>{c.text || c.kind}</div>
            ))}
            {emoji.map(([et, e]) => (
              <button key={et} type="button" className="absolute top-0.5 text-[13px]" style={{ left: pct(et) }}
                onPointerDown={(ev) => { ev.stopPropagation(); seek(et); }} title={`emoji at ${fmtTime(et)}`}>{e}</button>
            ))}
          </Lane>

          {/* playhead + hover */}
          <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-brand" style={{ left: pct(t) }}>
            <div className="absolute -left-[5px] -top-px h-0 w-0 border-x-[6px] border-t-[7px] border-x-transparent border-t-[var(--brand)]" />
          </div>
          {hover !== null && <div className="pointer-events-none absolute inset-y-0 w-px bg-white/30" style={{ left: pct(hover) }} />}
        </div>
      </div>
      {hover !== null && (
        <div className="pointer-events-none absolute -top-5 z-20 -translate-x-1/2 rounded bg-black/80 px-1.5 py-0.5 font-mono text-[10px] text-white"
          style={{ left: `calc(68px + (100% - 68px) * ${(hover / Math.max(duration, 0.01)) * zoom} - ${tracks.current?.scrollLeft || 0}px)` }}>{fmtTime(hover)}</div>
      )}
      {/* trim actions float over the end of the video track */}
      {onTrim && (
        <div className="absolute right-1.5 top-[2px] z-20 flex items-center gap-1 text-[10px] leading-4">
          {trimmed ? (
            <>
              <span className="rounded bg-black/75 px-1.5 text-white"><Scissors size={11} className="inline" /> {fmtTime(trimIn ?? 0)} – {fmtTime(trimOut ?? duration)}</span>
              <button type="button" className="rounded bg-black/75 px-1.5 text-white hover:bg-black" onClick={() => { setTrimIn(null); setTrimOut(null); }} title="Reset"><RotateCcw size={11} /></button>
              <button type="button" className="rounded bg-brand px-2 font-semibold text-brand-ink disabled:opacity-50" disabled={busy} onClick={applyTrim}>Apply trim</button>
            </>
          ) : (
            <>
              <button type="button" className="rounded bg-black/70 px-1.5 text-white hover:bg-black disabled:opacity-50" disabled={busy} onClick={() => extend("start", 2)} title="Start the clip 2 s earlier">−2 s start</button>
              <button type="button" className="rounded bg-black/70 px-1.5 text-white hover:bg-black disabled:opacity-50" disabled={busy} onClick={() => extend("end", 2)} title="End the clip 2 s later">+2 s end</button>
            </>
          )}
        </div>
      )}
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
