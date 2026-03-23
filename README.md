# Loki Direct (Local Grok Companion)

Run a local “Grok companion” that can:
- **Chat via xAI** (Grok) with tool-calling.
- **Control your desktop** (mouse/keyboard + screenshots) via `pyautogui`.
- **Control Intiface / Buttplug toys** (e.g., Lovense Nora) via `ws://127.0.0.1:12345`.
- **Ingest text/images/PDFs into persistent memory** (SQLite vector store).
- **Dropbox-style memory inbox**: drop files into `memories/inbox/` and Loki auto-processes + recalls them later.
- **Self-upgrade via plugins**: ask “add X” and Loki can generate a plugin file in `loki_plugins/`.
- **Authoritative time**: every model call includes **Unix epoch + ISO 8601** local/UTC in the system prompt; tool **`get_current_time`** for explicit checks.
- **Apple Calendar (macOS)**: read/create/update/delete events in **Calendar.app** via automation tools (optional).

This repo is evolving toward a fully local AI companion loop: perception (screen/files), action (desktop/toys), and memory (searchable recall).

---

## Quick start

### Requirements
- **macOS** (current setup), Python 3.10+
- **Intiface Central** (optional, for toys)
- Repo includes a `venv/`

### Install deps (venv)

```bash
./venv/bin/python -m pip install -U requests python-dotenv pyautogui buttplug pypdf
```

### Configure xAI key

Create/edit `.env`:

```bash
XAI_API_KEY=your_key_here
```

### Run

- **Terminal**:

```bash
python3 loki_direct.py
```

- **One-click (macOS)**: double-click `Start_Loki.command`

`loki_direct.py` will attempt to use the repo `venv` automatically when run with `python3`.

- **Web UI (macOS)**: double-click `Start_Loki_GUI.command` to open a basic browser UI with buttons (including Hold-to-Talk).
  - After starting, open: `http://127.0.0.1:7865`

Voice in the web UI is button-driven (press-and-hold) and uses your microphone + macOS `say` for speech.

---

## Using Loki (chat UI)

Loki is a simple CLI chat. You can:
- chat normally (“what should I do today?”)
- ask it to act (“click the top right button”, “type hello”)
- drop files into memory inbox for recall

### Built-in chat commands
- **`/help`**: show commands
- **`/tools`**: list tool names
- **`/scan`**: scan Intiface devices
- **`/mem`**: reload `memories/` text memory snapshot (not the vector DB)
- **`/attach <path>`**: attach a file (text/image/pdf) for immediate analysis
- **`/ingest <path>`**: ingest a file or folder into vector memory (manual)
- **`/compile_mem`**: write compiled memory document
- **`/upgrade <request>`**: generate a plugin (e.g. `/upgrade add tts`)
- **`/quit`**

### Pasting a file path
If you paste an **absolute path** to an existing file, Loki will **auto-treat it like `/attach`**.

---

## Desktop access (screen + input)

### What exists today
Tools (available to Grok via tool-calls):
- **`click`**: click coordinates
- **`type_text`**: type into focused field
- **`hotkey`**: press key combos
- **`monitors`**: list monitors with indices (0..N-1)
- **`screenshot_monitor_base64`**: screenshot one monitor and return a data URL
- **`screenshot_all_monitors_base64`**: screenshot all monitors and return data URLs
- **`screenshot`** / **`screenshot_base64`**: legacy single-screen screenshot helpers

### Feasibility: “see both screens”
**Yes, feasible.** On macOS, screenshotting + multi-monitor capture is doable, but requires permissions:
- **System Settings → Privacy & Security → Accessibility** (for control)
- **System Settings → Privacy & Security → Screen Recording** (for screenshots)

To make Loki “respond in chat accordingly”, the common loop is:
1) capture screenshot(s)
2) send to Grok as image input
3) Grok chooses actions (click/type/hotkey) based on what it sees

Vision analysis is done by Loki using xAI's **Responses API** (not the chat-completions tool loop), so screenshots can be understood reliably.

---

## Toy control (Intiface / Buttplug)

Loki connects to Intiface Central at:
- **`ws://127.0.0.1:12345`** (default)

Tools:
- **`intiface_status`**
- **`scan_devices`**
- **`list_devices`**
- **`vibrate`** (default matches “nora”)
- **`stop_device`**

Notes:
- `buttplug==1.0.0` requires `await dev.run_output(...)` — Loki Direct implements this.

---

## Persistent memory (vector DB + compiled doc)

There are three related layers:

- **Personality (`memories/persona/instructions.md`)**: centralized markdown for how Loki **writes**, **behaves**, and **speaks in text** (tone, cadence, boundaries). Injected into the **system prompt** every reply. The **`memories/persona/`** tree is **excluded** from the generic snapshot below so it is not duplicated. **Web UI:** open **Personality & instructions** → edit → **Save & apply**. **Chat:** `/persona` (path + size), `/mem` (reload from disk). **From chat, Loki can edit it** using tools **`read_persona_instructions`** and **`update_persona_instructions`** (`mode`: `replace` for a full rewrite, `append` to add at the end); changes are saved to disk and the live session system prompt is refreshed automatically in Web UI / GUI / CLI.

### Brave Leo (custom model) + shared memory with home Loki

The **Web UI** exposes an **OpenAI-compatible** surface on the **same port** as the chat UI (default `7865`):

- **`GET /v1/models`** — lists your configured **`XAI_MODEL`** (Brave’s **Model request name** must match that string exactly).
- **`POST /v1/chat/completions`** — forwards the conversation to **Grok** (no tools). Each turn is appended to **`memories/cross_chat_log.jsonl`**.

**Home Loki** (Web UI / GUI / CLI) **loads the tail of that log into the system prompt** on every model call (up to **`LOKI_CROSS_CHAT_PROMPT_MAX_CHARS`**, default 8000), so when you talk at home he can recall what you said in Brave. With **`LOKI_BRAVE_LEO_INJECT_SYNC=1`** (default), the bridge **also prepends** that same log to **Brave** requests so Leo sees recent home chat.

**Brave settings (example):**

1. Start **`loki_direct_webui.py`** and keep it running on the Mac where Brave runs (same machine, or tunnel/VPN if you know what you’re doing).
2. **Server endpoint:** `http://127.0.0.1:7865/v1` (no trailing slash on some builds — if Brave fails, try with/without `/v1` per Brave’s hint text).
3. **API Key:** optional locally; if you set **`LOKI_LEO_BRIDGE_API_KEY`** in `.env`, use the **same** value in Brave (sent as `Authorization: Bearer …`).
4. **Model request name:** exactly **`XAI_MODEL`** from `.env` (e.g. `grok-4-1-fast-reasoning`).
5. **Streaming:** not supported yet — disable stream if Brave offers it.
6. **System prompt in Brave:** you can mirror **`memories/persona/instructions.md`** there for Leo’s tone; the **shared log** is separate and automatic.

**Env toggles:** `LOKI_CROSS_CHAT_LOG`, `LOKI_CROSS_CHAT_LOG_PATH`, `LOKI_CROSS_CHAT_PROMPT_MAX_CHARS`, `LOKI_CROSS_CHAT_APPEND_HOME`, `LOKI_BRAVE_LEO_INJECT_SYNC`, `LOKI_LEO_BRIDGE_API_KEY`.
- **Snapshot memory (`/mem`)**: loads other text files from `memories/` (recursive) into the system prompt.
- **Vector memory (SQLite)**: ingests files into `loki_memory.sqlite3` for semantic recall on every user message.

### Supported memory file types
- **Text**: `.txt .md .json .yml .yaml`
- **Images**: `.png .jpg .jpeg .webp .gif` (captioned for indexing)
- **PDF**: `.pdf` (text extracted via `pypdf`)

### Dropbox-style workflow (recommended)
Drop files into:
- **`memories/inbox/`**

Loki watcher will:
1) wait for the file to finish copying
2) move it into:
   - **`memories/processed/`** (timestamped filename)
3) ingest into SQLite vector memory
4) update:
   - **`memories/compiled_memory.md`**

**Processed files remain recallable** because recall uses the vector DB.

---

## Environment toggles (all in `.env`)

### xAI
- **`XAI_API_KEY`**: required
- **`XAI_ENDPOINT`**: default `https://api.x.ai/v1/chat/completions`
- **`XAI_MODEL`**: default `grok-4-1-fast-reasoning`

### Embeddings / retrieval
Loki tries xAI embeddings, but will fall back to local hashing embeddings if you don’t have access.
- **`XAI_EMBEDDING_MODEL`**: default `grok-embedding` (may not be available)
- **`XAI_EMBEDDINGS_ENDPOINT`**: default `https://api.x.ai/v1/embeddings`
- **`LOKI_RETRIEVAL_K`**: default `6`

### Memory + DB paths
- **`LOKI_MEMORY_DIR`**: default `memories`
- **`LOKI_PERSONA_DIR`**: default `memories/persona`
- **`LOKI_PERSONA_INSTRUCTIONS_PATH`**: default `memories/persona/instructions.md`
- **`LOKI_PERSONA_INSTRUCTIONS_MAX_CHARS`**: default `48000`
- **`LOKI_INBOX_DIR`**: default `memories/inbox`
- **`LOKI_PROCESSED_DIR`**: default `memories/processed`
- **`LOKI_VECTOR_DB_PATH`**: default `loki_memory.sqlite3`
- **`LOKI_COMPILED_MEMORY_PATH`**: default `memories/compiled_memory.md`

### Watcher
- **`LOKI_WATCH_MEMORY_FOLDER`**: `1` (on) / `0` (off)
- **`LOKI_WATCH_POLL_S`**: default `2.0`

### Intiface
- **`INTIFACE_WS`**: default `ws://127.0.0.1:12345`

### Networking
- **`LOKI_HTTP_TIMEOUT_S`**: default `60`

### Time
- **`LOKI_TIME_SYSTEM_PROMPT`**: `1` (on) / `0` (off) — inject epoch + ISO clock block every model call

### Apple Calendar (macOS only)
- **`LOKI_APPLE_CALENDAR`**: `1` (on) / `0` (off)
- **`LOKI_APPLE_CALENDAR_DEFAULT`**: default calendar name when unspecified (default `Calendar`)

### Voice / TTS
- **`LOKI_VOICE_TTS_ENABLE`**: default spoken replies on/off (overridden by `memories/tts_settings.json` after UI save)
- **`LOKI_SAY_VOICE`**: macOS `say -v` name (e.g. `Daniel`)
- **`LOKI_SAY_RATE`**: `say -r` words per minute (empty = system default)
- **`LOKI_TTS_SETTINGS_PATH`**: JSON file for saved UI voice settings (default `memories/tts_settings.json`)
- **`LOKI_TTS_ENGINE`**: `say` (default) or **`piper`** for local neural TTS
- **`LOKI_PIPER_VOICE`**: Piper voice id (default `en_US-lessac-medium`) or path to a `.onnx` model file
- **`LOKI_PIPER_DATA_DIR`**: folder where downloaded Piper voices live (default `memories/piper_voices`)
- **`LOKI_PIPER_BINARY`**: legacy `piper` CLI path when using a raw `.onnx` file (default `piper`)
- **`LOKI_PIPER_MODEL`**: optional explicit `.onnx` path (overrides voice id when set and file exists)
- **`LOKI_PIPER_LENGTH_SCALE`**: Piper **pace** / phoneme length (default `1.0`; also used for `python -m piper`)
- **`LOKI_PIPER_NOISE_SCALE`**: Piper **expression** / generator noise (default `0.667`; UI/server clamp roughly **0.18–1.2**)
- **`LOKI_PIPER_NOISE_W_SCALE`**: Piper **clarity** / phoneme width noise (default `0.8`; clamp roughly **0.3–1.4**)
- **`LOKI_PIPER_VOLUME`**: Piper output volume multiplier (default `1.0`)
- **`LOKI_PIPER_SENTENCE_SILENCE`**: seconds of silence after each sentence (default `0`). Piper’s CLI used to insert an **odd** gap in bytes for many values (e.g. 0.05 s at 22.05 kHz), which **misaligned 16‑bit PCM** and caused **loud static after the first sentence**. Loki now **nudges** the value slightly so the gap is always an even number of bytes.
- **`LOKI_PIPER_PLAYBACK_RATE`**: macOS **afplay** speed after synthesis (default `1.0`)
- **`LOKI_PIPER_SPEAKER`**: optional speaker id (integer) for multi-speaker models
- **`LOKI_PIPER_MODEL_DIR`**: optional folder for `GET /api/tts/piper_onnx_models` when browsing models in the UI
- **`LOKI_DEBUG_TTS`**: set to `1` / `true` to print Piper preview parameters (pace, noise scales, volume, …) to the terminal

---

## Self-upgrades (plugins)

Use:
- **`/upgrade <request>`**

It writes a new plugin to:
- `loki_plugins/<something>.py`

Plugins can register tools via:
- `tools.register(ToolSpec(...))`
- `tools.add_tool(...)`
- `tools.append({...})`

Security note: plugin generation executes code you’re generating. Keep this local and treat it as trusted-by-you.

---

## Voice (feasibility + recommendations)

### TTS (Loki speaks)
**Default: macOS `say`** (zero extra deps). It’s fast but can sound “system robot” unless you tune it.

**Web UI (recommended)**  
Open **“Voice & speech (how Loki sounds)”** on the chat page:
- **Speak replies** — turn spoken answers on/off (independent of “Voice On” for the mic).
- **TTS engine** — **macOS say** or **Piper** (local neural).
- **macOS say**: **Voice** — every voice macOS exposes via `say -v ?` (try **Daniel**, **Tom**, **Fred** for US English male; **Samantha** / **Karen** for female; **Premium** voices need **System Settings → Siri & Spotlight → Siri Voice** downloads). **Speaking rate (WPM)** — slightly **slower** (e.g. 150–175) often sounds more natural than the default.
- **Piper**: **tap a voice card** (refreshes from your voice folder), tune **Sound** sliders (pace, expression, clarity, volume, pauses, playback speed), or use **Advanced** for folder path / downloads. Settings auto-save shortly after you change them.
- **Save** writes **`memories/tts_settings.json`** so CLI and web share the same profile.

**`.env` (defaults before first save)**  
- **`LOKI_SAY_VOICE`** — e.g. `Daniel` (empty = system default)  
- **`LOKI_SAY_RATE`** — words per minute for `say -r` (empty = system default)  
- **`LOKI_TTS_SETTINGS_PATH`** — override JSON path (default `memories/tts_settings.json`)
- **`LOKI_TTS_ENGINE=say`** or **`piper`**, plus **`LOKI_PIPER_*`** vars (see **Voice / TTS** above)

**Piper setup (recommended path: Python package)**  
1. In your venv: `pip install piper-tts` (or `pip install -r requirements-piper.txt`).  
   If the log shows **`No module named pathvalidate`**, run: `./venv/bin/pip install pathvalidate` (included in `requirements-piper.txt`).  
2. **See every voice you can install:** `./venv/bin/python -m piper.download_voices` (prints ids like `en_US-lessac-medium`, `en_GB-alan-medium`, …).  
3. **Download** into your Loki data folder (same path you’ll set in the UI), e.g.  
   `./venv/bin/python -m piper.download_voices --data-dir memories/piper_voices en_US-lessac-medium`  
   Repeat with another id to add more voices (each creates `<id>.onnx` + `<id>.onnx.json`).  
4. In the Web UI choose **Piper**, set **Piper data dir**, **Scan folder for voices**, select a voice, **Save**, then **Test voice**.
5. Sanity-check noise sliders: `./venv/bin/python smoke_piper_tts.py` (expects different WAV hashes for min vs max expression/clarity).  
6. **Browse / preview** voices: [Piper samples](https://rhasspy.github.io/piper-samples) · full catalog on [Hugging Face](https://huggingface.co/rhasspy/piper-voices/tree/main).  
7. Audio plays via **`afplay`** on macOS. If Piper fails (missing install/model), Loki **falls back to `say`** and logs `[tts] Piper synthesis failed`.

**More natural speech (hardware / install)**  
| Option | Quality | Notes |
|--------|---------|--------|
| **macOS `say` + Premium voice** | Good | Download enhanced voices in System Settings; pick them in the UI dropdown. |
| **[Piper](https://github.com/OHF-Voice/piper1-gpl)** (wired in-repo) | Very good | Local neural TTS via `piper-tts` or legacy `piper` + `.onnx`. |
| **Cloud APIs** (OpenAI TTS, ElevenLabs, etc.) | Best | Requires keys + network; can be added as a plugin later. |

Recommendation: tune **`say`** first for zero setup; switch the UI (or `LOKI_TTS_ENGINE=piper`) for **Piper** when you want more natural local speech.

### STT (you speak to Loki)
**Feasible.** Options:
- **whisper.cpp** (excellent offline accuracy; local-first default)
- **faster-whisper** (great; more deps; GPU optional)
- **Vosk** (lightweight; lower accuracy)

Recommendation: **whisper.cpp** for a local companion.

### Full “voice mode”
Feasible, but you’ll want to decide:
- push-to-talk vs always-on mic
- echo cancellation
- interruptibility (“barge-in”)

---

## Troubleshooting

### macOS won’t click/type or screenshot
Enable permissions for the app you’re running Loki from (Terminal/Cursor):
- **Accessibility**
- **Screen Recording**

### Intiface connection fails
- Start Intiface server and keep it running
- Confirm `INTIFACE_WS=ws://127.0.0.1:12345`

### Embeddings model not available
If xAI embeddings return 404/permission errors, Loki uses a **local embedding fallback** for retrieval.

---

## Roadmap ideas

- **Multi-monitor vision tools**: `screenshot_monitor(i)` + `monitors()` + region capture.
- **OCR**: reading text on-screen (e.g. chat apps). Options: `tesseract` or macOS Vision framework.
- **Better local embeddings**: add a local embedding model later for higher-quality recall.
- **Safety controls**: “confirm before click”, allowlist apps/regions.
- **Voice**: Web UI TTS tuning; Piper is available locally; optional cloud TTS backends.

