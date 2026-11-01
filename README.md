---
tier: project
domain: ai
---

# gen-ai-platform

Generate, store and tweak generative-AI artifacts — images for now — from a web page or from agents
over an OAuth MCP server. Every artifact keeps the prompt and settings that made it and links to the
artifact it was tweaked from; artifacts are organised into public or private projects. Models: FLUX.2 klein 4B
(default) and 9B, selectable per request. Built first for game sprites, but sprites are a preset, not
the whole product. Served at genai.danwils.com (planned).

Status: design only. See [`docs/design.md`](docs/design.md).

Prototype scripts (sd.cpp + sprite cleanup): `ai/model-test-scripts/flux2_klein/`.
