# Loki Direct (Local Grok Companion)

Run a local “Grok companion” that can:
- **Chat via xAI** (Grok) with tool-calling.
- **Control your desktop** (mouse/keyboard + screenshots) via `pyautogui`.
- **Control Intiface / Buttplug toys** (e.g., Lovense Nora) via `ws://127.0.0.1:12345`.
- **Ingest text/images/PDFs into persistent memory** (SQLite vector store).
- **Dropbox-style memory inbox**: drop files into `memories/inbox/` and Loki auto-processes + recalls them later.
- **Self-upgrade via plugins**: ask “add X” and Loki can generate a plugin file in `loki_plugins/`.

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

There are two “memory” systems:

- **Snapshot memory (`/mem`)**: loads text files from `memories/` and injects them into the system prompt.
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
**Feasible and easy.** Good options:
- **macOS built-in `say`** (zero deps, good “start now”)
- **Piper** (local neural TTS, higher quality, offline)
- Cloud TTS (highest quality, less “fully local”)

Recommendation: start with **`say`** (quick plugin), then upgrade to **Piper** if you want high-quality offline voices.

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
- **Voice**: start with `say`, add whisper.cpp for STT, then build a proper “voice mode”.

