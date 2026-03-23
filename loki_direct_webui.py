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
import re
import subprocess
import sys

from flask import Flask, jsonify, request

import loki_direct as ld
from loki_telegram import maybe_start_telegram, print_telegram_startup_hint, telegram_status_dict


_port_raw = os.environ.get("LOKI_WEB_PORT", "7865")
_port_raw = str(_port_raw).strip()
_port_match = re.search(r"([0-9]+)", _port_raw)
APP_PORT = int(_port_match.group(1)) if _port_match else 7865
APP_HOST = os.environ.get("LOKI_WEB_HOST", "127.0.0.1")
WEBUI_VERSION = os.environ.get("LOKI_WEBUI_VERSION", "2026-03-18.webcam")

# JSON keys accepted for POST /api/tts/settings and POST /api/tts/test (apply before preview).
_TTS_REQUEST_KEYS = (
    "say_voice",
    "say_rate_wpm",
    "tts_enable",
    "tts_engine",
    "piper_voice",
    "piper_data_dir",
    "piper_binary",
    "piper_length_scale",
    "piper_speaker_id",
    "piper_noise_scale",
    "piper_noise_w_scale",
    "piper_volume",
    "piper_sentence_silence",
    "piper_playback_rate",
)


class LokiWebUI:
    def __init__(self):
        if not ld.XAI_API_KEY:
            raise RuntimeError("XAI_API_KEY not set in .env")

        self.app = Flask(__name__)
        self.app.config["TEMPLATES_AUTO_RELOAD"] = True
        self.ui_events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.chat_lock = threading.Lock()
        self._busy = False
        self._presence_lock = threading.Lock()
        self._presence_state = "idle"
        self._presence_since = time.time()

        self.butt = ld.ButtplugController(ld.INTIFACE_WS)
        self.butt.start()

        try:
            self.screen = ld.ScreenController()
        except Exception:
            self.screen = None

        ld.ensure_persona_template()
        self.memory_text, _ = ld.load_memories(ld.MEMORY_DIR)

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

        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": ld.compose_system_with_time(ld.build_base_system_static(self.memory_text))}
        ]

        self.voice_enabled = True
        _tts0 = ld.load_tts_settings_merged()
        try:
            ld.LOKI_PIPER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.voice_mgr: Optional[ld.VoiceManager] = ld.VoiceManager(
            hotkey_char=ld.VOICE_HOTKEY,
            stt_model=ld.VOICE_STT_MODEL,
            device=ld.VOICE_DEVICE,
            compute_type=ld.VOICE_COMPUTE_TYPE,
            sample_rate=ld.VOICE_SAMPLE_RATE,
            channels=ld.VOICE_CHANNELS,
            max_seconds=ld.VOICE_MAX_SECONDS,
            min_seconds=ld.VOICE_MIN_SECONDS,
            tts_enable=bool(_tts0["tts_enable"]),
            say_voice=str(_tts0["say_voice"]),
            say_rate_wpm=_tts0["say_rate_wpm"],
            tts_engine=str(_tts0["tts_engine"]),
            piper_voice=str(_tts0["piper_voice"]),
            piper_onnx=_tts0["piper_onnx"],
            piper_voice_module=str(_tts0["piper_voice_module"]),
            piper_data_dir=_tts0["piper_data_dir"],
            piper_binary=str(_tts0["piper_binary"]),
            piper_length_scale=float(_tts0["piper_length_scale"]),
            piper_speaker_id=_tts0["piper_speaker_id"],
            piper_noise_scale=float(_tts0["piper_noise_scale"]),
            piper_noise_w_scale=float(_tts0["piper_noise_w_scale"]),
            piper_volume=float(_tts0["piper_volume"]),
            piper_sentence_silence=float(_tts0["piper_sentence_silence"]),
            piper_playback_rate=float(_tts0["piper_playback_rate"]),
            stt_task_fn=self._on_voice_transcript,
        )
        # Do NOT start hotkey listener; this UI drives start/stop recording.

        def _persona_session_refresh_web() -> None:
            with self.chat_lock:
                self.memory_text, _ = ld.load_memories(ld.MEMORY_DIR)
                ld.refresh_system_time_message(self.messages, ld.build_base_system_static(self.memory_text))

        ld.set_persona_session_refresh_hook(_persona_session_refresh_web)

        self._register_routes()
        print_telegram_startup_hint()
        try:
            maybe_start_telegram(self)
        except Exception as e:
            print(f"[telegram] start failed: {e}", flush=True)
        print(
            f"[webui] version={WEBUI_VERSION} starting at http://{APP_HOST}:{APP_PORT} "
            f"(Brave Leo OpenAI bridge: http://{APP_HOST}:{APP_PORT}/v1)",
            flush=True,
        )

    def _enqueue_event(self, role: str, text: str) -> None:
        self.ui_events.put({"role": role, "text": text})

    def _set_presence(self, state: str) -> None:
        s = (state or "").strip().lower() or "idle"
        if s not in {"idle", "listening", "thinking", "speaking"}:
            s = "idle"
        with self._presence_lock:
            if self._presence_state == s:
                return
            self._presence_state = s
            self._presence_since = time.time()

    def _presence_snapshot(self) -> Dict[str, Any]:
        with self._presence_lock:
            state = self._presence_state
            since = float(self._presence_since)
        return {"state": state, "since_epoch_s": since, "age_s": max(0.0, time.time() - since)}

    def _on_voice_transcript(self, transcript: str) -> None:
        """STT callback: push chat lines to the event queue (voice has no /api/send client echo)."""

        t = (transcript or "").strip()
        if not t:
            return
        self._enqueue_event("user", t)
        try:
            assistant = self.handle_text(t, from_voice=True, blocking=True)
        except Exception as e:
            assistant = f"[error] {e}"
        if ld.CROSS_CHAT_APPEND_HOME:
            ld.append_cross_chat_log("loki_direct_webui_voice", t, assistant)
        self._enqueue_event("assistant", assistant)

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
            image = data.get("image")
            if isinstance(image, str):
                image = image.strip() or None
            else:
                image = None
            if not text and not image:
                return jsonify({"ok": False, "error": "need message text and/or a webcam image"}), 400
            # User line is shown immediately in the browser; do not also enqueue it (poll would duplicate).
            # Synchronous chat is simplest for reliability.
            try:
                if image:
                    assistant = self.handle_webcam_send(text, image)
                else:
                    assistant = self.handle_text(text, from_voice=False, blocking=True)
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            except Exception as e:
                assistant = f"[error] {e}"
            if ld.CROSS_CHAT_APPEND_HOME:
                log_user = f"{text} 📷" if (text and image) else (text or "📷 [webcam]")
                ld.append_cross_chat_log("loki_direct_webui", log_user, assistant)
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
            if not self.voice_enabled and self.voice_mgr is not None:
                try:
                    self.voice_mgr.stop_recording()
                except Exception:
                    pass
            return jsonify({"ok": True, "enabled": self.voice_enabled})

        @self.app.route("/api/voice/start", methods=["POST"])
        def api_voice_start():
            if not self.voice_enabled or self.voice_mgr is None:
                return jsonify({"ok": False, "reason": "voice disabled"}), 400
            if self._busy:
                return jsonify({"ok": False, "reason": "busy"}), 409
            try:
                print("[webui] voice/start", flush=True)
                self.voice_mgr.start_recording()
                self._set_presence("listening")
            except Exception as e:
                return jsonify({"ok": False, "reason": str(e)}), 500
            return jsonify({"ok": True})

        @self.app.route("/api/voice/stop", methods=["POST"])
        def api_voice_stop():
            if self.voice_mgr is None:
                return jsonify({"ok": False, "reason": "no voice manager"}), 400
            try:
                print("[webui] voice/stop", flush=True)
                self.voice_mgr.stop_recording()
                self._set_presence("thinking")
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

        @self.app.route("/api/presence", methods=["GET"])
        def api_presence():
            snap = self._presence_snapshot()
            recording = False
            try:
                if self.voice_mgr is not None:
                    recording = bool(getattr(self.voice_mgr, "is_recording", lambda: False)())
            except Exception:
                recording = False
            if recording:
                snap["state"] = "listening"
            snap["busy"] = bool(self._busy)
            snap["recording"] = bool(recording)
            return jsonify({"ok": True, **snap})

        @self.app.route("/api/telegram/status", methods=["GET"])
        def api_telegram_status():
            """Why Telegram might be silent: env not loaded, missing token, etc. No secrets exposed."""
            try:
                return jsonify({"ok": True, **telegram_status_dict()})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500

        @self.app.route("/v1/models", methods=["GET"])
        def openai_v1_models():
            import loki_openai_bridge as bridge

            err = bridge.verify_bridge_auth(request.headers)
            if err:
                return jsonify({"error": {"message": err, "type": "authentication_error"}}), 401
            return jsonify(bridge.openai_models_payload()), 200

        @self.app.route("/v1/chat/completions", methods=["POST"])
        def openai_v1_chat_completions():
            import loki_openai_bridge as bridge

            err = bridge.verify_bridge_auth(request.headers)
            if err:
                return jsonify({"error": {"message": err, "type": "authentication_error"}}), 401
            body = request.get_json(force=True) or {}
            payload, code = bridge.openai_chat_completions(body, self.xai)
            print("[webui] POST /v1/chat/completions (Brave Leo bridge)", flush=True)
            return jsonify(payload), code

        @self.app.route("/api/persona", methods=["GET"])
        def api_persona_get():
            ld.ensure_persona_template()
            return jsonify(
                {
                    "ok": True,
                    "path": str(ld.PERSONA_INSTRUCTIONS_PATH),
                    "max_chars": ld.PERSONA_INSTRUCTIONS_MAX_CHARS,
                    "content": ld.load_persona_instructions(),
                }
            )

        @self.app.route("/api/persona", methods=["POST"])
        def api_persona_post():
            data = request.get_json(force=True) or {}
            content = data.get("content")
            if not isinstance(content, str):
                return jsonify({"ok": False, "error": "content must be a string"}), 400
            try:
                ld.save_persona_instructions(content)
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            except OSError as e:
                return jsonify({"ok": False, "error": str(e)}), 500
            with self.chat_lock:
                self.memory_text, _ = ld.load_memories(ld.MEMORY_DIR)
                ld.refresh_system_time_message(self.messages, ld.build_base_system_static(self.memory_text))
            print("[webui] POST /api/persona saved + refreshed system prompt", flush=True)
            return jsonify({"ok": True, "path": str(ld.PERSONA_INSTRUCTIONS_PATH), "len": len(content)})

        @self.app.route("/api/persona/reveal", methods=["POST"])
        def api_persona_reveal():
            if sys.platform != "darwin":
                return jsonify({"ok": False, "error": "Reveal in Finder is only available on macOS"}), 400
            ld.ensure_persona_template()
            p = ld.PERSONA_INSTRUCTIONS_PATH
            if not p.is_file():
                return jsonify({"ok": False, "error": "Persona file not found"}), 400
            try:
                subprocess.Popen(["open", "-R", str(p)])
            except OSError as e:
                return jsonify({"ok": False, "error": str(e)}), 500
            return jsonify({"ok": True})

        @self.app.route("/api/tts/voices", methods=["GET"])
        def api_tts_voices():
            voices = ld.list_macos_say_voices()
            return jsonify({"ok": True, "voices": voices, "platform": sys.platform})

        @self.app.route("/api/tts/piper_onnx_models", methods=["GET"])
        def api_tts_piper_onnx_models():
            import loki_piper_tts as lpt

            d = (request.args.get("dir") or "").strip()
            if d:
                root = Path(d).expanduser().resolve()
            elif ld.LOKI_PIPER_MODEL_DIR is not None:
                root = ld.LOKI_PIPER_MODEL_DIR
            else:
                root = ld.MEMORY_DIR
            return jsonify({"ok": True, "directory": str(root), "models": lpt.list_onnx_in_dir(root)})

        @self.app.route("/api/tts/piper_installed_voices", methods=["GET"])
        def api_tts_piper_installed_voices():
            """`.onnx` files in the Piper data dir (from `piper.download_voices`)."""

            import loki_piper_tts as lpt

            d = (request.args.get("data_dir") or "").strip()
            if d:
                root = Path(d).expanduser().resolve()
            else:
                root = ld.LOKI_PIPER_DATA_DIR
            voices = lpt.list_installed_piper_voices(root)
            return jsonify(
                {
                    "ok": True,
                    "data_dir": str(root),
                    "exists": root.is_dir(),
                    "voices": voices,
                }
            )

        @self.app.route("/api/tts/settings", methods=["GET"])
        def api_tts_settings_get():
            if self.voice_mgr is None:
                return jsonify({"ok": False, "error": "no voice manager"}), 400
            # Always sync from disk so the browser / VoiceManager can't stay on a stale voice
            # after saves, manual JSON edits, or another session writing tts_settings.json.
            merged = ld.load_tts_settings_merged()
            self.voice_mgr.hydrate_tts_from_merged(merged)
            snap = self.voice_mgr.tts_settings_snapshot()
            return jsonify({"ok": True, **snap, "settings_path": str(ld.TTS_SETTINGS_PATH)})

        @self.app.route("/api/tts/settings", methods=["POST"])
        def api_tts_settings_post():
            if self.voice_mgr is None:
                return jsonify({"ok": False, "error": "no voice manager"}), 400
            data = request.get_json(force=True) or {}
            if not any(k in data for k in _TTS_REQUEST_KEYS):
                merged = ld.load_tts_settings_merged()
                self.voice_mgr.hydrate_tts_from_merged(merged)
                snap = self.voice_mgr.tts_settings_snapshot()
                return jsonify({"ok": True, **snap, "settings_path": str(ld.TTS_SETTINGS_PATH)})
            snap = self.voice_mgr.apply_tts_request_fields(data)
            try:
                ld.save_tts_settings_file(snap)
            except Exception as e:
                return jsonify({"ok": False, "error": f"save failed: {e}", "applied": snap}), 500
            print(
                "[webui] POST /api/tts/settings "
                f"tts_engine={snap.get('tts_engine')!r} piper_voice={snap.get('piper_voice')!r} "
                f"data_dir={snap.get('piper_data_dir')!r}",
                flush=True,
            )
            return jsonify({"ok": True, **snap, "settings_path": str(ld.TTS_SETTINGS_PATH)})

        @self.app.route("/api/tts/test", methods=["POST"])
        def api_tts_test():
            if self.voice_mgr is None:
                return jsonify({"ok": False, "error": "no voice manager"}), 400
            data = request.get_json(force=True) or {}
            phrase = (data.get("text") or "Hello — I'm Loki. This is how I sound with your current voice settings.").strip()
            # Apply the same fields the UI shows, then preview in this request (avoids race with a
            # separate /settings POST and guarantees Test uses Piper when selected).
            tts_subset = {k: data[k] for k in _TTS_REQUEST_KEYS if k in data}
            if tts_subset:
                snap = self.voice_mgr.apply_tts_request_fields(tts_subset)
                try:
                    ld.save_tts_settings_file(snap)
                except Exception as e:
                    return jsonify({"ok": False, "error": f"save failed: {e}", "applied": snap}), 500
            else:
                merged = ld.load_tts_settings_merged()
                self.voice_mgr.hydrate_tts_from_merged(merged)
            try:
                self.voice_mgr.speak_preview(phrase)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500
            out = self.voice_mgr.tts_settings_snapshot()
            print(
                "[webui] POST /api/tts/test "
                f"tts_engine={out.get('tts_engine')!r} piper_voice={out.get('piper_voice')!r} "
                f"data_dir={out.get('piper_data_dir')!r}",
                flush=True,
            )
            return jsonify({"ok": True, **out, "settings_path": str(ld.TTS_SETTINGS_PATH)})

    def _html_page(self) -> str:
        # Keep it dependency-free: plain HTML/JS.
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>L041</title>
  <style>
    :root {{ --stealth-blur: 0px; --stealth-dim: 1; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; margin: 16px; background:#0f1115; color:#f3f5f7; }}
    #log {{ border: 1px solid #2b303b; height: 520px; overflow: auto; padding: 8px; border-radius: 8px; background: #171b22; filter: blur(var(--stealth-blur)); transition: filter .15s ease; }}
    .msg {{ margin: 6px 0; white-space: pre-wrap; }}
    .user {{ color: #f3f5f7; }}
    .assistant {{ color: #9ecbff; }}
    .system {{ color: #b8c0cc; font-style: italic; }}
    #controls {{ margin-top: 12px; display: flex; gap: 8px; }}
    #text {{ flex: 1; padding: 10px; border: 1px solid #2b303b; border-radius: 8px; background:#11161d; color:#f3f5f7; }}
    button {{ padding: 10px 14px; border: 1px solid #2b303b; border-radius: 8px; background: #1a2029; color:#f3f5f7; cursor: pointer; }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    #voiceRow {{ margin-top: 12px; display: flex; gap: 10px; align-items: center; }}
    #hold {{ background: #1f2530; }}
    label {{ display: flex; align-items: center; gap: 8px; }}
    .small {{ color: #aeb6c2; font-size: 12px; }}
    #ttsPanel {{ margin-top: 14px; border: 1px solid #2b303b; border-radius: 10px; padding: 10px 12px; background: #131923; opacity: var(--stealth-dim); transition: opacity .15s ease; }}
    #ttsPanel summary {{ cursor: pointer; font-weight: 600; }}
    .tts-row {{ margin: 10px 0; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }}
    .tts-row label {{ flex: 1; min-width: 200px; }}
    #ttsVoice {{ flex: 2; min-width: 220px; padding: 8px; border-radius: 8px; border: 1px solid #2b303b; background:#11161d; color:#f3f5f7; }}
    #ttsRate {{ flex: 1; min-width: 160px; }}
    .tts-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    #piperVoiceGrid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; margin: 10px 0; max-height: 240px; overflow-y: auto; padding: 4px; }}
    .piper-voice-card {{ display: block; width: 100%; text-align: left; padding: 12px 14px; border: 2px solid #2b303b; border-radius: 12px; background: #11161d; cursor: pointer; font: inherit; color:#f3f5f7; transition: border-color .15s, background .15s; }}
    .piper-voice-card:hover {{ border-color: #5c7a97; background: #161d27; }}
    .piper-voice-card--on {{ border-color: #66a9e4; background: #17324a; box-shadow: 0 0 0 1px #66a9e4; }}
    .pvc-title {{ font-weight: 600; font-size: 14px; word-break: break-word; line-height: 1.3; color:#f3f5f7; }}
    .pvc-sub {{ font-size: 11px; color: #aeb6c2; margin-top: 6px; }}
    .piper-subhead {{ font-size: 14px; font-weight: 600; margin: 16px 0 8px 0; color: #dce2ea; }}
    .piper-slider-row {{ margin: 12px 0; }}
    .piper-slider-row label {{ display: block; font-size: 13px; margin-bottom: 4px; }}
    .piper-slider-row input[type=range] {{ width: 100%; max-width: 420px; vertical-align: middle; }}
    .piper-slider-val {{ display: inline-block; min-width: 48px; margin-left: 8px; font-size: 12px; color: #8cc6ff; font-weight: 600; }}
    .piper-advanced summary {{ cursor: pointer; color: #c9d3df; margin-top: 12px; }}
    #piperDownloadHelp summary {{ cursor: pointer; color: #c9d3df; }}
    #piperUseCustomBtn {{ font-size: 13px; padding: 6px 12px; }}
    #webcamRow {{ margin-top: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start; }}
    #webcamPreviewWrap {{ border: 1px solid #ddd; border-radius: 8px; overflow: hidden; background: #111; min-height: 120px; display: none; }}
    #webcamPreviewWrap.on {{ display: block; }}
    #webcamVideo {{ display: block; max-width: 320px; max-height: 240px; width: auto; height: auto; }}
    #webcamHint {{ flex: 1; min-width: 200px; margin: 0; }}
    #personaPanel {{ opacity: var(--stealth-dim); transition: opacity .15s ease; }}
    code {{ color:#c3e1ff; }}
    a {{ color:#8fc8ff; }}
  </style>
</head>
<body>
  <h2>L041</h2>
  <div class="small">UI version: {WEBUI_VERSION}</div>
  <div id="log"></div>

  <div id="controls">
    <input id="text" type="text" placeholder="Type a message (try: /tools, /attach <path>)"/>
    <button id="send">Send</button>
    <button id="stealthToggle" type="button" title="Hide sensitive text quickly">Stealth Off</button>
  </div>

  <div id="webcamRow">
    <div>
      <button type="button" id="webcamStart">Camera on</button>
      <button type="button" id="webcamStop" disabled>Camera off</button>
      <button type="button" id="webcamSend" disabled>Send with camera</button>
    </div>
    <div id="webcamPreviewWrap">
      <video id="webcamVideo" playsinline muted autoplay></video>
    </div>
    <p class="small" id="webcamHint">Uses your browser camera (HTTPS or localhost). Turn the camera on, then send a frame with your question. Nothing streams continuously—only the frame you send goes to the server.</p>
  </div>

  <div id="voiceRow">
    <label><input id="voiceToggle" type="checkbox" checked/> Voice On</label>
    <button id="hold">Hold to Talk</button>
    <button id="stop" disabled>Stop</button>
    <span class="small" id="status">Idle</span>
  </div>

  <details id="personaPanel" style="margin-top:12px;border:1px solid #2b303b;border-radius:10px;padding:10px 12px;background:#131923">
    <summary style="cursor:pointer;font-weight:600">Personality &amp; instructions (how L041 writes &amp; behaves)</summary>
    <p class="small" style="margin:8px 0 6px 0;line-height:1.45">
      One canonical markdown file lives under your memory folder. It is injected into the <b>system prompt</b> every reply (not vector search). Edit here or in any editor; use <b>Save &amp; apply</b> or chat command <code>/mem</code> after external edits.
    </p>
    <p class="small" style="margin:0 0 8px 0;word-break:break-all"><code id="personaPathEl">…</code></p>
    <textarea id="personaText" rows="14" style="width:100%;box-sizing:border-box;padding:10px;border:1px solid #2b303b;border-radius:8px;background:#11161d;color:#f3f5f7;font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px;line-height:1.4" spellcheck="false" placeholder="Loading…"></textarea>
    <div class="tts-actions" style="margin-top:10px">
      <button type="button" id="personaSave">Save &amp; apply to chat</button>
      <button type="button" id="personaReload">Reload from disk</button>
      <button type="button" id="personaReveal">Reveal in Finder</button>
    </div>
    <p class="small" id="personaHint" style="margin-top:8px;color:#555"></p>
  </details>

  <details id="ttsPanel">
    <summary>Voice &amp; speech (how L041 sounds)</summary>
    <p class="small" style="margin:8px 0 0 0">Choose <b>macOS say</b> or local neural <b>Piper</b> (<code>pip install piper-tts</code>). Settings save to <code>memories/tts_settings.json</code>.</p>
    <div class="tts-row">
      <label><input type="checkbox" id="ttsSpeakReplies" checked/> Speak replies (audio when Loki answers)</label>
    </div>
    <div class="tts-row">
      <label style="flex:2">TTS engine<br/>
        <select id="ttsEngine">
          <option value="piper">Piper (neural) — recommended</option>
          <option value="say">macOS say</option>
        </select>
      </label>
    </div>
    <div id="sayBlock">
      <div class="tts-row">
        <label style="flex:2">macOS voice<br/>
          <select id="ttsVoice"><option value="">System default</option></select>
        </label>
      </div>
      <div class="tts-row">
        <label style="flex:2">
          <input type="checkbox" id="ttsRateDefault"/> Use Mac default speed (leave unchecked to set WPM)
        </label>
      </div>
      <div class="tts-row">
        <label style="flex:2">Speaking rate (words per minute)<br/>
          <input type="range" id="ttsRate" min="100" max="260" step="5" value="175"/>
          <span class="small" id="ttsRateVal">175</span>
        </label>
      </div>
    </div>
    <div id="piperBlock" style="display:none">
      <p class="small" style="margin:0 0 8px 0;line-height:1.45"><b>1. Choose a voice</b> — tap a card below (your choice saves automatically). <b>2. Tune sound</b> — use the sliders; they save a moment after you release.</p>
      <div class="tts-row" style="align-items:flex-end;flex-wrap:wrap">
        <button type="button" id="ttsPiperRefreshVoices">Refresh voice list</button>
        <span class="small" id="ttsPiperScanHint"></span>
      </div>
      <div id="piperVoiceGrid" role="radiogroup" aria-label="Installed Piper voices"></div>
      <div class="tts-row">
        <button type="button" id="piperUseCustomBtn">Use a custom voice ID or path instead…</button>
      </div>
      <div class="tts-row">
        <input type="text" id="ttsPiperVoice" style="width:100%;padding:8px;border-radius:8px;border:1px solid #ddd" placeholder="Voice id (e.g. en_US-lessac-medium) or full path to .onnx — edit when not using a card above"/>
      </div>

      <div class="piper-subhead">Sound (Piper neural voice)</div>
      <p class="small" style="margin:-4px 0 8px 0">These map to Piper’s <code>--noise-scale</code> / <code>--noise-w-scale</code> (more subtle than <b>Pace</b>; compare slider min vs max). Playback speed uses macOS <code>afplay</code> after the voice is generated.</p>
      <div class="piper-slider-row">
        <label>Pace <span class="small" style="font-weight:normal;color:#666">(speaking rate — lower = faster)</span></label>
        <input type="range" id="ttsPiperPace" min="0.55" max="1.45" step="0.05" value="1"/>
        <span class="piper-slider-val" id="ttsPiperPaceVal">1</span>
      </div>
      <div class="piper-slider-row">
        <label>Expression <span class="small" style="font-weight:normal;color:#666">(voice variation / warmth)</span></label>
        <input type="range" id="ttsPiperExpression" min="0.18" max="1.2" step="0.02" value="0.667"/>
        <span class="piper-slider-val" id="ttsPiperExpressionVal">0.667</span>
      </div>
      <div class="piper-slider-row">
        <label>Clarity <span class="small" style="font-weight:normal;color:#666">(phoneme definition)</span></label>
        <input type="range" id="ttsPiperClarity" min="0.3" max="1.4" step="0.02" value="0.8"/>
        <span class="piper-slider-val" id="ttsPiperClarityVal">0.8</span>
      </div>
      <div class="piper-slider-row">
        <label>Volume <span class="small" style="font-weight:normal;color:#666">(Piper output level)</span></label>
        <input type="range" id="ttsPiperVol" min="0.4" max="1.5" step="0.05" value="1"/>
        <span class="piper-slider-val" id="ttsPiperVolVal">1</span>
      </div>
      <div class="piper-slider-row">
        <label>Pause between sentences <span class="small" style="font-weight:normal;color:#666">(seconds)</span></label>
        <input type="range" id="ttsPiperPause" min="0" max="0.8" step="0.05" value="0"/>
        <span class="piper-slider-val" id="ttsPiperPauseVal">0</span>
      </div>
      <div class="piper-slider-row">
        <label>Playback speed <span class="small" style="font-weight:normal;color:#666">(how fast audio plays — macOS only)</span></label>
        <input type="range" id="ttsPiperPlaySpeed" min="0.75" max="1.25" step="0.05" value="1"/>
        <span class="piper-slider-val" id="ttsPiperPlaySpeedVal">1</span>
      </div>
      <div class="tts-row">
        <button type="button" id="ttsPiperResetSound">Reset sound sliders to defaults</button>
      </div>

      <details class="piper-advanced">
        <summary>Advanced — voice folder, multi-speaker, downloads</summary>
        <div class="tts-row" style="margin-top:10px">
          <label style="flex:2">Voice folder <span class="small">(where <code>.onnx</code> files are)</span><br/>
            <input type="text" id="ttsPiperDataDir" style="width:100%;padding:8px;border-radius:8px;border:1px solid #ddd" placeholder="leave empty for memories/piper_voices"/>
          </label>
        </div>
        <div class="tts-row">
          <label style="flex:2">Legacy Piper CLI <span class="small">(only if you use a raw <code>.onnx</code> path)</span><br/>
            <input type="text" id="ttsPiperBinary" style="width:100%;padding:8px;border-radius:8px;border:1px solid #ddd" placeholder="piper"/>
          </label>
        </div>
        <div class="tts-row">
          <label>Speaker number <span class="small">(multi-speaker models only; leave empty normally)</span><br/>
            <input type="number" id="ttsPiperSpeaker" step="1" style="width:120px;padding:8px" placeholder="default"/>
          </label>
        </div>
        <details id="piperDownloadHelp" style="margin-top:10px">
          <summary class="small">How to download more Piper voices</summary>
          <ol class="small" style="margin:8px 0 0 18px;line-height:1.5">
            <li>In your project venv: <code>pip install piper-tts</code> (+ <code>pathvalidate</code> if needed)</li>
            <li><b>List</b> voice ids: <code>./venv/bin/python -m piper.download_voices</code></li>
            <li><b>Download</b> into your folder:<br/>
              <code>./venv/bin/python -m piper.download_voices --data-dir <span id="piperHelpDataDirPh">memories/piper_voices</span> en_US-lessac-medium</code></li>
            <li>Click <b>Refresh voice list</b>, then pick a card.</li>
          </ol>
          <p class="small" style="margin:8px 0 0 0">Samples: <a href="https://rhasspy.github.io/piper-samples" target="_blank" rel="noopener">rhasspy.github.io/piper-samples</a></p>
        </details>
      </details>
    </div>
    <div class="tts-actions">
      <button type="button" id="ttsSave">Save voice settings</button>
      <button type="button" id="ttsTest">Test voice</button>
    </div>
    <p class="small" id="ttsHint"></p>
    <p class="small" style="margin-top:6px;color:#555">If you start Loki with <b>Start_Loki_GUI.command</b>, keep that Terminal window open — it tails <code>/tmp/loki_direct_webui.log</code>. Each <b>Test voice</b> should add a <code>POST /api/tts/test</code> line; if you see nothing, the browser isn’t reaching this server (wrong URL/port or server quit).</p>
  </details>

<script>
  const log = document.getElementById('log');
  const status = document.getElementById('status');
  const input = document.getElementById('text');
  const sendBtn = document.getElementById('send');
  const webcamStart = document.getElementById('webcamStart');
  const webcamStop = document.getElementById('webcamStop');
  const webcamSend = document.getElementById('webcamSend');
  const webcamVideo = document.getElementById('webcamVideo');
  const webcamWrap = document.getElementById('webcamPreviewWrap');
  const webcamHint = document.getElementById('webcamHint');
  const voiceToggle = document.getElementById('voiceToggle');
  const holdBtn = document.getElementById('hold');
  const stopBtn = document.getElementById('stop');
  const stealthToggle = document.getElementById('stealthToggle');
  let stealthOn = false;

  function applyStealthUI() {{
    const root = document.documentElement;
    if (stealthOn) {{
      root.style.setProperty('--stealth-blur', '12px');
      root.style.setProperty('--stealth-dim', '0.68');
      if (stealthToggle) stealthToggle.textContent = 'Stealth On';
      status.textContent = 'Stealth mode';
    }} else {{
      root.style.setProperty('--stealth-blur', '0px');
      root.style.setProperty('--stealth-dim', '1');
      if (stealthToggle) stealthToggle.textContent = 'Stealth Off';
      if (status.textContent === 'Stealth mode') status.textContent = 'Idle';
    }}
  }}

  if (stealthToggle) {{
    stealthToggle.onclick = () => {{
      stealthOn = !stealthOn;
      applyStealthUI();
      try {{ localStorage.setItem('l041_stealth', stealthOn ? '1' : '0'); }} catch (e) {{}}
    }};
    try {{ stealthOn = localStorage.getItem('l041_stealth') === '1'; }} catch (e) {{}}
    applyStealthUI();
  }}

  async function fetchWithTimeout(url, options = {{}}, timeoutMs = 95000) {{
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {{
      return await fetch(url, {{ ...options, signal: controller.signal }});
    }} finally {{
      clearTimeout(timer);
    }}
  }}

  function captureWebcamJpegDataUrl() {{
    if (!webcamVideo || !webcamVideo.srcObject) return null;
    const w = webcamVideo.videoWidth, h = webcamVideo.videoHeight;
    if (!w || !h) return null;
    const maxW = 1280;
    let tw = w, th = h;
    if (w > maxW) {{
      tw = maxW;
      th = Math.round(h * (maxW / w));
    }}
    const c = document.createElement('canvas');
    c.width = tw;
    c.height = th;
    const ctx = c.getContext('2d');
    ctx.drawImage(webcamVideo, 0, 0, tw, th);
    return c.toDataURL('image/jpeg', 0.85);
  }}

  if (webcamStart) {{
    webcamStart.onclick = async () => {{
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
        if (webcamHint) webcamHint.textContent = 'This browser does not expose getUserMedia.';
        return;
      }}
      try {{
        const stream = await navigator.mediaDevices.getUserMedia({{ video: {{ facingMode: 'user' }}, audio: false }});
        webcamVideo.srcObject = stream;
        webcamWrap.classList.add('on');
        webcamStart.disabled = true;
        webcamStop.disabled = false;
        webcamSend.disabled = false;
        if (webcamHint) webcamHint.textContent = 'Preview on. Type a question (optional) and click Send with camera.';
      }} catch (e) {{
        if (webcamHint) webcamHint.textContent = 'Camera error: ' + ((e && e.message) ? e.message : e);
      }}
    }};
  }}
  if (webcamStop) {{
    webcamStop.onclick = () => {{
      const stream = webcamVideo && webcamVideo.srcObject;
      if (stream) stream.getTracks().forEach((t) => t.stop());
      webcamVideo.srcObject = null;
      webcamWrap.classList.remove('on');
      webcamStart.disabled = false;
      webcamStop.disabled = true;
      webcamSend.disabled = true;
      if (webcamHint) webcamHint.textContent = 'Camera off. Uses your browser camera (HTTPS or localhost). Only the frame you send goes to the server.';
    }};
  }}
  if (webcamSend) {{
    webcamSend.onclick = async () => {{
      const dataUrl = captureWebcamJpegDataUrl();
      if (!dataUrl) {{
        add('system', 'Camera not ready — turn Camera on and wait for the preview.');
        return;
      }}
      if (sendInFlight) return;
      const text = input.value.trim();
      sendInFlight = true;
      add('user', text ? (text + ' \\uD83D\\uDCF7') : '\\uD83D\\uDCF7 [webcam frame]');
      if (text) input.value = '';
      status.textContent = 'Thinking...';
      try {{
        const r = await fetchWithTimeout('/api/send', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{ text, image: dataUrl }})
        }});
        const d = await r.json().catch(() => ({{}}));
        if (!r.ok) {{
          add('system', (d && d.error) ? d.error : ('Send failed: ' + r.status));
        }}
      }} catch (e) {{
        add('system', (e && e.name === 'AbortError') ? 'Request timed out while Loki was thinking. Please retry.' : ('Send failed: ' + e));
      }} finally {{
        sendInFlight = false;
        status.textContent = 'Idle';
      }}
    }};
  }}

  function add(role, text) {{
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = (role === 'user' ? 'You: ' : role === 'assistant' ? 'L041: ' : '• ') + text;
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
      // Checkbox is the UI source of truth; server enforces on /api/voice/start and TTS in _run_model_turn.
      const voiceOn = !!voiceToggle.checked;
      if (!d.recording) {{
        holding = false;
        holdBtn.disabled = !voiceOn;
        stopBtn.disabled = true;
        if (status.textContent === 'Listening...' || status.textContent === 'Processing...') {{
          status.textContent = 'Idle';
        }}
      }} else {{
        holdBtn.disabled = true;
        stopBtn.disabled = false;
      }}
    }} catch (e) {{
      // ignore
    }}
  }}
  
  setInterval(syncVoiceUI, 500);

  let sendInFlight = false;
  sendBtn.onclick = async () => {{
    const text = input.value.trim();
    if (!text || sendInFlight) return;
    sendInFlight = true;
    add('user', text);
    input.value = '';
    status.textContent = 'Thinking...';
    try {{
      const r = await fetchWithTimeout('/api/send', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{text}})
      }});
      const d = await r.json().catch(() => ({{}}));
      if (!r.ok) {{
        add('system', (d && d.error) ? d.error : ('Send failed: ' + r.status));
      }}
    }} catch (e) {{
      add('system', (e && e.name === 'AbortError') ? 'Request timed out while Loki was thinking. Please retry.' : ('Send failed: ' + e));
    }} finally {{
      sendInFlight = false;
      status.textContent = 'Idle';
    }}
  }};

  input.addEventListener('keydown', (e) => {{
    if (e.key === 'Enter') {{
      e.preventDefault();
      if (!sendInFlight) sendBtn.click();
    }}
  }});

  voiceToggle.onchange = async () => {{
    holdBtn.disabled = !voiceToggle.checked;
    stopBtn.disabled = true;
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
    stopBtn.disabled = true;
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
      stopBtn.disabled = true;
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

  // --- TTS (say + Piper) ---
  const ttsSpeakReplies = document.getElementById('ttsSpeakReplies');
  const ttsEngine = document.getElementById('ttsEngine');
  const sayBlock = document.getElementById('sayBlock');
  const piperBlock = document.getElementById('piperBlock');
  const ttsVoice = document.getElementById('ttsVoice');
  const ttsRate = document.getElementById('ttsRate');
  const ttsRateVal = document.getElementById('ttsRateVal');
  const ttsRateDefault = document.getElementById('ttsRateDefault');
  const ttsPiperVoice = document.getElementById('ttsPiperVoice');
  const ttsPiperDataDir = document.getElementById('ttsPiperDataDir');
  const ttsPiperBinary = document.getElementById('ttsPiperBinary');
  const ttsPiperSpeaker = document.getElementById('ttsPiperSpeaker');
  const ttsPiperPace = document.getElementById('ttsPiperPace');
  const ttsPiperPaceVal = document.getElementById('ttsPiperPaceVal');
  const ttsPiperExpression = document.getElementById('ttsPiperExpression');
  const ttsPiperExpressionVal = document.getElementById('ttsPiperExpressionVal');
  const ttsPiperClarity = document.getElementById('ttsPiperClarity');
  const ttsPiperClarityVal = document.getElementById('ttsPiperClarityVal');
  const ttsPiperVol = document.getElementById('ttsPiperVol');
  const ttsPiperVolVal = document.getElementById('ttsPiperVolVal');
  const ttsPiperPause = document.getElementById('ttsPiperPause');
  const ttsPiperPauseVal = document.getElementById('ttsPiperPauseVal');
  const ttsPiperPlaySpeed = document.getElementById('ttsPiperPlaySpeed');
  const ttsPiperPlaySpeedVal = document.getElementById('ttsPiperPlaySpeedVal');
  const ttsPiperResetSound = document.getElementById('ttsPiperResetSound');
  const ttsPiperRefreshVoices = document.getElementById('ttsPiperRefreshVoices');
  const piperVoiceGrid = document.getElementById('piperVoiceGrid');
  const ttsPiperScanHint = document.getElementById('ttsPiperScanHint');
  const piperUseCustomBtn = document.getElementById('piperUseCustomBtn');
  const piperHelpDataDirPh = document.getElementById('piperHelpDataDirPh');
  const ttsSave = document.getElementById('ttsSave');
  const ttsTest = document.getElementById('ttsTest');
  const ttsHint = document.getElementById('ttsHint');
  const ttsNoStore = {{ cache: 'no-store' }};

  let ttsSaveTimer = null;
  function scheduleTtsSave() {{
    if (ttsSaveTimer) clearTimeout(ttsSaveTimer);
    ttsSaveTimer = setTimeout(async () => {{ await postTtsSettings(); }}, 480);
  }}

  function bindPiperSlider(rangeEl, valEl, decimals) {{
    function sync() {{
      const v = parseFloat(rangeEl.value);
      valEl.textContent = (decimals !== undefined && !isNaN(v)) ? v.toFixed(decimals) : String(rangeEl.value);
    }}
    rangeEl.addEventListener('input', sync);
    rangeEl.addEventListener('change', () => {{ sync(); scheduleTtsSave(); }});
    sync();
  }}
  bindPiperSlider(ttsPiperPace, ttsPiperPaceVal, 2);
  bindPiperSlider(ttsPiperExpression, ttsPiperExpressionVal, 3);
  bindPiperSlider(ttsPiperClarity, ttsPiperClarityVal, 2);
  bindPiperSlider(ttsPiperVol, ttsPiperVolVal, 2);
  bindPiperSlider(ttsPiperPause, ttsPiperPauseVal, 2);
  bindPiperSlider(ttsPiperPlaySpeed, ttsPiperPlaySpeedVal, 2);

  ttsPiperResetSound.onclick = () => {{
    ttsPiperPace.value = '1';
    ttsPiperExpression.value = '0.667';
    ttsPiperClarity.value = '0.8';
    ttsPiperVol.value = '1';
    ttsPiperPause.value = '0';
    ttsPiperPlaySpeed.value = '1';
    [ttsPiperPace, ttsPiperExpression, ttsPiperClarity, ttsPiperVol, ttsPiperPause, ttsPiperPlaySpeed].forEach((el) => el.dispatchEvent(new Event('input')));
    scheduleTtsSave();
  }};

  function setPiperSliderFromServer(sd) {{
    function set(id, key, def) {{
      const el = document.getElementById(id);
      if (!el) return;
      let n = (sd[key] != null && sd[key] !== '') ? parseFloat(sd[key]) : def;
      if (isNaN(n)) n = def;
      el.value = String(n);
      el.dispatchEvent(new Event('input'));
    }}
    set('ttsPiperPace', 'piper_length_scale', 1);
    set('ttsPiperExpression', 'piper_noise_scale', 0.667);
    set('ttsPiperClarity', 'piper_noise_w_scale', 0.8);
    set('ttsPiperVol', 'piper_volume', 1);
    set('ttsPiperPause', 'piper_sentence_silence', 0);
    set('ttsPiperPlaySpeed', 'piper_playback_rate', 1);
  }}

  async function applyTtsFormFromServer(sd, hintText) {{
    if (!sd || sd.ok === false) return false;
    ttsSpeakReplies.checked = !!sd.tts_enable;
    ttsEngine.value = (sd.tts_engine === 'piper') ? 'piper' : 'say';
    refreshTtsEngineUi();
    ttsVoice.value = sd.say_voice || '';
    if (sd.say_rate_wpm == null || sd.say_rate_wpm === '') {{
      ttsRateDefault.checked = true;
    }} else {{
      ttsRateDefault.checked = false;
      const r = parseInt(sd.say_rate_wpm, 10);
      if (!isNaN(r)) ttsRate.value = String(Math.min(260, Math.max(100, r)));
    }}
    syncTtsRateDisabled();
    ttsPiperVoice.value = sd.piper_voice || '';
    ttsPiperDataDir.value = sd.piper_data_dir || '';
    ttsPiperBinary.value = sd.piper_binary || '';
    ttsPiperSpeaker.value = (sd.piper_speaker_id != null && sd.piper_speaker_id !== '') ? String(sd.piper_speaker_id) : '';
    setPiperSliderFromServer(sd);
    if (hintText !== undefined && hintText !== null) {{
      ttsHint.textContent = hintText;
    }} else if (sd.settings_path) {{
      ttsHint.textContent = 'Settings file: ' + sd.settings_path;
    }}
    updatePiperHelpExampleDir();
    if (ttsEngine.value === 'piper') await refreshPiperInstalledVoices();
    return true;
  }}

  function updatePiperHelpExampleDir() {{
    const d = ttsPiperDataDir.value.trim();
    if (piperHelpDataDirPh) piperHelpDataDirPh.textContent = d || 'memories/piper_voices';
  }}
  ttsPiperDataDir.addEventListener('input', () => {{ updatePiperHelpExampleDir(); scheduleTtsSave(); }});
  ttsPiperBinary.addEventListener('change', scheduleTtsSave);
  ttsPiperSpeaker.addEventListener('change', scheduleTtsSave);

  function setPiperVoiceFieldReadonly(ro) {{
    ttsPiperVoice.readOnly = ro;
    ttsPiperVoice.style.opacity = ro ? '0.88' : '1';
  }}

  function clearPiperVoiceCards() {{
    document.querySelectorAll('.piper-voice-card').forEach((b) => {{
      b.classList.remove('piper-voice-card--on');
      b.setAttribute('aria-checked', 'false');
    }});
  }}

  function selectPiperVoiceCard(id) {{
    ttsPiperVoice.value = id;
    setPiperVoiceFieldReadonly(true);
    document.querySelectorAll('.piper-voice-card').forEach((b) => {{
      const on = b.dataset.voiceId === id;
      b.classList.toggle('piper-voice-card--on', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    }});
  }}

  piperUseCustomBtn.onclick = () => {{
    clearPiperVoiceCards();
    setPiperVoiceFieldReadonly(false);
    ttsPiperVoice.focus();
    scheduleTtsSave();
  }};

  ttsPiperVoice.addEventListener('input', () => {{
    clearPiperVoiceCards();
    setPiperVoiceFieldReadonly(false);
    scheduleTtsSave();
  }});
  ttsPiperVoice.addEventListener('focus', () => {{
    clearPiperVoiceCards();
    setPiperVoiceFieldReadonly(false);
  }});

  async function refreshPiperInstalledVoices() {{
    ttsPiperScanHint.textContent = 'Scanning…';
    piperVoiceGrid.innerHTML = '';
    const prevSel = ttsPiperVoice.value.trim();
    const dd = ttsPiperDataDir.value.trim();
    const q = dd ? ('?data_dir=' + encodeURIComponent(dd)) : '';
    try {{
      const r = await fetch('/api/tts/piper_installed_voices' + q, ttsNoStore);
      const d = await r.json();
      if (!r.ok || !d.ok) {{
        ttsPiperScanHint.textContent = (d && d.error) ? d.error : 'Scan failed';
        return;
      }}
      ttsPiperScanHint.textContent = d.voices && d.voices.length
        ? (d.voices.length + ' voice(s) — tap one')
        : ('No voices in folder: ' + d.data_dir);
      if (!d.exists) {{
        ttsPiperScanHint.textContent = 'Folder not found: ' + d.data_dir;
        piperVoiceGrid.innerHTML = '<p class="small" style="margin:0">Create the folder or set <b>Advanced → Voice folder</b>, then refresh.</p>';
        setPiperVoiceFieldReadonly(false);
        return;
      }}
      if (!d.voices || !d.voices.length) {{
        piperVoiceGrid.innerHTML = '<p class="small" style="margin:0">No <code>.onnx</code> voices here yet. Open <b>Advanced</b> for download steps, then refresh.</p>';
        setPiperVoiceFieldReadonly(false);
        return;
      }}
      for (const v of d.voices) {{
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'piper-voice-card';
        btn.setAttribute('role', 'radio');
        btn.setAttribute('aria-checked', 'false');
        btn.dataset.voiceId = v.id;
        const title = document.createElement('div');
        title.className = 'pvc-title';
        title.textContent = v.id;
        const sub = document.createElement('div');
        sub.className = 'pvc-sub';
        sub.textContent = v.has_json ? 'Ready to use' : 'Warning: missing .onnx.json';
        btn.appendChild(title);
        btn.appendChild(sub);
        btn.onclick = () => {{ selectPiperVoiceCard(v.id); scheduleTtsSave(); }};
        piperVoiceGrid.appendChild(btn);
      }}
      if (prevSel && d.voices.some((x) => x.id === prevSel)) {{
        selectPiperVoiceCard(prevSel);
      }} else {{
        clearPiperVoiceCards();
        setPiperVoiceFieldReadonly(false);
      }}
    }} catch (err) {{
      ttsPiperScanHint.textContent = 'Scan failed (network?)';
    }}
  }}

  ttsPiperRefreshVoices.onclick = () => {{ refreshPiperInstalledVoices(); }};

  function refreshTtsEngineUi() {{
    const p = ttsEngine.value === 'piper';
    sayBlock.style.display = p ? 'none' : 'block';
    piperBlock.style.display = p ? 'block' : 'none';
  }}

  function syncTtsRateDisabled() {{
    ttsRate.disabled = ttsRateDefault.checked;
    ttsRateVal.textContent = ttsRateDefault.checked ? 'default' : String(ttsRate.value);
  }}
  ttsRate.addEventListener('input', () => {{ if (!ttsRateDefault.checked) ttsRateVal.textContent = ttsRate.value; }});
  ttsRateDefault.addEventListener('change', syncTtsRateDisabled);

  async function loadTtsUi() {{
    try {{
      const vr = await fetch('/api/tts/voices', ttsNoStore);
      const vd = await vr.json();
      if (vd.platform && vd.platform !== 'darwin') {{
        ttsVoice.disabled = true;
        if (!ttsHint.textContent) ttsHint.textContent = 'macOS say list unavailable on this OS — use Piper or set LOKI_SAY_VOICE in .env.';
      }}
      if (vd.voices && vd.voices.length) {{
        while (ttsVoice.options.length > 1) ttsVoice.remove(1);
        for (const v of vd.voices) {{
          const opt = document.createElement('option');
          opt.value = v.id;
          const loc = v.locale ? ' (' + v.locale + ')' : '';
          opt.textContent = v.id + loc;
          ttsVoice.appendChild(opt);
        }}
      }}
    }} catch (e) {{
      ttsHint.textContent = 'Could not load macOS voice list.';
    }}
    try {{
      const sr = await fetch('/api/tts/settings', ttsNoStore);
      const sd = await sr.json();
      if (!sr.ok) return;
      await applyTtsFormFromServer(sd, sd.settings_path ? ('Settings file: ' + sd.settings_path) : null);
    }} catch (e) {{}}
  }}

  function parseSliderFloat(el, defVal) {{
    if (!el) return defVal;
    const v = parseFloat(el.value);
    return Number.isFinite(v) ? v : defVal;
  }}

  function buildTtsPostBody() {{
    let spk = ttsPiperSpeaker.value.trim();
    let spkOut = null;
    if (spk !== '') {{
      const n = parseInt(spk, 10);
      if (!isNaN(n)) spkOut = n;
    }}
    const pauseRaw = parseSliderFloat(ttsPiperPause, 0.0);
    return {{
      tts_engine: ttsEngine.value,
      say_voice: ttsVoice.value || '',
      say_rate_wpm: ttsRateDefault.checked ? null : parseInt(ttsRate.value, 10),
      tts_enable: ttsSpeakReplies.checked,
      piper_voice: ttsPiperVoice.value.trim(),
      piper_data_dir: ttsPiperDataDir.value.trim(),
      piper_binary: ttsPiperBinary.value.trim(),
      piper_length_scale: parseSliderFloat(ttsPiperPace, 1.0),
      piper_noise_scale: parseSliderFloat(ttsPiperExpression, 0.667),
      piper_noise_w_scale: parseSliderFloat(ttsPiperClarity, 0.8),
      piper_volume: parseSliderFloat(ttsPiperVol, 1.0),
      piper_sentence_silence: Number.isFinite(pauseRaw) ? pauseRaw : 0.0,
      piper_playback_rate: parseSliderFloat(ttsPiperPlaySpeed, 1.0),
      piper_speaker_id: spkOut
    }};
  }}

  async function postTtsSettings() {{
    const body = buildTtsPostBody();
    const r = await fetch('/api/tts/settings', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body),
      ...ttsNoStore
    }});
    const d = await r.json().catch(() => ({{}}));
    if (!r.ok) {{
      ttsHint.textContent = (d && d.error) ? d.error : 'Save failed';
      return false;
    }}
    await applyTtsFormFromServer(d, 'Saved. ' + (d.settings_path || ''));
    return true;
  }}

  ttsSave.onclick = async () => {{ await postTtsSettings(); }};
  ttsSpeakReplies.addEventListener('change', async () => {{ await postTtsSettings(); }});
  ttsEngine.addEventListener('change', async () => {{
    refreshTtsEngineUi();
    if (ttsEngine.value === 'piper') {{
      updatePiperHelpExampleDir();
      await refreshPiperInstalledVoices();
    }}
    await postTtsSettings();
  }});

  ttsTest.onclick = async () => {{
    const body = Object.assign({{}}, buildTtsPostBody());
    const r = await fetch('/api/tts/test', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body),
      ...ttsNoStore
    }});
    const d = await r.json().catch(() => ({{}}));
    if (!r.ok) {{
      ttsHint.textContent = (d && d.error) ? d.error : 'Test failed (check terminal for [tts] Piper errors)';
      return;
    }}
    await applyTtsFormFromServer(d, 'Playing test with engine: ' + (d.tts_engine || '?') + '. ' + (d.settings_path || ''));
  }};

  const personaPathEl = document.getElementById('personaPathEl');
  const personaText = document.getElementById('personaText');
  const personaHint = document.getElementById('personaHint');
  const personaSave = document.getElementById('personaSave');
  const personaReload = document.getElementById('personaReload');
  const personaReveal = document.getElementById('personaReveal');

  async function loadPersonaPanel() {{
    if (!personaText) return;
    personaHint.textContent = '';
    try {{
      const r = await fetch('/api/persona', {{ cache: 'no-store' }});
      const d = await r.json();
      if (!r.ok || !d.ok) {{
        personaHint.textContent = (d && d.error) ? d.error : 'Could not load persona';
        return;
      }}
      if (personaPathEl) personaPathEl.textContent = d.path || '';
      personaText.value = d.content || '';
      personaHint.textContent = 'Limit ' + (d.max_chars || '') + ' characters — Save applies to this chat session; /mem reloads from disk.';
    }} catch (e) {{
      personaHint.textContent = 'Failed to load persona';
    }}
  }}

  if (personaSave) {{
    personaSave.onclick = async () => {{
      personaHint.textContent = 'Saving…';
      try {{
        const r = await fetch('/api/persona', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ content: personaText.value }}),
          cache: 'no-store'
        }});
        const d = await r.json().catch(() => ({{}}));
        if (!r.ok) {{
          personaHint.textContent = (d && d.error) ? d.error : 'Save failed';
          return;
        }}
        personaHint.textContent = 'Saved and applied to this chat session (' + (d.len || 0) + ' chars).';
      }} catch (e) {{
        personaHint.textContent = 'Save failed';
      }}
    }};
  }}
  if (personaReload) personaReload.onclick = () => loadPersonaPanel();
  if (personaReveal) {{
    personaReveal.onclick = async () => {{
      try {{
        const r = await fetch('/api/persona/reveal', {{ method: 'POST', cache: 'no-store' }});
        const d = await r.json().catch(() => ({{}}));
        if (!r.ok) personaHint.textContent = (d && d.error) ? d.error : 'Could not reveal file';
        else personaHint.textContent = 'Finder should highlight the file.';
      }} catch (e) {{
        personaHint.textContent = 'Reveal failed';
      }}
    }};
  }}

  loadPersonaPanel();
  loadTtsUi();
</script>
</body>
</html>"""

    def handle_webcam_send(self, user_text: str, image_data_url: str) -> str:
        """Analyze one browser webcam frame (data URL) + optional user message, then run chat turn."""

        with self.chat_lock:
            self._busy = True
            self._set_presence("thinking")
            try:
                return self._handle_webcam_send_locked(user_text, image_data_url)
            finally:
                self._busy = False
                if self._presence_snapshot().get("state") != "speaking":
                    self._set_presence("idle")

    def _handle_webcam_send_locked(self, user_text: str, image_data_url: str) -> str:
        url = ld.validate_image_data_url(image_data_url)
        ut = (user_text or "").strip()
        if ut:
            vision_prompt = (
                f"The user says:\n{ut}\n\n"
                "Describe what you see in this camera frame in detail. Answer their question directly if they asked one. "
                "Note any readable text, objects, people (generally), lighting, and background."
            )
        else:
            vision_prompt = (
                "Describe what you see in this camera frame in detail: objects, readable text, people (generally), "
                "lighting, and background."
            )
        analysis = ld.analyze_images_with_xai_responses(
            ld.XAI_API_KEY,
            [url],
            vision_prompt,
            max_output_tokens=520,
        )

        r_query = ut if ut else "webcam camera view scene description"
        retrieved_block = ""
        try:
            qemb = ld.embed_texts(self.xai, [r_query])[0]
            hits = self.vstore.search(qemb, k=ld.RETRIEVAL_K)
            if hits:
                parts = []
                for h in hits:
                    parts.append(f"- score={h['score']:.3f} source={h['source_path']} chunk={h['chunk_index']}\n{h['text']}")
                retrieved_block = "Retrieved memory:\n" + "\n\n".join(parts)
        except Exception:
            retrieved_block = ""

        if ut:
            core = f"{ut}\n\n---\n[Webcam frame — vision summary]\n{analysis}"
        else:
            core = f"[Webcam frame — user sent video only]\n\n[Vision summary]\n{analysis}"

        if retrieved_block:
            self.messages.append({"role": "user", "content": f"{core}\n\n---\n{retrieved_block}"})
        else:
            self.messages.append({"role": "user", "content": core})

        return self._run_model_turn(skip_tts=False)

    def handle_text(self, user_in: str, from_voice: bool, blocking: bool = True, *, skip_tts: bool = False) -> str:
        with self.chat_lock:
            self._busy = True
            self._set_presence("thinking")
            try:
                return self._handle_text_locked(user_in, skip_tts=skip_tts)
            finally:
                self._busy = False
                if self._presence_snapshot().get("state") != "speaking":
                    self._set_presence("idle")

    def _handle_text_locked(self, user_in: str, *, skip_tts: bool = False) -> str:
        autop = ld.looks_like_existing_path(user_in)
        if autop:
            user_in = f"/attach {autop}"

        if user_in == "/help":
            return (
                "Commands: /tools, /scan, /mem, /persona, /attach <path>, /ingest <path>, /compile_mem, "
                "/set_screen left <i>, /autodetect_screens, /upgrade <req> — time: get_current_time; "
                "macOS Calendar: apple_calendar_* tools. Web UI: **Camera on** + **Send with camera** for webcam. "
                "Telegram: same session if LOKI_TELEGRAM=1."
            )

        if user_in == "/tools":
            return "\n".join(self.tools.list_names())

        if user_in == "/scan":
            return self.butt.scan()

        if user_in == "/mem":
            self.memory_text, _ = ld.load_memories(ld.MEMORY_DIR)
            ld.refresh_system_time_message(self.messages, ld.build_base_system_static(self.memory_text))
            return (
                f"[memory] Reloaded {ld.MEMORY_DIR} + persona ({ld.PERSONA_INSTRUCTIONS_PATH.name}). "
                f"Path: {ld.PERSONA_INSTRUCTIONS_PATH}"
            )

        if user_in == "/persona":
            ld.ensure_persona_template()
            n = len(ld.load_persona_instructions())
            return (
                f"[persona] Instructions file:\n{ld.PERSONA_INSTRUCTIONS_PATH}\n"
                f"[persona] Current length: {n} characters (max {ld.PERSONA_INSTRUCTIONS_MAX_CHARS}). "
                "Run **/mem** after editing on disk to refresh the system prompt."
            )

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

            return self._run_model_turn(skip_tts=skip_tts)

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

        return self._run_model_turn(skip_tts=skip_tts)

    def _run_tool_call_with_timeout(self, tool_name: str, args: Dict[str, Any], timeout_s: float = 45.0) -> str:
        """
        Guard tool execution so one stuck tool cannot freeze /api/send forever.
        """
        out: Dict[str, str] = {}

        def _runner() -> None:
            try:
                out["result"] = ld.run_tool_call(self.tools, str(tool_name), args if isinstance(args, dict) else {})
            except Exception as e:
                out["error"] = str(e)

        t = threading.Thread(target=_runner, name=f"tool-{tool_name}", daemon=True)
        t.start()
        t.join(timeout=max(1.0, float(timeout_s)))
        if t.is_alive():
            return f"[tool timeout] `{tool_name}` exceeded {timeout_s:.0f}s; continue without it."
        if "error" in out:
            return f"[tool error] `{tool_name}`: {out['error']}"
        return out.get("result", "")

    def _run_model_turn(self, *, skip_tts: bool = False) -> str:
        ld.refresh_system_time_message(self.messages, ld.build_base_system_static(self.memory_text))
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
                timeout_s = 45.0
                if str(tool_name) == "submit_art_generation":
                    timeout_s = max(45.0, float(ld.LOKI_ART_WEBHOOK_TIMEOUT_S) + 30.0)
                result = self._run_tool_call_with_timeout(
                    str(tool_name), args if isinstance(args, dict) else {}, timeout_s=timeout_s
                )

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

            ld.refresh_system_time_message(self.messages, ld.build_base_system_static(self.memory_text))
            resp = self.xai.chat(self.messages, tools=self.tools.list_specs_for_model())
            msg = ld.extract_assistant_message(resp)

        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "\n".join([p.get("text", "") for p in content if isinstance(p, dict)])

        self.messages.append({"role": "assistant", "content": content})

        # TTS only when Voice On is enabled (checkbox syncs /api/voice/toggle). Telegram skips TTS.
        if not skip_tts and self.voice_enabled and self.voice_mgr:
            try:
                self._set_presence("speaking")
                self.voice_mgr.speak(str(content))
            except Exception:
                pass
            finally:
                self._set_presence("idle")
        else:
            self._set_presence("idle")

        return str(content)

    def run(self):
        self.app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=True)


def main() -> None:
    ui = LokiWebUI()
    print(f"[webui] Listening on {APP_HOST}:{APP_PORT}", flush=True)
    ui.run()


if __name__ == "__main__":
    main()

