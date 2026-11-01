import { useEffect, useState } from "react";
import { api, pending, type Artifact, type ArtifactFull } from "./api.ts";
import { Grid } from "./Grid.tsx";
import { TweakPanel } from "./Tweak.tsx";
import { Versions } from "./Versions.tsx";
import { Confirm } from "./Modal.tsx";
import { Link, navigate } from "./router.tsx";
import { dropFromLastList, neighbours, useApp } from "./state.tsx";

// One image and its recipe (workflow 4).
export function ArtifactPage({ id }: { id: string }) {
  const { projects, reloadProjects, catalogue } = useApp();
  const [a, setA] = useState<ArtifactFull | null>(null);
  const [kids, setKids] = useState<Artifact[]>([]);
  const [missing, setMissing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  const [keepSeed, setKeepSeed] = useState(false);
  const [zoom, setZoom] = useState(false);
  const { prev, next } = neighbours(id);

  useEffect(() => { api.artifact(id).then(x => { setA(x); setKids(x.children); }).catch(() => setMissing(true)); }, [id]);
  useEffect(() => {                      // keep queued variations fresh
    if (!kids.some(pending)) return;
    const t = setTimeout(async () => {
      const fresh = await Promise.all(kids.map(k => pending(k) ? api.artifact(k.id).catch(() => k) : Promise.resolve(k)));
      setKids(fresh);
    }, 1500);
    return () => clearTimeout(t);
  }, [kids]);
  useEffect(() => {                      // keep a pending one fresh
    if (!a || !pending(a)) return;
    const t = setTimeout(() => api.artifact(id).then(x => setA(x)).catch(() => {}), 1500);
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
            ? <img src={a.image_url} alt={a.prompt} className={a.preset === "sprite" || a.preset === "item_sprite" || zoom ? "pixel" : ""}
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
          <TweakPanel a={a} onQueued={k => setKids(cur => [...k, ...cur])} />
          {a.commercial === false && <p className="warn small">{a.model_label} is under a non-commercial licence: this image may not be used commercially.</p>}
          <dl>
            <dt>Project</dt><dd><Link to={`/p/${a.project.id}`}>{a.project.name}</Link> <span className={`vis ${a.project.visibility}`}>{a.project.visibility}</span></dd>
            <dt>Model</dt><dd>{a.model_label}{a.ran_model && a.ran_model !== a.model ? ` (ran on ${a.ran_model})` : ""}</dd>
            {a.parent && <><dt>Made from</dt><dd><Link to={`/a/${a.parent.id}`} className="made-from">
              {a.parent.image_url && <img src={a.parent.image_url} alt="" className={a.parent.preset === "sprite" ? "pixel" : ""} />}
              <span>{opLabel(a.op)}</span></Link></dd></>}
            <dt>Preset</dt><dd>{catalogue.presets.find(p => p.name === a.preset)?.label ?? a.preset}{a.background ? ` · ${a.background} background` : ""}</dd>
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
      <Versions a={a} onChange={vs => setA({ ...a, variants: vs })} />
      {kids.length > 0 && <>
        <h2 className="section">Made from this</h2>
        <Grid items={kids} loading={false} error={null} next={null} more={() => {}} retry={() => {}} empty="" />
      </>}
      {confirm && <Confirm title="Delete this image?" body="It's removed for good, including from the project." action="Delete"
        busy={busy} error={delError} onConfirm={del} onClose={() => setConfirm(false)} />}
    </div>
  );
}

function opLabel(op: string) {
  return op === "reroll" ? "re-rolled from" : op === "edit_prompt" ? "prompt changed from" : op === "edit_image" ? "edited from" : "from";
}
