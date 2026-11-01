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
export type ModelInfo = { name: string; label: string; licence: string; commercial: boolean; note: string; default: boolean };
export type PresetInfo = { name: string; label: string; hint: string; template: string; width: number; height: number };
export type Catalogue = { models: ModelInfo[]; presets: PresetInfo[]; sizes: { width: number; height: number }[] };
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
  image_url: string | null;
};
export type GenerateIn = {
  prompt: string; project_id?: string; preset: string; model: string; width: number; height: number;
  seed?: number; count: number; steps?: number;
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
  artifact: (id: string) => call<Artifact>("GET", `/api/artifacts/${id}`),
  deleteArtifact: (id: string) => call<{ ok: boolean }>("DELETE", `/api/artifacts/${id}`),
};

export const pending = (a: Artifact) => a.status === "queued" || a.status === "running";

export type TokenRow = { id: string; name: string; prefix: string; created_at: string; last_used_at: string | null };
export type GrantRow = { id: string; client_name: string; created_at: string; last_used_at: string | null };
export const agentsApi = {
  get: () => call<{ mcp_url: string; tokens: TokenRow[]; grants: GrantRow[] }>("GET", "/api/agents"),
  createToken: (name: string) => call<TokenRow & { token: string; mcp_url: string }>("POST", "/api/tokens", { name }),
  revokeToken: (id: string) => call<{ ok: boolean }>("DELETE", `/api/tokens/${id}`),
  revokeGrant: (id: string) => call<{ ok: boolean }>("DELETE", `/api/oauth/grants/${id}`),
};
