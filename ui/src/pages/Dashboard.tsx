import React, { useEffect, useState } from "react";
import { AudioLines, Clapperboard, Cpu, HardDrive, Image as ImageIcon, MemoryStick, Thermometer, Zap } from "lucide-react";
import { api, fmtMB, Generation, fileUrl } from "../api";
import { Meter, Pill, useStatus } from "../components/common";
import { CleanupCard } from "../components/cleanup";
import { ModelActivity } from "../components/activity";
import { useNav } from "../App";

export default function Dashboard() {
  const { status } = useStatus();
  const { go } = useNav();
  const [recent, setRecent] = useState<Generation[]>([]);
  useEffect(() => { api.get<Generation[]>("/api/history?limit=8").then(setRecent).catch(() => {}); }, [status?.current_job?.id]);

  if (!status) return <div className="muted">Connecting to LocalAI…</div>;
  const g = status.gpu;
  const svc = status.services;
  const cards: { key: string; label: string; sub: string }[] = [
    { key: "comfyui", label: "ComfyUI", sub: "image + video engine" },
    { key: "comfyui:ltx", label: "LTX-2.5", sub: "video + audio model" },
    { key: "chatterbox", label: "Chatterbox", sub: "multilingual TTS" },
    { key: "qwen", label: "Qwen3-TTS", sub: "TTS + voice cloning" },
    { key: "omnivoice", label: "OmniVoice", sub: "600+ languages" },
  ];
  const stateOf = (key: string) => {
    if (key === "comfyui:ltx") {
      const c = svc.comfyui;
      if (!status.video_installed) return "missing";
      if (c?.status !== "online") return c?.status ?? "offline";
      return status.loaded.comfyui?.includes("LTX") ? "online" : "ready";
    }
    const s = svc[key];
    if (!s) return "offline";
    if (s.status === "online" && s.kind === "tts") return s.health?.status === "loading" ? "loading" : s.health?.loaded ? "online" : "ready";
    return s.status;
  };
  const labelOf = (st: string) => (st === "online" ? "LOADED" : st === "missing" ? "NOT INSTALLED" : undefined);

  return (
    <div className="col" style={{ gap: 18 }}>
      <div className="grid cols-4">
        <div className="card stat">
          <div className="row muted small"><Zap size={14} /> GPU</div>
          <div className="v" style={{ fontSize: 20, margin: "6px 0" }}>{g.available ? g.name.replace("NVIDIA GeForce ", "") : "not detected"}</div>
          <div className="small muted">Driver {g.driver} · {g.utilization ?? 0}<span>% load</span>{g.power_w != null ? ` · ${g.power_w} W` : ""}</div>
        </div>
        <div className="card stat">
          <div className="row muted small"><Cpu size={14} /> VRAM</div>
          <div className="v">{fmtMB(g.vram_used_mb)} <span className="muted" style={{ fontSize: 15 }}>/ {fmtMB(g.vram_total_mb)}</span></div>
          <Meter value={g.vram_used_mb} max={g.vram_total_mb} />
        </div>
        <div className="card stat">
          <div className="row muted small"><MemoryStick size={14} /> System RAM</div>
          <div className="v">{fmtMB(status.ram_used_mb)} <span className="muted" style={{ fontSize: 15 }}>/ {fmtMB(status.ram_total_mb)}</span></div>
          <Meter value={status.ram_used_mb} max={status.ram_total_mb} />
        </div>
        <div className="card stat">
          <div className="row muted small"><Thermometer size={14} /> Temperature · <HardDrive size={14} /> Disk {status.drive}</div>
          <div className="v">{g.temperature_c ?? "–"}°C</div>
          <div className="small muted">{status.disk_free_gb} GB free of {status.disk_total_gb} GB</div>
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Backends</h3>
          <div className="col" style={{ gap: 8 }}>
            {cards.map((c) => {
              const st = stateOf(c.key);
              return (
                <div key={c.key} className="row" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                  <div className="grow"><b>{c.label}</b> <span className="small muted">· {c.sub}</span></div>
                  <Pill state={st} label={labelOf(st)} />
                </div>
              );
            })}
          </div>
          <div className="small faint" style={{ marginTop: 10 }}>READY = backend running, model loads on first use. VRAM mode: <b>{status.vram_mode.replace("_", " ").toUpperCase()}</b></div>
        </div>

        <div className="card">
          <h3>Now</h3>
          {status.current_job ? (
            <div className="col">
              <div className="row"><Pill state="loading" label={status.current_job.kind.toUpperCase()} /><span className="grow ellipsis">{status.current_job.prompt}</span></div>
              <Meter value={status.current_job.progress} max={1} />
              <div className="small muted">{status.current_job.message} · {status.current_job.elapsed.toFixed(0)} s</div>
            </div>
          ) : <div className="muted"><span>Idle.</span> {status.queue_length ? `${status.queue_length} in queue.` : "Queue is empty."}</div>}
          <div className="kv" style={{ marginTop: 14 }}>
            <span className="k">Loaded models</span>
            <span>{Object.values(status.loaded).join(", ") || "none"}</span>
            <span className="k">Queue length</span><span>{status.queue_length}</span>
          </div>
          <div className="row wrap" style={{ marginTop: 16 }}>
            <button className="btn primary" onClick={() => go("image/generate")}><ImageIcon size={15} />New image</button>
            <button className="btn" onClick={() => go("video/generate")}><Clapperboard size={15} />New video</button>
            <button className="btn" onClick={() => go("voice/tts")}><AudioLines size={15} />New voice</button>
          </div>
        </div>
      </div>

      <ModelActivity />
      <CleanupCard />

      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}><h3 style={{ margin: 0 }} className="grow">Recent</h3><button className="btn ghost sm" onClick={() => go("history")}>Open history</button></div>
        {recent.length === 0 ? <div className="muted">Nothing generated yet.</div> : (
          <div className="gallery" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
            {recent.map((r) => (
              <div key={r.id} className="tile" onClick={() => go("history")} title={r.prompt}>
                {r.type === "image" && r.urls[0] && <img src={r.urls[0]} loading="lazy" />}
                {r.type === "video" && r.urls[0] && <video src={r.urls[0]} muted preload="metadata" />}
                {r.type === "audio" && <div style={{ display: "grid", placeItems: "center", height: "100%" }}><AudioLines size={30} className="muted" /></div>}
                <div className="ov ellipsis">{r.prompt}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
