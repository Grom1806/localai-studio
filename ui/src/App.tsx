import React, { createContext, useContext, useEffect, useState } from "react";
import {
  Activity, AudioLines, Boxes, ChevronDown, Clapperboard, History, Image as ImageIcon, LayoutDashboard,
  Settings as SettingsIcon, Sparkles, UserRound,
} from "lucide-react";
import { useStatus, Meter } from "./components/common";
import { fmtMB } from "./api";
import { FreeRamButton } from "./components/cleanup";
import Dashboard from "./pages/Dashboard";
import ImagePage from "./pages/ImagePage";
import ImageEditPage from "./pages/ImageEditPage";
import ImageUpscalePage from "./pages/ImageUpscalePage";
import VideoPage from "./pages/VideoPage";
import VideoEditPage from "./pages/VideoEditPage";
import DubPage from "./pages/DubPage";
import AvatarPage from "./pages/AvatarPage";
import ClipsPage from "./pages/ClipsPage";
import { VideoUpscalePage, InterpolatePage } from "./pages/VideoToolsPages";
import VoicePage from "./pages/VoicePage";
import ModelsPage from "./pages/ModelsPage";
import HistoryPage from "./pages/HistoryPage";
import SystemPage from "./pages/SystemPage";
import SettingsPage from "./pages/SettingsPage";

export type Page =
  | "dashboard"
  | "image/generate" | "image/edit" | "image/upscale"
  | "avatar/create" | "avatar/library" | "video/translate"
  | "video/generate" | "video/edit" | "video/dub" | "video/clips" | "video/upscale" | "video/interpolate"
  | "voice/tts" | "voice/clone" | "voice/library"
  | "history" | "models" | "system" | "settings";
export type Handoff = { image?: string; video?: string; prompt?: string; text?: string; seed?: number; params?: Record<string, any> } | null;

const NavCtx = createContext<{ go: (p: Page, h?: Handoff) => void; handoff: Handoff; takeHandoff: (p: Page) => Handoff }>({
  go: () => {}, handoff: null, takeHandoff: () => null,
});
export const useNav = () => useContext(NavCtx);

type Group = { id: string; label: string; icon: React.ElementType; children?: { id: Page; label: string }[]; page?: Page };
const NAV: Group[] = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, page: "dashboard" },
  { id: "avatar", label: "Avatar", icon: UserRound, children: [
    { id: "avatar/create", label: "Create Avatar" }, { id: "avatar/library", label: "Saved Avatars" }] },
  { id: "image", label: "Image", icon: ImageIcon, children: [
    { id: "image/generate", label: "Generate" }, { id: "image/edit", label: "Edit" }, { id: "image/upscale", label: "Upscale & tools" }] },
  { id: "video", label: "Video", icon: Clapperboard, children: [
    { id: "video/generate", label: "Generate · LTX-2.5" }, { id: "video/translate", label: "Translate Video" }, { id: "video/edit", label: "Edit" }, { id: "video/dub", label: "Dub" },
    { id: "video/clips", label: "Clips" }, { id: "video/upscale", label: "Upscale" }, { id: "video/interpolate", label: "Interpolate" }] },
  { id: "voice", label: "Voice", icon: AudioLines, children: [
    { id: "voice/tts", label: "TTS" }, { id: "voice/clone", label: "Voice Clone" }, { id: "voice/library", label: "Saved Voices" }] },
];
const NAV2: Group[] = [
  { id: "history", label: "History", icon: History, page: "history" },
  { id: "models", label: "Models", icon: Boxes, page: "models" },
  { id: "system", label: "System", icon: Activity, page: "system" },
  { id: "settings", label: "Settings", icon: SettingsIcon, page: "settings" },
];
const TITLES: Record<Page, string> = {
  dashboard: "Dashboard", "avatar/create": "Avatar · Create", "avatar/library": "Avatar · Saved avatars",
  "video/translate": "Video · Translate (dub + lip sync)",
  "image/generate": "Image · Generate", "image/edit": "Image · Edit", "image/upscale": "Image · Upscale & tools",
  "video/generate": "Video · Generate (LTX-2.5)", "video/edit": "Video · Edit", "video/dub": "Video · Dub",
  "video/clips": "Video · Clips", "video/upscale": "Video · Upscale", "video/interpolate": "Video · Frame interpolation",
  "voice/tts": "Voice · Text to speech", "voice/clone": "Voice · Voice clone", "voice/library": "Voice · Saved voices",
  history: "History", models: "Models", system: "System", settings: "Settings",
};
// old single-level hashes keep working
const ALIASES: Record<string, Page> = { image: "image/generate", video: "video/generate", voice: "voice/tts" };

function pageFromHash(): Page {
  const h = decodeURIComponent(location.hash.slice(1));
  const p = (ALIASES[h] ?? h) as Page;
  return TITLES[p] ? p : "dashboard";
}

export default function App() {
  const [page, setPage] = useState<Page>(pageFromHash());
  const [open, setOpen] = useState<Record<string, boolean>>({ avatar: true, image: true, video: true, voice: true });
  const [handoff, setHandoff] = useState<{ page: Page; data: Handoff } | null>(null);
  const { status, offline } = useStatus();

  useEffect(() => {
    const f = () => setPage(pageFromHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);

  useEffect(() => {
    // Do not let LocalAI media keep playing when the user switches to YouTube
    // or another app. Playback stays paused when focus returns; the user can
    // start it again explicitly from the controls.
    const pauseMediaWhenInactive = () => {
      if (document.visibilityState !== "hidden" && document.hasFocus()) return;
      document.querySelectorAll<HTMLMediaElement>("audio, video").forEach((media) => media.pause());
    };
    document.addEventListener("visibilitychange", pauseMediaWhenInactive);
    window.addEventListener("blur", pauseMediaWhenInactive);
    return () => {
      document.removeEventListener("visibilitychange", pauseMediaWhenInactive);
      window.removeEventListener("blur", pauseMediaWhenInactive);
    };
  }, []);

  const go = (p: Page, h?: Handoff) => {
    if (h) setHandoff({ page: p, data: h });
    location.hash = p;
    setPage(p);
  };
  const takeHandoff = (p: Page) => {
    if (handoff && handoff.page === p) { const d = handoff.data; setHandoff(null); return d; }
    return null;
  };

  const g = status?.gpu;
  const job = status?.current_job;
  const fullScreenEditor = page === "video/edit";
  const Pages: Record<Page, React.ElementType> = {
    dashboard: Dashboard, "avatar/create": () => <AvatarPage mode="create" />, "avatar/library": () => <AvatarPage mode="library" />,
    "video/translate": () => <DubPage mode="translate" />, "image/generate": ImagePage, "image/edit": ImageEditPage, "image/upscale": ImageUpscalePage,
    "video/generate": VideoPage, "video/edit": VideoEditPage, "video/dub": DubPage, "video/clips": ClipsPage,
    "video/upscale": VideoUpscalePage, "video/interpolate": InterpolatePage,
    "voice/tts": () => <VoicePage mode="tts" />, "voice/clone": () => <VoicePage mode="clone" />,
    "voice/library": () => <VoicePage mode="library" />,
    history: HistoryPage, models: ModelsPage, system: SystemPage, settings: SettingsPage,
  };
  const PageEl = Pages[page];
  const jobPage: Record<string, Page> = { image: "image/generate", image_edit: "image/edit", image_upscale: "image/upscale",
    remove_bg: "image/upscale", video: "video/generate", avatar: "avatar/create", dub_line: "video/dub", video_edit: "video/edit", dub_analyze: "video/dub", dub_render: "video/dub",
    clips_analyze: "video/clips", clips_export: "video/clips", video_upscale: "video/upscale", interpolate: "video/interpolate", voice: "voice/tts" };

  const renderGroup = (grp: Group) => {
    if (grp.page) return <NavItem key={grp.id} label={grp.label} icon={grp.icon} active={page === grp.page} onClick={() => go(grp.page!)} />;
    const isOpen = open[grp.id];
    const activeChild = page.startsWith(grp.id + "/");
    return (
      <div key={grp.id}>
        <button className={`nav-item ${activeChild && !isOpen ? "active" : ""}`} onClick={() => setOpen({ ...open, [grp.id]: !isOpen })}>
          <grp.icon size={17} /><span className="grow">{grp.label}</span>
          <ChevronDown size={14} style={{ transform: isOpen ? "none" : "rotate(-90deg)", transition: "transform .15s" }} />
        </button>
        {isOpen && grp.children!.map((c) => (
          <button key={c.id} className={`nav-item sub ${page === c.id ? "active" : ""}`} onClick={() => go(c.id)}>{c.label}</button>
        ))}
      </div>
    );
  };

  return (
    <NavCtx.Provider value={{ go, handoff: handoff?.data ?? null, takeHandoff }}>
      <div className={`app ${fullScreenEditor ? "app-editor" : ""}`}>
        {!fullScreenEditor && <aside className="sidebar">
          <div className="brand"><div className="brand-mark"><Sparkles size={16} color="white" /></div>LocalAI Studio</div>
          {NAV.map(renderGroup)}
          <div className="nav-sep" />
          {NAV2.map(renderGroup)}
          <div className="sidebar-foot">
            Everything runs locally on this PC.<br />
            <span className="faint">127.0.0.1 only · no telemetry</span>
          </div>
        </aside>}
        <main className={`main ${fullScreenEditor ? "main-editor" : ""}`}>
          {!fullScreenEditor && <div className="topbar">
            <div className="title">{TITLES[page]}</div>
            <div className="spacer" />
            {offline && <span className="pill error"><span className="dot" />SERVER OFFLINE</span>}
            {job && (
              <button className="btn ghost sm" onClick={() => go(jobPage[job.kind] ?? "dashboard")}>
                <span className="pill loading"><span className="dot" />{job.kind.replace("_", " ").toUpperCase()}</span>
                <span className="small">{Math.round(job.progress * 100)}% · {job.message}</span>
              </button>
            )}
            {!!status?.queue_length && <span className="small muted">{status.queue_length} queued</span>}
            {g?.available && (
              <div className="mini-vram" title={g.name}>
                <span>VRAM</span>
                <Meter value={g.vram_used_mb} max={g.vram_total_mb} />
                <span>{fmtMB(g.vram_used_mb)} / {fmtMB(g.vram_total_mb)}</span>
                {g.temperature_c != null && <span>{g.temperature_c}°C</span>}
              </div>
            )}
            {status && <span className="small muted" title={`${fmtMB(status.ram_total_mb - status.ram_available_mb)} / ${fmtMB(status.ram_total_mb)}`}>
              RAM {Math.round(status.ram_percent)}%</span>}
            <FreeRamButton />
          </div>}
          <div className={`content ${fullScreenEditor ? "content-editor" : ""}`}><PageEl key={page} /></div>
        </main>
      </div>
    </NavCtx.Provider>
  );
}

function NavItem({ label, icon: Icon, active, onClick }: { label: string; icon: React.ElementType; active: boolean; onClick: () => void }) {
  return <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}><Icon size={17} />{label}</button>;
}
