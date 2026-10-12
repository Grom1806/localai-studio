import React, { useEffect, useState } from "react";
import { AudioLines, Clapperboard, Download, FolderOpen, Image as ImageIcon, Search, Trash2 } from "lucide-react";
import { api, downloadUrl, Generation, openFolder } from "../api";
import { Modal, Seg, useToast } from "../components/common";
import { useNav } from "../App";

export default function HistoryPage() {
  const toast = useToast();
  const { go } = useNav();
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Generation[]>([]);
  const [open, setOpen] = useState<Generation | null>(null);
  const load = () => api.get<Generation[]>(`/api/history?limit=200${type ? `&type=${type}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`).then(setRows);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [type, q]);

  const del = async (g: Generation) => {
    if (!confirm("Delete this item and its file(s)?")) return;
    await api.del(`/api/history/${g.id}`);
    setOpen(null); load(); toast("Deleted", "ok");
  };
  const Icon = (t: string) => (t === "image" ? ImageIcon : t === "video" ? Clapperboard : AudioLines);

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row">
        <div style={{ width: 360 }}><Seg value={type} onChange={setType} options={[{ v: "", label: "All" }, { v: "image", label: "Images" }, { v: "video", label: "Videos" }, { v: "audio", label: "Audio" }]} /></div>
        <div className="row grow" style={{ maxWidth: 420, marginLeft: "auto" }}><Search size={16} className="muted" /><input type="text" placeholder="Search prompts / models" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      {rows.length === 0 && <div className="muted">No generations yet.</div>}
      <div className="gallery">
        {rows.map((g) => {
          const I = Icon(g.type);
          return (
            <div key={g.id} className="tile" onClick={() => setOpen(g)} title={g.prompt}>
              {!g.exists[0] ? <div style={{ display: "grid", placeItems: "center", height: "100%" }} className="faint small">file missing</div>
                : g.type === "image" ? <img src={g.urls[0]} loading="lazy" />
                : g.type === "video" ? <video src={g.urls[0]} muted preload="metadata" />
                : <div style={{ display: "grid", placeItems: "center", height: "100%", padding: 14, textAlign: "center" }}><div><I size={28} className="muted" /><div className="small muted" style={{ marginTop: 6 }}>{g.prompt.slice(0, 90)}</div></div></div>}
              <div className="ov"><div className="row"><I size={13} /><span className="ellipsis grow">{g.created}</span></div></div>
            </div>
          );
        })}
      </div>
      {open && (
        <Modal title={`${open.type} · ${open.created}`} onClose={() => setOpen(null)} actions={<>
          {open.exists[0] && <a className="btn sm" href={downloadUrl(open.outputs[0])}><Download size={14} />Download</a>}
          {open.type === "image" && <button className="btn sm" onClick={() => go("video/generate", { image: open.outputs[0], prompt: open.prompt })}>Send to Video</button>}
          {open.type === "image" && <button className="btn sm" onClick={() => go("image/generate", { image: open.outputs[0], prompt: open.prompt })}>Use as input</button>}
          <button className="btn sm ghost" onClick={() => openFolder(open.outputs[0])}><FolderOpen size={14} /></button>
          <button className="btn sm ghost danger" onClick={() => del(open)}><Trash2 size={14} /></button>
        </>}>
          <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.4fr) minmax(280px, 1fr)", gap: 18 }}>
            <div>
              {open.type === "image" && <img src={open.urls[0]} style={{ maxWidth: "100%", borderRadius: 10 }} />}
              {open.type === "video" && <video src={open.urls[0]} controls loop style={{ maxWidth: "100%", borderRadius: 10 }} />}
              {open.type === "audio" && <audio src={open.urls[0]} controls />}
            </div>
            <div className="col">
              <div className="small muted">Prompt / text</div><div style={{ whiteSpace: "pre-wrap" }}>{open.prompt}</div>
              {open.negative && <><div className="small muted">Negative</div><div>{open.negative}</div></>}
              <div className="kv">
                <span className="k">Model</span><span>{open.model}</span>
                <span className="k">Engine</span><span>{open.engine}</span>
                <span className="k">Seed</span><span className="mono">{open.seed}</span>
                <span className="k">Time</span><span>{open.seconds}s</span>
                {open.peak_vram_mb != null && <><span className="k">Peak VRAM</span><span>{(open.peak_vram_mb / 1024).toFixed(1)} GB</span></>}
                <span className="k">File</span><span className="mono small" style={{ wordBreak: "break-all" }}>{open.outputs.join(", ")}</span>
                {open.inputs && Object.keys(open.inputs).length > 0 && <><span className="k">Inputs</span><span className="mono small" style={{ wordBreak: "break-all" }}>{JSON.stringify(open.inputs)}</span></>}
              </div>
              <details className="adv"><summary>All parameters</summary><pre className="mono small inner" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify(open.params, null, 2)}</pre></details>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
