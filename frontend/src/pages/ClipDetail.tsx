import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Download, Eye, EyeOff, RotateCcw } from "lucide-react";
import { api, type Clip } from "../api";
import { useApi } from "../hooks";
import { PhoneFrame } from "../components/PhoneFrame";
import { ClipEditor, useAccounts } from "../components/ClipEditor";
import { OverlayPreview } from "../components/OverlayPreview";
import { EditorTimeline, usePlayerWidth } from "../components/EditorTimeline";
import { useLiveEdits } from "../hooksLive";
import { useVirtualPlayer } from "../hooksVirtual";
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
  const vp = useVirtualPlayer(clip || undefined, video);
  const playerSlot = useRef<HTMLDivElement>(null);
  const pw = usePlayerWidth(playerSlot, clip?.id);
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
      <div className="grid gap-6 lg:grid-cols-[auto_1fr]">
        <div ref={playerSlot}>
          <PhoneFrame ref={video} src={live.live ? clip.base_url! : clip.url} poster={live.live ? clip.base_poster || undefined : clip.thumb_url} safeZones={safe} maxWidth={pw} caption={clip.meta?.instagram_caption}
            overlay={live.live && clip.overlay_spec ? <OverlayPreview spec={clip.overlay_spec} words={clip.words} edits={live.edits} video={video}
              onDragCaption={(y) => live.update({ caption_y: Math.round(y * 100) / 100 })} clock={vp.clock} /> : null} />
        </div>
        <div className="card flex min-h-[480px] flex-col overflow-hidden" style={{ height: Math.round((pw * 19.5) / 9) }}>
          <ClipEditor clip={clip} accounts={accounts} live={live} video={video} vp={vp} onChanged={setData} onApprove={approve}
            onReject={async (ids) => { await api.post("/clips/reject", { ids }); toast("info", "Rejected"); nav(-1); }} />
        </div>
      </div>
      <div className="mt-3">
        <EditorTimeline clip={clip} video={video} vp={vp} edits={live.edits} busy={clip.status === "rendering"}
          extra={<>
            <button type="button" className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setSafe(!safe)}>{safe ? <EyeOff size={13} /> : <Eye size={13} />} Safe zones</button>
            <button type="button" className="btn-ghost !px-2 !py-1 text-xs" onClick={async () => {
              const r = await api.post<{ ready: boolean; url?: string }>(`/clips/${clip.id}/export`);
              if (r.ready && r.url) window.open(r.url, "_blank"); else toast("info", "Adding your text to the video (a few seconds). Click again in a moment.");
            }}><Download size={13} /> MP4</button>
          </>}
          onTrim={async (tr) => {
            try {
              setData(await api.patch<Clip>(`/clips/${clip.id}`, { trim_start: tr.start, trim_end: tr.end, cut: [] }));
              toast("info", "Trimming: re-cutting from the clip's master (about 15 seconds)");
            } catch (e) { toast("err", (e as Error).message); }
          }} />
      </div>
    </Page>
  );
}
