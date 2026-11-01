import { useState } from "react";
import { api, type Project, type Visibility } from "./api.ts";
import { Modal } from "./Modal.tsx";

// Create or edit a project (workflow 5).
export function ProjectDialog({ project, onDone, onClose }: {
  project?: Project; onDone: (p: Project) => void; onClose: () => void;
}) {
  const [name, setName] = useState(project?.name ?? "");
  const [description, setDescription] = useState(project?.description ?? "");
  const [visibility, setVisibility] = useState<Visibility>(project?.visibility ?? "private");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameErr = name.trim() === "" ? "A project needs a name" : null;

  const save = async () => {
    if (nameErr) return;
    setBusy(true); setError(null);
    try {
      const b = { name: name.trim(), description, visibility };
      onDone(project ? await api.updateProject(project.id, b) : await api.createProject(b));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  return (
    <Modal title={project ? "Edit project" : "New project"} onClose={onClose}>
      <form className="form" onSubmit={e => { e.preventDefault(); save(); }}>
        <label>Name
          <input autoFocus value={name} onChange={e => setName(e.target.value)} maxLength={100}
            aria-invalid={!!(error || (nameErr && name !== ""))} />
          {error && <span className="error small">{error}</span>}
        </label>
        <label>Description <span className="muted small">(optional)</span>
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3} maxLength={2000} />
        </label>
        <fieldset className="vis-pick">
          <legend>Who can see it</legend>
          <label><input type="radio" checked={visibility === "private"} onChange={() => setVisibility("private")} />
            <span><strong>Private</strong> <span className="muted small">only you</span></span></label>
          <label><input type="radio" checked={visibility === "public"} onChange={() => setVisibility("public")} />
            <span><strong>Public</strong> <span className="muted small">everyone on the platform can view it; only you add to it</span></span></label>
        </fieldset>
        <div className="actions">
          <button type="button" className="ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={busy || !!nameErr}>{busy ? "Saving…" : project ? "Save" : "Create"}</button>
        </div>
      </form>
    </Modal>
  );
}
