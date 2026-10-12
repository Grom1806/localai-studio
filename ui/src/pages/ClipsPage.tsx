import React, { useEffect, useRef, useState } from "react";
import { AudioLines, Check, Copy, Download, Film, Heart, Pencil, Scissors, ThumbsDown, Trash2, Type, Wand2, X, ZoomIn, Upload } from "lucide-react";
import { api, downloadUrl, fileUrl, waitJob } from "../api";
import { DropZone, Field, Modal, Seg, Slider, useJob, useToast } from "../components/common";
import { EmptyStage, ResultView, RunningView, SendTo, UrlFetch } from "../components/tools";
import { blankSocial, SocialMetadataPanel, type SocialMeta } from "../components/SocialMetadataPanel";
import { useNav } from "../App";

type Word = { start: number; end: number; word: string };
type Frame = { path: string; time: number };
type ContentReview = { language: string; content_summary: string; visual_summary: string; opening_note: string; ending_note: string;
  subtitle_review?: string; context_ok: boolean; issues: string[]; edit_suggestions: string[]; visual_review_available?: boolean;
  audio_metrics?: { mean_db: number | null; peak_db: number | null } | null };
type Clip = { id: string; start: number; end: number; score: number; hook: number; interest: number; context: number;
  engagement: number; shareability: number; reason: string; text: string; words: Word[]; titles: string[]; status: string;
  label: string; title?: string; cuts?: [number, number][]; exports?: string[]; preview?: string;
  render_warnings?: string[];
  visual_frames?: Frame[]; analysis?: ContentReview; social?: SocialMeta; audio_metrics?: { mean_db: number | null; peak_db: number | null } | null };
type Project = { id: string; video_rel: string; language: string; scorer: string; clips: Clip[]; headline?: boolean;
  length?: [number, number]; info: { width: number; height: number; duration: number } };
type YoutubeStatus = { connected: boolean; target_ready: boolean; target_channel?: string; channel?: { title: string } | null };
type YoutubePublished = { video_id: string; title: string; url: string; studio_url: string; privacy_status: string; uploaded_at?: number };
type YoutubeReview = { clip: Clip; file: string; title: string; description: string; tags: string; hashtags: string;
  privacyStatus: "private" | "unlisted" | "public"; madeForKids: boolean };
type YoutubeVideoInfo = { width: number; height: number; duration: number };
type YoutubeBatchItem = YoutubeReview & { info: YoutubeVideoInfo };
const fmt = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
const mmss = (t: number) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const LAST = "localai.clips.last";
const titleOf = (c: Clip) => c.title ?? c.titles?.[0] ?? c.text.slice(0, 60);
function inspectYoutubeVideo(path: string): Promise<YoutubeVideoInfo> {
  return new Promise((resolve, reject) => {
    const el = document.createElement("video");
    el.preload = "metadata";
    el.onloadedmetadata = () => resolve({ width: el.videoWidth, height: el.videoHeight, duration: el.duration });
    el.onerror = () => reject(new Error("не удалось прочитать свойства файла"));
    el.src = fileUrl(path);
    el.load();
  });
}

// Grid thumbnail: shows a still frame after the fade-in; playback is user-initiated only.
function Thumb({ src }: { src: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    const seek = () => { if (v.paused && v.currentTime < 0.5) v.currentTime = 1.2; };
    if (v.readyState >= 1) seek();
    v.addEventListener("loadedmetadata", seek);
    return () => v.removeEventListener("loadedmetadata", seek);
  }, [src]);
  return <video ref={ref} src={src} muted playsInline preload="auto" loop />;
}

export default function ClipsPage() {
  const toast = useToast();
  const { takeHandoff, go } = useNav();
  const { job, run, cancel } = useJob();
  const [video, setVideo] = useState<string | null>(null);
  const [length, setLength] = useState("medium");
  const [minS, setMinS] = useState(20);
  const [maxS, setMaxS] = useState(45);
  const [count, setCount] = useState("5");
  const [proj, setProj] = useState<Project | null>(null);
  const [editing, setEditing] = useState<Clip | null>(null);
  const [detail, setDetail] = useState<Clip | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [recent, setRecent] = useState<{ id: string; video: string; clips: number; created: number }[]>([]);
  const [hideBanner, setHideBanner] = useState(false);
  const [youtube, setYoutube] = useState<YoutubeStatus | null>(null);
  const [youtubeReview, setYoutubeReview] = useState<YoutubeReview | null>(null);
  const [youtubePreparing, setYoutubePreparing] = useState(false);
  const [youtubeVideoInfo, setYoutubeVideoInfo] = useState<{ width: number; height: number; duration: number } | null>(null);
  const [publishedUploads, setPublishedUploads] = useState<YoutubePublished[]>([]);
  const [batchSelected, setBatchSelected] = useState<string[]>([]);
  const [youtubeBatch, setYoutubeBatch] = useState<YoutubeBatchItem[] | null>(null);
  const [batchResults, setBatchResults] = useState<Record<string, { ok: boolean; message: string; url?: string }>>({});
  const [batchProgress, setBatchProgress] = useState(0);
  const [batchPublishing, setBatchPublishing] = useState(false);

  const refresh = async (id: string) => {
    const p = await api.get<Project>(`/api/clips/${id}`);
    setProj(p);
    setDetail((d) => (d ? p.clips.find((c) => c.id === d.id) ?? null : null));
    return p;
  };

  useEffect(() => {
    const h = takeHandoff("video/clips");
    if (h?.video) setVideo(h.video);
    api.get<any[]>("/api/clips-projects").then((r) => {
      setRecent(r);
      if (!h?.video) {                    // after a page reload, reopen the last analysis
        let last: string | null = null;
        try { last = localStorage.getItem(LAST); } catch { /* storage unavailable */ }
        const id = r.find((x) => x.id === last)?.id ?? r[0]?.id;
        if (id) openProject(id);
      }
    }).catch(() => {});
    api.get<YoutubeStatus>("/api/youtube/status").then(setYoutube).catch(() => {});
    api.get<YoutubePublished[]>("/api/youtube/published").then(setPublishedUploads).catch(() => {});
  }, []);
  useEffect(() => { setBatchSelected([]); setYoutubeBatch(null); setBatchResults({}); }, [proj?.id]);

  const remember = (id: string) => { try { localStorage.setItem(LAST, id); } catch { /* ignore */ } };
  const openProject = async (id: string) => {
    remember(id);
    const p = await refresh(id);
    setVideo(p.video_rel); setResult(null);
    if (p.clips.some((c) => !c.preview)) {      // older analyses: create the 9:16 previews now
      const done = await run("/api/tools/clips_previews", { project: id });
      if (done?.status === "done") await refresh(id);
    }
  };
  const analyze = async () => {
    setProj(null); setResult(null);
    const done = await run("/api/tools/clips_analyze", { video, length, min_seconds: minS, max_seconds: maxS, count: Number(count) });
    if (done?.status === "done") { const id = (done.result as any).project; remember(id); await refresh(id); }
    api.get<any[]>("/api/clips-projects").then(setRecent).catch(() => {});
  };
  const findMoreClips = async () => {
    if (!proj?.video_rel || running) return;
    const [minSeconds, maxSeconds] = proj.length ?? [20, 45];
    setCount("10");
    setResult(null);
    const done = await run("/api/tools/clips_analyze", { video: proj.video_rel, length: "custom",
      min_seconds: minSeconds, max_seconds: maxSeconds, count: 10, exclude_existing: true });
    if (done?.status === "done") {
      const id = (done.result as any).project;
      remember(id);
      await refresh(id);
      toast(`Создано новых клипов: ${(done.result as any).clips ?? 0}`, "ok");
    }
    api.get<any[]>("/api/clips-projects").then(setRecent).catch(() => {});
  };
  const setStatus = async (c: Clip, status: string) => {
    await api.patch(`/api/clips/${proj!.id}/${c.id}`, { status: c.status === status ? "candidate" : status });
    await refresh(proj!.id);
  };
  const exportClip = async (c: Clip, edits: Record<string, any> = {}, download = false) => {
    const done = await run("/api/tools/clips_export", { project: proj!.id, clip: c.id, edits: { aspect: "9:16", export: "tiktok", ...edits } });
    if (done?.status === "done" && done.result?.outputs?.[0]) {
      await refresh(proj!.id);
      if (download) window.location.href = downloadUrl(done.result.outputs[0]);
      else setResult(done.result.outputs[0]);
      const warnings = Array.isArray(done.result.warnings) ? done.result.warnings : [];
      toast(warnings.length ? `Экспорт готов. ${warnings.join(" ")}` : "Клип экспортирован в HD (1080×1920)", "ok");
      return done.result.outputs[0] as string;
    }
    return null;
  };
  const exportThen = async (c: Clip, page: "video/dub" | "video/upscale") => {
    const path = c.exports?.slice(-1)[0] ?? (await exportClip(c));
    if (path) go(page, { video: path });
  };
  const duplicate = async (c: Clip) => {
    await api.post(`/api/clips/${proj!.id}/${c.id}/duplicate`);
    const p = await refresh(proj!.id);
    const missing = p.clips.filter((x) => !x.preview);
    if (missing.length) { await run("/api/tools/clips_previews", { project: proj!.id }); await refresh(proj!.id); }
    toast("Clip duplicated", "ok");
  };
  const toggleHeadline = async () => {
    const on = !(proj!.headline ?? true);
    await api.patch(`/api/clips/${proj!.id}`, { headline: on });
    const done = await run("/api/tools/clips_previews", { project: proj!.id, force: true });
    if (done?.status === "done") await refresh(proj!.id);
  };
  const enrichClip = async (c: Clip, start = c.start, end = c.end) => {
    if (!proj) return;
    const done = await run("/api/tools/clips_enrich", { project: proj.id, clip: c.id, start, end });
    if (done?.status === "done") {
      const updated = await refresh(proj.id);
      setEditing(updated.clips.find((x) => x.id === c.id) ?? null);
      toast("Video review and platform metadata updated", "ok");
    }
  };
  const saveSocial = async (c: Clip, social: SocialMeta) => {
    if (!proj) return;
    await api.patch(`/api/clips/${proj.id}/${c.id}`, { social });
    const updated = await refresh(proj.id);
    setEditing(updated.clips.find((x) => x.id === c.id) ?? null);
    toast("Platform metadata saved", "ok");
  };
  const openYoutubeReview = async (c: Clip) => {
    if (!youtube?.target_ready) { go("settings"); toast("Connect your YouTube channel in Settings first", "err"); return; }
    if (!proj || youtubePreparing) return;
    setYoutubePreparing(true);
    try {
      let clip = c;
      let meta = clip.social?.youtube;
      const hasMetadata = Boolean(meta?.title?.trim() && meta.description?.trim()
        && Array.isArray(meta.tags) && Array.isArray(meta.hashtags));
      if (!hasMetadata) {
        toast("Подготавливаю заголовок, описание и теги по содержанию клипа…", "info");
        const done = await run("/api/tools/clips_enrich", { project: proj.id, clip: c.id, start: c.start, end: c.end });
        if (done?.status !== "done") return;
        const updated = await refresh(proj.id);
        clip = updated.clips.find((item) => item.id === c.id) ?? c;
        meta = clip.social?.youtube;
      }
      let file = clip.exports?.slice(-1)[0];
      if (!file) file = (await exportClip(clip, { aspect: "9:16", export: "shorts" })) ?? undefined;
      if (!file) return;
      setYoutubeVideoInfo(null);
      setYoutubeReview({ clip, file, title: (meta?.title || titleOf(clip)).slice(0, 100), description: meta?.description || "",
        tags: (meta?.tags || []).join(", "), hashtags: (meta?.hashtags || []).join(", "), privacyStatus: "private", madeForKids: false });
    } finally {
      setYoutubePreparing(false);
    }
  };
  const exportVerticalForYoutube = async () => {
    if (!youtubeReview) return;
    const file = await exportClip(youtubeReview.clip, { aspect: "9:16", export: "shorts" });
    if (file) { setYoutubeVideoInfo(null); setYoutubeReview({ ...youtubeReview, file }); }
  };
  const uploadYoutube = async () => {
    if (!youtubeReview) return;
    if (proj) {
      const social = { ...blankSocial(), ...(youtubeReview.clip.social ?? {}), youtube: {
        title: youtubeReview.title.trim(), description: youtubeReview.description,
        tags: youtubeReview.tags.split(",").map((x) => x.trim()).filter(Boolean),
        hashtags: youtubeReview.hashtags.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean),
      } };
      try { await api.patch(`/api/clips/${proj.id}/${youtubeReview.clip.id}`, { social }); }
      catch (e: any) { toast(e.message, "err"); return; }
    }
    const done = await run("/api/tools/youtube_upload", { file: youtubeReview.file, title: youtubeReview.title.trim(),
      source_clip_id: `${proj?.id ?? ""}:${youtubeReview.clip.id}`,
      description: youtubeReview.description, tags: youtubeReview.tags.split(",").map((x) => x.trim()).filter(Boolean),
      hashtags: youtubeReview.hashtags.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean),
      privacy_status: youtubeReview.privacyStatus, made_for_kids: youtubeReview.madeForKids });
    if (done?.status === "done" && done.result?.url) {
      api.get<YoutubePublished[]>("/api/youtube/published").then(setPublishedUploads).catch(() => {});
      setYoutubeReview(null);
      setYoutubeVideoInfo(null);
      toast("Short uploaded to YouTube", "ok");
      api.get<YoutubeStatus>("/api/youtube/status").then(setYoutube).catch(() => {});
    }
  };
  const prepareYoutubeBatch = async () => {
    if (!proj || !youtube?.target_ready || youtubePreparing || !batchSelected.length) return;
    setYoutubePreparing(true); setYoutubeBatch(null); setBatchResults({}); setBatchProgress(0);
    const prepared: YoutubeBatchItem[] = [];
    try {
      for (const id of batchSelected) {
        let clip = proj.clips.find((x) => x.id === id);
        if (!clip || clip.status === "rejected") continue;
        let meta = clip.social?.youtube;
        const hasMetadata = Boolean(meta?.title?.trim() && meta.description?.trim()
          && Array.isArray(meta.tags) && Array.isArray(meta.hashtags));
        if (!hasMetadata) {
          toast(`Подготавливаю метаданные для «${titleOf(clip)}»…`, "info");
          const done = await run("/api/tools/clips_enrich", { project: proj.id, clip: clip.id, start: clip.start, end: clip.end });
          if (done?.status !== "done") continue;
          const updated = await refresh(proj.id);
          clip = updated.clips.find((x) => x.id === id) ?? clip;
          meta = clip.social?.youtube;
        }
        const file = await exportClip(clip, { aspect: "9:16", export: "shorts" });
        if (!file) continue;
        try {
          const info = await inspectYoutubeVideo(file);
          if (info.height < info.width || info.duration > 180 || info.duration <= 0) {
            toast(`Клип «${titleOf(clip)}» пропущен: формат не подходит для Shorts.`, "err");
            continue;
          }
          prepared.push({ clip, file, info, title: (meta?.title || titleOf(clip)).slice(0, 100),
            description: meta?.description || "", tags: (meta?.tags || []).join(", "), hashtags: (meta?.hashtags || []).join(", "),
            privacyStatus: "private", madeForKids: false });
        } catch (e: any) { toast(`Не удалось проверить «${titleOf(clip)}»: ${e.message}`, "err"); }
      }
      setBatchSelected([]);
      setYoutubeBatch(prepared);
      if (!prepared.length) toast("Нет готовых клипов для пакетной публикации.", "err");
      else toast(`Подготовлено клипов к проверке: ${prepared.length}`, "ok");
    } finally { setYoutubePreparing(false); }
  };
  const updateBatchItem = (id: string, patch: Partial<YoutubeBatchItem>) => setYoutubeBatch((all) => all?.map((x) => x.clip.id === id ? { ...x, ...patch } : x) ?? null);
  const uploadYoutubeBatch = async () => {
    if (!youtubeBatch?.length || batchPublishing) return;
    const remaining = youtubeBatch.filter((item) => !batchResults[item.clip.id]?.ok);
    if (!remaining.length) return;
    setBatchPublishing(true); setBatchProgress(0);
    let completed = 0, succeeded = 0;
    for (const item of remaining) {
      const id = item.clip.id;
      try {
        if (proj) {
          const social = { ...blankSocial(), ...(item.clip.social ?? {}), youtube: {
            title: item.title.trim(), description: item.description,
            tags: item.tags.split(",").map((x) => x.trim()).filter(Boolean),
            hashtags: item.hashtags.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean),
          } };
          await api.patch(`/api/clips/${proj.id}/${id}`, { social });
        }
        const done = await run("/api/tools/youtube_upload", { file: item.file, title: item.title.trim(),
          source_clip_id: `${proj?.id ?? ""}:${id}`, description: item.description,
          tags: item.tags.split(",").map((x) => x.trim()).filter(Boolean),
          hashtags: item.hashtags.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean),
          privacy_status: item.privacyStatus, made_for_kids: item.madeForKids });
        if (done?.status === "done" && done.result?.url) {
          succeeded++;
          setBatchResults((all) => ({ ...all, [id]: { ok: true, message: "Загружено", url: String(done.result?.url) } }));
        } else {
          setBatchResults((all) => ({ ...all, [id]: { ok: false, message: done?.error || "Не удалось загрузить" } }));
        }
      } catch (e: any) { setBatchResults((all) => ({ ...all, [id]: { ok: false, message: e.message || "Ошибка загрузки" } })); }
      completed++; setBatchProgress(completed);
    }
    setBatchPublishing(false);
    api.get<YoutubePublished[]>("/api/youtube/published").then(setPublishedUploads).catch(() => {});
    api.get<YoutubeStatus>("/api/youtube/status").then(setYoutube).catch(() => {});
    toast(`Пакетная загрузка завершена · успешно ${succeeded} из ${completed}.`, succeeded ? "ok" : "err");
  };
  const exportAll = async () => { for (const c of proj!.clips.filter((c) => c.status !== "rejected")) await exportClip(c); };
  const running = job && ["queued", "running"].includes(job.status);
  const clipsSorted = proj ? [...proj.clips].sort((a, b) => (a.status === "rejected" ? 1 : 0) - (b.status === "rejected" ? 1 : 0)) : [];

  return (
    <div className="studio" style={{ gridTemplateColumns: proj ? "320px minmax(0, 1fr)" : undefined }}>
      <div className="controls">
        <div className="card col">
          <Field label="Long video"><DropZone accept="video/*" value={video} onChange={(v) => { setVideo(v); setProj(null); }} label="Podcast, interview, stream…" kind="media" /></Field>
          <UrlFetch allowRange onVideo={(v) => { setVideo(v); setProj(null); }} />
          <Field label="Clip length"><Seg value={length} onChange={setLength} options={[{ v: "short", label: "15–30 s" }, { v: "medium", label: "30–60 s" }, { v: "long", label: "60–90 s" }, { v: "custom", label: "Custom" }]} /></Field>
          {length === "custom" && <div className="grid cols-2">
            <Field label="Min seconds"><input type="number" value={minS} onChange={(e) => setMinS(Number(e.target.value))} /></Field>
            <Field label="Max seconds"><input type="number" value={maxS} onChange={(e) => setMaxS(Number(e.target.value))} /></Field></div>}
          <Field label="How many"><Seg value={count} onChange={setCount} options={[{ v: "3", label: "Best 3" }, { v: "5", label: "Best 5" }, { v: "10", label: "Best 10" }]} /></Field>
          <button className="btn primary big" disabled={!!running || !video} onClick={analyze}><Wand2 size={17} />{running ? "Working…" : "Find best clips"}</button>
          {recent.length > 0 && <Field label="Previous analyses"><select value={proj?.id ?? ""} onChange={(e) => e.target.value && openProject(e.target.value)}>
            <option value="">choose…</option>{recent.map((r) => <option key={r.id} value={r.id}>{r.video.split("/").pop()} · {r.clips} clips · {new Date(r.created * 1000).toLocaleString()}</option>)}</select></Field>}
          <div className="small faint">Transcribed locally; the local Gemma model scores hook, context, engagement and shareability. Clips start and end on full sentences.</div>
        </div>
      </div>

      <div className="col" style={{ gap: 14 }}>
        {running && job && <RunningView job={job} onCancel={cancel} />}
        {!running && result && <ResultView path={result} meta="Exported clip (1080×1920)" />}
        {!running && !proj && !result && (video ? <ResultView path={video} meta="Input video" /> : <EmptyStage icon={Scissors} text="Add a long video or paste a link to find its best moments" />)}

        {proj && (
          <>
            <div className="row">
              <div className="grow"><b>Original clips ({proj.clips.length})</b> <span className="small muted">· {proj.language} · scored by {proj.scorer === "llm" ? "local LLM" : "heuristics"}</span></div>
              <button className="btn sm primary" onClick={prepareYoutubeBatch} disabled={!batchSelected.length || !!running || youtubePreparing || !youtube?.target_ready}>
                <Upload size={14} />{youtubePreparing ? "Подготовка набора…" : <>Набор YouTube · {batchSelected.length}</>}</button>
              <button className="btn sm" onClick={findMoreClips} disabled={!!running}><Wand2 size={14} />Find more clips</button>
              <button className="btn sm" onClick={exportAll} disabled={!!running}><Download size={14} />Export all (9:16 HD)</button>
            </div>
            {!youtube?.target_ready && <div className="card row wrap" style={{ padding: "12px 16px" }}>
              <div className="grow small">Connect the correct YouTube channel before uploading Shorts.</div>
              <button className="btn sm" onClick={() => go("settings")}><Upload size={14} />Set up YouTube</button>
            </div>}
            {publishedUploads.length > 0 && <div className="card col youtube-published" style={{ gap: 10 }}>
              <b>Последние загруженные видео</b>
              {publishedUploads.slice(0, 5).map((item) => <div className="row wrap youtube-published-item" key={item.video_id}>
                <span className="small youtube-published-title">{item.title || item.video_id} · {item.privacy_status === "private" ? "приватное" : item.privacy_status === "unlisted" ? "по ссылке" : "открытое"}</span>
                <a className="btn sm ghost" href={item.url} target="_blank" rel="noreferrer"><Upload size={14} />Открыть видео</a>
                <a className="btn sm" href={item.studio_url} target="_blank" rel="noreferrer"><Check size={14} />Проверить в YouTube Studio</a>
              </div>)}
              <div className="small youtube-review-copyright">Результат проверки авторских прав смотрите в YouTube Studio. Обычный API показывает обработку или отказ видео, но не результат раздела Checks и детали Content ID. Проверка может занять время и не является окончательной; чтобы проверить до публикации, загрузите видео приватно.</div>
            </div>}
            {!hideBanner && (
              <div className="card row" style={{ padding: "12px 16px" }}>
                <Type size={18} className="muted" />
                <div className="grow"><b>Auto headline</b>
                  <div className="small muted">{(proj.headline ?? true)
                    ? "A headline is shown in the first 5 seconds of every clip. Change it per clip in Edit clip."
                    : "Headlines are switched off for this project."}</div></div>
                <button className="btn sm" disabled={!!running} onClick={toggleHeadline}>{(proj.headline ?? true) ? "Disable it" : "Enable"}</button>
                <button className="btn sm ghost icon" onClick={() => setHideBanner(true)}><X size={14} /></button>
              </div>
            )}
            <div className="gallery" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 22 }}>
              {clipsSorted.map((c) => (
                <div key={c.id} className="col" style={{ gap: 8, opacity: c.status === "rejected" ? 0.4 : 1 }}>
                  <div className="tile" style={{ aspectRatio: "9/16", borderRadius: 14 }} onClick={() => setDetail(c)}>
                    {c.preview ? <Thumb src={fileUrl(c.preview)} />
                      : <div style={{ display: "grid", placeItems: "center", height: "100%" }} className="small faint">making 9:16 preview…</div>}
                    <div style={{ position: "absolute", top: 8, left: 8 }} className="pill online" title="Virality score">{c.score}</div>
                    <div style={{ position: "absolute", bottom: 8, right: 8, background: "rgba(0,0,0,.65)", borderRadius: 8, padding: "2px 7px" }} className="small mono">
                      {mmss(c.end - c.start)}</div>
                    {c.status === "liked" && <Heart size={18} style={{ position: "absolute", top: 10, right: 10, color: "#f87171", fill: "#f87171" }} />}
                  </div>
                  <div className="row wrap" style={{ justifyContent: "center", gap: 4 }}>
                    <label className="row small muted" style={{ gap: 4, padding: "0 5px" }} onClick={(e) => e.stopPropagation()} title="Add this clip to the batch review">
                      <input type="checkbox" checked={batchSelected.includes(c.id)} disabled={c.status === "rejected" || !youtube?.target_ready}
                        onChange={(e) => setBatchSelected((ids) => e.target.checked ? [...ids, c.id] : ids.filter((x) => x !== c.id))} />Batch
                    </label>
                    <button className="btn sm ghost icon" title="Download HD" disabled={!!running} onClick={() => exportClip(c, {}, true)}><Download size={16} /></button>
                    <button className="btn sm ghost icon" title="Edit clip" onClick={() => setEditing(c)}><Scissors size={16} /></button>
                    <button className="btn sm" title="Metadata preparation starts only after this click" aria-label="Preview for YouTube"
                      disabled={!!running || youtubePreparing || !youtube?.target_ready} onClick={() => openYoutubeReview(c)}><Upload size={14} />Preview for YouTube</button>
                    <button className="btn sm ghost icon" title="Dub this clip" onClick={() => exportThen(c, "video/dub")}><AudioLines size={16} /></button>
                    <button className="btn sm ghost icon" title={c.status === "rejected" ? "Restore" : "Reject"} onClick={() => setStatus(c, "rejected")}>
                      {c.status === "rejected" ? <Check size={16} /> : <ThumbsDown size={16} />}</button>
                  </div>
                  <div style={{ fontWeight: 600, fontSize: 14.5, lineHeight: 1.35, cursor: "pointer" }} onClick={() => setDetail(c)}>{titleOf(c)}</div>
                  {c.social?.youtube?.title && <div className="small muted ellipsis">YouTube: {c.social.youtube.title}</div>}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {detail && proj && (
        <Modal title="" onClose={() => setDetail(null)}>
          <div className="row" style={{ alignItems: "stretch", gap: 20, width: "min(1080px, 88vw)" }}>
            <div className="col" style={{ gap: 10, alignItems: "center" }}>
              <button className={`btn icon ${detail.status === "liked" ? "primary" : ""}`} title="Like" onClick={() => setStatus(detail, "liked")}><Heart size={16} /></button>
              <button className={`btn icon ${detail.status === "rejected" ? "danger" : ""}`} title="Dislike / reject" onClick={() => setStatus(detail, "rejected")}><ThumbsDown size={16} /></button>
            </div>
            <div style={{ position: "relative", flexShrink: 0 }}>
              {detail.preview
                ? <video src={fileUrl(detail.preview)} key={detail.preview} controls playsInline style={{ height: "72vh", aspectRatio: "9/16", borderRadius: 14, background: "#000", display: "block" }} />
                : <SegmentPlayer src={fileUrl(proj.video_rel)} start={detail.start} end={detail.end} />}
              <span className="pill offline" style={{ position: "absolute", top: 10, left: 10, background: "rgba(0,0,0,.6)" }}>LOW-RES PREVIEW</span>
            </div>
            <div className="col grow" style={{ minWidth: 0, gap: 12 }}>
              <div style={{ fontSize: 18, fontWeight: 600, lineHeight: 1.35 }}>{titleOf(detail)}</div>
              <div className="row small muted"><span className="score" style={{ minWidth: 38, height: 38, fontSize: 15 }}>{detail.score}</span>
                <span className="mono">[{mmss(detail.start)} – {mmss(detail.end)}]</span><span>{(detail.end - detail.start).toFixed(0)} s</span></div>
              <div className="grid cols-4" style={{ gap: 8 }}>
                {(["hook", "context", "engagement", "shareability"] as const).map((m) => (
                  <div key={m}><div className="small muted row" style={{ justifyContent: "space-between" }}><span>{m[0].toUpperCase() + m.slice(1)}</span><b>{detail[m]}</b></div>
                    <div className="bar"><div style={{ width: `${detail[m]}%` }} /></div></div>))}
              </div>
              {detail.reason && <div className="small faint">{detail.reason}</div>}
              {detail.analysis && <div className="card col" style={{ padding: 12, gap: 6 }}>
                <b className="small">AI video review</b>
                {detail.analysis.content_summary && <div className="small">{detail.analysis.content_summary}</div>}
                {detail.analysis.visual_summary && <div className="small muted">{detail.analysis.visual_summary}</div>}
                {!detail.analysis.visual_review_available && <div className="small faint">Visual review is unavailable for this clip.</div>}
                {(detail.analysis.issues ?? []).map((x, i) => <div key={i} className="small muted">• {x}</div>)}
                {detail.visual_frames?.length ? <div className="row wrap">{detail.visual_frames.map((f, i) =>
                  <img key={i} src={fileUrl(f.path)} alt={`Frame at ${f.time}s`} title={`${f.time}s`} loading="lazy"
                    style={{ width: 76, height: 76, objectFit: "cover", borderRadius: 8 }} />)}</div> : null}
              </div>}
              <div className="small" style={{ lineHeight: 1.6, maxHeight: "34vh", overflow: "auto", paddingRight: 6 }}>{detail.text}</div>
            </div>
            <div className="col" style={{ gap: 8, width: 210, flexShrink: 0 }}>
              <button className="btn primary" disabled={!!running} onClick={() => exportClip(detail, {}, true)}><Download size={15} />Download HD</button>
              <button className="btn" disabled={!!running || youtubePreparing || !youtube?.target_ready} onClick={() => { setDetail(null); openYoutubeReview(detail); }}><Upload size={15} />Preview for YouTube</button>
              <button className="btn" onClick={() => { setEditing(detail); setDetail(null); }}><Pencil size={15} />Edit clip</button>
              <button className="btn" disabled={!!running} onClick={() => exportThen(detail, "video/dub")}><AudioLines size={15} />Dub / translate</button>
              <button className="btn" disabled={!!running} onClick={() => exportThen(detail, "video/upscale")}><ZoomIn size={15} />Upscale</button>
              <button className="btn" disabled={!!running} onClick={() => duplicate(detail)}><Copy size={15} />Duplicate</button>
              {detail.exports?.slice(-1).map((e) => <a key={e} className="btn ghost" href={downloadUrl(e)}><Film size={15} />Last export</a>)}
              {running && job && <div className="small muted">{job.message} · {Math.round(job.progress * 100)}%</div>}
            </div>
          </div>
        </Modal>
      )}
      {youtubeReview && <Modal title="Проверка YouTube Shorts перед загрузкой" onClose={() => { if (!running) { setYoutubeReview(null); setYoutubeVideoInfo(null); } }}>
        <div className="youtube-review">
        <div className="grid cols-2 youtube-review-grid" style={{ width: "min(920px, 88vw)", alignItems: "start", gap: 18 }}>
          <div className="col youtube-review-preview" style={{ gap: 9 }}>
            <video src={fileUrl(youtubeReview.file)} controls playsInline style={{ width: "100%", maxHeight: "62vh", aspectRatio: "9/16", objectFit: "contain", background: "#000", borderRadius: 12 }}
              onLoadedMetadata={(e) => setYoutubeVideoInfo({ width: e.currentTarget.videoWidth, height: e.currentTarget.videoHeight, duration: e.currentTarget.duration })} />
            <div className="small youtube-review-file">Файл: <span className="mono">{youtubeReview.file.split("/").pop()}</span></div>
            {youtubeVideoInfo && <div className={`small youtube-review-format ${youtubeVideoInfo.height >= youtubeVideoInfo.width && youtubeVideoInfo.duration <= 180 ? "ok" : "warning"}`}>
              {youtubeVideoInfo.height >= youtubeVideoInfo.width && youtubeVideoInfo.duration <= 180
                ? `Формат Shorts проверен · ${youtubeVideoInfo.width}×${youtubeVideoInfo.height} · ${Math.round(youtubeVideoInfo.duration)} с`
                : `Пока не подходит для Shorts · ${youtubeVideoInfo.width}×${youtubeVideoInfo.height} · ${Math.round(youtubeVideoInfo.duration)} с (нужно квадратное/вертикальное видео до 180 секунд)`}
            </div>}
            {youtubeVideoInfo && youtubeVideoInfo.height < youtubeVideoInfo.width && <button className="btn" disabled={!!running} onClick={exportVerticalForYoutube}><Wand2 size={14} />Экспортировать в 9:16</button>}
            {youtube && <div className="small youtube-review-channel">Канал публикации: <b>{youtube.channel?.title ?? youtube.target_channel ?? "—"}</b></div>}
          </div>
          <div className="col youtube-review-form" style={{ gap: 10 }}>
            <div className="youtube-review-intro"><b>Метаданные подготовлены автоматически</b><span>Проверьте текст и само видео перед загрузкой.</span></div>
            <Field label="Название"><input maxLength={100} value={youtubeReview.title} onChange={(e) => setYoutubeReview({ ...youtubeReview, title: e.target.value })} /></Field>
            <Field label="Краткое описание"><textarea rows={4} maxLength={5000} value={youtubeReview.description} onChange={(e) => setYoutubeReview({ ...youtubeReview, description: e.target.value })} style={{ resize: "vertical" }} /></Field>
            <Field label="Теги" hint="Разделяйте запятыми"><input value={youtubeReview.tags} onChange={(e) => setYoutubeReview({ ...youtubeReview, tags: e.target.value })} /></Field>
            <Field label="Хэштеги" hint="Только несколько подходящих"><input value={youtubeReview.hashtags} onChange={(e) => setYoutubeReview({ ...youtubeReview, hashtags: e.target.value })} /></Field>
            <Field label="Доступ"><select value={youtubeReview.privacyStatus} onChange={(e) => setYoutubeReview({ ...youtubeReview, privacyStatus: e.target.value as YoutubeReview["privacyStatus"] })}>
              <option value="private">Приватное — только я</option><option value="unlisted">Доступ по ссылке</option><option value="public">Открытый доступ</option>
            </select><span className="youtube-review-copyright">Для проверки перед публичной публикацией оставьте видео приватным: после загрузки откройте «Проверить авторские права в YouTube Studio».</span></Field>
            <label className="row small youtube-review-kids"><input type="checkbox" checked={youtubeReview.madeForKids} onChange={(e) => setYoutubeReview({ ...youtubeReview, madeForKids: e.target.checked })} />Это видео создано специально для детей</label>
            <div className="small youtube-review-note">Обычное исламское напоминание не считается детским автоматически. Выберите этот параметр только если видео действительно адресовано детям.</div>
            {running && job && <div className="small youtube-review-note">{job.message} · {Math.round(job.progress * 100)}%</div>}
            <button className="btn primary" disabled={!!running || !youtubeReview.title.trim() || !youtubeVideoInfo || youtubeVideoInfo.height < youtubeVideoInfo.width || youtubeVideoInfo.duration > 180}
              onClick={uploadYoutube}><Upload size={15} />{running ? "Загрузка…" : `Загрузить · ${youtubeReview.privacyStatus === "public" ? "открытый доступ" : youtubeReview.privacyStatus === "unlisted" ? "по ссылке" : "приватно"}`}</button>
            <div className="small youtube-review-note">Будет загружен именно этот файл с указанными метаданными. Точные дубликаты блокируются.</div>
          </div>
        </div>
        </div>
      </Modal>}
      {youtubeBatch && <Modal title="Пакетная публикация · проверка Shorts" onClose={() => { if (!batchPublishing) setYoutubeBatch(null); }}>
        <div className="youtube-review" style={{ width: "min(1240px, 90vw)" }}>
          <div className="youtube-batch-intro col" style={{ gap: 5, marginBottom: 12 }}>
            <b>Канал: {youtube?.channel?.title ?? youtube?.target_channel ?? "—"} · подготовлено видео: {youtubeBatch.length}</b>
            <span className="small">Проверьте соответствие каждого файла его тексту. Загрузка начнётся только после кнопки ниже. По умолчанию все видео загружаются приватно.</span>
          </div>
          <div className="youtube-batch-list">
            {youtubeBatch.map((item) => {
              const status = batchResults[item.clip.id];
              return <div className="youtube-batch-item" key={item.clip.id}>
                <div className="col" style={{ gap: 8 }}>
                  <video src={fileUrl(item.file)} controls playsInline preload="metadata" />
                  <div className="small youtube-review-file">{item.file.split("/").pop()}</div>
                  <div className={`small youtube-review-format ${item.info.height >= item.info.width && item.info.duration <= 180 ? "ok" : "warning"}`}>
                    {item.info.width}×{item.info.height} · {Math.round(item.info.duration)} с
                  </div>
                  <div className="small muted">Фрагмент {mmss(item.clip.start)}–{mmss(item.clip.end)} · {item.clip.text.slice(0, 260)}{item.clip.text.length > 260 ? "…" : ""}</div>
                  {status && <div className={`notice ${status.ok ? "ok" : "err"}`}>
                    {status.message}{status.url && <> · <a href={status.url} target="_blank" rel="noreferrer">Открыть</a></>}
                  </div>}
                  {!batchPublishing && !status?.ok && <button className="btn sm danger" onClick={() => setYoutubeBatch((all) => all?.filter((x) => x.clip.id !== item.clip.id) ?? null)}>Убрать из набора</button>}
                </div>
                <div className="col youtube-review-form" style={{ gap: 9 }}>
                  <div className="youtube-review-intro"><b>{item.clip.label || "Клип"}</b><span>Поля относятся только к этому видео.</span></div>
                  <Field label="Название"><input maxLength={100} value={item.title} disabled={!!status?.ok || batchPublishing}
                    onChange={(e) => updateBatchItem(item.clip.id, { title: e.target.value })} /></Field>
                  <Field label="Описание"><textarea rows={3} maxLength={5000} value={item.description} disabled={!!status?.ok || batchPublishing}
                    onChange={(e) => updateBatchItem(item.clip.id, { description: e.target.value })} /></Field>
                  <Field label="Теги"><input value={item.tags} disabled={!!status?.ok || batchPublishing}
                    onChange={(e) => updateBatchItem(item.clip.id, { tags: e.target.value })} /></Field>
                  <Field label="Хэштеги"><input value={item.hashtags} disabled={!!status?.ok || batchPublishing}
                    onChange={(e) => updateBatchItem(item.clip.id, { hashtags: e.target.value })} /></Field>
                  <div className="grid cols-2">
                    <Field label="Видимость"><select value={item.privacyStatus} disabled={!!status?.ok || batchPublishing}
                      onChange={(e) => updateBatchItem(item.clip.id, { privacyStatus: e.target.value as YoutubeReview["privacyStatus"] })}>
                      <option value="private">Приватное</option><option value="unlisted">По ссылке</option><option value="public">Открытое</option>
                    </select></Field>
                    <label className="row small youtube-review-kids"><input type="checkbox" checked={item.madeForKids} disabled={!!status?.ok || batchPublishing}
                      onChange={(e) => updateBatchItem(item.clip.id, { madeForKids: e.target.checked })} />Создано специально для детей</label>
                  </div>
                </div>
              </div>;
            })}
          </div>
          {youtubeBatch.length > 0 && <div className="row wrap" style={{ justifyContent: "space-between", marginTop: 12 }}>
            <div className="youtube-batch-progress">{batchPublishing ? `Загрузка ${batchProgress} из ${youtubeBatch.filter((x) => !batchResults[x.clip.id]?.ok).length}…` : "Дубликаты проверяются LocalAI перед отправкой."}</div>
            <button className="btn primary" disabled={batchPublishing || !youtubeBatch.some((x) => !batchResults[x.clip.id]?.ok && x.title.trim())}
              onClick={uploadYoutubeBatch}><Upload size={15} />{batchPublishing ? "Загрузка…" : `Загрузить оставшиеся · ${youtubeBatch.filter((x) => !batchResults[x.clip.id]?.ok).length}`}</button>
          </div>}
          <div className="small youtube-review-note" style={{ marginTop: 10 }}>
            Обычное исламское видео не отмечается как детское автоматически. Выберите «создано специально для детей» только по фактической аудитории. Для проверки авторских прав сначала используйте приватную видимость.
          </div>
        </div>
      </Modal>}
      {editing && proj && <ClipEditor proj={proj} clip={editing} onClose={() => setEditing(null)}
        onExport={(edits) => { setEditing(null); exportClip(editing, edits); }}
        onEnrich={(start, end) => enrichClip(editing, start, end)} onSaveSocial={(social) => saveSocial(editing, social)} busy={!!running} />}
    </div>
  );
}

function SegmentPlayer({ src, start, end, onTime, playerRef }: { src: string; start: number; end: number; onTime?: (t: number) => void; playerRef?: React.RefObject<HTMLVideoElement | null> }) {
  const own = useRef<HTMLVideoElement>(null);
  const ref = playerRef ?? own;
  useEffect(() => { if (ref.current) ref.current.currentTime = start; }, [start]);
  return <video ref={ref} src={src} controls style={{ width: "100%", maxHeight: "60vh", borderRadius: 10 }}
    onTimeUpdate={(e) => { const t = e.currentTarget.currentTime; onTime?.(t); if (t > end) { e.currentTarget.pause(); e.currentTarget.currentTime = start; } }} />;
}

function ClipEditor({ proj, clip, onClose, onExport, onEnrich, onSaveSocial, busy }: {
  proj: Project; clip: Clip; onClose: () => void; onExport: (e: Record<string, any>) => void;
  onEnrich: (start: number, end: number) => void; onSaveSocial: (social: SocialMeta) => void; busy: boolean;
}) {
  const player = useRef<HTMLVideoElement>(null);
  const [start, setStart] = useState(clip.start);
  const [end, setEnd] = useState(clip.end);
  const [cuts, setCuts] = useState<[number, number][]>(clip.cuts ?? []);
  const [mark, setMark] = useState<number | null>(null);
  const [t, setT] = useState(clip.start);
  const [words, setWords] = useState<Word[]>(clip.words);
  const [title, setTitle] = useState(clip.title ?? clip.titles[0] ?? "");
  const [social, setSocial] = useState<SocialMeta>(clip.social ?? blankSocial());
  const [analysis, setAnalysis] = useState<ContentReview | undefined>(clip.analysis);
  const [s, setS] = useState({ aspect: "9:16", export: "tiktok", fps: 30, style: "creator", position: "bottom", words_per_line: 3,
    highlight: true, box: false, outline: 4, font_size: 0.066, zoom: 1.0, volume: 1.0, fade: 0.3, silence: "light", captions: true,
    music: null as string | null, music_volume: 0.15 });
  const [broll, setBroll] = useState<{ path: string; start: number; end: number }[]>([]);
  const lo = Math.max(0, clip.start - 20), hi = Math.min(proj.info.duration, clip.end + 20);
  const pct = (x: number) => `${((x - lo) / (hi - lo)) * 100}%`;
  const rel = (x: number) => +(x - clip.start).toFixed(3);
  const up = (k: string, v: any) => setS((p) => ({ ...p, [k]: v }));

  const buildEdits = () => {
    // words are stored relative to the ORIGINAL clip start; shift them if the start moved
    const shift = start - clip.start;
    const w = words.map((x) => ({ ...x, start: +(x.start - shift).toFixed(3), end: +(x.end - shift).toFixed(3) }))
      .filter((x) => x.start >= 0 && x.end <= end - start + 0.05);
    return { ...s, start, end, title, words: w, cuts: cuts.map(([a, b]) => [+(a - start).toFixed(3), +(b - start).toFixed(3)]), broll };
  };
  const exp = () => onExport(buildEdits());
  const [pv, setPv] = useState<string | null>(clip.preview ?? null);
  const [pvBusy, setPvBusy] = useState(false);
  useEffect(() => {
    setSocial(clip.social ?? blankSocial());
    setAnalysis(clip.analysis);
  }, [clip.id, clip.social, clip.analysis]);
  const updatePreview = async () => {
    setPvBusy(true);
    try {
      const j = await api.post<any>("/api/tools/clips_preview", { project: proj.id, clip: clip.id, edits: buildEdits() });
      const done = await waitJob(j.id, () => {});
      if (done.status === "done" && (done.result as any)?.preview) setPv((done.result as any).preview + `?t=${Date.now()}`);
    } finally { setPvBusy(false); }
  };

  return (
    <Modal title={`Edit clip · ${clip.label}`} onClose={onClose} actions={<>
      <button className="btn sm" disabled={pvBusy} onClick={updatePreview}><Film size={14} className={pvBusy ? "spin" : ""} />{pvBusy ? "Rendering…" : "Update 9:16 preview"}</button>
      <button className="btn primary sm" onClick={exp}><Download size={14} />Export</button></>}>
      <div className="grid" style={{ gridTemplateColumns: "220px minmax(0,1.3fr) minmax(320px,1fr)", gap: 18, width: "min(1400px, 92vw)" }}>
        <div className="col">
          <div className="small muted">9:16 preview</div>
          {pv ? <video src={fileUrl(pv.split("?")[0]) + (pv.includes("?") ? "?" + pv.split("?")[1] : "")} key={pv} controls playsInline style={{ width: 220, aspectRatio: "9/16", borderRadius: 12, background: "#000" }} />
            : <div style={{ width: 220, aspectRatio: "9/16", borderRadius: 12, background: "var(--panel-2)" }} />}
          <div className="small faint">Shows cropping, captions and title as they will be exported.</div>
        </div>
        <div className="col">
          <SegmentPlayer src={fileUrl(proj.video_rel)} start={start} end={end} onTime={setT} playerRef={player} />
          <div className="timeline" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); const x = lo + ((e.clientX - r.left) / r.width) * (hi - lo); if (player.current) player.current.currentTime = x; }}>
            <div className="sel" style={{ left: pct(start), width: `calc(${pct(end)} - ${pct(start)})` }} />
            {cuts.map(([a, b], i) => <div key={i} className="cut" style={{ left: pct(a), width: `calc(${pct(b)} - ${pct(a)})` }} />)}
            <div className="play" style={{ left: pct(t) }} />
          </div>
          <div className="row wrap small">
            <button className="btn sm" onClick={() => setStart(+t.toFixed(2))}>Set start</button>
            <button className="btn sm" onClick={() => setEnd(+t.toFixed(2))}>Set end</button>
            <button className="btn sm" onClick={() => setStart((x) => +(x - 0.5).toFixed(2))}>Start −0.5 s</button>
            <button className="btn sm" onClick={() => setStart((x) => +(x + 0.5).toFixed(2))}>Start +0.5 s</button>
            <button className="btn sm" onClick={() => setEnd((x) => +(x - 0.5).toFixed(2))}>End −0.5 s</button>
            <button className="btn sm" onClick={() => setEnd((x) => +(x + 0.5).toFixed(2))}>End +0.5 s</button>
            {mark === null ? <button className="btn sm" onClick={() => setMark(t)}><Scissors size={13} />Mark in (split)</button>
              : <button className="btn sm danger" onClick={() => { setCuts([...cuts, [Math.min(mark, t), Math.max(mark, t)]]); setMark(null); }}><Trash2 size={13} />Delete section to here</button>}
            {cuts.length > 0 && <button className="btn sm ghost" onClick={() => setCuts([])}>Clear deleted sections</button>}
            <span className="muted">{fmt(start - lo)} → {fmt(end - lo)} · {(end - start - cuts.reduce((a, [x, y]) => a + y - x, 0)).toFixed(1)} s</span>
          </div>
          <Field label="Captions (click a word to edit)">
            <div style={{ maxHeight: 150, overflow: "auto" }}>{words.map((w, i) => (
              <span key={i} className="wordchip" contentEditable suppressContentEditableWarning
                onBlur={(e) => { const v = e.currentTarget.textContent ?? ""; setWords(words.map((x, j) => (j === i ? { ...x, word: " " + v.trim() } : x))); }}>{w.word.trim()}</span>))}</div>
          </Field>
          <Field label="Hook title (shown at the top)">
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="(none)" />
            <div className="row wrap" style={{ gap: 5, marginTop: 6 }}>{clip.titles.map((x) => <button key={x} className="btn sm ghost" onClick={() => setTitle(x)}>{x}</button>)}
              <button className="btn sm ghost" onClick={() => setTitle("")}>No title</button></div>
          </Field>
        </div>
        <div className="col">
          <Field label="Format"><Seg value={s.aspect} onChange={(v) => up("aspect", v)} options={["9:16", "1:1", "4:5", "16:9"].map((v) => ({ v, label: v }))} /></Field>
          <div className="grid cols-2">
            <Field label="Export preset"><select value={s.export} onChange={(e) => up("export", e.target.value)}>
              <option value="tiktok">TikTok 1080×1920</option><option value="reels">Instagram Reels 1080×1920</option>
              <option value="shorts">YouTube Shorts 1080×1920</option><option value="youtube">YouTube 1920×1080</option></select></Field>
            <Field label="FPS"><select value={s.fps} onChange={(e) => up("fps", Number(e.target.value))}><option value={30}>30</option><option value={60}>60</option></select></Field>
            <Field label="Caption style"><select value={s.style} onChange={(e) => up("style", e.target.value)}>
              <option value="opus">Opus (bold, yellow highlight)</option><option value="minimal">Minimal</option><option value="bold">Bold</option><option value="creator">Creator</option><option value="podcast">Podcast</option></select></Field>
            <Field label="Layout"><select value={(s as any).layout} onChange={(e) => up("layout", e.target.value)}>
              <option value="auto">Auto (track faces, else fit + blur)</option><option value="crop">Crop (follow speaker)</option><option value="fit">Fit + blurred background</option></select></Field>
            <Field label="Position"><select value={s.position} onChange={(e) => up("position", e.target.value)}>
              <option value="bottom">Bottom</option><option value="middle">Middle</option><option value="top">Top</option></select></Field>
            <Field label="Silence removal"><select value={s.silence} onChange={(e) => up("silence", e.target.value)}>
              <option value="off">Off</option><option value="light">Light</option><option value="aggressive">Aggressive</option></select></Field>
            <Field label="Words per line"><input type="number" min={1} max={8} value={s.words_per_line} onChange={(e) => up("words_per_line", Number(e.target.value))} /></Field>
          </div>
          <div className="row wrap small">
            <label className="row"><input type="checkbox" checked={s.captions} onChange={(e) => up("captions", e.target.checked)} />Captions</label>
            <label className="row"><input type="checkbox" checked={s.highlight} onChange={(e) => up("highlight", e.target.checked)} />Highlight current word</label>
            <label className="row"><input type="checkbox" checked={s.box} onChange={(e) => up("box", e.target.checked)} />Background box</label>
          </div>
          <Slider label="Font size" value={s.font_size} min={0.035} max={0.09} step={0.002} onChange={(v) => up("font_size", v)} fmt={(v) => `${Math.round(v * 1920)} px`} />
          <Slider label="Outline" value={s.outline} min={0} max={8} step={1} onChange={(v) => up("outline", v)} />
          <Slider label="Zoom" value={s.zoom} min={1} max={1.6} step={0.05} onChange={(v) => up("zoom", v)} fmt={(v) => `${v.toFixed(2)}×`} />
          <Slider label="Volume" value={s.volume} min={0} max={2} step={0.05} onChange={(v) => up("volume", v)} fmt={(v) => `${Math.round(v * 100)}%`} />
          <Slider label="Fade in/out" value={s.fade} min={0} max={1.5} step={0.1} onChange={(v) => up("fade", v)} fmt={(v) => `${v.toFixed(1)} s`} />
          <Field label="Background music (your file)"><DropZone accept="audio/*" value={s.music} onChange={(p) => up("music", p)} label="Add music" kind="audio" /></Field>
          {s.music && <Slider label="Music volume" value={s.music_volume} min={0} max={0.6} step={0.01} onChange={(v) => up("music_volume", v)} fmt={(v) => `${Math.round(v * 100)}%`} />}
          <Field label="B-roll (your files, shown full-screen)">
            <div className="col">
              {broll.map((b, i) => (
                <div key={i} className="row small"><span className="grow ellipsis">{b.path.split("/").pop()}</span>
                  <input type="number" style={{ width: 70 }} value={b.start} onChange={(e) => setBroll(broll.map((x, j) => j === i ? { ...x, start: Number(e.target.value) } : x))} />
                  <input type="number" style={{ width: 70 }} value={b.end} onChange={(e) => setBroll(broll.map((x, j) => j === i ? { ...x, end: Number(e.target.value) } : x))} />
                  <button className="btn sm ghost icon" onClick={() => setBroll(broll.filter((_, j) => j !== i))}><X size={13} /></button></div>))}
              <DropZone accept="image/*,video/*" value={null} onChange={(p) => p && setBroll([...broll, { path: p, start: 2, end: 5 }])} label="Add B-roll image or video" kind="media" />
              <div className="small faint">Times are seconds from the clip start. Generated B-roll: make it in Image or Video first, then add it here.</div>
            </div>
          </Field>
          <div className="card col" style={{ padding: 12, gap: 8 }}>
            <div className="row wrap"><b className="grow">AI video review</b>
              <button className="btn sm" disabled={busy} onClick={() => onEnrich(start, end)}><Wand2 size={13} />{busy ? "Analyzing…" : "Analyze / improve"}</button></div>
            {analysis ? <>
              {analysis.content_summary && <div className="small">{analysis.content_summary}</div>}
              {analysis.visual_summary && <div className="small muted">{analysis.visual_summary}</div>}
              {!analysis.visual_review_available && <div className="small faint">Visual analysis unavailable; check the frames manually.</div>}
              <div className="small muted">Opening: {analysis.opening_note || "—"}</div>
              <div className="small muted">Ending: {analysis.ending_note || "—"}</div>
              <div className="small muted">Works on its own: {analysis.context_ok ? "Yes" : "Needs review"}</div>
              {analysis.subtitle_review && <div className="small muted">Subtitle check: {analysis.subtitle_review}</div>}
              {analysis.audio_metrics && <div className="small muted">Audio level · mean {analysis.audio_metrics.mean_db ?? "—"} dB · peak {analysis.audio_metrics.peak_db ?? "—"} dB</div>}
              {!!analysis.issues?.length && <div className="small">{analysis.issues.map((x, i) => <div key={i}>• {x}</div>)}</div>}
              {!!analysis.edit_suggestions?.length && <div className="small muted">{analysis.edit_suggestions.map((x, i) => <div key={i}>• {x}</div>)}</div>}
            </> : <div className="small faint">AI will compare the transcript with sampled frames and prepare a separate metadata set for each platform.</div>}
            {!!clip.visual_frames?.length && <div className="row wrap">{clip.visual_frames.map((f, i) =>
              <img key={i} src={fileUrl(f.path)} alt={`Frame at ${f.time}s`} title={`${f.time}s`} loading="lazy"
                style={{ width: 64, height: 64, objectFit: "cover", borderRadius: 7 }} />)}</div>}
          </div>
          <div className="card col" style={{ padding: 12, gap: 10 }}>
            <div className="row"><b className="grow">Post titles, descriptions and tags</b>
              <button className="btn sm" onClick={() => onSaveSocial(social)}><Check size={13} />Save metadata</button></div>
            <SocialMetadataPanel value={social} onChange={setSocial} compact />
          </div>
        </div>
      </div>
    </Modal>
  );
}
