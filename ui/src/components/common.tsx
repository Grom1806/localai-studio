import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ImagePlus, Loader2, Music, X } from "lucide-react";
import { api, Job, Status, fileUrl, waitJob } from "../api";

/* в”Ђв”Ђ toasts в”Ђв”Ђ */
type Toast = { id: number; text: string; kind: "ok" | "err" | "info" };
const ToastCtx = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "err" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-wrap">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

/* в”Ђв”Ђ live system status (polled) в”Ђв”Ђ */
const StatusCtx = createContext<{ status: Status | null; refresh: () => void; offline: boolean }>({ status: null, refresh: () => {}, offline: false });
export const useStatus = () => useContext(StatusCtx);

export function StatusProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [offline, setOffline] = useState(false);
  const refresh = useCallback(async () => {
    try { setStatus(await api.get<Status>("/api/status")); setOffline(false); } catch { setOffline(true); }
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [refresh]);
  return <StatusCtx.Provider value={{ status, refresh, offline }}>{children}</StatusCtx.Provider>;
}

/* в”Ђв”Ђ status pill в”Ђв”Ђ */
export function Pill({ state, label }: { state: string; label?: string }) {
  const map: Record<string, string> = { online: "ONLINE", offline: "OFFLINE", starting: "LOADING", loading: "LOADING", error: "ERROR", ready: "READY" };
  return <span className={`pill ${state}`}><span className="dot" />{label ?? map[state] ?? state.toUpperCase()}</span>;
}

/* в”Ђв”Ђ segmented control в”Ђв”Ђ */
export function Seg<T extends string>({ value, options, onChange }: { value: T; options: { v: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map((o) => <button key={o.v} className={value === o.v ? "on" : ""} onClick={() => onChange(o.v)} type="button">{o.label}</button>)}
    </div>
  );
}

/* в”Ђв”Ђ labelled field в”Ђв”Ђ */
export function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return <label className="field"><span className="lab"><span>{label}</span>{hint && <span className="faint">{hint}</span>}</span>{children}</label>;
}

export function NumberField({ label, value, onChange, min, max, step, hint }: {
  label: string; value: number | ""; onChange: (v: number | "") => void; min?: number; max?: number; step?: number; hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input type="number" value={value} min={min} max={max} step={step}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />
    </Field>
  );
}

export function Slider({ label, value, onChange, min, max, step, fmt }: {
  label: string; value: number; onChange: (v: number) => void; min: number; max: number; step: number; fmt?: (v: number) => string;
}) {
  return (
    <Field label={label} hint={fmt ? fmt(value) : String(value)}>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </Field>
  );
}

/* в”Ђв”Ђ upload drop zone в”Ђв”Ђ */
export function DropZone({ accept, value, onChange, label, kind = "image" }: {
  accept: string; value: string | null; onChange: (path: string | null) => void; label: string; kind?: "image" | "audio" | "media";
}) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const handle = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    try { const r = await api.upload(f); onChange(r.path); } catch (e: any) { toast(e.message, "err"); } finally { setBusy(false); }
  };
  const isVideo = value && /\.(mp4|webm|mov)$/i.test(value);
  return (
    <div className={`drop ${over ? "over" : ""}`} onClick={() => input.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); handle(e.dataTransfer.files[0]); }}>
      <input ref={input} type="file" accept={accept} hidden onChange={(e) => handle(e.target.files?.[0])} />
      {busy ? <Loader2 className="spin" size={18} /> : value ? (
        <>
          {kind === "audio" ? <audio src={fileUrl(value)} controls onClick={(e) => e.stopPropagation()} />
            : isVideo ? <video src={fileUrl(value)} muted style={{ maxHeight: 120, maxWidth: "100%", borderRadius: 8 }} />
            : <img src={fileUrl(value)} />}
          <div className="small ellipsis">{value.split("/").pop()}</div>
          <button className="btn ghost icon x" type="button" onClick={(e) => { e.stopPropagation(); onChange(null); }}><X size={14} /></button>
        </>
      ) : (
        <div className="col" style={{ alignItems: "center", gap: 4 }}>
          {kind === "audio" ? <Music size={18} /> : <ImagePlus size={18} />}
          <span>{label}</span>
          <span className="faint small">click or drop a file</span>
        </div>
      )}
    </div>
  );
}

/* в”Ђв”Ђ job progress panel в”Ђв”Ђ */
export function JobProgress({ job, onCancel }: { job: Job; onCancel?: () => void }) {
  return (
    <div className="progress-wrap">
      <Loader2 size={26} className="spin" style={{ color: "var(--accent)" }} />
      <div style={{ marginTop: 10, fontWeight: 600 }}>{job.status === "queued" ? "Waiting in queueвЂ¦" : job.message}</div>
      <div className="meter"><div style={{ width: `${Math.max(3, job.progress * 100)}%` }} /></div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="small muted">{Math.round(job.progress * 100)}% В· {job.elapsed.toFixed(0)} s</span>
        {onCancel && <button className="btn ghost sm" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

/* в”Ђв”Ђ modal в”Ђв”Ђ */
export function Modal({ title, onClose, children, actions }: { title: string; onClose: () => void; children: React.ReactNode; actions?: React.ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="mhead"><b className="grow ellipsis">{title}</b>{actions}<button className="btn ghost icon" onClick={onClose}><X size={16} /></button></div>
        <div className="mbody">{children}</div>
      </div>
    </div>
  );
}

export function Meter({ value, max }: { value: number; max: number }) {
  const pct = max ? (value / max) * 100 : 0;
  return <div className={`meter ${pct > 92 ? "bad" : pct > 80 ? "warn" : ""}`}><div style={{ width: `${pct}%` }} /></div>;
}

/** Hook: run a generation job and track it. */
export function useJob() {
  const [job, setJob] = useState<Job | null>(null);
  const toast = useToast();
  const run = useCallback(async (url: string, body: unknown): Promise<Job | null> => {
    try {
      const j = await api.post<Job>(url, body);
      setJob(j);
      const done = await waitJob(j.id, setJob);
      if (done.status === "error") toast(done.error || "Generation failed", "err");
      return done;
    } catch (e: any) {
      toast(e.message, "err");
      setJob(null);
      return null;
    }
  }, [toast]);
  const cancel = useCallback(async () => { if (job) await api.post(`/api/jobs/${job.id}/cancel`); }, [job]);
  return { job, run, cancel, setJob };
}
