import { useState } from "react";
import { api, type Artifact, type TweakOp } from "./api.ts";

// Make new images from this one (workflow 8): re-roll, change the prompt, or edit the picture itself.
export function TweakPanel({ a, onQueued }: { a: Artifact; onQueued: (kids: Artifact[]) => void }) {
  const [mode, setMode] = useState<TweakOp | null>(null);
  const [text, setText] = useState("");
  const [count, setCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = (m: TweakOp) => {
    setMode(m); setError(null);
    setText(m === "edit_prompt" ? a.prompt : "");
  };
  const submit = async (op: TweakOp) => {
    if (op !== "reroll" && !text.trim()) return;
    setBusy(true); setError(null);
    try {
      const r = await api.tweak(a.id, { op, count, prompt: op === "reroll" ? undefined : text.trim() });
      onQueued(r.artifacts);
      setMode(null);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  return (
    <div className="tweak">
      <div className="tweak-row">
        <span className="small muted">Make more</span>
        <select value={count} onChange={e => setCount(+e.target.value)} aria-label="how many" className="small">
          {[1, 2, 3, 4].map(n => <option key={n}>{n}</option>)}
        </select>
      </div>
      <div className="tweak-row">
        <button onClick={() => submit("reroll")} disabled={busy} title="Same recipe, new seed">Re-roll</button>
        <button onClick={() => open("edit_prompt")} className={mode === "edit_prompt" ? "on" : ""}
          title="Same seed and settings, a different prompt">Change prompt</button>
        <button onClick={() => open("edit_image")} className={mode === "edit_image" ? "on" : ""}
          disabled={a.status !== "done"} title="Change this picture: keeps the subject consistent">Edit image</button>
      </div>
      {mode && (
        <form className="tweak-form" onSubmit={e => { e.preventDefault(); submit(mode); }}>
          <textarea autoFocus rows={3} value={text} onChange={e => setText(e.target.value)} maxLength={4000}
            aria-label={mode === "edit_image" ? "what to change" : "new prompt"}
            placeholder={mode === "edit_image" ? "same character, now holding an axe" : ""}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(mode); } }} />
          <p className="muted small">{mode === "edit_image"
            ? "Describe the change. The image itself goes to the model, so the subject stays the same."
            : `Seed ${a.seed ?? "?"} and the other settings stay, so only the prompt changes.`}</p>
          <div className="actions">
            <button type="button" className="ghost" onClick={() => setMode(null)}>Cancel</button>
            <button type="submit" className="primary" disabled={busy || !text.trim()}>{busy ? "Queuing…" : "Go"}</button>
          </div>
        </form>
      )}
      {error && <p className="error small" role="alert">{error}</p>}
    </div>
  );
}
