// Thin typed wrapper around the LocalAI orchestrator API (same origin).

export type ServiceInfo = {
  name: string; label: string; kind: string; status: "online" | "offline" | "starting" | "error";
  port: number; pid: number | null; error: string | null; installed: boolean;
  health: { loaded?: boolean; status?: string; vram_mb?: number; engine?: string };
};

export type Job = {
  id: string; kind: "image" | "video" | "voice"; status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number; message: string; step: string | null; error: string | null; elapsed: number;
  result: { outputs: string[]; seconds: number; seed: number; peak_vram_mb?: number; duration?: number; [k: string]: unknown } | null;
  generation_id: number | null; prompt: string;
};

export type Status = {
  gpu: { available: boolean; name: string; vram_total_mb: number; vram_used_mb: number; vram_free_mb: number;
         temperature_c: number | null; utilization: number | null; power_w: number | null; driver: string };
  ram_total_mb: number; ram_used_mb: number; ram_available_mb: number; ram_percent: number;
  disk_total_gb: number; disk_free_gb: number; disk_used_gb: number; drive: string; cpu_percent: number;
  services: Record<string, ServiceInfo>;
  current_job: Job | null; queue_length: number; loaded: Record<string, string>; vram_mode: string; video_installed: boolean;
  stats: Record<string, { count: number; avg_seconds: number; avg_peak_vram_mb: number }>;
};

export type Generation = {
  id: number; created: string; type: "image" | "video" | "audio"; engine: string; model: string;
  prompt: string; negative: string | null; seed: number; params: Record<string, any>; inputs: Record<string, any>;
  outputs: string[]; urls: string[]; exists: boolean[]; seconds: number; peak_vram_mb: number | null;
};

export type Voice = { id: number; name: string; ref_path: string; url: string; language: string | null;
  transcript: string | null; notes: string | null; created: string };

export type ModelInfo = { id: string; name: string; type: string; backend: string; family: string; precision: string;
  vram_gb: number | null; installed: boolean; partial?: boolean; size_gb: number; paths: string[]; folder: string;
  loaded: boolean; license?: string; user?: boolean; installable?: boolean; deletable?: boolean };

export type Presets = {
  image: Record<string, any>; video: Record<string, any>; voice: Record<string, any>;
  image_models: Record<string, any>;
};

export class ApiError extends Error {}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch {
    throw new ApiError("LocalAI server is not reachable. Is it running?");
  }
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) {
    const d = data?.detail;
    throw new ApiError(typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : `Request failed (${r.status})`);
  }
  return data as T;
}

export const api = {
  get: <T,>(u: string) => req<T>("GET", u),
  post: <T,>(u: string, b?: unknown) => req<T>("POST", u, b ?? {}),
  put: <T,>(u: string, b?: unknown) => req<T>("PUT", u, b),
  patch: <T,>(u: string, b?: unknown) => req<T>("PATCH", u, b),
  del: <T,>(u: string) => req<T>("DELETE", u),
  upload: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ path: string; url: string; name: string }>("POST", "/api/upload", fd);
  },
};

export const fileUrl = (rel: string) => "/api/files/" + rel.split("/").map(encodeURIComponent).join("/");
export const downloadUrl = (rel: string) => fileUrl(rel) + "?download=true";
export const mp3Url = (rel: string) => "/api/mp3/" + rel.split("/").map(encodeURIComponent).join("/");
export const openFolder = (path: string) => api.post("/api/open-folder", { path });

/** Poll a job until it finishes. */
export async function waitJob(id: string, onUpdate: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api.get<Job>(`/api/jobs/${id}`);
    onUpdate(j);
    if (["done", "error", "cancelled"].includes(j.status)) return j;
    await new Promise((r) => setTimeout(r, 700));
  }
}

export const fmtMB = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);
