#!/usr/bin/env python3
"""
Minimal Tkinter UI for Loki Direct.

Features:
- Chat log + message input
- Voice toggle + hold-to-talk button (no global hotkey needed)

Run:
  python3 loki_direct_gui.py
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from tkinter import ttk

import loki_direct as ld


class LokiGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Loki Direct")
        self.geometry("980x720")

        self.ui_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.chat_lock = threading.Lock()

        self._build_ui()

        self.butt = ld.ButtplugController(ld.INTIFACE_WS)
        self.butt.start()

        try:
            self.screen = ld.ScreenController()
        except Exception as e:
            self.screen = None
            self._ui_append("system", f"[screen] Disabled: {e}")

        # Memory (snapshot prompt)
        self.memory_text, _mem_warnings = ld.load_memories(ld.MEMORY_DIR)

        # Tools + plugins
        self.tools = ld.build_core_tools(self.butt, self.screen)
        ld.ensure_plugins_package(ld.PLUGINS_DIR)
        for msg in ld.load_plugins(ld.PLUGINS_DIR, self.tools):
            self._ui_append("system", f"[plugin] {msg}")

        self.xai = ld.XAIClient(ld.XAI_API_KEY, ld.XAI_ENDPOINT, ld.XAI_MODEL, timeout_s=ld.REQUEST_TIMEOUT_S)
        self.vstore = ld.VectorMemoryStore(ld.VECTOR_DB_PATH)

        self.watcher: Optional[ld.MemoryFolderWatcher] = None
        if ld.WATCH_MEMORY_FOLDER:
            self.watcher = ld.MemoryFolderWatcher(ld.INBOX_DIR, ld.PROCESSED_DIR, ld.WATCH_POLL_S, xai=self.xai, vstore=self.vstore)
            self.watcher.start()

        # Conversation messages
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": ld.compose_system_with_time(ld.build_base_system_static(self.memory_text))}
        ]

        # Voice manager (button-driven)
        self.voice_enabled = True
        self.voice_mgr: Optional[ld.VoiceManager] = None
        self._init_voice()

        self._busy = False
        self._poll_ui_queue()

        self._ui_append("system", "Loki Direct GUI ready.")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="nsew")

        self.rowconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)

        self.chat_log = ScrolledText(top, wrap=tk.WORD, state="disabled")
        self.chat_log.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        controls = ttk.Frame(self)
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.input = ttk.Entry(controls, textvariable=self.input_var)
        self.input.grid(row=0, column=0, sticky="ew", padx=8, pady=8)

        self.send_btn = ttk.Button(controls, text="Send", command=self._on_send_clicked)
        self.send_btn.grid(row=0, column=1, sticky="e", padx=(0, 8), pady=8)

        voice_bar = ttk.Frame(self)
        voice_bar.grid(row=2, column=0, sticky="ew")
        voice_bar.columnconfigure(0, weight=0)
        voice_bar.columnconfigure(1, weight=0)
        voice_bar.columnconfigure(2, weight=1)

        self.voice_toggle_var = tk.BooleanVar(value=True)
        self.voice_toggle = ttk.Checkbutton(
            voice_bar,
            text="Voice On",
            variable=self.voice_toggle_var,
            command=self._on_voice_toggle,
        )
        self.voice_toggle.grid(row=0, column=0, padx=8, pady=(0, 8))

        self.hold_btn = ttk.Button(voice_bar, text="Hold to Talk", command=self._hold_btn_click_stub)
        self.hold_btn.grid(row=0, column=1, padx=8, pady=(0, 8))
        self.hold_btn.bind("<ButtonPress-1>", self._on_hold_start)
        self.hold_btn.bind("<ButtonRelease-1>", self._on_hold_stop)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(voice_bar, textvariable=self.status_var).grid(row=0, column=2, sticky="w", padx=8)

    def _init_voice(self) -> None:
        if not ld.VOICE_ENABLE:
            return

        # TTS uses macOS `say` inside VoiceManager.
        _tts0 = ld.load_tts_settings_merged()
        self.voice_mgr = ld.VoiceManager(
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
            stt_task_fn=lambda transcript: self._dispatch_voice_transcript(transcript),
        )

        # Do NOT start global keyboard listener; UI buttons drive recording.
        self._ui_append("system", "[voice] Ready (use Hold to Talk button).")

    def _dispatch_voice_transcript(self, transcript: str) -> None:
        # Run the chat turn in background thread context (already background from VoiceManager).
        self.handle_user_text(transcript, from_voice=True)

    def _on_voice_toggle(self) -> None:
        self.voice_enabled = bool(self.voice_toggle_var.get())
        self.hold_btn.configure(state="normal" if self.voice_enabled else "disabled")

    def _hold_btn_click_stub(self) -> None:
        # Button uses press/release bindings; click does nothing.
        return

    def _on_hold_start(self, _event=None) -> None:
        if not self.voice_enabled or self.voice_mgr is None:
            return
        if self._busy:
            return
        self.status_var.set("Listening...")
        try:
            self.voice_mgr.start_recording()
        except Exception as e:
            self._ui_append("system", f"[voice] start_recording failed: {e}")
            self.status_var.set("Idle")

    def _on_hold_stop(self, _event=None) -> None:
        if not self.voice_enabled or self.voice_mgr is None:
            return
        try:
            self.voice_mgr.stop_recording()
        except Exception:
            pass
        self.status_var.set("Processing...")

    def _ui_append(self, role: str, text: str) -> None:
        self.ui_queue.put((role, text))

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                role, text = self.ui_queue.get_nowait()
                self._render_chat(role, text)
        except queue.Empty:
            pass
        self.after(100, self._poll_ui_queue)

    def _render_chat(self, role: str, text: str) -> None:
        self.chat_log.configure(state="normal")
        prefix = "You" if role == "user" else ("Loki" if role == "assistant" else "•")
        self.chat_log.insert(tk.END, f"{prefix}: {text}\n")
        self.chat_log.configure(state="disabled")
        self.chat_log.see(tk.END)

    def _refresh_system_prompt(self) -> None:
        """Reload snapshot memory into the system message (clock refreshed on each model call)."""

        ld.refresh_system_time_message(self.messages, ld.build_base_system_static(self.memory_text))

    def _on_send_clicked(self) -> None:
        text = self.input_var.get().strip()
        if not text:
            return
        self.input_var.set("")
        self._render_chat("user", text)
        threading.Thread(target=self.handle_user_text, args=(text, False), daemon=True).start()

    def handle_user_text(self, user_in: str, from_voice: bool) -> None:
        if not user_in:
            return
        if self._busy:
            return

        with self.chat_lock:
            self._busy = True
            try:
                self._handle_text_locked(user_in)
            finally:
                self._busy = False
                self.after(0, lambda: self.status_var.set("Idle"))

    def _handle_text_locked(self, user_in: str) -> None:
        # Auto-path: if user pasted an existing file path, treat it like /attach
        autop = ld.looks_like_existing_path(user_in)
        if autop:
            user_in = f"/attach {autop}"

        if user_in.lower() in {"/quit", "quit", "exit"}:
            self.after(0, self._on_close)
            return

        # Commands
        if user_in == "/help":
            self._ui_append("system", "Commands: /tools, /scan, /mem, /attach <path>, /ingest <path>, /compile_mem, /set_screen left <i>, /autodetect_screens, /upgrade <req>")
            return
        if user_in == "/tools":
            self._ui_append("system", "\n".join(self.tools.list_names()))
            return
        if user_in == "/scan":
            self._ui_append("system", self.butt.scan())
            return
        if user_in == "/mem":
            self.memory_text, _ = ld.load_memories(ld.MEMORY_DIR)
            self._refresh_system_prompt()
            self._ui_append("system", f"[memory] Reloaded {ld.MEMORY_DIR}")
            return

        if user_in.startswith("/set_screen "):
            raw = user_in[len("/set_screen ") :].strip()
            parts = raw.split()
            if len(parts) != 2:
                self._ui_append("system", "Usage: /set_screen <left|right> <monitor_index>")
                return
            side = parts[0].strip().lower()
            idx = int(parts[1])
            screen_indices = ld.load_screen_indices()
            screen_indices[side] = idx
            ld.save_screen_indices(screen_indices)
            self._ui_append("system", f"[screen] Updated indices: left={screen_indices['left']} right={screen_indices['right']}")
            return

        if user_in == "/autodetect_screens":
            if self.screen is None:
                self._ui_append("system", "[screen] Disabled (no screen tools).")
                return
            mons = self.screen.monitors()
            if not mons:
                self._ui_append("system", "[screen] No monitors detected.")
                return
            mons_sorted = sorted(mons, key=lambda m: int(m.get("left", 0)))
            screen_indices = {"left": int(mons_sorted[0]["index"]), "right": int(mons_sorted[-1]["index"])}
            ld.save_screen_indices(screen_indices)
            self._ui_append("system", f"[screen] Autodetected: left={screen_indices['left']} right={screen_indices['right']}")
            return

        # Attach (file analysis)
        if user_in.startswith("/attach "):
            raw = user_in[len("/attach ") :].strip().strip('"').strip("'")
            if not raw:
                self._ui_append("system", "Usage: /attach <path>")
                return
            p = Path(raw)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if not p.exists() or not p.is_file():
                self._ui_append("system", f"[attach] Not found: {p}")
                return

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

            self._run_model_turn()
            return

        # Ingest into vector memory
        if user_in.startswith("/ingest "):
            raw = user_in[len("/ingest ") :].strip().strip('"').strip("'").replace("\\ ", " ")
            if not raw:
                self._ui_append("system", "Usage: /ingest <path>")
                return
            p = Path(raw)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if not p.exists():
                self._ui_append("system", f"[ingest] Not found: {p}")
                return

            files: List[Path] = []
            if p.is_file():
                files = [p]
            else:
                files = ld.iter_supported_files(p)

            ingested = 0
            failed = 0
            for fp in sorted(files):
                try:
                    ld.ingest_one_path(self.xai, self.vstore, fp)
                    ingested += 1
                except Exception as e:
                    failed += 1
                    self._ui_append("system", f"[ingest] Failed {fp.name}: {e}")

            try:
                self.vstore.export_compiled_markdown(ld.COMPILED_MEMORY_PATH)
            except Exception as e:
                self._ui_append("system", f"[compile] Failed: {e}")

            self._ui_append(
                "system",
                f"[ingest] Done. Files ingested: {ingested}, failed: {failed}. Compiled: {ld.COMPILED_MEMORY_PATH}",
            )
            return

        if user_in == "/compile_mem":
            self.vstore.export_compiled_markdown(ld.COMPILED_MEMORY_PATH)
            self._ui_append("system", f"[compile] Wrote {ld.COMPILED_MEMORY_PATH}")
            return

        # Normal chat turn with retrieval + tools
        self.messages.append({"role": "user", "content": user_in})
        # Retrieval injection: overwrite last user content with retrieved memory
        try:
            qemb = ld.embed_texts(self.xai, [user_in])[0]
            hits = self.vstore.search(qemb, k=ld.RETRIEVAL_K)
            if hits:
                parts = []
                for h in hits:
                    parts.append(f"- score={h['score']:.3f} source={h['source_path']} chunk={h['chunk_index']}\n{h['text']}")
                retrieved_block = "Retrieved memory:\n" + "\n\n".join(parts)
                self.messages[-1]["content"] = f"{user_in}\n\n---\n{retrieved_block}"
        except Exception:
            pass

        self._run_model_turn()

    def _run_model_turn(self) -> None:
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
                        result = ld.analyze_images_with_xai_responses(
                            self.xai.api_key, img_urls, prompt, max_output_tokens=360
                        )

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
        self.after(0, lambda: self._render_chat("assistant", str(content)))

        if self.voice_mgr and self.voice_enabled and getattr(self.voice_mgr, "tts_enable", True):
            try:
                self.voice_mgr.speak(str(content))
            except Exception:
                pass

    def _on_close(self) -> None:
        try:
            if self.watcher:
                self.watcher.stop()
        except Exception:
            pass
        try:
            if self.butt:
                self.butt.stop()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


def main() -> None:
    app = LokiGUI()
    app.mainloop()


if __name__ == "__main__":
    main()

