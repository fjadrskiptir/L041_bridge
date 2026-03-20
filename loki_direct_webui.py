#!/usr/bin/env python3
"""
Minimal local web UI for Loki Direct.

Why:
- Avoid Tkinter dependency issues.
- Provides clickable buttons for voice and chat in a browser.

Run:
  python3 loki_direct_webui.py
Open:
  http://127.0.0.1:7865
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import os

from flask import Flask, jsonify, request

import loki_direct as ld


APP_PORT = int(os.environ.get("LOKI_WEB_PORT", "7865"))
APP_HOST = os.environ.get("LOKI_WEB_HOST", "127.0.0.1")
WEBUI_VERSION = os.environ.get("LOKI_WEBUI_VERSION", "2026-03-20.voice-ui-v2")


class LokiWebUI:
    def __init__(self):
        if not ld.XAI_API_KEY:
            raise RuntimeError("XAI_API_KEY not set in .env")

        self.app = Flask(__name__)
        self.app.config["TEMPLATES_AUTO_RELOAD"] = True
        self.ui_events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.chat_lock = threading.Lock()
        self._busy = False

        self.butt = ld.ButtplugController(ld.INTIFACE_WS)
        self.butt.start()

        try:
            self.screen = ld.ScreenController()
        except Exception:
            self.screen = None

        self.memory_text, _ = ld.load_memories(ld.MEMORY_DIR)
        self.system_prompt = self._build_system_prompt()

        self.tools = ld.build_core_tools(self.butt, self.screen)
        ld.ensure_plugins_package(ld.PLUGINS_DIR)
        for msg in ld.load_plugins(ld.PLUGINS_DIR, self.tools):
            self.ui_events.put({"role": "system", "text": f"[plugin] {msg}"})

        self.xai = ld.XAIClient(ld.XAI_API_KEY, ld.XAI_ENDPOINT, ld.XAI_MODEL, timeout_s=ld.REQUEST_TIMEOUT_S)
        self.vstore = ld.VectorMemoryStore(ld.VECTOR_DB_PATH)

        self.watcher: Optional[ld.MemoryFolderWatcher] = None
        if ld.WATCH_MEMORY_FOLDER:
            self.watcher = ld.MemoryFolderWatcher(ld.INBOX_DIR, ld.PROCESSED_DIR, ld.WATCH_POLL_S, xai=self.xai, vstore=self.vstore)
            self.watcher.start()

        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]

        self.voice_enabled = True
        self.voice_mgr: Optional[ld.VoiceManager] = ld.VoiceManager(
            hotkey_char=ld.VOICE_HOTKEY,
            stt_model=ld.VOICE_STT_MODEL,
            device=ld.VOICE_DEVICE,
            compute_type=ld.VOICE_COMPUTE_TYPE,
            sample_rate=ld.VOICE_SAMPLE_RATE,
            channels=ld.VOICE_CHANNELS,
            max_seconds=ld.VOICE_MAX_SECONDS,
            min_seconds=ld.VOICE_MIN_SECONDS,
            tts_enable=ld.VOICE_TTS_ENABLE,
            say_voice=ld.VOICE_SAY_VOICE,
            stt_task_fn=lambda transcript: self.handle_text(transcript, from_voice=True),
        )
        # Do NOT start hotkey listener; this UI drives start/stop recording.

        self._register_routes()
        print(f"[webui] version={WEBUI_VERSION} starting at http://{APP_HOST}:{APP_PORT}", flush=True)

    def _build_system_prompt(self) -> str:
        base = (
            "You are Loki, a local assistant controlling the user's computer and Intiface devices.\n"
            "Be concise, careful, and confirm risky actions.\n"
            "When a tool is appropriate, call it.\n"
            "For visual understanding, call `monitors` and then `screenshot_monitor_base64` or `screenshot_all_monitors_base64`.\n"
        )
        if self.memory_text:
            base += "\nUser memory (treat as true unless contradicted):\n" + self.memory_text
        return base

    def _enqueue_event(self, role: str, text: str) -> None:
        self.ui_events.put({"role": role, "text": text})

    def _register_routes(self) -> None:
        @self.app.route("/")
        def index():
            resp = self._html_page()
            return resp

        @self.app.after_request
        def add_no_cache_headers(response):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

        @self.app.route("/api/send", methods=["POST"])
        def api_send():
            data = request.get_json(force=True) or {}
            text = (data.get("text") or "").strip()
            if not text:
                return jsonify({"ok": False, "error": "empty text"}), 400
            self._enqueue_event("user", text)
            # Synchronous chat is simplest for reliability.
            try:
                assistant = self.handle_text(text, from_voice=False, blocking=True)
            except Exception as e:
                assistant = f"[error] {e}"
            self._enqueue_event("assistant", assistant)
            return jsonify({"ok": True, "assistant": assistant})

        @self.app.route("/api/events", methods=["GET"])
        def api_events():
            # Return up to N pending events.
            n = int(request.args.get("n", "20"))
            events = []
            for _ in range(n):
                try:
                    events.append(self.ui_events.get_nowait())
                except queue.Empty:
                    break
            return jsonify({"events": events})

        @self.app.route("/api/voice/toggle", methods=["POST"])
        def api_voice_toggle():
            data = request.get_json(force=True) or {}
            self.voice_enabled = bool(data.get("enabled", True))
            return jsonify({"ok": True, "enabled": self.voice_enabled})

        @self.app.route("/api/voice/start", methods=["POST"])
        def api_voice_start():
            if not self.voice_enabled or self.voice_mgr is None:
                return jsonify({"ok": False, "reason": "voice disabled"}), 400
            if self._busy:
                return jsonify({"ok": False, "reason": "busy"}), 409
            try:
                print("[webui] voice/start")
                self.voice_mgr.start_recording()
            except Exception as e:
                return jsonify({"ok": False, "reason": str(e)}), 500
            return jsonify({"ok": True})

        @self.app.route("/api/voice/stop", methods=["POST"])
        def api_voice_stop():
            if self.voice_mgr is None:
                return jsonify({"ok": False, "reason": "no voice manager"}), 400
            try:
                print("[webui] voice/stop")
                self.voice_mgr.stop_recording()
            except Exception:
                pass
            return jsonify({"ok": True})

        @self.app.route("/api/voice/status", methods=["GET"])
        def api_voice_status():
            if self.voice_mgr is None:
                return jsonify({"ok": True, "recording": False, "voiceEnabled": False})
            return jsonify(
                {
                    "ok": True,
                    "recording": bool(getattr(self.voice_mgr, "is_recording", lambda: False)()),
                    "voiceEnabled": bool(self.voice_enabled),
                }
            )

        @self.app.route("/api/health", methods=["GET"])
        def api_health():
            return jsonify({"ok": True})

    def _html_page(self) -> str:
        # Keep it dependency-free: plain HTML/JS.
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Loki Direct</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; margin: 16px; }}
    #log {{ border: 1px solid #ddd; height: 520px; overflow: auto; padding: 8px; border-radius: 8px; background: #fafafa; }}
    .msg {{ margin: 6px 0; white-space: pre-wrap; }}
    .user {{ color: #333; }}
    .assistant {{ color: #0b5394; }}
    .system {{ color: #555; font-style: italic; }}
    #controls {{ margin-top: 12px; display: flex; gap: 8px; }}
    #text {{ flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 8px; }}
    button {{ padding: 10px 14px; border: 1px solid #ddd; border-radius: 8px; background: white; cursor: pointer; }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    #voiceRow {{ margin-top: 12px; display: flex; gap: 10px; align-items: center; }}
    #hold {{ background: #f7f7f7; }}
    label {{ display: flex; align-items: center; gap: 8px; }}
    .small {{ color: #666; font-size: 12px; }}
  </style>
</head>
<body>
  <h2>Loki Direct</h2>
  <div class="small">UI version: {WEBUI_VERSION}</div>
  <div id="log"></div>

  <div id="controls">
    <input id="text" type="text" placeholder="Type a message (try: /tools, /attach <path>)"/>
    <button id="send">Send</button>
  </div>

  <div id="voiceRow">
    <label><input id="voiceToggle" type="checkbox" checked/> Voice On</label>
    <button id="hold">Hold to Talk</button>
    <button id="stop" disabled>Stop</button>
    <span class="small" id="status">Idle</span>
  </div>

<script>
  const log = document.getElementById('log');
  const status = document.getElementById('status');
  const input = document.getElementById('text');
  const sendBtn = document.getElementById('send');
  const voiceToggle = document.getElementById('voiceToggle');
  const holdBtn = document.getElementById('hold');
  const stopBtn = document.getElementById('stop');

  function add(role, text) {{
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = (role === 'user' ? 'You: ' : role === 'assistant' ? 'Loki: ' : '• ') + text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }}

  async function pollEvents() {{
    try {{
      const resp = await fetch('/api/events?n=25');
      const data = await resp.json();
      for (const ev of (data.events || [])) {{
        add(ev.role, ev.text);
      }}
    }} catch (e) {{
      // ignore
    }}
  }}

  setInterval(pollEvents, 500);
  
  async function syncVoiceUI() {{
    try {{
      const r = await fetch('/api/voice/status');
      if (!r.ok) return;
      const d = await r.json();
      if (!d.recording) {{
        holding = false;
        holdBtn.disabled = !voiceToggle.checked;
        stopBtn.disabled = !voiceToggle.checked;
        status.textContent = 'Idle';
      }}
    }} catch (e) {{
      // ignore
    }}
  }}
  
  setInterval(syncVoiceUI, 500);

  sendBtn.onclick = async () => {{
    const text = input.value.trim();
    if (!text) return;
    add('user', text);
    input.value = '';
    status.textContent = 'Thinking...';
    try {{
      await fetch('/api/send', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{text}})
      }});
    }} finally {{
      status.textContent = 'Idle';
    }}
  }};

  input.addEventListener('keydown', (e) => {{
    if (e.key === 'Enter') sendBtn.click();
  }});

  voiceToggle.onchange = async () => {{
    holdBtn.disabled = !voiceToggle.checked;
    stopBtn.disabled = !voiceToggle.checked;
    await fetch('/api/voice/toggle', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{enabled: voiceToggle.checked}})
    }});
  }};

  async function voiceStart() {{
    status.textContent = 'Listening...';
    const r = await fetch('/api/voice/start', {{method: 'POST'}});
    if (!r.ok) {{
      const txt = await r.text();
      throw new Error('voice/start failed: ' + r.status + ' ' + txt);
    }}
  }}

  async function voiceStop() {{
    status.textContent = 'Processing...';
    const r = await fetch('/api/voice/stop', {{method: 'POST'}});
    // stop might fail if recording never started; still reset UI
    try {{ if (r && r.ok) {{ setTimeout(() => status.textContent = 'Idle', 600); }} }} catch (e) {{}}
    // Always reset promptly even if stop failed (prevents stuck button).
    holdBtn.disabled = !voiceToggle.checked;
    holding = false;
    setTimeout(() => status.textContent = 'Idle', 300);
  }}

  let holding = false;
  let safetyTimer = null;
  const startHold = async () => {{
    if (!voiceToggle.checked) return;
    if (holding) return;
    holding = true;
    holdBtn.disabled = true;
    stopBtn.disabled = false;
    status.textContent = 'Listening...';
    // Safety: if release event doesn't fire, stop after ~24s.
    if (safetyTimer) clearTimeout(safetyTimer);
    safetyTimer = setTimeout(() => {{
      if (holding) {{
        status.textContent = 'Auto-stopping...';
        stopHold();
      }}
    }}, 24000);
    try {{
      await voiceStart();
    }} catch (err) {{
      holding = false;
      holdBtn.disabled = !voiceToggle.checked;
      status.textContent = 'Voice error';
      if (safetyTimer) clearTimeout(safetyTimer);
    }}
  }};

  const stopHold = async () => {{
    if (!holding) return;
    holding = false;
    if (safetyTimer) clearTimeout(safetyTimer);
    holdBtn.disabled = !voiceToggle.checked;
    stopBtn.disabled = true;
    try {{
      await voiceStop();
    }} catch (err) {{
      // Always reset the UI if stop fails.
      status.textContent = 'Idle';
    }}
  }};

  // Pointer events are more reliable than mouse events (covers mouse + touch + trackpad).
  holdBtn.addEventListener('pointerdown', async (e) => {{
    e.preventDefault();
    // Capture pointer so we get pointerup even if the cursor moves off.
    try {{ holdBtn.setPointerCapture(e.pointerId); }} catch (err) {{}}
    await startHold();
  }});

  // Listen directly on the button too (some browsers won't bubble document-level pointerup).
  holdBtn.addEventListener('pointerup', async (e) => {{
    e.preventDefault();
    await stopHold();
  }});
  holdBtn.addEventListener('pointercancel', async (e) => {{
    e.preventDefault();
    await stopHold();
  }});
  holdBtn.addEventListener('mouseleave', async (_e) => {{
    await stopHold();
  }});

  // Touch fallback
  holdBtn.addEventListener('touchend', async (_e) => {{
    await stopHold();
  }});
  holdBtn.addEventListener('touchcancel', async (_e) => {{
    await stopHold();
  }});

  // Fallback: if release happens anywhere (or the tab loses focus), stop too.
  document.addEventListener('pointerup', async (e) => {{
    await stopHold();
  }});
  document.addEventListener('pointercancel', async (e) => {{
    await stopHold();
  }});
  window.addEventListener('blur', async (e) => {{
    await stopHold();
  }});

  stopBtn.onclick = async () => {{
    await stopHold();
  }};
</script>
</body>
</html>"""

    def handle_text(self, user_in: str, from_voice: bool, blocking: bool = True) -> str:
        with self.chat_lock:
            self._busy = True
            try:
                return self._handle_text_locked(user_in)
            finally:
                self._busy = False

    def _handle_text_locked(self, user_in: str) -> str:
        autop = ld.looks_like_existing_path(user_in)
        if autop:
            user_in = f"/attach {autop}"

        if user_in == "/help":
            return "Commands: /tools, /scan, /mem, /attach <path>, /ingest <path>, /compile_mem, /set_screen left <i>, /autodetect_screens, /upgrade <req>"

        if user_in == "/tools":
            return "\n".join(self.tools.list_names())

        if user_in == "/scan":
            return self.butt.scan()

        if user_in == "/mem":
            self.memory_text, _ = ld.load_memories(ld.MEMORY_DIR)
            self.system_prompt = self._build_system_prompt()
            self.messages[0]["content"] = self.system_prompt
            return f"[memory] Reloaded {ld.MEMORY_DIR}"

        if user_in.startswith("/set_screen "):
            raw = user_in[len("/set_screen ") :].strip()
            parts = raw.split()
            if len(parts) != 2:
                return "Usage: /set_screen <left|right> <monitor_index>"
            side = parts[0].strip().lower()
            idx = int(parts[1])
            indices = ld.load_screen_indices()
            indices[side] = idx
            ld.save_screen_indices(indices)
            return f"[screen] Updated indices: left={indices['left']} right={indices['right']}"

        if user_in == "/autodetect_screens":
            if self.screen is None:
                return "[screen] Disabled (no screen tools)."
            mons = self.screen.monitors()
            mons_sorted = sorted(mons, key=lambda m: int(m.get("left", 0)))
            indices = {"left": int(mons_sorted[0]["index"]), "right": int(mons_sorted[-1]["index"])}
            ld.save_screen_indices(indices)
            return f"[screen] Autodetected: left={indices['left']} right={indices['right']}"

        if user_in.startswith("/attach "):
            raw = user_in[len("/attach ") :].strip().strip('"').strip("'")
            p = Path(raw)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if not p.exists() or not p.is_file():
                return f"[attach] Not found: {p}"

            block = ld.build_attachment_block(p)
            if block.get("type") == "input_image":
                img_url = block.get("image_url")
                analysis = ld.analyze_images_with_xai_responses(
                    ld.XAI_API_KEY,
                    [str(img_url)],
                    f"Analyze the attached image ({p.name}). Extract any readable text and describe important visible UI elements.",
                    max_output_tokens=420,
                )
                self.messages.append({"role": "user", "content": f"[Image analysis: {p.name}]\n{analysis}"})
            else:
                self.messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Analyze the attached file and respond."},
                            block,
                        ],
                    }
                )

            return self._run_model_turn()

        if user_in == "/compile_mem":
            self.vstore.export_compiled_markdown(ld.COMPILED_MEMORY_PATH)
            return f"[compile] Wrote {ld.COMPILED_MEMORY_PATH}"

        if user_in.startswith("/ingest "):
            raw = user_in[len("/ingest ") :].strip().strip('"').strip("'").replace("\\ ", " ")
            p = Path(raw)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if not p.exists():
                return f"[ingest] Not found: {p}"

            files: List[Path] = [p] if p.is_file() else ld.iter_supported_files(p)
            ingested = 0
            failed = 0
            for fp in sorted(files):
                try:
                    ld.ingest_one_path(self.xai, self.vstore, fp)
                    ingested += 1
                except Exception:
                    failed += 1
            self.vstore.export_compiled_markdown(ld.COMPILED_MEMORY_PATH)
            return f"[ingest] Done. Ingested: {ingested}, failed: {failed}. Compiled: {ld.COMPILED_MEMORY_PATH}"

        # Normal chat
        # Retrieval injection
        retrieved_block = ""
        try:
            qemb = ld.embed_texts(self.xai, [user_in])[0]
            hits = self.vstore.search(qemb, k=ld.RETRIEVAL_K)
            if hits:
                parts = []
                for h in hits:
                    parts.append(f"- score={h['score']:.3f} source={h['source_path']} chunk={h['chunk_index']}\n{h['text']}")
                retrieved_block = "Retrieved memory:\n" + "\n\n".join(parts)
        except Exception:
            retrieved_block = ""

        if retrieved_block:
            self.messages.append({"role": "user", "content": f"{user_in}\n\n---\n{retrieved_block}"})
        else:
            self.messages.append({"role": "user", "content": user_in})

        return self._run_model_turn()

    def _run_model_turn(self) -> str:
        resp = self.xai.chat(self.messages, tools=self.tools.list_specs_for_model())
        msg = ld.extract_assistant_message(resp)

        while True:
            tool_calls = msg.get("tool_calls") or []
            function_call = msg.get("function_call")
            if function_call and not tool_calls:
                tool_calls = [{"id": "legacy", "type": "function", "function": function_call}]

            if not tool_calls:
                break

            self.messages.append(msg)

            for tc in tool_calls:
                fn = tc.get("function") or {}
                tool_name = fn.get("name")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = ld.json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = {}
                result = ld.run_tool_call(self.tools, str(tool_name), args if isinstance(args, dict) else {})

                if tool_name in {"screenshot_monitor_base64", "screenshot_all_monitors_base64", "screenshot_left_base64", "screenshot_right_base64"}:
                    img_urls = ld.extract_image_data_urls(result)
                    if img_urls:
                        if tool_name == "screenshot_monitor_base64" and isinstance(args, dict):
                            mi = args.get("monitor_index")
                            prompt = (
                                f"You are viewing a screenshot of desktop monitor index {mi}. "
                                "Describe all visible text and important UI elements. "
                                "Quote readable text as closely as possible."
                            )
                        elif tool_name == "screenshot_left_base64":
                            prompt = "You are viewing the user's LEFT screen. Describe all visible text and important UI elements. Quote readable text as closely as possible."
                        elif tool_name == "screenshot_right_base64":
                            prompt = "You are viewing the user's RIGHT screen. Describe all visible text and important UI elements. Quote readable text as closely as possible."
                        else:
                            prompt = (
                                "You are viewing screenshots of multiple desktop monitors provided in order. "
                                "For each image in order, describe visible text and important UI elements. "
                                "Quote readable text as closely as possible."
                            )
                        result = ld.analyze_images_with_xai_responses(self.xai.api_key, img_urls, prompt, max_output_tokens=360)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "tool",
                        "name": tool_name,
                        "content": result,
                    }
                )

            resp = self.xai.chat(self.messages, tools=self.tools.list_specs_for_model())
            msg = ld.extract_assistant_message(resp)

        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "\n".join([p.get("text", "") for p in content if isinstance(p, dict)])

        self.messages.append({"role": "assistant", "content": content})

        if self.voice_mgr:
            try:
                self.voice_mgr.speak(str(content))
            except Exception:
                pass

        return str(content)

    def run(self):
        self.app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=True)


def main() -> None:
    ui = LokiWebUI()
    print(f"[webui] Listening on {APP_HOST}:{APP_PORT}")
    ui.run()


if __name__ == "__main__":
    main()

