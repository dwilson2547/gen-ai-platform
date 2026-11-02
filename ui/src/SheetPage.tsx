import { useCallback, useEffect, useRef, useState } from "react";
import { busyCandidate, sheetsApi, type Candidate, type Sheet, type SheetExport, type SheetSlot } from "./api.ts";
import { Confirm } from "./Modal.tsx";
import { Link, navigate } from "./router.tsx";

// A character sheet (workflows 10–12): pick the base from 4, add variations (4 edits of the base each), freeze the
// good ones and re-roll the rest, keep any, export the kept ones at the base's scale.

const STARTERS: Record<string, string[]> = {
  Poses: ["standing idle", "walking", "running", "jumping", "attacking", "hurt, recoiling"],
  Expressions: ["happy, smiling", "angry", "surprised", "sad"],
};

export function SheetPage({ id }: { id: string }) {
  const [sh, setSh] = useState<Sheet | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(() => sheetsApi.get(id).then(setSh).catch(() => setMissing(true)), [id]);
  useEffect(() => { load(); }, [load]);
  const busy = !!sh?.slots.some(s => s.candidates.some(busyCandidate));
  useEffect(() => {                                     // images fill in live while any is being made or waiting
    if (!busy) return;
    const t = setInterval(() => { sheetsApi.get(id).then(setSh).catch(() => {}); }, 2000);
    return () => clearInterval(t);
  }, [busy, id]);

  // Every action returns the whole sheet; errors show at the top and leave the sheet as it was.
  const act = async (fn: () => Promise<Sheet>) => {
    setError(null);
    try { setSh(await fn()); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  if (missing) return <div className="panel"><h2>Not found, or not shared with you</h2><Link to="/characters">All characters</Link></div>;
  if (!sh) return <div className="page"><div className="spinner" /></div>;
  const base = sh.slots.find(s => s.kind === "base")!;
  const rows = sh.slots.filter(s => s.kind === "variation");
  const edit = sh.can_edit;

  return (
    <div className="page sheet">
      <div className="head-row">
        <div>
          <h2>{sh.name}</h2>
          <p className="muted small">
            <Link to={`/p/${sh.project.id}`}>{sh.project.name}</Link> <span className={`vis ${sh.project.visibility}`}>{sh.project.visibility}</span>
            {" "}· base by {sh.model_label}{sh.edit_model !== sh.model && ", variations by FLUX.2 klein 4B"} · {sh.kept} kept
            {!edit && ` · by ${sh.by}`}
          </p>
        </div>
        {edit && <button className="danger ghost" onClick={() => setDeleting(true)}>Delete sheet</button>}
      </div>
      {error && <div className="banner" role="alert"><span className="error">{error}</span><button className="ghost" onClick={() => setError(null)}>Dismiss</button></div>}

      {!sh.base_artifact_id ? (
        <section className="stage">
          <h3>1. Pick the base</h3>
          <p className="muted small">Click an image to freeze it. Re-roll replaces the ones that aren't frozen. When one is
            right, use it as the base: every variation is drawn from it.</p>
          <Row sheet={sh} slot={base} edit={edit} act={act} picking />
        </section>
      ) : (<>
        <section className="stage">
          <h3>Base</h3>
          <Row sheet={sh} slot={base} edit={edit} act={act} />
        </section>
        <section className="stage">
          <h3>2. Variations</h3>
          {rows.length === 0 && <p className="muted">No variations yet.{edit && " Add poses, expressions or weapon sets below; each gets 4 edits of the base."}</p>}
          {rows.map(r => <Row key={r.id} sheet={sh} slot={r} edit={edit} act={act} />)}
          {edit && <AddRows sheet={sh} act={act} />}
        </section>
        <ExportBox sheet={sh} edit={edit} act={act} />
      </>)}

      {deleting && <Confirm title="Delete this sheet?" action="Delete sheet"
        body="The sheet goes; its images stay in the project." onClose={() => setDeleting(false)}
        onConfirm={async () => { await sheetsApi.remove(sh.id); navigate("/characters"); }} />}
    </div>
  );
}

function Row({ sheet, slot, edit, act, picking }: {
  sheet: Sheet; slot: SheetSlot; edit: boolean; act: (fn: () => Promise<Sheet>) => Promise<void>; picking?: boolean;
}) {
  const [rewording, setRewording] = useState(false);
  const [text, setText] = useState(slot.instruction);
  const [removing, setRemoving] = useState(false);
  const unfrozen = slot.candidates.filter(c => !c.frozen).length;
  const working = slot.candidates.some(c => !c.frozen && busyCandidate(c));
  const fromImage = slot.kind === "base" && slot.candidates.length === 1;      // started from an existing image

  return (
    <div className="srow">
      <div className="srow-head">
        <div>
          <strong>{slot.kind === "base" ? (fromImage ? "From an image" : "Description") : slot.label}</strong>
          {slot.kind === "base" && !fromImage && <span className="muted small"> {slot.instruction}</span>}
        </div>
        {edit && !fromImage && (picking || slot.kind === "variation") && (
          <div className="srow-actions">
            <button onClick={() => act(() => sheetsApi.reroll(sheet.id, slot.id))} disabled={working || unfrozen === 0}
              title={unfrozen === 0 ? "Everything here is frozen" : working ? "Wait for these to finish" : "New images for the ones that aren't frozen"}>
              Re-roll {unfrozen === slot.candidates.length ? "all" : `${unfrozen} unfrozen`}</button>
            <button className="ghost" onClick={() => { setText(slot.instruction); setRewording(v => !v); }}>Reword</button>
            {slot.kind === "variation" && <button className="ghost" onClick={() => setRemoving(true)}>Remove</button>}
          </div>)}
      </div>
      {rewording && (
        <form className="row-form" onSubmit={async e => {
          e.preventDefault();
          if (!text.trim()) return;
          await act(() => sheetsApi.reroll(sheet.id, slot.id, text.trim()));
          setRewording(false);
        }}>
          <input value={text} onChange={e => setText(e.target.value)} maxLength={1000} autoFocus aria-label="new wording" style={{ flex: 1 }} />
          <button type="submit" className="primary" disabled={!text.trim() || working}>Re-roll with this</button>
          <button type="button" className="ghost" onClick={() => setRewording(false)}>Cancel</button>
        </form>)}
      <div className="cgrid">
        {slot.candidates.map(c => <Cand key={c.id} sheet={sheet} c={c} edit={edit} act={act} picking={picking} />)}
      </div>
      {removing && <Confirm title={`Remove "${slot.label}"?`} action="Remove" body="The row goes; its images stay in the project."
        onClose={() => setRemoving(false)}
        onConfirm={async () => { setRemoving(false); await act(() => sheetsApi.removeRow(sheet.id, slot.id)); }} />}
    </div>
  );
}

function Cand({ sheet, c, edit, act, picking }: {
  sheet: Sheet; c: Candidate; edit: boolean; act: (fn: () => Promise<Sheet>) => Promise<void>; picking?: boolean;
}) {
  const a = c.artifact;
  const done = a?.status === "done";
  const isBase = !!a && a.id === sheet.base_artifact_id;
  const state = c.held ? "Waiting for room in your queue" : !a ? "Deleted from the project"
    : a.status === "queued" ? "Queued" : a.status === "running" ? "Generating…" : a.status === "failed" ? `Failed: ${a.error ?? ""}` : null;
  const freeze = () => edit && act(() => sheetsApi.mark(sheet.id, c.id, { frozen: !c.frozen }));

  return (
    <figure className={"cand" + (c.frozen ? " frozen" : "") + (c.kept ? " kept" : "")}>
      <button className="cimg checker" onClick={freeze} disabled={!edit || !a}
        title={edit ? (c.frozen ? "Frozen: kept through re-rolls. Click to unfreeze" : "Click to freeze: re-roll leaves it alone") : ""}
        aria-pressed={c.frozen} aria-label={c.frozen ? "frozen" : "not frozen"}>
        {done && a?.image_url ? <img src={a.image_url} alt={a.prompt} />
          : <span className={"small " + (a?.status === "failed" ? "error" : "muted")}>{state}{busyCandidate(c) && <span className="spinner inline" />}</span>}
        {c.frozen && <span className="pin" aria-hidden>❄ frozen</span>}
        {isBase && <span className="pin base" aria-hidden>base</span>}
      </button>
      <figcaption>
        {edit && picking && done && <button className="primary small-btn" onClick={() => act(() => sheetsApi.setBase(sheet.id, c.id))}>Use as base</button>}
        {edit && !picking && done && (
          <label className="keep"><input type="checkbox" checked={c.kept}
            onChange={() => act(() => sheetsApi.mark(sheet.id, c.id, { kept: !c.kept }))} /> Keep</label>)}
        {a && <Link to={`/a/${a.id}`} className="small">Open</Link>}
      </figcaption>
    </figure>
  );
}

function AddRows({ sheet, act }: { sheet: Sheet; act: (fn: () => Promise<Sheet>) => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  const existing = new Set(sheet.slots.map(s => s.instruction));
  const addLine = (l: string) => {
    setText(t => (t.trim() ? t.replace(/\s*$/, "\n") : "") + l);
    ref.current?.focus();
  };
  return (
    <form className="addrows panel-box" onSubmit={async e => {
      e.preventDefault();
      if (!lines.length) return;
      setBusy(true);
      await act(() => sheetsApi.addRows(sheet.id, lines));
      setText(""); setBusy(false);
    }}>
      <strong>Add variations</strong>
      <span className="muted small">One per line: a pose, an expression or a weapon set ("holding a war hammer and a
        tower shield"). Each line gets 4 images.</span>
      <div className="starters">
        {Object.entries(STARTERS).map(([group, items]) => (
          <div key={group} className="tweak-row">
            <span className="muted small">{group}</span>
            {items.map(i => <button type="button" key={i} className="chip" disabled={existing.has(i) || lines.includes(i)}
              onClick={() => addLine(i)}>+ {i}</button>)}
          </div>))}
      </div>
      <textarea ref={ref} rows={3} value={text} onChange={e => setText(e.target.value)} aria-label="variations, one per line"
        placeholder={"running\nholding a sword and a round shield"} />
      <div className="actions">
        <span className="muted small">{lines.length ? `${lines.length} row${lines.length > 1 ? "s" : ""}, ${lines.length * 4} images` : ""}</span>
        <button type="submit" className="primary" disabled={busy || !lines.length}>{busy ? "Adding…" : "Add"}</button>
      </div>
    </form>
  );
}

function ExportBox({ sheet, edit, act }: { sheet: Sheet; edit: boolean; act: (fn: () => Promise<Sheet>) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<SheetExport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setError(null); setOut(null);
    try {
      const r = await sheetsApi.export(sheet.id);
      setOut(r);
      location.assign(r.download_url);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return (
    <section className="stage">
      <h3>3. Export</h3>
      <div className="row-form">
        <label>Tile
          <select value={sheet.pixel_size} disabled={!edit}
            onChange={e => act(() => sheetsApi.update(sheet.id, { pixel_size: +e.target.value }))}>
            {[16, 24, 32, 48, 64, 96, 128, 256].map(n => <option key={n} value={n}>{n} px</option>)}
          </select></label>
        <label>Colours
          <select value={sheet.pixel_colors} disabled={!edit}
            onChange={e => act(() => sheetsApi.update(sheet.id, { pixel_colors: +e.target.value }))}>
            {[8, 16, 24, 32, 64, 0].map(n => <option key={n} value={n}>{n || "all"}</option>)}
          </select></label>
        <button className="primary" onClick={go} disabled={busy || sheet.kept === 0}
          title={sheet.kept === 0 ? "Tick Keep on the images you want first" : ""}>
          {busy ? "Exporting…" : `Download ${sheet.kept} kept`}</button>
      </div>
      <p className="muted small">A zip: each kept image's transparent cutout and a pixel sprite on a {sheet.pixel_size}×{sheet.pixel_size} tile,
        standing on the bottom row, all at the base's scale so sizes compare (the base fills ¾ of the tile, leaving room for
        weapons and jumps).</p>
      {error && <p className="error">{error}</p>}
      {out && <p className="small">{out.files} files. {out.shrunk.length > 0 && (
        <span className="warn">Too big for the tile at the base's scale, so fitted instead: {out.shrunk.join(", ")}.</span>)}
        {" "}<a href={out.download_url}>Download again</a> (link valid 10 minutes)</p>}
    </section>
  );
}
