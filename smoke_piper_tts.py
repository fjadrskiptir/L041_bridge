#!/usr/bin/env python3
"""
Smoke-test: Piper noise_scale / noise_w_scale produce different WAV output.

Run from repo root (uses your memories/tts_settings.json + venv Piper):

  ./venv/bin/python smoke_piper_tts.py
"""

from __future__ import annotations

import hashlib
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import loki_direct as ld  # noqa: E402
import loki_piper_tts as lpt  # noqa: E402


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> int:
    merged = ld.load_tts_settings_merged()
    onnx = merged.get("piper_onnx")
    if isinstance(onnx, Path) and not onnx.is_file():
        onnx = None
    pvm = str(merged.get("piper_voice_module") or "").strip()
    pdd = merged.get("piper_data_dir")
    if not isinstance(pdd, Path):
        pdd = ld.LOKI_PIPER_DATA_DIR
    pbin = str(merged.get("piper_binary") or "piper")
    spk = merged.get("piper_speaker_id")

    if not pvm:
        print("SKIP: merged settings have no piper_voice_module / voice id")
        return 0

    text = "Smoke test one two three."
    base = dict(
        onnx_path=onnx if isinstance(onnx, Path) else None,
        voice_module=pvm,
        data_dir=pdd,
        piper_binary=pbin,
        length_scale=1.0,
        volume=1.0,
        sentence_silence=0.0,
        speaker_id=spk,
    )

    pairs = [
        (0.18, 0.3),
        (1.2, 1.4),
        (0.667, 0.8),
    ]
    hashes: list[str] = []
    for ns, nw in pairs:
        w = lpt.synthesize_piper_wav(text, noise_scale=ns, noise_w_scale=nw, **base)
        if not w or not w.is_file() or w.stat().st_size < 500:
            print(f"FAIL: synthesis for noise_scale={ns} noise_w_scale={nw} -> {w!r}")
            return 1
        hashes.append(_sha16(w))
        w.unlink(missing_ok=True)

    if hashes[0] == hashes[1]:
        print(f"FAIL: extreme noise settings produced identical audio ({hashes[0]})")
        return 1

    print(f"OK: Piper noise params change output ({hashes[0]} vs {hashes[1]}, mid={hashes[2]})")

    # Regression: 0.05s pause used to map to an odd byte gap at 22050 Hz → static after sentence 1.
    text2 = "Hello. Second sentence here."
    w2 = lpt.synthesize_piper_wav(
        text2,
        noise_scale=0.66,
        noise_w_scale=0.8,
        sentence_silence=0.05,
        **{k: v for k, v in base.items() if k != "sentence_silence"},
    )
    if not w2 or not w2.is_file():
        print("FAIL: sentence-silence synthesis returned no file")
        return 1
    try:
        wf = wave.open(str(w2), "rb")
        fr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
        wf.close()
        samples = struct.unpack("<" + "h" * (len(raw) // 2), raw)
        drop = min(len(samples), int(fr * 1.0))
        tail = samples[drop:]
        peak = max(abs(x) for x in tail) if tail else 0
        if peak >= 32000:
            print(
                f"FAIL: sentence_silence=0.05 likely still misaligned (tail peak={peak}; expect speech-level < 32000)"
            )
            return 1
        print(f"OK: sentence gap alignment (tail peak after 1s={peak}, no PCM corruption)")
    finally:
        w2.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
