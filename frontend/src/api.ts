// Thin client for the ClipForge backend.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown, isForm = false): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body && !isForm ? { "Content-Type": "application/json" } : undefined,
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
  upload: <T>(p: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<T>("POST", p, fd, true);
  },
};

// ---------------------------------------------------------------- types

export type Rights = { ok: boolean; label: string; detail: string; days_left: number | null; platforms: string[] };

export type Summary = {
  ingested: number; generated: number; passed: number; review: number; scheduled: number;
  published_today: number; failed_posts: number; replies_waiting: number; processing: number;
  views_24h: number; views_72h: number; earnings_72h: number; unread_events: number; paused: boolean;
};

export type EventItem = { id: number; kind: string; level: string; title: string; body: string; created_at: string; read: boolean; data: Record<string, unknown> };

export type QC = { name: string; passed: boolean; detail: string; blocking: boolean };

export type Hook = { text: string; style: string };

export type Clip = {
  id: number; moment_id: number; video_id: number; source_id: number; variant: string;
  hook_text: string; hook_style: string; caption_style: string; edit_preset: string; length_bucket: string; opening: string;
  layout: string; music: string; duration_s: number; status: string; error: string;
  qc: QC[]; qc_passed: boolean; predicted_score: number; predicted_views: number;
  url: string; thumb_url: string; rights: Rights; source_name: string; video_title: string;
  meta: Record<string, any>; caption_edits: Record<string, string>; target_accounts: number[];
  render_stats: Record<string, any>; words: [number, string, number, number, number][];
  moment: { title: string; scores: Record<string, number>; final_score: number; reason: string; hooks: Hook[]; text: string; start_s: number; end_s: number } | null;
  posts: Post[]; created_at: string; faceless?: boolean; voice?: string; base_url?: string; base_poster?: string; needs_burn?: boolean;
  overlay_spec?: import("./components/OverlayPreview").OverlaySpec | null; overlay?: import("./components/OverlayPreview").OverlayEdits; siblings?: { id: number; variant: string; status: string; thumb_url: string }[];
};

export type Account = {
  id: number; platform: "youtube" | "instagram"; name: string; handle: string; avatar: string; niche: string;
  language: string; voice: string; cadence_per_day: number; time_slots: string[]; timezone: string;
  min_gap_minutes: number; learn_slots: boolean; privacy_status: string; default_hashtags: string;
  google_project: string; auto_reply_categories: string[]; auto_hide_spam: boolean; active: boolean;
  status: string; status_detail: string; token_expires_at: string | null; login: string;
  published: number; views: number; followers_gained: number; earnings: number; scheduled: number; quota_left?: number;
};

export type Post = {
  id: number; clip_id: number; account_id: number; platform: string; scheduled_at: string; status: string;
  error: string; title: string; description: string; caption: string; url: string; published_at: string | null;
  views: number; likes: number; comments: number; shares: number; saves: number; followers_gained: number;
  earnings: number; views_24h: number | null; views_72h: number | null; avg_view_pct: number;
  thumb_url?: string; clip_url?: string; hook_text?: string; account_name?: string; account_avatar?: string;
};

export type Source = {
  id: number; name: string; kind: string; url: string; extra_urls: string[]; rights_type: string; proof_kind: string;
  proof_url: string; proof_file: string; proof_notes: string; allowed_platforms: string[]; credit_text: string;
  credit_link: string; revenue_terms: string; payout_per_1k: number | null; expires_at: string | null; active: boolean;
  niche: string; language: string; caption_script: string; watch: boolean; watch_interval_hours: number;
  auto_ingest: boolean; min_viral_score: number; max_video_age_days: number; max_ingest_per_check: number;
  min_duration_s: number; last_checked_at: string | null; rights: Rights; video_count: number; processed_count: number;
};

export type Video = {
  id: number; source_id: number; title: string; channel: string; url: string; thumbnail: string; external_id: string;
  published_at: string | null; duration_s: number; view_count: number; viral_score: number; velocity: number;
  status: string; progress: number; stage_message: string; error: string; source_name: string; rights: Rights;
  clip_counts: Record<string, number>; created_at: string; moments?: any[]; transcript_path?: string; language?: string;
};

export type Comment = {
  id: number; post_id: number; account_id: number; platform: string; author: string; text: string;
  category: string; needs_attention: boolean; draft_reply: string; reply_status: string; error: string;
  post_title: string; post_url: string; account_name: string; created_at: string;
};

export type Job = { id: number; kind: string; lane: string; status: string; progress: number; message: string; error: string; attempts: number; payload: any; created_at: string; started_at: string | null };

// ---------------------------------------------------------------- formatting

export const fmt = {
  n(v: number | null | undefined): string {
    if (v === null || v === undefined) return "–";
    if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`;
    if (Math.abs(v) >= 1e4) return `${(v / 1e3).toFixed(0)}k`;
    if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
    return `${Math.round(v)}`;
  },
  money(v: number): string {
    return `$${v.toFixed(v >= 100 ? 0 : 2)}`;
  },
  dur(s: number): string {
    const m = Math.floor(s / 60);
    const r = Math.round(s % 60);
    return m ? `${m}:${String(r).padStart(2, "0")}` : `${r}s`;
  },
  ago(iso: string | null | undefined): string {
    if (!iso) return "–";
    const d = (Date.now() - new Date(iso).getTime()) / 1000;
    if (d < 0) return fmt.until(iso);
    if (d < 60) return "just now";
    if (d < 3600) return `${Math.floor(d / 60)}m ago`;
    if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
    return `${Math.floor(d / 86400)}d ago`;
  },
  until(iso: string): string {
    const d = (new Date(iso).getTime() - Date.now()) / 1000;
    if (d < 60) return "now";
    if (d < 3600) return `in ${Math.floor(d / 60)}m`;
    if (d < 86400) return `in ${Math.floor(d / 3600)}h`;
    return `in ${Math.floor(d / 86400)}d`;
  },
  when(iso: string | null | undefined): string {
    if (!iso) return "–";
    return new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  },
  label(s: string): string {
    return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  },
};
