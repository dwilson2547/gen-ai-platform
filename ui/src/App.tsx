import { useEffect, useState } from "react";
import { api, ApiError, type Catalogue, type Config, type Me } from "./api.ts";
import { Agents } from "./Agents.tsx";
import { ArtifactPage } from "./ArtifactPage.tsx";
import { Home } from "./Home.tsx";
import { ProjectPage } from "./ProjectPage.tsx";
import { Projects } from "./Projects.tsx";
import { SheetPage } from "./SheetPage.tsx";
import { SheetList } from "./Sheets.tsx";
import { Link, navigate, useLocation } from "./router.tsx";
import { Splash } from "./Splash.tsx";
import { AppState } from "./state.tsx";

type Boot = { kind: "loading" } | { kind: "out"; config: Config | null; error?: string }
  | { kind: "in"; me: Me; catalogue: Catalogue } | { kind: "down"; message: string };

export default function App() {
  const [boot, setBoot] = useState<Boot>({ kind: "loading" });

  const load = async () => {
    try {
      const me = await api.me();
      setBoot({ kind: "in", me, catalogue: await api.catalogue() });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setBoot({ kind: "out", config: await api.config().catch(() => null) });
      } else setBoot({ kind: "down", message: e instanceof Error ? e.message : String(e) });
    }
  };
  useEffect(() => { load(); }, []);

  if (boot.kind === "loading") return <div className="boot"><div className="spinner" /></div>;
  if (boot.kind === "down") return (
    <div className="boot"><div className="panel"><h2>Can't reach the server</h2><p className="muted">{boot.message}</p>
      <button onClick={load}>Try again</button></div></div>);
  if (boot.kind === "out") return <Splash config={boot.config} onSignedIn={load} />;
  return <AppState me={boot.me} catalogue={boot.catalogue}><Shell me={boot.me} /></AppState>;
}

function Shell({ me }: { me: Me }) {
  const { path } = useLocation();
  const signOut = async () => { await api.logout().catch(() => {}); location.href = "/"; };
  let page;
  let m;
  if (path === "/" || path === "") page = <Home />;
  else if (path === "/projects") page = <Projects />;
  else if (path === "/agents") page = <Agents />;
  else if (path === "/characters") page = <div className="page"><SheetList canCreate /></div>;
  else if ((m = path.match(/^\/s\/([0-9a-f]{32})$/))) page = <SheetPage key={m[1]} id={m[1]} />;
  else if ((m = path.match(/^\/p\/([0-9a-f]{32})$/))) page = <ProjectPage key={m[1]} id={m[1]} />;
  else if ((m = path.match(/^\/a\/([0-9a-f]{32})$/))) page = <ArtifactPage key={m[1]} id={m[1]} />;
  else page = <div className="panel"><h2>Not found</h2><Link to="/">Back home</Link></div>;
  return (
    <div className="shell">
      <header className="top">
        <Link to="/" className="brand"><img src="/favicon.svg" alt="" /> genai</Link>
        <nav>
          <Link to="/" className={path === "/" ? "on" : ""}>Generate</Link>
          <Link to="/characters" className={path === "/characters" || path.startsWith("/s/") ? "on" : ""}>Characters</Link>
          <Link to="/projects" className={path.startsWith("/p") ? "on" : ""}>Projects</Link>
          <Link to="/agents" className={path === "/agents" ? "on" : ""}>Agents</Link>
        </nav>
        <div className="who">
          <span className="muted" title={me.username}>{me.display_name || me.username}</span>
          <button className="ghost" onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main>{page}</main>
    </div>
  );
}

export const goHome = () => navigate("/");
