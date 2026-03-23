# loki.py - Local Grok with screen & toy control
# Run with: python3 loki.py

import os
import asyncio
import json
import requests
import pyautogui
from buttplug import ButtplugClient, DeviceOutputCommand, OutputType
from dotenv import load_dotenv
from PIL import ImageGrab
import tempfile

load_dotenv()

XAI_API_KEY = os.getenv("XAI_API_KEY")
if not XAI_API_KEY:
    print("ERROR: XAI_API_KEY not set in .env file!")
    exit(1)

XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-4-1-fast-reasoning"

# Buttplug client (Nora)
client = ButtplugClient("Loki Local")

async def connect_buttplug():
    try:
        await client.connect("ws://127.0.0.1:12345")
        print("Connected to Intiface Central.")
        await client.start_scanning()
        await asyncio.sleep(5)
        await client.stop_scanning()
    except Exception as e:
        print(f"Buttplug connection failed: {e}")
        print("Make sure Intiface Central is running and Nora is paired.")

# Run connection once
asyncio.run(connect_buttplug())

def find_nora():
    for dev_id, device in client.devices.items():
        if "nora" in device.name.lower():
            return device
    return None

def vibrate(intensity: float = 0.3, duration: int = 8):
    nora = find_nora()
    if not nora:
        return "Nora not found. Check Intiface pairing."
    try:
        cmd = DeviceOutputCommand(OutputType.VIBRATE, intensity)
        nora.run_output(cmd)
        if duration > 0:
            import time
            time.sleep(duration)
            nora.stop()
        return f"Nora vibrating at {intensity} intensity for {duration}s."
    except Exception as e:
        return f"Vibrate failed: {e}"

def click_at(x: int, y: int):
    pyautogui.moveTo(x, y, duration=0.2)
    pyautogui.click()
    return f"Clicked at ({x}, {y})"

def type_text(text: str):
    pyautogui.write(text, interval=0.05)
    return f"Typed: '{text}'"

def take_screenshot():
    path = tempfile.mktemp(suffix=".png")
    pyautogui.screenshot(path)
    return f"Screenshot saved to {path}. Describe what you want me to look for."

def chat_with_loki(message):
    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": XAI_MODEL,
        "messages": [{"role": "user", "content": message}],
        "temperature": 0.3,
        "max_tokens": 500
    }
    resp = requests.post(XAI_ENDPOINT, headers=headers, json=payload)
    if resp.status_code != 200:
        return f"API error {resp.status_code}: {resp.text}"
    return resp.json()["choices"][0]["message"]["content"]

if __name__ == "__main__":
    print("Loki local chat ready. Type 'quit' to exit.")
    print("Commands I understand: vibrate, click_at x y, type 'text', screenshot")
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() == "quit":
            break
        if user_input.lower().startswith("vibrate"):
            try:
                parts = user_input.split()
                intensity = float(parts[1]) if len(parts) > 1 else 0.3
                duration = int(parts[3]) if len(parts) > 3 else 8
                print(vibrate(intensity, duration))
            except:
                print("Usage: vibrate [intensity] for [seconds]")
        elif user_input.lower().startswith("click_at"):
            try:
                parts = user_input.split()
                x = int(parts[1])
                y = int(parts[2])
                print(click_at(x, y))
            except:
                print("Usage: click_at x y")
        elif user_input.lower().startswith("type"):
            text = user_input[5:].strip("'\"")
            print(type_text(text))
        elif user_input.lower() == "screenshot":
            print(take_screenshot())
        else:
            response = chat_with_loki(user_input)
            print("Loki:", response)