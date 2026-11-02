import { useEffect, useState } from "react";
import { api, type Project } from "./api.ts";
import { Grid, useArtifacts } from "./Grid.tsx";
import { ProjectDialog } from "./ProjectDialog.tsx";
import { PromptBar } from "./PromptBar.tsx";
import { SheetList } from "./Sheets.tsx";
import { Link } from "./router.tsx";
import { useApp } from "./state.tsx";

// A project's grid (workflows 5 and 6). Mine: edit + a generate bar pinned to it. Someone else's public one: read-only.
export function ProjectPage({ id }: { id: string }) {
  const { reloadProjects } = useApp();
  const [p, setP] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);
  const [editing, setEditing] = useState(false);
  const list = useArtifacts(id);
  useEffect(() => { api.project(id).then(setP).catch(() => setMissing(true)); }, [id]);

  if (missing) return <div className="panel"><h2>Not found, or not shared with you</h2><Link to="/projects">All projects</Link></div>;
  if (!p) return <div className="page"><div className="spinner" /></div>;
  return (
    <div className="page">
      <div className="head-row">
        <div>
          <h2>{p.name} <span className={`vis ${p.visibility}`}>{p.visibility}</span></h2>
          <p className="muted small">{p.mine ? "Your project" : `Shared by ${p.owner}`} · {p.artifacts} image{p.artifacts === 1 ? "" : "s"}</p>
          {p.description && <p className="desc">{p.description}</p>}
        </div>
        {p.mine && <button onClick={() => setEditing(true)}>Edit</button>}
      </div>
      <SheetList projectId={p.id} canCreate={p.mine} />
      {p.mine && <PromptBar project={p} onQueued={a => { list.prepend(a); setP({ ...p, artifacts: p.artifacts + a.length }); }} />}
      <Grid {...list} empty={p.mine ? "No images in this project yet. Generate some above." : "This project has no images yet."} />
      {editing && <ProjectDialog project={p} onClose={() => setEditing(false)}
        onDone={np => { setEditing(false); setP({ ...p, ...np }); reloadProjects(); }} />}
    </div>
  );
}
