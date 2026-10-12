import React, { useEffect, useState } from "react";
import { Plus, ShieldAlert, Trash2, UserRound, Wand2 } from "lucide-react";
import { api, fileUrl, Voice } from "../api";
import { DropZone, Field, Modal, Seg, Slider, useJob, useToast } from "../components/common";
import { EmptyStage, ResultView, RunningView, UrlFetch } from "../components/tools";
import { useNav } from "../App";

type Avatar = { id: number; name: string; image_path: string; image_url: string; video_url: string | null; video_path: string | null;
  voice_id: number | null; language: string | null; preview_url: string | null; notes: string | null };
const LANGS: Record<string, string> = { ru: "Russian", en: "English", de: "German", fr: "French", es: "Spanish", it: "Italian" };

export default function AvatarPage({ mode = "create" }: { mode?: "create" | "library" }) {
  const toast = useToast();
  const { go, takeHandoff } = useNav();
  const { job, run, cancel } = useJob();
  const [kind, setKind] = useState<"photo" | "video">("photo");
  const [avatars, setAvatars] = useState<Avatar[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [itOk, setItOk] = useState(false);
  const [avatarId, setAvatarId] = useState<number | "">("");
  const [image, setImage] = useState<string | null>(null);
  const [video, setVideo] = useState<string | null>(null);
  const [script, setScript] = useState("");
  const [audioIn, setAudioIn] = useState<string | null>(null);
  const [lang, setLang] = useState("ru");
  const [voice, setVoice] = useState("ai");
  const [voiceId, setVoiceId] = useState<number | "">("");
  const [aiVoice, setAiVoice] = useState("female");
  const [refAudio, setRefAudio] = useState<string | null>(null);
  const [engine, setEngine] = useState("auto");
  const [emotion, setEmotion] = useState("neutral");
  const [movement, setMovement] = useState("natural");
  const [quality, setQuality] = useState("balanced");
  const [bgPrompt, setBgPrompt] = useState("");
  const [resolution, setResolution] = useState("480p");
  const [similarity, setSimilarity] = useState(0.6);
  const [speed, setSpeed] = useState(1.0);
  const [result, setResult] = useState<string | null>(null);
  const [editing, setEditing] = useState<Partial<Avatar> & { image?: string; video?: string } | null>(null);

  const load = () => api.get<{ avatars: Avatar[]; infinitetalk: boolean }>("/api/avatars").then((r) => { setAvatars(r.avatars); setItOk(r.infinitetalk); });
  useEffect(() => {
    load(); api.get<Voice[]>("/api/voices").then(setVoices);
    const h = takeHandoff("avatar/create");
    if (h?.params?.avatar_id) setAvatarId(Number(h.params.avatar_id));
    if (h?.image) setImage(h.image);
    if (h?.video) { setVideo(h.video); setKind("video"); }
  }, []);
  useEffect(() => {   // saved avatar pre-fills its default voice and language
    const a = avatars.find((x) => x.id === avatarId);
    if (a) { if (a.voice_id) { setVoice("saved"); setVoiceId(a.voice_id); } if (a.language) setLang(a.language); }
  }, [avatarId, avatars]);

  const generate = async () => {
    const done = await run("/api/tools/avatar", {
      avatar_id: avatarId || undefined, image: kind === "photo" && !avatarId ? image : undefined, video: kind === "video" && !avatarId ? video : undefined,
      script: audioIn ? undefined : script, audio: audioIn || undefined, language: lang, voice, voice_id: voiceId || undefined,
      ref_audio: refAudio || undefined, ai_voice: aiVoice, engine, emotion, movement, quality, background_prompt: bgPrompt || undefined, resolution,
      similarity, speed });
    if (done?.status === "done" && done.result?.outputs?.[0]) setResult(done.result.outputs[0]);
  };
  const saveAvatar = async () => {
    try {
      if (editing?.id) await api.patch(`/api/avatars/${editing.id}`, { name: editing.name, voice_id: editing.voice_id, language: editing.language, notes: editing.notes });
      else await api.post("/api/avatars", editing);
      setEditing(null); load(); toast("Avatar saved", "ok");
    } catch (e: any) { toast(e.message, "err"); }
  };
  const running = job && ["queued", "running"].includes(job.status);
  const hasSource = !!avatarId || (kind === "photo" ? !!image : !!video);

  if (mode === "library") {
    return (
      <div className="col" style={{ gap: 16 }}>
        <div className="row"><div className="grow muted">Saved avatars keep one copy of their image/video and a default voice.</div>
          <button className="btn primary" onClick={() => setEditing({ name: "", language: "ru" })}><Plus size={15} />New avatar</button></div>
        {avatars.length === 0 && <EmptyStage icon={UserRound} text="No saved avatars yet" />}
        <div className="gallery" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))" }}>
          {avatars.map((a) => (
            <div key={a.id} className="card col" style={{ padding: 10 }}>
              <div className="tile" style={{ aspectRatio: "1" }}>{a.preview_url ? <video src={a.preview_url} muted loop /> : <img src={a.image_url} />}</div>
              <b>{a.name}</b>
              <div className="small muted">{LANGS[a.language ?? ""] ?? "any language"} · {voices.find((v) => v.id === a.voice_id)?.name ?? "no default voice"}{a.video_url ? " · video" : ""}</div>
              {a.notes && <div className="small faint">{a.notes}</div>}
              <div className="row">
                <button className="btn sm primary grow" onClick={() => go("avatar/create", { params: { avatar_id: a.id } })}>Use</button>
                <button className="btn sm" onClick={() => setEditing(a)}>Edit</button>
                <button className="btn sm ghost icon danger" onClick={async () => { if (confirm(`Delete avatar "${a.name}"?`)) { await api.del(`/api/avatars/${a.id}`); load(); } }}><Trash2 size={14} /></button>
              </div>
            </div>
          ))}
        </div>
        {editing && <AvatarModal editing={editing} setEditing={setEditing} voices={voices} onSave={saveAvatar} />}
      </div>
    );
  }

  return (
    <div className="studio">
      <div className="controls">
        <div className="card col">
          <Seg value={kind} onChange={(v) => setKind(v as any)} options={[{ v: "photo", label: "Photo avatar" }, { v: "video", label: "Video avatar" }]} />
          <Field label="Saved avatar"><select value={avatarId} onChange={(e) => setAvatarId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">— none, use a new {kind} —</option>{avatars.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></Field>
          {!avatarId && (kind === "photo"
            ? <Field label="Portrait photo"><DropZone accept="image/*" value={image} onChange={setImage} label="Clear front-facing portrait" /></Field>
            : <><Field label="Source video"><DropZone accept="video/*" value={video} onChange={setVideo} label="Short clip of the person" kind="media" /></Field>
              <UrlFetch allowRange onVideo={setVideo} /></>)}
          <Field label="Script" hint={audioIn ? "using uploaded audio" : `${script.length} chars`}>
            <textarea rows={5} value={script} disabled={!!audioIn} onChange={(e) => setScript(e.target.value)} placeholder="Привет! Сегодня я расскажу вам…" /></Field>
          <details className="adv"><summary>…or use your own audio instead of a script</summary><div className="inner">
            <DropZone accept="audio/*" value={audioIn} onChange={setAudioIn} label="Speech audio" kind="audio" /></div></details>
          <div className="grid cols-2">
            <Field label="Language"><select value={lang} onChange={(e) => setLang(e.target.value)}>{Object.entries(LANGS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
            <Field label="Voice"><select value={voice} onChange={(e) => setVoice(e.target.value)}>
              <option value="ai">Default AI voice</option><option value="saved">Saved voice</option><option value="upload">Uploaded reference</option>
              {kind === "video" && <option value="clone">Clone voice from the video</option>}</select></Field>
            {voice === "ai" && <Field label="Default voice"><select value={aiVoice} onChange={(e) => setAiVoice(e.target.value)}>
              <option value="female">Female</option><option value="male">Male</option></select></Field>}
            {voice === "saved" && <Field label="Saved voice"><select value={voiceId} onChange={(e) => setVoiceId(Number(e.target.value) || "")}>
              <option value="">choose…</option>{voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>}
            <Field label="Voice engine"><select value={engine} onChange={(e) => setEngine(e.target.value)}>
              <option value="auto">{lang === "ru" ? "Auto (Best Russian)" : "Auto"}</option><option value="fish">Fish Audio S2 Pro</option><option value="qwen">Qwen3-TTS</option>
              <option value="chatterbox">Chatterbox</option><option value="omnivoice">OmniVoice</option></select></Field>
            <Field label="Emotion"><select value={emotion} onChange={(e) => setEmotion(e.target.value)}>
              {["neutral", "warm", "calm", "serious", "excited"].map((x) => <option key={x}>{x}</option>)}</select></Field>
            <Field label="Movement"><select value={movement} onChange={(e) => setMovement(e.target.value)}>
              <option value="calm">Calm</option><option value="natural">Natural</option><option value="expressive">Expressive</option></select></Field>
            <Field label="Resolution"><select value={resolution} onChange={(e) => setResolution(e.target.value)}><option value="480p">480p</option><option value="720p">720p</option></select></Field>
          </div>
          {voice === "upload" && <DropZone accept="audio/*" value={refAudio} onChange={setRefAudio} label="Reference voice (5–15 s)" kind="audio" />}
          <Field label="Lip sync quality" hint={itOk ? "" : "InfiniteTalk still installing → Fast only"}>
            <Seg value={quality} onChange={setQuality} options={[{ v: "fast", label: "Fast (LTX-2.5)" }, { v: "balanced", label: "Balanced" }, { v: "quality", label: "Quality" }]} /></Field>
          <button className="btn primary big" disabled={!!running || !hasSource || (!script.trim() && !audioIn)} onClick={generate}><Wand2 size={17} />{running ? "Generating…" : "Generate avatar video"}</button>
          <div className="notice warn"><ShieldAlert size={15} style={{ flexShrink: 0 }} />Only use faces and voices you own or have permission to use.</div>
        </div>
        <details className="adv"><summary>Advanced settings</summary><div className="inner">
          <Slider label="Voice similarity" value={similarity} min={0} max={1} step={0.05} onChange={setSimilarity} fmt={(v) => `${Math.round(v * 100)}%`} />
          <Slider label="Speaking rate" value={speed} min={0.75} max={1.3} step={0.05} onChange={setSpeed} fmt={(v) => `${v.toFixed(2)}×`} />
          <Field label="Background" hint="empty = keep original"><input type="text" value={bgPrompt} onChange={(e) => setBgPrompt(e.target.value)} placeholder="(keep the original background)" /></Field>
        </div></details>
      </div>
      <div className="col" style={{ gap: 14 }}>
        {running && job ? <RunningView job={job} onCancel={cancel} /> : result ? <ResultView path={result} meta="Avatar video" /> :
          avatarId ? <div className="stage"><img src={avatars.find((a) => a.id === avatarId)?.image_url} /></div> :
          image && kind === "photo" ? <ResultView path={image} meta="Portrait" /> : video && kind === "video" ? <ResultView path={video} meta="Source video" /> :
          <EmptyStage icon={UserRound} text="Choose a photo, a video or a saved avatar, then write the script" />}
        {!running && (image || video) && !avatarId && (
          <button className="btn" onClick={() => setEditing({ name: "", image: kind === "photo" ? image ?? undefined : undefined, video: kind === "video" ? video ?? undefined : undefined, language: lang, voice_id: voiceId || null })}>
            <Plus size={15} />Save as avatar</button>)}
      </div>
      {editing && <AvatarModal editing={editing} setEditing={setEditing} voices={voices} onSave={saveAvatar} />}
    </div>
  );
}

function AvatarModal({ editing, setEditing, voices, onSave }: { editing: any; setEditing: (x: any) => void; voices: Voice[]; onSave: () => void }) {
  return (
    <Modal title={editing.id ? "Edit avatar" : "Save avatar"} onClose={() => setEditing(null)}>
      <div className="col" style={{ width: "min(520px, 85vw)" }}>
        <Field label="Name"><input type="text" value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} autoFocus /></Field>
        {!editing.id && !editing.image && !editing.video && <>
          <Field label="Reference image"><DropZone accept="image/*" value={editing.image ?? null} onChange={(p) => setEditing({ ...editing, image: p })} label="Portrait" /></Field>
          <Field label="Reference video (optional)"><DropZone accept="video/*" value={editing.video ?? null} onChange={(p) => setEditing({ ...editing, video: p })} label="Short clip" kind="media" /></Field></>}
        <Field label="Default voice"><select value={editing.voice_id ?? ""} onChange={(e) => setEditing({ ...editing, voice_id: Number(e.target.value) || null })}>
          <option value="">none</option>{voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>
        <Field label="Language"><select value={editing.language ?? ""} onChange={(e) => setEditing({ ...editing, language: e.target.value || null })}>
          <option value="">any</option>{Object.entries(LANGS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Notes"><input type="text" value={editing.notes ?? ""} onChange={(e) => setEditing({ ...editing, notes: e.target.value })} /></Field>
        <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={() => setEditing(null)}>Cancel</button><button className="btn primary" onClick={onSave}>Save</button></div>
      </div>
    </Modal>
  );
}
