import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import {
  BarChart3, Bell, BrainCircuit, CalendarClock, Clapperboard, Flame, Library, Menu, MessageSquare, Moon,
  Pause, Play, Radar, Wand2, Settings as SettingsIcon, ShieldCheck, Sun, Users, Workflow, X,
} from "lucide-react";
import { api, type Summary } from "./api";
import { MemoryPanel } from "./components/MemoryPanel";
import { useApi } from "./hooks";
import Today from "./pages/Today";
import Review from "./pages/Review";
import LibraryPage from "./pages/Library";
import Sources from "./pages/Sources";
import Discover from "./pages/Discover";
import Schedule from "./pages/Schedule";
import Pages from "./pages/Pages";
import Analytics from "./pages/Analytics";
import Optimizer from "./pages/Optimizer";
import Comments from "./pages/Comments";
import Jobs from "./pages/Jobs";
import Settings from "./pages/Settings";
import Faceless from "./pages/Faceless";
import FacelessProject from "./pages/FacelessProject";
import ClipDetail from "./pages/ClipDetail";
import VideoDetail from "./pages/VideoDetail";

const NAV_GROUPS = [
  { title: "Make", items: [
    { to: "/", label: "Today", icon: Flame, end: true },
    { to: "/library", label: "Videos", icon: Library },
    { to: "/faceless", label: "Faceless studio", icon: Wand2 },
  ] },
  { title: "Publish", items: [
    { to: "/review", label: "Review", icon: Clapperboard, badge: "review" as const },
    { to: "/schedule", label: "Schedule", icon: CalendarClock },
    { to: "/pages", label: "Pages", icon: Users },
  ] },
  { title: "Grow", items: [
    { to: "/analytics", label: "Analytics", icon: BarChart3 },
    { to: "/comments", label: "Comments", icon: MessageSquare, badge: "replies_waiting" as const },
    { to: "/optimizer", label: "Optimizer", icon: BrainCircuit },
    { to: "/discover", label: "Viral finder", icon: Radar },
  ] },
  { title: "System", items: [
    { to: "/sources", label: "Rights", icon: ShieldCheck },
    { to: "/jobs", label: "Activity", icon: Workflow },
    { to: "/settings", label: "Settings", icon: SettingsIcon },
  ] },
];

export default function App() {
  const { data: summary, reload } = useApi<Summary>("/summary", 8000);
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<string>(() => document.documentElement.dataset.theme || "dark");
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("cf-theme", next); } catch { /* ignore */ }
  }

  async function togglePause() {
    await api.post("/automation", { paused: !summary?.paused });
    reload();
  }

  return (
    <div className="flex h-full">
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col border-r border-line bg-surface transition-transform md:static md:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex h-14 items-center gap-2 px-5">
          <img src="/icon.svg" className="h-7 w-7" alt="" />
          <span className="font-semibold tracking-tight">ClipForge</span>
          <button className="btn-ghost ml-auto !p-1.5 md:hidden" onClick={() => setOpen(false)} aria-label="Close menu"><X size={16} /></button>
        </div>
        <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-1">
          {NAV_GROUPS.map((g) => (
            <div key={g.title} className="pb-2">
              <div className="px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-mute">{g.title}</div>
              {g.items.map((n) => {
                const count = "badge" in n && n.badge && summary ? summary[n.badge] : 0;
                return (
                  <NavLink key={n.to} to={n.to} end={"end" in n ? n.end : false}
                    className={({ isActive }) => `flex items-center gap-3 rounded-xl px-3 py-1.5 text-sm ${isActive ? "bg-hover text-ink" : "text-dim hover:bg-hover hover:text-ink"}`}>
                    <n.icon size={16} />
                    <span>{n.label}</span>
                    {count ? <span className="ml-auto rounded-full bg-brand px-1.5 text-[11px] font-semibold text-brand-ink">{count}</span> : null}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="space-y-2 border-t border-line p-3">
          <MemoryPanel />
          <button onClick={togglePause} className={`btn w-full ${summary?.paused ? "bg-warn/15 text-warn" : "bg-good/10 text-good"}`}>
            {summary?.paused ? <Play size={15} /> : <Pause size={15} />}
            {summary?.paused ? "Automation paused" : "Automation running"}
          </button>
          <button onClick={toggleTheme} className="btn-ghost w-full">{theme === "dark" ? <Sun size={15} /> : <Moon size={15} />} {theme === "dark" ? "Light" : "Dark"} mode</button>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/50 md:hidden" onClick={() => setOpen(false)} />}

      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-page/80 px-4 backdrop-blur md:hidden">
          <button className="btn-ghost !p-1.5" onClick={() => setOpen(true)} aria-label="Open menu"><Menu size={18} /></button>
          <span className="font-semibold">ClipForge</span>
          <NavLink to="/" className="ml-auto text-dim"><Bell size={18} /></NavLink>
        </div>
        <Routes>
          <Route path="/" element={<Today summary={summary} />} />
          <Route path="/review" element={<Review />} />
          <Route path="/clips/:id" element={<ClipDetail />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/videos/:id" element={<VideoDetail />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/discover" element={<Discover />} />
          <Route path="/schedule" element={<Schedule />} />
          <Route path="/pages" element={<Pages />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/optimizer" element={<Optimizer />} />
          <Route path="/comments" element={<Comments />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/faceless" element={<Faceless />} />
          <Route path="/faceless/:id" element={<FacelessProject />} />
          <Route path="*" element={<Today summary={summary} />} />
        </Routes>
      </main>
    </div>
  );
}
