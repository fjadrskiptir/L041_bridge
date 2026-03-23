# Loki Direct (Local Grok Companion)

Run a local “Grok companion” that can:
- **Chat via xAI** (Grok) with tool-calling.
- **Web search** (DuckDuckGo via `web_search` tool — optional `duckduckgo-search` package).
- **Webcam (Web UI)**: capture a frame from your browser camera and send it with your message (xAI vision); not continuous video.
- **Telegram (Web UI)**: optional two-way chat + a few spontaneous “thinking of you” messages per day (your Mac stays the brain; phone works on cellular).
- **Control your desktop** (mouse/keyboard + screenshots) via `pyautogui`.
- **Control Intiface / Buttplug toys** (e.g., Lovense Nora) via `ws://127.0.0.1:12345`.
- **Ingest text/images/PDFs into persistent memory** (SQLite vector store).
- **Dropbox-style memory inbox**: drop files into `memories/inbox/` and Loki auto-processes + recalls them later.
- **Self-upgrade via plugins**: ask “add X” and Loki can generate a plugin file in `loki_plugins/`.
- **Authoritative time**: every model call includes **Unix epoch + ISO 8601** local/UTC in the system prompt; tool **`get_current_time`** for explicit checks.
- **Apple Calendar (macOS)**: read/create/update/delete events in **Calendar.app** via automation tools (optional).
- **Local art / image stack (optional)**: when **`LOKI_ART_WEBHOOK_URL`** is set, Loki gets tool **`submit_art_generation`** to POST prompts to *your* separate generator (ComfyUI bridge, A1111 API, custom server, etc.).

This repo is evolving toward a fully local AI companion loop: perception (screen/files), action (desktop/toys), and memory (searchable recall).

---

## Quick start

### Requirements
- **macOS** (current setup), Python 3.10+
- **Intiface Central** (optional, for toys)
- Repo includes a `venv/`

### Install deps (venv)

```bash
./venv/bin/python -m pip install -U requests python-dotenv pyautogui buttplug pypdf duckduckgo-search
```

(`duckduckgo-search` powers the **`web_search`** tool for research; Loki runs without it but will tell you to install if you ask him to search.)

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

- **Web UI (macOS)**: double-click `Start_Loki_GUI.command` to open a basic browser UI with buttons (including Hold-to-Talk and **Camera on / Send with camera**).
  - After starting, open: `http://127.0.0.1:7865`
  - New: **Stealth toggle** in the control row quickly blurs chat text and dims sensitive panels.
- **Desktop overlay (optional):** double-click `Start_L041_Overlay.command` for a small always-on-top orb that reflects state from `GET /api/presence` (`idle`, `listening`, `thinking`, `speaking`).  
  **Tk note:** Homebrew Python may lack Tk (`ModuleNotFoundError: _tkinter`) — install **`brew install python-tk@VERSION`** to match your `python@VERSION`. **Xcode / Command Line Tools Python** can `import tkinter` but **crash in `TkpInit` (Tcl_Panic)**; the launcher skips those interpreters. Override with **`LOKI_OVERLAY_PYTHON=/path/to/python3`** if needed.

Voice in the web UI is button-driven (press-and-hold) and uses your microphone + macOS `say` for speech.

---

## Local art / image generation (Loki → your app)

Loki does **not** embed a diffusion engine. Anything that *would* stop him before was simply **no tool wired to your generator**. With a webhook configured, he can call **`submit_art_generation`** with a detailed prompt (and optional `negative_prompt` / `style_notes` / `seed`).

### `.env` options

| Variable | Meaning |
|----------|---------|
| **`LOKI_ART_WEBHOOK_URL`** | `http://127.0.0.1:…` URL your art app listens on for **POST JSON**. |
| **`LOKI_ART_WEBHOOK_TIMEOUT_S`** | HTTP timeout (default `180`). Web UI allows extra headroom for this tool. |
| **`LOKI_ART_WEBHOOK_HEADERS_JSON`** | Optional JSON object of extra headers, e.g. `{"Authorization":"Bearer …"}`. |
| **`LOKI_ART_WEBHOOK_EXTRA_JSON`** | Optional JSON object **merged** into every request body (workflow id, size, etc.). |

### Default JSON body shape

Your service should accept a POST with JSON like:

```json
{
  "prompt": "…",
  "negative_prompt": "…",
  "style_notes": "…",
  "seed": 12345,
  "source": "loki"
}
```

(Only `prompt` and `source` are always present; other fields are omitted when empty.)

You implement the server or use your art app’s HTTP API / a tiny bridge script that translates this into ComfyUI / Automatic1111 / Flux workflows.

### Flux locally (realistic expectations)

**Flux** is a family of image models, not a single Terminal download. Typical setup:

1. Install a **UI or runtime** (e.g. **ComfyUI**, **Stable Diffusion WebUI Forge**, **InvokeAI**, etc.).
2. Download **model weights** from the model hub (often **many GB**; may need a Hugging Face account / license acceptance).
3. Point **`LOKI_ART_WEBHOOK_URL`** at a small **bridge** that receives Loki’s JSON and queues your workflow.

There is **no one safe universal `brew install flux`** line—hardware (VRAM/RAM), chosen UI, and which Flux variant you use all change the steps. When you pick a stack (e.g. “ComfyUI + Flux Dev on my GPU”), we can wire the exact bridge payload next.

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

### Web search
- **`LOKI_WEB_SEARCH`**: `1` (on) / `0` — enable tool `web_search` (DuckDuckGo).
- **`LOKI_WEB_SEARCH_MAX_RESULTS`**: default `8` (hard cap 15 per call).

### Webcam (Web UI only)
Uses **getUserMedia** in the browser (works on **localhost** or HTTPS). Each click of **Send with camera** uploads **one JPEG frame** to Loki; the server runs **xAI vision** (same path as `/attach` images), then Grok replies. Nothing is streamed continuously.
- **`LOKI_WEBCAM_MAX_MB`**: max decoded image size per frame (default `6`).

### Telegram (Web UI — long polling)
Your **phone talks to Telegram’s servers**; **`loki_direct_webui.py` on your Mac** long-polls Telegram and runs the **same Loki session** as the browser (shared `messages`, tools, memory). You can open the notification and **reply in the Telegram chat** on LTE/5G — no home Wi‑Fi required on the phone. **The Mac must be on** and the Web UI process running.

**Important:** **`Start_Loki.command` / `loki_direct.py` (CLI) does not run Telegram.** Only **`loki_direct_webui.py`** (e.g. **Start_Loki_GUI.command**) starts the bot. If messages get no reply, you’re almost certainly on CLI — switch to the Web UI launcher.

**While Loki is running, don’t open `getUpdates` in the browser** for that bot (it competes for the same update queue). Use **`getMe`** to test the token only.

1. Message **@BotFather** → `/newbot` → copy the **bot token** (looks like `123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`).
2. **Test the token in the browser** (replace the whole token — do **not** leave the word `TOKEN` or angle brackets in the URL):
   - `https://api.telegram.org/bot` **`PASTE_FULL_TOKEN_HERE`** `/getMe`  
   Example shape: `https://api.telegram.org/bot123456789:AAH.../getMe`  
   You should see `"ok":true` and your bot’s `username`.  
   **If you see `"ok":false` … `404` `Not Found`:** Telegram does that when the token is wrong — common causes: missing the literal word **`bot`** before the token, a typo, only half the token copied, extra spaces, or using the **bot’s @name** instead of the **token**.
3. Open Telegram, find your bot, tap **Start** (or send any message). Then visit:  
   `https://api.telegram.org/bot` **`SAME_TOKEN`** `/getUpdates`  
   In the JSON, find **`message` → `chat` → `id`** — that number is your **user id** for **`TELEGRAM_ALLOWED_CHAT_IDS`** (for a DM it’s usually positive; groups are negative).
4. In `.env`:
   - **`LOKI_TELEGRAM=1`**
   - **`TELEGRAM_BOT_TOKEN=...`**
   - **`TELEGRAM_ALLOWED_CHAT_IDS=123456789`** (comma-separated if several)

**If he never replies:** set **`LOKI_TELEGRAM_SETUP_HELP=1`**, restart the Web UI, message the bot again — you’ll get a hint with your **chat id** if `.env` doesn’t match (or send **`/myid`**). Turn **`LOKI_TELEGRAM_SETUP_HELP=0`** after setup.

**Diagnose env (no secrets):** open **`http://127.0.0.1:7865/api/telegram/status`** (use your real port from the launcher). You should see `telegram_enabled_flag`, `has_bot_token`, `allowed_chat_ids_count`, and whether the repo **`.env` file exists**. The Web UI also **always** prints a **`[telegram] config:`** line to **`/tmp/loki_direct_webui.log`** on startup — if you don’t see it, you’re not running `loki_direct_webui.py` or you’re tailing the wrong file.

**Proactive pings** (warm check-ins, capped per local day, grounded in **`cross_chat_log.jsonl`** when enabled):

**“Only when I’m not on home Wi‑Fi”:** your **Mac cannot tell** whether your **phone** is on home Wi‑Fi vs cellular. Practical options:
- Set **`LOKI_TELEGRAM_PROACTIVE_QUIET_HOURS_LOCAL`** to hours you’re usually **at home** (e.g. `22-7` = no spontaneous texts 10pm–7am local — uses **`LOKI_TELEGRAM_QUOTA_TZ`** if set).
- Or set **`LOKI_TELEGRAM_PROACTIVE_PER_DAY=0`** and rely on **manual** chat only until you add a phone Shortcut / other signal later.

- **`LOKI_TELEGRAM_PROACTIVE_PER_DAY`**: default `3` (`0` = inbound only).
- **`LOKI_TELEGRAM_PROACTIVE_MIN_INTERVAL_S`** / **`LOKI_TELEGRAM_PROACTIVE_MAX_INTERVAL_S`**: random spacing between pings (defaults ~1h–4h).
- **`LOKI_TELEGRAM_QUOTA_TZ`**: IANA timezone for the daily reset (e.g. `America/Los_Angeles`). If unset, uses the machine’s local calendar date.
- **`LOKI_TELEGRAM_QUOTA_PATH`**: override path for the quota JSON (default `memories/telegram_proactive_quota.json`).
- **`LOKI_TELEGRAM_PROACTIVE_INSTRUCTIONS_PATH`**: optional file; otherwise **`memories/telegram_proactive_instructions.md`** is read if present. See `memories/telegram_proactive_instructions.example.md`.
- **`LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL`**: default `0`. Set `1` to allow Telegram admin commands from allowed chat ids:
  - **`/loki_help`**: list remote admin commands
  - **`/loki_status`**: process status (pid/uptime)
  - **`/loki_mem_refresh`**: run `/ingest` for chat screenshots (default path `memories/Chats/Chat Screenshots`)
  - **`/loki_restart`**: restart the running Web UI process
  - **`/loki_stop`**: stop the running Web UI process
  - **`/loki_pause <duration>`**: unload launchd service now, auto-resume later (examples: `30m`, `2h`, `45s`)
  - **`/loki_resume`**: manually re-bootstrap + kickstart launchd service
- **`LOKI_TELEGRAM_MEM_REFRESH_PATH`**: optional override for `/loki_mem_refresh` ingest target folder.

Overlay tuning env vars (optional):
- **`LOKI_OVERLAY_PRESENCE_URL`**: default `http://127.0.0.1:7865/api/presence`
- **`LOKI_OVERLAY_SIZE`**: orb size in px (default `96`)
- **`LOKI_OVERLAY_ALPHA`**: window opacity `0.2-1.0` (default `0.92`)
- **`LOKI_OVERLAY_X`** / **`LOKI_OVERLAY_Y`**: screen position (default `24`, `24`)
- **`LOKI_OVERLAY_HUE_SHIFT_DEG`**: hue tint (default `0`)
- **`LOKI_OVERLAY_POLL_MS`**: poll interval (default `250`)

**Privacy:** only chat ids in **`TELEGRAM_ALLOWED_CHAT_IDS`** get replies. Inbound Telegram turns do **not** trigger Mac TTS (so your speaker doesn’t read every phone message aloud).

Proactive Telegram texts now also try to ground style/context from ingested memory chunks whose source path contains **`Chats/Chat Screenshots`**.  
Tip: run `/ingest memories/Chats/Chat Screenshots` at least once so those screenshots are represented in vector memory.

**Remote restart notes:** `/loki_restart` works while Loki is running and reachable on Telegram. If the process is fully down, Telegram commands cannot reach it; for true self-healing, run Loki under a macOS LaunchAgent with `KeepAlive`.

### macOS LaunchAgent (auto-restart / survives crashes)

If you want Loki to come back automatically (including after re-login), install the LaunchAgent once:

1. In Finder (or terminal), run:
   - `install_loki_launchagent.command`
2. It creates and loads:
   - `~/Library/LaunchAgents/com.ness.loki.webui.plist`
3. Logs:
   - `/tmp/loki_launchagent.log`
   - `/tmp/loki_launchagent.err.log`

Uninstall:
- `uninstall_loki_launchagent.command`

Files used:
- `run_loki_webui_service.sh` (service runner for launchd)

Notes:
- LaunchAgent uses `KeepAlive=true`, so if the process dies, launchd restarts it.
- With LaunchAgent installed, `/loki_restart` from Telegram is more reliable remotely.
- `/loki_stop` will stop the current process, but launchd may bring it back quickly (by design).

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
- **`LOKI_TIMEZONE`**: optional IANA timezone for consistent local-date reasoning.  
  Puerto Rico (no DST): **`America/Puerto_Rico`**

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

### Git: commits, GitHub Desktop, cloning ComfyUI next to this repo
See **[docs/GIT_PRIMER.md](docs/GIT_PRIMER.md)** (uncommitted vs staged vs untracked, `.gitignore`, push/pull, and how to clone another repo **outside** this folder).

---

## Roadmap ideas

- **Multi-monitor vision tools**: `screenshot_monitor(i)` + `monitors()` + region capture.
- **OCR**: reading text on-screen (e.g. chat apps). Options: `tesseract` or macOS Vision framework.
- **Better local embeddings**: add a local embedding model later for higher-quality recall.
- **Safety controls**: “confirm before click”, allowlist apps/regions.
- **Voice**: Web UI TTS tuning; Piper is available locally; optional cloud TTS backends.

