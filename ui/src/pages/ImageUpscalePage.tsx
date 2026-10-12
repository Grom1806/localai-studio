import React, { useEffect, useState } from "react";
import { Eraser, ZoomIn } from "lucide-react";
import { DropZone, Field, Seg, useJob } from "../components/common";
import { EmptyStage, Recent, ResultView, RunningView } from "../components/tools";
import { useNav } from "../App";

export default function ImageUpscalePage() {
  const { takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [image, setImage] = useState<string | null>(null);
  const [factor, setFactor] = useState("2");
  const [quality, setQuality] = useState("balanced");
  const [color, setColor] = useState("lab");
  const [result, setResult] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => { const h = takeHandoff("image/upscale"); if (h?.image) setImage(h.image); }, []);

  const go = async (kind: "image_upscale" | "remove_bg") => {
    const done = await run(`/api/tools/${kind}`, { image, factor: Number(factor), quality, color });
    if (done?.status === "done" && done.result?.outputs?.[0]) { setResult(done.result.outputs[0]); setTick((t) => t + 1); }
  };
  const running = job && ["queued", "running"].includes(job.status);

  return (
    <div className="studio">
      <div className="controls">
        <div className="card col">
          <Field label="Image"><DropZone accept="image/*" value={image} onChange={setImage} label="Image to upscale" /></Field>
          <Field label="Scale"><Seg value={factor} onChange={setFactor} options={[{ v: "2", label: "2×" }, { v: "4", label: "4×" }]} /></Field>
          <Field label="Mode">
            <Seg value={quality} onChange={setQuality} options={[{ v: "fast", label: "Fast (Real-ESRGAN)" }, { v: "balanced", label: "Faithful (SeedVR2)" }]} />
          </Field>
          <div className="small faint">{quality === "fast" ? "Very fast, classic sharpening. Good for graphics and quick previews."
            : "SeedVR2 7B restores real detail and faces while staying faithful to the original (no invented details)."}</div>
          <button className="btn primary big" disabled={!!running || !image} onClick={() => go("image_upscale")}><ZoomIn size={17} />Upscale</button>
          <button className="btn" disabled={!!running || !image} onClick={() => go("remove_bg")}><Eraser size={15} />Remove background (BiRefNet → transparent PNG)</button>
        </div>
        <details className="adv"><summary>Advanced settings</summary><div className="inner">
          <Field label="Colour matching (SeedVR2)"><select value={color} onChange={(e) => setColor(e.target.value)}>
            <option value="lab">LAB (most faithful)</option><option value="wavelet">Wavelet</option><option value="adain">AdaIN</option><option value="none">None</option></select></Field>
        </div></details>
      </div>
      <div className="col" style={{ gap: 14 }}>
        {running && job ? <RunningView job={job} onCancel={cancel} /> : result ? <ResultView path={result} /> :
          image ? <ResultView path={image} meta="Input image" /> : <EmptyStage icon={ZoomIn} text="Add an image to upscale" />}
        <Recent type="image" match={/seedvr|esrgan|birefnet/i} refresh={tick} onPick={(g) => setResult(g.outputs[0])} />
      </div>
    </div>
  );
}
