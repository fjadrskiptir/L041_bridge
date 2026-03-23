# Persona (`memories/persona/`)

## `instructions.md` (canonical)

This file is loaded into the **system prompt** on every model call. Use it for personality, writing style, cadence, boundaries, and how Loki should address the user.

- **Web UI:** expand **Personality & instructions**, edit, then **Save & apply to chat**.
- **Chat:** `/persona` shows the path; `/mem` reloads memories **and** this file into the running session.
- **Env overrides:** `LOKI_PERSONA_DIR`, `LOKI_PERSONA_INSTRUCTIONS_PATH`, `LOKI_PERSONA_INSTRUCTIONS_MAX_CHARS`.
- **Tools (chat):** `read_persona_instructions`, `update_persona_instructions` — Loki can load or change this file when you ask; `append` vs `replace` modes are supported.

## Other files here

Everything under `memories/persona/` is **excluded** from the automatic memory-folder text snapshot (so `instructions.md` is not duplicated). You can still add notes for yourself or use `/ingest` on a file if you want it in vector search.
