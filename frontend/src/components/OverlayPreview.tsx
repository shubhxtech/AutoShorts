// Live text layer drawn over the clean base video, so text edits are instant.
// Mirrors backend/autoshorts/render/captions.py + overlays.py (same canvas size, fonts and timings);
// the final MP4 is burned by the backend with the same settings on approval.
import { RefObject, useEffect, useMemo, useRef, useState } from "react";

export type OverlaySpec = {
  duration: number; fps: number; brand: string; caption_style: string; caption_font: string; emphasis: number[];
  caption_y_track: [number, number, string][]; hook: string; hook_seconds: number; headline: string; credit: string;
  credit_link: string; cta: string; cta_keyword: string; progress_bar: boolean; end_card: boolean;
  emoji: [number, string][]; panels: [number, number][]; key_words: string[];
};
export type OverlayEdits = Partial<{
  caption_style: string; caption_y: number | null; caption_scale: number; hook: string; headline: string; cta: string;
  progress_bar: boolean; emoji: boolean; end_card: boolean; brand: string; caption_edits: Record<string, string>; hide_captions: boolean;
}>;
type Word = [number, string, number, number, number];

const STYLES: Record<string, { font: string; size: number; upper: boolean; maxWords: number; maxChars: number; lines: number; stroke: number; shadow: number; hl: string; pop: number; reveal?: boolean; capsEmph?: boolean; bg?: boolean; weight: number; stretch?: string }> = {
  clean: { font: "Avenir Next", size: 80, upper: false, maxWords: 3, maxChars: 20, lines: 1, stroke: 8, shadow: 6, hl: "emphasis", pop: 0.06, reveal: true, capsEmph: true, weight: 800 },
  chip: { font: "Avenir Next", size: 56, upper: false, maxWords: 2, maxChars: 16, lines: 1, stroke: 0, shadow: 0, hl: "none", pop: 0.05, bg: true, weight: 600 },
  bold: { font: "Avenir Next", size: 88, upper: true, maxWords: 3, maxChars: 18, lines: 1, stroke: 9, shadow: 6, hl: "color", pop: 0.18, weight: 800 },
  karaoke: { font: "Avenir Next", size: 72, upper: false, maxWords: 6, maxChars: 26, lines: 2, stroke: 6, shadow: 5, hl: "box", pop: 0.08, weight: 800 },
  pop: { font: "Futura", size: 132, upper: true, maxWords: 2, maxChars: 12, lines: 1, stroke: 11, shadow: 7, hl: "color", pop: 0.3, weight: 800, stretch: "condensed" },
  boxed: { font: "Avenir Next", size: 76, upper: false, maxWords: 4, maxChars: 22, lines: 2, stroke: 0, shadow: 0, hl: "box_white", pop: 0.1, weight: 800 },
  minimal: { font: "Avenir Next", size: 62, upper: false, maxWords: 8, maxChars: 34, lines: 2, stroke: 0, shadow: 9, hl: "none", pop: 0, weight: 600 },
};
const WEAK_END = new Set(["the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "my", "your", "our", "his", "her", "their", "is", "was", "so", "if", "as", "by", "from"]);
const GREEN = "#39FF7A";

function chunk(words: Word[], st: (typeof STYLES)[string]) {
  const out: { idx: number[]; start: number; end: number }[] = [];
  let cur: number[] = [];
  let chars = 0;
  const limit = st.maxWords * st.lines;
  words.forEach((w, i) => {
    const gap = i ? w[2] - words[i - 1][3] : 0;
    const full = cur.length >= limit || chars + w[1].length > st.maxChars * st.lines;
    const last = cur.length ? words[cur[cur.length - 1]][1] : "";
    if (cur.length && (full || gap > 0.35 || /[.!?…।]$/.test(last) || (cur.length >= 2 && /[,;:]$/.test(last)))) {
      let carry: number[] = [];
      if (full && cur.length > 1 && WEAK_END.has(words[cur[cur.length - 1]][1].toLowerCase().replace(/[^\w']/g, ""))) carry = [cur.pop()!];
      out.push({ idx: cur, start: words[cur[0]][2], end: words[cur[cur.length - 1]][3] });
      cur = carry;
      chars = cur.reduce((n, j) => n + words[j][1].length + 1, 0);
    }
    cur.push(i);
    chars += w[1].length + 1;
  });
  if (cur.length) out.push({ idx: cur, start: words[cur[0]][2], end: words[cur[cur.length - 1]][3] });
  for (let k = 0; k < out.length - 1; k++) out[k].end = Math.min(out[k].end + 0.25, out[k + 1].start);
  if (out.length) out[out.length - 1].end += 0.4;
  return out;
}

function keyWord(text: string, prefer: Set<string>) {
  const stop = new Set(["the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "is", "are", "was", "you", "your", "this", "that", "it", "i", "my", "we", "they", "why", "how", "what", "never", "just", "most", "people"]);
  const ws = text.split(/\s+/).map((w) => w.replace(/[,.!?:;"']/g, "").toLowerCase());
  const num = ws.find((w) => /\d/.test(w));
  if (num) return num;
  const p = ws.find((w) => prefer.has(w) && !stop.has(w));
  if (p) return p;
  return ws.filter((w) => w && !stop.has(w)).sort((a, b) => b.length - a.length)[0] || "";
}

const backOut = (x: number, s = 1.6) => { x = Math.max(0, Math.min(1, x)) - 1; return x * x * ((s + 1) * x + s) + 1; };
const easeOut = (x: number) => 1 - Math.pow(1 - Math.max(0, Math.min(1, x)), 3);

export function useVideoTime(video: RefObject<HTMLVideoElement>) {
  const [t, setT] = useState(0);
  useEffect(() => {
    let raf = 0;
    const loop = () => { setT(video.current?.currentTime || 0); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [video]);
  return t;
}

export function OverlayPreview({ spec, words, edits, video, onDragCaption }: {
  spec: OverlaySpec; words: Word[]; edits: OverlayEdits; video: RefObject<HTMLVideoElement>;
  onDragCaption?: (y: number) => void;
}) {
  const t = useVideoTime(video);
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.3);
  useEffect(() => {
    const el = box.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver(() => setScale(el.clientWidth / 1080));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const s = { ...spec, ...Object.fromEntries(Object.entries(edits).filter(([, v]) => v !== undefined && v !== null)) } as OverlaySpec & OverlayEdits;
  const st = STYLES[s.caption_style || "clean"] || STYLES.clean;
  const sizeK = s.caption_scale || 1;
  const brand = s.brand || "#FFD400";
  const ew = edits.caption_edits || {};
  const textOf = (w: Word) => ew[String(w[0])] ?? w[1];
  const chunks = useMemo(() => chunk(words, st), [words, st]);
  const emph = useMemo(() => new Set(spec.emphasis || []), [spec.emphasis]);
  const d = spec.duration;

  // caption vertical position
  let capY = 0.68;
  if (edits.caption_y !== undefined && edits.caption_y !== null) capY = edits.caption_y;
  else if (spec.caption_y_track?.length) {
    let lo = 0;
    for (let k = 0; k < spec.caption_y_track.length; k++) { if (spec.caption_y_track[k][0] <= t) lo = k; else break; }
    capY = spec.caption_y_track[lo][1];
  }
  const split = Math.abs(capY - 0.5) < 0.05;
  const inPanel = (spec.panels || []).some(([a, b]) => a <= t && t <= b);

  const ci = chunks.findIndex((c) => c.start - 0.05 <= t && t < c.end);
  const ch = ci >= 0 ? chunks[ci] : null;
  let active = -1;
  if (ch) ch.idx.forEach((i) => { if (words[i][2] - 0.04 <= t) active = i; });
  const age = ch ? t - ch.start : 1;
  let pop = 1;
  if (st.pop && age < 0.16) { const k = age / 0.16; pop = k < 0.6 ? 0.78 + (1 + st.pop - 0.78) * (k / 0.6) : 1 + st.pop * (1 - (k - 0.6) / 0.4); }

  const strokeCss = (w: number) => w ? { WebkitTextStroke: `${w * 2 * sizeK}px #000`, paintOrder: "stroke fill" as const } : {};
  const shadowCss = (sh: number) => sh ? { textShadow: `0 4px ${sh * 2}px rgba(0,0,0,0.7)` } : {};

  // drag to move captions
  function startDrag(e: React.PointerEvent) {
    if (!onDragCaption) return;
    const host = box.current!.parentElement!.getBoundingClientRect();
    const move = (ev: PointerEvent) => onDragCaption(Math.max(0.12, Math.min(0.86, (ev.clientY - host.top) / host.height)));
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    e.preventDefault();
  }

  const hookOn = !!s.hook && t < (spec.hook_seconds || 0);
  const hookOut = 1 - easeOut((t - ((spec.hook_seconds || 0) - 0.25)) / 0.25);
  const kw = keyWord(s.hook || "", new Set((spec.key_words || []).map((x) => x.toLowerCase())));

  return (
    <div ref={box} className="pointer-events-none absolute left-0 top-0 origin-top-left select-none" style={{ width: 1080, height: 1920, transform: `scale(${scale})` }}>
      {s.progress_bar && d > 0 && (
        <div className="absolute left-0 top-0 h-[10px] w-full bg-black/50"><div className="h-full" style={{ width: `${Math.min(100, (t / d) * 100)}%`, background: brand }} /></div>
      )}

      {hookOn && (
        <div className="absolute inset-x-0 flex flex-col items-center" style={{ top: Math.max(150, 1920 * (split ? 0.125 : 0.21) - 70), opacity: Math.max(0, hookOut), transform: `scale(${0.94 + 0.06 * backOut(t / 0.18)})`, transformOrigin: "50% 0" }}>
          <div className="max-w-[980px] rounded-[28px] bg-white px-10 py-6 text-center leading-[1.08] text-[#0c0c0c]" style={{ font: `800 ${hookSize(s.hook || "")}px "Avenir Next", system-ui` }}>
            {(s.hook || "").replace(/\.$/, "").split(" ").map((w, i) => (
              <span key={i} className={w.replace(/[,.!?:;"']/g, "").toLowerCase() === kw ? "rounded-[14px] px-2" : ""} style={w.replace(/[,.!?:;"']/g, "").toLowerCase() === kw ? { background: brand } : undefined}>{w} </span>
            ))}
          </div>
          {s.credit && <div className="mt-3 text-white" style={{ font: `600 34px "Avenir Next", system-ui`, textShadow: "0 2px 8px rgba(0,0,0,.8)" }}>🎙 {s.credit}</div>}
        </div>
      )}
      {!hookOn && s.headline && !inPanel && t < d - (s.end_card ? 1.9 : 0) && (
        <div className="absolute inset-x-0 top-[160px] flex justify-center" style={{ opacity: easeOut((t - (spec.hook_seconds || 0)) / 0.3) }}>
          <div className="max-w-[900px] rounded-[20px] bg-black/65 px-7 py-3 text-center text-white" style={{ font: `800 46px "Avenir Next", system-ui` }}>{s.headline}</div>
        </div>
      )}

      {ch && !s.hide_captions && (
        // Full-width row so the text wraps at the same width as the final render (an absolutely
        // positioned box at left:50% would shrink to half the frame).
        <div className="absolute inset-x-0 flex justify-center" style={{ top: 1920 * capY, transform: "translateY(-50%)" }}>
        <div className={`flex max-w-[840px] flex-wrap justify-center gap-x-[0.28em] text-center ${onDragCaption ? "pointer-events-auto cursor-ns-resize" : ""}`}
          onPointerDown={startDrag}
          style={{ transform: `translateX(-20px) scale(${pop})`, font: `${st.weight} ${st.size * sizeK}px "${st.font}", system-ui`, fontStretch: st.stretch, lineHeight: 1.05,
            ...(st.bg ? { background: "rgba(58,58,58,.85)", borderRadius: 16, padding: "10px 22px" } : {}) }}>
          {ch.idx.map((i) => {
            const isEmph = emph.has(i);
            const isActive = i === active;
            let color = "#fff", bg: string | undefined, scaleW = 1;
            if (isEmph) { color = st.hl === "color" ? GREEN : brand; scaleW = st.hl === "emphasis" ? 1.12 : 1.1; }
            if (isActive) {
              if (st.hl === "color") { color = isEmph ? GREEN : brand; }
              else if (st.hl === "box") { bg = brand; color = "#000"; }
              else if (st.hl === "box_white") { bg = "#fff"; color = "#000"; }
            }
            const hidden = st.reveal && active >= 0 && i > active;
            let text = textOf(words[i]);
            if (st.upper || (st.capsEmph && isEmph)) text = text.toUpperCase();
            if (["bold", "pop", "clean"].includes(s.caption_style || "clean")) text = text.replace(/[,.;:]$/, "");
            return (
              <span key={i} style={{ color, fontSize: `${scaleW}em`, opacity: hidden ? 0 : 1, background: bg, borderRadius: bg ? 16 : undefined, padding: bg ? "0 14px" : undefined,
                ...(bg ? {} : strokeCss(st.stroke)), ...(bg ? {} : shadowCss(st.shadow)) }}>{text}</span>
            );
          })}
        </div>
        </div>
      )}

      {edits.emoji !== false && (spec.emoji || []).map(([et, e]) => {
        const a = t - et;
        if (a < 0 || a > 0.9) return null;
        return <div key={et} className="absolute left-1/2" style={{ top: 1920 * capY - 280 - 20 * a, fontSize: 150, transform: `translateX(-50%) scale(${0.4 + 0.75 * backOut(a / 0.25)})`, opacity: 1 - easeOut((a - 0.7) / 0.2) }}>{e}</div>;
      })}

      {s.cta_keyword && t >= d - 2.4 ? (
        <div className="absolute inset-x-0 flex justify-center" style={{ top: 1920 * 0.62, transform: "translateY(-50%)" }}>
          <div className="rounded-[10px] bg-[#121212]/95 px-10 py-7 text-center text-white shadow-2xl" style={{ transform: `scale(${t - (d - 2.4) > 0.35 ? 1 + 0.025 * Math.sin(t * 9) : 0.5 + 0.5 * backOut((t - (d - 2.4)) / 0.35)})`, font: `800 78px "Avenir Next", system-ui` }}>
            💬 COMMENT<div className="mt-3 rounded-[18px] px-8 py-2" style={{ background: "#e8342b", fontSize: 84 }}>{s.cta_keyword.toUpperCase()}</div>
          </div>
        </div>
      ) : s.end_card && t >= d - 1.9 ? (
        <div className="absolute inset-x-0 top-[576px] flex flex-col items-center gap-[18px]" style={{ opacity: Math.min(1, backOut((t - (d - 1.9)) / 0.3)) }}>
          {s.cta && <div className="rounded-[24px] px-9 py-4" style={{ background: brand, color: "#000", font: `800 58px "Avenir Next", system-ui` }}>{s.cta}</div>}
          {s.credit && <div className="rounded-[18px] bg-black/75 px-7 py-3 text-center text-white" style={{ font: `600 38px "Avenir Next", system-ui` }}>🎙 Full episode: {s.credit}{s.credit_link && <div className="text-[30px] text-[#c8c8c8]">{s.credit_link}</div>}</div>}
        </div>
      ) : null}
    </div>
  );
}

function hookSize(text: string) {
  const n = text.trim().replace(/\.$/, "").length;
  return n <= 26 ? 84 : n <= 40 ? 76 : n <= 56 ? 66 : 58;
}
