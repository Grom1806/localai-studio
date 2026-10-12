import React, { useEffect, useState } from "react";
import { Brush, MemoryStick, Trash2 } from "lucide-react";
import { api, fmtMB } from "../api";
import { Meter, useStatus, useToast } from "./common";

const fmtB = (b: number) => fmtMB(b / 2 ** 20);
type TempInfo = { temp_bytes: number; partial_bytes: number; total_bytes: number; busy: boolean };
type RamResult = { freed_bytes: number; available_bytes: number; unloaded_models: boolean; message: string | null };

/** "Free RAM" and "Clear temporary files" (Dashboard and System pages). */
export function CleanupCard() {
  const { status } = useStatus();
  const toast = useToast();
  const [info, setInfo] = useState<TempInfo | null>(null);
  const [busy, setBusy] = useState<"" | "ram" | "temp">("");
  const loadInfo = () => api.get<TempInfo>("/api/cleanup/temp").then(setInfo).catch(() => {});
  useEffect(() => { loadInfo(); }, []);

  const freeRam = async () => {
    setBusy("ram");
    try {
      const r = await api.post<RamResult>("/api/cleanup/ram");
      toast(r.message ?? `RAM freed: ${fmtB(r.freed_bytes)} · available ${fmtB(r.available_bytes)}`, r.message ? "info" : "ok");
    } catch (e: any) { toast(e.message, "err"); }
    setBusy("");
  };
  const cleanTemp = async () => {
    setBusy("temp");
    try {
      const r = await api.post<{ ok: boolean; freed_bytes: number; message?: string }>("/api/cleanup/temp");
      toast(r.ok ? `Temporary files removed: ${fmtB(r.freed_bytes)}` : r.message!, r.ok ? "ok" : "info");
    } catch (e: any) { toast(e.message, "err"); }
    await loadInfo();
    setBusy("");
  };

  const ramUsed = status ? status.ram_total_mb - status.ram_available_mb : 0;
  return (
    <div className="card col">
      <h3>Memory and cleanup</h3>
      {status && (
        <div className="col" style={{ gap: 6 }}>
          <div className="row small"><span className="grow muted">System RAM</span>
            <span>{fmtMB(ramUsed)} / {fmtMB(status.ram_total_mb)}</span></div>
          <Meter value={ramUsed} max={status.ram_total_mb} />
        </div>
      )}
      <div className="row wrap">
        <button className="btn primary" disabled={!!busy} onClick={freeRam}>
          <MemoryStick size={15} />{busy === "ram" ? "Freeing…" : "Free RAM"}</button>
        <button className="btn" disabled={!!busy || !info?.total_bytes} onClick={cleanTemp}>
          <Brush size={15} />{busy === "temp" ? "Cleaning…" : "Clear temporary files"}
          {!!info?.total_bytes && <span className="muted notranslate">({fmtB(info.total_bytes)})</span>}</button>
      </div>
      <div className="small faint">
        Free RAM unloads all models from memory and returns LocalAI's unused memory to Windows. Models load again automatically on the next generation.
        Temporary files are work folders and interrupted downloads; your results, uploads, models, voices and projects are kept.
      </div>
    </div>
  );
}

/** Small top-bar button: one click frees RAM. */
export function FreeRamButton() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  return (
    <button className="btn ghost sm icon" title="Free RAM" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        const r = await api.post<RamResult>("/api/cleanup/ram");
        toast(r.message ?? `RAM freed: ${fmtB(r.freed_bytes)} · available ${fmtB(r.available_bytes)}`, r.message ? "info" : "ok");
      } catch (e: any) { toast(e.message, "err"); }
      setBusy(false);
    }}>{busy ? <span className="spin" style={{ width: 14, height: 14 }} /> : <Trash2 size={15} />}</button>
  );
}
