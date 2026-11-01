import { useEffect, useState } from "react";
import { api, pending, type Artifact } from "./api.ts";
import { Confirm } from "./Modal.tsx";
import { Link, navigate } from "./router.tsx";
import { dropFromLastList, neighbours, useApp } from "./state.tsx";

// One image and its recipe (workflow 4).
export function ArtifactPage({ id }: { id: string }) {
  const { projects, reloadProjects, catalogue } = useApp();
  const [a, setA] = useState<Artifact | null>(null);
  const [missing, setMissing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  const [keepSeed, setKeepSeed] = useState(false);
  const [zoom, setZoom] = useState(false);
  const { prev, next } = neighbours(id);

  useEffect(() => { api.artifact(id).then(setA).catch(() => setMissing(true)); }, [id]);
  useEffect(() => {                      // keep a pending one fresh
    if (!a || !pending(a)) return;
    const t = setTimeout(() => api.artifact(id).then(setA).catch(() => {}), 1500);
    return () => clearTimeout(t);
  }, [a, id]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea, [role=dialog]")) return;
      if (e.key === "ArrowLeft" && prev) navigate(`/a/${prev}`);
      if (e.key === "ArrowRight" && next) navigate(`/a/${next}`);
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [prev, next]);

  if (missing) return <div className="panel"><h2>Not found, or not shared with you</h2><Link to="/">Back home</Link></div>;
  if (!a) return <div className="page"><div className="spinner" /></div>;
  const mineProject = !!projects?.mine.some(p => p.id === a.project.id);

  const del = async () => {
    setBusy(true); setDelError(null);
    try {
      await api.deleteArtifact(a.id);
      const { next: n, prev: p } = neighbours(a.id);
      dropFromLastList(a.id);
      reloadProjects();
      if (n || p) navigate(`/a/${n ?? p}`); else history.length > 1 ? history.back() : navigate("/");
    } catch (e) { setDelError(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };

  return (
    <div className="page apage">
      <div className="apage-nav">
        <button className="ghost" onClick={() => history.length > 1 ? history.back() : navigate("/")}>← Back</button>
        <span className="spacer" />
        <button className="ghost" disabled={!prev} onClick={() => prev && navigate(`/a/${prev}`)} title="Previous (←)">‹ Prev</button>
        <button className="ghost" disabled={!next} onClick={() => next && navigate(`/a/${next}`)} title="Next (→)">Next ›</button>
      </div>
      <div className="apage-body">
        <div className={`viewer ${zoom ? "zoom" : ""}`}>
          {a.status === "done" && a.image_url
            ? <img src={a.image_url} alt={a.prompt} className={a.preset === "sprite" || zoom ? "pixel" : ""}
                onClick={() => setZoom(z => !z)} title={zoom ? "Fit" : "Actual pixels"} />
            : a.status === "failed"
              ? <div className="state"><strong>Generation failed</strong><span>{a.error}</span></div>
              : <div className="state"><div className="spinner" /><span>{a.status === "running" ? "Generating…" : "Queued…"}</span></div>}
        </div>
        <aside className="recipe">
          <p className="prompt">{a.prompt}</p>
          <div className="actions-col">
            {a.status === "done" && <a className="button primary" href={`${a.image_url}?download=1`}>Download</a>}
            <div className="row">
              <button onClick={() => navigate(`/?from=${a.id}${keepSeed ? "&seed=1" : ""}`)}>Use these settings</button>
              <label className="small"><input type="checkbox" checked={keepSeed} onChange={e => setKeepSeed(e.target.checked)} /> same seed</label>
            </div>
            {mineProject && <button className="danger ghost" onClick={() => setConfirm(true)}>Delete</button>}
          </div>
          {a.commercial === false && <p className="warn small">{a.model_label} is under a non-commercial licence: this image may not be used commercially.</p>}
          <dl>
            <dt>Project</dt><dd><Link to={`/p/${a.project.id}`}>{a.project.name}</Link> <span className={`vis ${a.project.visibility}`}>{a.project.visibility}</span></dd>
            <dt>Model</dt><dd>{a.model_label}{a.ran_model && a.ran_model !== a.model ? ` (ran on ${a.ran_model})` : ""}</dd>
            <dt>Preset</dt><dd>{catalogue.presets.find(p => p.name === a.preset)?.label ?? a.preset}</dd>
            <dt>Seed</dt><dd>{a.seed ?? "—"}</dd>
            <dt>Size</dt><dd>{a.width} × {a.height}</dd>
            <dt>Steps</dt><dd>{a.steps ?? "—"}</dd>
            <dt>GPU time</dt><dd>{a.seconds != null ? `${a.seconds.toFixed(1)} s` : "—"}{a.worker ? ` on ${a.worker}` : ""}</dd>
            <dt>By</dt><dd>{a.by}</dd>
            <dt>Made</dt><dd>{new Date(a.created_at).toLocaleString()}</dd>
          </dl>
          {a.full_prompt !== a.prompt && (
            <details><summary className="small">Full prompt sent to the model</summary><p className="small mono">{a.full_prompt}</p></details>)}
        </aside>
      </div>
      {confirm && <Confirm title="Delete this image?" body="It's removed for good, including from the project." action="Delete"
        busy={busy} error={delError} onConfirm={del} onClose={() => setConfirm(false)} />}
    </div>
  );
}
