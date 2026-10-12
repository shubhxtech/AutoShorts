import { useEffect, useRef, useState } from "react";
import { api, type Clip } from "./api";
import type { OverlayEdits } from "./components/OverlayPreview";

/** Live text edits for a clip: applied instantly in the preview, saved quietly in the background. */
export function useLiveEdits(clip: Clip | undefined) {
  const [edits, setEdits] = useState<OverlayEdits>({});
  const [saving, setSaving] = useState(false);
  const pending = useRef<OverlayEdits | null>(null);
  const timer = useRef<number>();
  const id = clip?.id;

  useEffect(() => { setEdits(clip?.overlay || {}); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  function update(patch: OverlayEdits) {
    setEdits((e) => {
      const next = { ...e, ...patch };
      pending.current = next;
      return next;
    });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      if (!id || !pending.current) return;
      setSaving(true);
      try { await api.patch(`/clips/${id}`, { overlay: pending.current, rerender: false }); } finally { setSaving(false); }
    }, 600);
  }
  return { edits, update, saving, live: !!clip?.base_url && !!clip?.overlay_spec };
}
