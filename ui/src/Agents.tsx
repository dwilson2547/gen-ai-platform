import { useCallback, useEffect, useState } from "react";
import { agentsApi, type GrantRow, type TokenRow } from "./api.ts";
import { Confirm } from "./Modal.tsx";

// Workflow 7: connect an agent (MCP), manage personal tokens and connected apps.
export function Agents() {
  const [data, setData] = useState<{ mcp_url: string; tokens: TokenRow[]; grants: GrantRow[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<{ kind: "token" | "grant"; id: string; label: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => agentsApi.get().then(d => { setData(d); setError(null); })
    .catch(e => setError(e instanceof Error ? e.message : String(e))), []);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    setFormError(null);
    try { const t = await agentsApi.createToken(name.trim()); setCreated(t.token); setName(""); load(); }
    catch (e) { setFormError(e instanceof Error ? e.message : String(e)); }
  };
  const revoke = async () => {
    if (!revoking) return;
    setBusy(true);
    try {
      await (revoking.kind === "token" ? agentsApi.revokeToken(revoking.id) : agentsApi.revokeGrant(revoking.id));
      setRevoking(null); load();
    } finally { setBusy(false); }
  };
  const copy = (t: string) => navigator.clipboard?.writeText(t);
  const when = (s: string | null) => s ? new Date(s).toLocaleString() : "never";

  if (error) return <div className="banner error">Couldn't load: {error} <button onClick={load}>Retry</button></div>;
  if (!data) return <div className="page"><div className="spinner" /></div>;
  const addCmd = `claude mcp add --transport http genai ${data.mcp_url}`;
  return (
    <div className="page agents">
      <h2>Agents</h2>
      <section className="panel-box">
        <h3>Connect an agent</h3>
        <p className="muted">Agents can list your projects, generate images into them, look at the results and find
          earlier ones. They act as you and see only what you see. They can't delete anything.</p>
        <p>MCP server: <code>{data.mcp_url}</code> <button className="ghost small" onClick={() => copy(data.mcp_url)}>Copy</button></p>
        <p className="small">Claude Code: <code>{addCmd}</code> <button className="ghost small" onClick={() => copy(addCmd)}>Copy</button>,
          then run <code>/mcp</code> to sign in. claude.ai and other clients: add it as a custom connector with that URL.
          Either way you approve it here in the browser; no token to paste.</p>
      </section>

      <section className="panel-box">
        <h3>Connected apps</h3>
        {data.grants.length === 0 ? <p className="muted small">None yet. They appear here once you approve one.</p> : (
          <table className="rows"><tbody>
            {data.grants.map(g => (
              <tr key={g.id}><td><strong>{g.client_name}</strong></td>
                <td className="muted small">connected {when(g.created_at)} · last used {when(g.last_used_at)}</td>
                <td><button className="danger ghost small" onClick={() => setRevoking({ kind: "grant", id: g.id, label: g.client_name })}>Disconnect</button></td></tr>
            ))}
          </tbody></table>)}
      </section>

      <section className="panel-box">
        <h3>Personal access tokens</h3>
        <p className="muted small">For clients without OAuth sign-in: send it as <code>Authorization: Bearer …</code>.
          It also downloads full-size images.</p>
        <form className="row-form" onSubmit={e => { e.preventDefault(); if (name.trim()) create(); }}>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="Name, e.g. laptop" maxLength={100}
            aria-label="token name" aria-invalid={!!formError} />
          <button type="submit" className="primary" disabled={!name.trim()}>Create token</button>
          {formError && <span className="error small">{formError}</span>}
        </form>
        {created && (
          <div className="banner created" role="status">
            <div><strong>Copy it now; it won't be shown again.</strong><br /><code className="token">{created}</code></div>
            <button onClick={() => copy(created)}>Copy</button><button className="ghost" onClick={() => setCreated(null)}>Done</button>
          </div>)}
        {data.tokens.length === 0 ? <p className="muted small">No tokens.</p> : (
          <table className="rows"><tbody>
            {data.tokens.map(t => (
              <tr key={t.id}><td><strong>{t.name}</strong> <code className="small">{t.prefix}…</code></td>
                <td className="muted small">created {when(t.created_at)} · last used {when(t.last_used_at)}</td>
                <td><button className="danger ghost small" onClick={() => setRevoking({ kind: "token", id: t.id, label: t.name })}>Revoke</button></td></tr>
            ))}
          </tbody></table>)}
      </section>
      {revoking && <Confirm title={revoking.kind === "token" ? "Revoke this token?" : "Disconnect this app?"}
        body={<>Anything using <strong>{revoking.label}</strong> stops working at once.</>}
        action={revoking.kind === "token" ? "Revoke" : "Disconnect"} busy={busy}
        onConfirm={revoke} onClose={() => setRevoking(null)} />}
    </div>
  );
}
