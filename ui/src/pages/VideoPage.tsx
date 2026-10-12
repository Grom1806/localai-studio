import React, { useEffect, useState } from "react";
import { Clapperboard, Cpu, Dice5, Download, FolderOpen, ImageDown, RefreshCw, Trash2, Wand2 } from "lucide-react";
import { api, downloadUrl, fmtMB, Generation, openFolder, Presets } from "../api";
import { DropZone, Field, JobProgress, Meter, NumberField, Seg, Slider, useJob, useStatus, useToast } from "../components/common";
import { useNav } from "../App";

const RES: { label: string; w: number; h: number }[] = [
  { label: "16:9", w: 1024, h: 576 }, { label: "9:16", w: 576, h: 1024 }, { label: "1:1", w: 768, h: 768 },
  { label: "720p", w: 1280, h: 704 }, { label: "720p vertical", w: 704, h: 1280 },
];

export default function VideoPage() {
  const toast = useToast();
  const { status } = useStatus();
  const { takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [presets, setPresets] = useState<Presets | null>(null);
  const [preset, setPreset] = useState("balanced");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [image, setImage] = useState<string | null>(null);
  const [refVideo, setRefVideo] = useState<string | null>(null);
  const [w, setW] = useState(1024);
  const [h, setH] = useState(576);
  const [duration, setDuration] = useState(5);
  const [fps, setFps] = useState(24);
  const [seed, setSeed] = useState<number | "">(-1);
  const [cfg, setCfg] = useState(1.0);
  const [audio, setAudio] = useState(true);
  const [twoStage, setTwoStage] = useState(true);
  const [imageStrength, setImageStrength] = useState(0.7);
  const [autoDur, setAutoDur] = useState(false);
  const [model, setModel] = useState<"ltx25" | "hunyuan15">("ltx25");
  const [hyPreset, setHyPreset] = useState("balanced");
  const hy = model === "hunyuan15";
  const [items, setItems] = useState<Generation[]>([]);
  const [sel, setSel] = useState<Generation | null>(null);

  const load = () => api.get<Generation[]>("/api/history?type=video&limit=40").then((r) => { setItems(r); return r; });

  useEffect(() => {
    api.get<Presets>("/api/presets").then((p) => { setPresets(p); apply("balanced", p); });
    load().then((r) => setSel((s) => s ?? r[0] ?? null));
    const hnd = takeHandoff("video/generate");
    if (hnd?.image) setImage(hnd.image);
    if (hnd?.prompt) setPrompt(hnd.prompt);
  }, []);

  const apply = (name: string, p = presets) => {
    setPreset(name);
    const v = p?.video[name];
    if (!v) return;
    setW(v.width); setH(v.height); setDuration(v.duration); setFps(v.fps); setTwoStage(v.two_stage); setAudio(v.audio);
  };

  const frames = Math.floor(Math.max(9, Math.round(duration * fps)) / 8) * 8 + 1;
  // A reference video continues from its last frame (LTX-2.5 image-to-video on that frame).
  const startInput = image || refVideo;

  const generate = async (over: Record<string, any> = {}) => {
    const done = await run("/api/video/generate", hy ? {
      prompt, negative: negative || undefined, model, hy_preset: hyPreset, width: w, height: h, duration, fps,
      seed: seed === "" ? -1 : seed, image: startInput || undefined, two_stage: twoStage, ...over,
    } : {
      prompt, negative: negative || undefined, preset, width: w, height: h, duration, fps,
      seed: seed === "" ? -1 : seed, cfg, image: startInput || undefined, image_strength: startInput && twoStage ? imageStrength : undefined,
      audio, two_stage: twoStage, auto_duration: autoDur || undefined, ...over,
    });
    if (done?.status === "done") {
      const r = await load();
      setSel(r.find((x) => x.id === done.generation_id) ?? r[0] ?? null);
    }
  };

  const useLastFrame = async (g: Generation) => {
    try {
      const r = await api.post<{ path: string }>("/api/video/last-frame", { path: g.outputs[0] });
      setImage(r.path); setRefVideo(null);
      toast("Last frame set as reference image", "ok");
    } catch (e: any) { toast(e.message, "err"); }
  };

  const del = async (g: Generation) => {
    if (!confirm("Delete this video from disk and history?")) return;
    await api.del(`/api/history/${g.id}`);
    const r = await load();
    setSel(r[0] ?? null);
  };

  const running = job && ["queued", "running"].includes(job.status);
  const heavy = w * h * frames > 1280 * 704 * 121;
  const gpu = status?.gpu;

  return (
    <div className="studio">
      <div className="controls">
        <div className="card col">
          <Field label="Model"><Seg value={model} onChange={(v) => setModel(v as any)} options={[
            { v: "ltx25", label: "LTX-2.5 (video + sound)" }, { v: "hunyuan15", label: "HunyuanVideo 1.5" }]} /></Field>
          {hy && <div className="small faint">HunyuanVideo 1.5: 480p generation plus the official 720p super-resolution pass. No sound. Strong motion and prompt following.</div>}
          <Field label="Prompt" hint="action, camera, sound">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6}
              placeholder="A slow dolly shot through a rainy neon street at night, reflections on wet asphalt, distant thunder and soft rain sounds."
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) generate(); }} />
          </Field>
          {hy ? <Seg value={hyPreset} onChange={setHyPreset} options={[
            { v: "fast", label: "Fast (4 steps)" }, { v: "balanced", label: "Quality" }]} />
            : <Seg value={preset} onChange={(v) => apply(v)} options={[
            { v: "preview", label: "Preview" }, { v: "balanced", label: "Balanced" }, { v: "quality", label: "Quality" }]} />}
          <div className="grid cols-2">
            <Field label="Reference image"><DropZone accept="image/*" value={image} onChange={(p) => { setImage(p); if (p) setRefVideo(null); }} label="Start frame" /></Field>
            <Field label="Reference video"><DropZone accept="video/*" value={refVideo} onChange={(p) => { setRefVideo(p); if (p) setImage(null); }} label="Continue a clip" kind="media" /></Field>
          </div>
          {refVideo && <div className="small faint">The new video continues from the last frame of this clip.</div>}
          {hy ? (
            <Field label="Format" hint={twoStage ? "720p" : "480p"}>
              <div className="row wrap" style={{ gap: 6 }}>
                {[{ label: "16:9", w: 848, h: 480 }, { label: "9:16", w: 480, h: 848 }].map((r) => <button key={r.label} type="button"
                  className={`btn sm ${(w >= h) === (r.w >= r.h) ? "primary" : ""}`} onClick={() => { setW(r.w); setH(r.h); }}>{r.label}</button>)}
              </div>
            </Field>
          ) : (
          <Field label="Resolution" hint={`${w} × ${h}`}>
            <div className="row wrap" style={{ gap: 6 }}>
              {RES.map((r) => <button key={r.label} type="button" className={`btn sm ${w === r.w && h === r.h ? "primary" : ""}`}
                onClick={() => { setW(r.w); setH(r.h); }}>{r.label}</button>)}
            </div>
          </Field>
          )}
          {autoDur && !hy ? (
            <Field label="Duration" hint="predicted from the prompt (2–10 s)"><div className="notice info">LTX-2.5 duration head picks a natural length for this shot.</div></Field>
          ) : <Slider label="Duration" value={duration} min={2} max={10} step={1} onChange={setDuration} fmt={(v) => `${v} s · ${frames} frames`} />}
          {!hy && <label className="row small"><input type="checkbox" checked={autoDur} onChange={(e) => setAutoDur(e.target.checked)} />Auto duration</label>}
          <div className="grid cols-2">
            <Field label="FPS"><select value={fps} onChange={(e) => setFps(Number(e.target.value))}>{[24, 25, 30].map((f) => <option key={f}>{f}</option>)}</select></Field>
            <Field label="Seed" hint="-1 = random"><div className="row"><input type="number" value={seed} onChange={(e) => setSeed(e.target.value === "" ? "" : Number(e.target.value))} />
              <button className="btn icon" type="button" title="Random" onClick={() => setSeed(-1)}><Dice5 size={15} /></button></div></Field>
          </div>
          {hy ? <label className="row small"><input type="checkbox" checked={twoStage} onChange={(e) => setTwoStage(e.target.checked)} />720p super-resolution (slower, sharper)</label>
            : <label className="row small"><input type="checkbox" checked={audio} onChange={(e) => setAudio(e.target.checked)} />Audio on: synchronized sound and speech (LTX-2.5)</label>}
          {heavy && <div className="notice warn">Large or long video: this can take several minutes on 16 GB.</div>}
          <button className="btn primary big" disabled={!!running || !prompt.trim()} onClick={() => generate()}>
            <Wand2 size={17} />{running ? "Generating…" : "Generate video"}
          </button>
        </div>
        <details className="adv">
          <summary>Advanced settings</summary>
          <div className="inner">
            <Field label="Negative prompt"><textarea rows={2} style={{ minHeight: 50 }} value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="(the default negative is used when empty)" /></Field>
            <div className="grid cols-2">
              <NumberField label="Width" value={w} step={64} min={256} max={1920} onChange={(v) => setW(Number(v) || 1024)} />
              <NumberField label="Height" value={h} step={64} min={256} max={1920} onChange={(v) => setH(Number(v) || 576)} />
            </div>
            <Slider label="Guidance (video CFG)" value={cfg} min={1} max={4} step={0.1} onChange={setCfg} fmt={(v) => (v <= 1 ? "1.0 (distilled default)" : `${v.toFixed(1)} · about 2× slower`)} />
            {startInput && twoStage && <Slider label="Start-frame adherence (stage 1)" value={imageStrength} min={0.3} max={1} step={0.05} onChange={setImageStrength} />}
            <label className="row small"><input type="checkbox" checked={twoStage} onChange={(e) => setTwoStage(e.target.checked)} />Two-stage: half-size pass, ×2 latent upscale, refine</label>
          </div>
        </details>
      </div>

      <div className="col" style={{ gap: 14 }}>
        <div className="stage">
          {running && job ? (
            <div className="col" style={{ alignItems: "center", width: "100%" }}>
              <JobProgress job={job} onCancel={cancel} />
              {gpu?.available && (
                <div className="mini-vram" style={{ marginBottom: 24 }}>
                  <Cpu size={14} /><span>GPU VRAM</span>
                  <Meter value={gpu.vram_used_mb} max={gpu.vram_total_mb} />
                  <span>{fmtMB(gpu.vram_used_mb)} / {fmtMB(gpu.vram_total_mb)} · {gpu.utilization ?? 0}% · {gpu.temperature_c ?? "–"}°C</span>
                </div>
              )}
            </div>
          ) : sel?.urls[0] ? (
            <video src={sel.urls[0]} key={sel.id} controls loop />
          ) : <div className="empty"><Clapperboard size={42} /><div>Your videos will appear here</div></div>}
        </div>
        {sel && !running && (
          <div className="card row wrap" style={{ padding: 12 }}>
            <div className="grow small muted ellipsis" title={sel.prompt}>
              {sel.params?.width}×{sel.params?.height} · {sel.params?.frames} frames @ {sel.params?.fps} fps · seed {sel.seed} · {sel.seconds}s
              {sel.peak_vram_mb ? ` · peak ${fmtMB(sel.peak_vram_mb)}` : ""}
            </div>
            <a className="btn sm" href={downloadUrl(sel.outputs[0])}><Download size={14} />Download</a>
            <button className="btn sm" onClick={() => generate({ prompt: sel.prompt, negative: sel.negative || undefined, width: sel.params.width, height: sel.params.height,
              duration: sel.params.duration, fps: sel.params.fps, image: sel.inputs?.image, cfg: sel.params.cfg, audio: sel.params.audio, two_stage: sel.params.two_stage, seed: -1 })}>
              <RefreshCw size={14} />Regenerate</button>
            <button className="btn sm" onClick={() => useLastFrame(sel)}><ImageDown size={14} />Use last frame</button>
            <button className="btn sm" onClick={() => openFolder(sel.outputs[0])}><FolderOpen size={14} />Open output folder</button>
            <button className="btn sm ghost danger" onClick={() => del(sel)} title="Delete"><Trash2 size={14} /></button>
          </div>
        )}
        <div className="gallery" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
          {items.map((g) => (
            <div key={g.id} className={`tile ${sel?.id === g.id ? "sel" : ""}`} style={{ aspectRatio: "16/9" }} onClick={() => setSel(g)}>
              {g.urls[0] && <video src={g.urls[0]} muted preload="metadata" />}
              <div className="ov ellipsis">{g.prompt}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
