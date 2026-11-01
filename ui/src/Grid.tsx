import { useCallback, useEffect, useRef, useState } from "react";
import { api, pending, type Artifact } from "./api.ts";
import { navigate } from "./router.tsx";
import { setLastList } from "./state.tsx";

// A list of artifacts that pages and refreshes its pending ones until they finish (workflows 2, 3, 5, 6).
export function useArtifacts(projectId?: string) {
  const [items, setItems] = useState<Artifact[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (before: string | null = null) => {
    setLoading(true); setError(null);
    try {
      const r = await api.artifacts({ project_id: projectId, before, limit: 48 });
      setItems(cur => before ? [...cur, ...r.artifacts] : r.artifacts);
      setNext(r.next);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, [projectId]);
  useEffect(() => { load(); }, [load]);

  const itemsRef = useRef(items);
  itemsRef.current = items;
  const anyPending = items.some(pending);
  useEffect(() => {
    if (!anyPending) return;
    const t = setInterval(async () => {
      const ids = itemsRef.current.filter(pending).map(a => a.id);
      const fresh = await Promise.all(ids.map(id => api.artifact(id).catch(() => null)));
      const byId = new Map(fresh.filter((a): a is Artifact => !!a).map(a => [a.id, a]));
      setItems(cur => cur.map(a => byId.get(a.id) ?? a));
    }, 1500);
    return () => clearInterval(t);
  }, [anyPending]);

  return {
    items, loading, error, next,
    more: () => next && load(next),
    retry: () => load(),
    prepend: (a: Artifact[]) => setItems(cur => [...a, ...cur]),
  };
}

export function Grid({ items, loading, error, next, more, retry, empty }: ReturnType<typeof useArtifacts> & { empty: string }) {
  const open = (id: string) => { setLastList(items.map(a => a.id)); navigate(`/a/${id}`); };
  if (error) return <div className="banner error">Couldn't load images: {error} <button onClick={retry}>Retry</button></div>;
  if (loading && items.length === 0) return <div className="grid">{Array.from({ length: 8 }, (_, i) => <div key={i} className="card skel" />)}</div>;
  if (items.length === 0) return <p className="empty">{empty}</p>;
  return (
    <>
      <div className="grid">{items.map(a => <Card key={a.id} a={a} onOpen={() => open(a.id)} />)}</div>
      {next && <div className="more-row"><button onClick={more} disabled={loading}>{loading ? "Loading…" : "Load more"}</button></div>}
    </>
  );
}

function Card({ a, onOpen }: { a: Artifact; onOpen: () => void }) {
  const since = useElapsed(a);
  return (
    <button className={`card ${a.status}`} onClick={onOpen} title={a.prompt}>
      {a.status === "done" && a.image_url
        ? <img src={a.image_url} alt={a.prompt} loading="lazy" className={a.preset === "sprite" ? "pixel" : ""} />
        : a.status === "failed"
          ? <div className="state"><strong>Failed</strong><span className="small">{a.error}</span></div>
          : <div className="state"><div className="spinner" /><span>{a.status === "running" ? "Generating" : "Queued"} · {since}s</span></div>}
      <div className="cap"><span className="small">{a.prompt}</span>{a.commercial === false && <span className="badge nc" title="Non-commercial licence">NC</span>}</div>
    </button>
  );
}

function useElapsed(a: Artifact) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!pending(a)) return;
    const t = setInterval(() => tick(n => n + 1), 1000);
    return () => clearInterval(t);
  }, [a]);
  return Math.max(0, Math.round((Date.now() - Date.parse(a.created_at)) / 1000));
}
