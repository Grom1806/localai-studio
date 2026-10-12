import React, { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowUp, AudioLines, Captions, Check, Download, Eye, FilePlus, Film, Layers, Maximize2, MessageCircle, Music2, Palette, Pause, Play, Plus, RotateCcw, Save, Scissors, Send, Settings2, SkipBack, SkipForward, SlidersHorizontal, Sparkles, Trash2, Upload, Wand2 } from "lucide-react";
import { DropZone, Field, NumberField, Seg, Slider, useJob } from "../components/common";
import { EmptyStage, ResultView, RunningView } from "../components/tools";
import { api, fileUrl } from "../api";
import { makeSpeechEdit } from "../lib/autoMontage";
import { sceneMarkersForTimeline } from "../lib/sceneMarkers";
import { timelineSeekTargetAt } from "../lib/timelineNavigation";
import { previewTransitionAt, transitionAudioGains, transitionLayerStyle } from "../lib/transitionPreview";
import { SocialMetadataPanel } from "../components/SocialMetadataPanel";
import { useNav } from "../App";

type Source = { id: string; path: string; duration: number; width?: number; height?: number };
type Transition = { type: "dissolve" | "fade" | "fadeblack" | "wipeleft" | "wiperight" | "slideright" | "slideleft" | "circleopen" | "smoothleft"; duration: number };
const TRANSITION_LABELS: Record<Transition["type"], string> = {
  dissolve: "Растворение", fade: "Плавное смешивание", fadeblack: "Затемнение через чёрный",
  wipeleft: "Шторка влево", wiperight: "Шторка вправо", slideright: "Сдвиг вправо",
  slideleft: "Сдвиг влево", circleopen: "Круг", smoothleft: "Плавный сдвиг",
};
type Segment = { id: string; source: string; start: number; end: number; transitionAfter?: Transition };
type Grade = { brightness: number; contrast: number; saturation: number; temperature: number; tint: number };
type ChatMessage = { role: "assistant" | "user"; text: string };
type AgentProgress = { phase: string; percent: number; step: number; total: number; error?: boolean };
type CaptionWord = { start: number; end: number; word: string };
type SourceCaptions = Record<string, { language: string | null; words: CaptionWord[] }>;
type SubjectTrackPoint = { t: number; x: number };
type SubjectTracks = Record<string, SubjectTrackPoint[]>;
type AudioTrackName = "original" | "music" | "sfx";
type AudioTrackFlags = Record<AudioTrackName, boolean>;
type SfxClip = { id: string; path: string; at: number; duration: number; volume: number; prompt: string };
type RelinkTarget = { kind: "video"; id: string } | { kind: "music" } | { kind: "sfx"; id: string };
type BrollClip = { id: string; source: string; start: number; end: number; at: number; scale: number };
type TimelineTrimDrag = { pointerId: number; segmentId: string; edge: "start" | "end"; originX: number; pixelsPerSecond: number; initial: Segment; snapshot: EditSnapshot; finalStart: number; finalEnd: number };
type EditSnapshot = { sources: Source[]; segments: Segment[]; brollClips: BrollClip[]; selected: string; sourceCaptions: SourceCaptions; grade: Grade; aspect: string; fps: string;
  volume: number; musicVolume: number; duckMusic: boolean; audioMutes: AudioTrackFlags; audioSolos: AudioTrackFlags; fade: number; normalizeAudio: boolean; subjectFollow: boolean; music: string | null; overlay: string; overlayPosition: string; captionStyle: string; captionAnimation: string; captionPosition: string;
  captionHighlight: boolean; captionTextColor: string; captionHighlightColor: string; captionWordsPerLine: number; sfxClips: SfxClip[] };
const defaultGrade: Grade = { brightness: 0, contrast: 1, saturation: 1, temperature: 0, tint: 0 };
const defaultAudioTrackFlags: AudioTrackFlags = { original: false, music: false, sfx: false };
const readAudioTrackFlags = (value: any): AudioTrackFlags => ({
  original: value?.original === true, music: value?.music === true, sfx: value?.sfx === true,
});
const PROJECT_STORAGE_KEY = "localai.video-editor.project.v2";
const LEGACY_PROJECT_STORAGE_KEY = "localai.video-editor.project.v1";
type ProjectSummary = { id: string; name: string; updated_at: string | null; source_count: number };
const freshProjectId = () => typeof crypto !== "undefined" && "randomUUID" in crypto
  ? crypto.randomUUID() : `${Date.now()}_${Math.random().toString(36).slice(2)}`;
const key = () => `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
const fmt = (n: number) => `${Math.floor(n / 60)}:${(n % 60).toFixed(1).padStart(4, "0")}`;
const agentOperationLabel = (op: any) => {
  const labels: Record<string, string> = {
  analyze: "Анализирую видео и сцены", captions: "Готовлю субтитры", sfx: "Создаю звуковой эффект",
  face_blur: "Обнаруживаю и размываю лица", remove_pauses: "Нахожу длинные паузы",
  duck_music: "Настраиваю музыку под речь",
  upscale: "Увеличиваю разрешение", interpolate: "Повышаю частоту кадров",
  scene: "Готовлю AI‑правку сцены", transition: "Настраиваю переход", split: "Разрезаю клип",
  trim: "Подрезаю фрагмент", color: "Настраиваю цвет", volume: "Настраиваю звук", format: "Меняю формат кадра",
  };
  return labels[String(op?.type || "")] || "Применяю монтажные правки";
};
const effectiveTransition = (items: Segment[], index: number) => {
  const current = items[index], next = items[index + 1];
  if (!current?.transitionAfter || !next) return 0;
  const requested = current.transitionAfter.duration;
  const length = Math.min(current.end - current.start, next.end - next.start);
  const actual = Math.min(requested, length * .45);
  return actual >= .15 ? actual : 0;
};
const timelineLayout = (items: Segment[]) => {
  const starts: number[] = [];
  let duration = 0;
  items.forEach((segment, index) => {
    starts.push(duration);
    duration += Math.max(0, segment.end - segment.start) - effectiveTransition(items, index);
  });
  return { starts, duration: Math.max(0, duration) };
};
const subjectFocusAt = (points: SubjectTrackPoint[] | undefined, at: number) => {
  if (!points?.length) return null;
  if (at <= points[0].t) return Math.max(0, Math.min(1, points[0].x));
  let low = 0, high = points.length - 1;
  while (low + 1 < high) {
    const middle = (low + high) >> 1;
    if (points[middle].t <= at) low = middle; else high = middle;
  }
  const left = points[low], right = points[high];
  if (at >= right.t) return Math.max(0, Math.min(1, right.x));
  const ratio = Math.max(0, Math.min(1, (at - left.t) / Math.max(.001, right.t - left.t)));
  return Math.max(0, Math.min(1, left.x + (right.x - left.x) * ratio));
};
const cleanSubjectTracks = (input: any): SubjectTracks => {
  if (!input || typeof input !== "object" || Array.isArray(input)) return {};
  return Object.fromEntries(Object.entries(input).flatMap(([id, raw]) => {
    if (!Array.isArray(raw)) return [];
    const points = raw.slice(0, 10000).flatMap((point: any) => {
      const t = Number(point?.t), x = Number(point?.x);
      return Number.isFinite(t) && t >= 0 && Number.isFinite(x) ? [{ t, x: Math.max(0, Math.min(1, x)) }] : [];
    }).sort((a, b) => a.t - b.t);
    return points.length ? [[id, points] as const] : [];
  }));
};
export default function VideoEditPage() {
  const { takeHandoff, go } = useNav();
  const { job, run, cancel, setJob } = useJob();
  const [panel, setPanel] = useState<"edit" | "color" | "captions" | "audio" | "ai" | "analyze" | "export">("ai");
  const [projectReady, setProjectReady] = useState(false);
  const [projectSaveState, setProjectSaveState] = useState<"saving" | "saved" | "local-only" | "server-only" | "error">("saved");
  const [lastProjectSave, setLastProjectSave] = useState<number | null>(null);
  const [projectId, setProjectId] = useState(freshProjectId);
  const [projectName, setProjectName] = useState("Новый проект");
  const [projectLibrary, setProjectLibrary] = useState<ProjectSummary[]>([]);
  const [missingMedia, setMissingMedia] = useState<string[]>([]);
  const projectFileInput = useRef<HTMLInputElement>(null);
  const relinkMediaInput = useRef<HTMLInputElement>(null);
  const relinkTarget = useRef<RelinkTarget | null>(null);
  const [video, setVideo] = useState<string | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [brollClips, setBrollClips] = useState<BrollClip[]>([]);
  const [selected, setSelected] = useState("");
  const [playhead, setPlayhead] = useState(0);
  const player = useRef<HTMLVideoElement>(null);
  const pendingTimelineSeek = useRef<{ sourceId: string; time: number } | null>(null);
  const transitionPlayer = useRef<HTMLVideoElement>(null);
  const timelineScrubPointer = useRef<number | null>(null);
  const musicPreviewPlayer = useRef<HTMLAudioElement>(null);
  const sfxPreviewPlayers = useRef<Map<string, HTMLAudioElement>>(new Map());
  const sourcesRef = useRef<Source[]>([]);
  const [grade, setGrade] = useState<Grade>(defaultGrade);
  const [command, setCommand] = useState("");
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [renderInProgress, setRenderInProgress] = useState(false);
  const [agentProgress, setAgentProgress] = useState<AgentProgress | null>(null);
  const [assistantNote, setAssistantNote] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([{ role: "assistant", text: "Я AI‑монтажёр этого проекта. Анализирую материал и применяю локальные инструменты к текущему таймлайну: монтаж, цвет, субтитры, звук, размытие лиц и улучшение качества. Новые Shorts создаются в отдельном разделе «Клипы»." }]);
  const [undoStack, setUndoStack] = useState<EditSnapshot[]>([]);
  const [redoStack, setRedoStack] = useState<EditSnapshot[]>([]);
  const chatScroll = useRef<HTMLDivElement>(null);
  const [aspect, setAspect] = useState("source");
  const [fps, setFps] = useState("source");
  const [volume, setVolume] = useState(1);
  const [audioMutes, setAudioMutes] = useState<AudioTrackFlags>({ ...defaultAudioTrackFlags });
  const [audioSolos, setAudioSolos] = useState<AudioTrackFlags>({ ...defaultAudioTrackFlags });
  const [fade, setFade] = useState(0.25);
  const [normalizeAudio, setNormalizeAudio] = useState(false);
  const [subjectFollow, setSubjectFollow] = useState(true);
  const [subjectTracks, setSubjectTracks] = useState<SubjectTracks>({});
  const [overlay, setOverlay] = useState("");
  const [overlayPosition, setOverlayPosition] = useState("top");
  const [showSafeGuides, setShowSafeGuides] = useState(false);
  const [music, setMusic] = useState<string | null>(null);
  const [musicVolume, setMusicVolume] = useState(0.12);
  const [duckMusic, setDuckMusic] = useState(true);
  const [sourceCaptions, setSourceCaptions] = useState<SourceCaptions>({});
  const [captionWordQuery, setCaptionWordQuery] = useState("");
  const captionWordEdit = useRef<{ sourceId: string; index: number; initialText: string; undoPushed: boolean } | null>(null);
  const [captionTimingSelection, setCaptionTimingSelection] = useState<{ sourceId: string; index: number } | null>(null);
  const [captionTimingDraft, setCaptionTimingDraft] = useState<{ sourceId: string; index: number; edge: "start" | "end"; text: string } | null>(null);
  const [captionStyle, setCaptionStyle] = useState("creator");
  const [captionAnimation, setCaptionAnimation] = useState("pop");
  const [captionPosition, setCaptionPosition] = useState("bottom");
  const [captionWordsPerLine, setCaptionWordsPerLine] = useState(3);
  const [captionHighlight, setCaptionHighlight] = useState(true);
  const [captionTextColor, setCaptionTextColor] = useState("FFFFFF");
  const [captionHighlightColor, setCaptionHighlightColor] = useState("FFD84D");
  const [sfxPrompt, setSfxPrompt] = useState("");
  const [sfxDuration, setSfxDuration] = useState(4);
  const [sfxClips, setSfxClips] = useState<SfxClip[]>([]);
  const [editPrompt, setEditPrompt] = useState("");
  const [ref, setRef] = useState<string | null>(null);
  const [task, setTask] = useState("edit");
  const [preset, setPreset] = useState("balanced");
  const [editModel, setEditModel] = useState<"vace" | "bernini">("vace");
  const [installedModels, setInstalledModels] = useState<Record<string, boolean>>({});
  const [editStart, setEditStart] = useState(0);
  const [editDuration, setEditDuration] = useState(5);
  const [seed, setSeed] = useState<number | "">(-1);
  const [result, setResult] = useState<string | null>(null);
  const [resultQa, setResultQa] = useState<any | null>(null);
  const [maskPickMode, setMaskPickMode] = useState(false);
  const [maskPoint, setMaskPoint] = useState<{ x: number; y: number } | null>(null);
  const [maskSeedTime, setMaskSeedTime] = useState(0);
  const [maskMode, setMaskMode] = useState<"gaussian" | "pixelate">("gaussian");
  const [analysis, setAnalysis] = useState<any | null>(null);
  const [analysisSourcePath, setAnalysisSourcePath] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [timelineZoom, setTimelineZoom] = useState(1);
  const [draggingSegment, setDraggingSegment] = useState<string | null>(null);
  const timelineTrimDrag = useRef<TimelineTrimDrag | null>(null);
  const autoMontagePending = useRef(new Set<string>());
  const autoMontageRunning = useRef(new Set<string>());
  const timelineSegmentsRef = useRef(segments);
  const latestSnapshotRef = useRef<EditSnapshot | null>(null);
  timelineSegmentsRef.current = segments;
  latestSnapshotRef.current = {
    sources: sources.map((source) => ({ ...source })),
    segments: segments.map((segment) => ({ ...segment, transitionAfter: segment.transitionAfter ? { ...segment.transitionAfter } : undefined })),
    brollClips: brollClips.map((clip) => ({ ...clip })), selected,
    sourceCaptions: { ...sourceCaptions }, grade: { ...grade }, aspect, fps, volume, musicVolume, duckMusic, audioMutes: { ...audioMutes }, audioSolos: { ...audioSolos }, fade, normalizeAudio, subjectFollow, music, overlay, overlayPosition,
    captionStyle, captionAnimation, captionPosition, captionHighlight, captionTextColor, captionHighlightColor,
    captionWordsPerLine, sfxClips: sfxClips.map((clip) => ({ ...clip })),
  };

  const buildProjectDocument = () => ({
    version: 2, id: projectId, name: projectName.trim() || "Новый проект", updatedAt: new Date().toISOString(),
    media: [
      ...sources.map((source) => ({ kind: "video", id: source.id, path: source.path, name: source.path.split(/[\\/]/).pop(), duration: source.duration, width: source.width, height: source.height })),
      ...(music ? [{ kind: "music", path: music, name: music.split(/[\\/]/).pop() }] : []),
      ...sfxClips.map((clip) => ({ kind: "sfx", id: clip.id, path: clip.path, name: clip.path.split(/[\\/]/).pop() })),
    ],
    state: { video, sources, segments, brollClips, selected, music, grade, aspect, fps, volume, musicVolume, duckMusic, audioMutes, audioSolos, fade, subjectFollow,
      normalizeAudio, overlay, overlayPosition, sourceCaptions, captionStyle, captionAnimation, captionPosition, captionWordsPerLine,
      captionHighlight, captionTextColor, captionHighlightColor, sfxClips, analysis, analysisSourcePath, chatMessages },
    history: { undoStack, redoStack },
  });
  const restoreProjectDocument = (input: any, idOverride?: string) => {
    const document = input?.document || input;
    const saved = document?.state;
    if (document?.version !== 2 || !saved || !Array.isArray(saved.sources) || !Array.isArray(saved.segments)) {
      throw new Error("Файл не похож на проект LocalAI Studio версии 2.");
    }
    const savedSources = saved.sources.filter((source: any) => source && typeof source.id === "string" && typeof source.path === "string");
    sourcesRef.current = savedSources;
    setSources(savedSources);
    setSubjectTracks({});
    setSegments(saved.segments.filter((segment: any) => segment && typeof segment.id === "string" && typeof segment.source === "string"));
    setBrollClips(Array.isArray(saved.brollClips) ? saved.brollClips : []);
    setSelected(typeof saved.selected === "string" ? saved.selected : "");
    setVideo(typeof saved.video === "string" ? saved.video : null);
    setMusic(typeof saved.music === "string" ? saved.music : null);
    setGrade({ ...defaultGrade, ...(saved.grade || {}) });
    setAspect(typeof saved.aspect === "string" ? saved.aspect : "source");
    setFps(typeof saved.fps === "string" ? saved.fps : "source");
    setVolume(Number.isFinite(saved.volume) ? saved.volume : 1);
    setAudioMutes(readAudioTrackFlags(saved.audioMutes));
    setAudioSolos(readAudioTrackFlags(saved.audioSolos));
    setMusicVolume(Number.isFinite(saved.musicVolume) ? saved.musicVolume : .12);
    setDuckMusic(saved.duckMusic !== false);
    setFade(Number.isFinite(saved.fade) ? saved.fade : .25);
    setNormalizeAudio(saved.normalizeAudio === true);
    setSubjectFollow(saved.subjectFollow !== false);
    setOverlay(typeof saved.overlay === "string" ? saved.overlay : "");
    setOverlayPosition(["top", "middle", "bottom"].includes(saved.overlayPosition) ? saved.overlayPosition : "top");
    setSourceCaptions(saved.sourceCaptions && typeof saved.sourceCaptions === "object" ? saved.sourceCaptions : {});
    setCaptionTimingSelection(null); setCaptionTimingDraft(null); captionWordEdit.current = null;
    setCaptionStyle(typeof saved.captionStyle === "string" ? saved.captionStyle : "creator");
    setCaptionAnimation(typeof saved.captionAnimation === "string" ? saved.captionAnimation : "pop");
    setCaptionPosition(typeof saved.captionPosition === "string" ? saved.captionPosition : "bottom");
    setCaptionWordsPerLine(Number.isFinite(saved.captionWordsPerLine) ? saved.captionWordsPerLine : 3);
    setCaptionHighlight(saved.captionHighlight !== false);
    setCaptionTextColor(typeof saved.captionTextColor === "string" ? saved.captionTextColor : "FFFFFF");
    setCaptionHighlightColor(typeof saved.captionHighlightColor === "string" ? saved.captionHighlightColor : "FFD84D");
    setSfxClips(Array.isArray(saved.sfxClips) ? saved.sfxClips : []);
    setAnalysis(saved.analysis && typeof saved.analysis === "object" ? saved.analysis : null);
    setAnalysisSourcePath(typeof saved.analysisSourcePath === "string" ? saved.analysisSourcePath : null);
    setChatMessages(Array.isArray(saved.chatMessages) ? saved.chatMessages : [{ role: "assistant", text: "Проект открыт. Продолжим монтаж на сохранённом таймлайне." }]);
    setUndoStack(Array.isArray(document.history?.undoStack) ? document.history.undoStack.slice(-30) : []);
    setRedoStack(Array.isArray(document.history?.redoStack) ? document.history.redoStack.slice(-30) : []);
    setProjectId(idOverride || document.id || freshProjectId());
    const sourceName = String(savedSources[0]?.path || "").split(/[\\/]/).pop()?.replace(/\.[^.]+$/, "");
    const storedName = String(input?.name || document.name || "").trim();
    setProjectName(String(!storedName || storedName === "Восстановленный проект" ? sourceName || "Новый проект" : storedName).slice(0, 120));
    setLastProjectSave(Date.parse(String(document.updatedAt || input?.updated_at || "")) || Date.now());
  };
  useEffect(() => {
    let restored = false;
    try {
      const raw = localStorage.getItem(PROJECT_STORAGE_KEY) || localStorage.getItem(LEGACY_PROJECT_STORAGE_KEY);
      const saved = raw ? JSON.parse(raw) : null;
      if (saved?.version === 2 && saved.state) {
        restoreProjectDocument(saved); restored = true;
      } else if (saved?.version === 1 && Array.isArray(saved.sources) && Array.isArray(saved.segments)) {
        restoreProjectDocument({ version: 2, id: freshProjectId(), name: "Восстановленный проект", updatedAt: new Date(saved.savedAt || Date.now()).toISOString(), state: saved, history: {} });
        restored = true;
      }
    } catch {
      setProjectSaveState("error");
    }
    const handoff = takeHandoff("video/edit");
    if (handoff?.video) setVideo(handoff.video);
    if (restored) setAssistantNote("Проект восстановлен из локального автосохранения.");
    setProjectReady(true);
  }, []);
  useEffect(() => {
    if (!projectReady) return;
    api.get<ProjectSummary[]>("/api/editor/projects").then(setProjectLibrary).catch(() => {});
  }, [projectReady]);
  useEffect(() => {
    if (!projectReady) return;
    setProjectSaveState("saving");
    const timer = window.setTimeout(() => {
      const document = buildProjectDocument();
      const savedAt = Date.now();
      let browserCopySaved = false;
      try {
        localStorage.setItem(PROJECT_STORAGE_KEY, JSON.stringify(document));
        browserCopySaved = true;
      } catch {
        // The named on-disk project remains the durable copy when browser storage is full.
      }
      api.put<ProjectSummary>(`/api/editor/projects/${encodeURIComponent(projectId)}`, { name: document.name, document })
        .then((summary) => {
          setProjectLibrary((all) => [summary, ...all.filter((item) => item.id !== summary.id)]
            .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || ""))));
          setLastProjectSave(savedAt); setProjectSaveState(browserCopySaved ? "saved" : "server-only");
        }).catch(() => {
          setLastProjectSave(browserCopySaved ? savedAt : null);
          setProjectSaveState(browserCopySaved ? "local-only" : "error");
        });
    }, 850);
    return () => window.clearTimeout(timer);
  }, [projectReady, projectId, projectName, video, sources, segments, brollClips, selected, music, grade, aspect, fps,
    volume, musicVolume, duckMusic, audioMutes, audioSolos, fade, normalizeAudio, subjectFollow, overlay, overlayPosition, sourceCaptions, captionStyle, captionAnimation,
    captionPosition, captionWordsPerLine, captionHighlight, captionTextColor, captionHighlightColor, sfxClips,
    analysis, analysisSourcePath, chatMessages, undoStack, redoStack]);
  useEffect(() => {
    let active = true;
    const refresh = () => api.get<{ id: string; installed: boolean }[]>("/api/models")
      .then((models) => { if (active) setInstalledModels(Object.fromEntries(models.map((m) => [m.id, m.installed]))); }).catch(() => {});
    refresh(); const timer = window.setInterval(refresh, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    const colors: Record<string, [string, string]> = {
      creator: ["FFFFFF", "34D399"], reels: ["FFFFFF", "FFD84D"], clean: ["FFFFFF", "FFFFFF"],
      podcast: ["FFFFFF", "FBBF24"], cinematic: ["FFF4DB", "FFF4DB"], neon: ["FFFFFF", "BFA7FF"],
      boxed: ["FFFFFF", "8FE6D1"], outline: ["FFFFFF", "75D8FF"], minimal: ["FFFFFF", "FFFFFF"],
      opus: ["FFFFFF", "FFD400"], bold: ["FFFFFF", "00E5FF"], movie: ["FFF3C4", "FFF3C4"], subtitle: ["FFFFFF", "FFFFFF"],
    };
    const [textColor, highlightColor] = colors[captionStyle] || colors.creator;
    setCaptionTextColor(textColor); setCaptionHighlightColor(highlightColor);
  }, [captionStyle]);
  useEffect(() => { sourcesRef.current = sources; }, [sources]);
  useEffect(() => { if (video) addSource(video); }, [video]);
  const mediaPathKey = [...new Set([...sources.map((source) => source.path), ...(music ? [music] : []), ...sfxClips.map((clip) => clip.path)].filter(Boolean))].join("\n");
  useEffect(() => {
    const paths = mediaPathKey ? mediaPathKey.split("\n") : [];
    if (!paths.length) { setMissingMedia([]); return; }
    let current = true;
    api.post<{ items: { path: string; exists: boolean }[] }>("/api/editor/media/check", { paths })
      .then((result) => { if (current) setMissingMedia(result.items.filter((item) => !item.exists).map((item) => item.path)); })
      .catch(() => {});
    return () => { current = false; };
  }, [mediaPathKey]);

  const addSource = (path: string) => {
    if (sourcesRef.current.some((s) => s.path === path)) return;
    const isFirst = !sourcesRef.current.length;
    const item = { id: key(), path, duration: 0 };
    sourcesRef.current = [...sourcesRef.current, item];
    setSources((all) => [...all, item]);
    if (isFirst) setVideo(path);
  };
  const importVideo = (path: string) => {
    if (sourcesRef.current.some((source) => source.path === path)) return;
    autoMontagePending.current.add(path);
    addSource(path);
    if (projectName === "Новый проект") {
      const filename = path.split(/[\\/]/).pop() || "Видео";
      setProjectName(filename.replace(/\.[^.]+$/, "").slice(0, 120));
    }
    setPanel("ai");
  };
  const activeSegment = segments.find((s) => s.id === selected) ?? segments[0];
  const activeSource = sources.find((s) => s.id === activeSegment?.source);
  const audioTrackAudible = (track: AudioTrackName, mutes = audioMutes, solos = audioSolos) =>
    !mutes[track] && (!Object.values(solos).some(Boolean) || solos[track]);
  const filter = `brightness(${Math.max(.5, 1 + grade.brightness)}) contrast(${grade.contrast}) saturate(${grade.saturation}) sepia(${Math.abs(grade.temperature) * .13}) hue-rotate(${(grade.temperature < 0 ? 5 : 0) + grade.tint * 5}deg)`;
  const previewTargetAspect = aspect === "9:16" ? 9 / 16 : aspect === "1:1" ? 1 : aspect === "16:9" ? 16 / 9 : null;
  const previewSubjectX = subjectFollow && activeSource && previewTargetAspect
    ? subjectFocusAt(subjectTracks[activeSource.id], playhead) : null;
  const previewSourceAspect = activeSource?.width && activeSource.height ? activeSource.width / activeSource.height : 16 / 9;
  const previewCropFraction = previewTargetAspect && previewSourceAspect > previewTargetAspect
    ? previewTargetAspect / previewSourceAspect : 1;
  const previewMaxStart = Math.max(0, 1 - previewCropFraction);
  const previewObjectPosition = previewSubjectX == null || previewMaxStart <= 0 ? 50
    : Math.max(0, Math.min(100, (Math.max(0, Math.min(previewMaxStart, previewSubjectX - previewCropFraction / 2)) / previewMaxStart) * 100));
  const previewFrameStyle: React.CSSProperties | undefined = aspect === "9:16"
    ? { width: "min(100%, 45vw, 25.3125vh, 348.75px)", height: "min(80vw, 45vh, 620px)" }
    : aspect === "1:1" ? { width: "min(100%, 45vw, 45vh, 620px)", height: "min(45vw, 45vh, 620px)" }
      : aspect === "16:9" ? { width: "min(100%, 45vw, 80vh, 1102px)", height: "min(25.3125vw, 45vh, 620px)" }
        : undefined;
  const sourcePreviewStyle: React.CSSProperties = previewTargetAspect
    ? { filter, width: "100%", height: "100%", maxWidth: "none", maxHeight: "none", objectFit: "cover", objectPosition: `${previewObjectPosition}% 50%` }
    : { filter };
  const activeVideoAnalysis = analysis && analysisSourcePath === activeSource?.path ? analysis : null;
  const running = job && ["queued", "running"].includes(job.status);
  const agentStepFraction = agentProgress?.total
    ? (Math.max(0, agentProgress.step - 1) + (running ? job?.progress || 0 : 0)) / agentProgress.total : 0;
  const agentProgressPercent = agentProgress?.total
    ? Math.min(98, Math.max(agentProgress.percent, Math.round(12 + 80 * agentStepFraction)))
    : Math.max(0, Math.min(100, agentProgress?.percent || 0));
  const timeline = timelineLayout(segments);
  const timelineDuration = timeline.duration;
  const sceneMarkers = activeVideoAnalysis?.scene_detection_available === true
    ? sceneMarkersForTimeline(segments, timeline.starts, activeSource?.id, activeVideoAnalysis.scene_changes || []) : [];
  const sourceTail = sources.length % 100;
  const sourceNoun = sourceTail % 10 === 1 && sourceTail !== 11 ? "исходник"
    : sourceTail % 10 >= 2 && sourceTail % 10 <= 4 && (sourceTail < 12 || sourceTail > 14) ? "исходника" : "исходников";
  const vaceReady = installedModels.vace === true;
  const sfxReady = installedModels.stable_audio_3_sfx === true;
  const faceBlurReady = installedModels.yunet_face_blur === true;
  const sam2Ready = installedModels.sam2_video_mask === true;
  const activeIndex = activeSegment ? segments.findIndex((s) => s.id === activeSegment.id) : -1;
  const timelinePosition = activeSegment ? (timeline.starts[activeIndex] || 0)
    + Math.max(0, Math.min(activeSegment.end - activeSegment.start, playhead - activeSegment.start)) : 0;
  const previewTransition = activeIndex >= 0 ? previewTransitionAt(segments, activeIndex, playhead) : null;
  const previewTransitionSource = sources.find((source) => source.id === previewTransition?.otherSource);
  const transitionSecondaryRole = previewTransition?.activeRole === "incoming" ? "outgoing" : "incoming";
  const primaryPreviewStyle: React.CSSProperties = previewTransition && !maskPickMode
    ? { ...sourcePreviewStyle, position: "relative", ...transitionLayerStyle(previewTransition.type, previewTransition.progress, previewTransition.activeRole) }
    : sourcePreviewStyle;
  const secondaryPreviewStyle: React.CSSProperties | undefined = previewTransition && !maskPickMode
    ? { ...sourcePreviewStyle, position: "absolute", inset: 0, width: "100%", height: "100%", maxWidth: "none", maxHeight: "none",
        objectFit: previewTargetAspect ? "cover" : "contain",
        ...transitionLayerStyle(previewTransition.type, previewTransition.progress, transitionSecondaryRole) }
    : undefined;
  const syncPreviewAudio = () => {
    const gains = previewTransition
      ? transitionAudioGains(previewTransition.type, previewTransition.progress, previewTransition.activeRole)
      : { active: 1, other: 0 };
    if (player.current) {
      player.current.volume = Math.max(0, Math.min(1, volume * gains.active));
      player.current.muted = !audioTrackAudible("original");
    }
    if (transitionPlayer.current) {
      transitionPlayer.current.volume = Math.max(0, Math.min(1, volume * gains.other));
      transitionPlayer.current.muted = !audioTrackAudible("original");
    }
    const musicPlayer = musicPreviewPlayer.current;
    const musicIsAudible = !!music && audioTrackAudible("music");
    const speechNow = !!activeSource && (sourceCaptions[activeSource.id]?.words || [])
      .some((word) => playhead >= word.start && playhead < word.end);
    if (musicPlayer) {
      musicPlayer.volume = musicIsAudible ? Math.max(0, Math.min(1, musicVolume * (duckMusic && speechNow ? .38 : 1))) : 0;
      if (musicPlayer.readyState >= 1 && Number.isFinite(musicPlayer.duration) && musicPlayer.duration > 0) {
        const target = timelinePosition % musicPlayer.duration;
        if (Math.abs(musicPlayer.currentTime - target) > .35) musicPlayer.currentTime = target;
      }
      if (playing && musicIsAudible && !musicPlayer.ended) { if (musicPlayer.paused) void musicPlayer.play().catch(() => undefined); }
      else musicPlayer.pause();
    }
    for (const clip of sfxClips) {
      const audio = sfxPreviewPlayers.current.get(clip.id);
      if (!audio) continue;
      const offset = timelinePosition - clip.at;
      const clipIsAudible = audioTrackAudible("sfx") && offset >= 0 && offset < clip.duration;
      audio.volume = clipIsAudible ? Math.max(0, Math.min(1, clip.volume)) : 0;
      if (clipIsAudible && audio.readyState >= 1 && Math.abs(audio.currentTime - offset) > .3) audio.currentTime = offset;
      else if (!clipIsAudible && audio.readyState >= 1 && audio.currentTime > .05) audio.currentTime = 0;
      if (playing && clipIsAudible) { if (audio.paused) void audio.play().catch(() => undefined); }
      else audio.pause();
    }
  };
  const activeBroll = brollClips.find((clip) => timelinePosition >= clip.at && timelinePosition < clip.at + clip.end - clip.start);
  const activeBrollSource = sources.find((source) => source.id === activeBroll?.source);
  const previewWords = sourceCaptions[activeSource?.id || ""]?.words || [];
  const selectedCaptionTiming = captionTimingSelection && activeSource && captionTimingSelection.sourceId === activeSource.id
    ? { index: captionTimingSelection.index, word: previewWords[captionTimingSelection.index] } : null;
  const normalizedCaptionQuery = captionWordQuery.trim().toLowerCase();
  const visibleCaptionWords = previewWords.map((word, index) => ({ word, index })).filter(({ word }) =>
    !normalizedCaptionQuery || word.word.toLowerCase().includes(normalizedCaptionQuery));
  const captionWordsWithText = previewWords.filter((word) => word.word.trim());
  const previewWordIndex = captionWordsWithText.findIndex((word) => playhead >= word.start && playhead <= word.end);
  const previewGroupStart = previewWordIndex < 0 ? -1 : Math.floor(previewWordIndex / captionWordsPerLine) * captionWordsPerLine;
  const captionPreviewWords = previewGroupStart < 0 ? [] : captionWordsWithText.slice(previewGroupStart, previewGroupStart + captionWordsPerLine);
  const timelinePercent = timelineDuration ? Math.min(100, Math.max(0, timelinePosition / timelineDuration * 100)) : 0;
  const markCount = Math.max(2, Math.min(9, Math.ceil(timelineDuration / 5) + 1));
  const timeMarks = Array.from({ length: markCount }, (_, i) => timelineDuration * i / Math.max(1, markCount - 1));

  const setSourceInfo = (id: string, e: React.SyntheticEvent<HTMLVideoElement>) => {
    const el = e.currentTarget;
    const duration = Number.isFinite(el.duration) ? el.duration : 0;
    const next = { id, path: sourcesRef.current.find((s) => s.id === id)?.path ?? "", duration,
      width: el.videoWidth, height: el.videoHeight };
    sourcesRef.current = sourcesRef.current.map((source) => source.id === id ? { ...source, ...next } : source);
    setSources((all) => all.map((s) => s.id === id ? { ...s, ...next } : s));
    setSegments((all) => {
      if (!duration) return all;
      if (!all.length) return [{ id: key(), source: id, start: 0, end: duration }];
      return all.map((segment) => segment.source !== id ? segment : {
        ...segment, start: Math.min(Math.max(0, segment.start), Math.max(0, duration - .1)),
        end: Math.min(Math.max(segment.start + .1, segment.end), duration),
      }).filter((segment) => segment.end - segment.start >= .1);
    });
    setBrollClips((all) => all.map((clip) => {
      if (clip.source !== id) return clip;
      const start = Math.min(Math.max(0, clip.start), Math.max(0, duration - .1));
      return { ...clip, start, end: Math.min(Math.max(start + .1, clip.end), duration) };
    }).filter((clip) => clip.end - clip.start >= .1));
    setSelected((current) => current || "pending");
    if (next.path && autoMontagePending.current.has(next.path) && duration > 0 && !autoMontageRunning.current.has(next.path)) {
      autoMontagePending.current.delete(next.path);
      window.setTimeout(() => { void autoMontageUploadedVideo(next); }, 0);
    }
  };
  useEffect(() => {
    if (segments.length && !segments.some((segment) => segment.id === selected)) setSelected(segments[0].id);
  }, [selected, segments]);
  useEffect(() => {
    if (!player.current || !activeSegment) return;
    const el = player.current;
    const seek = () => { if (el.currentTime < activeSegment.start || el.currentTime > activeSegment.end) el.currentTime = activeSegment.start; };
    if (el.readyState >= 1) seek(); else el.addEventListener("loadedmetadata", seek, { once: true });
    return () => el.removeEventListener("loadedmetadata", seek);
  }, [activeSegment?.id, activeSource?.path]);
  useEffect(() => { syncPreviewAudio(); }, [playing, timelinePosition, playhead, activeSource?.id, sourceCaptions,
    music, musicVolume, duckMusic, sfxClips, audioMutes, audioSolos, volume, previewTransition?.progress, previewTransition?.activeRole]);
  useEffect(() => {
    const element = transitionPlayer.current;
    if (!element || !previewTransition || !previewTransitionSource || maskPickMode) return;
    if (element.readyState >= 1 && (!playing || Math.abs(element.currentTime - previewTransition.otherTime) > .35)) {
      element.currentTime = previewTransition.otherTime;
    }
    if (playing) { if (element.paused) void element.play().catch(() => undefined); }
    else element.pause();
  }, [previewTransition?.otherSource, previewTransition?.otherTime, previewTransition?.progress, previewTransitionSource?.path, playing, maskPickMode]);
  useEffect(() => { if (activeSegment) setEditStart(activeSegment.start); }, [activeSegment?.id]);
  useEffect(() => { chatScroll.current?.scrollTo({ top: chatScroll.current.scrollHeight, behavior: "smooth" }); }, [chatMessages, assistantBusy]);

  const splitItemsAtSourceTime = (items: Segment[], index: number, sourceTime: number) => {
    const segment = items[index];
    if (!segment || sourceTime <= segment.start + .12 || sourceTime >= segment.end - .12) return null;
    const right = { ...segment, id: key(), start: +sourceTime.toFixed(3) };
    const left = { ...segment, end: +sourceTime.toFixed(3), transitionAfter: undefined };
    return { items: [...items.slice(0, index), left, right, ...items.slice(index + 1)], leftId: left.id, rightId: right.id };
  };
  const splitAtPlayhead = () => {
    if (!activeSegment) return;
    const index = segments.findIndex((segment) => segment.id === activeSegment.id);
    const split = splitItemsAtSourceTime(segments, index, playhead);
    if (!split) return;
    pushUndo();
    setSegments(split.items); setSelected(split.rightId);
  };
  const removeSegment = (id: string) => {
    const next = segments.filter((s) => s.id !== id);
    pushUndo();
    setSegments(next); setSelected(next[Math.max(0, segments.findIndex((s) => s.id === id) - 1)]?.id ?? "");
  };
  const moveSegment = (direction: -1 | 1) => {
    const i = segments.findIndex((s) => s.id === selected), n = i + direction;
    if (i < 0 || n < 0 || n >= segments.length) return;
    pushUndo();
    const all = [...segments]; [all[i], all[n]] = [all[n], all[i]]; setSegments(all);
  };
  const reorderSegment = (fromId: string, toId: string) => {
    const from = segments.findIndex((s) => s.id === fromId), to = segments.findIndex((s) => s.id === toId);
    if (from < 0 || to < 0 || from === to) return;
    pushUndo();
    const all = [...segments], [moved] = all.splice(from, 1);
    all.splice(to, 0, moved);
    setSegments(all); setSelected(fromId);
  };
  const addSourceToTimeline = (source: Source) => {
    if (!source.duration) return;
    pushUndo();
    const segment = { id: key(), source: source.id, start: 0, end: source.duration };
    setSegments((all) => [...all, segment]); setSelected(segment.id);
  };
  const addBrollToTimeline = (source: Source) => {
    if (!source.duration || !timelineDuration) return;
    pushUndo();
    const length = Math.min(8, source.duration);
    setBrollClips((all) => [...all, { id: key(), source: source.id, start: 0, end: length, at: timelinePosition, scale: .38 }]);
    setAssistantNote("B-roll добавлен на отдельную дорожку V2. Перетащите его на шкале или настройте время во вкладке «Монтаж».");
  };
  const setSegmentBound = (which: "start" | "end", value: number) => {
    if (!activeSegment) return;
    pushUndo();
    const src = sources.find((s) => s.id === activeSegment.source);
    const bounded = Math.max(0, Math.min(src?.duration || value, value));
    setSegments((all) => all.map((s) => s.id !== activeSegment.id ? s : which === "start"
      ? { ...s, start: Math.min(bounded, s.end - .1) } : { ...s, end: Math.max(bounded, s.start + .1) }));
  };
  const beginTimelineTrim = (event: React.PointerEvent<HTMLSpanElement>, segment: Segment, edge: "start" | "end") => {
    event.preventDefault(); event.stopPropagation();
    const track = event.currentTarget.closest(".editor-video-track") as HTMLElement | null;
    if (!track || !timelineDuration) return;
    const usableWidth = track.clientWidth - 6 - 4 * Math.max(0, segments.length - 1);
    if (usableWidth <= 0) return;
    setSelected(segment.id);
    timelineTrimDrag.current = {
      pointerId: event.pointerId, segmentId: segment.id, edge, originX: event.clientX,
      pixelsPerSecond: usableWidth / timelineDuration, initial: { ...segment }, snapshot: captureSnapshot(),
      finalStart: segment.start, finalEnd: segment.end,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveTimelineTrim = (event: React.PointerEvent<HTMLSpanElement>) => {
    const drag = timelineTrimDrag.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const sourceDuration = sources.find((source) => source.id === drag.initial.source)?.duration || drag.initial.end;
    const delta = (event.clientX - drag.originX) / drag.pixelsPerSecond;
    const requested = drag.edge === "start" ? drag.initial.start + delta : drag.initial.end + delta;
    const value = +(drag.edge === "start"
      ? Math.max(0, Math.min(drag.initial.end - .1, requested))
      : Math.max(drag.initial.start + .1, Math.min(sourceDuration, requested))).toFixed(2);
    const current = drag.edge === "start" ? drag.initial.start : drag.initial.end;
    if (Math.abs(value - current) < .005) return;
    if (drag.edge === "start") drag.finalStart = value; else drag.finalEnd = value;
    setSegments((all) => all.map((segment) => segment.id !== drag.segmentId ? segment
      : drag.edge === "start" ? { ...segment, start: value } : { ...segment, end: value }));
  };
  const finishTimelineTrim = (event: React.PointerEvent<HTMLSpanElement>) => {
    const drag = timelineTrimDrag.current;
    if (drag?.pointerId !== event.pointerId) return;
    if (Math.abs(drag.finalStart - drag.initial.start) >= .005 || Math.abs(drag.finalEnd - drag.initial.end) >= .005) {
      setUndoStack((all) => [...all, drag.snapshot].slice(-30));
      setRedoStack([]);
    }
    timelineTrimDrag.current = null;
  };
  const keyboardTimelineTrim = (event: React.KeyboardEvent<HTMLSpanElement>, segment: Segment, edge: "start" | "end") => {
    const sourceDuration = sources.find((source) => source.id === segment.source)?.duration || segment.end;
    const current = edge === "start" ? segment.start : segment.end;
    const step = event.shiftKey ? 1 : .1;
    let value: number;
    if (event.key === "Home") value = edge === "start" ? 0 : segment.start + .1;
    else if (event.key === "End") value = edge === "start" ? segment.end - .1 : sourceDuration;
    else if (["ArrowLeft", "ArrowDown"].includes(event.key)) value = current - step;
    else if (["ArrowRight", "ArrowUp"].includes(event.key)) value = current + step;
    else return;
    event.preventDefault(); event.stopPropagation();
    value = +(edge === "start" ? Math.max(0, Math.min(segment.end - .1, value))
      : Math.max(segment.start + .1, Math.min(sourceDuration, value))).toFixed(2);
    if (Math.abs(value - current) < .005) return;
    pushUndo();
    setSegments((all) => all.map((item) => item.id !== segment.id ? item
      : edge === "start" ? { ...item, start: value } : { ...item, end: value }));
  };
  const seekTo = (time: number) => { if (player.current) player.current.currentTime = time; setPlayhead(time); };
  const seekToSegment = (segment: Segment, sourceTime: number) => {
    const bounded = Math.max(segment.start, Math.min(segment.end, sourceTime));
    setSelected(segment.id);
    if (segment.source !== activeSource?.id) {
      pendingTimelineSeek.current = { sourceId: segment.source, time: bounded };
      setPlayhead(bounded);
    } else {
      pendingTimelineSeek.current = null;
      seekTo(bounded);
    }
  };
  const seekTimelineTo = (time: number) => {
    const target = timelineSeekTargetAt(segments, time);
    if (!target) return;
    const segment = segments.find((item) => item.id === target.segmentId);
    if (segment) seekToSegment(segment, target.sourceTime);
  };
  const scrubTimelinePointer = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!timelineDuration) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width <= 0) return;
    const fraction = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
    seekTimelineTo(fraction * timelineDuration);
  };
  const beginTimelineScrub = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !timelineDuration) return;
    event.preventDefault();
    timelineScrubPointer.current = event.pointerId;
    event.currentTarget.setPointerCapture(event.pointerId);
    scrubTimelinePointer(event);
  };
  const moveTimelineScrub = (event: React.PointerEvent<HTMLDivElement>) => {
    if (timelineScrubPointer.current === event.pointerId && event.buttons === 1) scrubTimelinePointer(event);
  };
  const endTimelineScrub = (event: React.PointerEvent<HTMLDivElement>) => {
    if (timelineScrubPointer.current === event.pointerId) timelineScrubPointer.current = null;
  };
  const keyboardTimelineScrub = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 1 : .1;
    if (event.key === 'Home') { event.preventDefault(); seekTimelineTo(0); }
    else if (event.key === 'End') { event.preventDefault(); seekTimelineTo(timelineDuration); }
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') { event.preventDefault(); seekTimelineTo(timelinePosition - step); }
    else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') { event.preventDefault(); seekTimelineTo(timelinePosition + step); }
  };
  const seekToAnalysisTime = (sourceTime: number, segmentId?: string) => {
    const segment = segments.find((item) => (!segmentId || item.id === segmentId) && item.source === activeSource?.id
      && sourceTime >= item.start && sourceTime <= item.end);
    if (!segment) return;
    setSelected(segment.id);
    seekTo(Math.max(segment.start, Math.min(segment.end, sourceTime)));
  };

  const captureSnapshot = (): EditSnapshot => {
    const snapshot = latestSnapshotRef.current;
    if (!snapshot) throw new Error("Проект ещё не готов к сохранению шага отмены.");
    return {
      ...snapshot, sources: snapshot.sources.map((source) => ({ ...source })),
      segments: snapshot.segments.map((segment) => ({ ...segment, transitionAfter: segment.transitionAfter ? { ...segment.transitionAfter } : undefined })),
      brollClips: snapshot.brollClips.map((clip) => ({ ...clip })),
      sourceCaptions: { ...snapshot.sourceCaptions }, grade: { ...snapshot.grade },
      audioMutes: { ...snapshot.audioMutes }, audioSolos: { ...snapshot.audioSolos },
      sfxClips: snapshot.sfxClips.map((clip) => ({ ...clip })),
    };
  };
  const pushUndo = () => {
    setUndoStack((all) => [...all, captureSnapshot()].slice(-30));
    setRedoStack([]);
  };
  const commitCaptionTiming = (sourceId: string, index: number, edge: "start" | "end", text: string) => {
    setCaptionTimingDraft(null);
    if (!text.trim()) return;
    const value = Number(text);
    const current = sourceCaptions[sourceId]?.words[index];
    if (!Number.isFinite(value) || !current) return;
    const duration = sources.find((source) => source.id === sourceId)?.duration || Number.MAX_SAFE_INTEGER;
    const words = sourceCaptions[sourceId].words;
    const previous = words[index - 1], next = words[index + 1];
    const rounded = Math.round(value * 100) / 100;
    let nextValue: number;
    if (edge === "start") {
      const min = previous ? previous.start + .01 : 0;
      const max = Math.min(current.end - .04, next ? next.start - .01 : duration);
      if (max < min) return;
      nextValue = Math.max(min, Math.min(max, rounded));
    } else {
      const min = current.start + .04;
      const max = Math.min(duration, next ? next.start - .01 : duration);
      if (max < min) return;
      nextValue = Math.max(min, Math.min(max, rounded));
    }
    if (Math.abs(nextValue - current[edge]) < .005) return;
    pushUndo();
    setSourceCaptions((all) => {
      const sourceCaptionsForVideo = all[sourceId];
      if (!sourceCaptionsForVideo?.words[index]) return all;
      const words = sourceCaptionsForVideo.words.map((word, wordIndex) => wordIndex === index
        ? { ...word, [edge]: nextValue } : word);
      return { ...all, [sourceId]: { ...sourceCaptionsForVideo, words } };
    });
  };
  const restoreSnapshot = (snapshot: EditSnapshot) => {
    setSources(snapshot.sources); sourcesRef.current = snapshot.sources;
    setSegments(snapshot.segments); setSelected(snapshot.selected); setSourceCaptions(snapshot.sourceCaptions);
    setBrollClips(snapshot.brollClips);
    setGrade(snapshot.grade); setAspect(snapshot.aspect); setFps(snapshot.fps); setVolume(snapshot.volume);
    setAudioMutes(readAudioTrackFlags(snapshot.audioMutes)); setAudioSolos(readAudioTrackFlags(snapshot.audioSolos));
    setMusicVolume(snapshot.musicVolume); setDuckMusic(snapshot.duckMusic !== false); setFade(snapshot.fade); setNormalizeAudio(snapshot.normalizeAudio); setSubjectFollow(snapshot.subjectFollow !== false); setMusic(snapshot.music); setOverlay(snapshot.overlay); setOverlayPosition(snapshot.overlayPosition);
    setCaptionStyle(snapshot.captionStyle); setCaptionAnimation(snapshot.captionAnimation); setCaptionPosition(snapshot.captionPosition); setCaptionHighlight(snapshot.captionHighlight);
    setCaptionTextColor(snapshot.captionTextColor); setCaptionHighlightColor(snapshot.captionHighlightColor);
    setCaptionWordsPerLine(snapshot.captionWordsPerLine); setSfxClips(snapshot.sfxClips);
  };

  const transcribeTimeline = async () => {
    const used = sources.filter((source) => segments.some((segment) => segment.source === source.id));
    if (!used.length) return null;
    const done = await run("/api/tools/video_transcribe", { sources: used.map(({ id, path }) => ({ id, path })) });
    if (done?.status !== "done") return null;
    const resultCaptions = (done.result as any)?.captions as SourceCaptions | undefined;
    if (resultCaptions) setSourceCaptions(resultCaptions);
    return resultCaptions || null;
  };
  const autoMontageUploadedVideo = async (source: Source) => {
    if (autoMontageRunning.current.has(source.path)) return;
    autoMontageRunning.current.add(source.path);
    setPanel("ai"); setAssistantBusy(true); setAssistantNote(""); setResult(null); setResultQa(null); setJob(null);
    setAgentProgress({ phase: "Анализирую речь и кадры", percent: 5, step: 1, total: 3 });
    try {
      const analysisJob = await run("/api/tools/video_analyze", { video: source.path, purpose: "editor" });
      if (analysisJob?.status !== "done") throw new Error(analysisJob?.error || "Локальный анализ видео не завершился.");
      const value = (analysisJob.result as any)?.analysis ?? null;
      const transcription = (analysisJob.result as any)?.transcription || {};
      const words: CaptionWord[] = Array.isArray(transcription.words) ? transcription.words.map((word: any) => ({
        start: Number(word.start), end: Number(word.end), word: String(word.word || ""),
      })).filter((word: CaptionWord) => Number.isFinite(word.start) && Number.isFinite(word.end) && word.end > word.start && word.word.trim()) : [];
      setAnalysis(value); setAnalysisSourcePath(value ? source.path : null);

      setAgentProgress({ phase: "Собираю редактируемый монтаж", percent: 55, step: 2, total: 3 });
      const latest = latestSnapshotRef.current;
      if (!latest) throw new Error("Проект ещё не готов к монтажу.");
      const subtitleEvidence = String(value?.existing_subtitles || "uncertain");
      const captionsCanBeAdded = words.length > 0 && subtitleEvidence === "not_visible";
      const editPlan = makeSpeechEdit(source, words, subtitleEvidence, value?.scene_changes || [], value?.scene_detection_available === true);
      const editedParts = editPlan.segments;
      const currentSegments = timelineSegmentsRef.current;
      const activeSources = new Set(currentSegments.map((segment) => segment.source));
      const visualBalance = value?.visual_balance;
      const recommendation = visualBalance?.recommended_grade;
      const previousGrade = latest.grade || defaultGrade;
      const neutralGrade = Math.abs(previousGrade.brightness) < .001 && Math.abs(previousGrade.contrast - 1) < .001 &&
        Math.abs(previousGrade.saturation - 1) < .001 && Math.abs(previousGrade.temperature) < .001 && Math.abs(previousGrade.tint) < .001;
      const canApplyAutoGrade = visualBalance?.available === true && visualBalance.adjustment_recommended === true &&
        recommendation && Number.isFinite(recommendation.brightness) && Number.isFinite(recommendation.contrast) &&
        neutralGrade && activeSources.size <= 1 && (!activeSources.size || activeSources.has(source.id));
      const renderGrade: Grade = canApplyAutoGrade ? { ...previousGrade,
        brightness: Math.max(-.08, Math.min(.08, recommendation.brightness)),
        contrast: Math.max(.94, Math.min(1.06, recommendation.contrast)),
      } : previousGrade;
      const autoGradeApplied = !!canApplyAutoGrade &&
        (Math.abs(renderGrade.brightness) >= .001 || Math.abs(renderGrade.contrast - 1) >= .001);
      const sourceSegments = currentSegments.filter((segment) => segment.source === source.id);
      let nextSegments = [...currentSegments];
      if (sourceSegments.length === 1 && sourceSegments[0].start <= .02 && Math.abs(sourceSegments[0].end - source.duration) <= .25) {
        const index = nextSegments.findIndex((segment) => segment.id === sourceSegments[0].id);
        const parts = editedParts.map((segment, partIndex) => ({ ...segment, id: partIndex === 0 ? sourceSegments[0].id : segment.id }));
        nextSegments.splice(index, 1, ...parts);
      } else if (!sourceSegments.length) {
        nextSegments = [...nextSegments, ...editedParts];
      }

      pushUndo();
      if (autoGradeApplied) setGrade(renderGrade);
      timelineSegmentsRef.current = nextSegments;
      setSegments(nextSegments);
      setSelected(nextSegments.find((segment) => segment.source === source.id)?.id || nextSegments[0]?.id || "");
      setPlayhead(nextSegments.find((segment) => segment.source === source.id)?.start || 0);
      setFade(Math.max(.25, latest.fade));
      setNormalizeAudio(true);
      if (captionsCanBeAdded) {
        setSourceCaptions((all) => ({ ...all, [source.id]: { language: transcription.language || value?.language || null, words } }));
      }

      const captionsForRender: SourceCaptions = captionsCanBeAdded
        ? { ...latest.sourceCaptions, [source.id]: { language: transcription.language || value?.language || null, words } }
        : latest.sourceCaptions;
      const layout = timelineLayout(nextSegments);
      const captionWords = nextSegments.flatMap((segment, index) => {
        const sourceWords = captionsForRender[segment.source]?.words || [];
        const timelineStart = layout.starts[index] || 0;
        return sourceWords.flatMap((word) => {
          const start = Math.max(segment.start, word.start), end = Math.min(segment.end, word.end);
          return end > start ? [{ start: timelineStart + start - segment.start, end: timelineStart + end - segment.start, word: word.word }] : [];
        });
      });
      setAgentProgress({ phase: "Рендерю и проверяю экспорт", percent: 66, step: 3, total: 3 });
      setRenderInProgress(true);
      const renderJob = await run("/api/tools/video_timeline_render", {
        sources: sourcesRef.current.map(({ id, path }) => ({ id, path })),
        segments: nextSegments.map(({ id, source: sourceId, start, end, transitionAfter }) => ({ id, source: sourceId, start, end, transition_after: transitionAfter })),
        broll: latest.brollClips.map(({ source: sourceId, start, end, at, scale }) => ({ path: sourcesRef.current.find((item) => item.id === sourceId)?.path, start, end, at, scale })),
        grade: renderGrade, aspect: latest.aspect, fps: latest.fps,
        volume: audioTrackAudible("original", latest.audioMutes, latest.audioSolos) ? latest.volume : 0, fade: Math.max(.25, latest.fade),
        normalize_audio: true, subject_follow: latest.subjectFollow, overlay: latest.overlay, overlay_position: latest.overlayPosition,
        music: audioTrackAudible("music", latest.audioMutes, latest.audioSolos) ? latest.music || undefined : undefined,
        music_volume: latest.musicVolume, duck_music: latest.duckMusic && audioTrackAudible("original", latest.audioMutes, latest.audioSolos),
        sfx: audioTrackAudible("sfx", latest.audioMutes, latest.audioSolos)
          ? latest.sfxClips.map(({ path, at, volume: gain }) => ({ path, at, volume: gain })) : [], caption_words: captionWords,
        caption_language: sourcesRef.current.map((source) => captionsForRender[source.id]?.language).find(Boolean) || activeVideoAnalysis?.language || "ru",
        caption_style: latest.captionStyle, caption_animation: latest.captionAnimation,
        caption_position: latest.captionPosition, caption_words_per_line: latest.captionWordsPerLine,
        caption_highlight: latest.captionHighlight, caption_text_color: latest.captionTextColor,
        caption_highlight_color: latest.captionHighlightColor,
        assistant_note: "Автомонтаж: локальный анализ, осторожная обрезка длинных пауз, переходы, безопасные титры, объективная проверка уровней изображения и выравнивание громкости.",
      });
      if (renderJob?.status !== "done" || !renderJob.result?.outputs?.[0]) throw new Error(renderJob?.error || "Экспорт или автоматическая проверка не завершились.");
      const qa = (renderJob.result as any)?.quality ?? null;
      setResult(renderJob.result.outputs[0]); setResultQa(qa);
      const cutCount = Math.max(0, editedParts.length - 1);
      const reframe = (renderJob.result as any)?.reframe || {};
      setSubjectTracks(cleanSubjectTracks(reframe.source_tracks));
      const reframeNote = Number(reframe.tracked_sources || 0) > 0
        ? `При кадрировании лицо удерживалось по ${Number(reframe.tracked_samples || 0)} опорным точкам.`
        : Number(reframe.fallback_sources || 0) > 0 ? "Для части видео применён центральный crop: лицо не удалось надёжно отследить." : "";
      const captionNote = captionsCanBeAdded ? "Добавил титры по расшифровке Whisper." : words.length
        ? subtitleEvidence === "visible" ? "Не дублировал уже видимые в кадре субтитры." : "Не стал накладывать титры: по кадрам нельзя надёжно определить, есть ли они уже в исходнике."
        : "Речь не распознана достаточно надёжно для титров; исходный звук оставлен без изменений.";
      const visualNotes = Number(qa?.visual_review?.notes?.length || 0);
      const visualReviewNote = !qa?.visual_review?.available ? "ИИ-проверка кадров недоступна; просмотрите миниатюры вручную."
        : visualNotes ? `ИИ отметил возможные визуальные замечания (${visualNotes}) для просмотра.`
          : "На просмотренных кадрах ИИ явных проблем не отметил.";
      const gradeNote = visualBalance?.available
        ? autoGradeApplied ? `Аккуратно скорректировал яркость и контраст по ${visualBalance.sample_count} контрольным кадрам.`
          : visualBalance.adjustment_recommended ? "Автоцвет нашёл возможную коррекцию, но сохранил заданный цвет или многокамерный таймлайн без изменений."
            : `Проверил уровни на ${visualBalance.sample_count} контрольных кадрах; заметная автокоррекция не нужна.`
        : "Анализ уровней изображения недоступен; цвет оставлен без изменений.";
      const sceneNote = editPlan.sceneCuts ? `Добавил ${editPlan.sceneCuts} перехода на подтверждённых сменах плана в паузах между законченными фразами.` : "";
      const summary = `Автомонтаж готов: ${cutCount ? `подготовил ${cutCount} монтажных стыков с плавными переходами` : "сохранил цельный фрагмент"}; выровнял громкость. ${gradeNote} ${sceneNote} ${captionNote} ${reframeNote} ${qa?.passed ? "Техническая проверка экспорта пройдена." : "Экспорт создан; просмотрите замечания проверки."} ${visualReviewNote} Проверьте результат и отредактируйте таймлайн при необходимости.`;
      setAssistantNote(summary);
      setChatMessages((all) => [...all, { role: "assistant", text: summary }]);
      setAgentProgress({ phase: "Монтаж и экспорт проверены", percent: 100, step: 3, total: 3 });
    } catch (error: any) {
      const message = `Автомонтаж прерван: ${error?.message || "неизвестная ошибка"}. Исходное видео и доступный таймлайн сохранены.`;
      setAssistantNote(message); setChatMessages((all) => [...all, { role: "assistant", text: message }]);
      setAgentProgress({ phase: "Нужна проверка", percent: 100, step: 0, total: 3, error: true });
    } finally {
      setRenderInProgress(false);
      autoMontageRunning.current.delete(source.path);
      setAssistantBusy(false);
    }
  };
  const makeCaptions = async () => {
    setPanel("captions");
    await transcribeTimeline();
  };
  const applyProducerLook = (look: "vertical" | "podcast" | "cinematic") => {
    pushUndo();
    const profiles = {
      vertical: { grade: { brightness: 0.015, contrast: 1.08, saturation: 1.12, temperature: 0.04, tint: 0 }, style: "reels", aspect: "9:16", highlight: true },
      podcast: { grade: { brightness: 0, contrast: 1.04, saturation: 0.98, temperature: 0, tint: 0 }, style: "boxed", aspect, highlight: true },
      cinematic: { grade: { brightness: -0.005, contrast: 1.08, saturation: 0.91, temperature: -0.04, tint: 0.01 }, style: "cinematic", aspect, highlight: false },
    } as const;
    const profile = profiles[look];
    setGrade(profile.grade); setCaptionStyle(profile.style); setCaptionPosition("bottom"); setCaptionHighlight(profile.highlight);
    setAspect(profile.aspect);
    setSegments((all) => all.map((segment, index) => index < all.length - 1
      ? { ...segment, transitionAfter: { type: "dissolve", duration: look === "cinematic" ? 0.9 : 0.55 } }
      : segment));
    setAssistantNote(`Применён профиль «${look === "vertical" ? "Vertical Creator" : look === "podcast" ? "Podcast" : "Cinema"}»: цвет, оформление субтитров и плавные переходы. Проверьте предпросмотр перед экспортом.`);
  };
  const generateSfx = async () => {
    if (!sfxPrompt.trim() || !sfxReady || !segments.length) return;
    const prompt = sfxPrompt.trim();
    const done = await run("/api/tools/audio_sfx", { prompt, duration: sfxDuration });
    if (done?.status === "done" && done.result?.outputs?.[0]) {
      pushUndo();
      setSfxClips((all) => [...all, { id: key(), path: done.result!.outputs[0], at: timelinePosition,
        duration: Number(done.result?.duration) || sfxDuration, volume: 0.24, prompt }]);
      setAssistantNote("Звуковой эффект создан и размещён на аудиодорожке в позиции курсора.");
    }
  };
  const installSfx = async () => {
    const done = await run("/api/models/stable_audio_3_sfx/install", {});
    if (done?.status === "done") setAssistantNote("Stable Audio 3 Small-SFX готова. Теперь создайте звуковой эффект во вкладке «Звук».");
  };

  const installFaceBlur = async () => {
    const done = await run("/api/models/yunet_face_blur/install", {});
    if (done?.status === "done") setAssistantNote("YuNet установлен на CPU: можно размывать лица и автоматически удерживать говорящего при вертикальном кадрировании.");
  };

  const installSam2 = async () => {
    const done = await run("/api/models/sam2_video_mask/install", {});
    if (done?.status === "done") setAssistantNote("SAM 2.1 установлена локально. Теперь можно выбрать объект на кадре и отследить маску.");
  };
  const trackSelectedMask = async () => {
    if (!sam2Ready || !maskPoint || !activeSource || !activeSegment) return;
    const done = await run("/api/tools/video_mask_track", {
      video: activeSource.path, start: activeSegment.start, end: activeSegment.end,
      seed_time: maskSeedTime, point: maskPoint, mode: maskMode,
    });
    const output = done?.result?.outputs?.[0];
    if (done?.status === "done" && output) {
      pushUndo();
      setSources((all) => all.map((source) => source.id === activeSource.id ? { ...source, path: output } : source));
      setResult(output); setResultQa(null);
      setAssistantNote(`SAM 2 отследила объект от выбранного кадра (${fmt(maskSeedTime)}). Маска действует на диапазон выбранного фрагмента; проверьте быстрые движения и перекрытия.`);
      setMaskPoint(null);
    }
  };

  const runAssistant = async (submitted = command) => {
    const prompt = submitted.trim();
    if (!prompt || assistantBusy) return;
    setCommand(""); setChatMessages((all) => [...all, { role: "user", text: prompt }]);
    setAssistantBusy(true); setAssistantNote(""); setJob(null);
    setAgentProgress({ phase: "Разбираю команду и готовлю план монтажа", percent: 4, step: 0, total: 0 });
    try {
      const plan = await api.post<{ reply: string; operations: any[] }>("/api/editor/assist", { prompt, context: {
        timeline_duration: timelineDuration, playhead_seconds: timelinePosition, selected_segment_index: activeIndex,
        segments: segments.map((segment, index) => ({ ...segment, timeline_start: timeline.starts[index] || 0 })),
        grade, aspect, original_volume: volume, music_volume: musicVolume, duck_music: duckMusic,
        audio_mutes: audioMutes, audio_solos: audioSolos, caption_style: captionStyle,
        caption_animation: captionAnimation,
        captions_ready: Object.keys(sourceCaptions).length > 0, sfx_model_installed: sfxReady,
        video_evidence: activeVideoAnalysis ? {
          content_summary: activeVideoAnalysis.content_summary,
          visual_summary: activeVideoAnalysis.visual_summary,
          opening_note: activeVideoAnalysis.opening_note,
          ending_note: activeVideoAnalysis.ending_note,
          issues: activeVideoAnalysis.issues,
          edit_suggestions: activeVideoAnalysis.edit_suggestions,
          frame_notes: activeVideoAnalysis.frame_notes,
          scene_changes: activeVideoAnalysis.scene_changes,
          scene_detection_available: activeVideoAnalysis.scene_detection_available,
        } : undefined,
      } });
      const nextSegments: Segment[] = segments.map((segment) => ({ ...segment, transitionAfter: segment.transitionAfter ? { ...segment.transitionAfter } : undefined }));
      let nextSources: Source[] = sources.map((source) => ({ ...source }));
      let nextCaptions: SourceCaptions = { ...sourceCaptions }, captionsUpdated = false, nextFps = fps;
      let nextGrade = { ...grade }, nextAspect = aspect, nextVolume = volume, nextMusicVolume = musicVolume, nextDuckMusic = duckMusic;
      let nextAudioMutes = { ...audioMutes }, nextAudioSolos = { ...audioSolos };
      let selectedId = selected || activeSegment?.id || "", nextPlayhead: number | null = null;
      let hasSnapshot = false, changed = false;
      const applied: string[] = [];
      const remember = () => {
        if (hasSnapshot) return;
        pushUndo();
        hasSnapshot = true;
      };
      const splitAtTimeline = (time: number) => {
        const layout = timelineLayout(nextSegments);
        const index = nextSegments.findIndex((segment, i) => time > layout.starts[i] + .12 && time < layout.starts[i] + (segment.end - segment.start) - .12);
        if (index < 0) return null;
        const splitAt = nextSegments[index].start + (time - layout.starts[index]);
        const split = splitItemsAtSourceTime(nextSegments, index, splitAt);
        if (!split) return null;
        nextSegments.splice(0, nextSegments.length, ...split.items);
        selectedId = split.rightId; nextPlayhead = splitAt; changed = true;
        return { index, leftId: split.leftId, rightId: split.rightId, at: time };
      };

      const operations = Array.isArray(plan.operations) ? plan.operations : [];
      if (!operations.length) setAgentProgress({ phase: "План готов", percent: 100, step: 0, total: 0 });
      for (const [opIndex, op] of operations.entries()) {
        setAgentProgress({ phase: agentOperationLabel(op), percent: Math.min(96, Math.round(12 + 80 * opIndex / Math.max(1, operations.length))),
          step: opIndex + 1, total: operations.length });
        if (op.type === "split") {
          remember();
          const split = splitAtTimeline(Number(op.at_seconds));
          if (split) applied.push(`Разрезан фрагмент на ${fmt(Number(op.at_seconds))}.`);
          else applied.push(`Разрез не создан: точка ${fmt(Number(op.at_seconds))} должна быть внутри выбранного видео, не ближе 0,12 с к краю.`);
        } else if (op.type === "transition") {
          let index = Number.isInteger(op.target_index) ? Number(op.target_index) : nextSegments.findIndex((segment) => segment.id === selectedId);
          if (op.after_seconds !== undefined) {
            const time = Number(op.after_seconds);
            const layout = timelineLayout(nextSegments);
            index = nextSegments.findIndex((segment, i) => i < nextSegments.length - 1 && Math.abs(layout.starts[i] + (segment.end - segment.start) - time) < .22);
            if (index < 0) {
              const split = splitAtTimeline(time);
              index = split?.index ?? -1;
            }
          }
          if (index < 0 || index >= nextSegments.length - 1) {
            applied.push("Переход не добавлен: нужен стык между двумя фрагментами. Разрежьте клип в месте перехода или выберите стык.");
          } else {
            remember();
            const style = ["dissolve", "fade", "fadeblack", "wipeleft", "wiperight", "slideright", "slideleft", "circleopen", "smoothleft"].includes(op.transition_type) ? op.transition_type : "dissolve";
            nextSegments[index] = { ...nextSegments[index], transitionAfter: { type: style, duration: Math.max(.15, Math.min(2, Number(op.duration) || .7)) } };
            changed = true;
            applied.push(`На стыке ${index + 1}–${index + 2} установлен переход «${style === "dissolve" ? "плавное растворение" : style}", ${Number(op.duration || .7).toFixed(1)} с.`);
          }
        } else if (op.type === "trim") {
          const index = nextSegments.findIndex((segment) => segment.id === selectedId);
          if (index >= 0) {
            const item = nextSegments[index], src = sources.find((source) => source.id === item.source);
            const start = Math.max(0, Math.min(src?.duration || item.end, Number(op.start_seconds)));
            const end = Math.max(start + .1, Math.min(src?.duration || item.end, Number(op.end_seconds)));
            if (end > start + .1) { remember(); nextSegments[index] = { ...item, start, end }; changed = true; nextPlayhead = start; applied.push(`Границы выбранного фрагмента выставлены на ${fmt(start)}–${fmt(end)}.`); }
          }
        } else if (op.type === "color") {
          remember(); nextGrade = { brightness: Number(op.brightness), contrast: Number(op.contrast), saturation: Number(op.saturation), temperature: Number(op.temperature), tint: Number(op.tint) };
          changed = true; applied.push("Цветокоррекция обновлена в предпросмотре и для экспорта.");
        } else if (op.type === "volume") {
          remember();
          if (op.original_volume !== undefined) nextVolume = Math.max(0, Math.min(2, Number(op.original_volume)));
          if (op.music_volume !== undefined) nextMusicVolume = Math.max(0, Math.min(.6, Number(op.music_volume)));
          changed = true; applied.push(`Громкость обновлена: оригинал ${Math.round(nextVolume * 100)}%, музыка ${Math.round(nextMusicVolume * 100)}%.`);
        } else if (op.type === "duck_music") {
          remember(); nextDuckMusic = op.enabled !== false; changed = true;
          applied.push(nextDuckMusic ? "Автоприглушение музыки во время речи включено." : "Автоприглушение музыки выключено.");
        } else if (op.type === "audio_track") {
          const track = op.audio_track as AudioTrackName;
          const mode = op.track_mode as "mute" | "solo";
          if (!(track in nextAudioMutes) || !["mute", "solo"].includes(mode)) {
            applied.push("Команда управления дорожкой не распознана.");
            continue;
          }
          const enabled = op.enabled !== false;
          const state = mode === "mute" ? nextAudioMutes : nextAudioSolos;
          if (state[track] === enabled) {
            applied.push(`Дорожка ${track === "original" ? "A1 · оригинал" : track === "music" ? "A2 · музыка" : "A3 · SFX"}: режим уже установлен.`);
            continue;
          }
          remember();
          if (mode === "mute") nextAudioMutes = { ...nextAudioMutes, [track]: enabled };
          else nextAudioSolos = { ...nextAudioSolos, [track]: enabled };
          changed = true;
          const trackLabel = track === "original" ? "A1 · оригинал" : track === "music" ? "A2 · музыка" : "A3 · SFX";
          applied.push(`${trackLabel}: ${mode === "mute" ? (enabled ? "звук выключен" : "звук включён") : (enabled ? "оставлена в Solo" : "Solo снят")}.`);
        } else if (op.type === "format") {
          remember(); nextAspect = op.aspect; changed = true; applied.push(`Формат кадра изменён: ${op.aspect === "9:16" ? "вертикальный 9:16" : op.aspect}.`);
        } else if (op.type === "remove_pauses") {
          const usedSources = nextSources.filter((source) => nextSegments.some((segment) => segment.source === source.id));
          if (!usedSources.length) { applied.push("Чтобы убрать паузы, сначала добавьте видео на шкалу."); continue; }
          remember();
          const hasMissingWords = usedSources.some((source) => !nextCaptions[source.id]?.words?.length);
          if (hasMissingWords) {
            const transcript = await run("/api/tools/video_transcribe", { sources: usedSources.map(({ id, path }) => ({ id, path })) });
            const wordsBySource = transcript?.status === "done" ? (transcript.result as any)?.captions as SourceCaptions | undefined : undefined;
            if (wordsBySource) { nextCaptions = { ...nextCaptions, ...wordsBySource }; captionsUpdated = true; }
          }
          const edited: Segment[] = [];
          let pauseCuts = 0;
          for (const segment of nextSegments) {
            const words = (nextCaptions[segment.source]?.words || []).filter((word) =>
              word.end > segment.start && word.start < segment.end).sort((a, b) => a.start - b.start);
            if (!words.length) { edited.push(segment); continue; }
            let groupStart = Math.max(segment.start, words[0].start - .12), groupEnd = Math.min(segment.end, words[0].end + .22);
            const groups: Array<[number, number]> = [];
            for (const word of words.slice(1)) {
              if (word.start - groupEnd > 1.05) { groups.push([groupStart, groupEnd]); groupStart = Math.max(segment.start, word.start - .12); }
              groupEnd = Math.min(segment.end, Math.max(groupEnd, word.end + .22));
            }
            groups.push([groupStart, groupEnd]);
            const kept = groups.filter(([start, end]) => end - start >= .25);
            if (!kept.length) { edited.push(segment); continue; }
            if (kept.length < 2) { edited.push(segment); continue; }
            pauseCuts += kept.length - 1;
            for (const [index, [start, end]] of kept.entries()) edited.push({ ...segment,
              id: index === 0 ? segment.id : key(), start, end,
              transitionAfter: index === kept.length - 1 ? segment.transitionAfter : undefined });
          }
          if (edited.length <= 80 && pauseCuts > 0) {
            const oldDuration = nextSegments.reduce((total, segment) => total + segment.end - segment.start, 0);
            const newDuration = edited.reduce((total, segment) => total + segment.end - segment.start, 0);
            nextSegments.splice(0, nextSegments.length, ...edited); changed = true;
            applied.push(`Сокращены длинные паузы по локальной расшифровке; оставлено ${fmt(newDuration)}, убрано примерно ${fmt(Math.max(0, oldDuration - newDuration))}. Изменение редактируется обычными границами клипов.`);
          } else applied.push(edited.length > 80
            ? "Получилось слишком много речевых фрагментов; таймлайн не изменён. Сначала выделите нужную часть видео."
            : "Длинные паузы не найдены либо речь пока не распознана. Таймлайн не менял.");
        } else if (op.type === "upscale" || op.type === "interpolate") {
          const target = nextSegments.find((segment) => segment.id === selectedId) || activeSegment;
          const source = nextSources.find((item) => item.id === target?.source);
          if (!source) { applied.push("Для обработки сначала добавьте исходное видео на шкалу."); continue; }
          remember();
          const isUpscale = op.type === "upscale";
          const done = await run(isUpscale ? "/api/tools/video_upscale" : "/api/tools/interpolate", isUpscale
            ? { video: source.path, quality: "balanced", target_height: Number(op.target_height) || 1080 }
            : { video: source.path, multiplier: Number(op.multiplier) || 2, target_fps: Number(op.target_fps) || undefined });
          const toolResult = done?.result, output = toolResult?.outputs?.[0];
          if (done?.status === "done" && toolResult && output) {
            nextSources = nextSources.map((item) => item.id === source.id ? { ...item, path: output,
              width: Number(toolResult.width) || item.width, height: Number(toolResult.height) || item.height } : item);
            if (!isUpscale && Number(toolResult.fps)) nextFps = String(Number(toolResult.fps));
            changed = true;
            applied.push(isUpscale ? `SeedVR2 завершил локальный upscale исходника до ${toolResult.height || Number(op.target_height) || 1080}p; монтажные диапазоны сохранены.`
              : `RIFE создал версию ${Number(toolResult.fps) || "с повышенной частотой"} FPS; монтажные диапазоны сохранены.`);
          } else applied.push(isUpscale ? "Upscale не завершился; проверьте сообщение задачи." : "Интерполяция RIFE не завершилась; проверьте сообщение задачи.");
        } else if (op.type === "face_blur") {
          const target = nextSegments.find((segment) => segment.id === selectedId) || activeSegment;
          const source = nextSources.find((item) => item.id === target?.source);
          if (!source) { applied.push("Чтобы размыть лица, сначала добавьте видео и клип на таймлайн."); continue; }
          if (!faceBlurReady) { applied.push("Для локального размытия установите YuNet: кнопка установки доступна в панели AI‑монтажёра."); continue; }
          remember();
          const done = await run("/api/tools/video_face_blur", { video: source.path, mode: String(op.mode || "gaussian") });
          const output = done?.result?.outputs?.[0];
          if (done?.status === "done" && output) {
            nextSources = nextSources.map((item) => item.id === source.id ? { ...item, path: output } : item);
            changed = true;
            applied.push("Лица размыты в исходнике локально. Результат подставлен во все его фрагменты; проверьте быстрые повороты и мелкие лица.");
          } else applied.push("Размытие лиц не завершилось. Проверьте состояние задачи и исходное видео.");
        } else if (op.type === "captions") {
          remember(); setCaptionStyle(String(op.caption_style || "creator"));
          setCaptionAnimation(String(op.caption_animation || "none")); setCaptionHighlight(op.caption_highlight !== false);
          setPanel("captions"); changed = true;
          const missing = nextSegments.some((segment) => !sourceCaptions[segment.source]);
          if (missing) {
            const transcript = await transcribeTimeline();
            applied.push(transcript ? "Субтитры расшифрованы Whisper и стиль применён." : "Стиль выбран. Чтобы получить субтитры, нажмите «Создать автоматически».");
          } else applied.push("Стиль субтитров обновлён для предпросмотра и экспорта.");
        } else if (op.type === "sfx") {
          setPanel("audio");
          if (!sfxReady) applied.push("Запрос понял. Сначала установите модель Stable Audio 3 Small-SFX во вкладке «Звук».");
          else {
            const effectPrompt = String(op.effect_prompt || prompt).slice(0, 400);
            const done = await run("/api/tools/audio_sfx", { prompt: effectPrompt, duration: Number(op.duration) || 4 });
            if (done?.status === "done" && done.result?.outputs?.[0]) {
              const effectDuration = Number(done.result.duration) || Number(op.duration) || 4;
              remember();
              const wantsBestMoments = /лучших местах|удачных местах|на стыках|best (?:moments|places)|at transitions/i.test(prompt);
              const layout = timelineLayout(nextSegments);
              const positions = wantsBestMoments ? layout.starts.slice(1).slice(0, 6)
                : [Number(op.at_seconds) || timelinePosition];
              const placements = positions.length ? positions : [Number(op.at_seconds) || timelinePosition];
              setSfxClips((all) => [...all, ...placements.map((at) => ({ id: key(), path: done.result!.outputs[0], at,
                duration: effectDuration, volume: .24, prompt: effectPrompt }))]);
              applied.push(wantsBestMoments && placements.length > 1
                ? `Звуковой эффект создан и размещён на ${placements.length} стыках видео на отдельной дорожке SFX.`
                : "Звуковой эффект создан и размещён на отдельной дорожке SFX.");
            } else applied.push("Эффект не создан. Проверьте состояние задачи.");
          }
        } else if (op.type === "analyze") {
          const target = nextSegments.find((segment) => segment.id === selectedId) || activeSegment;
          const source = sources.find((item) => item.id === target?.source);
          if (source) {
            setPanel("analyze"); setAnalysis(null);
            const done = await run("/api/tools/video_analyze", { video: source.path });
            if (done?.status === "done") { setAnalysis((done.result as any)?.analysis ?? null); applied.push("Анализ выбранного видео завершён и показан во вкладке «Анализ»."); }
            else applied.push("Анализ не завершился. Проверьте сообщение о задаче в редакторе.");
          } else applied.push("Для анализа сначала добавьте видео в медиатеку.");
        } else if (op.type === "scene") {
          const target = nextSegments.find((segment) => segment.id === selectedId) || activeSegment;
          const source = sources.find((item) => item.id === target?.source);
          const scenePrompt = String(op.scene_prompt || prompt).slice(0, 500);
          setEditPrompt(scenePrompt); setPanel("ai");
          if (!target || !source) { applied.push("Выберите видео, чтобы запустить AI‑правку сцены."); continue; }
          if (editModel === "vace" && !vaceReady) { applied.push("Запрос сцены заполнен, но Wan VACE не установлен. Установите модель или выберите доступную."); continue; }
          const start = Math.max(target.start, Math.min(target.end - .5, playhead));
          const duration = Math.max(.5, Math.min(5, target.end - start));
          const done = await run("/api/tools/video_edit", { video: source.path, image: ref || undefined, prompt: scenePrompt,
            task, preset, model: editModel, start, duration, seed: seed === "" ? -1 : seed });
          if (done?.status === "done" && done.result?.outputs?.[0]) {
            setResult(done.result.outputs[0]); setResultQa(null);
            const resultPath = done.result.outputs[0], resultDuration = Number(done.result.duration) || duration;
            const generated = { id: key(), source: key(), start: 0, end: resultDuration };
            setSources((all) => [...all, { id: generated.source, path: resultPath, duration: resultDuration }]);
            const latestIndex = nextSegments.findIndex((segment) => segment.id === target.id);
            if (latestIndex >= 0) {
              remember();
              const original = nextSegments[latestIndex], before = start - original.start, after = original.end - (start + duration), replacement: Segment[] = [];
              if (before > .12) replacement.push({ ...original, end: start, transitionAfter: undefined });
              replacement.push(generated);
              if (after > .12) replacement.push({ ...original, id: key(), start: start + duration });
              nextSegments.splice(latestIndex, 1, ...replacement); selectedId = generated.id; nextPlayhead = 0; changed = true;
            }
            applied.push("AI‑фрагмент создан и добавлен на монтажную шкалу; исходный фрагмент сохранён до и после него.");
          } else applied.push("AI‑правка сцены не завершилась.");
        }
      }
      if (changed) {
        setSegments(nextSegments); setSources(nextSources); sourcesRef.current = nextSources;
        if (captionsUpdated) setSourceCaptions(nextCaptions);
        setGrade(nextGrade); setAspect(nextAspect); setFps(nextFps); setVolume(nextVolume); setMusicVolume(nextMusicVolume); setDuckMusic(nextDuckMusic);
        setAudioMutes(nextAudioMutes); setAudioSolos(nextAudioSolos);
        if (selectedId) setSelected(selectedId);
        if (nextPlayhead !== null) setPlayhead(nextPlayhead);
      }
      const response = [plan.reply, ...applied].filter(Boolean).join("\n");
      setAssistantNote(response); setChatMessages((all) => [...all, { role: "assistant", text: response || "Уточните, что именно нужно изменить в проекте." }]);
      setAgentProgress({ phase: "Команда завершена", percent: 100, step: operations.length, total: operations.length });
    } catch (e: any) {
      const message = e.message || "AI‑помощник недоступен.";
      setAssistantNote(message); setChatMessages((all) => [...all, { role: "assistant", text: message }]);
      setAgentProgress({ phase: "Не удалось выполнить команду", percent: 100, step: 0, total: 0, error: true });
    } finally { setAssistantBusy(false); }
  };
  const undoAssistantEdit = () => {
    if (!undoStack.length) return;
    const previous = undoStack[undoStack.length - 1];
    setRedoStack((all) => [...all, captureSnapshot()].slice(-30));
    setUndoStack((all) => all.slice(0, -1)); restoreSnapshot(previous);
    setAssistantNote("Последние изменения отменены.");
    setChatMessages((all) => [...all, { role: "assistant", text: "Последние изменения отменены." }]);
  };
  const redoAssistantEdit = () => {
    if (!redoStack.length) return;
    const next = redoStack[redoStack.length - 1];
    setUndoStack((all) => [...all, captureSnapshot()].slice(-30));
    setRedoStack((all) => all.slice(0, -1)); restoreSnapshot(next);
    setAssistantNote("Изменение восстановлено.");
    setChatMessages((all) => [...all, { role: "assistant", text: "Изменение восстановлено." }]);
  };
  const snapshotBeforeDirectEdit = () => {
    pushUndo();
  };
  const toggleAudioTrack = (track: AudioTrackName, mode: "mute" | "solo") => {
    const current = mode === "mute" ? audioMutes[track] : audioSolos[track];
    pushUndo();
    if (mode === "mute") setAudioMutes((all) => ({ ...all, [track]: !current }));
    else setAudioSolos((all) => ({ ...all, [track]: !current }));
  };
  const audioTrackLabel = (track: AudioTrackName, code: string, label: string) => <div className="editor-audio-track-label">
    <span>{code} · {label}</span>
    <div className="editor-audio-track-buttons">
      <button type="button" className={`editor-track-toggle mute ${audioMutes[track] ? "active" : ""}`}
        aria-label={`${audioMutes[track] ? "Включить" : "Выключить"} звук дорожки ${code} ${label}`} aria-pressed={audioMutes[track]}
        title={audioMutes[track] ? "Включить дорожку" : "Отключить дорожку"} onClick={() => toggleAudioTrack(track, "mute")}>M</button>
      <button type="button" className={`editor-track-toggle solo ${audioSolos[track] ? "active" : ""}`}
        aria-label={`${audioSolos[track] ? "Снять Solo с" : "Оставить только"} дорожки ${code} ${label}`} aria-pressed={audioSolos[track]}
        title={audioSolos[track] ? "Снять Solo" : "Прослушать только эту дорожку"} onClick={() => toggleAudioTrack(track, "solo")}>S</button>
    </div>
  </div>;
  const renderTimeline = async () => {
    if (renderInProgress) return;
    const captionWords = segments.flatMap((segment, index) => {
      const sourceWords = sourceCaptions[segment.source]?.words || [];
      const timelineStart = timeline.starts[index] || 0;
      return sourceWords.flatMap((word) => {
        const start = Math.max(segment.start, word.start), end = Math.min(segment.end, word.end);
        return end > start ? [{ start: timelineStart + start - segment.start, end: timelineStart + end - segment.start, word: word.word }] : [];
      });
    });
    setRenderInProgress(true);
    try {
      const done = await run("/api/tools/video_timeline_render", {
      sources: sources.map(({ id, path }) => ({ id, path })), segments: segments.map(({ id, source, start, end, transitionAfter }) => ({ id, source, start, end, transition_after: transitionAfter })),
      broll: brollClips.map(({ source, start, end, at, scale }) => ({ path: sources.find((item) => item.id === source)?.path, start, end, at, scale })),
      grade, aspect, fps, volume: audioTrackAudible("original") ? volume : 0, fade,
      normalize_audio: normalizeAudio, subject_follow: subjectFollow, overlay, overlay_position: overlayPosition,
      music: audioTrackAudible("music") ? music || undefined : undefined, music_volume: musicVolume,
      duck_music: duckMusic && audioTrackAudible("original"),
      sfx: audioTrackAudible("sfx") ? sfxClips.map(({ path, at, volume: gain }) => ({ path, at, volume: gain })) : [], caption_words: captionWords,
      caption_language: sources.map((source) => sourceCaptions[source.id]?.language).find(Boolean) || "ru",
      caption_style: captionStyle, caption_animation: captionAnimation, caption_position: captionPosition, caption_words_per_line: captionWordsPerLine,
      caption_highlight: captionHighlight, caption_text_color: captionTextColor, caption_highlight_color: captionHighlightColor,
      assistant_note: assistantNote,
      });
      if (done?.status === "done" && done.result?.outputs?.[0]) {
        setResult(done.result.outputs[0]);
        const quality = (done.result as any)?.quality ?? null;
        setResultQa(quality);
        const reframe = (done.result as any)?.reframe || quality?.reframe || {};
        setSubjectTracks(cleanSubjectTracks(reframe.source_tracks));
        const reframeNote = Number(reframe.tracked_sources || 0) > 0
          ? `Кадрирование удерживало лицо по ${Number(reframe.tracked_samples || 0)} опорным точкам.`
          : Number(reframe.fallback_sources || 0) > 0 ? "Для части видео использован центральный crop; лицо не удалось отследить." : "";
        const visualNotes = Number(quality?.visual_review?.notes?.length || 0);
        const qaNote = quality?.passed
          ? !quality?.visual_review?.available ? "Техническая проверка прошла. Проверьте контрольные кадры вручную."
            : visualNotes ? `Техническая проверка прошла. ИИ отметил возможные визуальные замечания (${visualNotes}).` : "Техническая проверка прошла; на просмотренных кадрах ИИ явных проблем не отметил."
          : "Экспорт создан. Проверьте замечания автоматической проверки.";
        setAssistantNote([reframeNote, qaNote].filter(Boolean).join(" "));
      }
    } finally {
      setRenderInProgress(false);
    }
  };
  const generateScene = async () => {
    if (!activeSegment || !activeSource) return;
    const start = Math.max(activeSegment.start, Math.min(activeSegment.end - .5, editStart));
    const duration = Math.max(.5, Math.min(5, activeSegment.end - start, editDuration));
    const done = await run("/api/tools/video_edit", { video: activeSource.path, image: ref || undefined, prompt: editPrompt,
      task, preset, model: editModel, start, duration, seed: seed === "" ? -1 : seed });
    if (done?.status === "done" && done.result?.outputs?.[0]) {
      const resultPath = done.result.outputs[0], resultDuration = Number(done.result.duration) || duration;
      setResult(resultPath); setResultQa(null);
      snapshotBeforeDirectEdit();
      const generated = { id: key(), source: key(), start: 0, end: resultDuration };
      setSources((all) => [...all, { id: generated.source, path: resultPath, duration: resultDuration }]);
      const index = segments.findIndex((segment) => segment.id === activeSegment.id);
      const before = start - activeSegment.start, after = activeSegment.end - (start + duration), replacement: Segment[] = [];
      if (before > .12) replacement.push({ ...activeSegment, end: start, transitionAfter: undefined });
      replacement.push(generated);
      if (after > .12) replacement.push({ ...activeSegment, id: key(), start: start + duration });
      if (index >= 0) setSegments((all) => [...all.slice(0, index), ...replacement, ...all.slice(index + 1)]);
      setSelected(generated.id); setPlayhead(0);
      setAssistantNote("AI‑фрагмент добавлен на монтажную шкалу; исходное видео сохранено до и после него.");
    }
  };
  const analyze = async () => {
    if (!activeSource) return;
    const sourcePath = activeSource.path;
    setAnalysis(null);
    setAnalysisSourcePath(null);
    const done = await run("/api/tools/video_analyze", { video: sourcePath, purpose: "editor" });
    if (done?.status === "done") {
      const value = (done.result as any)?.analysis ?? null;
      setAnalysis(value); setAnalysisSourcePath(value ? sourcePath : null);
    }
  };

  const togglePlayback = () => {
    if (!player.current) return;
    if (player.current.paused) void player.current.play().catch(() => setPlaying(false));
    else player.current.pause();
  };
  const jump = (delta: number) => {
    if (activeSegment) seekTo(Math.max(activeSegment.start, Math.min(activeSegment.end, playhead + delta)));
  };
  const enterFullscreen = () => { if (player.current?.requestFullscreen) void player.current.requestFullscreen(); };
  const panelTabs = [
    { id: "edit", label: "Монтаж", icon: Settings2 },
    { id: "color", label: "Цвет", icon: Palette },
    { id: "captions", label: "Титры", icon: Captions },
    { id: "audio", label: "Звук", icon: AudioLines },
    { id: "ai", label: "AI‑чат", icon: MessageCircle },
    { id: "analyze", label: "Анализ", icon: Eye },
    { id: "export", label: "Экспорт", icon: Download },
  ] as const;
  const rememberProjectSummary = (summary: ProjectSummary) => setProjectLibrary((all) => [summary, ...all.filter((item) => item.id !== summary.id)]
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || ""))));
  const saveProjectNow = async () => {
    const document = buildProjectDocument();
    let browserCopySaved = false;
    try {
      localStorage.setItem(PROJECT_STORAGE_KEY, JSON.stringify(document)); browserCopySaved = true;
    } catch { /* continue saving the named project on the LocalAI server */ }
    try {
      const summary = await api.put<ProjectSummary>(`/api/editor/projects/${encodeURIComponent(projectId)}`, { name: document.name, document });
      rememberProjectSummary(summary); setLastProjectSave(Date.now()); setProjectSaveState(browserCopySaved ? "saved" : "server-only");
      return true;
    } catch {
      setProjectSaveState(browserCopySaved ? "local-only" : "error");
      return false;
    }
  };
  const openSavedProject = async (id: string) => {
    if (!id || id === projectId) return;
    await saveProjectNow();
    try {
      const saved = await api.get<any>(`/api/editor/projects/${encodeURIComponent(id)}`);
      restoreProjectDocument(saved, id);
      setProjectSaveState("saved"); setAssistantNote(`Открыт проект «${saved.name || saved.document?.name || "Без названия"}».`);
    } catch (error: any) {
      setAssistantNote(error.message || "Не удалось открыть проект.");
    }
  };
  const createProject = async () => {
    await saveProjectNow();
    sourcesRef.current = []; setSources([]); setSegments([]); setBrollClips([]); setSelected(""); setVideo(null);
    setMusic(null); setOverlay(""); setSourceCaptions({}); setSfxClips([]); setAnalysis(null); setAnalysisSourcePath(null);
    setUndoStack([]); setRedoStack([]); setGrade(defaultGrade); setAspect("source"); setFps("source"); setVolume(1);
    setMusicVolume(.12); setDuckMusic(true); setFade(.25); setNormalizeAudio(false); setCaptionStyle("creator"); setCaptionAnimation("pop");
    setCaptionPosition("bottom"); setCaptionWordsPerLine(3); setCaptionHighlight(true); setCaptionTextColor("FFFFFF");
    setCaptionHighlightColor("FFD84D"); setChatMessages([{ role: "assistant", text: "Я AI‑монтажёр этого проекта. Импортируйте видео — начну локальный анализ и соберу черновой монтаж." }]);
    setProjectId(freshProjectId()); setProjectName("Новый проект"); setProjectSaveState("saving");
    setAssistantNote("Создан новый проект. Импортируйте исходное видео, чтобы начать.");
  };
  const exportProjectFile = () => {
    const projectDoc = buildProjectDocument();
    const blob = new Blob([JSON.stringify(projectDoc, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), anchor = window.document.createElement("a");
    anchor.href = url; anchor.download = `${(projectName || "localai-project").replace(/[<>:"/\\|?*]+/g, "_").slice(0, 80)}.laiproject`;
    anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const importProjectFile = async (file?: File) => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const copyId = freshProjectId();
      restoreProjectDocument(parsed, copyId);
      const baseName = String(parsed.name || parsed.document?.name || file.name.replace(/\.laiproject$/i, "")).slice(0, 110);
      setProjectName(baseName);
      setProjectSaveState("saving"); setAssistantNote("Переносимый проект импортирован. Проверяю привязку исходников…");
    } catch (error: any) {
      setAssistantNote(error.message || "Не удалось импортировать файл проекта.");
    }
  };
  const relinkMedia = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0], target = relinkTarget.current;
    event.target.value = "";
    if (!file || !target) return;
    if (target.kind === "video" && !file.type.startsWith("video/")) { setAssistantNote("Для перепривязки выберите видеофайл."); return; }
    if (target.kind !== "video" && !file.type.startsWith("audio/")) { setAssistantNote("Для перепривязки выберите аудиофайл."); return; }
    try {
      const uploaded = await api.upload(file);
      if (target.kind === "video") {
        const previous = sourcesRef.current.find((source) => source.id === target.id);
        if (!previous) return;
        const nextSources = sourcesRef.current.map((source) => source.id === target.id
          ? { ...source, path: uploaded.path, duration: 0, width: undefined, height: undefined } : source);
        sourcesRef.current = nextSources; setSources(nextSources);
        setVideo((value) => value === previous.path ? uploaded.path : value);
        setSourceCaptions((all) => { const next = { ...all }; delete next[target.id]; return next; });
        if (analysisSourcePath === previous.path) { setAnalysis(null); setAnalysisSourcePath(null); }
        setAssistantNote(`Исходник «${file.name}» перепривязан. Таймкод и монтаж сохранены; проверьте диапазоны, если длительность отличается.`);
      } else if (target.kind === "music") {
        setMusic(uploaded.path); setAssistantNote(`Фоновая музыка «${file.name}» перепривязана.`);
      } else {
        setSfxClips((all) => all.map((clip) => clip.id === target.id ? { ...clip, path: uploaded.path } : clip));
        setAssistantNote(`Звуковой эффект «${file.name}» перепривязан.`);
      }
    } catch (error: any) {
      setAssistantNote(error.message || "Не удалось загрузить выбранный исходник.");
    } finally { relinkTarget.current = null; }
  };

  return (
    <div className="video-editor-page">
      <header className="editor-topbar">
        <div className="editor-top-left">
          <button className="editor-back" title="Назад к клипам" onClick={() => go("video/clips")}><ArrowLeft size={18} /></button>
          <div className="editor-mark"><Film size={17} /></div>
          <div className="editor-project-name">
            <b>Видеоредактор</b>
          <input className="editor-project-title" aria-label="Название проекта" value={projectName} disabled={!!running || assistantBusy}
            onChange={(event) => setProjectName(event.target.value.slice(0, 120))} onBlur={() => void saveProjectNow()} />
          </div>
        </div>
        <div className="editor-top-status" aria-live="polite"><span className="editor-status-dot" />
          {projectSaveState === "saving" ? "Сохраняю проект…"
            : projectSaveState === "error" ? "Не удалось сохранить проект"
              : projectSaveState === "local-only" ? "Сохранено в браузере · LocalAI недоступен"
                : projectSaveState === "server-only" ? "Сохранено на LocalAI · хранилище браузера заполнено"
                  : lastProjectSave ? `Проект сохранён · ${new Date(lastProjectSave).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
                    : "Локальное автосохранение"}
        </div>
        <div className="editor-top-actions">
          <select className="editor-project-select" aria-label="Открыть сохранённый проект" value="" disabled={!!running || assistantBusy}
            onChange={(event) => { if (event.target.value) void openSavedProject(event.target.value); }}>
            <option value="">Открыть проект…</option>
            {projectLibrary.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.source_count} видео</option>)}
          </select>
          <button className="btn editor-top-button" title="Создать проект" disabled={!!running || assistantBusy} onClick={() => void createProject()}><FilePlus size={14} />Новый</button>
          <button className="btn editor-top-button" title="Сохранить проект сейчас" disabled={!!running || assistantBusy} onClick={() => void saveProjectNow()}><Save size={14} />Сохранить</button>
          <button className="btn editor-top-button" title="Экспортировать переносимый файл проекта" disabled={!!running || assistantBusy} onClick={exportProjectFile}><Download size={14} />Файл</button>
          <button className="btn editor-top-button" title="Импортировать файл проекта" disabled={!!running || assistantBusy} onClick={() => projectFileInput.current?.click()}><Upload size={14} />Импорт</button>
          <button className="btn editor-top-button" title="Отменить последнее изменение" disabled={!undoStack.length} onClick={undoAssistantEdit}><RotateCcw size={14} />Отменить</button>
          <button className="btn editor-top-button" title="Повторить отменённое изменение" disabled={!redoStack.length} onClick={redoAssistantEdit}><RotateCcw size={14} style={{ transform: "scaleX(-1)" }} />Повторить</button>
          <button className="btn primary editor-export-top" title="Экспортировать итоговое видео" disabled={!!running || !sources.length || !segments.length} onClick={renderTimeline}>
            <Download size={15} />{running ? "Экспорт…" : "Экспортировать"}
          </button>
        </div>
        <input ref={projectFileInput} type="file" accept=".laiproject,.json,application/json" hidden
          onChange={(event) => { void importProjectFile(event.target.files?.[0]); event.target.value = ""; }} />
        <input ref={relinkMediaInput} type="file" accept="video/*,audio/*,.mp4,.mov,.webm,.wav,.mp3,.m4a,.flac,.ogg" hidden onChange={(event) => { void relinkMedia(event); }} />
      </header>

      <div className="editor-workbench">
        <aside className="editor-library">
          <div className="editor-pane-heading"><div><b>Медиатека</b><span>{sources.length} {sourceNoun}</span></div></div>
          <DropZone accept="video/*" value={null} onChange={(p) => p && importVideo(p)} label="Импортировать видео · автомонтаж" kind="media" />
          <div className="editor-media-list">
            {sources.map((s) => <div className={"editor-media-row " + (activeSource?.id === s.id ? "active" : "")} key={s.id}>
              <div className="editor-media-thumb"><Film size={17} /></div>
              <button className="editor-media-name" title={s.path} onClick={() => {
                const first = segments.find((segment) => segment.source === s.id);
                  if (first) { setSelected(first.id); setPlayhead(first.start); }
                  else addSourceToTimeline(s);
                }}>
                  <b>{s.path.split(/[\\/]/).pop()}</b><span>{missingMedia.includes(s.path) ? "Файл не найден" : s.duration ? fmt(s.duration) : "Загрузка…"}</span>
                </button>
              {missingMedia.includes(s.path) && <button className="btn sm icon" title="Найти исходное видео и перепривязать" onClick={() => {
                relinkTarget.current = { kind: "video", id: s.id }; relinkMediaInput.current?.click();
              }}><Upload size={14} /></button>}
              <button className="btn sm icon" title="Добавить на монтажную шкалу V1" disabled={!s.duration} onClick={() => addSourceToTimeline(s)}><Plus size={15} /></button>
              <button className="btn sm icon" title="Добавить как B-roll на V2" disabled={!s.duration || !timelineDuration} onClick={() => addBrollToTimeline(s)}><Layers size={14} /></button>
            </div>)}
          </div>
          {missingMedia.length > 0 && <div className="editor-missing-media" role="status">
            Не найдены медиафайлы: {missingMedia.length}. Для видео, музыки и SFX можно перепривязать файл в соответствующей строке.
          </div>}
          {sources.map((s) => <video key={"meta-" + s.id} src={fileUrl(s.path)} muted preload="metadata" style={{ display: "none" }}
            onLoadedMetadata={(e) => setSourceInfo(s.id, e)} />)}
          {music && <audio ref={musicPreviewPlayer} src={fileUrl(music)} preload="auto" loop hidden onLoadedMetadata={syncPreviewAudio} />}
          {sfxClips.map((clip) => <audio key={clip.id} ref={(element) => {
            if (element) sfxPreviewPlayers.current.set(clip.id, element);
            else sfxPreviewPlayers.current.delete(clip.id);
          }} src={fileUrl(clip.path)} preload="auto" hidden onLoadedMetadata={syncPreviewAudio} />)}
          {!sources.length && <div className="editor-empty-media">Импортируйте видео, чтобы начать монтаж. Файлы остаются на этом компьютере.</div>}
          <div className="editor-library-tip"><Sparkles size={14} />Несколько исходников можно собрать в одном проекте.</div>
        </aside>

        <section className="editor-center">
          <section className="editor-monitor">
            <div className="editor-monitor-toolbar">
              <div><b>Предпросмотр</b><span>{activeSource && activeSegment ? fmt(playhead) + " / " + fmt(activeSegment.end) : "Добавьте видео"}</span></div>
              <button className="btn sm icon" title="На весь экран" onClick={enterFullscreen} disabled={!activeSource}><Maximize2 size={15} /></button>
            </div>
            <div className="editor-monitor-canvas">
              {activeSource ? <div className="editor-preview-frame" style={previewFrameStyle}>
                <video ref={player} className={`editor-source-preview ${maskPickMode ? "editor-mask-pick" : ""}`} src={fileUrl(activeSource.path)} key={activeSource.id} playsInline
                  style={primaryPreviewStyle} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
                  onClick={(event) => {
                    if (!maskPickMode) return;
                    const rect = event.currentTarget.getBoundingClientRect();
                    const x = (event.clientX - rect.left) / rect.width, y = (event.clientY - rect.top) / rect.height;
                    if (x < 0 || x > 1 || y < 0 || y > 1) return;
                    setMaskPoint({ x, y }); setMaskSeedTime(event.currentTarget.currentTime); setMaskPickMode(false);
                  }}
                  onTimeUpdate={(e) => {
                    const t = e.currentTarget.currentTime; setPlayhead(t);
                    if (activeSegment && t >= activeSegment.end) {
                      e.currentTarget.pause(); e.currentTarget.currentTime = activeSegment.start; setPlayhead(activeSegment.start);
                    }
                  }}
                  onLoadedMetadata={() => {
                    if (!activeSegment || !player.current) return;
                    const pending = pendingTimelineSeek.current;
                    const target = pending?.sourceId === activeSource.id ? pending.time : activeSegment.start;
                    if (pending?.sourceId === activeSource.id) pendingTimelineSeek.current = null;
                    player.current.currentTime = target; setPlayhead(target);
                  }} />
                {previewTransition && previewTransitionSource && !maskPickMode && <video className="editor-transition-preview"
                  data-transition-type={previewTransition.type} data-transition-role={transitionSecondaryRole}
                  ref={transitionPlayer} src={fileUrl(previewTransitionSource.path)} key={`${previewTransition.otherSource}-${previewTransition.type}`}
                  playsInline muted={!audioTrackAudible("original")} style={secondaryPreviewStyle}
                  onLoadedMetadata={(event) => {
                    event.currentTarget.currentTime = previewTransition.otherTime; syncPreviewAudio();
                    if (playing) void event.currentTarget.play().catch(() => undefined);
                  }} />}
                {activeBroll && activeBrollSource && <video className="editor-broll-preview" src={fileUrl(activeBrollSource.path)} muted playsInline
                  key={activeBroll.id + activeBrollSource.id} ref={(element) => {
                    if (!element) return;
                    const sourceTime = activeBroll.start + timelinePosition - activeBroll.at;
                    if (Math.abs(element.currentTime - sourceTime) > .3 && element.readyState >= 1) element.currentTime = sourceTime;
                    if (playing) void element.play().catch(() => undefined); else element.pause();
                  }}
                  style={{ width: `${Math.round(activeBroll.scale * 100)}%` }}
                  onLoadedMetadata={(event) => { event.currentTarget.currentTime = activeBroll.start + timelinePosition - activeBroll.at; if (playing) void event.currentTarget.play().catch(() => undefined); }} />}
                {captionPreviewWords.length > 0 && <div className={`editor-caption-preview ${captionStyle} ${captionPosition} editor-caption-${captionAnimation}`}
                  style={{ color: `#${captionTextColor}`, "--caption-highlight": `#${captionHighlightColor}` } as React.CSSProperties}>
                  {captionPreviewWords.map((word, index) => index + previewGroupStart === previewWordIndex && captionHighlight
                    ? <strong key={`${word.start}-${index}`}>{word.word}</strong> : <span key={`${word.start}-${index}`}>{word.word}</span>)}
                </div>}
                {!!overlay.trim() && <div className={`editor-title-preview ${overlayPosition}`}>{overlay}</div>}
                {showSafeGuides && <div className="editor-safe-guides" aria-hidden="true"><span>БЕЗОПАСНАЯ ЗОНА</span></div>}
              </div> : <EmptyStage text="Импортируйте видео, чтобы начать" />}
            </div>
            {activeSegment && <div className="editor-player-controls">
              <div className="editor-player-buttons">
                <button className="btn sm icon" title="Назад на 5 секунд" onClick={() => jump(-5)}><SkipBack size={16} /></button>
                <button className="editor-play-button" title={playing ? "Пауза" : "Воспроизвести"} onClick={togglePlayback}>
                  {playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
                </button>
                <button className="btn sm icon" title="Вперёд на 5 секунд" onClick={() => jump(5)}><SkipForward size={16} /></button>
              </div>
              <span className="editor-timecode">{fmt(playhead)}</span>
              <input aria-label="Позиция просмотра" className="editor-scrubber" type="range" min={activeSegment.start} max={activeSegment.end}
                step="0.05" value={Math.max(activeSegment.start, Math.min(activeSegment.end, playhead))} onChange={(e) => seekTo(Number(e.target.value))} />
              <span className="editor-timecode">{fmt(activeSegment.end)}</span>
            </div>}
          </section>
          {running && job && <RunningView job={job} onCancel={cancel} />}
          {result && !running && <div className="editor-result">
            {resultQa && <section className={`editor-export-qa ${resultQa.passed ? "passed" : "needs-review"}`} aria-live="polite">
              <div className="editor-export-qa-heading"><b>{resultQa.passed ? "Техническая проверка пройдена" : "Проверьте экспорт перед использованием"}</b>
                <span>{resultQa.decode_ok ? "Файл полностью декодируется" : "Есть ошибки декодирования"}</span></div>
              <div>{resultQa.width} × {resultQa.height} · {Number(resultQa.fps || 0).toFixed(0)} кадр/с · {Number(resultQa.duration || 0).toFixed(1)} с</div>
              <div>Звук: {resultQa.audio_expected === false ? "активный звук не ожидался" : resultQa.audio_ok ? "дорожка есть" : "дорожка не найдена"}
                {resultQa.audio_peak_db != null ? ` · пик ${resultQa.audio_peak_db} dBFS` : ""}
                {resultQa.audio_mean_db != null ? ` · средний ${resultQa.audio_mean_db} dBFS` : ""}
                {resultQa.transitions?.length ? ` · переходов проверено: ${resultQa.transitions.length}` : " · переходов нет"}</div>
              {resultQa.reframe?.requested && <small>Кадрирование: {resultQa.reframe.tracked_sources
                ? `лицо удерживалось по ${resultQa.reframe.tracked_samples} опорным точкам`
                : resultQa.reframe.reason || "центральное"}</small>}
              {!!resultQa.warnings?.length && <ul>{resultQa.warnings.map((warning: string, index: number) => <li key={index}>{warning}</li>)}</ul>}
              {!!resultQa.black_intervals?.length && <small>Тёмные отрезки: {resultQa.black_intervals.map((item: any) => `${item.start}–${item.end} с`).join(", ")}</small>}
              {!!resultQa.transitions?.length && <small>Переходы: {resultQa.transitions.map((item: any) => `${TRANSITION_LABELS[item.type as Transition["type"]] || item.type} · ${Number(item.duration).toFixed(2)} с`).join("; ")}</small>}
              {!!resultQa.review_frames?.length && <div className="editor-export-frames">
                <small>Контрольные кадры из готового экспорта · сверьте кадрирование, субтитры и переходы</small>
                <div className="editor-export-frame-strip">{resultQa.review_frames.map((frame: any, index: number) => <a
                  className="editor-export-frame" href={fileUrl(frame.path)} target="_blank" rel="noreferrer" key={`${frame.path}-${index}`}>
                  <img src={fileUrl(frame.path)} alt={`Кадр экспорта на ${Number(frame.time).toFixed(2)} секунде`} loading="lazy" />
                  <span>{Number(frame.time).toFixed(2)} с</span>
                </a>)}</div>
              </div>}
              {resultQa.visual_review_available && <small>Показаны отдельные кадры; сверьте весь ролик перед публикацией.</small>}
              {resultQa.visual_review?.available ? <div className={`editor-export-ai-review ${resultQa.visual_review.notes?.length ? "has-notes" : ""}`}>
                <small>ИИ просмотрел {Number(resultQa.visual_review.sampled_frames || 0)} выборочных кадров; это не проверка каждого кадра.</small>
                {resultQa.visual_review.notes?.length
                  ? <ul>{resultQa.visual_review.notes.map((note: any, index: number) => <li key={`${note.frame_index}-${index}`}>
                    <b>{Number(note.time).toFixed(2)} с · {note.severity === "high" ? "проверьте" : note.severity === "medium" ? "внимание" : "заметка"}</b> {note.note}
                  </li>)}</ul>
                  : <span>На просмотренных кадрах явных проблем не отмечено. Проверьте весь ролик перед публикацией.</span>}
              </div> : resultQa.visual_review && <small>ИИ-проверка кадров не завершилась; используйте миниатюры для ручной проверки.</small>}
            </section>}
            <ResultView path={result} meta={assistantNote || "Результат монтажа"} />
          </div>}
        </section>

        <aside className="editor-inspector">
          <div className="editor-pane-heading"><div><b>Инструменты</b><span>Настройте выбранный фрагмент</span></div></div>
          {renderInProgress && <div className="editor-color-preview" role="status">Параметры этого экспорта зафиксированы. После завершения можно внести изменения и запустить новый экспорт.</div>}
          <fieldset className="editor-inspector-controls" disabled={renderInProgress} aria-busy={renderInProgress || undefined}>
          <div className="editor-inspector-tabs">
            {panelTabs.map((tab) => <button key={tab.id} className={panel === tab.id ? "active" : ""} onClick={() => setPanel(tab.id)}>
              <tab.icon size={15} /><span>{tab.label}</span>
            </button>)}
          </div>

          {panel === "edit" && <div className="editor-inspector-body">
            <div className="editor-property-title"><b>Фрагмент</b><span>{activeSegment ? "№ " + (activeIndex + 1) : "Не выбран"}</span></div>
            {activeSegment ? <>
              <div className="grid cols-2">
                <NumberField label="Начало, сек." value={+activeSegment.start.toFixed(2)} min={0} max={activeSegment.end - .1} step={.1}
                  onChange={(v) => setSegmentBound("start", Number(v) || 0)} />
                <NumberField label="Конец, сек." value={+activeSegment.end.toFixed(2)} min={activeSegment.start + .1}
                  max={sources.find((s) => s.id === activeSegment.source)?.duration} step={.1}
                  onChange={(v) => setSegmentBound("end", Number(v) || activeSegment.end)} />
              </div>
              <div className="editor-button-row">
                <button className="btn sm" onClick={() => setSegmentBound("start", playhead)}><Check size={13} />Начало здесь</button>
                <button className="btn sm" onClick={() => setSegmentBound("end", playhead)}><Check size={13} />Конец здесь</button>
              </div>
              <div className="editor-property-title"><b>Кадр</b><span>Формат кадрирования</span></div>
              <Field label="Соотношение сторон"><select value={aspect} onChange={(e) => setAspect(e.target.value)}>
                <option value="source">Как в исходнике</option><option value="9:16">9:16 · Shorts</option><option value="1:1">1:1 · квадрат</option><option value="16:9">16:9 · широкое</option>
              </select></Field>
              {aspect === "9:16" && <>
                <label className="editor-caption-toggle"><input type="checkbox" checked={subjectFollow} onChange={(e) => { pushUndo(); setSubjectFollow(e.target.checked); }} />Удерживать говорящего в кадре</label>
                <div className="editor-color-preview">YuNet отслеживает лицо локально на CPU. После первого экспорта с найденным лицом предпросмотр следует по той же траектории. Если лицо не найдено, используется центральное кадрирование.</div>
                {!faceBlurReady && <button className="btn sm" disabled={!!running} onClick={() => void installFaceBlur()}><Download size={13} />Установить YuNet для кадрирования</button>}
              </>}
              <div className="editor-property-title"><b>Переход к следующему фрагменту</b><span>{activeIndex < segments.length - 1 ? "Экспортируется" : "Нет следующего фрагмента"}</span></div>
              <Field label="Стиль"><select disabled={activeIndex < 0 || activeIndex >= segments.length - 1}
                value={activeSegment.transitionAfter?.type || "none"}
                onChange={(e) => setSegments((all) => all.map((segment) => segment.id !== activeSegment.id ? segment : e.target.value === "none"
                  ? { ...segment, transitionAfter: undefined }
                  : { ...segment, transitionAfter: { type: e.target.value as Transition["type"], duration: segment.transitionAfter?.duration || .7 } }))}>
              <option value="none">Без перехода</option><option value="dissolve">Плавное растворение</option><option value="fade">Плавное смешивание</option><option value="fadeblack">Затемнение через чёрный</option>
                <option value="wipeleft">Шторка влево</option><option value="wiperight">Шторка вправо</option><option value="slideright">Сдвиг вправо</option>
                <option value="slideleft">Сдвиг влево</option><option value="circleopen">Круг</option><option value="smoothleft">Плавный сдвиг</option>
              </select></Field>
              {activeSegment.transitionAfter && <label className="editor-transition-duration">Длительность <b>{activeSegment.transitionAfter.duration.toFixed(1)} с</b>
                <input aria-label="Длительность перехода" type="range" min=".15" max="2" step=".05" value={activeSegment.transitionAfter.duration}
                  onChange={(e) => setSegments((all) => all.map((segment) => segment.id !== activeSegment.id ? segment : { ...segment, transitionAfter: { ...activeSegment.transitionAfter!, duration: Number(e.target.value) } }))} />
              </label>}
              <div className="editor-property-title"><b>Заголовок в кадре</b><span>Необязательно</span></div>
              <Field label="Текст"><input value={overlay} maxLength={180} onChange={(e) => setOverlay(e.target.value)} placeholder="Добавить короткий титр" /></Field>
              <Field label="Положение заголовка"><select value={overlayPosition} onChange={(e) => { pushUndo(); setOverlayPosition(e.target.value); }}>
                <option value="top">Сверху · отступ 12%</option><option value="middle">По центру</option><option value="bottom">Снизу · отступ 20%</option>
              </select></Field>
              <div className="editor-color-preview">Настройка относится к заголовку, добавленному в редакторе. Текст, уже встроенный в файл, останется на исходном месте.</div>
              <div className="editor-button-row">
                <button className="btn sm" disabled={!activeSegment || playhead <= activeSegment.start + .12 || playhead >= activeSegment.end - .12} onClick={splitAtPlayhead}>
                  <Scissors size={14} />Разрезать здесь
                </button>
                <button className="btn sm danger" disabled={!activeSegment} onClick={() => activeSegment && removeSegment(activeSegment.id)}><Trash2 size={14} />Удалить фрагмент</button>
              </div>
            </> : <div className="editor-empty-media">Добавьте исходник и нажмите «+», чтобы поместить его на шкалу.</div>}
            <div className="editor-property-title"><b><Layers size={14} /> B-roll · V2</b><span>{brollClips.length} фрагм.</span></div>
            {!brollClips.length && <div className="editor-color-preview">Нажмите значок слоёв у исходника в медиатеке, чтобы добавить видео поверх основного кадра.</div>}
            {brollClips.map((clip, index) => {
              const source = sources.find((item) => item.id === clip.source);
              return <div className="editor-broll-inspector" key={clip.id}>
                <b>{source?.path.split(/[\\/]/).pop() || `B-roll ${index + 1}`}</b>
                <div className="grid cols-2">
                  <NumberField label="Позиция в проекте" value={+clip.at.toFixed(1)} min={0} max={Math.max(0, timelineDuration - (clip.end - clip.start))} step={.1}
                    onChange={(value) => { pushUndo(); setBrollClips((all) => all.map((item) => item.id === clip.id ? { ...item, at: Number(value) } : item)); }} />
                  <NumberField label="Длина B-roll" value={+(clip.end - clip.start).toFixed(1)} min={.5} max={source?.duration || 8} step={.5}
                    onChange={(value) => { pushUndo(); setBrollClips((all) => all.map((item) => item.id === clip.id ? { ...item, end: Math.min(source?.duration || 8, item.start + Number(value)) } : item)); }} />
                </div>
                <div className="editor-broll-actions"><label>Размер окна <input type="range" min=".2" max=".7" step=".02" value={clip.scale}
                  onPointerDown={pushUndo} onChange={(event) => setBrollClips((all) => all.map((item) => item.id === clip.id ? { ...item, scale: Number(event.target.value) } : item))} /></label>
                  <button className="btn sm ghost icon danger" title="Удалить B-roll" onClick={() => { pushUndo(); setBrollClips((all) => all.filter((item) => item.id !== clip.id)); }}><Trash2 size={13} /></button>
                </div>
              </div>;
            })}
          </div>}

          {panel === "color" && <div className="editor-inspector-body">
            <div className="editor-property-title"><b>Цветокоррекция</b><button className="btn sm" onClick={() => setGrade(defaultGrade)}>Сброс</button></div>
            <div className="editor-property-title"><b>Профили видеопродакшена</b><span>одним нажатием</span></div>
            <div className="editor-look-grid">
              <button onClick={() => applyProducerLook("vertical")}><b>Vertical Creator</b><span>9:16 · выразительный цвет · яркие титры</span></button>
              <button onClick={() => applyProducerLook("podcast")}><b>Подкаст</b><span>мягкая коррекция · читаемая подложка</span></button>
              <button onClick={() => applyProducerLook("cinematic")}><b>Кино</b><span>сдержанный цвет · спокойные титры</span></button>
            </div>
            <div className="editor-color-preview">Цветовые изменения видны в предпросмотре. При экспорте они применяются к видео.</div>
            <Slider label="Яркость" value={grade.brightness} min={-.25} max={.25} step={.01}
              onChange={(brightness) => setGrade({ ...grade, brightness })} fmt={(v) => (v >= 0 ? "+" : "") + v.toFixed(2)} />
            <Slider label="Контраст" value={grade.contrast} min={.5} max={1.8} step={.02}
              onChange={(contrast) => setGrade({ ...grade, contrast })} fmt={(v) => v.toFixed(2) + "×"} />
            <Slider label="Насыщенность" value={grade.saturation} min={0} max={1.8} step={.02}
              onChange={(saturation) => setGrade({ ...grade, saturation })} fmt={(v) => v.toFixed(2) + "×"} />
            <Slider label="Температура" value={grade.temperature} min={-1} max={1} step={.02}
              onChange={(temperature) => setGrade({ ...grade, temperature })} fmt={(v) => v < -.02 ? "Холоднее" : v > .02 ? "Теплее" : "Нейтрально"} />
            <Slider label="Оттенок" value={grade.tint} min={-1} max={1} step={.02}
              onChange={(tint) => setGrade({ ...grade, tint })} fmt={(v) => v < -.02 ? "Зелёный" : v > .02 ? "Пурпурный" : "Нейтрально"} />
          </div>}

          {panel === "captions" && <div className="editor-inspector-body">
            <div className="editor-property-title"><b>Автоматические субтитры</b><span>Whisper · локально</span></div>
            <div className="editor-color-preview">Речь распознаётся на этом компьютере. Текст и таймкоды сохраняются в проекте; выбранные стили прожигаются в экспортируемое видео.</div>
            <button className="btn primary" disabled={!!running || !segments.length} onClick={() => void makeCaptions()}>
              <Captions size={15} />{running ? "Распознаю речь…" : Object.keys(sourceCaptions).length ? "Обновить расшифровку" : "Создать субтитры автоматически"}
            </button>
            {Object.keys(sourceCaptions).length > 0 && <div className="editor-caption-status">
              <Check size={14} />{Object.values(sourceCaptions).reduce((count, item) => count + item.words.filter((word) => word.word.trim()).length, 0)} слов
              {activeSource && sourceCaptions[activeSource.id]?.language ? ` · язык: ${sourceCaptions[activeSource.id].language}` : " · расшифровка готова"}
            </div>}
            {activeSource && sourceCaptions[activeSource.id] && <section className="editor-caption-transcript" aria-label="Редактор расшифровки">
              <div className="editor-property-title"><b>Текст и таймкоды</b><span>{visibleCaptionWords.filter(({ word }) => word.word.trim()).length} / {previewWords.filter((word) => word.word.trim()).length} слов</span></div>
              <div className="editor-color-preview">Исправляйте распознанные слова или удаляйте лишние значком корзины. Нажмите на время, чтобы перейти к этому месту. Таймкоды сохраняются; изменения войдут в экспорт.</div>
              {selectedCaptionTiming?.word && activeSource && <div className="editor-caption-time-editor">
                <div className="editor-property-title"><b>Точная синхронизация</b><span>{selectedCaptionTiming.word.word.trim() || "слово удалено"}</span></div>
                <div className="editor-caption-time-fields">
                  {(["start", "end"] as const).map((edge) => {
                    const word = selectedCaptionTiming.word!;
                    const editing = captionTimingDraft?.sourceId === activeSource.id && captionTimingDraft.index === selectedCaptionTiming.index && captionTimingDraft.edge === edge;
                    const value = editing ? captionTimingDraft.text : word[edge].toFixed(2);
                    return <label key={edge}>
                      <span>{edge === "start" ? "Начало" : "Конец"}, с</span>
                      <input type="number" step="0.01" min={edge === "start" ? 0 : word.start + .04}
                        max={edge === "start" ? Math.max(0, word.end - .04) : activeSource.duration || word.end}
                        aria-label={`${edge === "start" ? "Начало" : "Конец"} слова ${selectedCaptionTiming.index + 1}`}
                        value={value}
                        onFocus={() => setCaptionTimingDraft({ sourceId: activeSource.id, index: selectedCaptionTiming.index, edge, text: word[edge].toFixed(2) })}
                        onChange={(event) => setCaptionTimingDraft({ sourceId: activeSource.id, index: selectedCaptionTiming.index, edge, text: event.target.value })}
                        onBlur={(event) => commitCaptionTiming(activeSource.id, selectedCaptionTiming.index, edge, event.currentTarget.value)}
                        onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
                    </label>;
                  })}
                </div>
                <div className="editor-caption-time-hint">Таймкод относительно исходника. Минимальная длина слова — 0,04 с.</div>
              </div>}
              <input className="editor-caption-search" type="search" aria-label="Найти слово в расшифровке" placeholder="Найти слово…"
                value={captionWordQuery} onChange={(event) => setCaptionWordQuery(event.target.value)} />
              {previewWords.length === 0 ? <div className="editor-caption-empty">В расшифровке пока нет слов.</div>
                : visibleCaptionWords.length === 0 ? <div className="editor-caption-empty">Совпадений нет.</div>
                  : <div className="editor-caption-word-list">
                    {visibleCaptionWords.map(({ word, index }) => <div className="editor-caption-word-row" key={`${activeSource.id}-${word.start}-${index}`}>
                      <button type="button" className="editor-caption-word-time" title="Перейти к слову в предпросмотре"
                        onClick={() => { setCaptionTimingSelection({ sourceId: activeSource.id, index }); seekTo(word.start); }}>{fmt(word.start)}</button>
                      <input type="text" aria-label={`Слово ${index + 1} в ${fmt(word.start)}`} placeholder="Текст слова" maxLength={100}
                        value={word.word.trim()}
                        onFocus={() => { captionWordEdit.current = { sourceId: activeSource.id, index, initialText: word.word.trim(), undoPushed: false }; }}
                        onChange={(event) => {
                          const text = event.target.value.replace(/\s+/g, " ").slice(0, 100);
                          const edit = captionWordEdit.current;
                          if (edit?.sourceId === activeSource.id && edit.index === index && !edit.undoPushed && text !== edit.initialText) {
                            pushUndo(); edit.undoPushed = true;
                          }
                          setSourceCaptions((all) => {
                            const sourceCaptionsForVideo = all[activeSource.id];
                            if (!sourceCaptionsForVideo) return all;
                            const words = sourceCaptionsForVideo.words.map((item, wordIndex) => wordIndex === index ? { ...item, word: text } : item);
                            return { ...all, [activeSource.id]: { ...sourceCaptionsForVideo, words } };
                          });
                        }}
                        onBlur={() => { captionWordEdit.current = null; }} />
                      <button type="button" className="btn sm ghost icon danger" aria-label={`Удалить слово ${index + 1} в ${fmt(word.start)}`}
                        title="Убрать слово из субтитров" onClick={() => {
                          pushUndo();
                          setCaptionTimingSelection((selection) => !selection || selection.sourceId !== activeSource.id
                            ? selection : selection.index === index ? null : selection.index > index ? { ...selection, index: selection.index - 1 } : selection);
                          setCaptionTimingDraft(null);
                          setSourceCaptions((all) => {
                            const sourceCaptionsForVideo = all[activeSource.id];
                            if (!sourceCaptionsForVideo) return all;
                            const words = sourceCaptionsForVideo.words.filter((_, wordIndex) => wordIndex !== index);
                            return { ...all, [activeSource.id]: { ...sourceCaptionsForVideo, words } };
                          });
                        }}><Trash2 size={13} /></button>
                    </div>)}
                  </div>}
            </section>}
            <div className="editor-property-title"><b>Стиль субтитров</b><span>выберите оформление</span></div>
            <div className="editor-caption-styles">
              {[
                ["creator", "Creator", "Жирный шрифт и акцент"], ["reels", "Shorts Pop", "Яркая подсветка слов"],
                ["clean", "Clean", "Чисто и сдержанно"], ["podcast", "Подкаст", "Контрастная подложка"],
                ["cinematic", "Cinema", "Спокойная антиква"], ["neon", "Neon", "Мягкий цветной акцент"],
                ["boxed", "Soft Box", "Удобная плашка"], ["outline", "Bold Outline", "Крупный контурный текст"],
                ["minimal", "Minimal", "Простой белый текст"], ["opus", "Karaoke", "Подсветка по словам"],
              ].map(([id, label, detail]) => <button key={id} className={captionStyle === id ? "active" : ""}
                onClick={() => { pushUndo(); setCaptionStyle(id); }}><b>{label}</b><span>{detail}</span></button>)}
            </div>
            <Field label="Анимация текста"><select value={captionAnimation} onChange={(e) => { pushUndo(); setCaptionAnimation(e.target.value); }}>
              <option value="none">Без анимации</option><option value="pop">Pop · упругий акцент</option>
              <option value="bounce">Bounce · мягкий отскок</option><option value="fade">Fade · плавное появление</option>
            </select></Field>
            <Field label="Положение"><select value={captionPosition} onChange={(e) => { pushUndo(); setCaptionPosition(e.target.value); }}>
              <option value="bottom">Снизу</option><option value="middle">По центру</option><option value="top">Сверху</option>
            </select></Field>
            <label className="editor-caption-toggle"><input type="checkbox" checked={showSafeGuides} onChange={(e) => setShowSafeGuides(e.target.checked)} />Показать безопасную зону в предпросмотре</label>
            <div className="editor-color-preview">Пунктир показывает примерную зону для Shorts/Reels. Новые титры и субтитры будут располагаться с отступами; надписи, уже встроенные в исходное видео, не перемещаются.</div>
            <Field label="Слов в строке"><Seg value={String(captionWordsPerLine)} onChange={(value) => { pushUndo(); setCaptionWordsPerLine(Number(value)); }}
              options={[{ v: "2", label: "2" }, { v: "3", label: "3" }, { v: "4", label: "4" }]} /></Field>
            <label className="editor-caption-toggle"><input type="checkbox" checked={captionHighlight} onChange={(e) => { pushUndo(); setCaptionHighlight(e.target.checked); }} />Подсвечивать активное слово</label>
            <div className="editor-caption-colors">
              <label>Основной текст<input aria-label="Цвет основного текста субтитров" type="color" value={`#${captionTextColor}`}
                onChange={(e) => { pushUndo(); setCaptionTextColor(e.target.value.slice(1).toUpperCase()); }} /></label>
              <label>Подсветка<input aria-label="Цвет подсветки субтитров" type="color" value={`#${captionHighlightColor}`}
                onChange={(e) => { pushUndo(); setCaptionHighlightColor(e.target.value.slice(1).toUpperCase()); }} /></label>
            </div>
            <div className="editor-color-preview">Проверьте текст на кадре предпросмотра. Если распознавание ошиблось, исправьте его в расшифровке исходника до экспорта.</div>
          </div>}

          {panel === "audio" && <div className="editor-inspector-body">
            <div className="editor-property-title"><b>Звук проекта</b><Music2 size={16} /></div>
            <Slider label="Громкость оригинала" value={volume} min={0} max={2} step={.05} onChange={setVolume} fmt={(v) => Math.round(v * 100) + "%"} />
            <label className="editor-caption-toggle"><input type="checkbox" checked={normalizeAudio} onChange={(e) => { pushUndo(); setNormalizeAudio(e.target.checked); }} />Выровнять громкость при экспорте</label>
            <div className="editor-color-preview">Локальная обработка выравнивает общую громкость и ограничивает пики. Автомонтаж включает её для более ровной речи.</div>
            <Field label="Добавить фоновую музыку"><DropZone accept="audio/*" value={music} onChange={setMusic} label="Выбрать аудиофайл" kind="audio" /></Field>
            {music && missingMedia.includes(music) && <button className="btn sm" onClick={() => {
              relinkTarget.current = { kind: "music" }; relinkMediaInput.current?.click();
            }}><Upload size={13} />Перепривязать музыку</button>}
            {music && <>
              <div className="editor-audio-selected"><AudioLines size={16} /><span>{music.split(/[\\/]/).pop()}</span></div>
              <Slider label="Громкость музыки" value={musicVolume} min={0} max={.6} step={.01} onChange={setMusicVolume} fmt={(v) => Math.round(v * 100) + "%"} />
              <label className="editor-caption-toggle"><input type="checkbox" checked={duckMusic} onChange={(e) => { pushUndo(); setDuckMusic(e.target.checked); }} />Приглушать музыку во время речи</label>
              <div className="editor-color-preview">Музыка мягко тише, пока говорит человек, и возвращается в паузах.</div>
            </>}
            <div className="editor-divider" />
            <div className="editor-property-title"><b>AI‑звуковые эффекты</b><span>{sfxReady ? "Stable Audio · CPU" : "модель не установлена"}</span></div>
            {!sfxReady ? <div className="editor-sfx-install-card">
              <p>Установите Stable Audio 3 Small‑SFX для генерации коротких звуков и переходов. Нужно около 2,6 ГБ; генерация работает локально на процессоре.</p>
              <button className="btn primary" disabled={!!running} onClick={() => void installSfx()}>
                <Download size={14} />{running ? "Установка…" : "Установить модель SFX"}
              </button>
            </div> : <>
              <div className="editor-sfx-presets">
                {["Мягкий свист перехода", "Лёгкий воздушный whoosh", "Тихий акцентный удар"].map((presetText) =>
                  <button key={presetText} className="btn sm" onClick={() => setSfxPrompt(presetText)}>{presetText}</button>)}
              </div>
              <Field label="Опишите эффект на русском или английском"><textarea rows={2} maxLength={400} value={sfxPrompt}
                onChange={(e) => setSfxPrompt(e.target.value)} placeholder="Например: мягкий короткий звук перехода, без музыки и речи" /></Field>
              <Slider label="Длина звука" value={sfxDuration} min={1} max={10} step={.5} onChange={setSfxDuration} fmt={(v) => v.toFixed(1) + " с"} />
              <button className="btn primary" disabled={!!running || !sfxPrompt.trim() || !segments.length} onClick={() => void generateSfx()}>
                <Sparkles size={14} />{running ? "Создаю эффект…" : `Создать и поставить на ${fmt(timelinePosition)}`}
              </button>
            </>}
            {sfxClips.length > 0 && <div className="editor-sfx-list"><b>Эффекты на шкале</b>
              {sfxClips.map((clip) => <div className="editor-sfx-row" key={clip.id}>
                <div><b>{clip.prompt}</b><span>{clip.duration.toFixed(1)} с · {missingMedia.includes(clip.path) ? "файл не найден" : clip.path.split(/[\\/]/).pop()}</span></div>
                {missingMedia.includes(clip.path) && <button className="btn sm icon" title="Перепривязать SFX" onClick={() => {
                  relinkTarget.current = { kind: "sfx", id: clip.id }; relinkMediaInput.current?.click();
                }}><Upload size={13} /></button>}
                <label>Начало<input type="number" min={0} max={timelineDuration} step={.1} value={clip.at.toFixed(1)}
                  onChange={(e) => setSfxClips((all) => all.map((item) => item.id === clip.id ? { ...item, at: Math.max(0, Math.min(timelineDuration, Number(e.target.value) || 0)) } : item))} /></label>
                <button className="btn sm ghost icon danger" title="Удалить эффект с таймлайна" onClick={() => setSfxClips((all) => all.filter((item) => item.id !== clip.id))}><Trash2 size={13} /></button>
              </div>)}
            </div>}
            <Slider label="Плавное появление и затухание" value={fade} min={0} max={3} step={.1} onChange={setFade} fmt={(v) => v.toFixed(1) + " с"} />
            <div className="editor-color-preview">Добавляйте только музыку, на использование которой у вас есть права.</div>
          </div>}

          {panel === "ai" && <div className="editor-inspector-body editor-assistant-panel">
            <div className="editor-chat-heading"><div className="editor-chat-avatar"><Sparkles size={15} /></div><div><b>AI‑монтажёр</b><span>Управляет текущим проектом</span></div><span className="pill online">локально</span></div>
            {agentProgress && <div className={`editor-agent-progress ${agentProgress.error ? "error" : ""}`} role="status" aria-live="polite">
              <div className="editor-agent-progress-head"><b>{agentProgress.phase}</b><span>{agentProgress.total > 0 ? `${agentProgress.step}/${agentProgress.total} · ` : ""}{agentProgressPercent}%</span></div>
              <div className="editor-agent-progress-track" role="progressbar" aria-label="Выполнение AI‑монтажёра" aria-valuemin={0} aria-valuemax={100} aria-valuenow={agentProgressPercent}>
                <span style={{ width: `${agentProgressPercent}%` }} />
              </div>
              {assistantBusy && <small>{running ? job?.message : "AI готовит следующий шаг…"}</small>}
            </div>}
            <div className="editor-chat-messages" ref={chatScroll} aria-live="polite">
              {chatMessages.map((message, index) => <div className={`editor-chat-message ${message.role}`} key={index}>
                {message.role === "assistant" && <span className="editor-chat-mini"><Sparkles size={11} /></span>}
                <p>{message.text}</p>
              </div>)}
              {assistantBusy && <div className="editor-chat-thinking"><span /><span /><span />AI выполняет команду…</div>}
            </div>
            <div className="editor-chat-shortcuts" aria-label="Быстрые команды">
              <button className="btn sm" onClick={() => void runAssistant("Разрежь клип в позиции курсора")}>Разрез на курсоре</button>
              <button className="btn sm" onClick={() => runAssistant("Добавь плавное растворение на выбранном стыке")}>Плавный переход</button>
              <button className="btn sm" onClick={() => runAssistant("Сделай цвет немного теплее")}>Тёплый цвет</button>
              <button className="btn sm" onClick={() => void runAssistant("Размой лица на видео")}>Размыть лица</button>
              <button className="btn sm" onClick={() => void runAssistant("Проанализируй выбранное видео")}>Анализ видео</button>
            </div>
            {!faceBlurReady && <div className="editor-face-blur-install">
              <div><b>Детектор лиц: размытие и кадрирование</b><span>YuNet · OpenCV DNN · CPU · MIT</span></div>
              <button className="btn sm" disabled={!!running} onClick={() => void installFaceBlur()}><Download size={13} />Установить детектор</button>
            </div>}
            <div className="editor-mask-tool">
              <div className="editor-property-title"><b>Маска объекта и tracking</b><span>{sam2Ready ? "SAM 2.1 · RTX 5080" : "модель не установлена"}</span></div>
              {!sam2Ready ? <>
                <p>SAM 2 выделяет объект по точке на кадре и отслеживает его по выбранному фрагменту. Модель занимает около 156 МБ; во время обработки другие тяжёлые модели выгружаются из видеопамяти.</p>
                <button className="btn sm" disabled={!!running} onClick={() => void installSam2()}><Download size={13} />Установить SAM 2.1</button>
              </> : <>
                <p>{maskPickMode ? "Щёлкните по объекту в окне предпросмотра. Эта точка задаст маску и стартовый кадр." : "Выберите точку внутри объекта в предпросмотре. Отслеживание охватывает только текущий фрагмент, максимум 60 секунд."}</p>
                <Field label="Эффект на выбранном объекте"><select value={maskMode} onChange={(event) => setMaskMode(event.target.value as "gaussian" | "pixelate")}>
                  <option value="gaussian">Мягкое размытие</option><option value="pixelate">Пикселизация</option>
                </select></Field>
                {maskPoint && <div className="editor-mask-selection">Точка выбрана · кадр {fmt(maskSeedTime)} · {Math.round(maskPoint.x * 100)}%, {Math.round(maskPoint.y * 100)}%</div>}
                <div className="editor-button-row">
                  <button className={`btn sm ${maskPickMode ? "primary" : ""}`} disabled={!activeSegment || !!running} onClick={() => setMaskPickMode((value) => !value)}>
                    {maskPickMode ? "Отменить выбор" : "Выбрать объект в кадре"}
                  </button>
                  <button className="btn sm primary" disabled={!maskPoint || !activeSegment || !!running} onClick={() => void trackSelectedMask()}>
                    {running ? "Отслеживаю…" : "Отследить и применить"}
                  </button>
                </div>
                <small>Видеомаска считается с частотой 8 кадр/с. Просмотрите область маски, особенно при быстрых движениях и перекрытии объекта.</small>
              </>}
            </div>
            <div className="editor-chat-composer">
              <textarea rows={2} value={command} onChange={(e) => setCommand(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && command.trim()) { e.preventDefault(); void runAssistant(); } }}
                placeholder="Напишите, что изменить в видео…" aria-label="Команда AI‑монтажёру" />
              <button className="btn primary editor-chat-send" title="Выполнить команду" disabled={!command.trim() || assistantBusy} onClick={() => void runAssistant()}>
                {assistantBusy ? <span className="editor-chat-spinner" /> : <Send size={15} />}
              </button>
            </div>
            <div className="editor-chat-hint">Например: «Убери паузы, добавь анимированные субтитры и SFX» или «Разрежь на 12 секунде и добавь плавный переход». Создание новых клипов находится в разделе «Клипы».</div>
            <button className="btn sm editor-open-clips" onClick={() => go("video/clips")}><Film size={13} />Открыть создание клипов</button>
            <details className="editor-ai-tools">
              <summary><Wand2 size={14} />Инструменты генеративной правки <span className={vaceReady ? "pill online" : "pill missing"}>{vaceReady ? "VACE готов" : "VACE не установлен"}</span></summary>
              <Field label="Описание правки"><textarea rows={3} value={editPrompt} onChange={(e) => setEditPrompt(e.target.value)}
                placeholder="Опишите изменение освещения, фона или объекта. Не меняйте смысл речи." /></Field>
              <Field label="Модель"><Seg value={editModel} onChange={setEditModel} options={[{ v: "vace", label: "Wan VACE · 1.3B" }, { v: "bernini", label: "Bernini-R · 14B" }]} /></Field>
              <Field label="Тип правки"><select value={task} onChange={(e) => setTask(e.target.value)}>
                <option value="edit">Общая правка</option><option value="style">Стиль и освещение</option><option value="propagate">Заменить или добавить объект</option><option value="action">Изменить действие или положение</option>
              </select></Field>
              <Field label="Изображение-образец (необязательно)"><DropZone accept="image/*" value={ref} onChange={setRef} label="Выбрать образец" /></Field>
              <Field label="Качество"><Seg value={preset} onChange={setPreset} options={[{ v: "fast", label: "Быстро" }, { v: "balanced", label: "Баланс" }, { v: "quality", label: "Высокое" }]} /></Field>
              <div className="grid cols-2"><NumberField label="Начало, сек." value={editStart} min={activeSegment?.start ?? 0} max={activeSegment?.end ?? 5} step={.1} onChange={(v) => setEditStart(Number(v) || 0)} />
                <NumberField label="Длина (до 5 сек.)" value={editDuration} min={.5} max={5} step={.5} onChange={(v) => setEditDuration(Number(v) || 5)} /></div>
              <button className="btn primary" disabled={!!running || !activeSource || !editPrompt.trim() || (editModel === "vace" && !vaceReady)} onClick={generateScene}>
                <Wand2 size={15} />{running ? "Создаётся…" : "Создать AI‑правку и вставить в шкалу"}
              </button>
              <div className="editor-color-preview">AI изменяет короткий выбранный фрагмент, затем заменяет его в монтаже. Всегда проверьте результат.</div>
            </details>
          </div>}

          {panel === "analyze" && <div className="editor-inspector-body">
            <div className="editor-property-title"><b>Анализ видео</b><span>локально</span></div>
            <p className="small muted">Локальный анализ связывает расшифровку с кадрами и вероятными сменами плана. Он не готовит YouTube‑метаданные автоматически.</p>
            <button className="btn primary" disabled={!!running || !activeSource} onClick={analyze}><Eye size={15} />{running ? "Анализ…" : "Анализировать исходник"}</button>
            {activeVideoAnalysis?.youtube ? <SocialMetadataPanel value={activeVideoAnalysis} compact />
              : activeVideoAnalysis && <div className="editor-color-preview">
                {activeVideoAnalysis.content_summary && <p><b>Содержание</b><br />{activeVideoAnalysis.content_summary}</p>}
                {activeVideoAnalysis.visual_summary && <p><b>По выбранным кадрам</b><br />{activeVideoAnalysis.visual_summary}</p>}
                {activeVideoAnalysis.opening_note && <p><b>Начало</b><br />{activeVideoAnalysis.opening_note}</p>}
                {activeVideoAnalysis.ending_note && <p><b>Конец</b><br />{activeVideoAnalysis.ending_note}</p>}
                {activeVideoAnalysis.scene_changes?.length > 0 ? <>
                  <b>Кандидаты на смену плана</b>
                  <ul>{activeVideoAnalysis.scene_changes.slice(0, 12).map((change: any, index: number) =>
                    <li key={index}><button type="button" className="editor-analysis-jump"
                      disabled={!segments.some((segment) => segment.source === activeSource?.id && Number(change.time) >= segment.start && Number(change.time) <= segment.end)}
                      onClick={() => seekToAnalysisTime(Number(change.time))}>
                      {fmt(Number(change.time) || 0)} · визуальная разница {Math.round((Number(change.score) || 0) * 100)}% · проверить
                    </button></li>)}</ul>
                  <small>Это технические сигналы между соседними кадрами, а не автоматические точки разреза.</small>
                </> : <p><b>Смена планов</b><br />{activeVideoAnalysis.scene_detection_available
                  ? "При низкочастотной проверке склейки не отмечены; быстрые или плавные переходы могут быть пропущены."
                  : "Техническая проверка склеек не завершилась."}</p>}
                {activeVideoAnalysis.frame_notes?.length > 0 && <>
                  <b>Заметки по выборочным кадрам</b>
                  <ul>{activeVideoAnalysis.frame_notes.map((scene: any, index: number) => <li key={index}>
                    <button type="button" className="editor-analysis-jump"
                      disabled={!segments.some((segment) => segment.source === activeSource?.id && Number(scene.time) >= segment.start && Number(scene.time) <= segment.end)}
                      onClick={() => seekToAnalysisTime(Number(scene.time))}>
                      {fmt(Number(scene.time) || 0)} · {scene.note} · уверенность: {scene.confidence === "high" ? "высокая" : scene.confidence === "medium" ? "средняя" : "низкая"} · перейти
                    </button>
                  </li>)}</ul>
                </>}
                {activeVideoAnalysis.edit_suggestions?.length > 0 && <><b>Варианты монтажа</b><ul>{activeVideoAnalysis.edit_suggestions.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></>}
                <p>Титры в исходнике: {activeVideoAnalysis.existing_subtitles === "visible" ? "видны на выбранных кадрах"
                  : activeVideoAnalysis.existing_subtitles === "not_visible" ? "не обнаружены на выбранных кадрах" : "точно определить не удалось"}</p>
              </div>}
          </div>}

          {panel === "export" && <div className="editor-inspector-body">
            <div className="editor-property-title"><b>Экспорт проекта</b><span>FFmpeg</span></div>
            <Field label="Частота кадров"><select value={fps} onChange={(e) => setFps(e.target.value)}>
              <option value="source">Как в исходнике</option><option value="24">24 кадр/с</option><option value="30">30 кадр/с</option><option value="60">60 кадр/с</option>
            </select></Field>
            <div className="editor-export-summary">
              <b>{segments.length} фрагментов · {fmt(timelineDuration)}</b>
              <span>{aspect === "source" ? "Размер кадра исходника" : aspect + " · для экспорта"}</span>
              <span>Звук: {Math.round(volume * 100)}%{music ? " + музыка" : ""}{sfxClips.length ? ` + SFX ${sfxClips.length}` : ""}</span>
              <span>Субтитры: {Object.keys(sourceCaptions).length ? `${captionStyle} · ${captionPosition}` : "не добавлены"}</span>
            </div>
            <button className="btn primary big" disabled={!!running || !sources.length || !segments.length} onClick={renderTimeline}>
              <Download size={16} />{running ? "Экспортируется…" : "Экспортировать видео"}
            </button>
          </div>}
          </fieldset>
        </aside>
      </div>

      <section className="editor-timeline-dock">
        <div className="editor-timeline-heading">
          <div><b>Монтажная шкала</b><span>{segments.length} фрагм. · {fmt(timelineDuration)}{activeSegment ? " · выбран " + fmt(activeSegment.end - activeSegment.start) : ""}</span></div>
          <div className="editor-timeline-actions">
            <button className="btn sm" disabled={!activeSegment || playhead <= activeSegment.start + .12 || playhead >= activeSegment.end - .12} onClick={splitAtPlayhead}><Scissors size={14} />Разрезать</button>
            <button className="btn sm" disabled={activeIndex <= 0} onClick={() => moveSegment(-1)} title="Переместить раньше"><ArrowUp size={14} /></button>
            <button className="btn sm" disabled={activeIndex < 0 || activeIndex >= segments.length - 1} onClick={() => moveSegment(1)} title="Переместить позже"><ArrowDown size={14} /></button>
            <label className="editor-zoom"><SlidersHorizontal size={14} /><span>Масштаб</span><input aria-label="Масштаб шкалы" type="range" min=".8" max="2" step=".1" value={timelineZoom} onChange={(e) => setTimelineZoom(Number(e.target.value))} /></label>
          </div>
        </div>
        <div className="editor-timeline-grid">
          <div className="editor-track-labels"><div />
            <div>V1 · ВИДЕО</div><div>V2 · B-ROLL</div><div>V3 · СУБТИТРЫ</div>
            {audioTrackLabel("original", "A1", "ОРИГ.")}
            {audioTrackLabel("music", "A2", "МУЗЫКА")}
            {audioTrackLabel("sfx", "A3", "SFX")}
          </div>
          <div className="editor-timeline-scroll">
            <div className="editor-timeline-scale" style={{ width: (timelineZoom * 100) + "%" }}>
              <div className="editor-time-ruler" role="slider" tabIndex={0} aria-label="Позиция на таймлайне"
                aria-valuemin={0} aria-valuemax={+timelineDuration.toFixed(2)} aria-valuenow={+timelinePosition.toFixed(2)}
                aria-valuetext={`${fmt(timelinePosition)} из ${fmt(timelineDuration)}`} title="Щёлкните или перетащите указатель · стрелки: 0,1 с · Shift + стрелка: 1 с"
                onPointerDown={beginTimelineScrub} onPointerMove={moveTimelineScrub} onPointerUp={endTimelineScrub}
                onPointerCancel={endTimelineScrub} onKeyDown={keyboardTimelineScrub}>
                {timeMarks.map((time, i) => <span key={i} style={{ left: (markCount === 1 ? 0 : i / (markCount - 1) * 100) + "%" }}>{fmt(time)}</span>)}
              </div>
              <div className="editor-video-track">
                {segments.map((s, i) => {
                  const source = sources.find((x) => x.id === s.source);
                  const sourceDuration = source?.duration || s.end;
                  const selectClip = (event?: React.MouseEvent<HTMLDivElement>) => {
                    if (!event) { seekToSegment(s, s.start); return; }
                    const bounds = event.currentTarget.getBoundingClientRect();
                    const fraction = bounds.width > 0 ? Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) : 0;
                    seekToSegment(s, s.start + fraction * (s.end - s.start));
                  };
                  return <div key={s.id} className={"editor-timeline-clip " + (selected === s.id ? "selected" : "")}
                    role="group" aria-label={`Фрагмент ${i + 1}: ${fmt(s.start)}–${fmt(s.end)}`}
                    style={{ flex: Math.max(.2, s.end - s.start - effectiveTransition(segments, i)) + " 1 0" }}
                    draggable onDragStart={() => setDraggingSegment(s.id)} onDragEnd={() => setDraggingSegment(null)}
                    onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (draggingSegment) reorderSegment(draggingSegment, s.id); setDraggingSegment(null); }}
                    onClick={selectClip}>
                    <span className="editor-trim-handle left" role="slider" tabIndex={0} aria-label={`Начало фрагмента ${i + 1}`}
                      aria-valuemin={0} aria-valuemax={+(s.end - .1).toFixed(2)} aria-valuenow={+s.start.toFixed(2)} aria-valuetext={`${fmt(s.start)} секунды`}
                      title="Перетащите, чтобы изменить точку начала · ←/→ для точной настройки"
                      onPointerDown={(event) => beginTimelineTrim(event, s, "start")} onPointerMove={moveTimelineTrim}
                      onPointerUp={finishTimelineTrim} onPointerCancel={finishTimelineTrim} onLostPointerCapture={finishTimelineTrim}
                      onKeyDown={(event) => keyboardTimelineTrim(event, s, "start")} onClick={(event) => event.stopPropagation()} />
                    <div className="editor-timeline-clip-content" role="button" tabIndex={0} aria-label={`Выбрать фрагмент ${i + 1}: ${source?.path.split(/[\\/]/).pop() || "Видео"}`}
                      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectClip(); } }}>
                      <span className="editor-clip-number">{i + 1}</span><b>{source?.path.split(/[\\/]/).pop() || "Видео"}</b>
                      {s.transitionAfter && i < segments.length - 1 && <em className="editor-transition-marker" title={`Переход ${s.transitionAfter.type}, ${s.transitionAfter.duration.toFixed(1)} с`}>↔</em>}
                      <span>{fmt(s.end - s.start - effectiveTransition(segments, i))}</span>
                    </div>
                    <span className="editor-trim-handle right" role="slider" tabIndex={0} aria-label={`Конец фрагмента ${i + 1}`}
                      aria-valuemin={+(s.start + .1).toFixed(2)} aria-valuemax={+sourceDuration.toFixed(2)} aria-valuenow={+s.end.toFixed(2)} aria-valuetext={`${fmt(s.end)} секунды`}
                      title="Перетащите, чтобы изменить точку конца · ←/→ для точной настройки"
                      onPointerDown={(event) => beginTimelineTrim(event, s, "end")} onPointerMove={moveTimelineTrim}
                      onPointerUp={finishTimelineTrim} onPointerCancel={finishTimelineTrim} onLostPointerCapture={finishTimelineTrim}
                      onKeyDown={(event) => keyboardTimelineTrim(event, s, "end")} onClick={(event) => event.stopPropagation()} />
                  </div>;
                })}
                {sceneMarkers.map((marker) => <button key={marker.id} type="button" className="editor-scene-marker"
                  style={{ left: `${timelineDuration ? marker.timelineTime / timelineDuration * 100 : 0}%` }}
                  aria-label={`Проверить кандидата смены плана ${fmt(marker.sourceTime)}, визуальная разница ${Math.round(marker.score * 100)} процентов`}
                  title={`Возможная смена плана · ${fmt(marker.sourceTime)} · визуальная разница ${Math.round(marker.score * 100)}% · нажмите, чтобы проверить`}
                  onClick={(event) => { event.stopPropagation(); seekToAnalysisTime(marker.sourceTime, marker.segmentId); }}><i aria-hidden="true" /></button>)}
                {!segments.length && <div className="editor-timeline-empty">Перетащите исходник из медиатеки или нажмите «+» у видео</div>}
                {segments.length > 0 && <div className="editor-playhead" style={{ left: timelinePercent + "%" }}><i /></div>}
              </div>
              <div className="editor-broll-track">
                {brollClips.map((clip) => <button className="editor-broll-clip" key={clip.id} title="Нажмите, чтобы настроить B-roll"
                  draggable onDragEnd={(event) => {
                    const bounds = event.currentTarget.parentElement?.getBoundingClientRect();
                    if (!bounds || !timelineDuration) return;
                    pushUndo();
                    const maxAt = Math.max(0, timelineDuration - (clip.end - clip.start));
                    const at = Math.max(0, Math.min(maxAt, (event.clientX - bounds.left) / bounds.width * timelineDuration));
                    setBrollClips((all) => all.map((item) => item.id === clip.id ? { ...item, at } : item));
                  }}
                  style={{ left: `${timelineDuration ? clip.at / timelineDuration * 100 : 0}%`,
                    width: `${Math.max(2.5, timelineDuration ? (clip.end - clip.start) / timelineDuration * 100 : 2.5)}%` }}
                  onClick={() => { setPanel("edit"); setSelected(segments[0]?.id || ""); }}>
                  <Layers size={12} />{sources.find((source) => source.id === clip.source)?.path.split(/[\\/]/).pop() || "B-roll"}
                </button>)}
                {!brollClips.length && <span className="editor-track-placeholder">Добавьте видео значком слоёв в медиатеке</span>}
              </div>
              <div className="editor-caption-track">
                {segments.map((segment, index) => {
                  const words = sourceCaptions[segment.source]?.words || [];
                  const count = words.filter((word) => word.end > segment.start && word.start < segment.end).length;
                  return count ? <button key={segment.id} className="editor-caption-clip" title={`${count} слов · ${captionStyle}`}
                    style={{ left: `${timelineDuration ? timeline.starts[index] / timelineDuration * 100 : 0}%`,
                      width: `${Math.max(2.5, timelineDuration ? (segment.end - segment.start) / timelineDuration * 100 : 2.5)}%` }}
                    onClick={() => { setSelected(segment.id); seekTo(segment.start); }}>
                    <Captions size={12} />{count} слов · {captionStyle}
                  </button> : null;
                })}
                {!Object.keys(sourceCaptions).length && <span className="editor-track-placeholder">Расшифруйте речь — субтитры появятся здесь</span>}
              </div>
              <div className={`editor-original-track ${audioTrackAudible("original") ? "" : "muted"}`}>
                {segments.map((segment, index) => {
                  const source = sources.find((item) => item.id === segment.source);
                  return <div key={segment.id} className="editor-original-clip" title="Оригинальная аудиодорожка"
                    style={{ left: `${timelineDuration ? timeline.starts[index] / timelineDuration * 100 : 0}%`,
                      width: `${Math.max(2.5, timelineDuration ? (segment.end - segment.start) / timelineDuration * 100 : 2.5)}%` }}>
                    <AudioLines size={12} />{source?.path.split(/[\\/]/).pop() || "Original Audio"}
                  </div>;
                })}
              </div>
              <div className={`editor-music-track ${audioTrackAudible("music") ? "" : "muted"}`}>
                {music ? <div className="editor-audio-clip editor-audio-music" style={{ left: 3, right: 3 }}><b><AudioLines size={13} />{music.split(/[\\/]/).pop()}</b>
                  <div className="editor-waveform">{Array.from({ length: 42 }, (_, i) => <i key={i} style={{ height: (18 + Math.abs(Math.sin(i * 1.73) * Math.cos(i * .39)) * 70) + "%" }} />)}</div>
                </div> : <span className="editor-track-placeholder">Добавьте аудиофайл, чтобы разместить музыку</span>}
              </div>
              <div className={`editor-sfx-track ${audioTrackAudible("sfx") ? "" : "muted"}`}>
                {sfxClips.map((clip) => <div className="editor-audio-clip editor-audio-sfx" key={clip.id}
                  draggable title={`Перетащите для изменения позиции · ${clip.prompt} · ${fmt(clip.at)}`} onDragEnd={(event) => {
                    const bounds = event.currentTarget.parentElement?.getBoundingClientRect();
                    if (!bounds || !timelineDuration) return;
                    pushUndo();
                    const at = Math.max(0, Math.min(timelineDuration, (event.clientX - bounds.left) / bounds.width * timelineDuration));
                    setSfxClips((all) => all.map((item) => item.id === clip.id ? { ...item, at } : item));
                  }} style={{ left: `${timelineDuration ? clip.at / timelineDuration * 100 : 0}%`,
                    width: `${Math.max(2.5, timelineDuration ? clip.duration / timelineDuration * 100 : 2.5)}%` }}>
                  <b><AudioLines size={12} />SFX · {clip.prompt}</b>
                  <div className="editor-waveform">{Array.from({ length: 16 }, (_, i) => <i key={i} style={{ height: (18 + Math.abs(Math.sin(i * 2.17) * Math.cos(i * .52)) * 70) + "%" }} />)}</div>
                </div>)}
                {!sfxClips.length && <span className="editor-track-placeholder"><Music2 size={13} />Создайте эффект в панели «Звук» или через AI-чат</span>}
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
