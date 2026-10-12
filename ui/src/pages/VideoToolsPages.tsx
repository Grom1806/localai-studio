import React, { useEffect, useState } from "react";
import { Film, ZoomIn } from "lucide-react";
import { DropZone, Field, Seg, useJob } from "../components/common";
import { EmptyStage, Recent, ResultView, RunningView, UrlFetch } from "../components/tools";
import { useNav } from "../App";

export function VideoUpscalePage() {
  const { takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [video, setVideo] = useState<string | null>(null);
  const [mode, setMode] = useState("1080");
  const [quality, setQuality] = useState("balanced");
  const [result, setResult] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { const h = takeHandoff("video/upscale"); if (h?.video) setVideo(h.video); }, []);
  const go = async () => {
    const body: Record<string, any> = { video, quality };
    if (mode === "2x") body.factor = 2; else body.target_height = Number(mode);
    const done = await run("/api/tools/video_upscale", body);
    if (done?.status === "done" && done.result?.outputs?.[0]) { setResult(done.result.outputs[0]); setTick((t) => t + 1); }
  };
  const running = job && ["queued", "running"].includes(job.status);
  return (
    <div className="studio">
      <div className="controls"><div className="card col">
        <Field label="Input video"><DropZone accept="video/*" value={video} onChange={setVideo} label="Video to upscale" kind="media" /></Field>
        <UrlFetch allowRange onVideo={setVideo} />
        <Field label="Target"><Seg value={mode} onChange={setMode} options={[{ v: "2x", label: "2×" }, { v: "1080", label: "1080p" }, { v: "1440", label: "1440p" }, { v: "2160", label: "4K" }]} /></Field>
        <Field label="Quality"><Seg value={quality} onChange={setQuality} options={[{ v: "fast", label: "Fast (Real-ESRGAN)" }, { v: "balanced", label: "Faithful (SeedVR2)" }]} /></Field>
        <div className="small faint">FPS and audio are kept automatically. Long videos are processed in segments so RAM stays safe.</div>
        <button className="btn primary big" disabled={!!running || !video} onClick={go}><ZoomIn size={17} />Upscale video</button>
      </div></div>
      <div className="col" style={{ gap: 14 }}>
        {running && job ? <RunningView job={job} onCancel={cancel} /> : result ? <ResultView path={result} /> :
          video ? <ResultView path={video} meta="Input video" /> : <EmptyStage icon={ZoomIn} text="Add a video to upscale" />}
        <Recent type="video" match={/seedvr|esrgan/i} refresh={tick} onPick={(g) => setResult(g.outputs[0])} />
      </div>
    </div>
  );
}

export function InterpolatePage() {
  const { takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [video, setVideo] = useState<string | null>(null);
  const [mult, setMult] = useState("2");
  const [result, setResult] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { const h = takeHandoff("video/interpolate"); if (h?.video) setVideo(h.video); }, []);
  const go = async () => {
    const done = await run("/api/tools/interpolate", { video, multiplier: Number(mult) });
    if (done?.status === "done" && done.result?.outputs?.[0]) { setResult(done.result.outputs[0]); setTick((t) => t + 1); }
  };
  const running = job && ["queued", "running"].includes(job.status);
  return (
    <div className="studio">
      <div className="controls"><div className="card col">
        <Field label="Input video"><DropZone accept="video/*" value={video} onChange={setVideo} label="Video" kind="media" /></Field>
        <UrlFetch allowRange onVideo={setVideo} />
        <Field label="Frame rate"><Seg value={mult} onChange={setMult} options={[{ v: "2", label: "2× FPS" }, { v: "4", label: "4× FPS" }]} /></Field>
        <div className="small faint">RIFE 4.25 creates the in-between frames (e.g. 24 → 48, 30 → 60, 30 → 120 FPS). Original audio stays in sync.</div>
        <button className="btn primary big" disabled={!!running || !video} onClick={go}><Film size={17} />Interpolate</button>
      </div></div>
      <div className="col" style={{ gap: 14 }}>
        {running && job ? <RunningView job={job} onCancel={cancel} /> : result ? <ResultView path={result} /> :
          video ? <ResultView path={video} meta="Input video" /> : <EmptyStage icon={Film} text="Add a video to make it smoother" />}
        <Recent type="video" match={/rife/i} refresh={tick} onPick={(g) => setResult(g.outputs[0])} />
      </div>
    </div>
  );
}
