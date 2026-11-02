import { useEffect, useState } from "react";
import { sheetsApi, type Artifact, type SheetBrief } from "./api.ts";
import { Modal } from "./Modal.tsx";
import { ProjectPicker } from "./ProjectPicker.tsx";
import { Link, navigate } from "./router.tsx";
import { useApp } from "./state.tsx";

// Character sheets list (workflow 13): your sheets (Characters page) or a project's (project page).
export function SheetList({ projectId, canCreate, title }: { projectId?: string; canCreate: boolean; title?: string }) {
  const [rows, setRows] = useState<SheetBrief[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const load = () => {
    setError(null);
    sheetsApi.list(projectId).then(r => setRows(r.sheets)).catch(e => setError(e instanceof Error ? e.message : String(e)));
  };
  useEffect(load, [projectId]);

  if (projectId && rows && rows.length === 0 && !canCreate) return null;     // someone else's project, no sheets
  return (
    <section className="section">
      <div className="head-row">
        <h2>{title ?? "Characters"}</h2>
        {canCreate && <button className="primary" onClick={() => setCreating(true)}>New character</button>}
      </div>
      {error && <div className="banner"><span className="error">{error}</span><button onClick={load}>Retry</button></div>}
      {!rows && !error && <div className="spinner" />}
      {rows && rows.length === 0 && (
        <div className="empty">No characters yet. A character sheet keeps one character consistent across poses,
          expressions and weapon sets. {canCreate && <button className="ghost" onClick={() => setCreating(true)}>Start one</button>}</div>)}
      {rows && rows.length > 0 && (
        <div className="pgrid">
          {rows.map(r => (
            <Link key={r.id} to={`/s/${r.id}`} className="pcard">
              <div className="cover sheetcover">{r.cover_url ? <img src={r.cover_url} alt="" /> : <span className="muted small">Picking a base…</span>}</div>
              <div className="pmeta">
                <strong>{r.name}</strong>
                <span className="muted small">{r.project.name} · {r.kept} kept{r.by && !r.can_edit ? ` · by ${r.by}` : ""}</span>
              </div>
            </Link>))}
        </div>)}
      {creating && <NewSheetDialog projectId={projectId} onClose={() => setCreating(false)} />}
    </section>
  );
}

// "New character" (workflow 10): from a description, or from an existing image (`base`), which skips stage 1.
export function NewSheetDialog({ projectId, base, onClose }: { projectId?: string; base?: Artifact; onClose: () => void }) {
  const { catalogue, projects } = useApp();
  const defModel = catalogue.models.find(m => m.default)?.name ?? catalogue.models[0].name;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState(defModel);
  const [background, setBackground] = useState("magenta");
  const [project, setProject] = useState<string | null>(projectId ?? (base && projects?.mine.some(p => p.id === base.project.id) ? base.project.id : null));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const m = catalogue.models.find(x => x.name === model);
  const missing = !name.trim() ? "Name the character" : !base && !description.trim() ? "Describe the character" : null;

  const go = async () => {
    if (missing) return;
    setBusy(true); setError(null);
    try {
      const sh = await sheetsApi.create({
        name: name.trim(), model, background, project_id: project ?? undefined,
        ...(base ? { base_artifact_id: base.id } : { description: description.trim() }),
      });
      navigate(`/s/${sh.id}`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };

  return (
    <Modal title={base ? "Make a character sheet" : "New character"} onClose={onClose}>
      <form className="form" onSubmit={e => { e.preventDefault(); go(); }}>
        <label>Name
          <input autoFocus value={name} onChange={e => setName(e.target.value)} maxLength={100} placeholder="Grub the archer" />
        </label>
        {base ? (
          <p className="muted small">The base is this image: <em>{base.prompt}</em>. Next you add poses, expressions and
            weapon sets; each is an edit of it.</p>
        ) : (<>
          <label>Description
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3} maxLength={4000}
              placeholder="a goblin archer with a brown beard and leather armour" />
            <span className="muted small">You get 4 to choose from. Freeze the ones you like and re-roll the rest.</span>
          </label>
          <div className="prow controls">
            <div className="seg" role="radiogroup" aria-label="model">
              {catalogue.models.map(x => (
                <button type="button" key={x.name} role="radio" aria-checked={model === x.name} title={x.note}
                  className={model === x.name ? "on" : ""} onClick={() => setModel(x.name)}>{x.label.replace("FLUX.2 klein ", "")}</button>))}
            </div>
            <div className="seg" role="radiogroup" aria-label="background">
              {catalogue.backgrounds.map(b => (
                <button type="button" key={b} role="radio" aria-checked={background === b}
                  className={(background === b ? "on " : "") + "bg-" + b} onClick={() => setBackground(b)}>
                  <span className="swatch" />{b}</button>))}
            </div>
          </div>
          {m && !m.commercial && <p className="warn small">{m.label}: {m.licence}. Its images may not be used commercially.</p>}
          {m && !m.edits && <p className="muted small">{m.label} draws the base; the variations are edits, made with
            {" "}{catalogue.models.find(x => x.default)?.label}.</p>}
        </>)}
        {!projectId && (
          <label>Project
            <ProjectPicker projects={projects?.mine ?? []} value={project} onChange={setProject} />
          </label>)}
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="button" className="ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={busy || !!missing} title={missing ?? ""}>
            {busy ? "Starting…" : base ? "Create sheet" : "Generate 4"}</button>
        </div>
      </form>
    </Modal>
  );
}
