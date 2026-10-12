import React, { useEffect, useRef, useState } from "react";
import { Eye, FileVideo, Languages, Play, RefreshCw, ShieldAlert, Volume2 } from "lucide-react";
import { api, fileUrl, Job, Voice, waitJob } from "../api";
import { DropZone, Field, Seg, useJob, useToast } from "../components/common";
import { EmptyStage, ResultView, RunningView, UrlFetch } from "../components/tools";
import { useNav } from "../App";

const LANGS: Record<string, string> = { ru: "Russian", en: "English", de: "German", fr: "French", es: "Spanish", it: "Italian",
  pt: "Portuguese", pl: "Polish", tr: "Turkish", uk: "Ukrainian", zh: "Chinese", ja: "Japanese", ko: "Korean" };
type Segment = { i: number; start: number; end: number; speaker: string; text: string; translation: string };
type Speaker = { id: string; label: string; ref: string | null; ref_url: string | null; ref_text: string; voice: string;
  voice_id: number | null; custom_ref: string | null; enabled?: boolean };
type Project = { id: string; video_rel: string; source_language: string; target_language: string; segments: Segment[];
  speakers: Speaker[]; engine: string; quality: string; subtitles: string; note: string | null; background: string | null };
const t2 = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function DubPage({ mode = "dub" }: { mode?: "dub" | "translate" }) {
  const toast = useToast();
  const { takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const translate = mode === "translate";
  const [video, setVideo] = useState<string | null>(null);
  const [src, setSrc] = useState("auto");
  const [tgt, setTgt] = useState("ru");
  const [engine, setEngine] = useState("auto");
  const [tmode, setTmode] = useState("dubbing");
  const [speakers, setSpeakers] = useState("auto");
  const [bg, setBg] = useState(true);
  const [subs, setSubs] = useState("off");
  const [subStyle, setSubStyle] = useState("minimal");
  const [quality, setQuality] = useState("balanced");
  const [lipsync, setLipsync] = useState(translate ? "balanced" : "off");
  const [proj, setProj] = useState<Project | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [lipOk, setLipOk] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [lines, setLines] = useState<Record<number, { url: string; fits: boolean; busy?: boolean }>>({});
  const audio = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const h = takeHandoff(translate ? "video/translate" : "video/dub"); if (h?.video) setVideo(h.video);
    api.get<Voice[]>("/api/voices").then(setVoices);
    api.get<{ infinitetalk: boolean }>("/api/avatars").then((r) => setLipOk(r.infinitetalk)).catch(() => {});
  }, []);
  useEffect(() => { setTmode(tgt === "ru" ? "dubbing" : "natural"); }, [tgt]);

  const analyze = async () => {
    setProj(null); setResult(null); setLines({});
    const done = await run("/api/tools/dub_analyze", { video, source_language: src, target_language: tgt, engine,
      speakers, preserve_background: bg, subtitles: subs, quality, translation_mode: tmode });
    if (done?.status === "done") {
      const p = await api.get<Project>(`/api/dub/${(done.result as any).project}`);
      setProj(p);
      if (p.note) toast(p.note, "info");
    }
  };
  const render = async (preview: boolean) => {
    if (!proj) return;
    const done = await run("/api/tools/dub_render", { project: proj.id, preview, preview_seconds: 10, segments: proj.segments,
      speakers: proj.speakers, engine: proj.engine, quality, subtitles: subs, subtitle_style: subStyle, lipsync });
    if (done?.status === "done" && done.result?.outputs?.[0]) {
      setResult(done.result.outputs[0]);
      const r = done.result as any;
      if (r.extension > 0.2) toast(`Timeline extended by ${r.extension}s so the voice keeps a natural pace.`, "info");
    }
  };
  const previewLine = async (s: Segment) => {
    if (!proj) return;
    setLines((l) => ({ ...l, [s.i]: { ...(l[s.i] ?? { url: "", fits: true }), busy: true } }));
    try {
      const j = await api.post<Job>("/api/tools/dub_line", { project: proj.id, segment: s.i, text: s.translation, engine: proj.engine,
        speaker_override: proj.speakers.find((x) => x.id === s.speaker) });
      const done = await waitJob(j.id, () => {});
      if (done.status === "done" && done.result?.outputs?.[0]) {
        const url = fileUrl(done.result.outputs[0]) + `?t=${Date.now()}`;
        setLines((l) => ({ ...l, [s.i]: { url, fits: (done.result as any).fits } }));
        if (audio.current) { audio.current.src = url; audio.current.play(); }
      } else toast(done.error ?? "Voice preview failed", "err");
    } finally { setLines((l) => ({ ...l, [s.i]: { ...(l[s.i] ?? { url: "", fits: true }), busy: false } })); }
  };
  const setSeg = (i: number, v: string) => setProj((p) => p && { ...p, segments: p.segments.map((s) => (s.i === i ? { ...s, translation: v } : s)) });
  const setSpk = (id: string, patch: Partial<Speaker>) => setProj((p) => p && { ...p, speakers: p.speakers.map((s) => (s.id === id ? { ...s, ...patch } : s)) });
  const running = job && ["queued", "running"].includes(job.status);
  const stages = ["Transcribing", "Translating", "Generating voice", "Synchronizing", "Mixing audio", "Lip sync", "Rendering"];

  return (
    <div className="studio">
      <audio ref={audio} hidden />
      <div className="controls">
        <div className="card col">
          <Field label="Input video"><DropZone accept="video/*" value={video} onChange={(v) => { setVideo(v); setProj(null); }} label={translate ? "Video to translate" : "Video to dub"} kind="media" /></Field>
          <UrlFetch allowRange onVideo={(v) => { setVideo(v); setProj(null); }} />
          <div className="grid cols-2">
            <Field label="Source language"><select value={src} onChange={(e) => setSrc(e.target.value)}>
              <option value="auto">Auto-detect</option><option value="ru">Russian</option><option value="en">English</option><option value="de">German</option></select></Field>
            <Field label="Target language"><select value={tgt} onChange={(e) => setTgt(e.target.value)}>
              {Object.entries(LANGS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Translation"><select value={tmode} onChange={(e) => setTmode(e.target.value)}>
              <option value="dubbing">Dubbing (natural voice-over)</option><option value="natural">Natural</option><option value="literal">Literal</option></select></Field>
            <Field label="Voice engine"><select value={engine} onChange={(e) => setEngine(e.target.value)}>
              <option value="auto">{tgt === "ru" ? "Auto (Best Russian)" : "Auto (best for language)"}</option><option value="fish">Fish Audio S2 Pro</option>
              <option value="qwen">Qwen3-TTS</option><option value="chatterbox">Chatterbox</option><option value="omnivoice">OmniVoice</option></select></Field>
            <Field label="Speakers"><select value={speakers} onChange={(e) => setSpeakers(e.target.value)}>
              <option value="auto">Auto-detect</option><option value="1">One speaker (faster)</option><option value="2">2</option><option value="3">3</option></select></Field>
            <Field label="Lip sync" hint={lipOk ? "" : "model not installed yet"}><select value={lipsync} onChange={(e) => setLipsync(e.target.value)}>
              <option value="off">Off (fastest)</option><option value="balanced" disabled={!lipOk}>Balanced</option><option value="quality" disabled={!lipOk}>Quality</option></select></Field>
            <Field label="Subtitles"><select value={subs} onChange={(e) => setSubs(e.target.value)}>
              <option value="off">Off</option><option value="original">Original</option><option value="translated">Translated</option><option value="both">Both</option></select></Field>
            <Field label="Subtitle style"><select value={subStyle} onChange={(e) => setSubStyle(e.target.value)} disabled={subs === "off"}>
              <option value="minimal">Minimal</option><option value="creator">Creator</option><option value="movie">Movie</option><option value="subtitle">Subtitle</option></select></Field>
          </div>
          <label className="row small"><input type="checkbox" checked={bg} onChange={(e) => setBg(e.target.checked)} />Preserve background audio (music, effects, ambience)</label>
          <Seg value={quality} onChange={setQuality} options={[{ v: "fast", label: "Fast" }, { v: "balanced", label: "Balanced" }, { v: "quality", label: "Quality" }]} />
          <div className="notice warn"><ShieldAlert size={15} style={{ flexShrink: 0 }} />Only clone voices you own or have permission to use.</div>
          <button className="btn primary big" disabled={!!running || !video} onClick={analyze}><Languages size={17} />{proj ? "Re-analyze" : translate ? "Translate video" : "Analyze & translate"}</button>
          <div className="small faint">Step 1 transcribes and translates. Review and edit every line, listen to the voices, make a 10-second preview, then render the full video.</div>
        </div>
      </div>

      <div className="col" style={{ gap: 14 }}>
        {running && job ? (
          <>
            <RunningView job={job} onCancel={cancel} />
            <div className="row wrap" style={{ gap: 6 }}>{stages.map((s) => (
              <span key={s} className={`pill ${job.message.toLowerCase().startsWith(s.toLowerCase().split(" ")[0]) ? "loading" : "offline"}`}>{s}</span>))}</div>
          </>
        ) : result ? <ResultView path={result} meta={translate ? "Translated video" : "Dubbed video"} /> :
          !proj && (video ? <ResultView path={video} meta="Input video" /> : <EmptyStage icon={FileVideo} text="Add a video, choose the target language and press Analyze" />)}

        {proj && !running && (
          <>
            <div className="card col">
              <div className="row"><h3 style={{ margin: 0 }} className="grow">Speakers · {LANGS[proj.source_language] ?? proj.source_language} → {LANGS[proj.target_language]}</h3>
                <span className="small muted">engine: {proj.engine}{proj.background ? " · background preserved" : ""}</span></div>
              {proj.speakers.map((s) => (
                <div key={s.id} className="voice-card" style={{ opacity: s.enabled === false ? 0.5 : 1 }}>
                  <label className="row small" title="Dub this speaker"><input type="checkbox" checked={s.enabled !== false} onChange={(e) => setSpk(s.id, { enabled: e.target.checked })} /></label>
                  <b style={{ minWidth: 80 }}>{s.label}</b>
                  {s.ref_url && <audio src={s.ref_url} controls style={{ width: 200 }} />}
                  <select style={{ width: 170 }} value={s.voice} onChange={(e) => setSpk(s.id, { voice: e.target.value })}>
                    <option value="clone">Clone original voice</option><option value="saved">Saved voice</option><option value="custom">Uploaded reference</option><option value="ai">Default AI voice</option></select>
                  {s.voice === "saved" && <select style={{ width: 160 }} value={s.voice_id ?? ""} onChange={(e) => setSpk(s.id, { voice_id: Number(e.target.value) || null })}>
                    <option value="">choose…</option>{voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select>}
                  {s.voice === "custom" && <div style={{ width: 200 }}><DropZone accept="audio/*" value={s.custom_ref} onChange={(p) => setSpk(s.id, { custom_ref: p })} label="Reference audio" kind="audio" /></div>}
                  {s.enabled === false && <span className="small faint">keeps original voice</span>}
                </div>
              ))}
              <div className="row">
                <button className="btn" onClick={() => render(true)}><Eye size={15} />Generate 10 s preview</button>
                <button className="btn primary" onClick={() => render(false)}><Play size={15} />Generate full video</button>
              </div>
            </div>
            <div className="card" style={{ padding: 0, maxHeight: 620, overflow: "auto" }}>
              <table className="tbl">
                <thead><tr><th style={{ width: 60 }}>Time</th><th style={{ width: 80 }}>Speaker</th><th>Original</th><th>Translation (editable)</th><th style={{ width: 96 }}>Voice</th></tr></thead>
                <tbody>{proj.segments.map((s) => {
                  const ln = lines[s.i];
                  return (
                    <tr key={s.i}>
                      <td className="mono muted">{t2(s.start)}</td>
                      <td className="small">{proj.speakers.find((x) => x.id === s.speaker)?.label}</td>
                      <td className="small">{s.text}</td>
                      <td><textarea rows={2} style={{ minHeight: 44 }} value={s.translation} onChange={(e) => setSeg(s.i, e.target.value)} />
                        {ln?.url && !ln.fits && <div className="small" style={{ color: "var(--warn)" }}>A bit long for its slot; consider shortening.</div>}</td>
                      <td><div className="row" style={{ gap: 4 }}>
                        <button className="btn sm icon" title="Preview voice" disabled={ln?.busy} onClick={() => (ln?.url && audio.current ? (audio.current.src = ln.url, audio.current.play()) : previewLine(s))}><Volume2 size={14} /></button>
                        <button className="btn sm icon" title="Regenerate" disabled={ln?.busy} onClick={() => previewLine(s)}><RefreshCw size={14} className={ln?.busy ? "spin" : ""} /></button>
                      </div></td>
                    </tr>);
                })}</tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
