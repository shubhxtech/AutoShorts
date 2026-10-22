// Virtual cut: rearranging the clip (trim, cut out a section, move a section to the start, switch the
// opening) without rendering. The draft video already contains all the footage, so the player just plays
// its pieces in the new order. The order is saved as source-time ranges (clip.meta.cut), which survive later
// re-renders; the backend assembles the real cut once, on approve.
import { RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Clip } from "./api";

/** A stretch of the draft video (draft seconds), placed at `v0` on the virtual timeline. */
export type Piece = { b0: number; b1: number; v0: number; kind: string };
export type VirtualClock = { v: number; dur: number; base: number };

type Seg = { s0: number; s1: number; kind: string; o0: number; o1: number };

function draftSegments(clip: Clip): Seg[] {
  const edl: [number, number, string, number][] = clip.render_stats?.edl || [];
  const cuts: number[] = clip.render_stats?.boundaries || [];
  const starts = [0, ...cuts];
  return edl.map(([s0, s1, kind], i) => ({ s0, s1, kind, o0: starts[i] ?? 0, o1: (starts[i] ?? 0) + (s1 - s0) }));
}

/** Source range -> the draft pieces that show it (pauses removed by jump cuts simply aren't there). */
function srcToDraft(a: number, b: number, segs: Seg[]): { b0: number; b1: number; kind: string }[] {
  const out: { b0: number; b1: number; kind: string }[] = [];
  for (const g of segs) {
    if (g.kind === "still") continue;
    const x0 = Math.max(a, g.s0), x1 = Math.min(b, g.s1);
    if (x1 - x0 > 0.04) out.push({ b0: g.o0 + (x0 - g.s0), b1: g.o0 + (x1 - g.s0), kind: g.kind });
  }
  return out;
}

function draftToSrc(p: { b0: number; b1: number }, segs: Seg[]): [number, number] | null {
  const g = segs.find((s) => p.b0 >= s.o0 - 0.01 && p.b0 < s.o1 - 0.01);
  if (!g || g.kind === "still") return null;
  return [Math.max(g.s0, g.s0 + (p.b0 - g.o0)), g.s0 + (Math.min(p.b1, g.o1) - g.o0)];
}

function layout(pieces: { b0: number; b1: number; kind: string }[]): Piece[] {
  let v = 0;
  return pieces.filter((p) => p.b1 - p.b0 > 0.04).map((p) => { const q = { ...p, v0: v }; v += p.b1 - p.b0; return q; });
}

/** Split pieces at virtual time v (so edits can cut exactly there). */
function splitAt(pieces: Piece[], v: number): Piece[] {
  const out: Piece[] = [];
  for (const p of pieces) {
    const len = p.b1 - p.b0;
    if (v > p.v0 + 0.02 && v < p.v0 + len - 0.02) {
      const m = p.b0 + (v - p.v0);
      out.push({ ...p, b1: m }, { ...p, b0: m });
    } else out.push(p);
  }
  return layout(out);
}

export type VirtualPlayer = ReturnType<typeof useVirtualPlayer>;

export function useVirtualPlayer(clip: Clip | undefined, video: RefObject<HTMLVideoElement>) {
  // The draft only changes when the clip is re-rendered: key on that, not on every refetch of the clip object,
  // so a server response that arrives mid-edit can't reset what you're doing.
  const draftKey = `${clip?.id}:${clip?.render_stats?.render_seconds}:${clip?.duration_s}:${clip?.status}`;
  const segs = useMemo(() => (clip ? draftSegments(clip) : []), [draftKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const identity = useMemo(() => layout(segs.map((g) => ({ b0: g.o0, b1: g.o1, kind: g.kind }))), [segs]);
  const fromClip = useCallback((c: Clip | undefined) => {
    const cut: [number, number][] = c?.meta?.cut || [];
    if (!cut.length) return identity;
    return layout(cut.flatMap(([a, b]) => srcToDraft(a, b, segs)));
  }, [identity, segs]);

  const [pieces, setPieces] = useState<Piece[]>(() => fromClip(clip));
  const [loop, setLoop] = useState(true);
  const clock = useRef<VirtualClock>({ v: 0, dur: 0, base: 0 });
  const idx = useRef(0);
  const saveTimer = useRef<number>();
  const edited = pieces.length !== identity.length || pieces.some((p, i) => Math.abs(p.b0 - identity[i].b0) > 0.02 || Math.abs(p.b1 - identity[i].b1) > 0.02);
  const dur = pieces.reduce((n, p) => n + (p.b1 - p.b0), 0);

  useEffect(() => { setPieces(fromClip(clip)); idx.current = 0; }, [draftKey, fromClip]); // eslint-disable-line react-hooks/exhaustive-deps

  // Play the pieces back to back: when the draft reaches the end of a piece, jump to the next one.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const el = video.current;
      if (el && pieces.length) {
        el.loop = false;                                   // looping is handled here, in virtual time
        let i = Math.min(idx.current, pieces.length - 1);
        const b = el.currentTime;
        let p = pieces[i];
        if (b < p.b0 - 0.3 || b > p.b1 + 0.3) {            // seeked from outside (e.g. a click on the video)
          const j = pieces.findIndex((q) => b >= q.b0 && b < q.b1);
          if (j >= 0) { i = j; p = pieces[j]; }
        }
        if (!el.paused && b >= p.b1 - 0.03) {
          if (i + 1 < pieces.length) { i += 1; el.currentTime = pieces[i].b0; }
          else if (loop) { i = 0; el.currentTime = pieces[0].b0; }
          else { el.pause(); }
        }
        idx.current = i;
        const cur = pieces[i];
        clock.current = { v: cur.v0 + Math.max(0, Math.min(cur.b1, el.currentTime) - cur.b0), dur, base: el.currentTime };
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [video, pieces, loop, dur]);

  const seek = useCallback((v: number) => {
    const el = video.current;
    if (!el || !pieces.length) return;
    v = Math.max(0, Math.min(dur - 0.01, v));
    const i = Math.max(0, pieces.findIndex((p) => v >= p.v0 && v < p.v0 + (p.b1 - p.b0)));
    idx.current = i;
    el.currentTime = pieces[i].b0 + (v - pieces[i].v0);
    clock.current = { ...clock.current, v };
  }, [video, pieces, dur]);

  // Persist (debounced) as source ranges; nothing renders until approve.
  const commit = useCallback((next: Piece[]) => {
    setPieces(next);
    window.clearTimeout(saveTimer.current);
    if (!clip) return;
    saveTimer.current = window.setTimeout(() => {
      const same = next.length === identity.length && next.every((p, i) => Math.abs(p.b0 - identity[i].b0) < 0.02 && Math.abs(p.b1 - identity[i].b1) < 0.02);
      const cut = same ? [] : next.map((p) => draftToSrc(p, segs)).filter(Boolean);
      api.patch(`/clips/${clip.id}`, { cut, rerender: false }).catch(() => {});
    }, 350);
  }, [clip, identity, segs]);

  const trim = useCallback((vIn: number, vOut: number) => {
    let ps = splitAt(splitAt(pieces, vIn), vOut);
    ps = ps.filter((p) => p.v0 >= vIn - 0.01 && p.v0 + (p.b1 - p.b0) <= vOut + 0.01);
    commit(layout(ps));
  }, [pieces, commit]);

  const cutOut = useCallback((a: number, b: number) => {
    const ps = splitAt(splitAt(pieces, a), b).filter((p) => !(p.v0 >= a - 0.01 && p.v0 + (p.b1 - p.b0) <= b + 0.01));
    if (ps.length) commit(layout(ps));
  }, [pieces, commit]);

  /** Copy a section to the very start as a teaser (like a cold open), keeping it in place too. */
  const teaser = useCallback((a: number, b: number) => {
    const ps = splitAt(splitAt(pieces, a), b);
    const sel = ps.filter((p) => p.v0 >= a - 0.01 && p.v0 + (p.b1 - p.b0) <= b + 0.01).map((p) => ({ ...p, kind: "cold_open" }));
    commit(layout([...sel, ...ps.filter((p) => p.kind !== "cold_open")]));
  }, [pieces, commit]);

  /** Opening without rendering: straight drops a teaser; cold open puts the punchline first. */
  const setOpening = useCallback((opening: string): boolean => {
    if (!clip) return false;
    const body = pieces.filter((p) => p.kind !== "cold_open");
    if (opening === "straight") { commit(layout(body)); return true; }
    if (opening === "cold_open") {
      const pl = clip.moment?.punchline_s;
      if (!pl) return false;
      const t0 = pl[0], t1 = Math.min(pl[1], pl[0] + 3.2);       // same tease length as the renderer
      const tease = srcToDraft(t0, t1, segs).map((p) => ({ ...p, kind: "cold_open" }));
      if (!tease.length) return false;
      commit(layout([...tease, ...body]));
      return true;
    }
    return false;
  }, [clip, pieces, segs, commit]);

  const reset = useCallback(() => commit(identity), [identity, commit]);

  return { pieces, dur, clock, seek, loop, setLoop, trim, cutOut, teaser, setOpening, reset, edited };
}
