import { Link } from "react-router-dom";
import { Film } from "lucide-react";
import { fmt, type Video } from "../api";
import { useApi } from "../hooks";
import { Empty, Page, RightsBadge, StatusPill } from "../components/ui";
import { PasteLink } from "../components/PasteLink";

export default function LibraryPage() {
  const { data, reload } = useApi<Video[]>("/videos?status=queued,downloading,transcribing,analyzing,mining,rendering,ready,failed,skipped&limit=200", 6000);
  return (
    <Page title="Library" subtitle="Every video you've given ClipForge, with its clips and transcript.">
      <PasteLink onQueued={reload} />
      <div className="mt-6">
        {!data?.length ? (
          <Empty icon={<Film size={28} />} title="No videos yet">Paste a link above to get started.</Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-mute">
                <tr className="border-b border-line">
                  <th className="px-5 py-3 font-medium">Video</th><th className="px-3 py-3 font-medium">Status</th>
                  <th className="px-3 py-3 font-medium">Length</th><th className="px-3 py-3 font-medium">Clips</th>
                  <th className="px-3 py-3 font-medium">Rights</th><th className="px-3 py-3 font-medium">Added</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.map((v) => {
                  const total = Object.values(v.clip_counts || {}).reduce((a, b) => a + b, 0);
                  return (
                    <tr key={v.id} className="hover:bg-raised/50">
                      <td className="px-5 py-3">
                        <Link to={`/videos/${v.id}`} className="flex items-center gap-3">
                          {v.thumbnail ? <img src={v.thumbnail} className="h-10 w-16 rounded-md object-cover" alt="" /> : <div className="h-10 w-16 rounded-md bg-hover" />}
                          <div className="min-w-0"><div className="truncate font-medium hover:underline">{v.title || v.url}</div><div className="truncate text-xs text-mute">{v.channel || v.source_name}</div></div>
                        </Link>
                      </td>
                      <td className="px-3 py-3"><StatusPill status={v.status} />{v.error && <div className="mt-1 max-w-[220px] truncate text-xs text-bad" title={v.error}>{v.error}</div>}</td>
                      <td className="tabular px-3 py-3 text-dim">{v.duration_s ? fmt.dur(v.duration_s) : "–"}</td>
                      <td className="px-3 py-3 text-dim">{total ? `${v.clip_counts.review || 0} to review · ${total} total` : "–"}</td>
                      <td className="px-3 py-3"><RightsBadge rights={v.rights} compact /></td>
                      <td className="px-3 py-3 text-dim">{fmt.ago(v.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Page>
  );
}
