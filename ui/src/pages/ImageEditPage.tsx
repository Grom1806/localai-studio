import React, { useEffect, useState } from "react";
import { Dice5, Plus, Wand2, X } from "lucide-react";
import { DropZone, Field, Seg, useJob } from "../components/common";
import { EmptyStage, Recent, ResultView, RunningView } from "../components/tools";
import { useNav } from "../App";

const EXAMPLES = ["change the jacket to black", "remove the person in the background", "make this nighttime",
  "change the room but keep the same person", "close the person's mouth", "change only the clothes, keep the face identical"];

export default function ImageEditPage() {
  const { takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [image, setImage] = useState<string | null>(null);
  const [mask, setMask] = useState<string | null>(null);
  const [refs, setRefs] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [seed, setSeed] = useState<number | "">(-1);
  const [preset, setPreset] = useState("balanced");
  const [result, setResult] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => { const h = takeHandoff("image/edit"); if (h?.image) setImage(h.image); }, []);

  const generate = async () => {
    const done = await run("/api/tools/image_edit", { image: image || undefined, mask: mask || undefined, refs, prompt,
      negative: negative || undefined, seed: seed === "" ? -1 : seed, preset });
    if (done?.status === "done" && done.result?.outputs?.[0]) { setResult(done.result.outputs[0]); setTick((t) => t + 1); }
  };
  const running = job && ["queued", "running"].includes(job.status);

  return (
    <div className="studio">
      <div className="controls">
        <div className="card col">
          <Field label="Input image"><DropZone accept="image/*" value={image} onChange={setImage} label="Image to edit" /></Field>
          <Field label="Instruction">
            <textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="change the jacket to black, keep the face identical"
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) generate(); }} />
          </Field>
          <div className="row wrap" style={{ gap: 5 }}>
            {EXAMPLES.map((x) => <button key={x} className="btn sm ghost" type="button" onClick={() => setPrompt(x)}>{x}</button>)}
          </div>
          <Seg value={preset} onChange={setPreset} options={[{ v: "fast", label: "Fast" }, { v: "balanced", label: "Balanced" }, { v: "quality", label: "Quality" }]} />
          <button className="btn primary big" disabled={!!running || !prompt.trim() || !image} onClick={generate}>
            <Wand2 size={17} />{running ? "Editing…" : "Generate edit"}
          </button>
          <div className="small faint">Qwen-Image-2.1 understands plain instructions. Say what must stay the same ("keep the face identical") for the strongest identity preservation.</div>
        </div>
        <details className="adv">
          <summary>Advanced settings</summary>
          <div className="inner">
            <Field label="Mask (optional)" hint="white = may change"><DropZone accept="image/png,image/*" value={mask} onChange={setMask} label="Only this area is edited" /></Field>
            <Field label="Reference images (optional)" hint="character, clothing, object">
              <div className="col">
                {refs.map((r, i) => (
                  <div key={i} className="row"><span className="small ellipsis grow">{r.split("/").pop()}</span>
                    <button className="btn sm ghost icon" onClick={() => setRefs(refs.filter((_, j) => j !== i))}><X size={13} /></button></div>
                ))}
                {refs.length < 9 && <DropZone accept="image/*" value={null} onChange={(p) => p && setRefs([...refs, p])} label="Add reference" />}
              </div>
            </Field>
            <Field label="Negative prompt"><input type="text" value={negative} onChange={(e) => setNegative(e.target.value)} placeholder="blurry, distorted face" /></Field>
            <Field label="Seed" hint="-1 = random"><div className="row"><input type="number" value={seed} onChange={(e) => setSeed(e.target.value === "" ? "" : Number(e.target.value))} />
              <button className="btn icon" type="button" onClick={() => setSeed(-1)}><Dice5 size={15} /></button></div></Field>
          </div>
        </details>
      </div>
      <div className="col" style={{ gap: 14 }}>
        {running && job ? <RunningView job={job} onCancel={cancel} /> : result ? <ResultView path={result} meta={prompt} /> :
          image ? <ResultView path={image} meta="Input image" /> : <EmptyStage icon={Plus} text="Add an image and describe the change" />}
        <Recent type="image" match={/qwen/i} refresh={tick} onPick={(g) => setResult(g.outputs[0])} />
      </div>
    </div>
  );
}
