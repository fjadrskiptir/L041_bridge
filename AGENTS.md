# Agent notes — Loki Direct (l041_bridge)

Two chat roles work well here: **upgrades** (default) vs **debug** (when something breaks).

## When to open which chat

| Situation | Chat |
|-----------|------|
| New behavior, refactors, docs, larger design | **This project’s main Composer chat** (upgrade / feature thread) |
| Regression, crash, wrong output, HTTP/API errors, “it used to work” | **New chat** + attach rule **Debug protocol** (`.cursor/rules/debug-protocol.mdc`) |

Keeping debug separate avoids mixing large feature diffs with urgent fixes and preserves context in each thread.

## Debug chat — what to say first

1. In Cursor, start a **new chat**.
2. **Attach / @** the rule: **Debug protocol** (or open `.cursor/rules/debug-protocol.mdc` so it is in context).
3. Paste the block below, filled in.

```text
--- DEBUG HANDOFF (paste into new chat + @ Debug protocol) ---

Repo: l041_bridge (Loki Direct). Branch: <branch or main>

Symptom: <what broke, in one short paragraph>

Repro: <numbered steps — e.g. launch Web UI, click X, speak, which setting>

Expected vs actual: <what should happen> / <what happens>

Evidence: <paste full error line or traceback; scrub secrets>

Recent change (if known): <commit, PR, or “unknown”>

Constraint: Debug only — minimal diff, no feature work unless required to fix.

--- END ---
```

4. Add paths if you know them: e.g. `loki_direct_webui.py`, `loki_elevenlabs_tts.py`.

The debug thread should pick up from this without pulling the upgrade thread off track.

## Hygiene reminder

- **Commit:** `*.py`, shared scripts, `README.md`, `.cursor/rules`, this file.
- **Do not commit:** `.env`, local `memories/*` state, sqlite DBs — see `.gitignore`.
