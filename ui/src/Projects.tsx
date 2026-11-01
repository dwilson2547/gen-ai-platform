import { useState } from "react";
import type { Project } from "./api.ts";
import { ProjectDialog } from "./ProjectDialog.tsx";
import { Link, navigate } from "./router.tsx";
import { useApp } from "./state.tsx";

export function Projects() {
  const { projects, reloadProjects } = useApp();
  const [creating, setCreating] = useState(false);
  return (
    <div className="page">
      <div className="head-row">
        <h2>My projects</h2>
        <button className="primary" onClick={() => setCreating(true)}>New project</button>
      </div>
      {projects === null ? <div className="pgrid">{[0, 1, 2].map(i => <div key={i} className="pcard skel" />)}</div>
        : <div className="pgrid">{projects.mine.map(p => <PCard key={p.id} p={p} />)}</div>}
      <h2 className="section">Shared by others</h2>
      {projects === null ? null : projects.public.length === 0
        ? <p className="empty">No one has shared a project yet.</p>
        : <div className="pgrid">{projects.public.map(p => <PCard key={p.id} p={p} />)}</div>}
      {creating && <ProjectDialog onClose={() => setCreating(false)}
        onDone={async p => { setCreating(false); await reloadProjects(); navigate(`/p/${p.id}`); }} />}
    </div>
  );
}

function PCard({ p }: { p: Project }) {
  return (
    <Link to={`/p/${p.id}`} className="pcard">
      <div className="cover">{p.cover_url ? <img src={p.cover_url} alt="" loading="lazy" /> : <span className="muted small">No images yet</span>}</div>
      <div className="pmeta">
        <strong>{p.name}</strong>
        <span className="muted small">{p.artifacts} image{p.artifacts === 1 ? "" : "s"}{!p.mine && ` · ${p.owner}`}</span>
        <span className={`vis ${p.visibility}`}>{p.visibility}</span>
      </div>
    </Link>
  );
}
