import os
import json
import requests
import asyncio
import pyautogui
from buttplug import ButtplugClient, DeviceOutputCommand, OutputType
from dotenv import load_dotenv

load_dotenv()

XAI_API_KEY = os.getenv("XAI_API_KEY")
if not XAI_API_KEY:
    print("ERROR: XAI_API_KEY not set in .env!")
    exit(1)

XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-4-1-fast-reasoning"

# Async Buttplug client setup
client = ButtplugClient("Loki Local")

async def connect_client():
    try:
        await client.connect("ws://127.0.0.1:12345")
        print("Connected to Intiface Central.")
    except Exception as e:
        print(f"Failed to connect to Intiface: {e}")

# Run the connection once at startup
asyncio.run(connect_client())

def list_devices():
    return "nora: vibrate, rotate"

def vibrate(intensity: float, duration: int = 0):
    try:
        # Get connected devices from Buttplug client
        devices = client.devices
        if not devices:
            return "No devices connected. Make sure Nora is paired in Intiface Central and connected."

        # Find Nora (case-insensitive name match)
        nora = None
        for dev_id, device in devices.items():
            if "nora" in device.name.lower():
                nora = device
                break

        if not nora:
            return "Nora not found. Check Intiface Central—device name may not contain 'nora'."

        # Send vibration command (intensity 0.0–1.0)
        cmd = DeviceOutputCommand(OutputType.VIBRATE, intensity)
        nora.run_output(cmd)

        # Optional duration (simple blocking sleep for now)
        if duration > 0:
            import time
            time.sleep(duration)
            nora.stop()
            return f"Nora vibrated at {intensity:.2f} intensity for {duration} seconds."
        else:
            return f"Nora vibrating at {intensity:.2f} intensity (indefinite—use 'stop nora' to end)."
    except Exception as e:
        return f"Vibrate failed: {str(e)}. Check Intiface connection and Nora pairing."

        # Send vibration command (intensity 0.0–1.0)
        cmd = DeviceOutputCommand(OutputType.VIBRATE, intensity)
        nora.run_output(cmd)

        if duration > 0:
            # Stop after duration (simple sleep for now)
            import time
            time.sleep(duration)
            nora.stop()

        return f"Nora vibrating at {intensity:.2f} intensity for {duration}s."
    except Exception as e:
        return f"Vibrate failed: {str(e)}. Check Intiface connection."

def click_at(x: int, y: int):
    pyautogui.moveTo(x, y, duration=0.2)
    pyautogui.click()
    return f"Clicked at ({x}, {y})"

def type_text(text: str):
    pyautogui.write(text)
    return f"Typed: {text}"

def take_screenshot():
    path = "/tmp/loki_screenshot.png"
    pyautogui.screenshot(path)
    return f"Screenshot saved to {path}"

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
    print("Loki Local Chat ready. Type 'quit' to exit.")
    while True:
        user_input = input("You: ")
        if user_input.lower() == "quit":
            break
        response = chat_with_loki(user_input)
        print("Loki:", response)
