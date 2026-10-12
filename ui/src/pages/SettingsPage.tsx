import React, { useEffect, useState } from "react";
import { FolderOpen, Upload } from "lucide-react";
import { api, openFolder } from "../api";
import { Field, Seg, useToast } from "../components/common";
import { getLang, setLang, type Lang } from "../i18n";

type S = { vram_mode: string; image_default_preset: string; video_default_preset: string; voice_default_preset: string;
  voice_default_engine: string; ram_reserve_gb: number; vram_margin_gb: number; ports: Record<string, number>; open_browser: boolean };
type YoutubeStatus = { configured: boolean; connected: boolean; connecting: boolean; connection_error: string | null;
  channel: { id: string; title: string; custom_url?: string } | null;
  channels: { id: string; title: string; custom_url?: string }[]; target_channel: string; target_ready: boolean };

export default function SettingsPage() {
  const toast = useToast();
  const [s, setS] = useState<S | null>(null);
  const [yt, setYt] = useState<YoutubeStatus | null>(null);
  const [authLink, setAuthLink] = useState("");
  useEffect(() => { api.get<S>("/api/settings").then(setS); api.get<YoutubeStatus>("/api/youtube/status").then(setYt).catch(() => {}); }, []);
  const save = async (patch: Partial<S>) => {
    try { setS(await api.put<S>("/api/settings", patch)); toast("Saved", "ok"); } catch (e: any) { toast(e.message, "err"); }
  };
  const setYoutubeClient = async (file?: File) => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      setYt(await api.post<YoutubeStatus>("/api/youtube/client", parsed));
      toast("Google Desktop OAuth client saved securely", "ok");
    } catch (e: any) { toast(e.message || "Could not read the Google OAuth JSON file", "err"); }
  };
  const connectYoutube = async () => {
    const popup = window.open("about:blank", "_blank");
    try {
      const r = await api.post<{ auth_url: string }>("/api/youtube/connect", {});
      setAuthLink(r.auth_url);
      if (popup) popup.location.href = r.auth_url;
      else toast("Allow pop-ups, then open the Google authorization link below", "err");
      for (let i = 0; i < 610; i++) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const current = await api.get<YoutubeStatus>("/api/youtube/status");
        setYt(current);
        if (!current.connecting) {
          if (current.connected && current.target_ready) toast("YouTube channel connected", "ok");
          else if (current.connection_error) toast(current.connection_error, "err");
          break;
        }
      }
    } catch (e: any) { popup?.close(); toast(e.message, "err"); }
  };
  const disconnectYoutube = async () => {
    try { setYt(await api.post<YoutubeStatus>("/api/youtube/disconnect", {})); toast("YouTube account disconnected", "ok"); }
    catch (e: any) { toast(e.message, "err"); }
  };
  if (!s) return null;
  return (
    <div className="col" style={{ gap: 16, maxWidth: 820 }}>
      <div className="card col">
        <h3>Interface language</h3>
        <div className="notranslate"><Seg value={getLang()} onChange={(v) => setLang(v as Lang)} options={[
          { v: "ru", label: "Русский" }, { v: "en", label: "English" }]} /></div>
      </div>
      <div className="card col">
        <h3 className="row"><Upload size={19} /> YouTube publishing</h3>
        <div className="small muted">Connect a Google account once. LocalAI uploads only when you review a clip and press its final upload button.</div>
        <div className="small muted">Setup: in Google Cloud, enable YouTube Data API v3, create an OAuth client with type Desktop app, download its JSON file, then choose that file here. Connect the Google account/channel you want to publish to.</div>
        {yt?.configured && <div className="small">Current Google OAuth Desktop client: ready. Choose another JSON below to replace it.</div>}
        <div className="col" style={{ gap: 6 }}>
          <div className="small muted">{yt?.configured ? "Replace OAuth client JSON" : "Choose OAuth client JSON"} · must be a Google OAuth client of type Desktop app. LocalAI encrypts its secret on this computer.</div>
          <input type="file" accept="application/json,.json" onChange={(e) => { setYoutubeClient(e.target.files?.[0]); e.currentTarget.value = ""; }} />
        </div>
        {yt?.connected && <div className="col" style={{ gap: 8 }}>
          <div className="row wrap">
            <span className={`pill ${yt.target_ready ? "online" : "offline"}`}>{yt.target_ready ? "Correct channel connected" : "Channel could not be verified"}</span>
          </div>
          <div className="small">Google returned: <b>{yt.channels.map((c) => c.title).join(", ") || "no YouTube channel"}</b> · Required: <b>{yt.target_channel}</b></div>
          {!yt.target_ready && <div className="small faint">For safety, LocalAI only uploads when Google confirms exactly one channel (and, if set, that it matches the channel in config\settings.json → youtube_channel). Reconnect Google with the account/channel you want to publish to.</div>}
        </div>}
        {yt?.connection_error && <div className="small" style={{ color: "var(--danger, #f87171)" }}>{yt.connection_error}</div>}
        <div className="row wrap">
          {yt?.configured && <button className="btn primary" disabled={!!yt.connecting} onClick={connectYoutube}>{yt.connecting ? "Waiting for Google…" : yt.connected ? "Reconnect Google account" : "Connect Google account"}</button>}
          {yt?.connected && <button className="btn" onClick={disconnectYoutube}>Disconnect</button>}
          {authLink && <a className="btn ghost" href={authLink} target="_blank" rel="noreferrer">Open Google authorization</a>}
        </div>
        <div className="small faint">YouTube may restrict API uploads to private visibility until the Google API project passes Google's audit.</div>
      </div>
      <div className="card col">
        <h3>VRAM management</h3>
        <Seg value={s.vram_mode} onChange={(v) => save({ vram_mode: v })} options={[
          { v: "auto", label: "Auto (recommended)" }, { v: "keep_loaded", label: "Keep loaded" }, { v: "low_vram", label: "Low VRAM" }]} />
        <div className="small muted">
          {s.vram_mode === "auto" && "Unloads other models only when the next job needs the memory. Video always gets the GPU to itself."}
          {s.vram_mode === "keep_loaded" && "Keeps models in VRAM between jobs for faster switching. Unloads only when memory actually runs out."}
          {s.vram_mode === "low_vram" && "Unloads everything before and after each job. Slowest, but leaves the GPU free for games and other apps."}
        </div>
        <div className="grid cols-2">
          <Field label="Keep free system RAM (GB)"><input type="number" step={0.5} value={s.ram_reserve_gb} onChange={(e) => setS({ ...s, ram_reserve_gb: Number(e.target.value) })} onBlur={() => save({ ram_reserve_gb: s.ram_reserve_gb })} /></Field>
          <Field label="VRAM safety margin (GB)"><input type="number" step={0.5} value={s.vram_margin_gb} onChange={(e) => setS({ ...s, vram_margin_gb: Number(e.target.value) })} onBlur={() => save({ vram_margin_gb: s.vram_margin_gb })} /></Field>
        </div>
      </div>
      <div className="card col">
        <h3>Defaults</h3>
        <div className="grid cols-2">
          <Field label="Image preset"><select value={s.image_default_preset} onChange={(e) => save({ image_default_preset: e.target.value })}>
            <option value="fast">Fast</option><option value="balanced">Balanced</option><option value="quality">Max quality</option></select></Field>
          <Field label="Video preset"><select value={s.video_default_preset} onChange={(e) => save({ video_default_preset: e.target.value })}>
            <option value="preview">Preview</option><option value="balanced">Balanced</option><option value="quality">Quality</option></select></Field>
          <Field label="Voice preset"><select value={s.voice_default_preset} onChange={(e) => save({ voice_default_preset: e.target.value })}>
            <option value="fast">Fast</option><option value="natural">Natural</option><option value="max">Maximum quality</option></select></Field>
          <Field label="Voice engine"><select value={s.voice_default_engine} onChange={(e) => save({ voice_default_engine: e.target.value })}>
            <option value="chatterbox">Chatterbox</option><option value="qwen">Qwen3-TTS</option><option value="omnivoice">OmniVoice</option></select></Field>
        </div>
        <label className="row"><input type="checkbox" checked={s.open_browser} onChange={(e) => save({ open_browser: e.target.checked })} />Open the browser when LocalAI starts</label>
      </div>
      <div className="card col">
        <h3>Ports (127.0.0.1 only)</h3>
        <div className="grid cols-3">
          {Object.entries(s.ports).map(([k, v]) => <div key={k} className="kv"><span className="k">{k}</span><span className="mono">{v}</span></div>)}
        </div>
        <div className="small faint">Edit config\settings.json to change ports. If a port is busy, LocalAI picks the next free one automatically. Nothing is exposed to your network.</div>
      </div>
      <div className="card row wrap">
        {["outputs/images", "outputs/image-edit", "outputs/image-upscale", "outputs/image-no-background", "outputs/videos",
          "outputs/video-edit", "outputs/video-upscale", "outputs/video-smooth", "outputs/clips", "outputs/avatar", "outputs/dubbing",
          "outputs/translated-video", "outputs/audio", "models", "voices", "logs", "config"].map((f) =>
          <button key={f} className="btn sm" onClick={() => openFolder(f)}><FolderOpen size={14} />{f}</button>)}
      </div>
    </div>
  );
}
