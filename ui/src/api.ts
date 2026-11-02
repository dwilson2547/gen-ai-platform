// Every write carries X-Genai: the server's CSRF guard (api/genai/auth.py).

export class ApiError extends Error {
  status: number;
  constructor(status: number, body: any) {
    const d = body?.detail;
    super(typeof d === "string" ? d : Array.isArray(d) ? d.map((e: any) => e.msg).join("; ") : `HTTP ${status}`);
    this.status = status;
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Genai": "1" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(url, { method, headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, data);
  return data as T;
}

export type Config = { oidc: boolean; dev_login: boolean; version: string };
export type Me = { id: string; username: string; display_name: string | null };
export type ModelInfo = {
  name: string; label: string; licence: string; commercial: boolean; note: string; default: boolean;
  edits: boolean; sprite_size: number | null;
};
export type PresetInfo = { name: string; label: string; hint: string; template: string; width: number; height: number; sprite: boolean };
export type Catalogue = { models: ModelInfo[]; presets: PresetInfo[]; sizes: { width: number; height: number }[]; backgrounds: string[] };
export type Visibility = "private" | "public";
export type Project = {
  id: string; name: string; description: string; visibility: Visibility; is_default: boolean; owner: string;
  mine: boolean; created_at: string; artifacts: number; cover_url: string | null;
};
export type Status = "queued" | "running" | "done" | "failed";
export type Artifact = {
  id: string; project: { id: string; name: string; visibility: Visibility }; by: string; created_at: string;
  prompt: string; full_prompt: string; preset: string; model: string; model_label: string; commercial: boolean | null;
  seed: number | null; width: number; height: number; steps: number | null; cfg_scale: number | null;
  parent_id: string | null; op: string; status: Status; error: string | null; ran_model: string | null;
  worker: string | null; seconds: number | null; bytes: number | null; finished_at: string | null;
  image_url: string | null; title: string | null; background: string | null;
};
export type Variant = {
  id: string; kind: "cutout" | "pixel"; params: Record<string, any>; width: number; height: number; bytes: number;
  image_url: string; created_at: string;
};
export type ArtifactFull = Artifact & { parent: Artifact | null; children: Artifact[]; variants: Variant[]; can_edit: boolean };
export type TweakOp = "reroll" | "edit_prompt" | "edit_image";
export type GenerateIn = {
  prompt: string; project_id?: string; preset: string; model: string; width?: number; height?: number;
  seed?: number; count: number; steps?: number; background?: string;
};

const qs = (o: Record<string, string | number | undefined | null>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? "?" + s : "";
};

export const api = {
  config: () => call<Config>("GET", "/api/config"),
  me: () => call<Me>("GET", "/api/me"),
  catalogue: () => call<Catalogue>("GET", "/api/catalogue"),
  devLogin: (name: string) => call<Me>("POST", "/api/auth/dev-login", { name }),
  logout: () => call<{ ok: boolean }>("POST", "/api/auth/logout"),
  projects: () => call<{ mine: Project[]; public: Project[] }>("GET", "/api/projects"),
  project: (id: string) => call<Project>("GET", `/api/projects/${id}`),
  createProject: (b: { name: string; description: string; visibility: Visibility }) =>
    call<Project>("POST", "/api/projects", b),
  updateProject: (id: string, b: Partial<{ name: string; description: string; visibility: Visibility }>) =>
    call<Project>("PATCH", `/api/projects/${id}`, b),
  generate: (b: GenerateIn) => call<{ artifacts: Artifact[] }>("POST", "/api/generate", b),
  artifacts: (o: { project_id?: string; before?: string | null; limit?: number }) =>
    call<{ artifacts: Artifact[]; next: string | null }>("GET", "/api/artifacts" + qs(o)),
  artifact: (id: string) => call<ArtifactFull>("GET", `/api/artifacts/${id}`),
  tweak: (id: string, b: { op: TweakOp; prompt?: string; count?: number; seed?: number }) =>
    call<{ artifacts: Artifact[] }>("POST", `/api/artifacts/${id}/tweak`, b),
  deleteArtifact: (id: string) => call<{ ok: boolean }>("DELETE", `/api/artifacts/${id}`),
  makePixel: (id: string, b: { size: number; colors: number; canvas: string }) =>
    call<Variant>("POST", `/api/artifacts/${id}/variants`, b),
  deleteVariant: (id: string) => call<{ ok: boolean }>("DELETE", `/api/variants/${id}`),
};

export const pending = (a: Artifact) => a.status === "queued" || a.status === "running";

// Character sheets (design §11)
export type Candidate = { id: string; position: number; frozen: boolean; kept: boolean; held: boolean; artifact: Artifact | null };
export type SheetSlot = { id: string; kind: "base" | "variation"; label: string; instruction: string; position: number; candidates: Candidate[] };
export type SheetBrief = {
  id: string; name: string; description: string; model: string; model_label: string; edit_model: string;
  background: string; by: string; created_at: string; project: { id: string; name: string; visibility: Visibility };
  base_artifact_id: string | null; cover_url: string | null; pixel_size: number; pixel_colors: number; kept: number;
  can_edit: boolean;
};
export type Sheet = SheetBrief & { slots: SheetSlot[] };
export type SheetExport = { download_url: string; expires_at: string; files: number; shrunk: string[] };
export const busyCandidate = (c: Candidate) => c.held || (c.artifact !== null && pending(c.artifact));
export const sheetsApi = {
  list: (project_id?: string) => call<{ sheets: SheetBrief[] }>("GET", "/api/sheets" + qs({ project_id })),
  get: (id: string) => call<Sheet>("GET", `/api/sheets/${id}`),
  create: (b: { name: string; description?: string; base_artifact_id?: string; project_id?: string; model: string; background: string }) =>
    call<Sheet>("POST", "/api/sheets", b),
  update: (id: string, b: Partial<{ name: string; pixel_size: number; pixel_colors: number }>) =>
    call<Sheet>("PATCH", `/api/sheets/${id}`, b),
  remove: (id: string) => call<{ ok: boolean }>("DELETE", `/api/sheets/${id}`),
  setBase: (id: string, candidate_id: string) => call<Sheet>("POST", `/api/sheets/${id}/base`, { candidate_id }),
  addRows: (id: string, lines: string[]) => call<Sheet>("POST", `/api/sheets/${id}/slots`, { lines }),
  reroll: (id: string, slot: string, instruction?: string) =>
    call<Sheet>("POST", `/api/sheets/${id}/slots/${slot}/reroll`, { instruction }),
  removeRow: (id: string, slot: string) => call<Sheet>("DELETE", `/api/sheets/${id}/slots/${slot}`),
  mark: (id: string, cand: string, b: { frozen?: boolean; kept?: boolean }) =>
    call<Sheet>("PATCH", `/api/sheets/${id}/candidates/${cand}`, b),
  export: (id: string) => call<SheetExport>("POST", `/api/sheets/${id}/export`),
};

export type TokenRow = { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null };
export type GrantRow = { id: string; client_name: string; created_at: string; last_used_at: string | null };
export const agentsApi = {
  get: () => call<{ mcp_url: string; tokens: TokenRow[]; grants: GrantRow[] }>("GET", "/api/agents"),
  createToken: (name: string) => call<TokenRow & { token: string; mcp_url: string }>("POST", "/api/tokens", { name }),
  revokeToken: (id: string) => call<{ ok: boolean }>("DELETE", `/api/tokens/${id}`),
  revokeGrant: (id: string) => call<{ ok: boolean }>("DELETE", `/api/oauth/grants/${id}`),
};
