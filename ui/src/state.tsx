import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Catalogue, type Me, type Project } from "./api.ts";

// What every signed-in page needs: who I am, the catalogue, and the projects I can see (refreshed after edits).
type Ctx = {
  me: Me; catalogue: Catalogue; projects: { mine: Project[]; public: Project[] } | null;
  reloadProjects: () => Promise<void>;
};
const C = createContext<Ctx | null>(null);

export function AppState({ me, catalogue, children }: { me: Me; catalogue: Catalogue; children: ReactNode }) {
  const [projects, setProjects] = useState<Ctx["projects"]>(null);
  const reloadProjects = useCallback(async () => setProjects(await api.projects()), []);
  useEffect(() => { reloadProjects().catch(() => setProjects({ mine: [], public: [] })); }, [reloadProjects]);
  return <C.Provider value={{ me, catalogue, projects, reloadProjects }}>{children}</C.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(C);
  if (!c) throw new Error("useApp outside AppState");
  return c;
}

// The grid an image page was opened from, for previous/next. Lost on reload, which is fine: a direct link has none.
let lastList: string[] = [];
export const setLastList = (ids: string[]) => { lastList = ids; };
export const dropFromLastList = (id: string) => { lastList = lastList.filter(x => x !== id); };
export const neighbours = (id: string) => {
  const i = lastList.indexOf(id);
  return i < 0 ? { prev: null, next: null } : { prev: lastList[i - 1] ?? null, next: lastList[i + 1] ?? null };
};
