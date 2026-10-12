import React, { useEffect, useState } from "react";
import { Clapperboard, Download, FolderOpen, Image as ImageIcon, RefreshCw, Trash2, Wand2, CornerDownLeft, Dice5 } from "lucide-react";
import { api, downloadUrl, fileUrl, Generation, openFolder, Presets } from "../api";
import { DropZone, Field, JobProgress, NumberField, Seg, Slider, useJob, useToast } from "../components/common";
import { useNav } from "../App";

const RATIOS: { label: string; w: number; h: number }[] = [
  { label: "1:1", w: 1024, h: 1024 }, { label: "3:2", w: 1216, h: 832 }, { label: "2:3", w: 832, h: 1216 },
  { label: "16:9", w: 1344, h: 768 }, { label: "9:16", w: 768, h: 1344 }, { label: "21:9", w: 1536, h: 640 },
];

type Opts = { image_models: Record<string, { label: string; supports: string[] }>; loras: string[]; samplers: string[]; schedulers: string[] };

export default function ImagePage() {
  const toast = useToast();
  const { go, takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [presets, setPresets] = useState<Presets | null>(null);
  const [opts, setOpts] = useState<Opts | null>(null);
  const [preset, setPreset] = useState("balanced");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [model, setModel] = useState("z_image_turbo");
  const [w, setW] = useState(1024);
  const [h, setH] = useState(1024);
  const [steps, setSteps] = useState<number | "">(8);
  const [cfg, setCfg] = useState(1.0);
  const [seed, setSeed] = useState<number | "">(-1);
  const [sampler, setSampler] = useState("");
  const [scheduler, setScheduler] = useState("");
  const [ref, setRef] = useState<string | null>(null);
  const [mask, setMask] = useState<string | null>(null);
  const [denoise, setDenoise] = useState(0.6);
  const [lora, setLora] = useState("");
  const [loraStrength, setLoraStrength] = useState(0.8);
  const [upscale, setUpscale] = useState(false);
  const [detail, setDetail] = useState(0.3);
  const [items, setItems] = useState<Generation[]>([]);
  const [sel, setSel] = useState<Generation | null>(null);

  const load = () => api.get<Generation[]>("/api/history?type=image&limit=60").then((r) => { setItems(r); return r; });

  useEffect(() => {
    api.get<Presets>("/api/presets").then(setPresets);
    api.get<Opts>("/api/options").then(setOpts).catch(() => {});
    load().then((r) => setSel((s) => s ?? r[0] ?? null));
    const h = takeHandoff("image/generate");
    if (h?.image) setRef(h.image);
    if (h?.prompt) setPrompt(h.prompt);
  }, []);

  const applyPreset = (name: string, m = model) => {
    setPreset(name);
    const p = presets?.image[name];
    if (!p) return;
    const mp = presets?.image_models?.[m] ?? {};
    setSteps(mp.steps ?? p.steps); setCfg(mp.cfg ?? p.cfg ?? 1); setUpscale(!!p.upscale); setDetail(p.detail ?? 0.3);
  };
  useEffect(() => { if (presets) applyPreset(preset); }, [presets]);

  const supports = opts?.image_models[model]?.supports ?? ["t2i"];
  const isEdit = model === "flux2_klein_4b";

  const generate = async (overrides: Record<string, any> = {}) => {
    const body = {
      prompt, negative: negative || undefined, model, preset, width: w, height: h,
      steps: steps === "" ? undefined : steps, cfg, seed: seed === "" ? -1 : seed,
      sampler: sampler || undefined, scheduler: scheduler || undefined,
      image: ref || undefined, mask: mask || undefined, denoise: ref ? denoise : undefined,
      mode: ref ? (mask ? "inpaint" : isEdit ? "edit" : "i2i") : "t2i",
      lora: lora || undefined, lora_strength: lora ? loraStrength : undefined,
      upscale, detail, ...overrides,
    };
    const done = await run("/api/image/generate", body);
    if (done?.status === "done") {
      const r = await load();
      setSel(r.find((x) => x.id === done.generation_id) ?? r[0] ?? null);
    }
  };

  const regenerate = (g: Generation) => {
    const p = g.params || {};
    setPrompt(g.prompt || ""); setNegative(g.negative || "");
    if (p.model) setModel(p.model);
    if (p.width) setW(p.width); if (p.height) setH(p.height);
    generate({ prompt: g.prompt, negative: g.negative || undefined, model: p.model, width: p.width, height: p.height,
      steps: p.steps, cfg: p.cfg, seed: -1, image: g.inputs?.image, mask: g.inputs?.mask, mode: p.mode,
      denoise: p.denoise, lora: p.lora, lora_strength: p.lora_strength, upscale: p.upscale, detail: p.detail, preset: p.preset });
  };

  const del = async (g: Generation) => {
    if (!confirm("Delete this image from disk and history?")) return;
    await api.del(`/api/history/${g.id}`);
    const r = await load();
    setSel(r[0] ?? null);
    toast("Deleted", "ok");
  };

  const running = job && ["queued", "running"].includes(job.status);

  return (
    <div className="studio">
      <div className="controls">
        <div className="card col">
          <Field label="Prompt">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5}
              placeholder={isEdit && ref ? "Describe the change, e.g. 'make the jacket red, keep the face'" : "A cinematic photo of…"}
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) generate(); }} />
          </Field>
          <Seg value={preset} onChange={(v) => applyPreset(v)} options={[
            { v: "fast", label: "Fast" }, { v: "balanced", label: "Balanced" }, { v: "quality", label: "Max quality" }]} />
          <Field label="Model">
            <select value={model} onChange={(e) => { setModel(e.target.value); applyPreset(preset, e.target.value); }}>
              {Object.entries(opts?.image_models ?? { z_image_turbo: { label: "Z-Image Turbo" } }).map(([k, v]) =>
                <option key={k} value={k}>{(v as any).label}</option>)}
            </select>
          </Field>
          <Field label="Size" hint={`${w} × ${h}`}>
            <div className="row wrap" style={{ gap: 6 }}>
              {RATIOS.map((r) => <button key={r.label} type="button" className={`btn sm ${w === r.w && h === r.h ? "primary" : ""}`}
                onClick={() => { setW(r.w); setH(r.h); }}>{r.label}</button>)}
            </div>
          </Field>
          <Field label={isEdit ? "Reference image (character / object / edit source)" : "Reference image (image-to-image)"}>
            <DropZone accept="image/*" value={ref} onChange={setRef} label="Add a reference image" />
          </Field>
          {ref && !isEdit && <Slider label="Change strength" value={denoise} min={0.1} max={1} step={0.05} onChange={setDenoise}
            fmt={(v) => (v < 0.4 ? "subtle" : v < 0.7 ? "medium" : "strong") + ` · ${v.toFixed(2)}`} />}
          {ref && isEdit && <div className="notice info">FLUX.2 klein keeps the subject from the reference. Describe what to change or the new scene.</div>}
          <button className="btn primary big" disabled={!!running || (!prompt.trim() && !ref)} onClick={() => generate()}>
            <Wand2 size={17} />{running ? "Generating…" : "Generate"}
          </button>
        </div>

        <details className="adv">
          <summary>Advanced settings</summary>
          <div className="inner">
            <Field label="Negative prompt" hint={cfg <= 1 ? "used when guidance > 1" : ""}>
              <textarea value={negative} onChange={(e) => setNegative(e.target.value)} rows={2} placeholder="blurry, lowres, watermark" style={{ minHeight: 50 }} />
            </Field>
            <div className="grid cols-2">
              <NumberField label="Width" value={w} step={16} min={256} max={2048} onChange={(v) => setW(Number(v) || 1024)} />
              <NumberField label="Height" value={h} step={16} min={256} max={2048} onChange={(v) => setH(Number(v) || 1024)} />
              <NumberField label="Steps" value={steps} min={1} max={50} onChange={setSteps} />
              <Field label="Seed" hint="-1 = random">
                <div className="row"><input type="number" value={seed} onChange={(e) => setSeed(e.target.value === "" ? "" : Number(e.target.value))} />
                  <button className="btn icon" type="button" title="Random" onClick={() => setSeed(-1)}><Dice5 size={15} /></button></div>
              </Field>
            </div>
            <Slider label="Guidance (CFG)" value={cfg} min={1} max={6} step={0.1} onChange={setCfg}
              fmt={(v) => (v <= 1 ? "1.0 (turbo default)" : v.toFixed(1) + " · slower")} />
            {!isEdit && <div className="grid cols-2">
              <Field label="Sampler"><select value={sampler} onChange={(e) => setSampler(e.target.value)}>
                <option value="">default</option>{opts?.samplers.map((s) => <option key={s}>{s}</option>)}</select></Field>
              <Field label="Scheduler"><select value={scheduler} onChange={(e) => setScheduler(e.target.value)}>
                <option value="">default</option>{opts?.schedulers.map((s) => <option key={s}>{s}</option>)}</select></Field>
            </div>}
            {supports.includes("lora") && <Field label="LoRA" hint={opts?.loras.length ? "" : "put .safetensors in models\\image\\loras"}>
              <select value={lora} onChange={(e) => setLora(e.target.value)}>
                <option value="">none</option>{opts?.loras.map((l) => <option key={l}>{l}</option>)}</select></Field>}
            {lora && <Slider label="LoRA strength" value={loraStrength} min={0} max={1.5} step={0.05} onChange={setLoraStrength} />}
            {supports.includes("inpaint") && ref && (
              <Field label="Inpaint mask (white = repaint)"><DropZone accept="image/png,image/*" value={mask} onChange={setMask} label="Add mask PNG" /></Field>
            )}
            <label className="row"><input type="checkbox" checked={upscale} onChange={(e) => setUpscale(e.target.checked)} /> Upscale ×2 + detail / face enhancement</label>
            {upscale && !isEdit && <Slider label="Detail strength" value={detail} min={0} max={0.6} step={0.05} onChange={setDetail} />}
          </div>
        </details>
      </div>

      <div className="col" style={{ gap: 14 }}>
        <div className="stage">
          {running && job ? <JobProgress job={job} onCancel={cancel} /> : sel?.urls[0] ? (
            <img src={sel.urls[0]} key={sel.id} />
          ) : (
            <div className="empty"><ImageIcon size={42} /><div>Your images will appear here</div></div>
          )}
        </div>
        {sel && !running && (
          <div className="card row wrap" style={{ padding: 12 }}>
            <div className="grow small muted ellipsis" title={sel.prompt}>
              {sel.model} · {sel.params?.width}×{sel.params?.height} · seed {sel.seed} · {sel.seconds}s
            </div>
            <a className="btn sm" href={downloadUrl(sel.outputs[0])}><Download size={14} />Download</a>
            <button className="btn sm" onClick={() => regenerate(sel)}><RefreshCw size={14} />Regenerate</button>
            <button className="btn sm" onClick={() => { setRef(sel.outputs[0]); toast("Set as reference image", "ok"); }}><CornerDownLeft size={14} />Use as input</button>
            <button className="btn sm" onClick={() => go("video/generate", { image: sel.outputs[0], prompt: sel.prompt })}><Clapperboard size={14} />Send to Video</button>
            <button className="btn sm ghost" onClick={() => openFolder(sel.outputs[0])} title="Open folder"><FolderOpen size={14} /></button>
            <button className="btn sm ghost danger" onClick={() => del(sel)} title="Delete"><Trash2 size={14} /></button>
          </div>
        )}
        <div className="gallery">
          {items.map((g) => (
            <div key={g.id} className={`tile ${sel?.id === g.id ? "sel" : ""}`} onClick={() => setSel(g)}>
              {g.urls[0] && <img src={g.urls[0]} loading="lazy" />}
              <div className="ov ellipsis">{g.prompt}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
