import { useMemo, useRef, useState } from "react";
import { Loader2, Mic, Play, Square, Upload } from "lucide-react";
import { api } from "../api";
import { useApi } from "../hooks";
import { useToast } from "./ui";

type Voice = { id: string; engine: string; label: string; note: string; group: string };
type Voices = { voices: Voice[]; engines: Record<string, { downloaded: boolean; size_gb: number }> };

/** Grouped voice list with an instant preview; first use of an engine downloads its model. */
export function VoicePicker({ value, onChange, compact = false }: { value: string; onChange: (v: string) => void; compact?: boolean }) {
  const toast = useToast();
  const { data, reload } = useApi<Voices>("/voices");
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);
  const groups = useMemo(() => {
    const g: Record<string, Voice[]> = {};
    for (const v of data?.voices || []) (g[v.group] ||= []).push(v);
    return g;
  }, [data]);
  const current = data?.voices.find((v) => v.id === value || v.id === `kokoro:${value}`);
  const engine = current?.engine || value.split(":")[0];
  const eng = data?.engines?.[engine];

  async function preview() {
    if (playing) { audio.current?.pause(); setPlaying(false); return; }
    setBusy(true);
    if (eng && !eng.downloaded) toast("info", `First use downloads this voice model (~${eng.size_gb} GB). One moment…`);
    try {
      const r = await api.post<{ url: string }>("/voices/preview", { voice: value, text: "" });
      audio.current?.pause();
      audio.current = new Audio(r.url);
      audio.current.onended = () => setPlaying(false);
      await audio.current.play();
      setPlaying(true);
      reload();
    } catch (e) { toast("err", (e as Error).message); } finally { setBusy(false); }
  }

  async function upload(f?: File) {
    if (!f) return;
    const name = prompt("Name this voice", f.name.replace(/\.[^.]+$/, "")) || "my_voice";
    try {
      const r = await api.upload<{ id: string }>(`/voices/upload?name=${encodeURIComponent(name)}`, f);
      toast("ok", "Voice sample added. Only clone voices you own or have permission to use.");
      await reload();
      onChange(r.id);
    } catch (e) { toast("err", (e as Error).message); }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex gap-2">
        <select className="input" value={current?.id || value} onChange={(e) => onChange(e.target.value)}>
          {Object.entries(groups).map(([g, vs]) => (
            <optgroup key={g} label={g}>{vs.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}</optgroup>
          ))}
        </select>
        <button type="button" className="btn-outline shrink-0 !px-3" onClick={preview} disabled={busy} title="Preview this voice">
          {busy ? <Loader2 size={15} className="animate-spin" /> : playing ? <Square size={14} /> : <Play size={15} />}
        </button>
        {!compact && (
          <label className="btn-ghost shrink-0 cursor-pointer !px-3" title="Clone a voice from a 10–30 s sample you own">
            <Upload size={15} /><input type="file" accept="audio/*" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
          </label>
        )}
      </div>
      {current && <p className="flex items-start gap-1 text-[11px] text-mute"><Mic size={11} className="mt-0.5 shrink-0" />{current.note}{eng && !eng.downloaded ? ` · downloads ~${eng.size_gb} GB on first use` : ""}</p>}
    </div>
  );
}
