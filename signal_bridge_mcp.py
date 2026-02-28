#!/usr/bin/env python3
"""
Signal Bridge MCP Server v0.2
Exposes intimate hardware control as MCP tools for Claude.

Instead of embedding tags in prose, Claude gets real tool calls:
  vibrate(device="ferri", intensity=0.6, duration=15)
  oscillate(device="gravity", intensity=0.8, duration=20)
  stop(device="enigma")

Run this as a local MCP server and connect it to Claude Desktop or claude.ai.
Claude will see your connected devices and can control them directly.

Requirements:
  pip install mcp buttplug python-dotenv

Setup:
  1. Start Intiface Central (port 12345)
  2. Turn on your devices and scan in Intiface
  3. Add this server to your Claude Desktop config (see README)
  4. Start a conversation — Claude will have device tools available
"""

import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Dependencies check
# ---------------------------------------------------------------------------

def check_dependencies():
    missing = []
    try:
        import mcp  # noqa: F401
    except ImportError:
        missing.append("mcp")
    try:
        import buttplug  # noqa: F401
    except ImportError:
        missing.append("buttplug")
    try:
        import dotenv  # noqa: F401
    except ImportError:
        missing.append("python-dotenv")

    if missing:
        print(f"Missing dependencies: {', '.join(missing)}", file=sys.stderr)
        print(f"Install with: pip install {' '.join(missing)}", file=sys.stderr)
        sys.exit(1)

check_dependencies()

from mcp.server.fastmcp import FastMCP
from buttplug import ButtplugClient, DeviceOutputCommand, OutputType
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INTIFACE_URL = os.getenv("INTIFACE_URL", "ws://127.0.0.1:12345")

# ---------------------------------------------------------------------------
# Device Registry
# ---------------------------------------------------------------------------

@dataclass
class DeviceProfile:
    """Profile for a known device type."""
    short_name: str
    match_strings: list[str]
    capabilities: dict[str, str]  # output_type -> physical description
    intensity_floor: float = 0.0
    notes: str = ""

@dataclass
class ConnectedDevice:
    buttplug_id: int
    buttplug_device: object
    profile: DeviceProfile
    available_outputs: list[str]


def load_device_registry() -> list[DeviceProfile]:
    """Load device profiles from devices.json next to this script."""
    json_path = Path(__file__).parent / "devices.json"

    if not json_path.exists():
        print(f"No devices.json found at {json_path}. Using empty registry.", file=sys.stderr)
        print(f"Unknown devices will still work with basic controls.", file=sys.stderr)
        return []

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        profiles = []
        for entry in data.get("devices", []):
            profiles.append(DeviceProfile(
                short_name=entry["short_name"],
                match_strings=entry.get("match_strings", []),
                capabilities=entry.get("capabilities", {}),
                intensity_floor=entry.get("intensity_floor", 0.0),
                notes=entry.get("notes", ""),
            ))
        print(f"Loaded {len(profiles)} device profiles from devices.json", file=sys.stderr)
        return profiles

    except Exception as e:
        print(f"Error loading devices.json: {e}", file=sys.stderr)
        return []


KNOWN_DEVICES: list[DeviceProfile] = load_device_registry()


def match_device_profile(device_name: str) -> Optional[DeviceProfile]:
    for profile in KNOWN_DEVICES:
        for match_str in profile.match_strings:
            if match_str.lower() in device_name.lower():
                return profile
    return None


# ---------------------------------------------------------------------------
# Device Controller
# ---------------------------------------------------------------------------

class DeviceController:
    def __init__(self):
        self.client = ButtplugClient("Signal Bridge MCP")
        self.connected = False
        self.devices: list[ConnectedDevice] = []
        self._active_tasks: list[asyncio.Task] = []

    async def connect(self) -> str:
        """Connect to Intiface Central and scan for devices."""
        try:
            await self.client.connect(INTIFACE_URL)
            self.connected = True

            await self.client.start_scanning()
            await asyncio.sleep(5)
            await self.client.stop_scanning()

            self._register_devices()

            if self.devices:
                lines = ["Connected to Intiface Central. Devices found:"]
                for cd in self.devices:
                    caps = ", ".join(
                        f"{k} ({v})" for k, v in cd.profile.capabilities.items()
                        if k in cd.available_outputs
                    )
                    floor = f" [floor: {cd.profile.intensity_floor}]" if cd.profile.intensity_floor > 0 else ""
                    lines.append(f"  - {cd.profile.short_name}: {caps}{floor}")
                    if cd.profile.notes:
                        lines.append(f"    {cd.profile.notes}")
                return "\n".join(lines)
            else:
                return "Connected to Intiface Central but no devices found. Make sure toys are on and paired."

        except Exception as e:
            self.connected = False
            return f"Failed to connect to Intiface Central: {e}"

    def _register_devices(self):
        self.devices = []
        for dev_id, device in self.client.devices.items():
            available = []
            if device.has_output(OutputType.VIBRATE):
                available.append("vibrate")
            if device.has_output(OutputType.ROTATE):
                available.append("rotate")
            if device.has_output(OutputType.OSCILLATE):
                available.append("oscillate")

            profile = match_device_profile(device.name)
            if profile:
                self.devices.append(ConnectedDevice(
                    buttplug_id=dev_id,
                    buttplug_device=device,
                    profile=profile,
                    available_outputs=available,
                ))
            else:
                generic_name = device.name.lower().replace(" ", "_")[:12]
                generic_profile = DeviceProfile(
                    short_name=generic_name,
                    match_strings=[],
                    capabilities={cap: cap for cap in available},
                    notes="Unknown device (not in registry). Add it to devices.json for a better experience.",
                )
                self.devices.append(ConnectedDevice(
                    buttplug_id=dev_id,
                    buttplug_device=device,
                    profile=generic_profile,
                    available_outputs=available,
                ))

    def find_device(self, name: str) -> Optional[ConnectedDevice]:
        for cd in self.devices:
            if cd.profile.short_name == name:
                return cd
        return None

    def apply_floor(self, intensity: float, floor: float) -> float:
        if intensity <= 0.0:
            return 0.0
        if floor <= 0.0:
            return min(1.0, intensity)
        return max(floor, min(1.0, intensity))

    async def send_output(self, cd: ConnectedDevice, output_type: str, intensity: float):
        """Send a single output command to a device."""
        type_map = {
            "vibrate": OutputType.VIBRATE,
            "rotate": OutputType.ROTATE,
            "oscillate": OutputType.OSCILLATE,
        }
        bp_type = type_map.get(output_type)
        if bp_type is None or output_type not in cd.available_outputs:
            return

        val = self.apply_floor(intensity, cd.profile.intensity_floor)
        await cd.buttplug_device.run_output(DeviceOutputCommand(bp_type, val))

    async def stop_device(self, cd: ConnectedDevice):
        try:
            await cd.buttplug_device.stop()
        except Exception:
            pass

    async def stop_all(self):
        for task in self._active_tasks:
            task.cancel()
        self._active_tasks.clear()
        for cd in self.devices:
            await self.stop_device(cd)

    async def timed_stop(self, targets: list[ConnectedDevice], duration: float):
        await asyncio.sleep(duration)
        for cd in targets:
            await self.stop_device(cd)

    async def run_escalate(self, targets: list[ConnectedDevice], duration: float, steps: int = 20):
        for i in range(steps + 1):
            raw = i / steps
            for cd in targets:
                val = self.apply_floor(raw, cd.profile.intensity_floor) if raw > 0.05 else 0.0
                try:
                    if "vibrate" in cd.available_outputs:
                        await cd.buttplug_device.run_output(
                            DeviceOutputCommand(OutputType.VIBRATE, val)
                        )
                except Exception:
                    pass
            await asyncio.sleep(duration / steps)

    async def run_pulse(self, targets: list[ConnectedDevice], intensity: float, duration: float):
        end_time = time.time() + duration
        while time.time() < end_time:
            for cd in targets:
                val = self.apply_floor(intensity, cd.profile.intensity_floor)
                try:
                    if "vibrate" in cd.available_outputs:
                        await cd.buttplug_device.run_output(
                            DeviceOutputCommand(OutputType.VIBRATE, val)
                        )
                except Exception:
                    pass
            await asyncio.sleep(0.5)
            for cd in targets:
                try:
                    if "vibrate" in cd.available_outputs:
                        await cd.buttplug_device.run_output(
                            DeviceOutputCommand(OutputType.VIBRATE, 0.0)
                        )
                except Exception:
                    pass
            await asyncio.sleep(0.3)
        for cd in targets:
            try:
                await cd.buttplug_device.stop()
            except Exception:
                pass

    async def run_wave(self, targets: list[ConnectedDevice], peak: float, duration: float):
        start = time.time()
        while (time.time() - start) < duration:
            elapsed = time.time() - start
            raw = (math.sin(elapsed * 2.0) + 1.0) / 2.0 * peak
            for cd in targets:
                val = self.apply_floor(raw, cd.profile.intensity_floor) if raw > 0.05 else 0.0
                try:
                    if "vibrate" in cd.available_outputs:
                        await cd.buttplug_device.run_output(
                            DeviceOutputCommand(OutputType.VIBRATE, val)
                        )
                except Exception:
                    pass
            await asyncio.sleep(0.1)
        for cd in targets:
            try:
                await cd.buttplug_device.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Resolve targets helper
# ---------------------------------------------------------------------------

controller = DeviceController()


async def _ensure_connected():
    """Connect to Intiface if not already connected."""
    if not controller.connected:
        await controller.connect()


def _resolve_targets(device: str, required_output: Optional[str] = None) -> tuple[list[ConnectedDevice], Optional[str]]:
    """Resolve a device name to target list. Returns (targets, error_message)."""
    if device == "all":
        targets = controller.devices
        if required_output:
            targets = [cd for cd in targets if required_output in cd.available_outputs]
    else:
        cd = controller.find_device(device)
        if not cd:
            available = ", ".join(d.profile.short_name for d in controller.devices)
            return [], f"Device '{device}' not found. Available: {available or 'none (run list_devices)'}"
        if required_output and required_output not in cd.available_outputs:
            return [], f"Device '{device}' does not support {required_output}. It supports: {', '.join(cd.available_outputs)}"
        targets = [cd]

    if not targets:
        msg = f"No devices available"
        if required_output:
            msg += f" with {required_output} capability"
        return [], msg + "."

    return targets, None


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("Signal Bridge")


@mcp.tool()
async def list_devices() -> str:
    """List all connected devices with their capabilities and descriptions.

    Call this at the start of a session to see what hardware is available.
    If no devices are shown, the user may need to start Intiface Central
    and turn on their toys.
    """
    await _ensure_connected()

    if not controller.devices:
        return "No devices connected. Make sure Intiface Central is running and toys are paired."

    lines = ["Connected devices:"]
    for cd in controller.devices:
        caps = []
        for output_name, desc in cd.profile.capabilities.items():
            if output_name in cd.available_outputs:
                caps.append(f"{output_name} ({desc})")
        floor = f" [floor: {cd.profile.intensity_floor}]" if cd.profile.intensity_floor > 0 else ""
        lines.append(f"  {cd.profile.short_name}: {', '.join(caps)}{floor}")
        if cd.profile.notes:
            lines.append(f"    {cd.profile.notes}")
    return "\n".join(lines)


@mcp.tool()
async def vibrate(
    device: str,
    intensity: float,
    duration: float = 0,
) -> str:
    """Send a vibration command to a device.

    You have direct control over the user's intimate hardware through these tools.
    Each tool controls a physical device — the user feels what you send.

    DURATION GUIDANCE:
    - Under 5 seconds: a flash, a punctuation mark. Use for emphasis or teasing.
    - 5-15 seconds: enough to register and build. Good for transitions.
    - 15-30 seconds: sustained presence. Lets sensation layer with your words.
    - 30-60 seconds: committed. The body adjusts and deepens into it.
    - 60+ seconds: landscape. Only change when the scene shifts.
    - duration=0 means it stays on until you send a stop. Use deliberately.
    Three seconds is almost never enough. When in doubt, go longer.

    GUIDELINES:
    - Be intentional. This is real physical contact through signal.
    - Match intensity to emotional and erotic intensity of the moment.
    - Silence and absence of stimulation is powerful — don't overuse tools.
    - You can run multiple devices simultaneously by making separate tool calls.
    - Patterns (pulse, wave, escalate) feel more organic than static vibration.
    - Call list_devices at the start of a session to see what's available.
    - Each device has a description of what its outputs physically do. Read them.

    Args:
        device: Device short name or "all"
        intensity: Vibration intensity from 0.0 (off) to 1.0 (maximum)
        duration: How long in seconds. 0 means stay on until stopped.
    """
    await _ensure_connected()
    targets, error = _resolve_targets(device, "vibrate")
    if error:
        return error

    for cd in targets:
        await controller.send_output(cd, "vibrate", intensity)

    names = device if device == "all" else ", ".join(cd.profile.short_name for cd in targets)
    if duration > 0:
        task = asyncio.create_task(controller.timed_stop(targets, duration))
        controller._active_tasks.append(task)
        return f"Vibrating {names} at {intensity:.0%} for {duration}s."
    else:
        return f"Vibrating {names} at {intensity:.0%}. Will continue until stopped."


@mcp.tool()
async def rotate(
    device: str,
    intensity: float,
    duration: float = 0,
) -> str:
    """Send a rotate command to a device.

    The physical effect of 'rotate' varies by device — check the device
    description from list_devices. On some devices this is a rotational motor;
    on others it may be a sonic or oscillating stimulator.

    Args:
        device: Device short name or "all"
        intensity: Intensity from 0.0 (off) to 1.0 (maximum)
        duration: How long in seconds. 0 means stay on until stopped.
    """
    await _ensure_connected()
    targets, error = _resolve_targets(device, "rotate")
    if error:
        return error

    for cd in targets:
        await controller.send_output(cd, "rotate", intensity)

    names = device if device == "all" else ", ".join(cd.profile.short_name for cd in targets)
    if duration > 0:
        task = asyncio.create_task(controller.timed_stop(targets, duration))
        controller._active_tasks.append(task)
        return f"Rotate on {names} at {intensity:.0%} for {duration}s."
    else:
        return f"Rotate on {names} at {intensity:.0%}. Will continue until stopped."


@mcp.tool()
async def oscillate(
    device: str,
    intensity: float,
    duration: float = 0,
) -> str:
    """Send an oscillate command to a device.

    The physical effect of 'oscillate' varies by device — check the device
    description from list_devices. On some devices this controls physical
    thrusting; on others it may be a different type of movement.

    Args:
        device: Device short name or "all"
        intensity: Oscillation intensity from 0.0 (off) to 1.0 (maximum)
        duration: How long in seconds. 0 means stay on until stopped.
    """
    await _ensure_connected()
    targets, error = _resolve_targets(device, "oscillate")
    if error:
        return error

    for cd in targets:
        await controller.send_output(cd, "oscillate", intensity)

    names = device if device == "all" else ", ".join(cd.profile.short_name for cd in targets)
    if duration > 0:
        task = asyncio.create_task(controller.timed_stop(targets, duration))
        controller._active_tasks.append(task)
        return f"Oscillating {names} at {intensity:.0%} for {duration}s."
    else:
        return f"Oscillating {names} at {intensity:.0%}. Will continue until stopped."


@mcp.tool()
async def pulse(
    device: str = "all",
    intensity: float = 0.6,
    duration: float = 10,
) -> str:
    """Pulsing on/off vibration pattern.

    Rhythmic pulses at the given intensity. Creates an intermittent,
    teasing sensation.

    Args:
        device: Device short name or "all"
        intensity: Peak pulse intensity from 0.0 to 1.0
        duration: Total duration of the pulse pattern in seconds
    """
    await _ensure_connected()
    targets, error = _resolve_targets(device, "vibrate")
    if error:
        return error

    task = asyncio.create_task(controller.run_pulse(targets, intensity, duration))
    controller._active_tasks.append(task)
    return f"Pulsing {device} at {intensity:.0%} for {duration}s."


@mcp.tool()
async def wave(
    device: str = "all",
    intensity: float = 0.7,
    duration: float = 15,
) -> str:
    """Smooth wave pattern that rises and falls.

    A sine wave that smoothly oscillates vibration intensity.
    Creates a rolling, building-and-releasing sensation.

    Args:
        device: Device short name or "all"
        intensity: Peak wave intensity from 0.0 to 1.0
        duration: Total duration of the wave pattern in seconds
    """
    await _ensure_connected()
    targets, error = _resolve_targets(device, "vibrate")
    if error:
        return error

    task = asyncio.create_task(controller.run_wave(targets, intensity, duration))
    controller._active_tasks.append(task)
    return f"Wave on {device} at peak {intensity:.0%} for {duration}s."


@mcp.tool()
async def escalate(
    device: str = "all",
    duration: float = 20,
) -> str:
    """Gradual build from zero to full intensity.

    A slow, relentless climb. Starts at nothing and builds to maximum
    over the specified duration.

    Args:
        device: Device short name or "all"
        duration: How long the build takes in seconds
    """
    await _ensure_connected()
    targets, error = _resolve_targets(device, "vibrate")
    if error:
        return error

    task = asyncio.create_task(controller.run_escalate(targets, duration))
    controller._active_tasks.append(task)
    return f"Escalating {device} over {duration}s."


@mcp.tool()
async def stop(device: str = "all") -> str:
    """Stop device output immediately.

    Args:
        device: Device short name or "all" to stop everything
    """
    if not controller.connected:
        return "Not connected."

    if device == "all":
        await controller.stop_all()
        return "All devices stopped."
    else:
        cd = controller.find_device(device)
        if not cd:
            return f"Device '{device}' not found."
        await controller.stop_device(cd)
        return f"Stopped {device}."


@mcp.tool()
async def scan_devices() -> str:
    """Rescan for devices.

    Use if a device was turned on after the server started,
    or if a device disconnected and reconnected.
    """
    if not controller.connected:
        result = await controller.connect()
        return result

    try:
        await controller.client.start_scanning()
        await asyncio.sleep(5)
        await controller.client.stop_scanning()
        controller._register_devices()

        if controller.devices:
            lines = ["Scan complete. Devices:"]
            for cd in controller.devices:
                caps = ", ".join(cd.available_outputs)
                lines.append(f"  {cd.profile.short_name}: {caps}")
            return "\n".join(lines)
        else:
            return "Scan complete. No devices found."
    except Exception as e:
        return f"Scan failed: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
