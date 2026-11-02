# gen-ai-platform — design

Status legend: ✅ built · 📐 planned (decided, not built) · ❓ open (needs a decision)

## 1. Goals 📐

- Prompt for an image in a browser, or have an agent do it over MCP, and get back a stored artifact.
- Every artifact records **how it was made** (prompt, model, seed, size, steps, preset, post-processing)
  so it can be reproduced or tweaked.
- **Tweak** an artifact into a new one: re-roll, edit the prompt, edit from the image itself (klein
  reference editing), or re-run post-processing. Tweaks link to their parent, so each piece has a lineage.
- Organise artifacts into **projects**.
- A small group: the user and friends doing game dev. Not a public site.

Non-goals for now: video/audio/3D, model fine-tuning, billing, per-project sharing lists or roles.

## 2. Access (decided 2026-11-01) 📐

- **Everything is behind login**, the home page included: signed-out visitors get a splash screen with
  a sign-in button and nothing else. No anonymous browsing (unlike photo-dump's public pages).
- Sign-in via the cluster's authentik (in-app OIDC, the webdoc/nodewright pattern). Who may sign in is
  an authentik group (`gen-ai-platform-users`); friends get authentik accounts in that group.
- **Projects are public or private.** Private = visible only to its creator. Public = visible to every
  signed-in user of the platform. That is the whole model: no members, no roles.
- **No admin view by default**: an admin can not see other users' private projects.
- Only a project's creator adds to it, public or private (decided 2026-11-01). Others can view a public
  project and tweak its artifacts *into one of their own projects*; the child links back to the parent.
- MCP always authenticated (OAuth, copied from webdoc's `oauth.py`), acting as the signed-in user with the
  same visibility rules.

## 3. Data model 📐

- **User** — from OIDC (`sub`, display name, email).
- **Project** — `id, owner, name, description, visibility (private|public), created_at`. Every user gets a
  default private project (`Scratch`) so a generate call never needs one.
- **Artifact** — one generated image:
  - `project_id, created_by, created_at, kind (image)`
  - `prompt` (what the user typed), `full_prompt` (after the preset template), `negative`/`preset`
  - `model` (e.g. `flux2-klein-4b`), `ran_model`/`worker` as returned by gpu-scheduler, `seed, width,
    height, steps, cfg`
  - `parent_id` + `op` (`generate | reroll | edit_prompt | edit_image | postprocess`)
  - `status` (`queued | running | done | failed`), `error`
  - `title`, `tags`
- **Variant** — files of an artifact: `original` (the model's output) plus derived ones, e.g.
  `sprite_64` (keyed, cropped, downscaled, palette-reduced). Each with storage key, size, dimensions,
  and the post-process params used.

Objects live in an AIStor bucket (`gen-ai-platform`), metadata in Postgres.

## 4. Generation 📐

- **Two models from the start (decided 2026-11-01):** `flux2-klein-4b` (default) and `flux2-klein-9b`,
  chosen per request — a switch in the UI, a `model` argument on MCP tools. 4B for most work, 9B for
  harder prompts.
- Through **gpu-scheduler**, never directly: the platform submits a job for the logical model and gets
  a callback. New gpu-scheduler adapter runs `sd-server` (stable-diffusion.cpp,
  CUDA build) with three files per model: diffusion GGUF, Qwen3 text encoder GGUF (Qwen3-4B for 4B, Qwen3-8B for 9B), and
  the shared FLUX.2 VAE (`Comfy-Org/flux2-klein-4B` `split_files/vae/flux2-vae.safetensors`).
- **Output path** ✅ (gpu-scheduler `models/sdcpp.py`, live 2026-11-01): the platform puts a presigned PUT URL
  for the original in the job (`output_url`); the adapter uploads the PNG there and the result carries only
  metadata (seed, size, steps, model, bytes, seconds). Reference images for edits go in as `ref_images`
  (`{"image_url"}` presigned GETs, up to 4). The adapter always picks and returns the seed. Same seed reproduces
  an image only on the same GPU.
- **GPU: the 3080 on ainode (12 GB)** (decided 2026-11-01), queued with the other gpu-scheduler models.

| | 4B | 9B |
|---|---|---|
| Files | `flux-2-klein-4b-Q4_K_S.gguf` 2.6 GB + `Qwen3-4B-Q4_K_M.gguf` 2.5 GB | `flux-2-klein-9b-Q4_K_M.gguf` 5.7 GB + `Qwen3-8B-Q4_K_M.gguf` 5.0 GB |
| 1024², 4 steps, 5080, warm sd-server (measured) | 1.96 s; reference edit 4.5 s | 3.4 s; edit 7.3 s |
| Peak VRAM, 5080 (measured) | 8.2 GB | 12.5 GB — over the 3080's 12 |
| **3080, prod (measured 2026-11-01)** | **2.9 s warm; edit 6.7 s; 8.1 GB peak** | **with `--offload-to-cpu`: 6.3 s warm; edit 13.5 s; 8.7 GB peak** |
| 3080 first job after a fresh PVC (download + load + image) | 137.5 s | 276.9 s |

  Weights: the user's unsloth GGUFs; text encoders from `unsloth/Qwen3-4B-GGUF` and `unsloth/Qwen3-8B-GGUF`.
- **Licences differ.** klein 4B is Apache 2.0. klein 9B is under the **FLUX Non-Commercial License**
  (per the `unsloth/FLUX.2-klein-9B-GGUF` model card; the BFL repo is gated). Images from 9B may not be
  usable in a commercial game. The UI and MCP should say so when 9B is chosen, and each artifact
  records its model.
- Quality, same 6 prompts and seed (prototype): 9B honoured "no shadow" (4B drew one), gave the slime a
  face and shading, added the fox's tail; cleaner edges overall.

### 4a. Qwen-Image 2512 (tested on the 5080 2026-11-01) 🧪

- "Qwen Image Max" (`qwen-image-max-2025-12-30`) is an **API-only** Alibaba Model Studio product: no weights. The open
  weights are **`Qwen/Qwen-Image-2512`** (~20B), model card `license: apache-2.0`. Quality relative to Max: unknown.
  Trap: the newer `Qwen-Image-2.1` / `-Turbo` are under the Qwen Research License, not Apache.
- Files (in `~/models/qwen-image/`): `byteshape/Qwen-Image-2512-GGUF` Q3_K_M (8.8 GB) or Q4_K_S (10.2 GB),
  Qwen2.5-VL-7B-Instruct Q4_K_M (`--llm`, 4.7 GB), `Comfy-Org/Qwen-Image_ComfyUI` VAE (0.25 GB), 4-step Lightning LoRA
  `lightx2v/Qwen-Image-2512-Lightning` bf16 (0.85 GB, Apache-2.0). Scripts: `ai/model-test-scripts/qwen_image/`.
- sd.cpp at the pinned commit (df0fa26) runs it with the LoRA: 2160/2160 LoRA tensors applied, 4 steps at cfg 1.0,
  euler, flow-shift 3. sd-server ignores `<lora:…>` in prompts: the request needs `"lora": [{"path": "<file>",
  "multiplier": 1}]` with `--lora-model-dir`. `--lora-apply-mode immediately` aborts on the quantized model (runtime
  mode works).
- Measured on the 5080, `--offload-to-cpu --max-vram 11 --vae-tiling --diffusion-fa`, warm sd-server, Q3 + LoRA:
  | size | per image (HTTP round trip) | of which sampling / VAE decode |
  |---|---|---|
  | 512² | 8.0–9.8 s | 4.6 s / 0.5 s |
  | 768² | 11.5–13.6 s | ⚠ not split out |
  | 1024² | 21.8–22.9 s | 18.0 s / 3.5 s |
  Peak VRAM above the idle desktop: ~10.4 GB (Q3), ~11.2 GB (Q4, sd-cli), so it fits a 12 GB card; the
  `--max-vram 11` cap cost no speed. RAM for the weights 12.7 GB (sd.cpp's own count, Q3), inside the scheduler pod's
  16 Gi limit with ⚠ unmeasured headroom. Compare klein 4B ~2 s warm on the same card.
- 3080: ⚠ unmeasured. Scaling by klein 4B's 3080/5080 ratio (2.9 s vs ~2 s) gives an estimate of ~12 s at 512² and
  ~30 s at 1024².
- Quality (sprite prompts, seed 42/7): clean pixel art, flat key colour; Q3 and Q4 near-identical; draws objects for
  item prompts (a treasure map, where klein's sprite preset drew a person); 512² good enough for sprites (they end at
  64 px). Misses: "translucent" ignored (slime), one 1024² knight with two swords, the green item background tinted
  the map's land green (the key-colour caveat of §5).
- Editing: `Qwen/Qwen-Image-Edit-2511` (Apache-2.0) is a separate checkpoint (a second model) needing the vision
  projector (`--llm_vision`); not tested.
- gpu-scheduler change if adopted: registry entry with `--lora-model-dir`, `--offload-to-cpu`, `--vae-tiling`, flow
  shift; adapter sends the `lora` field. Platform: a third model in the catalogue, default size 512² for sprite presets.

## 5. Presets and post-processing 📐

A preset = prompt template + default size/steps + post-processing chain. Generation stays general;
`sprite` is the first preset (decided 2026-11-01: sprites are a preset prompt within a general generator).

`sprite` (from the prototype in `ai/model-test-scripts/flux2_klein/`):
- Template: "pixel art game sprite of {subject}, full body, single character centred, side view, clean
  outlines, limited palette, flat solid magenta background, no shadow, no text".
- Post-process: key out the background colour sampled from the corners (klein renders "#ff00ff" as
  hot pink — observed #f10f88, #ff1581, #f418a3, #fd0f95), crop, BOX downscale, palette reduce.
- Known weak spots from the first 7 samples: drop shadows survive keying (treasure chest); glows pick up
  background tint (wizard); "translucent" ignored (slime); "side view" not reliably honoured; per-sprite
  crop-to-fit gives inconsistent scale — needs a fixed-canvas option (feet on the bottom row).

Post-processing runs in the platform (CPU, Pillow), not on the GPU, and can be re-run on an existing
artifact with new params, producing a new variant.

## 6. MCP tools ✅ (0.2.0, `api/genai/mcp_server.py`, OAuth `oauth.py`, tokens `tokens.py`)

Streamable HTTP at `/mcp` (stateless, JSON). Auth is webdoc's: OAuth with dynamic client registration and a consent
page (sign-in through authentik on the way if needed), or a personal access token from the Agents page. Agents act as
their user with the web UI's visibility rules; no tool deletes anything or changes a project's visibility.

- `list_models_and_presets`, `list_projects`, `create_project(name, description, visibility)`
- `generate(prompt, project?, preset?, model?, width?, height?, seed?, count?, steps?, wait_seconds=60)` → artifacts;
  waits (≤120 s) for them, returning anything still pending with its status. 9B results carry a non-commercial note.
- `get_artifact(id)`: recipe, status, web page, and once done a `download_url`: a link that needs no token and expires
  after 10 minutes (0.2.1, `links.py`; random token stored hashed), so an OAuth-connected agent, whose token lives in
  its client, can `curl` the PNG into a repo. `generate` returns one per finished image too. The bearer-token route
  `/api/artifacts/<id>/image` still works for personal tokens.
- `get_image(id, size=preview|full)`: the image as MCP image content (preview fits 512 px, nearest-neighbour for
  sprites) so the agent can look at it.
- `list_artifacts(query?, project?, mine_only?, limit, before)`: prompt-text search over what the user can see.
- `tweak(artifact_id, op=reroll|edit_prompt|edit_image, prompt?, seed?, count?, model?, project?)` (0.3.0).

Verified 2026-11-01: 15 MCP/OAuth tests (full browser OAuth flow incl. sign-in detour, refresh rotation, revoke), and
the SDK's own streamable-HTTP client against a local sd-server (create_project → generate into it by name → get_image →
bearer download → search).

## 7. Web UI 📐

- Splash + sign-in (signed out).
- Home: prompt box (preset, project, model switch 4B/9B — default 4B, size, seed, count) and a live grid of recent results.
- Projects: my projects, public projects; project page = artifact grid.
- Artifact page: image(s) and variants, full recipe, lineage (parent and children), actions: re-roll,
  edit prompt, edit image, re-post-process, move, download.

## 8. Architecture 📐

Same stack as webdoc: FastAPI + SQLAlchemy on the cluster's Postgres, React/Vite UI, MCP in-process,
AIStor bucket, Helm chart at `helm/gen-ai-platform/` deployed by Argo, served behind authentik at a
**`genai.danwils.com`** (decided 2026-11-01). Public GitHub repo `gen-ai-platform`. Auth/OAuth/tokens copied from webdoc.

## 9. Phases 📐 (proposal)

1. ✅ gpu-scheduler `flux2-klein-4b` and `flux2-klein-9b` adapters (sd-server, presigned upload), live and
   smoke-tested on the 3080 2026-11-01 (gpu-scheduler sha-5781dffbc4c0).
2. Platform core: auth, splash, projects (public/private), generate, artifacts, storage, grid UI.
   - 2a ✅ backend (2026-11-01): `api/genai/` — authentik OIDC gated on `USER_GROUP`, sessions, projects, generate
     (one gpu-scheduler job per image, presigned PUT into the bucket), a poller folding job results into artifacts,
     an auth-checked image proxy; 17 tests. Schema by `create_all` until there's prod data to keep.
   - 2b ✅ UI (2026-11-01, `ui/`, React/Vite as webdoc): splash, prompt bar over a live grid, projects, project
     page, image page; six workflows verified in a browser against a local sd-server (`.ui_dev_state.md`). Dev-only
     `DEV_SD_SERVER` runs jobs on a local sd-server instead of gpu-scheduler.
   - 2c deploy (2026-11-01): image (Dockerfile, CI publishes `dwilson2547/gen-ai-platform`), chart
     `helm/gen-ai-platform` (Argo app `gen-ai-platform` in cluster-config), authentik blueprint bound to
     `gen-ai-platform-users`, `scripts/create-secrets.sh` for the database, gpu-scheduler token, bucket keys and OIDC
     secret. Image checked locally: read-only root, non-root, against Postgres 17.
3. ✅ MCP server with OAuth (0.2.0).
4. Tweaks and lineage; `sprite` preset and post-processing variants.
   - 4a ✅ (0.3.0, 2026-11-01) tweaks: `reroll` (same recipe, new seed), `edit_prompt` (new prompt, parent's seed),
     `edit_image` (instruction + the parent's image as a FLUX.2 reference, no preset template; keeps the parent's preset for clean-up since 0.4.1). Re-rolling or re-wording
     an edit redoes it from the same source image. Results link to their parent (`parent_id`, `op`); the image page shows
     "Made from" and "Made from this", filtered by visibility. A tweak goes into the given project, else the parent's
     if it is yours, else your Scratch. API `POST /api/artifacts/<id>/tweak`, MCP tool `tweak`. References reach
     gpu-scheduler as presigned GETs.
   - 4b ✅ (0.4.0, 2026-11-01, user's choices): presets "Character sprite" (`sprite`, unchanged name) and "Item sprite"
     (`item_sprite`, no full-body/side-view); background magenta (default) or green, read back from the stored full
     prompt (no new column); re-rolls reuse the exact stored full prompt. Sprites get a transparent `cutout` variant
     automatically when they finish (keyed by hue near the corner-sampled key colour, so same-hue drop shadows go too);
     `pixel` variants on demand (8–512 px, 0/2–256 colours, square tile standing on the bottom row or fit), owner only,
     same settings replace. New table `variants`; download links reach variants with `?variant=`. MCP: `make_sprite`,
     `generate(background=)`, `get_image(variant=)`, variants in results. A subject with the key's hue loses those
     parts in the cutout (a pink X on a map, on magenta): that's what the green option is for.
5. More presets.

## 10. Open questions ❓

- None blocking phase 2. Later: Qwen-Image as a third, Apache-2.0 model (§4a).
