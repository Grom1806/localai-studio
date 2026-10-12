import React, { useEffect, useState } from "react";
import { Download, FolderOpen, Power, PowerOff, Trash2 } from "lucide-react";
import { api, Job, ModelInfo, openFolder, waitJob } from "../api";
import { Modal, Pill, useToast } from "../components/common";

export default function ModelsPage() {
  const toast = useToast();
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [installJob, setInstallJob] = useState<Job | null>(null);
  const [del, setDel] = useState<ModelInfo | null>(null);
  const [typed, setTyped] = useState("");
  const load = () => api.get<ModelInfo[]>("/api/models").then(setModels);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);

  const act = async (m: ModelInfo, action: "load" | "unload") => {
    setBusy(m.id);
    try {
      const r: any = await api.post(`/api/models/${encodeURIComponent(m.id)}/${action}`);
      toast(r?.message || (action === "load" ? `${m.name} loading` : `${m.name} unloaded`), "ok");
    } catch (e: any) { toast(e.message, "err"); } finally { setBusy(null); load(); }
  };
  const install = async (m: ModelInfo) => {
    setBusy(m.id);
    try {
      const queued = await api.post<Job>(`/api/models/${encodeURIComponent(m.id)}/install`);
      setInstallJob(queued);
      const done = await waitJob(queued.id, setInstallJob);
      if (done.status === "done") toast(`${m.name} installed`, "ok");
      else toast(done.error || `${m.name} could not be installed`, "err");
    } catch (e: any) { toast(e.message, "err"); }
    finally { setInstallJob(null); setBusy(null); void load(); }
  };
  const unloadAll = async () => { await api.post("/api/models/unload-all"); toast("All models unloaded from VRAM", "ok"); load(); };
  const doDelete = async () => {
    if (!del) return;
    try {
      await api.del(`/api/models/${encodeURIComponent(del.id)}?confirm=${encodeURIComponent(del.id)}`);
      toast(`${del.name} deleted`, "ok");
    } catch (e: any) { toast(e.message, "err"); }
    setDel(null); setTyped(""); load();
  };
  const total = models.filter((m) => !m.user).reduce((a, m) => a + m.size_gb, 0);

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <div className="grow muted">{models.length} models · {total.toFixed(1)} GB on disk (shared text encoders counted per model)</div>
        <button className="btn" onClick={unloadAll}><PowerOff size={15} />Unload everything</button>
        <button className="btn" onClick={() => openFolder("models")}><FolderOpen size={15} />Models folder</button>
      </div>
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        <table className="tbl">
          <thead><tr><th>Name</th><th>Type</th><th>Backend</th><th>Disk</th><th>Precision</th><th>VRAM est.</th><th>State</th><th style={{ textAlign: "right" }}>Actions</th></tr></thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.id}>
                <td><b>{m.name}</b><div className="small faint mono ellipsis" style={{ maxWidth: 380 }} title={m.paths.join("\n")}>{m.folder}</div></td>
                <td>{m.type}</td>
                <td className="muted">{m.backend}</td>
                <td>{m.size_gb.toFixed(1)} GB</td>
                <td className="muted">{m.precision}</td>
                <td className="muted">{m.vram_gb ? `~${m.vram_gb} GB` : "–"}</td>
                <td>{busy === m.id && installJob && m.installable
                  ? <Pill state="offline" label={`${Math.round(installJob.progress * 100)}%`} />
                  : !m.installed ? <Pill state="error" label={m.partial ? "INCOMPLETE" : "MISSING"} />
                    : m.installable ? <Pill state="online" label="READY · CPU" />
                      : m.loaded ? <Pill state="online" label="LOADED" /> : <Pill state="offline" label="UNLOADED" />}</td>
                <td>
                  <div className="row" style={{ justifyContent: "flex-end", gap: 6 }}>
                    {m.installable && !m.installed && <button className="btn sm primary" disabled={busy === m.id} onClick={() => void install(m)}>
                      {busy === m.id ? <Power size={13} className="spin" /> : <Download size={13} />}Install
                    </button>}
                    {!m.installable && !m.user && m.installed && (m.loaded
                      ? <button className="btn sm" disabled={busy === m.id} onClick={() => act(m, "unload")}><PowerOff size={13} />Unload</button>
                      : <button className="btn sm" disabled={busy === m.id} onClick={() => act(m, "load")}><Power size={13} />Load</button>)}
                    <button className="btn sm ghost icon" title="Open folder" onClick={() => openFolder(m.paths[0])}><FolderOpen size={14} /></button>
                    {m.deletable !== false && <button className="btn sm ghost icon danger" title="Delete" onClick={() => setDel(m)}><Trash2 size={14} /></button>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="small faint">Image and video models are loaded by ComfyUI automatically when you generate; the resource manager unloads them when another engine needs the GPU.
        Add LoRAs by copying <span className="mono">.safetensors</span> files into <span className="mono">models\image\loras</span>.</div>

      {del && (
        <Modal title={`Delete ${del.name}?`} onClose={() => { setDel(null); setTyped(""); }}>
          <div className="col" style={{ width: "min(520px, 85vw)" }}>
            <div className="notice err">This permanently deletes {del.size_gb.toFixed(1)} GB of model files from disk. Shared files used by other models are kept. You would need to download it again to use it.</div>
            <div className="small muted">Type <b className="mono">{del.id}</b> to confirm:</div>
            <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => { setDel(null); setTyped(""); }}>Cancel</button>
              <button className="btn danger" disabled={typed !== del.id} onClick={doDelete}><Trash2 size={14} />Delete permanently</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
