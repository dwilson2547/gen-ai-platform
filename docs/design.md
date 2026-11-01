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

### 4a. Qwen-Image 2512 (researched 2026-11-01, not tested) ❓

From a research pass (model cards + the pinned sd.cpp checkout); nothing downloaded or run yet.
- "Qwen Image Max" (`qwen-image-max-2025-12-30`) is an **API-only** Alibaba Model Studio product: no weights. The open
  weights are **`Qwen/Qwen-Image-2512`** (~20B), model card `license: apache-2.0`. Quality relative to Max: unknown.
  Trap: the newer `Qwen-Image-2.1` / `-Turbo` are under the Qwen Research License, not Apache.
- sd.cpp at the pinned commit runs it (`docs/qwen_image.md`): diffusion GGUF + Qwen2.5-VL-7B text encoder (`--llm`) +
  Qwen-Image VAE. Base settings are ~40 steps with CFG (minutes per image); a 4-step **Lightning LoRA**
  (`lightx2v/Qwen-Image-2512-Lightning`, Apache-2.0) is what would make it usable — whether sd.cpp loads its keys is
  ⚠ unverified.
- 3080 fit, all ⚠ unverified estimates: Q4 file sets total 16–18 GB (over the scheduler pod's 16 Gi with offload);
  the smallest viable set is `byteshape/Qwen-Image-2512-GGUF` Q3_K_S (7.8 GB) + Qwen2.5-VL-7B Q4_K_M (4.7 GB) + VAE
  (0.25 GB) + Lightning LoRA (0.85 GB) ≈ 13.6 GB RAM. Speed guessed at 2–3× klein 9B with the LoRA.
- Editing: `Qwen/Qwen-Image-Edit-2511` (Apache-2.0) is a separate checkpoint (a second model) needing the vision
  projector (`--llm_vision`).
- gpu-scheduler change: mostly a registry entry; LoRA support (`--lora-model-dir` + `lora` in the request) and an
  optional vision file are small adapter additions.
- Next step if wanted: download the ~13.6 GB set, test on the 5080 (LoRA loads? quality at Q3 for sprites? speed),
  then decide.

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

## 6. MCP tools 📐

- `list_projects`, `create_project(name, visibility)`, `update_project`
- `generate(prompt, project?, preset?, model? (4b default | 9b), size?, seed?, count?)` → artifact ids (waits up to N seconds,
  then returns status so long batches don't block)
- `tweak(artifact_id, op, prompt?, seed?, project?)` — re-roll, edit prompt, edit from image
- `postprocess(artifact_id, preset_or_params)`
- `search_artifacts(query?, project?, tags?)`, `get_artifact(id)` — returns metadata and image URLs;
  `get_image(id, variant)` returns the image itself so the agent can look at it
- `list_presets`

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
   - 2c deploy: image, chart, CI, authentik provider + group, bucket, DB, gpu-scheduler client token.
3. MCP server with OAuth.
4. Tweaks and lineage; `sprite` preset and post-processing variants.
5. More presets.

## 10. Open questions ❓

- None blocking phase 2. Later: Qwen-Image as a third, Apache-2.0 model (§4a).
