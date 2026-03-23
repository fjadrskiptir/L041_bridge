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


def _log_piper_failure(where: str, proc: subprocess.CompletedProcess) -> None:
    err = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    tail = err[:1200] if err else "(no stderr/stdout)"
    print(f"[tts] Piper failed ({where}) exit={proc.returncode}: {tail}", flush=True)
    low = err.lower()
    if "no module named piper" in low or "no module named 'piper'" in low:
        ex = sys.executable
        print(
            "[tts] Fix: install Piper into the SAME Python Loki uses, then restart the web UI:\n"
            f"      {ex} -m pip install piper-tts\n"
            "      (or: pip install -r requirements-piper.txt from your project venv)",
            flush=True,
        )
    if "pathvalidate" in low and "no module named" in low:
        ex = sys.executable
        print(
            "[tts] Fix: Piper needs the pathvalidate package in this venv:\n"
            f"      {ex} -m pip install pathvalidate\n"
            "      (or: pip install -r requirements-piper.txt)",
            flush=True,
        )


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
    noise_scale: Optional[float] = None,
    noise_w_scale: Optional[float] = None,
    volume: Optional[float] = None,
    sentence_silence: Optional[float] = None,
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
        # Always use `python -m piper` so length / noise / volume / silence flags work.
        # The legacy `piper` binary path ignored most of those, which made UI sliders ineffective
        # whenever the voice resolved to a direct .onnx file.
        model_arg: str
        if onnx_path is not None and onnx_path.is_file():
            model_arg = str(onnx_path.resolve())
        else:
            vm = (voice_module or "").strip()
            if not vm:
                return None
            model_arg = vm

        dd = (data_dir or Path.cwd()).resolve()
        dd.mkdir(parents=True, exist_ok=True)
        cmd2: List[str] = [
            sys.executable,
            "-m",
            "piper",
            "-m",
            model_arg,
            "-f",
            str(wav_path),
            "--data-dir",
            str(dd),
        ]
        if speaker_id is not None:
            cmd2.extend(["-s", str(int(speaker_id))])
        if length_scale is not None:
            cmd2.extend(["--length-scale", str(float(length_scale))])
        if noise_scale is not None:
            cmd2.extend(["--noise-scale", str(float(noise_scale))])
        if noise_w_scale is not None:
            cmd2.extend(["--noise-w-scale", str(float(noise_w_scale))])
        if volume is not None:
            cmd2.extend(["--volume", str(float(volume))])
        if sentence_silence is not None and float(sentence_silence) > 1e-5:
            cmd2.extend(["--sentence-silence", str(float(sentence_silence))])
        cmd2.extend(["--", text])
        proc2 = subprocess.run(cmd2, check=False, timeout=timeout_s, capture_output=True, text=True)
        if proc2.returncode != 0:
            _log_piper_failure(f"python -m piper -m {model_arg}", proc2)
            return None
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


def play_wav_async(wav_path: Path, *, playback_rate: float = 1.0) -> subprocess.Popen:
    """Play a WAV file; returns the player Popen (for termination). macOS: afplay."""

    if sys.platform == "darwin":
        cmd = ["afplay"]
        try:
            r = float(playback_rate)
        except (TypeError, ValueError):
            r = 1.0
        if abs(r - 1.0) > 0.02:
            cmd.extend(["-r", str(r)])
        cmd.append(str(wav_path))
        return subprocess.Popen(
            cmd,
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
