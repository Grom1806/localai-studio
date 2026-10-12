import React from "react";
import { fmtMB, ServiceInfo, Status } from "../api";
import { Meter, useStatus } from "./common";

// Which service runs each job kind (voice jobs are matched by the worker's own "busy" state).
const COMFY_JOBS = new Set(["image", "image_edit", "image_upscale", "remove_bg", "video", "video_edit", "video_upscale",
  "interpolate", "avatar"]);
const MEDIA_JOBS = new Set(["dub_analyze", "clips_analyze", "clips_export", "clips_preview", "clips_previews", "fetch_url"]);
const LLM_EST_MB = 8500;            // llama-server does not report VRAM; Gemma-4 12B Q4 + 16k context ≈ 8.5 GB
const DESKTOP_MB = 1200;            // Windows desktop / browser baseline, not LocalAI

type Row = { key: string; label: string; state: "busy" | "loaded" | "idle" | "off" | "error"; vram: number; est?: boolean;
  detail?: string; progress?: number; message?: string };

function rows(s: Status): Row[] {
  const job = s.current_job;
  const out: Row[] = [];
  let known = 0;
  const svcs = Object.values(s.services) as ServiceInfo[];
  for (const svc of svcs) {
    if (svc.name === "comfyui") continue;
    const on = svc.status === "online";
    let vram = on ? svc.health?.vram_mb ?? 0 : 0;
    let est = false;
    if (svc.name === "llm" && on) { vram = LLM_EST_MB; est = true; }
    const busy = on && (svc.health?.status === "busy" || svc.health?.status === "loading" ||
      (!!job && job.status === "running" && ((svc.name === "media" && MEDIA_JOBS.has(job.kind)) ||
        (svc.kind === "tts" && job.kind === "voice" && svc.health?.status !== "idle"))));
    const loaded = on && (svc.health?.loaded || (svc.name === "llm"));
    known += vram;
    out.push({
      key: svc.name, label: svc.label, vram, est,
      state: svc.status === "error" ? "error" : !on ? "off" : busy ? "busy" : loaded ? "loaded" : "idle",
      detail: svc.health?.status === "loading" ? "loading the model…" : !svc.installed ? "not installed" : undefined,
      ...(busy && job && job.status === "running" && svc.kind !== "tts" ? { progress: job.progress, message: job.message } : {}),
    });
  }
  // ComfyUI does not report its own VRAM: whatever the GPU uses beyond the other workers is attributed to it.
  const c = s.services.comfyui;
  const comfyOn = c?.status === "online";
  const comfyVram = comfyOn ? Math.max(0, s.gpu.vram_used_mb - known - DESKTOP_MB) : 0;
  const comfyBusy = !!job && job.status === "running" && COMFY_JOBS.has(job.kind);
  out.unshift({
    key: "comfyui", label: "ComfyUI · " + (s.loaded.comfyui || "images and video"), vram: comfyVram, est: true,
    state: !comfyOn ? (c?.status === "error" ? "error" : "off") : comfyBusy ? "busy" : comfyVram > 1500 ? "loaded" : "idle",
    ...(comfyBusy && job ? { progress: job.progress, message: job.message } : {}),
  });
  return out;
}

const STATE_LABEL: Record<Row["state"], string> = { busy: "working", loaded: "loaded", idle: "idle", off: "off", error: "error" };
const STATE_PILL: Record<Row["state"], string> = { busy: "loading", loaded: "online", idle: "", off: "offline", error: "error" };

/** Live list of every model service: state, VRAM it holds, and progress of what it is doing. */
export function ModelActivity() {
  const { status } = useStatus();
  if (!status?.gpu?.available) return null;
  const list = rows(status).sort((a, b) => b.vram - a.vram);
  const total = status.gpu.vram_total_mb;
  return (
    <div className="card col">
      <div className="row"><h3 className="grow" style={{ margin: 0 }}>Models now</h3>
        <span className="small muted">VRAM {fmtMB(status.gpu.vram_used_mb)} / {fmtMB(total)} · GPU {status.gpu.utilization ?? 0}%</span></div>
      <Meter value={status.gpu.vram_used_mb} max={total} />
      <div className="col" style={{ gap: 10, marginTop: 4 }}>
        {list.map((r) => (
          <div key={r.key} className="col" style={{ gap: 4, opacity: r.state === "off" ? 0.55 : 1 }}>
            <div className="row small">
              <span className={`pill ${STATE_PILL[r.state]}`} style={{ minWidth: 86, justifyContent: "center" }}>
                {r.state === "busy" && <span className="dot" />}{STATE_LABEL[r.state]}</span>
              <span className="grow ellipsis"><b>{r.label}</b>{r.detail && <span className="muted"> · {r.detail}</span>}</span>
              <span className="muted" style={{ minWidth: 90, textAlign: "right" }}>{r.vram > 0 ? (r.est ? "~" : "") + fmtMB(r.vram) : "—"}</span>
            </div>
            <div style={{ height: 6, background: "var(--bg-3, rgba(255,255,255,.06))", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${Math.min(100, (r.vram / total) * 100)}%`, height: "100%", borderRadius: 4,
                background: r.state === "busy" ? "var(--accent, #8b7cf6)" : "rgba(139,124,246,.45)", transition: "width .4s" }} />
            </div>
            {r.progress != null && (
              <div className="row small" style={{ gap: 8 }}>
                <div className="grow"><Meter value={r.progress} max={1} /></div>
                <span className="muted ellipsis" style={{ maxWidth: "55%" }}>{Math.round(r.progress * 100)}% · {r.message}</span>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="small faint">The bar shows how much video memory each model holds. ComfyUI and the local LLM are estimated.</div>
    </div>
  );
}
