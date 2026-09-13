import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Eye, EyeOff, RotateCcw } from "lucide-react";
import { api, type Clip } from "../api";
import { useApi } from "../hooks";
import { PhoneFrame } from "../components/PhoneFrame";
import { ClipEditor, useAccounts } from "../components/ClipEditor";
import { OverlayPreview } from "../components/OverlayPreview";
import { useLiveEdits } from "../hooksLive";
import { useRef } from "react";
import { Page, useToast } from "../components/ui";

export default function ClipDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const { data: clip, setData } = useApi<Clip>(`/clips/${id}`, 5000);
  const accounts = useAccounts();
  const [safe, setSafe] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const live = useLiveEdits(clip || undefined);
  if (!clip) return <Page title="Clip"><div className="text-dim">Loading…</div></Page>;

  async function approve(ids: number[], accs: number[] = [], at?: string) {
    try {
      await api.post("/clips/approve", { ids, accounts: accs, at });
      toast("ok", "Approved and scheduled");
      nav("/schedule");
    } catch (e) {
      toast("err", (e as Error).message);
    }
  }

  return (
    <Page title={`Clip #${clip.id}`} actions={clip.status === "rejected" ? (
      <button className="btn-outline" onClick={async () => setData(await api.post<Clip>(`/clips/${clip.id}/restore`))}><RotateCcw size={15} /> Restore</button>
    ) : null}>
      <div className="grid gap-6 lg:grid-cols-[minmax(300px,380px)_1fr]">
        <div>
          <PhoneFrame ref={video} src={live.live ? clip.base_url! : clip.url} poster={live.live ? clip.base_poster || undefined : clip.thumb_url} safeZones={safe} caption={clip.meta?.instagram_caption}
            overlay={live.live && clip.overlay_spec ? <OverlayPreview spec={clip.overlay_spec} words={clip.words} edits={live.edits} video={video}
              onDragCaption={(y) => live.update({ caption_y: Math.round(y * 100) / 100 })} /> : null} />
          <div className="mt-3 flex justify-center gap-2">
            <button className="btn-ghost" onClick={() => setSafe(!safe)}>{safe ? <EyeOff size={16} /> : <Eye size={16} />} Safe zones</button>
            <button className="btn-ghost" onClick={async () => {
              const r = await api.post<{ ready: boolean; url?: string }>(`/clips/${clip.id}/export`);
              if (r.ready && r.url) window.open(r.url, "_blank"); else toast("info", "Adding your text to the video (a few seconds). Click again in a moment.");
            }}>Download MP4</button>
          </div>
        </div>
        <div className="card flex h-[calc(100vh-160px)] min-h-[520px] flex-col overflow-hidden">
          <ClipEditor clip={clip} accounts={accounts} live={live} video={video} onChanged={setData} onApprove={approve}
            onReject={async (ids) => { await api.post("/clips/reject", { ids }); toast("info", "Rejected"); nav(-1); }} />
        </div>
      </div>
    </Page>
  );
}
