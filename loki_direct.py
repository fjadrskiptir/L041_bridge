#!/usr/bin/env python3
"""
Loki Direct - local Grok app with screen + toy control + memory + self-upgrade.

Run:
  python3 loki_direct.py

Requirements (in your existing venv):
  pip install requests python-dotenv pyautogui buttplug

Intiface Central:
  ws://127.0.0.1:12345
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import queue
import re
import subprocess
import signal
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

import math
import sqlite3
import hashlib

def _maybe_reexec_into_venv() -> None:
    """
    Ensure `python3 loki_direct.py` uses the repo venv if present.
    This avoids missing-dependency issues when system python is used.
    """

    try:
        repo_root = Path(__file__).resolve().parent
    except Exception:
        return
    vpy = repo_root / "venv" / "bin" / "python"
    if not vpy.exists():
        return
    try:
        in_repo_venv = Path(sys.executable).absolute().as_posix().startswith((repo_root / "venv" / "bin").as_posix())
    except Exception:
        in_repo_venv = False
    if in_repo_venv:
        return
    # Always add venv site-packages so imports work even if we can't execv.
    lib = repo_root / "venv" / "lib"
    if lib.exists():
        for sp in sorted(lib.glob("python*/site-packages")):
            sys.path.insert(0, str(sp))
            break

    # If we're being run as a script, prefer re-exec into the venv interpreter.
    try:
        argv0 = Path(sys.argv[0]).name
    except Exception:
        argv0 = ""
    if argv0 == Path(__file__).name:
        try:
            os.execv(str(vpy), [str(vpy), str(Path(__file__).resolve()), *sys.argv[1:]])
        except Exception:
            pass


_maybe_reexec_into_venv()

try:
    import requests  # noqa: E402
    from dotenv import load_dotenv  # noqa: E402
except ModuleNotFoundError:
    # Last-resort: if the early venv setup didn't take, try again.
    _maybe_reexec_into_venv()
    import requests  # type: ignore  # noqa: E402
    from dotenv import load_dotenv  # type: ignore  # noqa: E402

load_dotenv()


# -----------------------------
# Config
# -----------------------------

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_ENDPOINT = os.getenv("XAI_ENDPOINT", "https://api.x.ai/v1/chat/completions")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning")
XAI_EMBEDDING_MODEL = os.getenv("XAI_EMBEDDING_MODEL", "grok-embedding")
XAI_EMBEDDINGS_ENDPOINT = os.getenv("XAI_EMBEDDINGS_ENDPOINT", "https://api.x.ai/v1/embeddings")
XAI_VISION_MODEL = os.getenv("XAI_VISION_MODEL", "grok-4.20-beta-latest-non-reasoning")
XAI_RESPONSES_ENDPOINT = os.getenv("XAI_RESPONSES_ENDPOINT", "https://api.x.ai/v1/responses")

INTIFACE_WS = os.getenv("INTIFACE_WS", "ws://127.0.0.1:12345")

MEMORY_DIR = Path(os.getenv("LOKI_MEMORY_DIR", "memories")).resolve()
PLUGINS_DIR = Path(os.getenv("LOKI_PLUGINS_DIR", "loki_plugins")).resolve()
VECTOR_DB_PATH = Path(os.getenv("LOKI_VECTOR_DB_PATH", "loki_memory.sqlite3")).resolve()
COMPILED_MEMORY_PATH = Path(os.getenv("LOKI_COMPILED_MEMORY_PATH", str(MEMORY_DIR / "compiled_memory.md"))).resolve()
INBOX_DIR = Path(os.getenv("LOKI_INBOX_DIR", str(MEMORY_DIR / "inbox"))).resolve()
PROCESSED_DIR = Path(os.getenv("LOKI_PROCESSED_DIR", str(MEMORY_DIR / "processed"))).resolve()
SCREEN_CONFIG_PATH = Path(os.getenv("LOKI_SCREEN_CONFIG_PATH", str(MEMORY_DIR / "screen_indices.json"))).resolve()

REQUEST_TIMEOUT_S = float(os.getenv("LOKI_HTTP_TIMEOUT_S", "60"))
RETRIEVAL_K = int(os.getenv("LOKI_RETRIEVAL_K", "6"))
WATCH_MEMORY_FOLDER = os.getenv("LOKI_WATCH_MEMORY_FOLDER", "1").strip() not in {"0", "false", "False", "no", "NO"}
WATCH_POLL_S = float(os.getenv("LOKI_WATCH_POLL_S", "2.0"))
LOKI_MAX_SCREENSHOT_IMAGES = int(os.getenv("LOKI_MAX_SCREENSHOT_IMAGES", "4"))

# Authoritative clock in system prompt (epoch + ISO) — helps models reason about real dates/timelines.
LOKI_TIME_SYSTEM_PROMPT = os.getenv("LOKI_TIME_SYSTEM_PROMPT", "1").strip() not in {"0", "false", "False", "no", "NO"}
# macOS Calendar.app automation (JavaScript for Automation). Disable with LOKI_APPLE_CALENDAR=0.
LOKI_APPLE_CALENDAR = os.getenv("LOKI_APPLE_CALENDAR", "1").strip() not in {"0", "false", "False", "no", "NO"}
LOKI_APPLE_CALENDAR_DEFAULT = (os.getenv("LOKI_APPLE_CALENDAR_DEFAULT", "Calendar") or "Calendar").strip()
VOICE_ENABLE = os.getenv("LOKI_VOICE_ENABLE", "1").strip() not in {"0", "false", "False", "no", "NO"}
VOICE_HOTKEY = os.getenv("LOKI_VOICE_HOTKEY", "ctrl_l").strip().lower() or "ctrl_l"
VOICE_STT_MODEL = os.getenv("LOKI_VOICE_STT_MODEL", "base").strip() or "base"
VOICE_DEVICE = os.getenv("LOKI_VOICE_DEVICE", "cpu").strip() or "cpu"
VOICE_COMPUTE_TYPE = os.getenv("LOKI_VOICE_COMPUTE_TYPE", "int8").strip() or "int8"
VOICE_SAMPLE_RATE = int(os.getenv("LOKI_VOICE_SAMPLE_RATE", "16000"))
VOICE_CHANNELS = int(os.getenv("LOKI_VOICE_CHANNELS", "1"))
VOICE_MAX_SECONDS = float(os.getenv("LOKI_VOICE_MAX_SECONDS", "20"))
VOICE_MIN_SECONDS = float(os.getenv("LOKI_VOICE_MIN_SECONDS", "0.6"))
VOICE_TTS_ENABLE = os.getenv("LOKI_VOICE_TTS_ENABLE", "1").strip() not in {"0", "false", "False", "no", "NO"}
VOICE_SAY_VOICE = os.getenv("LOKI_SAY_VOICE", "").strip()
# macOS `say -r` words per minute; empty = system default (omit flag)
_say_rate_raw = os.getenv("LOKI_SAY_RATE", "").strip()
VOICE_SAY_RATE_WPM: Optional[int] = None
if _say_rate_raw:
    try:
        _sr = int(_say_rate_raw)
        if _sr > 0:
            VOICE_SAY_RATE_WPM = _sr
    except ValueError:
        pass
TTS_SETTINGS_PATH = Path(os.getenv("LOKI_TTS_SETTINGS_PATH", str(MEMORY_DIR / "tts_settings.json"))).resolve()

# TTS engine: macOS `say` (default) or local **Piper** (neural). See README + `pip install piper-tts`.
LOKI_TTS_ENGINE = os.getenv("LOKI_TTS_ENGINE", "say").strip().lower()
if LOKI_TTS_ENGINE not in ("say", "piper"):
    LOKI_TTS_ENGINE = "say"
LOKI_PIPER_BINARY = (os.getenv("LOKI_PIPER_BINARY", "piper") or "piper").strip()
_piper_model_env = os.getenv("LOKI_PIPER_MODEL", "").strip()
LOKI_PIPER_MODEL: Optional[Path] = Path(_piper_model_env).expanduser().resolve() if _piper_model_env else None
LOKI_PIPER_VOICE = (os.getenv("LOKI_PIPER_VOICE", "en_US-lessac-medium") or "en_US-lessac-medium").strip()
_piper_dd = os.getenv("LOKI_PIPER_DATA_DIR", "").strip()
LOKI_PIPER_DATA_DIR = Path(_piper_dd).expanduser().resolve() if _piper_dd else (MEMORY_DIR / "piper_voices").resolve()
try:
    LOKI_PIPER_LENGTH_SCALE = float(os.getenv("LOKI_PIPER_LENGTH_SCALE", "1.0"))
except ValueError:
    LOKI_PIPER_LENGTH_SCALE = 1.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# Piper `python -m piper` synthesis (see `python -m piper --help`). ONNX CLI path ignores noise/volume flags.
LOKI_PIPER_NOISE_SCALE = _env_float("LOKI_PIPER_NOISE_SCALE", 0.667)
LOKI_PIPER_NOISE_W_SCALE = _env_float("LOKI_PIPER_NOISE_W_SCALE", 0.8)
LOKI_PIPER_VOLUME = _env_float("LOKI_PIPER_VOLUME", 1.0)
LOKI_PIPER_SENTENCE_SILENCE = _env_float("LOKI_PIPER_SENTENCE_SILENCE", 0.0)
LOKI_PIPER_PLAYBACK_RATE = _env_float("LOKI_PIPER_PLAYBACK_RATE", 1.0)
_pspk = os.getenv("LOKI_PIPER_SPEAKER", "").strip()
LOKI_PIPER_SPEAKER_ID: Optional[int] = int(_pspk) if _pspk.isdigit() else None
_pmd = os.getenv("LOKI_PIPER_MODEL_DIR", "").strip()
LOKI_PIPER_MODEL_DIR: Optional[Path] = Path(_pmd).expanduser().resolve() if _pmd else None

LOKI_SCREENSHOT_ON_ERROR_BLANK = os.getenv("LOKI_SCREENSHOT_ON_ERROR_BLANK", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_BLANK_PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="


# -----------------------------
# Utilities
# -----------------------------

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def safe_read_text(path: Path, max_chars: int = 80_000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[Could not read {path.name}: {e}]"
    if len(data) > max_chars:
        return data[:max_chars] + "\n[...truncated...]\n"
    return data


def load_memories(folder: Path) -> Tuple[str, List[str]]:
    if not folder.exists():
        return "", []
    if not folder.is_dir():
        return "", [f"{folder} exists but is not a directory"]

    text_exts = {".txt", ".md", ".markdown", ".json", ".yaml", ".yml"}
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    text_files = sorted([p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in text_exts])
    image_files = sorted([p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in image_exts])
    if not text_files and not image_files:
        return "", []

    chunks: List[str] = []
    if text_files:
        for p in text_files:
            rel = p.relative_to(folder)
            chunks.append(f"### Memory (text): {rel}\n{safe_read_text(p)}")
    if image_files:
        manifest = "\n".join([f"- {p.relative_to(folder)}" for p in image_files])
        chunks.append(
            "### Memory (images manifest)\n"
            "These image files exist in the memory folder. Use /attach <path> if you want me to analyze one.\n"
            f"{manifest}"
        )
    return "\n\n".join(chunks), []


def guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


def build_attachment_block(path: Path, max_text_chars: int = 120_000) -> Dict[str, Any]:
    mime = guess_mime(path)
    if mime.startswith("text/") or mime in {"application/json"}:
        return {
            "type": "input_text",
            "text": f"[Attached file: {path.name}]\n{safe_read_text(path, max_chars=max_text_chars)}",
        }
    if mime == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages_text: List[str] = []
            for i, page in enumerate(reader.pages[:50]):
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                if t.strip():
                    pages_text.append(f"--- Page {i+1} ---\n{t.strip()}")
            joined = "\n\n".join(pages_text).strip()
            if not joined:
                joined = "[PDF had no extractable text.]"
            if len(joined) > max_text_chars:
                joined = joined[:max_text_chars] + "\n[...truncated...]\n"
            return {
                "type": "input_text",
                "text": f"[Attached PDF: {path.name}]\n{joined}",
            }
        except Exception as e:
            return {"type": "input_text", "text": f"[Attached PDF: {path.name}] (failed to extract text: {e})"}
    if mime.startswith("image/"):
        b64 = b64_file(path)
        return {
            "type": "input_image",
            "image_url": f"data:{mime};base64,{b64}",
            "detail": "high",
        }
    return {"type": "input_text", "text": f"[Attached file: {path.name} ({mime}) not supported for inline analysis yet]"}


def looks_like_existing_path(s: str) -> Optional[Path]:
    """
    Heuristic: if user pastes a local path (maybe with escaped spaces), treat as an attach.
    """

    raw = s.strip()
    if not raw:
        return None
    if raw.startswith("/attach "):
        return None
    if raw.startswith("~"):
        raw = str(Path(raw).expanduser())
    # Handle backslash-escaped spaces from shell copying.
    raw = raw.replace("\\ ", " ")
    # If they pasted something like: "/path/to/file.jpg " (with trailing punctuation)
    raw = raw.strip().strip('"').strip("'").strip()
    p = Path(raw)
    if not p.is_absolute():
        return None
    try:
        if p.exists() and p.is_file():
            return p
    except Exception:
        return None
    return None


def load_screen_indices() -> Dict[str, int]:
    """
    Persisted mapping for:
      left: monitor index to treat as "left screen"
      right: monitor index to treat as "right screen"
    """
    defaults = {"left": 0, "right": 1}
    try:
        if SCREEN_CONFIG_PATH.exists():
            raw = json.loads(SCREEN_CONFIG_PATH.read_text(encoding="utf-8"))
            left = int(raw.get("left", defaults["left"]))
            right = int(raw.get("right", defaults["right"]))
            return {"left": left, "right": right}
    except Exception:
        pass
    return defaults


def save_screen_indices(indices: Dict[str, int]) -> None:
    SCREEN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCREEN_CONFIG_PATH.write_text(json.dumps(indices, indent=2), encoding="utf-8")


def iter_supported_files(root: Path, *, exclude_inbox: bool = True) -> List[Path]:
    ok_exts = {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".yaml",
        ".yml",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".pdf",
    }
    out: List[Path] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue
            if p.resolve() == COMPILED_MEMORY_PATH:
                continue
            if exclude_inbox:
                # Avoid ingesting inbox files via generic folder ingest; they should be processed/moved first.
                try:
                    if INBOX_DIR in p.resolve().parents:
                        continue
                except Exception:
                    pass
            if p.suffix.lower() in ok_exts:
                out.append(p)
    except Exception:
        return []
    return out


def ensure_plugins_package(plugins_dir: Path) -> None:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    init_py = plugins_dir / "__init__.py"
    if not init_py.exists():
        init_py.write_text("# Loki plugins package\n", encoding="utf-8")


def b64_file(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


# -----------------------------
# Buttplug controller (Intiface)
# -----------------------------

class ButtplugController:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._shutdown = threading.Event()
        self._client = None
        self._connect_task_handle: Optional[asyncio.Task] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, name="buttplug-loop", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def stop(self) -> None:
        self._shutdown.set()
        if self._client and self._loop:
            try:
                self._run_coro(self._client.disconnect())
            except Exception:
                pass
        if self._loop:
            try:
                if self._connect_task_handle and not self._connect_task_handle.done():
                    self._connect_task_handle.cancel()
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._connect_task_handle = loop.create_task(self._connect_task())
            self._ready.set()
            loop.run_forever()
        finally:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.stop()
            except Exception:
                pass

    async def _connect_task(self) -> None:
        try:
            from buttplug import ButtplugClient
        except Exception as e:
            print(f"[buttplug] Import failed: {e}")
            return

        try:
            client = ButtplugClient("Loki Direct")
            await client.connect(self.ws_url)
            self._client = client
            print(f"[buttplug] Connected to Intiface at {self.ws_url}")
        except Exception as e:
            print(f"[buttplug] Connection failed: {e}")
            return

        while not self._shutdown.is_set():
            await asyncio.sleep(0.25)

        try:
            if self._client:
                await self._client.disconnect()
        except Exception:
            pass
        try:
            if self._loop:
                self._loop.stop()
        except Exception:
            pass

    def _run_coro(self, coro) -> Any:
        if not self._loop:
            raise RuntimeError("Buttplug loop not started")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=30)

    def status(self) -> str:
        if not self._client:
            return "Not connected to Intiface. Is Intiface running (Start Server) on ws://127.0.0.1:12345?"
        return f"Connected to Intiface at {self.ws_url}."

    def scan(self, seconds: int = 6) -> str:
        seconds = int(max(1, min(30, seconds)))
        if not self._client:
            return "Not connected."

        async def _scan():
            await self._client.start_scanning()
            await asyncio.sleep(seconds)
            await self._client.stop_scanning()
            return True

        try:
            self._run_coro(_scan())
        except Exception as e:
            return f"Scan failed: {e}"
        return f"Scan complete ({seconds}s)."

    def list_devices(self) -> str:
        if not self._client:
            return "Not connected."
        devices = getattr(self._client, "devices", {}) or {}
        if not devices:
            return "No devices detected. Try scan_devices."
        lines = []
        for dev_id, dev in devices.items():
            lines.append(f"- {dev_id}: {dev.name}")
        return "Devices:\n" + "\n".join(lines)

    def _find_device_by_name_contains(self, needle: str):
        if not self._client:
            return None
        needle = needle.lower().strip()
        for _dev_id, dev in (getattr(self._client, "devices", {}) or {}).items():
            if needle in (dev.name or "").lower():
                return dev
        return None

    def vibrate(self, device_name_contains: str = "nora", intensity: float = 0.2, duration_s: int = 8) -> str:
        intensity = clamp01(float(intensity))
        duration_s = int(max(0, min(3600, duration_s)))
        if not self._client:
            return "Not connected."

        dev = self._find_device_by_name_contains(device_name_contains)
        if not dev:
            return f"Device not found matching '{device_name_contains}'. Use list_devices."

        async def _do():
            from buttplug import DeviceOutputCommand, OutputType

            cmd = DeviceOutputCommand(OutputType.VIBRATE, intensity)
            await dev.run_output(cmd)
            if duration_s > 0:
                await asyncio.sleep(duration_s)
                await dev.stop()
            return True

        try:
            self._run_coro(_do())
        except Exception as e:
            return f"Vibrate failed: {e}"
        if duration_s > 0:
            return f"Vibrated '{dev.name}' at {intensity:.2f} for {duration_s}s."
        return f"Vibrating '{dev.name}' at {intensity:.2f} (until stopped)."

    def stop_device(self, device_name_contains: str = "nora") -> str:
        if not self._client:
            return "Not connected."
        dev = self._find_device_by_name_contains(device_name_contains)
        if not dev:
            return f"Device not found matching '{device_name_contains}'."

        async def _do():
            await dev.stop()
            return True

        try:
            self._run_coro(_do())
        except Exception as e:
            return f"Stop failed: {e}"
        return f"Stopped '{dev.name}'."


# -----------------------------
# Screen control (pyautogui)
# -----------------------------

class ScreenController:
    def __init__(self):
        try:
            import pyautogui  # noqa: F401
        except Exception as e:
            raise RuntimeError(f"pyautogui import failed: {e}")

    def _get_mss(self):
        import mss  # type: ignore

        return mss.mss()

    def monitors(self) -> List[Dict[str, Any]]:
        """
        Return monitor list with stable indices for user/model selection.
        Indices are 0..N-1 corresponding to mss.monitors[1:].
        """
        try:
            with self._get_mss() as sct:
                mons = []
                for i, m in enumerate(sct.monitors[1:]):
                    mons.append(
                        {
                            "index": i,
                            "left": int(m.get("left", 0)),
                            "top": int(m.get("top", 0)),
                            "width": int(m.get("width", 0)),
                            "height": int(m.get("height", 0)),
                            "name": m.get("name") or f"monitor_{i}",
                        }
                    )
                if mons:
                    return mons
        except Exception:
            pass

        # Fallback: treat the primary screen as a single monitor.
        import pyautogui

        w, h = pyautogui.size()
        return [{"index": 0, "left": 0, "top": 0, "width": int(w), "height": int(h), "name": "primary"}]

    def _capture_monitor_png_bytes(self, monitor_index: int, max_dim: int = 1600) -> bytes:
        """
        Capture a single monitor to PNG bytes, optionally downscaling for smaller payloads.
        """
        max_dim = int(max(256, min(4096, max_dim)))
        from io import BytesIO

        from PIL import Image

        mi = int(monitor_index)
        if mi < 0:
            mi = 0

        try:
            with self._get_mss() as sct:
                # mss uses index 1..N-1 for real monitors; 0 is "all monitors"
                mons = sct.monitors[1:]
                if mons:
                    if mi >= len(mons):
                        mi = len(mons) - 1

                    mon = mons[mi]
                    img = sct.grab(mon)  # BGRA
                    # Create RGB image from raw bytes
                    pil_img = Image.frombytes("RGB", img.size, img.rgb)
                else:
                    raise RuntimeError("mss returned no monitors")
        except Exception:
            # Fallback: capture using pyautogui (typically primary screen only).
            import pyautogui

            pil_img = pyautogui.screenshot()

            # Downscale to keep payload size reasonable
        # Downscale to keep payload size reasonable
        w, h = pil_img.size
        scale = min(1.0, float(max_dim) / max(w, h))
        if scale < 1.0:
            pil_img = pil_img.resize((int(w * scale), int(h * scale)))

        out = BytesIO()
        pil_img.save(out, format="PNG")
        return out.getvalue()

    def screenshot_monitor_base64(self, monitor_index: int, max_dim: int = 1600) -> str:
        try:
            b = self._capture_monitor_png_bytes(monitor_index, max_dim=max_dim)
            b64 = base64.b64encode(b).decode("ascii")
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            if LOKI_SCREENSHOT_ON_ERROR_BLANK:
                return _BLANK_PNG_DATA_URL
            return f"[screenshot_monitor_base64 failed: {e}]"

    def screenshot_monitor(self, monitor_index: int, max_dim: int = 1600) -> str:
        b = self._capture_monitor_png_bytes(monitor_index, max_dim=max_dim)
        path = Path(tempfile.mkstemp(prefix="loki_m", suffix=".png")[1]).resolve()
        path.write_bytes(b)
        return str(path)

    def screenshot_all_monitors_base64(self, max_dim: int = 1600) -> Dict[str, Any]:
        images: List[str] = []
        mons = self.monitors()
        for mi in range(len(mons)):
            images.append(self.screenshot_monitor_base64(mi, max_dim=max_dim))
        return {"images": images, "count": len(images)}

    def click(self, x: int, y: int, button: str = "left") -> str:
        import pyautogui

        x = int(x)
        y = int(y)
        if button not in {"left", "right", "middle"}:
            button = "left"
        pyautogui.moveTo(x, y, duration=0.15)
        pyautogui.click(button=button)
        return f"Clicked {button} at ({x}, {y})."

    def type(self, text: str, interval_s: float = 0.03) -> str:
        import pyautogui

        interval_s = float(max(0.0, min(0.5, interval_s)))
        pyautogui.write(str(text), interval=interval_s)
        return f"Typed {len(text)} chars."

    def hotkey(self, *keys: str) -> str:
        import pyautogui

        keys = tuple(k for k in keys if k)
        if not keys:
            return "No keys provided."
        pyautogui.hotkey(*keys)
        return f"Pressed hotkey: {' + '.join(keys)}"

    def screenshot(self) -> str:
        import pyautogui

        path = Path(tempfile.mkstemp(prefix="loki_", suffix=".png")[1]).resolve()
        pyautogui.screenshot(str(path))
        return str(path)


# -----------------------------
# Voice (hold-to-speak + TTS)
# -----------------------------


def parse_piper_voice_setting(
    stored: str,
    *,
    env_onnx: Optional[Path],
    env_voice_default: str,
) -> Tuple[Optional[Path], str]:
    """
    `stored` is either a path to a `.onnx` file (legacy Piper binary) or a Piper **voice id**
    for `python -m piper -m <id>` (e.g. en_US-lessac-medium).
    """

    s = (stored or "").strip()
    if s:
        if s.lower().endswith(".onnx"):
            p = Path(s).expanduser().resolve()
            if p.is_file():
                return p, ""
            return None, ""
        return None, s
    if env_onnx is not None and env_onnx.is_file():
        return env_onnx, ""
    return None, (env_voice_default or "en_US-lessac-medium").strip()


def list_macos_say_voices() -> List[Dict[str, str]]:
    """Parse `say -v ?` on macOS. Returns [{id, locale, sample}, ...]."""

    if sys.platform != "darwin":
        return []
    try:
        proc = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        txt = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception:
        return []
    out: List[Dict[str, str]] = []
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.+?)\s{2,}(\S+)\s+#\s*(.*)$", line)
        if m:
            out.append(
                {
                    "id": m.group(1).strip(),
                    "locale": m.group(2).strip(),
                    "sample": m.group(3).strip(),
                }
            )
            continue
        parts = re.split(r"\s{2,}", line, maxsplit=2)
        if not parts or not parts[0].strip():
            continue
        vid = parts[0].strip()
        loc = parts[1].strip() if len(parts) > 1 else ""
        samp = ""
        if len(parts) > 2:
            samp = parts[2].lstrip("# ").strip()
        out.append({"id": vid, "locale": loc, "sample": samp})
    # Stable sort: English locales first-ish, then name
    try:
        out.sort(key=lambda x: (not str(x.get("locale", "")).lower().startswith("en"), x.get("id", "").lower()))
    except Exception:
        out.sort(key=lambda x: x.get("id", "").lower())
    return out


def load_tts_settings_merged(path: Optional[Path] = None) -> Dict[str, Any]:
    """Merge JSON file (if any) with env defaults."""

    p = (path or TTS_SETTINGS_PATH).resolve()
    raw: Dict[str, Any] = {}
    try:
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
    except Exception:
        raw = {}
    voice = raw.get("say_voice")
    if not isinstance(voice, str) or not voice.strip():
        voice = VOICE_SAY_VOICE
    rate = raw.get("say_rate_wpm")
    rate_out: Optional[int]
    if rate is None or rate == "":
        rate_out = VOICE_SAY_RATE_WPM
    else:
        try:
            r = int(rate)
            rate_out = r if r > 0 else None
        except (TypeError, ValueError):
            rate_out = VOICE_SAY_RATE_WPM
    te = raw.get("tts_enable")
    if isinstance(te, bool):
        tts_enable = te
    else:
        tts_enable = VOICE_TTS_ENABLE

    eng = raw.get("tts_engine")
    if isinstance(eng, str) and eng.strip().lower() in ("say", "piper"):
        tts_engine = eng.strip().lower()
    else:
        tts_engine = LOKI_TTS_ENGINE

    pv_raw = raw.get("piper_voice")
    if isinstance(pv_raw, str) and pv_raw.strip():
        piper_voice = pv_raw.strip()
    else:
        if LOKI_PIPER_MODEL is not None and LOKI_PIPER_MODEL.is_file():
            piper_voice = str(LOKI_PIPER_MODEL)
        else:
            piper_voice = LOKI_PIPER_VOICE

    pdd_raw = raw.get("piper_data_dir")
    if isinstance(pdd_raw, str) and pdd_raw.strip():
        piper_data_dir = Path(pdd_raw).expanduser().resolve()
    else:
        piper_data_dir = LOKI_PIPER_DATA_DIR

    pbin_raw = raw.get("piper_binary")
    if isinstance(pbin_raw, str) and pbin_raw.strip():
        piper_binary = pbin_raw.strip()
    else:
        piper_binary = LOKI_PIPER_BINARY

    pls = raw.get("piper_length_scale", LOKI_PIPER_LENGTH_SCALE)
    try:
        piper_length_scale = float(pls)
    except (TypeError, ValueError):
        piper_length_scale = LOKI_PIPER_LENGTH_SCALE

    ps = raw.get("piper_speaker_id")
    piper_speaker_id: Optional[int]
    if ps is None or ps == "":
        piper_speaker_id = LOKI_PIPER_SPEAKER_ID
    else:
        try:
            piper_speaker_id = int(ps)
        except (TypeError, ValueError):
            piper_speaker_id = LOKI_PIPER_SPEAKER_ID

    try:
        piper_noise_scale = float(raw.get("piper_noise_scale", LOKI_PIPER_NOISE_SCALE))
    except (TypeError, ValueError):
        piper_noise_scale = LOKI_PIPER_NOISE_SCALE
    try:
        piper_noise_w_scale = float(raw.get("piper_noise_w_scale", LOKI_PIPER_NOISE_W_SCALE))
    except (TypeError, ValueError):
        piper_noise_w_scale = LOKI_PIPER_NOISE_W_SCALE
    try:
        piper_volume = float(raw.get("piper_volume", LOKI_PIPER_VOLUME))
    except (TypeError, ValueError):
        piper_volume = LOKI_PIPER_VOLUME
    try:
        piper_sentence_silence = float(raw.get("piper_sentence_silence", LOKI_PIPER_SENTENCE_SILENCE))
    except (TypeError, ValueError):
        piper_sentence_silence = LOKI_PIPER_SENTENCE_SILENCE
    try:
        piper_playback_rate = float(raw.get("piper_playback_rate", LOKI_PIPER_PLAYBACK_RATE))
    except (TypeError, ValueError):
        piper_playback_rate = LOKI_PIPER_PLAYBACK_RATE

    piper_onnx, piper_voice_module = parse_piper_voice_setting(
        piper_voice,
        env_onnx=LOKI_PIPER_MODEL,
        env_voice_default=LOKI_PIPER_VOICE,
    )

    return {
        "say_voice": str(voice).strip(),
        "say_rate_wpm": rate_out,
        "tts_enable": bool(tts_enable),
        "tts_engine": tts_engine,
        "piper_voice": piper_voice,
        "piper_onnx": piper_onnx,
        "piper_voice_module": piper_voice_module,
        "piper_data_dir": piper_data_dir,
        "piper_binary": piper_binary,
        "piper_length_scale": piper_length_scale,
        "piper_speaker_id": piper_speaker_id,
        "piper_noise_scale": piper_noise_scale,
        "piper_noise_w_scale": piper_noise_w_scale,
        "piper_volume": piper_volume,
        "piper_sentence_silence": piper_sentence_silence,
        "piper_playback_rate": piper_playback_rate,
    }


def save_tts_settings_file(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = (path or TTS_SETTINGS_PATH).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class VoiceManager:
    def __init__(
        self,
        *,
        hotkey_char: str,
        stt_model: str,
        device: str,
        compute_type: str,
        sample_rate: int,
        channels: int,
        max_seconds: float,
        min_seconds: float,
        tts_enable: bool,
        say_voice: str,
        say_rate_wpm: Optional[int] = None,
        tts_engine: str = "say",
        piper_voice: str = "",
        piper_onnx: Optional[Path] = None,
        piper_voice_module: str = "",
        piper_data_dir: Optional[Path] = None,
        piper_binary: str = "piper",
        piper_length_scale: float = 1.0,
        piper_speaker_id: Optional[int] = None,
        piper_noise_scale: float = 0.667,
        piper_noise_w_scale: float = 0.8,
        piper_volume: float = 1.0,
        piper_sentence_silence: float = 0.0,
        piper_playback_rate: float = 1.0,
        stt_task_fn: Callable[[str], None],
    ):
        self.hotkey_spec = str(hotkey_char).strip().lower()
        self.stt_model = stt_model
        self.device = device
        self.compute_type = compute_type
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.max_seconds = float(max_seconds)
        self.min_seconds = float(min_seconds)
        self.tts_enable = bool(tts_enable)
        self.say_voice = (say_voice or "").strip()
        try:
            _sr = int(say_rate_wpm) if say_rate_wpm is not None else 0
        except (TypeError, ValueError):
            _sr = 0
        self.say_rate_wpm: Optional[int] = _sr if _sr > 0 else None
        self.stt_task_fn = stt_task_fn

        self.tts_engine = tts_engine if tts_engine in ("say", "piper") else "say"
        self.piper_voice = (piper_voice or "").strip()
        self.piper_onnx = piper_onnx
        self.piper_voice_module = (piper_voice_module or "").strip()
        self.piper_data_dir = piper_data_dir
        self.piper_binary = (piper_binary or "piper").strip()
        try:
            self.piper_length_scale = float(piper_length_scale)
        except (TypeError, ValueError):
            self.piper_length_scale = 1.0
        self.piper_speaker_id = piper_speaker_id
        try:
            self.piper_noise_scale = float(piper_noise_scale)
        except (TypeError, ValueError):
            self.piper_noise_scale = 0.667
        try:
            self.piper_noise_w_scale = float(piper_noise_w_scale)
        except (TypeError, ValueError):
            self.piper_noise_w_scale = 0.8
        try:
            self.piper_volume = float(piper_volume)
        except (TypeError, ValueError):
            self.piper_volume = 1.0
        try:
            self.piper_sentence_silence = float(piper_sentence_silence)
        except (TypeError, ValueError):
            self.piper_sentence_silence = 0.0
        try:
            self.piper_playback_rate = float(piper_playback_rate)
        except (TypeError, ValueError):
            self.piper_playback_rate = 1.0

        self._tts_settings_lock = threading.Lock()

        self._listening = False
        self._recording = False
        self._stream = None
        self._frames = []
        self._frames_lock = threading.Lock()
        self._stop_timer: Optional[threading.Timer] = None

        self._stt_model_obj = None
        self._stt_model_lock = threading.Lock()

        self._tts_proc: Optional[subprocess.Popen] = None
        self._tts_lock = threading.Lock()

        self._kb_listener = None

    def tts_settings_snapshot(self) -> Dict[str, Any]:
        with self._tts_settings_lock:
            return {
                "say_voice": self.say_voice,
                "say_rate_wpm": self.say_rate_wpm,
                "tts_enable": self.tts_enable,
                "tts_engine": self.tts_engine,
                "piper_voice": self.piper_voice,
                "piper_data_dir": str(self.piper_data_dir) if self.piper_data_dir else "",
                "piper_binary": self.piper_binary,
                "piper_length_scale": self.piper_length_scale,
                "piper_speaker_id": self.piper_speaker_id,
                "piper_noise_scale": self.piper_noise_scale,
                "piper_noise_w_scale": self.piper_noise_w_scale,
                "piper_volume": self.piper_volume,
                "piper_sentence_silence": self.piper_sentence_silence,
                "piper_playback_rate": self.piper_playback_rate,
            }

    def hydrate_tts_from_merged(self, m: Dict[str, Any]) -> None:
        """
        Replace in-memory TTS fields from `load_tts_settings_merged()` so GET /settings matches
        `memories/tts_settings.json` (avoids stale UI / wrong voice after saves or external edits).
        """

        with self._tts_settings_lock:
            self.say_voice = str(m.get("say_voice") or "").strip()
            self.say_rate_wpm = m.get("say_rate_wpm")
            self.tts_enable = bool(m.get("tts_enable", True))
            te = str(m.get("tts_engine") or "say").strip().lower()
            self.tts_engine = te if te in ("say", "piper") else "say"
            self.piper_voice = str(m.get("piper_voice") or "").strip()
            po = m.get("piper_onnx")
            self.piper_onnx = po if isinstance(po, Path) else None
            self.piper_voice_module = str(m.get("piper_voice_module") or "").strip()
            pdd = m.get("piper_data_dir")
            if isinstance(pdd, Path):
                self.piper_data_dir = pdd
            elif isinstance(pdd, str) and pdd.strip():
                self.piper_data_dir = Path(pdd).expanduser().resolve()
            else:
                self.piper_data_dir = LOKI_PIPER_DATA_DIR
            self.piper_binary = str(m.get("piper_binary") or "piper").strip() or "piper"
            try:
                self.piper_length_scale = float(m.get("piper_length_scale", 1.0))
            except (TypeError, ValueError):
                self.piper_length_scale = 1.0
            ps = m.get("piper_speaker_id")
            if ps is None or ps == "":
                self.piper_speaker_id = None
            else:
                try:
                    self.piper_speaker_id = int(ps)
                except (TypeError, ValueError):
                    self.piper_speaker_id = None
            try:
                self.piper_noise_scale = float(m.get("piper_noise_scale", LOKI_PIPER_NOISE_SCALE))
            except (TypeError, ValueError):
                self.piper_noise_scale = LOKI_PIPER_NOISE_SCALE
            try:
                self.piper_noise_w_scale = float(m.get("piper_noise_w_scale", LOKI_PIPER_NOISE_W_SCALE))
            except (TypeError, ValueError):
                self.piper_noise_w_scale = LOKI_PIPER_NOISE_W_SCALE
            try:
                self.piper_volume = float(m.get("piper_volume", LOKI_PIPER_VOLUME))
            except (TypeError, ValueError):
                self.piper_volume = LOKI_PIPER_VOLUME
            try:
                self.piper_sentence_silence = float(m.get("piper_sentence_silence", LOKI_PIPER_SENTENCE_SILENCE))
            except (TypeError, ValueError):
                self.piper_sentence_silence = LOKI_PIPER_SENTENCE_SILENCE
            try:
                self.piper_playback_rate = float(m.get("piper_playback_rate", LOKI_PIPER_PLAYBACK_RATE))
            except (TypeError, ValueError):
                self.piper_playback_rate = LOKI_PIPER_PLAYBACK_RATE

    def apply_tts_request_fields(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply only keys present in `data` (e.g. JSON body). `say_rate_wpm: null` clears to macOS default.
        """

        with self._tts_settings_lock:
            if "say_voice" in data:
                self.say_voice = str(data.get("say_voice") or "").strip()
            if "say_rate_wpm" in data:
                v = data.get("say_rate_wpm")
                if v in (None, ""):
                    self.say_rate_wpm = None
                else:
                    try:
                        r = int(v)
                        self.say_rate_wpm = r if r > 0 else None
                    except (TypeError, ValueError):
                        pass
            if "tts_enable" in data:
                self.tts_enable = bool(data.get("tts_enable"))
            if "tts_engine" in data:
                te = str(data.get("tts_engine") or "").strip().lower()
                if te in ("say", "piper"):
                    self.tts_engine = te
            if "piper_voice" in data:
                self.piper_voice = str(data.get("piper_voice") or "").strip()
                self.piper_onnx, self.piper_voice_module = parse_piper_voice_setting(
                    self.piper_voice,
                    env_onnx=LOKI_PIPER_MODEL,
                    env_voice_default=LOKI_PIPER_VOICE,
                )
            if "piper_data_dir" in data:
                pdd = str(data.get("piper_data_dir") or "").strip()
                self.piper_data_dir = Path(pdd).expanduser().resolve() if pdd else LOKI_PIPER_DATA_DIR
            if "piper_binary" in data:
                self.piper_binary = str(data.get("piper_binary") or "piper").strip() or "piper"
            if "piper_length_scale" in data:
                try:
                    self.piper_length_scale = float(data.get("piper_length_scale"))
                except (TypeError, ValueError):
                    pass
            if "piper_speaker_id" in data:
                ps = data.get("piper_speaker_id")
                if ps in (None, ""):
                    self.piper_speaker_id = None
                else:
                    try:
                        self.piper_speaker_id = int(ps)
                    except (TypeError, ValueError):
                        pass
            if "piper_noise_scale" in data:
                try:
                    self.piper_noise_scale = float(data.get("piper_noise_scale"))
                except (TypeError, ValueError):
                    pass
            if "piper_noise_w_scale" in data:
                try:
                    self.piper_noise_w_scale = float(data.get("piper_noise_w_scale"))
                except (TypeError, ValueError):
                    pass
            if "piper_volume" in data:
                try:
                    self.piper_volume = float(data.get("piper_volume"))
                except (TypeError, ValueError):
                    pass
            if "piper_sentence_silence" in data:
                try:
                    self.piper_sentence_silence = float(data.get("piper_sentence_silence"))
                except (TypeError, ValueError):
                    pass
            if "piper_playback_rate" in data:
                try:
                    self.piper_playback_rate = float(data.get("piper_playback_rate"))
                except (TypeError, ValueError):
                    pass
            return {
                "say_voice": self.say_voice,
                "say_rate_wpm": self.say_rate_wpm,
                "tts_enable": self.tts_enable,
                "tts_engine": self.tts_engine,
                "piper_voice": self.piper_voice,
                "piper_data_dir": str(self.piper_data_dir) if self.piper_data_dir else "",
                "piper_binary": self.piper_binary,
                "piper_length_scale": self.piper_length_scale,
                "piper_speaker_id": self.piper_speaker_id,
                "piper_noise_scale": self.piper_noise_scale,
                "piper_noise_w_scale": self.piper_noise_w_scale,
                "piper_volume": self.piper_volume,
                "piper_sentence_silence": self.piper_sentence_silence,
                "piper_playback_rate": self.piper_playback_rate,
            }

    def _stop_tts_proc(self) -> None:
        try:
            if self._tts_proc and self._tts_proc.poll() is None:
                self._tts_proc.terminate()
        except Exception:
            pass

    def _play_say_popen(self, text: str, *, voice: str, rate: Optional[int]) -> None:
        with self._tts_lock:
            self._stop_tts_proc()
            cmd = ["say"]
            if voice:
                cmd += ["-v", voice]
            if rate and int(rate) > 0:
                cmd += ["-r", str(int(rate))]
            cmd += [text]
            try:
                self._tts_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                self._tts_proc = None

    def _tts_say(self, text: str) -> None:
        self._dispatch_tts(text, preview=False)

    def speak(self, text: str) -> None:
        # External hook used by our chat logic.
        self._tts_say(text)

    def speak_preview(self, text: str) -> None:
        """Play sample audio even when `tts_enable` is off (UI “test voice” button)."""

        self._dispatch_tts(text, preview=True)

    def _dispatch_tts(self, text: str, *, preview: bool) -> None:
        if not preview and not self.tts_enable:
            return
        text = (text or "").strip()
        if not text:
            return

        with self._tts_settings_lock:
            engine = self.tts_engine
            voice = self.say_voice
            rate = self.say_rate_wpm
            onnx = self.piper_onnx
            pvm = self.piper_voice_module
            pdd = self.piper_data_dir
            pbin = self.piper_binary
            plen = self.piper_length_scale
            pspk = self.piper_speaker_id
            pns = self.piper_noise_scale
            pnw = self.piper_noise_w_scale
            pvol = self.piper_volume
            psil = self.piper_sentence_silence
            pplay = self.piper_playback_rate

        if engine != "piper":
            self._play_say_popen(text, voice=voice, rate=rate)
            return

        def worker() -> None:
            import loki_piper_tts as lpt

            wav = lpt.synthesize_piper_wav(
                text,
                onnx_path=onnx,
                voice_module=pvm,
                data_dir=pdd,
                piper_binary=pbin,
                length_scale=plen,
                noise_scale=pns if onnx is None else None,
                noise_w_scale=pnw if onnx is None else None,
                volume=pvol if onnx is None else None,
                sentence_silence=psil if onnx is None else None,
                speaker_id=pspk,
            )
            if not wav:
                print(
                    "[tts] Piper synthesis failed; falling back to macOS say "
                    f"(voice_module={pvm!r} onnx={onnx} data_dir={pdd}). "
                    "Check terminal above for [tts] Piper failed … details.",
                    flush=True,
                )
                self._play_say_popen(text, voice=voice, rate=rate)
                return
            proc_local: Optional[subprocess.Popen] = None
            try:
                with self._tts_lock:
                    self._stop_tts_proc()
                    try:
                        proc_local = lpt.play_wav_async(wav, playback_rate=pplay)
                        self._tts_proc = proc_local
                    except Exception as e:
                        print(f"[tts] Piper play failed ({e}); falling back to say", flush=True)
                if proc_local is None:
                    self._play_say_popen(text, voice=voice, rate=rate)
                else:
                    proc_local.wait()
            finally:
                try:
                    wav.unlink(missing_ok=True)  # type: ignore[attr-defined]
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _ensure_stt_model(self):
        with self._stt_model_lock:
            if self._stt_model_obj is not None:
                return self._stt_model_obj
            from faster_whisper import WhisperModel

            self._stt_model_obj = WhisperModel(
                self.stt_model,
                device=self.device,
                compute_type=self.compute_type,
            )
            return self._stt_model_obj

    def _start_recording(self) -> None:
        if self._recording:
            return
        # Stop any ongoing TTS so it doesn't talk over you.
        with self._tts_lock:
            try:
                if self._tts_proc and self._tts_proc.poll() is None:
                    self._tts_proc.terminate()
            except Exception:
                pass

        import numpy as np
        import sounddevice as sd

        self._frames = []
        self._recording = True
        print("[voice] start_recording")
        started_at = time.time()

        def callback(indata, frames, time_info, status):
            if status:
                # Non-fatal audio warnings.
                pass
            # Keep raw PCM samples.
            self._frames.append(np.copy(indata))

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()

        def auto_stop():
            # Ensure we stop even if user holds too long.
            if not self._recording:
                return
            try:
                self._stop_recording()
            except Exception:
                pass

        self._stop_timer = threading.Timer(self.max_seconds, auto_stop)
        self._stop_timer.daemon = True
        self._stop_timer.start()

    def _stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        print("[voice] stop_recording")
        if self._stop_timer:
            try:
                self._stop_timer.cancel()
            except Exception:
                pass
            self._stop_timer = None

        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stream = None

        with self._frames_lock:
            frames = self._frames
        if not frames:
            return

        import numpy as np

        audio = np.concatenate(frames, axis=0)
        # audio shape: (num_samples, channels)
        if audio.ndim == 1:
            audio = audio.reshape(-1, 1)
        num_samples = audio.shape[0]
        dur_s = num_samples / float(self.sample_rate)
        print(f"[voice] utterance_duration_s={dur_s:.2f} frames={num_samples}")
        if dur_s < self.min_seconds:
            print("[voice] Ignored too-short utterance.")
            return

        # Write a temporary WAV file (whisper/whisper-faster reads paths).
        wav_path = Path(tempfile.mkstemp(prefix="loki_voice_", suffix=".wav")[1]).resolve()
        import wave

        try:
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)  # int16
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio.astype(np.int16).tobytes())
        except Exception:
            try:
                wav_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
            except Exception:
                pass
            print("[voice] Failed to write WAV.")
            return

        # STT in background to avoid blocking keyboard listener.
        threading.Thread(target=self._transcribe_and_dispatch, args=(wav_path,), daemon=True).start()

    def _transcribe_and_dispatch(self, wav_path: Path) -> None:
        try:
            print("[voice] Transcribing...")
            model = self._ensure_stt_model()
            segments, info = model.transcribe(
                str(wav_path),
                language=None,
                vad_filter=False,
            )
            text = " ".join([seg.text.strip() for seg in segments if getattr(seg, "text", None)])
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                print(f"[voice] Heard: {text}")
                self.stt_task_fn(text)
            else:
                print("[voice] No speech detected.")
        except Exception as e:
            print(f"[voice] STT failed: {e}")
        finally:
            try:
                wav_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
            except Exception:
                pass

    def start(self) -> None:
        if self._listening:
            return
        self._listening = True

        from pynput import keyboard
        Key = keyboard.Key

        def matches_hotkey(key_obj) -> bool:
            # Character hotkey (single letter)
            if hasattr(key_obj, "char") and key_obj.char:
                return self.hotkey_spec == str(key_obj.char).lower()[:1]
            # Special keys
            if self.hotkey_spec in {"caps_lock", "capslock"}:
                return key_obj == Key.caps_lock
            if self.hotkey_spec in {"ctrl", "control", "ctrl_l"}:
                return key_obj == Key.ctrl_l
            if self.hotkey_spec in {"ctrl_r"}:
                return key_obj == Key.ctrl_r
            if self.hotkey_spec in {"shift", "shift_l"}:
                return key_obj == Key.shift_l
            if self.hotkey_spec in {"shift_r"}:
                return key_obj == Key.shift_r
            if self.hotkey_spec in {"alt", "alt_l"}:
                return key_obj == Key.alt_l
            if self.hotkey_spec in {"alt_r"}:
                return key_obj == Key.alt_r
            return False

        def should_toggle_on_press() -> bool:
            # Caps lock is not a true "hold-to-speak" key; treat it as toggle.
            return self.hotkey_spec in {"caps_lock", "capslock"}

        def on_press(key):
            try:
                if matches_hotkey(key):
                    if should_toggle_on_press():
                        # Toggle recording.
                        if self._recording:
                            self._stop_recording()
                        else:
                            self._start_recording()
                    else:
                        self._start_recording()
            except Exception:
                pass

        def on_release(key):
            try:
                if matches_hotkey(key):
                    if should_toggle_on_press():
                        return
                    self._stop_recording()
            except Exception:
                pass

        self._kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._kb_listener.daemon = True
        self._kb_listener.start()

    def stop(self) -> None:
        self._listening = False
        try:
            if self._kb_listener:
                self._kb_listener.stop()
        except Exception:
            pass
        try:
            self._stop_recording()
        except Exception:
            pass

    # UI-friendly wrappers (no global hotkey required)
    def start_recording(self) -> None:
        self._start_recording()

    def stop_recording(self) -> None:
        self._stop_recording()

    def is_recording(self) -> bool:
        return bool(self._recording)


# -----------------------------
# Tools & Plugins
# -----------------------------

ToolFn = Callable[..., Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: ToolFn


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    # Plugin-friendly compatibility helpers
    def append(self, tool_dict: Dict[str, Any]) -> None:
        """
        Accept a simple dict tool format, e.g.:
          {"name": "...", "description": "...", "function": callable, "parameters": {...}}
        """

        name = str(tool_dict.get("name") or "").strip()
        if not name:
            raise ValueError("tool_dict missing 'name'")
        description = str(tool_dict.get("description") or "").strip() or "Plugin tool."
        fn = tool_dict.get("fn") or tool_dict.get("function")
        if not callable(fn):
            raise ValueError("tool_dict missing callable 'function'/'fn'")
        parameters = tool_dict.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}, "additionalProperties": True}
        self.register(ToolSpec(name=name, description=description, parameters=parameters, fn=fn))

    def add_tool(self, name: str, description: str, fn: Callable[..., Any], parameters: Optional[Dict[str, Any]] = None) -> None:
        self.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters or {"type": "object", "properties": {}, "additionalProperties": True},
                fn=fn,
            )
        )

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_specs_for_model(self) -> List[Dict[str, Any]]:
        out = []
        for t in self._tools.values():
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
            )
        return out

    def list_names(self) -> List[str]:
        return sorted(self._tools.keys())


def load_plugins(plugins_dir: Path, tools: ToolRegistry) -> List[str]:
    ensure_plugins_package(plugins_dir)
    msgs: List[str] = []

    sys.path.insert(0, str(plugins_dir.parent))
    pkg_name = plugins_dir.name

    for py in sorted(plugins_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        mod_name = f"{pkg_name}.{py.stem}"
        try:
            mod = importlib.import_module(mod_name)
            importlib.reload(mod)
            _register_plugin(mod, tools)
            msgs.append(f"Loaded plugin: {py.name}")
        except Exception as e:
            msgs.append(f"Failed plugin {py.name}: {e}")
    return msgs


def _register_plugin(mod: ModuleType, tools: ToolRegistry) -> None:
    fn = getattr(mod, "register", None)
    if not callable(fn):
        return
    fn(tools)


# -----------------------------
# xAI chat client (tool calling loop)
# -----------------------------

class XAIClient:
    def __init__(self, api_key: str, endpoint: str, model: str, timeout_s: float = 60.0):
        if not api_key:
            raise RuntimeError("XAI_API_KEY not set.")
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.timeout_s = timeout_s

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 900,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout_s)
        if resp.status_code != 200:
            raise RuntimeError(f"xAI API error {resp.status_code}: {resp.text}")
        return resp.json()

    def embed(self, texts: List[str], model: str, endpoint: str) -> List[List[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload: Dict[str, Any] = {"model": model, "input": texts}
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=self.timeout_s)
        if resp.status_code != 200:
            raise RuntimeError(f"xAI embeddings error {resp.status_code}: {resp.text}")
        data = resp.json()
        items = data.get("data") or []
        out: List[List[float]] = []
        for it in items:
            out.append(it.get("embedding") or [])
        return out


def extract_assistant_message(resp: Dict[str, Any]) -> Dict[str, Any]:
    choices = resp.get("choices") or []
    if not choices:
        return {"role": "assistant", "content": "[No response choices]"}
    msg = choices[0].get("message") or {}
    if not msg:
        return {"role": "assistant", "content": "[Empty message]"}
    return msg


def extract_responses_text(resp_json: Dict[str, Any]) -> str:
    out: List[str] = []
    for item in resp_json.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                t = content.get("text") or ""
                if t:
                    out.append(str(t))
    if out:
        return "\n".join(out).strip()
    return ""


def analyze_images_with_xai_responses(
    api_key: str,
    image_data_urls: List[str],
    prompt: str,
    *,
    max_output_tokens: int = 320,
    timeout_s: float = 120.0,
) -> str:
    """
    Use xAI Responses API for image understanding.
    We feed the images and prompt as `input` content parts.
    """
    if not image_data_urls:
        return ""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    content_parts: List[Dict[str, Any]] = []
    for url in image_data_urls[:LOKI_MAX_SCREENSHOT_IMAGES]:
        content_parts.append({"type": "input_image", "image_url": url, "detail": "high"})
    content_parts.append({"type": "input_text", "text": prompt})
    payload = {
        "model": XAI_VISION_MODEL,
        "input": [{"role": "user", "content": content_parts}],
        "max_output_tokens": int(max_output_tokens),
    }
    resp = requests.post(XAI_RESPONSES_ENDPOINT, headers=headers, json=payload, timeout=timeout_s)
    if resp.status_code != 200:
        return f"[image analysis failed {resp.status_code}] {resp.text[:500]}"
    return extract_responses_text(resp.json()) or "[image analysis returned no text]"


def extract_image_data_urls(tool_result: str) -> List[str]:
    """
    Parse data URLs like: data:image/png;base64,...
    from tool results.
    """
    s = str(tool_result).strip()
    urls: List[str] = []
    if s.startswith("data:image/"):
        urls.append(s)
        return urls[:LOKI_MAX_SCREENSHOT_IMAGES]
    try:
        data = json.loads(s)
    except Exception:
        return []

    if isinstance(data, dict) and isinstance(data.get("images"), list):
        for item in data["images"]:
            if isinstance(item, str) and item.startswith("data:image/"):
                urls.append(item)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.startswith("data:image/"):
                urls.append(item)
    elif isinstance(data, str) and data.startswith("data:image/"):
        urls.append(data)
    return urls[:LOKI_MAX_SCREENSHOT_IMAGES]


def run_tool_call(tools: ToolRegistry, tool_name: str, args: Dict[str, Any]) -> str:
    spec = tools.get(tool_name)
    if not spec:
        return f"Unknown tool: {tool_name}"
    try:
        result = spec.fn(**(args or {}))
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)
        return str(result)
    except TypeError as e:
        return f"Tool arg error: {e}"
    except Exception as e:
        return f"Tool failed: {e}"


# -----------------------------
# Vector memory (SQLite)
# -----------------------------

def _cosine_sim(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return -1.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def embed_local(texts: List[str], dim: int = 768) -> List[List[float]]:
    """
    Dependency-free embedding fallback (hashed bag-of-words).
    Not as strong as model embeddings, but works for local retrieval.
    """

    out: List[List[float]] = []
    for t in texts:
        v = [0.0] * dim
        # Simple tokenization
        tokens = re.findall(r"[A-Za-z0-9_]{2,}", t.lower())
        if not tokens:
            out.append(v)
            continue
        for tok in tokens:
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if (h[4] & 1) == 0 else -1.0
            v[idx] += sign
        # L2 normalize
        n = math.sqrt(sum(x * x for x in v))
        if n > 0:
            v = [x / n for x in v]
        out.append(v)
    return out


def embed_texts(xai: XAIClient, texts: List[str]) -> List[List[float]]:
    """
    Try xAI embeddings first; fall back to local embeddings if unavailable.
    """

    try:
        embs = xai.embed(texts, model=XAI_EMBEDDING_MODEL, endpoint=XAI_EMBEDDINGS_ENDPOINT)
        if embs and all(isinstance(e, list) and e for e in embs):
            return embs
    except Exception:
        pass
    return embed_local(texts)


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 120) -> List[str]:
    text = text.replace("\r\n", "\n")
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
            continue
        if len(buf) + 2 + len(p) <= max_chars:
            buf = buf + "\n\n" + p
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)

    # Simple overlap on character tail
    if overlap > 0 and len(chunks) > 1:
        out: List[str] = []
        prev_tail = ""
        for c in chunks:
            out.append((prev_tail + c).strip())
            prev_tail = c[-overlap:]
        return out
    return chunks


class VectorMemoryStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);")
            conn.commit()
        finally:
            conn.close()

    def upsert_chunks(self, source_path: str, mime: str, texts: List[str], embeddings: List[List[float]]) -> int:
        if len(texts) != len(embeddings):
            raise ValueError("texts/embeddings length mismatch")
        conn = self._connect()
        try:
            conn.execute("DELETE FROM chunks WHERE source_path = ?", (source_path,))
            now = time.time()
            rows = 0
            for i, (t, emb) in enumerate(zip(texts, embeddings)):
                conn.execute(
                    "INSERT INTO chunks(source_path,mime,chunk_index,text,embedding_json,created_at) VALUES (?,?,?,?,?,?)",
                    (source_path, mime, i, t, json.dumps(emb), now),
                )
                rows += 1
            conn.commit()
            return rows
        finally:
            conn.close()

    def search(self, query_embedding: List[float], k: int = 6) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT source_path,mime,chunk_index,text,embedding_json FROM chunks")
            scored: List[Tuple[float, Dict[str, Any]]] = []
            for source_path, mime, chunk_index, text, emb_json in cur.fetchall():
                try:
                    emb = json.loads(emb_json)
                except Exception:
                    continue
                score = _cosine_sim(query_embedding, emb)
                scored.append(
                    (
                        score,
                        {
                            "source_path": source_path,
                            "mime": mime,
                            "chunk_index": chunk_index,
                            "text": text,
                            "score": score,
                        },
                    )
                )
            scored.sort(key=lambda x: x[0], reverse=True)
            return [d for _s, d in scored[: max(1, k)] if d["score"] > 0]
        finally:
            conn.close()

    def export_compiled_markdown(self, out_path: Path, limit_chars_per_chunk: int = 4000) -> None:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT source_path,mime,chunk_index,text,created_at FROM chunks ORDER BY source_path, chunk_index"
            )
            lines: List[str] = ["# Loki Compiled Memory", ""]
            current = None
            for source_path, mime, chunk_index, text, created_at in cur.fetchall():
                if source_path != current:
                    current = source_path
                    lines.append(f"## {source_path}")
                    lines.append(f"- mime: `{mime}`")
                    lines.append(f"- updated: `{time.ctime(created_at)}`")
                    lines.append("")
                if len(text) > limit_chars_per_chunk:
                    text = text[:limit_chars_per_chunk] + "\n[...truncated...]\n"
                lines.append(f"### Chunk {chunk_index}")
                lines.append(text)
                lines.append("")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("\n".join(lines), encoding="utf-8")
        finally:
            conn.close()


def ingest_one_path(xai: XAIClient, vstore: VectorMemoryStore, fp: Path) -> None:
    mime = guess_mime(fp)
    if mime.startswith("image/"):
        # Best-effort caption; if unavailable, store a placeholder.
        try:
            block = build_attachment_block(fp)
            if block.get("type") == "input_image":
                img_url = block.get("image_url")
                cap = analyze_images_with_xai_responses(
                    xai.api_key,
                    [str(img_url)],
                    f"Describe the attached image ({fp.name}) for long-term memory indexing. Extract any readable text verbatim and describe important visible UI/context.",
                    max_output_tokens=420,
                )
            else:
                cap = "(image present; caption unavailable)"
        except Exception:
            cap = "(image present; caption unavailable)"
        text_for_store = f"[Image: {fp.name}]\n{cap}"
        chunks = _chunk_text(text_for_store)
    elif mime == "application/pdf":
        # Extract text for indexing
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(fp))
            pages_text: List[str] = []
            for i, page in enumerate(reader.pages[:80]):
                try:
                    t = page.extract_text() or ""
                except Exception:
                    t = ""
                if t.strip():
                    pages_text.append(f"--- Page {i+1} ---\n{t.strip()}")
            text_for_store = "\n\n".join(pages_text).strip() or "[PDF had no extractable text.]"
        except Exception as e:
            text_for_store = f"[PDF extraction failed: {e}]"
        chunks = _chunk_text(f"[PDF: {fp.name}]\n{text_for_store}")
    else:
        text_for_store = safe_read_text(fp)
        chunks = _chunk_text(text_for_store)

    embs = embed_texts(xai, chunks)
    vstore.upsert_chunks(str(fp), mime=mime, texts=chunks, embeddings=embs)


class MemoryFolderWatcher:
    def __init__(self, inbox_dir: Path, processed_dir: Path, poll_s: float, xai: XAIClient, vstore: VectorMemoryStore):
        self.inbox_dir = inbox_dir
        self.processed_dir = processed_dir
        self.poll_s = float(max(0.5, min(30.0, poll_s)))
        self.xai = xai
        self.vstore = vstore
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: Dict[str, Tuple[float, int]] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
            self.processed_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        self._thread = threading.Thread(target=self._run, name="memory-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _snapshot(self) -> Dict[str, Tuple[float, int]]:
        snap: Dict[str, Tuple[float, int]] = {}
        # Only watch inbox; processed is treated as immutable source-of-truth.
        for fp in iter_supported_files(self.inbox_dir, exclude_inbox=False):
            try:
                st = fp.stat()
                snap[str(fp)] = (st.st_mtime, st.st_size)
            except Exception:
                continue
        return snap

    def _wait_until_stable(self, fp: Path, checks: int = 3, delay_s: float = 0.4) -> bool:
        """
        Avoid ingesting half-copied files by waiting for stable (mtime,size).
        """

        last = None
        for _ in range(max(1, checks)):
            try:
                st = fp.stat()
                sig = (st.st_mtime, st.st_size)
            except Exception:
                return False
            if last is not None and sig == last:
                return True
            last = sig
            time.sleep(delay_s)
        return False

    def _unique_processed_path(self, fp: Path) -> Path:
        ts = time.strftime("%Y%m%d-%H%M%S")
        base = fp.stem
        ext = fp.suffix
        candidate = self.processed_dir / f"{ts}_{base}{ext}"
        i = 1
        while candidate.exists():
            candidate = self.processed_dir / f"{ts}_{base}_{i}{ext}"
            i += 1
        return candidate

    def _run(self) -> None:
        # Initial snapshot
        self._seen = self._snapshot()
        while not self._stop.is_set():
            time.sleep(self.poll_s)
            snap = self._snapshot()
            changed = []
            for path_str, sig in snap.items():
                if self._seen.get(path_str) != sig:
                    changed.append(Path(path_str))
            if changed:
                for fp in sorted(changed):
                    try:
                        if not fp.exists() or not fp.is_file():
                            continue
                        if not self._wait_until_stable(fp):
                            continue
                        target = self._unique_processed_path(fp)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        fp.replace(target)
                        ingest_one_path(self.xai, self.vstore, target)
                        print(f"[watch] Processed+ingested: {target.name}")
                    except Exception as e:
                        print(f"[watch] Failed {fp.name}: {e}")
                try:
                    self.vstore.export_compiled_markdown(COMPILED_MEMORY_PATH)
                except Exception as e:
                    print(f"[watch] Compile failed: {e}")
            self._seen = snap


# -----------------------------
# Self-upgrade (plugin generation)
# -----------------------------

SELF_UPGRADE_SYSTEM = """You are Loki's plugin author.
Return ONLY valid JSON with keys:
- file_name: string (snake_case, .py)
- code: string (full python file contents)

Rules:
- Implement a function register(tools) that registers one or more tools.
- Use only stdlib unless user explicitly requested a dependency.
- Keep it small and reliable.
"""


def generate_plugin(xai: XAIClient, request_text: str) -> Dict[str, str]:
    messages = [
        {"role": "system", "content": SELF_UPGRADE_SYSTEM},
        {
            "role": "user",
            "content": f"Request: {request_text}\n\nCreate a plugin that adds this capability.",
        },
    ]
    resp = xai.chat(messages, tools=None)
    msg = extract_assistant_message(resp)
    content = msg.get("content") or ""
    if isinstance(content, list):
        # Some APIs may return structured parts; join any text parts.
        content = "\n".join([p.get("text", "") for p in content if isinstance(p, dict)])

    # Strip common markdown fences if present.
    content_str = str(content).strip()
    if content_str.startswith("```"):
        content_str = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", content_str)
        content_str = re.sub(r"\s*```$", "", content_str).strip()

    try:
        data = json.loads(content_str)
    except Exception:
        # Fallback: try to extract a JSON object from anywhere in the text
        m = re.search(r"\{[\s\S]*\}", content_str)
        if not m:
            raise RuntimeError(f"Plugin generator did not return JSON. Got:\n{content_str[:4000]}")
        data = json.loads(m.group(0))

    file_name = str(data.get("file_name") or "").strip()
    code = str(data.get("code") or "")
    if not file_name.endswith(".py") or not re.fullmatch(r"[a-z0-9_]+\.py", file_name):
        raise RuntimeError(f"Invalid file_name: {file_name!r}")
    if "def register(" not in code:
        raise RuntimeError("Plugin code must define register(tools).")
    return {"file_name": file_name, "code": code}


# -----------------------------
# Time context (epoch + ISO) for reliable real-world dates
# -----------------------------


def get_time_context_dict(iana_timezone: Optional[str] = None) -> Dict[str, Any]:
    """
    Return a JSON-serializable snapshot of "now".
    `epoch_seconds_utc` is the usual Unix timestamp (seconds since 1970-01-01 UTC).
    """

    if iana_timezone and ZoneInfo is not None:
        try:
            now = datetime.now(ZoneInfo(iana_timezone))
        except Exception:
            now = datetime.now().astimezone()
    else:
        now = datetime.now().astimezone()
    utc = datetime.now(timezone.utc)
    tz_label = str(now.tzinfo) if now.tzinfo else "local"
    if now.tzinfo is not None and ZoneInfo is not None:
        try:
            tz_label = getattr(now.tzinfo, "key", tz_label)  # type: ignore[attr-defined]
        except Exception:
            pass
    return {
        "epoch_seconds_utc": int(utc.timestamp()),
        "epoch_seconds_local_offset": int(now.timestamp()),
        "iso_local": now.isoformat(timespec="seconds"),
        "iso_utc": utc.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "timezone": tz_label,
        "weekday_local": now.strftime("%A"),
        "date_local": now.strftime("%Y-%m-%d"),
        "iana_timezone_requested": iana_timezone or "",
    }


def time_context_prompt_block() -> str:
    d = get_time_context_dict()
    return (
        "### Current time (authoritative — do not guess from training data)\n"
        f"- **Unix epoch seconds (UTC-based instant)**: `{d['epoch_seconds_utc']}`\n"
        f"- **ISO 8601 local**: `{d['iso_local']}`\n"
        f"- **ISO 8601 UTC**: `{d['iso_utc']}`\n"
        f"- **Timezone**: `{d['timezone']}`\n"
        f"- **Local date / weekday**: `{d['date_local']}` / `{d['weekday_local']}`\n"
        "Resolve relative phrases (“tomorrow”, “next Friday”) using the local date above, or call `get_current_time`. "
        "For calendar events, prefer ISO 8601 with offset (e.g. `2026-03-20T15:30:00-07:00`).\n"
    )


def compose_system_with_time(static_base: str) -> str:
    static_base = (static_base or "").rstrip()
    if not LOKI_TIME_SYSTEM_PROMPT:
        return static_base
    return static_base + "\n\n" + time_context_prompt_block()


def refresh_system_time_message(messages: List[Dict[str, Any]], static_base: str) -> None:
    """Refresh the first system message so every model call sees up-to-date clock + epoch."""

    content = compose_system_with_time(static_base)
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = content
    else:
        messages.insert(0, {"role": "system", "content": content})


def build_base_system_static(memory_text: str) -> str:
    """Core system instructions + snapshot memory (no clock block — clock is added per request)."""

    base = (
        "You are Loki, a local assistant controlling the user's computer and Intiface devices.\n"
        "Be concise, careful, and confirm risky actions.\n"
        "When a tool is appropriate, call it.\n"
        "For visual understanding of the desktop, call `monitors` and then `screenshot_monitor_base64` or `screenshot_all_monitors_base64`.\n"
        "You receive an authoritative **clock block** (Unix epoch + ISO timestamps) on every model call—use it for real-world dates and timelines; "
        "when in doubt call `get_current_time`.\n"
    )
    if LOKI_APPLE_CALENDAR and sys.platform == "darwin":
        base += (
            "On macOS you can use `apple_calendar_*` tools to read and modify the user's Apple Calendar (Calendar.app). "
            f"If the user does not name a calendar, prefer `{LOKI_APPLE_CALENDAR_DEFAULT}` (override via LOKI_APPLE_CALENDAR_DEFAULT). "
            "Always confirm before deleting events.\n"
        )
    if memory_text:
        base += "\nUser memory (treat as true unless contradicted):\n" + memory_text
    return base


# -----------------------------
# App
# -----------------------------

def build_core_tools(butt: ButtplugController, screen: Optional[ScreenController]) -> ToolRegistry:
    tools = ToolRegistry()

    tools.register(
        ToolSpec(
            name="help",
            description="List available commands/tools.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            fn=lambda: {"tools": tools.list_names()},
        )
    )

    tools.register(
        ToolSpec(
            name="intiface_status",
            description="Get Intiface/Buttplug connection status.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            fn=lambda: butt.status(),
        )
    )

    tools.register(
        ToolSpec(
            name="scan_devices",
            description="Scan for devices via Intiface for a few seconds.",
            parameters={
                "type": "object",
                "properties": {"seconds": {"type": "integer", "minimum": 1, "maximum": 30}},
                "required": [],
                "additionalProperties": False,
            },
            fn=lambda seconds=6: butt.scan(seconds=seconds),
        )
    )

    tools.register(
        ToolSpec(
            name="list_devices",
            description="List connected devices visible via Intiface.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            fn=lambda: butt.list_devices(),
        )
    )

    tools.register(
        ToolSpec(
            name="vibrate",
            description="Vibrate a device (default matches 'nora') at intensity 0..1 for duration seconds.",
            parameters={
                "type": "object",
                "properties": {
                    "device_name_contains": {"type": "string", "default": "nora"},
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.2},
                    "duration_s": {"type": "integer", "minimum": 0, "maximum": 3600, "default": 8},
                },
                "required": ["intensity"],
                "additionalProperties": False,
            },
            fn=lambda intensity, device_name_contains="nora", duration_s=8: butt.vibrate(
                device_name_contains=device_name_contains, intensity=float(intensity), duration_s=int(duration_s)
            ),
        )
    )

    tools.register(
        ToolSpec(
            name="stop_device",
            description="Stop a device immediately (default matches 'nora').",
            parameters={
                "type": "object",
                "properties": {"device_name_contains": {"type": "string", "default": "nora"}},
                "required": [],
                "additionalProperties": False,
            },
            fn=lambda device_name_contains="nora": butt.stop_device(device_name_contains=device_name_contains),
        )
    )

    if screen is not None:
        tools.register(
            ToolSpec(
                name="click",
                description="Click the mouse at screen coordinates.",
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                    },
                    "required": ["x", "y"],
                    "additionalProperties": False,
                },
                fn=lambda x, y, button="left": screen.click(x=int(x), y=int(y), button=str(button)),
            )
        )

        tools.register(
            ToolSpec(
                name="type_text",
                description="Type text at the current cursor focus.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "interval_s": {"type": "number", "default": 0.03}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                fn=lambda text, interval_s=0.03: screen.type(text=str(text), interval_s=float(interval_s)),
            )
        )

        tools.register(
            ToolSpec(
                name="hotkey",
                description="Press a keyboard hotkey chord (e.g., ['command','space']).",
                parameters={
                    "type": "object",
                    "properties": {"keys": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
                    "required": ["keys"],
                    "additionalProperties": False,
                },
                fn=lambda keys: screen.hotkey(*[str(k) for k in keys]),
            )
        )

        tools.register(
            ToolSpec(
                name="screenshot",
                description="Take a screenshot and return the file path.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                fn=lambda: screen.screenshot(),
            )
        )

        tools.register(
            ToolSpec(
                name="screenshot_base64",
                description="Take a screenshot and return base64 PNG (large).",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                fn=lambda: b64_file(Path(screen.screenshot())),
            )
        )

        tools.register(
            ToolSpec(
                name="monitors",
                description="List available monitors with indices for selecting which screen to view.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                fn=lambda: screen.monitors(),
            )
        )

        tools.register(
            ToolSpec(
                name="screenshot_monitor_base64",
                description="Capture a specific monitor by index and return a data:image/png;base64 URL.",
                parameters={
                    "type": "object",
                    "properties": {
                        "monitor_index": {"type": "integer", "minimum": 0},
                        "max_dim": {"type": "integer", "minimum": 256, "maximum": 4096, "default": 1600},
                    },
                    "required": ["monitor_index"],
                    "additionalProperties": False,
                },
                fn=lambda monitor_index, max_dim=1600: screen.screenshot_monitor_base64(
                    monitor_index=int(monitor_index), max_dim=int(max_dim)
                ),
            )
        )

        tools.register(
            ToolSpec(
                name="screenshot_monitor",
                description="Capture a specific monitor by index and return the saved PNG file path.",
                parameters={
                    "type": "object",
                    "properties": {
                        "monitor_index": {"type": "integer", "minimum": 0},
                        "max_dim": {"type": "integer", "minimum": 256, "maximum": 4096, "default": 1600},
                    },
                    "required": ["monitor_index"],
                    "additionalProperties": False,
                },
                fn=lambda monitor_index, max_dim=1600: screen.screenshot_monitor(
                    monitor_index=int(monitor_index), max_dim=int(max_dim)
                ),
            )
        )

        tools.register(
            ToolSpec(
                name="screenshot_all_monitors_base64",
                description="Capture all monitors and return JSON with a list of data:image/png;base64 URLs.",
                parameters={
                    "type": "object",
                    "properties": {"max_dim": {"type": "integer", "minimum": 256, "maximum": 4096, "default": 1200}},
                    "required": [],
                    "additionalProperties": False,
                },
                fn=lambda max_dim=1200: screen.screenshot_all_monitors_base64(max_dim=int(max_dim)),
            )
        )

    def _tool_get_current_time(iana_timezone: str = "") -> str:
        tz = (iana_timezone or "").strip() or None
        return json.dumps(get_time_context_dict(tz))

    tools.register(
        ToolSpec(
            name="get_current_time",
            description=(
                "Return authoritative current time: Unix epoch seconds, ISO local/UTC, local date/weekday, timezone. "
                "Use whenever 'now', relative dates, or scheduling accuracy matters."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "iana_timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone, e.g. America/Los_Angeles (default: machine local).",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
            fn=_tool_get_current_time,
        )
    )

    if LOKI_APPLE_CALENDAR and sys.platform == "darwin":
        try:
            import loki_apple_calendar as lac  # type: ignore
        except Exception:
            lac = None
        if lac is not None:
            _def_cal = LOKI_APPLE_CALENDAR_DEFAULT

            tools.register(
                ToolSpec(
                    name="apple_calendar_list_calendars",
                    description="List Apple Calendar calendar names from Calendar.app (macOS automation).",
                    parameters={"type": "object", "properties": {}, "additionalProperties": False},
                    fn=lambda: lac.list_calendars(),
                )
            )

            def _cal_list_events(start_iso: str, end_iso: str, calendar_name: str = "") -> str:
                return lac.list_events(str(start_iso), str(end_iso), str(calendar_name or ""))

            tools.register(
                ToolSpec(
                    name="apple_calendar_list_events",
                    description=(
                        "List Calendar events with start times between start_iso and end_iso (ISO 8601). "
                        "Optionally filter to a single calendar_name from apple_calendar_list_calendars."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "start_iso": {"type": "string"},
                            "end_iso": {"type": "string"},
                            "calendar_name": {"type": "string"},
                        },
                        "required": ["start_iso", "end_iso"],
                        "additionalProperties": False,
                    },
                    fn=_cal_list_events,
                )
            )

            def _cal_create(
                title: str,
                start_iso: str,
                end_iso: str,
                calendar_name: str = "",
                location: str = "",
                notes: str = "",
                allday: bool = False,
            ) -> str:
                return lac.create_event(
                    str(calendar_name or _def_cal),
                    str(title),
                    str(start_iso),
                    str(end_iso),
                    str(location or ""),
                    str(notes or ""),
                    bool(allday),
                )

            tools.register(
                ToolSpec(
                    name="apple_calendar_create_event",
                    description=(
                        "Create an Apple Calendar event. "
                        f"If calendar_name is omitted, uses default `{_def_cal}` (set LOKI_APPLE_CALENDAR_DEFAULT). "
                        "Use ISO 8601 with offset for start_iso/end_iso."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "start_iso": {"type": "string"},
                            "end_iso": {"type": "string"},
                            "calendar_name": {"type": "string"},
                            "location": {"type": "string"},
                            "notes": {"type": "string"},
                            "allday": {"type": "boolean"},
                        },
                        "required": ["title", "start_iso", "end_iso"],
                        "additionalProperties": False,
                    },
                    fn=_cal_create,
                )
            )

            def _cal_delete(calendar_name: str = "", event_uid: str = "") -> str:
                return lac.delete_event(str(calendar_name or _def_cal), str(event_uid))

            tools.register(
                ToolSpec(
                    name="apple_calendar_delete_event",
                    description=(
                        "Permanently delete a calendar event by event_uid (from list_events). "
                        "Always confirm with the user before calling this."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "calendar_name": {"type": "string"},
                            "event_uid": {"type": "string"},
                        },
                        "required": ["event_uid"],
                        "additionalProperties": False,
                    },
                    fn=_cal_delete,
                )
            )

            def _cal_update(
                event_uid: str,
                calendar_name: str = "",
                title: str = "",
                start_iso: str = "",
                end_iso: str = "",
                location: Optional[str] = None,
                notes: Optional[str] = None,
                allday: Optional[bool] = None,
            ) -> str:
                return lac.update_event(
                    str(calendar_name or _def_cal),
                    str(event_uid),
                    str(title or ""),
                    str(start_iso or ""),
                    str(end_iso or ""),
                    location,
                    notes,
                    allday,
                )

            tools.register(
                ToolSpec(
                    name="apple_calendar_update_event",
                    description=(
                        "Update an existing Apple Calendar event by event_uid. "
                        "Only non-empty fields are applied (except location/notes/allday when explicitly provided)."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "event_uid": {"type": "string"},
                            "calendar_name": {"type": "string"},
                            "title": {"type": "string"},
                            "start_iso": {"type": "string"},
                            "end_iso": {"type": "string"},
                            "location": {"type": "string"},
                            "notes": {"type": "string"},
                            "allday": {"type": "boolean"},
                        },
                        "required": ["event_uid"],
                        "additionalProperties": False,
                    },
                    fn=_cal_update,
                )
            )

    return tools


def print_banner() -> None:
    print("Loki Direct ready.")
    print("Enter messages normally. Commands:")
    print("  /help")
    print("  /mem (reload memories)")
    print("  /attach <path> (attach a text/image file for analysis)")
    print("  /ingest <path> (add file/folder into vector memory)")
    print("  /compile_mem (write compiled memory document)")
    print(f"  drop files into: {INBOX_DIR} (auto-moves to {PROCESSED_DIR})")
    print("  /set_screen <left|right> <monitor_index> (persist which physical screen index is which)")
    print("  /autodetect_screens (choose left/right based on monitor X positions)")
    if VOICE_ENABLE:
        print(f"  Voice: hold '{VOICE_HOTKEY}' to speak (TTS={'on' if VOICE_TTS_ENABLE else 'off'})")
    print("  /tools (list tool names)")
    print("  /scan (scan Intiface devices)")
    print("  /upgrade <request>   (e.g. /upgrade add tts)")
    if LOKI_APPLE_CALENDAR and sys.platform == "darwin":
        print("  Time: clock + epoch in system prompt; tool get_current_time")
        print(f"  Apple Calendar tools (Calendar.app); default calendar: {LOKI_APPLE_CALENDAR_DEFAULT!r}")
    else:
        print("  Time: clock + epoch in system prompt; tool get_current_time")
    print("  /quit")


def main() -> int:
    if not XAI_API_KEY:
        print("ERROR: XAI_API_KEY not set (check .env).")
        return 1

    # Controllers
    butt = ButtplugController(INTIFACE_WS)
    butt.start()

    screen: Optional[ScreenController]
    try:
        screen = ScreenController()
    except Exception as e:
        screen = None
        print(f"[screen] Disabled: {e}")

    screen_indices = load_screen_indices()
    if screen is not None:
        print(f"[screen] Using indices: left={screen_indices['left']} right={screen_indices['right']}")

    # Memory
    memory_text, memory_warnings = load_memories(MEMORY_DIR)
    if memory_warnings:
        for w in memory_warnings:
            print(f"[memory] {w}")
    if memory_text:
        print(f"[memory] Loaded from {MEMORY_DIR}")
    else:
        print(f"[memory] No memory files found in {MEMORY_DIR} (optional).")

    # Tools + Plugins
    tools = build_core_tools(butt, screen)
    ensure_plugins_package(PLUGINS_DIR)
    for msg in load_plugins(PLUGINS_DIR, tools):
        print(f"[plugin] {msg}")

    if screen is not None:
        # Screen helpers so you don't have to remember monitor indices.
        tools.add_tool(
            name="screen_left_index",
            description="Return the configured monitor index Loki should treat as the LEFT screen.",
            fn=lambda: int(screen_indices.get("left", 0)),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tools.add_tool(
            name="screen_right_index",
            description="Return the configured monitor index Loki should treat as the RIGHT screen.",
            fn=lambda: int(screen_indices.get("right", 0)),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tools.add_tool(
            name="screenshot_left_base64",
            description="Screenshot the configured LEFT screen and return a data:image/png;base64 URL.",
            fn=lambda max_dim=1600: screen.screenshot_monitor_base64(int(screen_indices.get("left", 0)), max_dim=int(max_dim)),
            parameters={
                "type": "object",
                "properties": {"max_dim": {"type": "integer", "minimum": 256, "maximum": 4096, "default": 1600}},
                "required": [],
                "additionalProperties": False,
            },
        )
        tools.add_tool(
            name="screenshot_right_base64",
            description="Screenshot the configured RIGHT screen and return a data:image/png;base64 URL.",
            fn=lambda max_dim=1600: screen.screenshot_monitor_base64(int(screen_indices.get("right", 0)), max_dim=int(max_dim)),
            parameters={
                "type": "object",
                "properties": {"max_dim": {"type": "integer", "minimum": 256, "maximum": 4096, "default": 1600}},
                "required": [],
                "additionalProperties": False,
            },
        )

    xai = XAIClient(XAI_API_KEY, XAI_ENDPOINT, XAI_MODEL, timeout_s=REQUEST_TIMEOUT_S)
    vstore = VectorMemoryStore(VECTOR_DB_PATH)
    watcher: Optional[MemoryFolderWatcher] = None
    if WATCH_MEMORY_FOLDER:
        watcher = MemoryFolderWatcher(INBOX_DIR, PROCESSED_DIR, WATCH_POLL_S, xai=xai, vstore=vstore)
        watcher.start()
        print(f"[watch] Watching inbox {INBOX_DIR} (poll {WATCH_POLL_S:.1f}s)")

    base_system_static = build_base_system_static(memory_text)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": compose_system_with_time(base_system_static)}]

    chat_lock = threading.Lock()
    voice_mgr: Optional[VoiceManager] = None

    def _voice_stt_task(text: str) -> None:
        nonlocal voice_mgr
        with chat_lock:
            user_in = (text or "").strip()
            if not user_in:
                return

            # Retrieval: embed the user's text and attach top-k relevant chunks.
            retrieved_block = ""
            try:
                qemb = embed_texts(xai, [user_in])[0]
                hits = vstore.search(qemb, k=RETRIEVAL_K)
                if hits:
                    parts = []
                    for h in hits:
                        parts.append(
                            f"- score={h['score']:.3f} source={h['source_path']} chunk={h['chunk_index']}\n{h['text']}"
                        )
                    retrieved_block = "Retrieved memory:\n" + "\n\n".join(parts)
            except Exception:
                retrieved_block = ""

            # Append user message for this turn.
            if retrieved_block:
                messages.append({"role": "user", "content": f"{user_in}\n\n---\n{retrieved_block}"})
            else:
                messages.append({"role": "user", "content": user_in})

            # Call xAI with tools enabled.
            refresh_system_time_message(messages, base_system_static)
            resp = xai.chat(messages, tools=tools.list_specs_for_model())
            msg = extract_assistant_message(resp)

            # Tool call loop (OpenAI-style)
            while True:
                tool_calls = msg.get("tool_calls") or []
                function_call = msg.get("function_call")
                if function_call and not tool_calls:
                    tool_calls = [{"id": "legacy", "type": "function", "function": function_call}]

                if not tool_calls:
                    break

                messages.append(msg)

                for tc in tool_calls:
                    fn = (tc.get("function") or {})
                    tool_name = fn.get("name")
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except Exception:
                        args = {}
                    result = run_tool_call(tools, str(tool_name), args if isinstance(args, dict) else {})

                    if tool_name in {
                        "screenshot_monitor_base64",
                        "screenshot_all_monitors_base64",
                        "screenshot_left_base64",
                        "screenshot_right_base64",
                    }:
                        img_urls = extract_image_data_urls(result)
                        if img_urls:
                            if tool_name == "screenshot_monitor_base64" and isinstance(args, dict):
                                mi = args.get("monitor_index")
                                prompt = (
                                    f"You are viewing a screenshot of desktop monitor index {mi}. "
                                    "Describe all visible text and important UI elements. "
                                    "Quote readable text as closely as possible."
                                )
                            elif tool_name == "screenshot_left_base64":
                                prompt = (
                                    "You are viewing the user's LEFT screen. "
                                    "Describe all visible text and important UI elements. "
                                    "Quote readable text as closely as possible."
                                )
                            elif tool_name == "screenshot_right_base64":
                                prompt = (
                                    "You are viewing the user's RIGHT screen. "
                                    "Describe all visible text and important UI elements. "
                                    "Quote readable text as closely as possible."
                                )
                            else:
                                prompt = (
                                    "You are viewing screenshots of multiple desktop monitors provided in order. "
                                    "For each image in order, describe visible text and important UI elements. "
                                    "Quote readable text as closely as possible."
                                )
                            result = analyze_images_with_xai_responses(
                                xai.api_key,
                                img_urls,
                                prompt,
                                max_output_tokens=360,
                            )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id") or "tool",
                            "name": tool_name,
                            "content": result,
                        }
                    )

                refresh_system_time_message(messages, base_system_static)
                resp = xai.chat(messages, tools=tools.list_specs_for_model())
                msg = extract_assistant_message(resp)

            content = msg.get("content") or ""
            if isinstance(content, list):
                content = "\n".join([p.get("text", "") for p in content if isinstance(p, dict)])

            print(f"Loki> {content}")
            messages.append({"role": "assistant", "content": content})

            try:
                if voice_mgr:
                    voice_mgr.speak(str(content))
            except Exception:
                pass

    if VOICE_ENABLE:
        print("[voice] Hold-to-speak enabled.")
        print(f"[voice] Hold-to-speak hotkey spec: '{VOICE_HOTKEY}'.")
        print("[voice] Permissions you may need:")
        print("- Microphone (for recording)")
        print("- Input Monitoring + Accessibility (for global hotkey listening via pynput)")
        try:
            _tts0 = load_tts_settings_merged()
            voice_mgr = VoiceManager(
                hotkey_char=VOICE_HOTKEY,
                stt_model=VOICE_STT_MODEL,
                device=VOICE_DEVICE,
                compute_type=VOICE_COMPUTE_TYPE,
                sample_rate=VOICE_SAMPLE_RATE,
                channels=VOICE_CHANNELS,
                max_seconds=VOICE_MAX_SECONDS,
                min_seconds=VOICE_MIN_SECONDS,
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
                stt_task_fn=_voice_stt_task,
            )
            voice_mgr.start()
        except Exception as e:
            voice_mgr = None
            print(f"[voice] Disabled (init failed): {e}")

    # Graceful exit
    stop_now = threading.Event()

    def _sigint(_signum, _frame):
        stop_now.set()

    signal.signal(signal.SIGINT, _sigint)

    print_banner()

    while not stop_now.is_set():
        try:
            user_in = input("\nYou> ").strip()
        except EOFError:
            break

        if not user_in:
            continue
        if user_in.lower() in {"/quit", "quit", "exit"}:
            break

        if user_in.startswith("/set_screen "):
            raw = user_in[len("/set_screen ") :].strip()
            parts = raw.split()
            if len(parts) != 2:
                print("Usage: /set_screen <left|right> <monitor_index>")
                continue
            side = parts[0].strip().lower()
            if side not in {"left", "right"}:
                print("Side must be `left` or `right`.")
                continue
            try:
                idx = int(parts[1])
            except Exception:
                print("monitor_index must be an integer.")
                continue
            screen_indices[side] = idx
            save_screen_indices(screen_indices)
            print(f"[screen] Updated indices: left={screen_indices['left']} right={screen_indices['right']}")
            continue

        if user_in == "/autodetect_screens":
            if screen is None:
                print("[screen] Disabled (no screen tools).")
                continue
            try:
                mons = screen.monitors()
                if not mons:
                    print("[screen] No monitors detected.")
                    continue
                # Choose left/right based on monitor left coordinate.
                mons_sorted = sorted(mons, key=lambda m: int(m.get("left", 0)))
                left_m = mons_sorted[0]
                right_m = mons_sorted[-1]
                screen_indices["left"] = int(left_m["index"])
                screen_indices["right"] = int(right_m["index"])
                save_screen_indices(screen_indices)
                print(f"[screen] Autodetected: left={screen_indices['left']} right={screen_indices['right']}")
            except Exception as e:
                print(f"[screen] Autodetect failed: {e}")
            continue

        # If they paste a real file path, treat it like /attach automatically.
        autop = looks_like_existing_path(user_in)
        if autop:
            user_in = f"/attach {autop}"

        if user_in == "/help":
            print_banner()
            continue

        if user_in == "/tools":
            print("\n".join(tools.list_names()))
            continue

        if user_in == "/scan":
            print(butt.scan())
            continue

        if user_in == "/mem":
            memory_text, memory_warnings = load_memories(MEMORY_DIR)
            if memory_warnings:
                for w in memory_warnings:
                    print(f"[memory] {w}")
            base_system_static = build_base_system_static(memory_text)
            tail = [m for m in messages if m.get("role") != "system"]
            messages = [{"role": "system", "content": compose_system_with_time(base_system_static)}] + tail
            print(f"[memory] Reloaded {MEMORY_DIR}")
            continue

        if user_in.startswith("/ingest "):
            raw = user_in[len("/ingest ") :].strip().strip('"').strip("'").replace("\\ ", " ")
            if not raw:
                print("Usage: /ingest <path>")
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if not p.exists():
                print(f"[ingest] Not found: {p}")
                continue

            # Collect files
            files: List[Path] = []
            if p.is_file():
                files = [p]
            else:
                # ingest common text + images
                files = iter_supported_files(p)

            if not files:
                print("[ingest] No supported files found.")
                continue

            ingested = 0
            failed = 0
            for fp in sorted(files):
                try:
                    ingest_one_path(xai, vstore, fp)
                    ingested += 1
                except Exception as e:
                    failed += 1
                    print(f"[ingest] Failed {fp.name}: {e}")

            try:
                vstore.export_compiled_markdown(COMPILED_MEMORY_PATH)
            except Exception as e:
                print(f"[compile] Failed: {e}")

            print(f"[ingest] Done. Files ingested: {ingested}, failed: {failed}. Compiled: {COMPILED_MEMORY_PATH}")
            continue

        if user_in == "/compile_mem":
            try:
                vstore.export_compiled_markdown(COMPILED_MEMORY_PATH)
                print(f"[compile] Wrote {COMPILED_MEMORY_PATH}")
            except Exception as e:
                print(f"[compile] Failed: {e}")
            continue

        if user_in.startswith("/attach "):
            raw = user_in[len("/attach ") :].strip().strip('"').strip("'")
            if not raw:
                print("Usage: /attach <path>")
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            if not p.exists() or not p.is_file():
                print(f"[attach] Not found: {p}")
                continue
            try:
                block = build_attachment_block(p)
            except Exception as e:
                print(f"[attach] Failed: {e}")
                continue

            # Chat completions isn't reliably multimodal here, so we route images through
            # the Responses API and then feed the resulting text back into chat.
            if block.get("type") == "input_image":
                img_url = block.get("image_url")
                analysis = analyze_images_with_xai_responses(
                    xai.api_key,
                    [str(img_url)],
                    f"Analyze the attached image ({p.name}). Extract any readable text and describe important visible UI elements.",
                    max_output_tokens=420,
                )
                messages.append({"role": "user", "content": f"[Image analysis: {p.name}]\n{analysis}"})
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Analyze the attached file and respond."},
                            block,
                        ],
                    }
                )
            print(f"[attach] Attached {p.name}")
            # fall through to run the normal chat logic below, but skip adding user_in again
            user_in = ""

        if user_in.startswith("/upgrade "):
            request_text = user_in[len("/upgrade ") :].strip()
            if not request_text:
                print("Usage: /upgrade <request>")
                continue
            try:
                plugin = generate_plugin(xai, request_text)
                ensure_plugins_package(PLUGINS_DIR)
                target = (PLUGINS_DIR / plugin["file_name"]).resolve()
                if target.exists():
                    print(f"[upgrade] Refusing to overwrite existing plugin: {target.name}")
                    continue
                target.write_text(plugin["code"], encoding="utf-8")
                for msg in load_plugins(PLUGINS_DIR, tools):
                    pass
                print(f"[upgrade] Added plugin {target.name}. Tools now: {', '.join(tools.list_names())}")
            except Exception as e:
                print(f"[upgrade] Failed: {e}")
            continue

        # Retrieval: embed the user's text and attach top-k relevant chunks.
        retrieved_block = ""
        if user_in:
            try:
                qemb = embed_texts(xai, [user_in])[0]
                hits = vstore.search(qemb, k=RETRIEVAL_K)
                if hits:
                    parts = []
                    for h in hits:
                        parts.append(
                            f"- score={h['score']:.3f} source={h['source_path']} chunk={h['chunk_index']}\n{h['text']}"
                        )
                    retrieved_block = "Retrieved memory:\n" + "\n\n".join(parts)
            except Exception:
                retrieved_block = ""

        # Normal chat turn (with tool calling)
        if user_in:
            if retrieved_block:
                messages.append(
                    {
                        "role": "user",
                        "content": f"{user_in}\n\n---\n{retrieved_block}",
                    }
                )
            else:
                messages.append({"role": "user", "content": user_in})

        try:
            refresh_system_time_message(messages, base_system_static)
            resp = xai.chat(messages, tools=tools.list_specs_for_model())
            msg = extract_assistant_message(resp)
        except Exception as e:
            print(f"Loki> [API error] {e}")
            continue

        # Tool call loop (OpenAI-style)
        # We support two shapes:
        # - msg["tool_calls"] (list)
        # - legacy: msg["function_call"] (single)
        while True:
            tool_calls = msg.get("tool_calls") or []
            function_call = msg.get("function_call")
            if function_call and not tool_calls:
                tool_calls = [{"id": "legacy", "type": "function", "function": function_call}]

            if not tool_calls:
                break

            messages.append(msg)

            for tc in tool_calls:
                fn = (tc.get("function") or {})
                tool_name = fn.get("name")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = {}
                result = run_tool_call(tools, str(tool_name), args if isinstance(args, dict) else {})
                if tool_name in {
                    "screenshot_monitor_base64",
                    "screenshot_all_monitors_base64",
                    "screenshot_left_base64",
                    "screenshot_right_base64",
                }:
                    img_urls = extract_image_data_urls(result)
                    if img_urls:
                        if tool_name == "screenshot_monitor_base64" and isinstance(args, dict):
                            mi = args.get("monitor_index")
                            prompt = (
                                f"You are viewing a screenshot of desktop monitor index {mi}. "
                                "Describe all visible text and important UI elements. "
                                "Quote readable text as closely as possible."
                            )
                        elif tool_name == "screenshot_left_base64":
                            prompt = (
                                "You are viewing the user's LEFT screen. "
                                "Describe all visible text and important UI elements. "
                                "Quote readable text as closely as possible."
                            )
                        elif tool_name == "screenshot_right_base64":
                            prompt = (
                                "You are viewing the user's RIGHT screen. "
                                "Describe all visible text and important UI elements. "
                                "Quote readable text as closely as possible."
                            )
                        else:
                            prompt = (
                                "You are viewing screenshots of multiple desktop monitors provided in order. "
                                "For each image in order, describe visible text and important UI elements. "
                                "Quote readable text as closely as possible."
                            )
                        result = analyze_images_with_xai_responses(
                            xai.api_key,
                            img_urls,
                            prompt,
                            max_output_tokens=360,
                        )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "tool",
                        "name": tool_name,
                        "content": result,
                    }
                )

            try:
                refresh_system_time_message(messages, base_system_static)
                resp = xai.chat(messages, tools=tools.list_specs_for_model())
                msg = extract_assistant_message(resp)
            except Exception as e:
                print(f"Loki> [API error after tool] {e}")
                msg = {"role": "assistant", "content": f"[API error after tool] {e}"}
                break

        # Print assistant message
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "\n".join([p.get("text", "") for p in content if isinstance(p, dict)])
        print(f"Loki> {content}")
        messages.append({"role": "assistant", "content": content})

    try:
        butt.stop_device("nora")
    except Exception:
        pass
    try:
        if watcher:
            watcher.stop()
    except Exception:
        pass
    try:
        if voice_mgr:
            voice_mgr.stop()
    except Exception:
        pass
    butt.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

