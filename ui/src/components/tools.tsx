import React, { useEffect, useState } from "react";
import { AudioLines, Clapperboard, Download, FolderOpen, Scissors, Sparkles, Wand2, ZoomIn, Film, Image as ImageIcon } from "lucide-react";
import { api, downloadUrl, fileUrl, Generation, Job, openFolder } from "../api";
import { JobProgress, Meter, useStatus } from "./common";
import { useNav } from "../App";
import { fmtMB } from "../api";

const isVideo = (p?: string | null) => !!p && /\.(mp4|mov|webm|mkv|m4v)$/i.test(p);

/** Cross-feature hand-off buttons ("Send to …"). */
export function SendTo({ path, compact }: { path: string; compact?: boolean }) {
  const { go } = useNav();
  const sm = compact ? "btn sm ghost" : "btn sm";
  if (isVideo(path)) {
    return (
      <>
        <button className={sm} onClick={() => go("video/dub", { video: path })}><AudioLines size={14} />Send to Dub</button>
        <button className={sm} onClick={() => go("video/clips", { video: path })}><Scissors size={14} />Send to Clips</button>
        <button className={sm} onClick={() => go("video/upscale", { video: path })}><ZoomIn size={14} />Send to Upscale</button>
        <button className={sm} onClick={() => go("video/interpolate", { video: path })}><Film size={14} />Send to Interpolate</button>
        <button className={sm} onClick={() => go("video/edit", { video: path })}><Wand2 size={14} />Send to Video Editor</button>
      </>
    );
  }
  return (
    <>
      <button className={sm} onClick={() => go("image/edit", { image: path })}><Wand2 size={14} />Edit</button>
      <button className={sm} onClick={() => go("image/upscale", { image: path })}><ZoomIn size={14} />Upscale</button>
      <button className={sm} onClick={() => go("video/generate", { image: path })}><Clapperboard size={14} />Send to Video</button>
    </>
  );
}

/** Shows a finished output with actions. */
export function ResultView({ path, meta }: { path: string; meta?: React.ReactNode }) {
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="stage">
        {isVideo(path) ? <video src={fileUrl(path)} key={path} controls loop /> : <img src={fileUrl(path)} key={path} />}
      </div>
      <div className="card row wrap" style={{ padding: 12 }}>
        <div className="grow small muted ellipsis">{meta ?? path.split("/").pop()}</div>
        <a className="btn sm" href={downloadUrl(path)}><Download size={14} />Download</a>
        <button className="btn sm" onClick={() => openFolder(path)}><FolderOpen size={14} />Open folder</button>
        <SendTo path={path} />
      </div>
    </div>
  );
}

/** Progress panel with live GPU VRAM. */
export function RunningView({ job, onCancel }: { job: Job; onCancel?: () => void }) {
  const { status } = useStatus();
  const g = status?.gpu;
  return (
    <div className="stage">
      <div className="col" style={{ alignItems: "center", width: "100%" }}>
        <JobProgress job={job} onCancel={onCancel} />
        {g?.available && (
          <div className="mini-vram" style={{ marginBottom: 24 }}>
            <span>GPU VRAM</span><Meter value={g.vram_used_mb} max={g.vram_total_mb} />
            <span>{fmtMB(g.vram_used_mb)} / {fmtMB(g.vram_total_mb)} · {g.utilization ?? 0}%</span>
          </div>
        )}
      </div>
    </div>
  );
}

/** Small strip of recent results filtered by model name. */
export function Recent({ type, match, onPick, refresh }: { type: "image" | "video"; match: RegExp; onPick: (g: Generation) => void; refresh?: number }) {
  const [items, setItems] = useState<Generation[]>([]);
  useEffect(() => {
    api.get<Generation[]>(`/api/history?type=${type}&limit=80`).then((r) => setItems(r.filter((g) => match.test(g.model)).slice(0, 12)));
  }, [refresh]);
  if (!items.length) return null;
  return (
    <div className="card">
      <h3>Recent</h3>
      <div className="gallery" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
        {items.map((g) => (
          <div key={g.id} className="tile" style={{ aspectRatio: type === "video" ? "16/9" : "1" }} onClick={() => onPick(g)} title={g.prompt}>
            {type === "video" ? <video src={g.urls[0]} muted preload="metadata" /> : <img src={g.urls[0]} loading="lazy" />}
            <div className="ov ellipsis">{g.prompt}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Paste a YouTube (or other site) link; the video is downloaded locally and handed to onVideo. */
export function UrlFetch({ onVideo, allowRange }: { onVideo: (path: string) => void; allowRange?: boolean }) {
  const [url, setUrl] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const busy = job && ["queued", "running"].includes(job.status);
  const go = async () => {
    setErr(null);
    try {
      const j = await api.post<Job>("/api/tools/fetch_url", { url: url.trim(), start: start || undefined, end: end || undefined });
      setJob(j);
      const { waitJob } = await import("../api");
      const done = await waitJob(j.id, setJob);
      if (done.status === "done" && (done.result as any)?.path) { onVideo((done.result as any).path); setUrl(""); }
      else setErr(done.error ?? "Download failed");
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 6 }}>
        <input type="text" placeholder="…or paste a YouTube / video link" value={url} onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && url.trim() && !busy) go(); }} />
        <button className="btn sm" disabled={!url.trim() || !!busy} onClick={go}>{busy ? "…" : "Fetch"}</button>
      </div>
      {allowRange && url.trim() && (
        <div className="row small" style={{ gap: 6 }}>
          <span className="muted">Only part:</span>
          <input type="text" style={{ width: 90 }} placeholder="from 00:10:00" value={start} onChange={(e) => setStart(e.target.value)} />
          <input type="text" style={{ width: 90 }} placeholder="to 00:25:00" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
      )}
      {busy && job && <div className="small muted">{job.message} <div className="meter" style={{ marginTop: 4 }}><div style={{ width: `${job.progress * 100}%` }} /></div></div>}
      {err && <div className="small" style={{ color: "var(--bad)" }}>{err}</div>}
      {url.trim() && !busy && <div className="small faint">Only download videos you have the right to use.</div>}
    </div>
  );
}

export function EmptyStage({ icon: Icon = Sparkles, text }: { icon?: React.ElementType; text: string }) {
  return <div className="stage"><div className="empty"><Icon size={42} /><div>{text}</div></div></div>;
}

export { isVideo, ImageIcon };
