import { useState } from "react";
import { Link2, Loader2, Sparkles } from "lucide-react";
import { api, type Account } from "../api";
import { useApi } from "../hooks";
import { Platform, Toggle, useToast } from "./ui";

const RIGHTS = [
  { id: "own", label: "My own content" },
  { id: "licensed", label: "Licensed / Creative Commons" },
  { id: "creator_permission", label: "Creator gave permission" },
  { id: "clipping_campaign", label: "Clipping campaign" },
];

/** The main entry point: paste a video link, confirm rights, get clips. */
export function PasteLink({ onQueued }: { onQueued?: () => void }) {
  const toast = useToast();
  const { data: accounts } = useApi<Account[]>("/accounts");
  const [url, setUrl] = useState("");
  const [rights, setRights] = useState("own");
  const [attest, setAttest] = useState(false);
  const [proof, setProof] = useState("");
  const [credit, setCredit] = useState("");
  const [niche, setNiche] = useState("");
  const [hinglish, setHinglish] = useState(false);
  const [auto, setAuto] = useState(false);
  const [top, setTop] = useState(3);
  const [pages, setPages] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    try {
      await api.post("/quick", {
        url, rights_type: rights, attest, proof_url: proof, credit_text: credit, niche,
        caption_script: hinglish ? "latin" : "native", accounts: pages, auto_publish: auto, auto_publish_top: top,
      });
      toast("ok", "Queued. Clips will appear in Review as they're ready.");
      setUrl("");
      setAttest(false);
      onQueued?.();
    } catch (err) {
      toast("err", (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const needsProof = rights !== "own";
  return (
    <form onSubmit={submit} className="card overflow-hidden">
      <div className="flex flex-col gap-3 p-5 md:flex-row md:items-center">
        <div className="relative flex-1">
          <Link2 size={18} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-mute" />
          <input className="input !rounded-2xl !py-3.5 !pl-11 text-base" placeholder="Paste a YouTube (or any) video link, or a file path…"
            value={url} onChange={(e) => setUrl(e.target.value)} autoFocus />
        </div>
        <button className="btn-primary !rounded-2xl !px-6 !py-3.5 text-base" disabled={busy || !url.trim() || !attest}
          title={!attest ? "Tick “I own this video or have permission” first" : undefined}>
          {busy ? <Loader2 size={18} className="animate-spin" /> : <Sparkles size={18} />} Make clips
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-line bg-raised/50 px-5 py-3 text-sm">
        <select className="input !w-auto !py-1.5" value={rights} onChange={(e) => setRights(e.target.value)} aria-label="Rights">
          {RIGHTS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
        <label className="flex cursor-pointer items-center gap-2">
          <input type="checkbox" checked={attest} onChange={(e) => setAttest(e.target.checked)} className="h-4 w-4 accent-[var(--brand)]" />
          <span className={url.trim() && !attest ? "text-brand" : ""}>I own this video or have permission to clip it</span>
        </label>
        <Toggle checked={auto} onChange={setAuto} label={<span>Hands-off: post the best {top} clips automatically</span>} />
        <button type="button" className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setMore(!more)}>{more ? "Fewer options" : "More options"}</button>
      </div>
      {(more || needsProof) && (
        <div className="grid gap-3 border-t border-line px-5 py-4 md:grid-cols-4">
          {needsProof && (
            <label className="md:col-span-2"><span className="label">Proof link (licence, permission email, campaign)</span>
              <input className="input" value={proof} onChange={(e) => setProof(e.target.value)} placeholder="https://…" /></label>
          )}
          <label><span className="label">Credit shown on clips</span>
            <input className="input" value={credit} onChange={(e) => setCredit(e.target.value)} placeholder="e.g. The Diary Of A CEO" /></label>
          <label><span className="label">Niche</span>
            <input className="input" value={niche} onChange={(e) => setNiche(e.target.value)} placeholder="business, fitness…" /></label>
          {more && (
            <>
              <label><span className="label">Clips to auto-post</span>
                <input type="number" min={1} max={10} className="input" value={top} onChange={(e) => setTop(Number(e.target.value))} /></label>
              <div className="md:col-span-2">
                <span className="label">Post to pages (default: all matching)</span>
                <div className="flex flex-wrap gap-2">
                  {(accounts || []).filter((a) => a.active).map((a) => (
                    <button type="button" key={a.id} onClick={() => setPages((p) => p.includes(a.id) ? p.filter((x) => x !== a.id) : [...p, a.id])}
                      className={`chip !py-1 ${pages.includes(a.id) ? "!border-brand text-ink" : ""}`}><Platform p={a.platform} size={12} />{a.name}</button>
                  ))}
                  {!accounts?.length && <span className="text-xs text-mute">Connect pages under Pages first.</span>}
                </div>
              </div>
              <div className="flex items-end"><Toggle checked={hinglish} onChange={setHinglish} label="Hindi → Hinglish captions" /></div>
            </>
          )}
        </div>
      )}
    </form>
  );
}
