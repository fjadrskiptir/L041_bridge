"""
Local neural TTS via Piper (https://github.com/OHF-Voice/piper1-gpl).

Two invocation styles:
1) **Python module** (recommended): `pip install piper-tts` in the project venv, then e.g.
     `python -m piper.download_voices en_US-lessac-medium`
   Loki runs: `python -m piper -m <voice> -f out.wav --data-dir <dir> -- <text>`

2) **Legacy binary** + `.onnx` model (rhasspy/piper releases):
     `piper --model /path/to/model.onnx --output_file out.wav`
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def looks_like_onnx_path(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    if t.lower().endswith(".onnx"):
        return True
    try:
        return Path(t).suffix.lower() == ".onnx"
    except Exception:
        return False


def resolve_piper_binary(binary: str) -> str:
    b = (binary or "piper").strip()
    if not b:
        return "piper"
    p = Path(b)
    if p.is_file():
        return str(p.resolve())
    w = shutil.which(b)
    return w or b


def synthesize_piper_wav(
    text: str,
    *,
    onnx_path: Optional[Path],
    voice_module: str,
    data_dir: Optional[Path],
    piper_binary: str,
    length_scale: Optional[float] = None,
    speaker_id: Optional[int] = None,
    timeout_s: int = 180,
) -> Optional[Path]:
    """
    Write `text` to a temp .wav via Piper. Returns path or None on failure.
    """

    text = (text or "").strip()
    if not text:
        return None

    fd, tmp = tempfile.mkstemp(prefix="loki_piper_", suffix=".wav")
    import os

    os.close(fd)
    wav_path = Path(tmp)

    try:
        if onnx_path is not None and onnx_path.is_file():
            exe = resolve_piper_binary(piper_binary)
            cmd: List[str] = [exe, "--model", str(onnx_path.resolve()), "--output_file", str(wav_path)]
            if length_scale is not None and abs(float(length_scale) - 1.0) > 1e-6:
                cmd.extend(["--length_scale", str(float(length_scale))])
            if speaker_id is not None:
                cmd.extend(["--speaker", str(int(speaker_id))])
            subprocess.run(
                cmd,
                input=text,
                text=True,
                check=True,
                timeout=timeout_s,
                capture_output=True,
            )
            return wav_path if wav_path.is_file() and wav_path.stat().st_size > 0 else None

        vm = (voice_module or "").strip()
        if not vm:
            return None
        dd = (data_dir or Path.cwd()).resolve()
        dd.mkdir(parents=True, exist_ok=True)
        cmd2: List[str] = [
            sys.executable,
            "-m",
            "piper",
            "-m",
            vm,
            "-f",
            str(wav_path),
            "--data-dir",
            str(dd),
            "--",
            text,
        ]
        subprocess.run(cmd2, check=True, timeout=timeout_s, capture_output=True)
        return wav_path if wav_path.is_file() and wav_path.stat().st_size > 0 else None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, subprocess.TimeoutExpired):
        try:
            wav_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
        except Exception:
            pass
        return None
    except Exception:
        try:
            wav_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
        except Exception:
            pass
        return None


def play_wav_async(wav_path: Path) -> subprocess.Popen:
    """Play a WAV file; returns the player Popen (for termination). macOS: afplay."""

    if sys.platform == "darwin":
        return subprocess.Popen(
            ["afplay", str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    ffplay = shutil.which("ffplay")
    if ffplay:
        return subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    # Last resort: try aplay (may need format args; often fails for float wav)
    aplay = shutil.which("aplay")
    if aplay:
        return subprocess.Popen(
            [aplay, str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    raise RuntimeError("No afplay/ffplay/aplay found to play Piper WAV output")


def list_onnx_in_dir(directory: Path) -> List[Dict[str, Any]]:
    """List *.onnx models under `directory` (non-recursive)."""

    d = directory.resolve()
    if not d.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(d.glob("*.onnx")):
        out.append({"path": str(p), "name": p.name})
    return out


def piper_voice_config_path(onnx_path: Path) -> Path:
    """Companion JSON next to Piper's `<voice>.onnx` (i.e. `<voice>.onnx.json`)."""

    return Path(str(onnx_path) + ".json")


def list_installed_piper_voices(data_dir: Path) -> List[Dict[str, Any]]:
    """
    Voices downloaded via `python -m piper.download_voices --data-dir <dir> <voice_id>`
    appear as `<voice_id>.onnx` (+ `<voice_id>.onnx.json`). Return one entry per .onnx.
    """

    d = data_dir.resolve()
    if not d.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(d.glob("*.onnx")):
        voice_id = p.stem
        cfg = piper_voice_config_path(p)
        out.append(
            {
                "id": voice_id,
                "onnx": str(p),
                "has_json": cfg.is_file(),
            }
        )
    return out
