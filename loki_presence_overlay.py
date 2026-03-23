#!/usr/bin/env python3
"""
L041 Presence Overlay (desktop, outside browser).

A small always-on-top orb that reflects Web UI presence states:
idle / listening / thinking / speaking.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from typing import Any, Dict

try:
    import tkinter as tk
except ModuleNotFoundError as e:
    if "_tkinter" in str(e) or "tkinter" in str(e).lower():
        raise SystemExit(
            "Tkinter is not available in this Python build (missing _tkinter).\n"
            "On macOS: run the overlay with Apple Python: /usr/bin/python3 loki_presence_overlay.py\n"
            "Or install: brew install python-tk@3.13\n"
            f"Current interpreter: {__import__('sys').executable}"
        ) from e
    raise


PRESENCE_URL = os.getenv("LOKI_OVERLAY_PRESENCE_URL", "http://127.0.0.1:7865/api/presence")
OVERLAY_SIZE = int(os.getenv("LOKI_OVERLAY_SIZE", "96"))
OVERLAY_ALPHA = max(0.2, min(1.0, float(os.getenv("LOKI_OVERLAY_ALPHA", "0.92"))))
OVERLAY_X = int(os.getenv("LOKI_OVERLAY_X", "24"))
OVERLAY_Y = int(os.getenv("LOKI_OVERLAY_Y", "24"))
OVERLAY_HUE_SHIFT_DEG = float(os.getenv("LOKI_OVERLAY_HUE_SHIFT_DEG", "0"))
POLL_MS = max(120, int(os.getenv("LOKI_OVERLAY_POLL_MS", "250")))


def _hex_rgb(r: int, g: int, b: int) -> str:
    return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,b)):02x}"


def _shift_rgb(rgb: tuple[int, int, int], deg: float) -> tuple[int, int, int]:
    # Lightweight hue-ish rotation for mood tinting without extra deps.
    rad = math.radians(deg)
    c = math.cos(rad)
    s = math.sin(rad)
    r, g, b = [x / 255.0 for x in rgb]
    nr = r * (0.299 + 0.701 * c + 0.168 * s) + g * (0.587 - 0.587 * c + 0.330 * s) + b * (0.114 - 0.114 * c - 0.497 * s)
    ng = r * (0.299 - 0.299 * c - 0.328 * s) + g * (0.587 + 0.413 * c + 0.035 * s) + b * (0.114 - 0.114 * c + 0.292 * s)
    nb = r * (0.299 - 0.300 * c + 1.250 * s) + g * (0.587 - 0.588 * c - 1.050 * s) + b * (0.114 + 0.886 * c - 0.203 * s)
    return (int(max(0, min(1, nr)) * 255), int(max(0, min(1, ng)) * 255), int(max(0, min(1, nb)) * 255))


BASE_COLORS: Dict[str, tuple[int, int, int]] = {
    "idle": (100, 110, 128),
    "listening": (65, 214, 255),
    "thinking": (255, 188, 76),
    "speaking": (112, 165, 255),
}


class Overlay:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("L041 Overlay")
        self.root.configure(bg="#000000")
        self.root.geometry(f"{OVERLAY_SIZE}x{OVERLAY_SIZE}+{OVERLAY_X}+{OVERLAY_Y}")
        self.root.withdraw()

        self.canvas = tk.Canvas(self.root, width=OVERLAY_SIZE, height=OVERLAY_SIZE, bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.state = "idle"
        self.last_ok = 0.0
        self._drag_start = None
        self._install_drag()
        self._tick()
        self._show_window()

    def _show_window(self) -> None:
        # Some macOS Tk builds can abort if WM flags are set too early.
        # Apply in a staged way and ignore unsupported flags.
        self.root.deiconify()
        self.root.update_idletasks()
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            self.root.attributes("-alpha", OVERLAY_ALPHA)
        except tk.TclError:
            pass
        try:
            self.root.overrideredirect(True)
        except tk.TclError:
            pass

    def _install_drag(self) -> None:
        def on_down(ev: tk.Event) -> None:
            self._drag_start = (ev.x_root, ev.y_root)

        def on_move(ev: tk.Event) -> None:
            if not self._drag_start:
                return
            sx, sy = self._drag_start
            dx = ev.x_root - sx
            dy = ev.y_root - sy
            gx = self.root.winfo_x() + dx
            gy = self.root.winfo_y() + dy
            self.root.geometry(f"+{gx}+{gy}")
            self._drag_start = (ev.x_root, ev.y_root)

        self.canvas.bind("<ButtonPress-1>", on_down)
        self.canvas.bind("<B1-Motion>", on_move)

    def _poll_presence(self) -> None:
        try:
            req = urllib.request.Request(
                PRESENCE_URL,
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.6) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            d: Dict[str, Any] = json.loads(raw) if raw.strip() else {}
            st = str(d.get("state") or "idle").lower()
            if st in BASE_COLORS:
                self.state = st
                self.last_ok = time.time()
        except Exception:
            # No update; renderer handles stale state.
            pass

    def _render(self) -> None:
        self.canvas.delete("all")
        now = time.time()
        stale = (now - self.last_ok) > 3.5
        st = "idle" if stale else self.state
        base = BASE_COLORS.get(st, BASE_COLORS["idle"])
        if OVERLAY_HUE_SHIFT_DEG:
            base = _shift_rgb(base, OVERLAY_HUE_SHIFT_DEG)

        t = now
        if st == "listening":
            amp = 0.16 + 0.10 * (0.5 + 0.5 * math.sin(t * 4.6))
        elif st == "thinking":
            amp = 0.20 + 0.12 * (0.5 + 0.5 * math.sin(t * 7.8))
        elif st == "speaking":
            amp = 0.22 + 0.14 * (0.5 + 0.5 * math.sin(t * 10.4))
        else:
            amp = 0.10 + 0.06 * (0.5 + 0.5 * math.sin(t * 2.0))

        r_outer = int((OVERLAY_SIZE * 0.37) + (OVERLAY_SIZE * amp * 0.18))
        r_inner = int(r_outer * 0.60)
        cx = OVERLAY_SIZE // 2
        cy = OVERLAY_SIZE // 2

        # Outer glow
        glow = _hex_rgb(int(base[0] * 0.45), int(base[1] * 0.45), int(base[2] * 0.45))
        self.canvas.create_oval(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer, fill=glow, outline="")
        # Inner core
        core = _hex_rgb(base[0], base[1], base[2])
        self.canvas.create_oval(cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner, fill=core, outline="")
        # Tiny badge in corner: S/L/T/I
        badge = {"speaking": "S", "listening": "L", "thinking": "T", "idle": "I"}[st]
        try:
            self.canvas.create_text(
                OVERLAY_SIZE - 11,
                OVERLAY_SIZE - 10,
                text=badge,
                fill="#e9edf4",
                font=("TkDefaultFont", 9, "bold"),
            )
        except tk.TclError:
            self.canvas.create_text(OVERLAY_SIZE - 11, OVERLAY_SIZE - 10, text=badge, fill="#e9edf4")

    def _tick(self) -> None:
        self._poll_presence()
        self._render()
        self.root.after(POLL_MS, self._tick)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    Overlay().run()


if __name__ == "__main__":
    main()

