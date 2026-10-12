import React, { useEffect, useRef, useState } from "react";
import { Play, RotateCw, Square } from "lucide-react";
import { api, fmtMB, ServiceInfo } from "../api";
import { Pill, useStatus, useToast } from "../components/common";
import { CleanupCard } from "../components/cleanup";
import { ModelActivity } from "../components/activity";

export default function SystemPage() {
  const { status, refresh } = useStatus();
  const toast = useToast();
  const [logs, setLogs] = useState<string[]>([]);
  const [logName, setLogName] = useState("api.log");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => { api.get<string[]>("/api/logs").then(setLogs); }, []);
  useEffect(() => {
    const f = () => api.get<{ text: string }>(`/api/logs/${logName}?lines=400`).then((r) => {
      setText(r.text);
      requestAnimationFrame(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; });
    }).catch(() => setText("(no log yet)"));
    f();
    const t = setInterval(f, 4000);
    return () => clearInterval(t);
  }, [logName]);

  const action = async (s: ServiceInfo, a: string) => {
    setBusy(s.name);
    try { await api.post(`/api/services/${s.name}/${a}`); toast(`${s.label}: ${a} done`, "ok"); }
    catch (e: any) { toast(e.message, "err"); } finally { setBusy(null); refresh(); }
  };

  if (!status) return null;
  return (
    <div className="col" style={{ gap: 16 }}>
      <ModelActivity />
      <CleanupCard />
      <div className="card" style={{ padding: 0 }}>
        <table className="tbl">
          <thead><tr><th>Service</th><th>Status</th><th>Port</th><th>PID</th><th>Model</th><th>VRAM (own)</th><th style={{ textAlign: "right" }}>Control</th></tr></thead>
          <tbody>
            {Object.values(status.services).map((s) => (
              <tr key={s.name}>
                <td><b>{s.label}</b>{s.error && <div className="small" style={{ color: "var(--bad)" }}>{s.error}</div>}</td>
                <td><Pill state={s.status} /></td>
                <td className="mono">127.0.0.1:{s.port}</td>
                <td className="mono muted">{s.pid ?? "–"}</td>
                <td className="muted">{s.kind === "tts" ? (s.health?.loaded ? "loaded" : "not loaded") : status.loaded.comfyui ?? "–"}</td>
                <td className="muted">{s.health?.vram_mb ? fmtMB(s.health.vram_mb) : "–"}</td>
                <td><div className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                  {s.status === "offline" || s.status === "error"
                    ? <button className="btn sm" disabled={busy === s.name} onClick={() => action(s, "start")}><Play size={13} />Start</button>
                    : <button className="btn sm ghost" disabled={busy === s.name} onClick={() => action(s, "stop")}><Square size={13} />Stop</button>}
                  <button className="btn sm" disabled={busy === s.name} onClick={() => action(s, "restart")}><RotateCw size={13} />Restart</button>
                </div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid cols-3">
        <div className="card kv">
          <span className="k">GPU</span><span>{status.gpu.name}</span>
          <span className="k">Driver</span><span>{status.gpu.driver}</span>
          <span className="k">VRAM</span><span>{fmtMB(status.gpu.vram_used_mb)} / {fmtMB(status.gpu.vram_total_mb)}</span>
          <span className="k">Utilization</span><span>{status.gpu.utilization ?? "–"}%</span>
          <span className="k">Power</span><span>{status.gpu.power_w ?? "–"} W</span>
        </div>
        <div className="card kv">
          <span className="k">RAM</span><span>{fmtMB(status.ram_used_mb)} / {fmtMB(status.ram_total_mb)}</span>
          <span className="k">RAM available</span><span>{fmtMB(status.ram_available_mb)}</span>
          <span className="k">CPU</span><span>{status.cpu_percent}%</span>
          <span className="k">Disk {status.drive}</span><span>{status.disk_free_gb} GB free</span>
        </div>
        <div className="card kv">
          {Object.entries(status.stats).map(([k, v]) => (
            <React.Fragment key={k}><span className="k">{k}</span><span>{v.count} done · avg {v.avg_seconds?.toFixed(1)}s · peak {v.avg_peak_vram_mb ? fmtMB(v.avg_peak_vram_mb) : "–"}</span></React.Fragment>
          ))}
          {!Object.keys(status.stats).length && <span className="muted">No statistics yet</span>}
        </div>
      </div>
      <div className="card col">
        <div className="row"><h3 style={{ margin: 0 }} className="grow">Logs</h3>
          <select style={{ width: 280 }} value={logName} onChange={(e) => setLogName(e.target.value)}>{logs.map((l) => <option key={l}>{l}</option>)}</select></div>
        <div className="logbox notranslate" ref={box}>{text}</div>
      </div>
    </div>
  );
}
