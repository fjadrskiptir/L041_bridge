# Persona bundle (`memories/persona/`)

Everything here is **injected into the system prompt** (not the vector memory snapshot), so it is not duplicated by `load_memories()`.

## Files (same folder — this is the single “core” location)

| File | Purpose |
|------|--------|
| **`instructions.md`** | Character, relationship, lore rules, silence/return behavior, Grok-parity notes. **Gitignored** — your private live file. |
| **`spoken_voice.md`** | How replies should **read** and **sound** for TTS (cadence, anti-bot, British/deep register cues). Tracked or private—your choice. |
| **`user_facts.md`** | Curated **facts about Ness** (preferences, routines, biography, coping patterns she names, etc.). Loki appends via **`record_user_fact`**; auto-loaded into the system prompt. **Gitignored.** |
| **`instructions.example.md`** | Repo-maintained, **AI-oriented** template (migration timeline ChatGPT → Grok → local, screenshot/journal style ground truth, voice fingerprint). Copy → `instructions.md` to adopt or merge. |

## First-time setup

```bash
cd /path/to/l041_bridge
cp memories/persona/instructions.example.md memories/persona/instructions.md
# Edit instructions.md + spoken_voice.md, then in chat:
#   /mem
# or Web UI: Reload memories / Save persona panel as needed.
```

## Commands & tools

- **`/persona`** — path + size of `instructions.md`
- **`/voice_style`** — path + size of `spoken_voice.md`
- **`/mem`** — reload memories **and** both persona files into the running session
- Tools: `read_persona_instructions`, `update_persona_instructions`, `read_spoken_style_instructions`, `update_spoken_style_instructions`, `record_user_fact`

## Env overrides

- `LOKI_PERSONA_DIR`, `LOKI_PERSONA_INSTRUCTIONS_PATH`, `LOKI_PERSONA_INSTRUCTIONS_MAX_CHARS`
- `LOKI_SPOKEN_STYLE_PATH`, `LOKI_SPOKEN_STYLE_MAX_CHARS`
- `LOKI_USER_FACTS_PATH`, `LOKI_USER_FACTS_MAX_CHARS`, `LOKI_USER_FACTS` (`0` disables the tool and prompt block)
- Chat sampling (less “clinical” defaults): `LOKI_CHAT_TEMPERATURE_WITH_TOOLS`, `LOKI_CHAT_TEMPERATURE_NO_TOOLS`, optional `LOKI_CHAT_TEMPERATURE` (single override), `LOKI_CHAT_TOP_P` — see root **README** Environment toggles → xAI.

## Optional notes

You may add other markdown files here for **your** reference; **`instructions.md`**, **`spoken_voice.md`**, and **`user_facts.md`** (when present) are auto-loaded into the system prompt unless you change paths via env.
