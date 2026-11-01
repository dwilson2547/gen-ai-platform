import { useEffect, useMemo, useState } from "react";
import { api, type Artifact, type Project } from "./api.ts";
import { ProjectPicker } from "./ProjectPicker.tsx";
import { useApp } from "./state.tsx";

export type Prefill = {
  prompt: string; preset: string; model: string; width: number; height: number; steps: number | null;
  background: string | null;
  seed: number | null; project_id: string | null;
};

// The generate bar (workflow 2). `project` pins the target (a project page); otherwise the user picks, default Scratch.
export function PromptBar({ project, prefill, onQueued }: {
  project?: Project; prefill?: Prefill | null; onQueued: (a: Artifact[]) => void;
}) {
  const { catalogue, projects, reloadProjects } = useApp();
  const defModel = catalogue.models.find(m => m.default)?.name ?? catalogue.models[0].name;
  const [prompt, setPrompt] = useState("");
  const [preset, setPreset] = useState("none");
  const [background, setBackground] = useState("magenta");
  const [model, setModel] = useState(defModel);
  const [projectId, setProjectId] = useState<string | null>(project?.id ?? null);
  const [more, setMore] = useState(false);
  const [size, setSize] = useState("1024x1024");
  const [seed, setSeed] = useState("");
  const [count, setCount] = useState(1);
  const [steps, setSteps] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!prefill) return;
    setPrompt(prefill.prompt); setPreset(prefill.preset); setModel(prefill.model);
    if (prefill.background) setBackground(prefill.background);
    setSize(`${prefill.width}x${prefill.height}`); setSteps(prefill.steps && prefill.steps !== 4 ? String(prefill.steps) : "");
    setSeed(prefill.seed !== null ? String(prefill.seed) : "");
    if (prefill.project_id && !project) setProjectId(prefill.project_id);
    if (prefill.seed !== null || (prefill.steps && prefill.steps !== 4)
        || `${prefill.width}x${prefill.height}` !== "1024x1024") setMore(true);
  }, [prefill, project]);

  const p = catalogue.presets.find(x => x.name === preset);
  const m = catalogue.models.find(x => x.name === model);
  const mine = projects?.mine ?? [];
  const seedErr = seed !== "" && !/^\d{1,10}$/.test(seed) ? "Seed is a whole number" : null;
  const stepsErr = steps !== "" && !(/^\d+$/.test(steps) && +steps >= 1 && +steps <= 50) ? "Steps: 1–50" : null;
  const sizes = useMemo(() => catalogue.sizes.map(s => `${s.width}x${s.height}`), [catalogue]);

  const submit = async () => {
    if (!prompt.trim() || seedErr || stepsErr || busy) return;
    const [w, h] = size.split("x").map(Number);
    setBusy(true); setError(null);
    try {
      const r = await api.generate({
        prompt, preset, model, width: w, height: h, count, background: p?.sprite ? background : undefined,
        project_id: project?.id ?? projectId ?? undefined,
        seed: seed === "" ? undefined : +seed, steps: steps === "" ? undefined : +steps,
      });
      onQueued(r.artifacts);
      reloadProjects();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <form className="promptbar" onSubmit={e => { e.preventDefault(); submit(); }}>
      <div className="prow">
        <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={2} maxLength={4000}
          placeholder={preset === "sprite" ? "a goblin archer" : preset === "item_sprite" ? "a treasure map" : "Describe the image…"} aria-label="prompt"
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
        <button type="submit" className="primary big" disabled={busy || !prompt.trim() || !!seedErr || !!stepsErr}>
          {busy ? "Queuing…" : count > 1 ? `Generate ${count}` : "Generate"}
        </button>
      </div>
      <div className="prow controls">
        <div className="seg" role="radiogroup" aria-label="preset">
          {catalogue.presets.map(x => (
            <button type="button" key={x.name} role="radio" aria-checked={preset === x.name}
              className={preset === x.name ? "on" : ""} onClick={() => setPreset(x.name)}>{x.label}</button>
          ))}
        </div>
        <div className="seg" role="radiogroup" aria-label="model">
          {catalogue.models.map(x => (
            <button type="button" key={x.name} role="radio" aria-checked={model === x.name} title={x.note}
              className={model === x.name ? "on" : ""} onClick={() => setModel(x.name)}>{x.label.replace("FLUX.2 klein ", "")}</button>
          ))}
        </div>
        {p?.sprite && (
          <div className="seg" role="radiogroup" aria-label="background" title="Key colour the clean-up removes: green for pink or purple subjects">
            {catalogue.backgrounds.map(b => (
              <button type="button" key={b} role="radio" aria-checked={background === b}
                className={(background === b ? "on " : "") + "bg-" + b} onClick={() => setBackground(b)}>
                <span className="swatch" />{b}</button>
            ))}
          </div>)}
        {!project && <ProjectPicker projects={mine} value={projectId} onChange={setProjectId} />}
        <button type="button" className="ghost" onClick={() => setMore(v => !v)} aria-expanded={more}>
          {more ? "Less ▴" : "More ▾"}</button>
      </div>
      {m && !m.commercial && (
        <p className="warn small">{m.label}: {m.licence}. Its images may not be used commercially.</p>)}
      {p?.hint && <p className="muted small">{p.hint}{p.sprite && " Background is removed automatically; use green for pink or purple subjects."}</p>}
      {more && (
        <div className="prow more">
          <label>Size
            <select value={size} onChange={e => setSize(e.target.value)}>
              {sizes.map(s => <option key={s} value={s}>{s.replace("x", " × ")}</option>)}
            </select></label>
          <label>Images
            <select value={count} onChange={e => setCount(+e.target.value)}>
              {[1, 2, 3, 4].map(n => <option key={n}>{n}</option>)}
            </select></label>
          <label>Seed
            <input value={seed} onChange={e => setSeed(e.target.value.trim())} placeholder="random" inputMode="numeric"
              aria-invalid={!!seedErr} className={seedErr ? "bad" : ""} />
            {seedErr && <span className="error small">{seedErr}</span>}</label>
          <label>Steps
            <input value={steps} onChange={e => setSteps(e.target.value.trim())} placeholder="4" inputMode="numeric"
              aria-invalid={!!stepsErr} className={stepsErr ? "bad" : ""} />
            {stepsErr && <span className="error small">{stepsErr}</span>}</label>
          {count > 1 && seed !== "" && <span className="muted small">Seeds {seed}–{+seed + count - 1}</span>}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}
