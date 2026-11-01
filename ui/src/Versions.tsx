import { useState } from "react";
import { api, type ArtifactFull, type Variant } from "./api.ts";
import { Confirm } from "./Modal.tsx";

const SIZES = [16, 24, 32, 48, 64, 96, 128, 256];
const COLORS = [0, 8, 16, 24, 32, 64];

// Workflow 9: an image's versions — the automatic cutout and pixel-grid sprites — and making new ones.
export function Versions({ a, onChange }: { a: ArtifactFull; onChange: (vs: Variant[]) => void }) {
  const [size, setSize] = useState(64);
  const [colors, setColors] = useState(24);
  const [canvas, setCanvas] = useState("square");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Variant | null>(null);

  const make = async () => {
    setBusy(true); setError(null);
    try {
      const v = await api.makePixel(a.id, { size, colors, canvas });
      onChange([...a.variants.filter(x => !(x.kind === "pixel" && x.params.size === v.params.size
        && x.params.colors === v.params.colors && x.params.canvas === v.params.canvas)), v]);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const del = async () => {
    if (!deleting) return;
    setBusy(true);
    try { await api.deleteVariant(deleting.id); onChange(a.variants.filter(x => x.id !== deleting.id)); setDeleting(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  if (a.status !== "done" || (a.variants.length === 0 && !a.can_edit)) return null;

  return (
    <section className="versions">
      <h2 className="section">Versions</h2>
      <div className="vgrid">
        {a.variants.map(v => (
          <figure key={v.id} className="vcard">
            <div className="checker"><img src={v.image_url} alt="" className="pixel" /></div>
            <figcaption>
              <strong>{v.kind === "cutout" ? "Transparent cutout" : `${v.width}×${v.height} sprite`}</strong>
              <span className="muted small">{v.kind === "pixel"
                ? `${v.params.colors ? v.params.colors + " colours" : "all colours"} · ${v.params.canvas}`
                : `key ${v.params.key}`}</span>
              <span className="vactions">
                <a className="button small" href={`${v.image_url}?download=1`}>Download</a>
                {a.can_edit && v.kind === "pixel" && <button className="danger ghost small" onClick={() => setDeleting(v)}>Delete</button>}
              </span>
            </figcaption>
          </figure>
        ))}
      </div>
      {a.variants.length === 0 && <p className="muted small">No versions yet.</p>}
      {a.can_edit && (
        <form className="row-form" onSubmit={e => { e.preventDefault(); make(); }}>
          <span className="small">Make a pixel sprite</span>
          <label className="small">Size <select value={size} onChange={e => setSize(+e.target.value)}>
            {SIZES.map(n => <option key={n} value={n}>{n} px</option>)}</select></label>
          <label className="small">Colours <select value={colors} onChange={e => setColors(+e.target.value)}>
            {COLORS.map(n => <option key={n} value={n}>{n || "all"}</option>)}</select></label>
          <label className="small">Canvas <select value={canvas} onChange={e => setCanvas(e.target.value)}>
            <option value="square">square tile (standing on the bottom row)</option>
            <option value="fit">fit (cropped to the subject)</option></select></label>
          <button type="submit" className="primary" disabled={busy}>{busy ? "Working…" : "Make"}</button>
          {error && <span className="error small" role="alert">{error}</span>}
        </form>)}
      {deleting && <Confirm title="Delete this version?" body="The original image and its other versions stay."
        action="Delete" busy={busy} onConfirm={del} onClose={() => setDeleting(null)} />}
    </section>
  );
}
