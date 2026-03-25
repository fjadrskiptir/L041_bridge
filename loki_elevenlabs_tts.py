"""
Cloud TTS via ElevenLabs HTTP API (https://elevenlabs.io/docs/api-reference/text-to-speech).

API key must come from env `ELEVENLABS_API_KEY` (never commit). Voice ID is configured in the Web UI or `ELEVENLABS_VOICE_ID`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import requests


ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"


def _sanitize_xi_api_key(raw: str) -> str:
    """Match loki_direct._sanitize_env_secret (BOM / stray quotes)."""

    k = (raw or "").strip().lstrip("\ufeff")
    if len(k) >= 2 and k[0] == k[-1] and k[0] in ('"', "'"):
        k = k[1:-1].strip()
    return k


def _print_invalid_api_key_hint(key: str) -> None:
    k = (key or "").strip()
    n_ctrl = sum(1 for c in k if ord(c) < 32)
    print(
        "[tts] ElevenLabs: invalid_api_key — ElevenLabs rejected the xi-api-key value. "
        f"Sanity: len={len(k)} ctrl_bytes={n_ctrl}. "
        "Fix: open https://elevenlabs.io/app/settings/api-keys , copy the API key again, "
        "set a single line in repo .env as ELEVENLABS_API_KEY=... (no spaces around =), save, restart. "
        "If it still fails, regenerate the key there and update .env.",
        flush=True,
    )


def synthesize_elevenlabs_mp3(
    text: str,
    *,
    api_key: str,
    voice_id: str,
    model_id: str,
    stability: float,
    similarity_boost: float,
    style: float,
    use_speaker_boost: bool,
    timeout_s: float = 120.0,
) -> Optional[Path]:
    """
    POST text to ElevenLabs; write MP3 to a temp file. Returns path or None on failure.
    """

    t = (text or "").strip()
    key = _sanitize_xi_api_key(api_key or "")
    vid = (voice_id or "").strip()
    mid = (model_id or "eleven_turbo_v2_5").strip()

    if not t or not key or not vid:
        return None

    url = f"{ELEVENLABS_TTS_URL}/{vid}"
    # Default to a widely-supported MP3 preset; override with ELEVENLABS_OUTPUT_FORMAT if needed.
    out_fmt = (os.getenv("ELEVENLABS_OUTPUT_FORMAT") or "mp3_44100_32").strip()
    params = {"output_format": out_fmt} if out_fmt else {}
    voice_settings = {
        "stability": max(0.0, min(1.0, float(stability))),
        "similarity_boost": max(0.0, min(1.0, float(similarity_boost))),
        "style": max(0.0, min(1.0, float(style))),
        "use_speaker_boost": bool(use_speaker_boost),
    }
    payload: Dict[str, Any] = {
        "text": t,
        "model_id": mid,
        "voice_settings": voice_settings,
    }
    headers = {
        "xi-api-key": key,
        # API returns application/octet-stream for audio bytes
        "Accept": "application/octet-stream, audio/mpeg, */*",
        "Content-Type": "application/json",
    }

    def _post(body: Dict[str, Any], qp: Dict[str, str]) -> requests.Response:
        return requests.post(
            url,
            params=qp or None,
            headers=headers,
            json=body,
            timeout=max(10.0, float(timeout_s)),
        )

    try:
        resp = _post(payload, params)
    except requests.RequestException as e:
        print(f"[tts] ElevenLabs request failed: {e}", flush=True)
        return None

    if resp.status_code != 200:
        tail = (resp.text or "")[:800]
        print(f"[tts] ElevenLabs HTTP {resp.status_code} (with voice_settings): {tail}", flush=True)
        # Retry minimal body (some accounts / models reject certain voice_settings combinations)
        minimal = {"text": t, "model_id": mid}
        try:
            resp2 = _post(minimal, params)
        except requests.RequestException as e:
            print(f"[tts] ElevenLabs retry failed: {e}", flush=True)
            return None
        if resp2.status_code != 200:
            tail2 = (resp2.text or "")[:800]
            print(f"[tts] ElevenLabs HTTP {resp2.status_code} (minimal body): {tail2}", flush=True)
            # Last resort: default API output format (no query param)
            try:
                resp3 = _post(minimal, {})
            except requests.RequestException as e:
                print(f"[tts] ElevenLabs final retry failed: {e}", flush=True)
                return None
            if resp3.status_code != 200:
                tail3 = (resp3.text or "")[:800]
                print(f"[tts] ElevenLabs HTTP {resp3.status_code} (no output_format): {tail3}", flush=True)
                if resp3.status_code == 401 and "invalid_api_key" in (resp3.text or ""):
                    _print_invalid_api_key_hint(key)
                return None
            resp = resp3
        else:
            resp = resp2

    if resp.status_code != 200:
        return None

    print(f"[tts] ElevenLabs: OK ({len(resp.content)} bytes, format={out_fmt or 'default'})", flush=True)

    fd, tmp = tempfile.mkstemp(prefix="loki_elevenlabs_", suffix=".mp3")
    os.close(fd)
    out = Path(tmp)
    try:
        out.write_bytes(resp.content)
        if out.stat().st_size < 64:
            out.unlink(missing_ok=True)  # type: ignore[attr-defined]
            print("[tts] ElevenLabs returned empty audio", flush=True)
            return None
        return out
    except OSError as e:
        print(f"[tts] ElevenLabs write failed: {e}", flush=True)
        try:
            out.unlink(missing_ok=True)  # type: ignore[attr-defined]
        except Exception:
            pass
        return None


def play_mp3_async(mp3_path: Path, *, playback_rate: float = 1.0) -> subprocess.Popen:
    """Play MP3; macOS uses afplay. Returns Popen for cooperative stop."""

    if sys.platform == "darwin":
        cmd = ["afplay"]
        try:
            r = float(playback_rate)
        except (TypeError, ValueError):
            r = 1.0
        if abs(r - 1.0) > 0.02:
            cmd.extend(["-r", str(r)])
        cmd.append(str(mp3_path))
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Best-effort elsewhere
    for bin_name in ("ffplay", "mpv"):
        w = shutil.which(bin_name)
        if w:
            if bin_name == "ffplay":
                return subprocess.Popen(
                    [w, "-nodisp", "-autoexit", str(mp3_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return subprocess.Popen([w, "--really-quiet", str(mp3_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    raise RuntimeError("No afplay/ffplay/mpv found to play ElevenLabs MP3 output")
