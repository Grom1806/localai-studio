import React, { useEffect, useState } from "react";
import { AudioLines, Dice5, Download, FolderOpen, Pencil, Plus, RefreshCw, Save, ShieldAlert, Trash2, Wand2 } from "lucide-react";
import { api, downloadUrl, fileUrl, Generation, mp3Url, openFolder, Voice } from "../api";
import { DropZone, Field, JobProgress, Modal, Seg, Slider, useJob, useToast } from "../components/common";
import { useNav } from "../App";

const LANGS: Record<string, string> = {
  ru: "Russian", en: "English", de: "German", fr: "French", es: "Spanish", it: "Italian", pt: "Portuguese",
  pl: "Polish", tr: "Turkish", nl: "Dutch", uk: "Ukrainian", zh: "Chinese", ja: "Japanese", ko: "Korean", ar: "Arabic", hi: "Hindi",
};
const ENGINES = [
  { v: "auto", label: "Auto (best measured engine)", desc: "Uses the engine that scored best in the benchmark for the chosen language" },
  { v: "fish", label: "Fish Audio S2 Pro", desc: "Expressive 80-language model with strong cloning (Fish Audio Research License: non-commercial)" },
  { v: "chatterbox", label: "Chatterbox", desc: "Natural expressive speech, 23 languages, voice cloning" },
  { v: "qwen", label: "Qwen3-TTS", desc: "10 languages, preset speakers with emotion instructions, cloning" },
  { v: "omnivoice", label: "OmniVoice", desc: "600+ languages, fast, cloning and voice design" },
] as const;
const ENGINE_LANGS: Record<string, string[]> = {
  auto: ["ru", "en", "de", "fr", "es", "it", "pt", "pl", "tr", "nl", "uk", "zh", "ja", "ko", "ar", "hi"],
  fish: ["ru", "en", "de", "fr", "es", "it", "pt", "pl", "tr", "nl", "uk", "zh", "ja", "ko", "ar"],
  chatterbox: ["ru", "en", "de", "fr", "es", "it", "pt", "pl", "tr", "nl", "zh", "ja", "ko", "ar", "hi"],
  qwen: ["ru", "en", "de", "fr", "es", "it", "pt", "zh", "ja", "ko"],
  omnivoice: Object.keys(LANGS),
};
const QWEN_SPEAKERS = ["Ryan", "Aiden", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee"];

export default function VoicePage({ mode = "tts" }: { mode?: "tts" | "clone" | "library" }) {
  const { go } = useNav();
  const toast = useToast();
  const { takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [text, setText] = useState("");
  const [engine, setEngine] = useState<string>("auto");
  const [lang, setLang] = useState("ru");
  const [preset, setPreset] = useState("natural");
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState<number | "">("");
  const [refUpload, setRefUpload] = useState<string | null>(null);
  const [refText, setRefText] = useState("");
  const [speaker, setSpeaker] = useState("Ryan");
  const [instruct, setInstruct] = useState("");
  const [exaggeration, setExaggeration] = useState(0.5);
  const [cfgWeight, setCfgWeight] = useState(0.5);
  const [temperature, setTemperature] = useState(0.8);
  const [numStep, setNumStep] = useState(32);
  const [speed, setSpeed] = useState(1.0);
  const [seed, setSeed] = useState<number | "">("");
  const [items, setItems] = useState<Generation[]>([]);
  const [sel, setSel] = useState<Generation | null>(null);
  const [editing, setEditing] = useState<Partial<Voice> | null>(null);

  const loadVoices = () => api.get<Voice[]>("/api/voices").then(setVoices);
  const load = () => api.get<Generation[]>("/api/history?type=audio&limit=40").then((r) => { setItems(r); return r; });
  useEffect(() => {
    loadVoices();
    load().then((r) => setSel((s) => s ?? r[0] ?? null));
    const h = takeHandoff(mode === "clone" ? "voice/clone" : "voice/tts");
    if (h?.text) setText(h.text);
    if (h?.params?.voice_id) setVoiceId(Number(h.params.voice_id));
  }, []);
  useEffect(() => { if (!ENGINE_LANGS[engine].includes(lang)) setLang("en"); }, [engine]);

  const hasRef = !!voiceId || !!refUpload;
  const generate = async (over: Record<string, any> = {}) => {
    const params: Record<string, any> = {};
    if (engine === "chatterbox") Object.assign(params, { exaggeration, cfg_weight: cfgWeight, temperature });
    if (engine === "omnivoice") params.num_step = numStep;
    const done = await run("/api/voice/generate", {
      text, engine, language: lang, preset, voice_id: voiceId || undefined,
      ref_audio: !voiceId && refUpload ? refUpload : undefined, ref_text: refText || undefined,
      speaker: engine === "qwen" && !hasRef ? speaker : undefined,
      instruct: instruct || undefined, speed, seed: seed === "" ? undefined : seed, params, ...over,
    });
    if (done?.status === "done") {
      const r = await load();
      setSel(r.find((x) => x.id === done.generation_id) ?? r[0] ?? null);
    }
  };

  const saveVoice = async () => {
    if (!editing?.name || !editing.ref_path) { toast("Name and reference audio are required", "err"); return; }
    try {
      if (editing.id) await api.patch(`/api/voices/${editing.id}`, { name: editing.name, language: editing.language, notes: editing.notes, transcript: editing.transcript });
      else {
        const v = await api.post<Voice>("/api/voices", editing);
        setVoiceId(v.id); setRefUpload(null);
      }
      setEditing(null); loadVoices(); toast("Voice saved", "ok");
    } catch (e: any) { toast(e.message, "err"); }
  };

  const delVoice = async (v: Voice) => {
    if (!confirm(`Delete saved voice "${v.name}"?`)) return;
    await api.del(`/api/voices/${v.id}`);
    if (voiceId === v.id) setVoiceId("");
    loadVoices();
  };

  const delGen = async (g: Generation) => {
    if (!confirm("Delete this audio from disk and history?")) return;
    await api.del(`/api/history/${g.id}`);
    const r = await load(); setSel(r[0] ?? null);
  };

  const running = job && ["queued", "running"].includes(job.status);
  const curVoice = voices.find((v) => v.id === voiceId);

  const lib = mode === "library";
  return (
    <div className="studio" style={lib ? { gridTemplateColumns: "minmax(0, 1fr)" } : undefined}>
      {!lib && <div className="controls">
        {mode === "clone" && <div className="notice info">Voice clone: upload a clean 5–15 s clip (or pick a saved voice), then type any text in any supported language.</div>}
        <div className="card col">
          <Field label="Text" hint={`${text.length} / 5000`}>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder="Привет! Это пример синтеза речи. / Hello! This is a speech sample."
              onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) generate(); }} />
          </Field>
          <div className="grid cols-2">
            <Field label="Voice engine">
              <select value={engine} onChange={(e) => setEngine(e.target.value)}>{ENGINES.map((e) => <option key={e.v} value={e.v}>{e.v === "auto" && lang === "ru" ? "Auto (Best Russian)" : e.label}</option>)}</select>
            </Field>
            <Field label="Language">
              <select value={lang} onChange={(e) => setLang(e.target.value)}>{ENGINE_LANGS[engine].map((l) => <option key={l} value={l}>{LANGS[l]}</option>)}</select>
            </Field>
          </div>
          <div className="small faint">{ENGINES.find((e) => e.v === engine)?.desc}</div>
          <Seg value={preset} onChange={setPreset} options={[{ v: "fast", label: "Fast" }, { v: "natural", label: "Natural" }, { v: "max", label: "Max quality" }]} />
          <Field label="Reference voice">
            <select value={voiceId} onChange={(e) => { setVoiceId(e.target.value ? Number(e.target.value) : ""); }}>
              <option value="">{engine === "qwen" ? "— preset speaker / upload below —" : "— default voice / upload below —"}</option>
              {voices.map((v) => <option key={v.id} value={v.id}>{v.name}{v.language ? ` (${v.language})` : ""}</option>)}
            </select>
          </Field>
          {!voiceId && <DropZone accept="audio/*" value={refUpload} onChange={setRefUpload} label="Upload reference audio (5–15 s, clean speech)" kind="audio" />}
          {curVoice && <audio src={curVoice.url} controls />}
          {engine === "qwen" && !hasRef && (
            <Field label="Speaker"><select value={speaker} onChange={(e) => setSpeaker(e.target.value)}>{QWEN_SPEAKERS.map((s) => <option key={s}>{s}</option>)}</select></Field>
          )}
          {engine === "chatterbox" && <Slider label="Emotion / expressiveness" value={exaggeration} min={0.25} max={1.5} step={0.05} onChange={setExaggeration}
            fmt={(v) => (v < 0.45 ? "calm" : v < 0.75 ? "natural" : "dramatic") + ` · ${v.toFixed(2)}`} />}
          {(engine === "qwen" && !hasRef) && <Field label="Emotion / style" hint="free text"><input type="text" value={instruct} onChange={(e) => setInstruct(e.target.value)} placeholder="e.g. warm and cheerful, speaking slowly" /></Field>}
          {(engine === "omnivoice" && !hasRef) && <Field label="Voice design" hint="optional"><input type="text" value={instruct} onChange={(e) => setInstruct(e.target.value)} placeholder="female, low pitch, british accent" /></Field>}
          <Slider label="Speed" value={speed} min={0.6} max={1.6} step={0.05} onChange={setSpeed} fmt={(v) => `${v.toFixed(2)}×`} />
          <div className="notice warn"><ShieldAlert size={15} style={{ flexShrink: 0 }} />Only clone voices you own or have permission to use.</div>
          <button className="btn primary big" disabled={!!running || !text.trim()} onClick={() => generate()}>
            <Wand2 size={17} />{running ? "Generating…" : "Generate speech"}
          </button>
        </div>
        <details className="adv">
          <summary>Advanced settings</summary>
          <div className="inner">
            <Field label="Seed" hint="empty = random"><div className="row"><input type="number" value={seed} onChange={(e) => setSeed(e.target.value === "" ? "" : Number(e.target.value))} />
              <button className="btn icon" type="button" onClick={() => setSeed("")}><Dice5 size={15} /></button></div></Field>
            {hasRef && (engine !== "chatterbox") && <Field label="Reference transcript" hint="improves cloning">
              <textarea rows={2} style={{ minHeight: 50 }} value={refText} onChange={(e) => setRefText(e.target.value)} placeholder={curVoice?.transcript || "What is said in the reference clip"} /></Field>}
            {engine === "chatterbox" && <>
              <Slider label="CFG weight (pacing)" value={cfgWeight} min={0} max={1} step={0.05} onChange={setCfgWeight} />
              <Slider label="Temperature" value={temperature} min={0.3} max={1.2} step={0.05} onChange={setTemperature} />
              <div className="small faint">Tip: for a fast-speaking reference, lower CFG weight to ~0.3. For cross-language cloning set CFG weight to 0.</div>
            </>}
            {engine === "omnivoice" && <Slider label="Diffusion steps" value={numStep} min={8} max={64} step={4} onChange={setNumStep} />}
          </div>
        </details>
      </div>}

      <div className="col" style={{ gap: 14 }}>
        {!lib && <div className="stage" style={{ minHeight: 220 }}>
          {running && job ? <JobProgress job={job} onCancel={cancel} /> : sel?.urls[0] ? (
            <div style={{ width: "min(640px, 92%)" }} className="col">
              <div className="row"><AudioLines size={20} style={{ color: "var(--accent)" }} /><b className="grow ellipsis">{sel.prompt}</b></div>
              <audio src={sel.urls[0]} key={sel.id} controls />
              <div className="small muted">{sel.model} · {sel.params?.language} · seed {sel.seed} · generated in {sel.seconds}s</div>
              <div className="row wrap">
                <a className="btn sm" href={downloadUrl(sel.outputs[0])}><Download size={14} />WAV</a>
                <a className="btn sm" href={mp3Url(sel.outputs[0])}><Download size={14} />MP3</a>
                <button className="btn sm" onClick={() => setEditing({ name: "", ref_path: refUpload || sel.outputs[0], language: lang })}><Save size={14} />Save voice</button>
                <button className="btn sm" onClick={() => generate({ text: sel.prompt, engine: sel.engine, language: sel.params?.language, seed: undefined })}><RefreshCw size={14} />Regenerate</button>
                <button className="btn sm ghost" onClick={() => openFolder(sel.outputs[0])}><FolderOpen size={14} /></button>
                <button className="btn sm ghost danger" onClick={() => delGen(sel)}><Trash2 size={14} /></button>
              </div>
            </div>
          ) : <div className="empty"><AudioLines size={42} /><div>Generated speech will appear here</div></div>}
        </div>}

        <div className="card">
          <div className="row" style={{ marginBottom: 10 }}>
            <h3 style={{ margin: 0 }} className="grow">Voice library</h3>
            <button className="btn sm" onClick={() => setEditing({ name: "", ref_path: refUpload || "", language: lang })}><Plus size={14} />New voice</button>
          </div>
          {voices.length === 0 ? <div className="muted small">No saved voices yet. Upload a clean 5–15 s clip of a voice you have permission to use.</div> : (
            <div className="col" style={{ gap: 8 }}>
              {voices.map((v) => (
                <div key={v.id} className="voice-card">
                  <div style={{ minWidth: 140 }}><b>{v.name}</b><div className="small muted">{v.language ? LANGS[v.language] ?? v.language : "any language"}</div></div>
                  <audio src={v.url} controls style={{ flex: 1 }} />
                  <div className="small faint ellipsis" style={{ maxWidth: 180 }} title={v.notes ?? ""}>{v.notes}</div>
                  <button className="btn sm" onClick={() => (lib ? go("voice/clone", { params: { voice_id: v.id } }) : setVoiceId(v.id))}>Use</button>
                  <button className="btn sm ghost icon" onClick={() => setEditing(v)}><Pencil size={14} /></button>
                  <button className="btn sm ghost icon danger" onClick={() => delVoice(v)}><Trash2 size={14} /></button>
                </div>
              ))}
            </div>
          )}
        </div>

        {!lib && <div className="card">
          <h3>Recent audio</h3>
          <div className="col" style={{ gap: 6 }}>
            {items.map((g) => (
              <div key={g.id} className="row" style={{ cursor: "pointer", padding: "6px 8px", borderRadius: 8, background: sel?.id === g.id ? "var(--accent-soft)" : undefined }} onClick={() => setSel(g)}>
                <AudioLines size={14} className="muted" /><span className="grow ellipsis">{g.prompt}</span><span className="small faint">{g.engine} · {g.created.slice(5, 16)}</span>
              </div>
            ))}
          </div>
        </div>}
      </div>

      {editing && (
        <Modal title={editing.id ? "Edit voice" : "Save voice"} onClose={() => setEditing(null)}>
          <div className="col" style={{ width: "min(520px, 85vw)" }}>
            <Field label="Name"><input type="text" value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} autoFocus /></Field>
            {!editing.id && <Field label="Reference audio"><DropZone accept="audio/*" value={editing.ref_path || null} onChange={(p) => setEditing({ ...editing, ref_path: p ?? "" })} label="Upload audio" kind="audio" /></Field>}
            <Field label="Language"><select value={editing.language ?? ""} onChange={(e) => setEditing({ ...editing, language: e.target.value || null })}>
              <option value="">any</option>{Object.entries(LANGS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Transcript" hint="auto-filled by Whisper if empty"><textarea rows={2} style={{ minHeight: 50 }} value={editing.transcript ?? ""} onChange={(e) => setEditing({ ...editing, transcript: e.target.value })} /></Field>
            <Field label="Notes"><input type="text" value={editing.notes ?? ""} onChange={(e) => setEditing({ ...editing, notes: e.target.value })} /></Field>
            <div className="notice warn"><ShieldAlert size={15} />Only clone voices you own or have permission to use.</div>
            <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={() => setEditing(null)}>Cancel</button><button className="btn primary" onClick={saveVoice}>Save</button></div>
          </div>
        </Modal>
      )}
    </div>
  );
}
