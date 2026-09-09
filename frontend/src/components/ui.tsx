import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, ShieldAlert, ShieldCheck, X, XCircle } from "lucide-react";
import type { Rights } from "../api";

export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 pb-16 pt-6 md:px-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-dim">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

export function Card({ title, actions, children, className = "", pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-5 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      <div className={pad ? "p-5" : ""}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, accent = false }: { label: string; value: ReactNode; hint?: ReactNode; accent?: boolean }) {
  return (
    <div className="card px-5 py-4">
      <div className="text-xs font-medium uppercase tracking-wide text-mute">{label}</div>
      <div className={`mt-1 text-3xl font-semibold ${accent ? "text-brand" : ""}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-dim">{hint}</div>}
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  review: "text-brand", approved: "text-good", scheduled: "text-accent", published: "text-good",
  rejected: "text-mute", qc_failed: "text-bad", failed: "text-bad", rendering: "text-dim",
  publishing: "text-accent", canceled: "text-mute", ready: "text-good", skipped: "text-mute",
  discovered: "text-dim", queued: "text-dim", downloading: "text-accent", transcribing: "text-accent",
  analyzing: "text-accent", mining: "text-accent", connected: "text-good", expired: "text-bad", error: "text-bad",
  running: "text-accent", done: "text-good", draft: "text-brand", drafted: "text-brand", posted: "text-good", hidden: "text-mute", new: "text-dim",
};

export function StatusPill({ status }: { status: string }) {
  const busy = ["rendering", "publishing", "downloading", "transcribing", "analyzing", "mining", "running"].includes(status);
  return (
    <span className={`chip ${STATUS_STYLE[status] ?? "text-dim"}`}>
      {busy && <Loader2 size={11} className="animate-spin" />}
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function RightsBadge({ rights, compact = false }: { rights: Rights; compact?: boolean }) {
  const Icon = rights.ok ? ShieldCheck : ShieldAlert;
  return (
    <span title={rights.detail} className={`chip ${rights.ok ? "text-good" : "text-bad"}`}>
      <Icon size={12} />
      {rights.label}
      {!compact && rights.days_left !== null && rights.days_left !== undefined && rights.days_left < 30 && (
        <span className="text-warn">· {rights.days_left}d left</span>
      )}
    </span>
  );
}

export function Platform({ p, size = 14 }: { p: string; size?: number }) {
  if (p === "youtube")
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-label="YouTube"><rect x="1" y="4" width="22" height="16" rx="5" fill="#ff0033" /><path d="M10 8.5v7l6-3.5z" fill="#fff" /></svg>
    );
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-label="Instagram">
      <defs><linearGradient id="igg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stopColor="#feda75" /><stop offset=".5" stopColor="#d62976" /><stop offset="1" stopColor="#4f5bd5" /></linearGradient></defs>
      <rect x="2" y="2" width="20" height="20" rx="6" fill="url(#igg)" /><circle cx="12" cy="12" r="4.2" fill="none" stroke="#fff" strokeWidth="2" /><circle cx="17.3" cy="6.7" r="1.2" fill="#fff" />
    </svg>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line px-6 py-14 text-center">
      {icon && <div className="mb-3 text-mute">{icon}</div>}
      <div className="font-medium">{title}</div>
      {children && <div className="mt-2 max-w-md text-sm text-dim">{children}</div>}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 shrink-0 rounded-full border transition-colors ${checked ? "border-brand bg-brand" : "border-line bg-hover"}`}>
        <span className={`absolute left-0.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 rounded-full bg-white shadow transition-transform duration-150 ${checked ? "translate-x-4" : "translate-x-0"}`} />
      </button>
      {label}
    </label>
  );
}

export function Modal({ open, onClose, title, children, wide = false }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 pt-[8vh]" onClick={onClose}>
      <div className={`card w-full ${wide ? "max-w-3xl" : "max-w-lg"}`} onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="font-semibold">{title}</h2>
          <button className="btn-ghost !p-1.5" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </header>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-mute">{hint}</span>}
    </label>
  );
}

// ---------------------------------------------------------------- toasts

type Toast = { id: number; kind: "ok" | "err" | "info"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(380px,calc(100vw-32px))] flex-col gap-2">
        {toasts.map((t) => {
          const Icon = t.kind === "ok" ? CheckCircle2 : t.kind === "err" ? XCircle : Info;
          return (
            <div key={t.id} className="card pointer-events-auto flex items-start gap-2 px-4 py-3 text-sm shadow-xl">
              <Icon size={16} className={t.kind === "ok" ? "text-good" : t.kind === "err" ? "text-bad" : "text-accent"} />
              <span>{t.text}</span>
            </div>
          );
        })}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

export function LevelIcon({ level }: { level: string }) {
  if (level === "success") return <CheckCircle2 size={16} className="text-good" />;
  if (level === "error") return <XCircle size={16} className="text-bad" />;
  if (level === "warning") return <AlertTriangle size={16} className="text-warn" />;
  return <Info size={16} className="text-accent" />;
}

export function Score({ value, max = 10 }: { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(1, value / max));
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-hover">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${pct * 100}%` }} />
      </span>
      <span className="tabular text-xs text-dim">{value.toFixed(1)}</span>
    </span>
  );
}
