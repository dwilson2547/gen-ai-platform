---
tier: project
domain: ai
---

# gen-ai-platform

Generate, store and tweak generative-AI artifacts — images for now — from a web page or from agents
over an OAuth MCP server. Every artifact keeps the prompt and settings that made it and links to the
artifact it was tweaked from; artifacts are organised into public or private projects. Models: FLUX.2 klein 4B
(default) and 9B, and Qwen-Image 2512 (text-to-image only), selectable per request. Built first for game sprites, but sprites are a preset, not
the whole product. Served at genai.danwils.com (planned).

Status: live at https://genai.danwils.com (0.1.0, 2026-11-01). 0.2.0 adds the MCP server (OAuth or personal tokens;
connect from the Agents page).

Dev: `cd ui && npm install && npm run build`, then `./start.sh` (SQLite, images in `.data/`, dev sign-in,
http://localhost:8097) and `./kill.sh`. Real images locally: run sd-server on :8155 and start with
`DEV_SD_SERVER=http://127.0.0.1:8155 ./start.sh`. UI dev with hot reload: `cd ui && npm run dev` (:5177, proxies the
API). Tests: `cd api && .venv/bin/pytest`.
See [`docs/design.md`](docs/design.md).

Prototype scripts (sd.cpp + sprite cleanup): `ai/model-test-scripts/flux2_klein/`.
