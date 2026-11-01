import { useEffect, useState } from "react";
import { api } from "./api.ts";
import { Grid, useArtifacts } from "./Grid.tsx";
import { PromptBar, type Prefill } from "./PromptBar.tsx";
import { useApp } from "./state.tsx";

// Hero surface: the prompt bar over my images (workflows 2 and 3). `/?from=<id>[&seed=1]` prefills from an artifact.
export function Home() {
  const list = useArtifacts();
  const { projects } = useApp();
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  // Read once at mount; the prefill waits for the project list (to know whether the artifact's project is mine).
  const [from] = useState(() => {
    const q = new URLSearchParams(location.search);
    if (q.get("from")) history.replaceState(null, "", "/");
    return q.get("from") ? { id: q.get("from")!, keepSeed: q.get("seed") === "1" } : null;
  });
  useEffect(() => {
    if (!from || projects === null) return;
    api.artifact(from.id).then(a => setPrefill({
      prompt: a.prompt, preset: a.preset, model: a.model, width: a.width, height: a.height, steps: a.steps,
      seed: from.keepSeed ? a.seed : null,
      project_id: projects.mine.some(p => p.id === a.project.id) ? a.project.id : null,
    })).catch(() => {});
  }, [from, projects]);
  return (
    <div className="page">
      <PromptBar prefill={prefill} onQueued={list.prepend} />
      <h2 className="section">My images</h2>
      <Grid {...list} empty="Nothing yet. Describe something above and hit Generate." />
    </div>
  );
}
