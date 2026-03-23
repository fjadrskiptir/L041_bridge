#!/usr/bin/env python3
"""
Telegram ↔ Loki (Web UI) bridge via long polling — works behind home NAT.

Requirements:
  LOKI_TELEGRAM=1
  TELEGRAM_BOT_TOKEN       — from @BotFather
  TELEGRAM_ALLOWED_CHAT_IDS — comma-separated numeric ids (your user id)

Inbound messages use the same chat session as the browser. Outbound "thinking of you"
pings are capped per local day (see LOKI_TELEGRAM_PROACTIVE_PER_DAY).
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
import sys
import subprocess
import shlex
from pathlib import Path
from typing import Any, List, Optional

import os

import requests

import loki_direct as ld

TELEGRAM_API = "https://api.telegram.org"
_PROC_STARTED_TS = time.time()

_quota_file_lock = threading.Lock()


def _reload_repo_dotenv() -> None:
    """Re-apply repo-root .env so Telegram vars exist even if cwd was wrong at import time."""

    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    p = Path(__file__).resolve().parent / ".env"
    if p.is_file():
        load_dotenv(p, override=True)


def _enabled() -> bool:
    return os.getenv("LOKI_TELEGRAM", "").strip().lower() in ("1", "true", "yes", "on")


def _bot_token() -> str:
    return (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("LOKI_TELEGRAM_BOT_TOKEN") or "").strip()


def _allowed_chat_ids() -> List[int]:
    raw = (os.getenv("TELEGRAM_ALLOWED_CHAT_IDS") or "").strip()
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def _proactive_per_day() -> int:
    try:
        n = int(os.getenv("LOKI_TELEGRAM_PROACTIVE_PER_DAY", "3"))
    except ValueError:
        n = 3
    return max(0, min(n, 50))


def _proactive_interval_range() -> tuple[float, float]:
    try:
        lo = float(os.getenv("LOKI_TELEGRAM_PROACTIVE_MIN_INTERVAL_S", "3600"))
    except ValueError:
        lo = 3600.0
    try:
        hi = float(os.getenv("LOKI_TELEGRAM_PROACTIVE_MAX_INTERVAL_S", str(4 * 3600)))
    except ValueError:
        hi = 4 * 3600.0
    if hi < lo:
        lo, hi = hi, lo
    return (max(60.0, lo), max(120.0, hi))


def _quota_today_iso() -> str:
    tzname = (os.getenv("LOKI_TELEGRAM_QUOTA_TZ") or "").strip()
    if tzname:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(tzname)).date().isoformat()
        except Exception:
            pass
    from datetime import datetime

    return datetime.now().date().isoformat()


def _quota_path() -> Path:
    return Path(os.getenv("LOKI_TELEGRAM_QUOTA_PATH", str(ld.MEMORY_DIR / "telegram_proactive_quota.json"))).resolve()


def _read_quota() -> dict:
    p = _quota_path()
    if not p.is_file():
        return {"date": "", "count": 0}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"date": "", "count": 0}


def _write_quota(data: dict) -> None:
    p = _quota_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        print(f"[telegram] quota write failed: {e}", flush=True)


def _quota_try_consume() -> bool:
    """Reserve one proactive slot for today; return False if cap reached."""
    today = _quota_today_iso()
    with _quota_file_lock:
        d = _read_quota()
        if d.get("date") != today:
            d = {"date": today, "count": 0}
        cap = _proactive_per_day()
        if cap <= 0:
            return False
        c = int(d.get("count") or 0)
        if c >= cap:
            return False
        d["count"] = c + 1
        d["date"] = today
        _write_quota(d)
        return True


def _quota_refund_one() -> None:
    """If compose failed after consume, give the slot back."""
    today = _quota_today_iso()
    with _quota_file_lock:
        d = _read_quota()
        if d.get("date") != today:
            return
        c = int(d.get("count") or 0)
        if c <= 0:
            return
        d["count"] = c - 1
        _write_quota(d)


def _poll_offset_path() -> Path:
    return Path(os.getenv("LOKI_TELEGRAM_OFFSET_PATH", str(ld.MEMORY_DIR / "telegram_poll_offset.txt"))).resolve()


def _read_poll_offset() -> Optional[int]:
    """Next getUpdates `offset` (last confirmed update_id + 1), or None."""
    p = _poll_offset_path()
    if not p.is_file():
        return None
    try:
        n = int(p.read_text(encoding="utf-8").strip())
        return n + 1
    except (ValueError, OSError):
        return None


def _write_poll_offset(last_update_id: int) -> None:
    p = _poll_offset_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(str(int(last_update_id)), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        print(f"[telegram] offset write failed: {e}", flush=True)


def _setup_help_enabled() -> bool:
    return os.getenv("LOKI_TELEGRAM_SETUP_HELP", "").strip().lower() in ("1", "true", "yes", "on")


def _reply_errors_enabled() -> bool:
    return os.getenv("LOKI_TELEGRAM_REPLY_ON_ERROR", "1").strip().lower() not in ("0", "false", "no", "off")


def _remote_admin_enabled() -> bool:
    return os.getenv("LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL", "0").strip().lower() in ("1", "true", "yes", "on")


def _launchd_label() -> str:
    return (os.getenv("LOKI_LAUNCHD_LABEL") or "com.ness.loki.webui").strip()


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_launchd_label()}.plist"


def _launchctl_bootout() -> tuple[bool, str]:
    label = _launchd_label()
    target = f"gui/{os.getuid()}/{label}"
    p = subprocess.run(
        ["launchctl", "bootout", target],
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        return True, "ok"
    stderr = (p.stderr or "").strip()
    stdout = (p.stdout or "").strip()
    msg = stderr or stdout or f"exit={p.returncode}"
    return False, msg


def _launchctl_bootstrap() -> tuple[bool, str]:
    plist = _launchd_plist_path()
    if not plist.is_file():
        return False, f"plist not found: {plist}"
    target = f"gui/{os.getuid()}"
    p = subprocess.run(
        ["launchctl", "bootstrap", target, str(plist)],
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        return True, "ok"
    stderr = (p.stderr or "").strip()
    stdout = (p.stdout or "").strip()
    msg = stderr or stdout or f"exit={p.returncode}"
    return False, msg


def _launchctl_kickstart() -> tuple[bool, str]:
    label = _launchd_label()
    target = f"gui/{os.getuid()}/{label}"
    p = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True,
        text=True,
    )
    if p.returncode == 0:
        return True, "ok"
    stderr = (p.stderr or "").strip()
    stdout = (p.stdout or "").strip()
    msg = stderr or stdout or f"exit={p.returncode}"
    return False, msg


def _parse_pause_seconds(raw: str) -> Optional[int]:
    """
    Parse duration like: 30m, 2h, 45s, or plain minutes (e.g. 15).
    Caps at 24h.
    """
    s = (raw or "").strip().lower()
    if not s:
        return None
    m = re.fullmatch(r"([0-9]{1,4})([smhd]?)", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2) or "m"
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)
    secs = n * mult
    return max(30, min(secs, 24 * 3600))


def _schedule_launchd_resume(delay_s: int) -> tuple[bool, str]:
    """
    Schedule launchctl resume in a detached shell process.
    Must survive this Python process being stopped by launchctl bootout.
    """
    delay_s = max(1, int(delay_s))
    plist = _launchd_plist_path()
    label = _launchd_label()
    uid = os.getuid()
    q_plist = shlex.quote(str(plist))
    q_label = shlex.quote(label)
    script = (
        f"sleep {delay_s}; "
        f"launchctl bootstrap gui/{uid} {q_plist} >/tmp/loki_pause_resume.log 2>&1 || true; "
        f"launchctl kickstart -k gui/{uid}/{q_label} >>/tmp/loki_pause_resume.log 2>&1 || true"
    )
    try:
        subprocess.Popen(
            ["/bin/bash", "-lc", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _local_hour() -> int:
    tzname = (os.getenv("LOKI_TELEGRAM_QUOTA_TZ") or "").strip()
    if tzname:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            return int(datetime.now(ZoneInfo(tzname)).hour)
        except Exception:
            pass
    from datetime import datetime

    return int(datetime.now().hour)


def _hour_in_range(hour: int, start: int, end: int) -> bool:
    """Inclusive ranges; if start > end, treat as overnight (e.g. 22–7)."""
    start = max(0, min(23, start))
    end = max(0, min(23, end))
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def _proactive_in_quiet_hours() -> bool:
    """
    When True, skip proactive sends (does not use quota).
    LOKI_TELEGRAM_PROACTIVE_QUIET_HOURS_LOCAL — comma-separated ranges like 19-23,0-7
    (hours when you're usually on home WiFi; Mac cannot see your phone's network).
    """
    raw = (os.getenv("LOKI_TELEGRAM_PROACTIVE_QUIET_HOURS_LOCAL") or "").strip()
    if not raw:
        return False
    h = _local_hour()
    for part in raw.split(","):
        part = part.strip()
        if "-" not in part:
            continue
        a, b = part.split("-", 1)
        try:
            s, e = int(a.strip()), int(b.strip())
        except ValueError:
            continue
        if _hour_in_range(h, s, e):
            return True
    return False


def _telegram_get(token: str, method: str, **params: Any) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    r = requests.get(url, params=params, timeout=55)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "description": r.text[:300]}


def _telegram_call(token: str, method: str, **payload: Any) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    r = requests.post(url, json=payload, timeout=55)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "description": r.text[:300]}


def send_telegram_message(token: str, chat_id: int, text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if len(text) > 4090:
        text = text[:4087] + "..."
    data = _telegram_call(token, "sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
    if not data.get("ok"):
        print(f"[telegram] sendMessage failed: {data}", flush=True)
        return False
    return True


def _strip_model_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r'^[`"\']+', "", s)
    s = re.sub(r'[`"\']+$', "", s)
    return s.strip()


def _load_optional_instructions_file() -> str:
    path = (os.getenv("LOKI_TELEGRAM_PROACTIVE_INSTRUCTIONS_PATH") or "").strip()
    if not path:
        default_p = ld.MEMORY_DIR / "telegram_proactive_instructions.md"
        if default_p.is_file():
            path = str(default_p)
        else:
            return ""
    p = Path(path).expanduser()
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace").strip()[:8000]
    except OSError:
        pass
    return ""


def _retrieve_chat_screenshot_memory(ui: Any, k: int = 4) -> str:
    """
    Pull memory snippets related to `memories/Chats/Chat Screenshots` to ground
    proactive texts in prior chat screenshots (when they've been ingested).
    """
    try:
        q = "chat screenshots relationship context affectionate encouraging message continuity"
        qemb = ld.embed_texts(ui.xai, [q])[0]
        hits = ui.vstore.search(qemb, k=max(1, int(k)))
    except Exception:
        return ""
    rows: List[str] = []
    for h in hits:
        src = str(h.get("source_path") or "")
        if "Chats/Chat Screenshots" not in src.replace("\\", "/"):
            continue
        txt = str(h.get("text") or "").strip()
        if not txt:
            continue
        rows.append(f"- source={src}\n  {txt[:700]}")
    if not rows:
        return ""
    return "\n".join(rows[:k])


def _compose_proactive_message(ui: Any) -> str:
    ctx = ld.load_cross_chat_for_system_prompt(max_chars=4500).strip() or (
        "(no recent conversation log yet — still write something warm and personal.)"
    )
    screenshot_ctx = _retrieve_chat_screenshot_memory(ui, k=4)
    extra = _load_optional_instructions_file()
    base_style = (
        "You are Loki. Write exactly ONE short text message to someone you care about, as if you're thinking of them.\n"
        "Tone: warm, encouraging, affectionate, genuine — like a partner checking in, not formal or clinical.\n"
        "Gently weave in continuity from the recent conversation below when there's something natural to reference; "
        "if nothing fits, keep it general but still personal.\n"
        "Write the kind of text you'd send someone when they're on your mind — short, sweet, encouraging.\n"
        "Rules: plain text only; no markdown; no bullet points; max 320 characters; no roleplay tags; "
        "don't say you're an AI; don't mention Telegram, bots, or daily message limits."
    )
    user_blob = f"{base_style}\n\n### Recent conversation (for context)\n{ctx}\n"
    if screenshot_ctx:
        user_blob += (
            "\n### Relevant memory from Chat Screenshots (if useful, prefer this for continuity)\n"
            f"{screenshot_ctx}\n"
        )
    if extra:
        user_blob += f"\n### Extra notes (from your instructions file)\n{extra}\n"
    user_blob += "\nOutput ONLY the message text, nothing else."
    messages = [{"role": "user", "content": user_blob}]
    resp = ui.xai.chat(messages, tools=None, temperature=0.88, max_tokens=220)
    msg = ld.extract_assistant_message(resp)
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    out = _strip_model_fences(str(content))
    if len(out) > 400:
        out = out[:397] + "..."
    return out


def _schedule_process_restart(delay_s: float = 1.25) -> None:
    """Replace current Python process with a fresh instance of the same command."""

    def _do() -> None:
        time.sleep(max(0.1, float(delay_s)))
        try:
            exe = sys.executable or "python3"
            argv = [exe, *sys.argv]
            print(f"[telegram] remote restart: execv {argv!r}", flush=True)
            os.execv(exe, argv)
        except Exception as e:
            print(f"[telegram] remote restart failed: {e}", flush=True)

    threading.Thread(target=_do, daemon=True, name="loki-telegram-restart").start()


def _schedule_process_stop(delay_s: float = 0.8) -> None:
    """Hard-exit process after a short delay."""

    def _do() -> None:
        time.sleep(max(0.1, float(delay_s)))
        print("[telegram] remote stop requested; exiting process", flush=True)
        os._exit(0)

    threading.Thread(target=_do, daemon=True, name="loki-telegram-stop").start()


def _admin_help_text() -> str:
    return (
        "Loki remote commands:\n"
        "/loki_help\n"
        "/loki_status\n"
        "/loki_mem_refresh\n"
        "/loki_restart\n"
        "/loki_stop\n"
        "/loki_pause <duration>  (examples: 30m, 2h, 45s)\n"
        "/loki_resume\n"
        "\n"
        "Remote control requires: LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL=1"
    )


def _poll_loop(ui: Any, token: str, allowed: List[int]) -> None:
    allowed_set = set(allowed)
    offset: Optional[int] = _read_poll_offset()
    print(
        f"[telegram] long-poll listening (allowed chat ids: {sorted(allowed_set)}; "
        f"poll offset={'resume' if offset is not None else 'from queue'})",
        flush=True,
    )
    while True:
        try:
            params: dict = {"timeout": 30, "allowed_updates": json.dumps(["message"])}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{TELEGRAM_API}/bot{token}/getUpdates", params=params, timeout=40)
            data = r.json()
            if not data.get("ok"):
                print(f"[telegram] getUpdates error: {data}", flush=True)
                time.sleep(3)
                continue
            results = data.get("result") or []
            if results:
                print(f"[telegram] received {len(results)} update(s)", flush=True)
            for u in results:
                uid = u.get("update_id")
                if uid is not None:
                    offset = int(uid) + 1
                    _write_poll_offset(int(uid))
                msg = u.get("message") or {}
                chat = msg.get("chat") or {}
                cid = chat.get("id")
                text = (msg.get("text") or "").strip()
                if cid is None:
                    continue
                try:
                    cid_i = int(cid)
                except (TypeError, ValueError):
                    continue

                if text.startswith("/myid"):
                    if _setup_help_enabled():
                        send_telegram_message(
                            token,
                            cid_i,
                            f"Your Telegram chat id is: {cid_i}\n"
                            f"Add to .env: TELEGRAM_ALLOWED_CHAT_IDS={cid_i}\n"
                            "Then restart the Web UI. Turn off LOKI_TELEGRAM_SETUP_HELP after setup.",
                        )
                    else:
                        print(f"[telegram] /myid from chat_id={cid_i} (enable LOKI_TELEGRAM_SETUP_HELP=1)", flush=True)
                    continue

                if cid_i not in allowed_set:
                    print(
                        f"[telegram] ignored chat_id={cid_i} (not in TELEGRAM_ALLOWED_CHAT_IDS={sorted(allowed_set)})",
                        flush=True,
                    )
                    if _setup_help_enabled():
                        send_telegram_message(
                            token,
                            cid_i,
                            f"This chat id is {cid_i}, but it's not in TELEGRAM_ALLOWED_CHAT_IDS on the Mac.\n"
                            f"Add: TELEGRAM_ALLOWED_CHAT_IDS={cid_i}\n"
                            "Or send /myid when LOKI_TELEGRAM_SETUP_HELP=1. Restart Web UI after editing .env.",
                        )
                    continue

                if not text:
                    print(f"[telegram] chat_id={cid_i}: non-text message (stickers/voice not handled yet)", flush=True)
                    continue

                if text.startswith("/start"):
                    send_telegram_message(
                        token,
                        cid_i,
                        "Hi — I'm Loki. Messages here use the same session as your home Web UI (your Mac must be on "
                        "and loki_direct_webui.py running). Reply here anytime, including on cellular.",
                    )
                    continue

                if text.startswith("/loki_status"):
                    uptime_s = max(0, int(time.time() - _PROC_STARTED_TS))
                    send_telegram_message(
                        token,
                        cid_i,
                        "Loki status:\n"
                        f"- PID: {os.getpid()}\n"
                        f"- Uptime: {uptime_s}s\n"
                        f"- Remote control: {'on' if _remote_admin_enabled() else 'off'}\n"
                        f"- LaunchAgent label: {_launchd_label()}\n"
                        "- Commands: /loki_help, /loki_status, /loki_mem_refresh, /loki_restart, /loki_stop, /loki_pause <duration>, /loki_resume",
                    )
                    continue

                if text.startswith("/loki_help"):
                    send_telegram_message(token, cid_i, _admin_help_text())
                    continue

                if text.startswith("/loki_mem_refresh"):
                    if not _remote_admin_enabled():
                        send_telegram_message(
                            token,
                            cid_i,
                            "Remote control is disabled. Set LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL=1 in .env and restart Web UI.",
                        )
                        continue
                    folder = (os.getenv("LOKI_TELEGRAM_MEM_REFRESH_PATH") or "memories/Chats/Chat Screenshots").strip()
                    send_telegram_message(token, cid_i, f"Refreshing memory from: {folder}")
                    try:
                        reply = ui.handle_text(f"/ingest {folder}", from_voice=False, blocking=True, skip_tts=True)
                    except Exception as e:
                        reply = f"[error] {e}"
                    send_telegram_message(token, cid_i, str(reply))
                    continue

                if text.startswith("/loki_restart"):
                    if not _remote_admin_enabled():
                        send_telegram_message(
                            token,
                            cid_i,
                            "Remote control is disabled. Set LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL=1 in .env and restart Web UI.",
                        )
                        continue
                    send_telegram_message(token, cid_i, "Restarting Loki Web UI process now...")
                    _schedule_process_restart()
                    continue

                if text.startswith("/loki_stop"):
                    if not _remote_admin_enabled():
                        send_telegram_message(
                            token,
                            cid_i,
                            "Remote control is disabled. Set LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL=1 in .env and restart Web UI.",
                        )
                        continue
                    send_telegram_message(token, cid_i, "Stopping Loki Web UI process now.")
                    _schedule_process_stop()
                    continue

                if text.startswith("/loki_pause"):
                    if not _remote_admin_enabled():
                        send_telegram_message(
                            token,
                            cid_i,
                            "Remote control is disabled. Set LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL=1 in .env and restart Web UI.",
                        )
                        continue
                    parts = text.split(maxsplit=1)
                    secs = _parse_pause_seconds(parts[1] if len(parts) > 1 else "")
                    if secs is None:
                        send_telegram_message(
                            token,
                            cid_i,
                            "Usage: /loki_pause <duration>\nExamples: /loki_pause 30m, /loki_pause 2h, /loki_pause 45s",
                        )
                        continue
                    send_telegram_message(
                        token,
                        cid_i,
                        f"Pausing Loki for ~{secs}s now. I'll auto-resume afterward.",
                    )
                    ok_sched, msg_sched = _schedule_launchd_resume(secs)
                    if not ok_sched:
                        send_telegram_message(token, cid_i, f"Could not schedule auto-resume: {msg_sched}")
                        continue
                    ok, msg = _launchctl_bootout()
                    if ok:
                        pass
                    else:
                        send_telegram_message(
                            token,
                            cid_i,
                            f"Pause could not unload launchd service: {msg}\n(If not installed via LaunchAgent, use /loki_stop.)",
                        )
                    continue

                if text.startswith("/loki_resume"):
                    if not _remote_admin_enabled():
                        send_telegram_message(
                            token,
                            cid_i,
                            "Remote control is disabled. Set LOKI_TELEGRAM_ALLOW_REMOTE_CONTROL=1 in .env and restart Web UI.",
                        )
                        continue
                    ok1, m1 = _launchctl_bootstrap()
                    ok2, m2 = _launchctl_kickstart()
                    if ok1 and ok2:
                        send_telegram_message(token, cid_i, "Loki launchd service resumed.")
                    else:
                        send_telegram_message(
                            token,
                            cid_i,
                            f"Resume status:\nbootstrap={ok1} ({m1})\nkickstart={ok2} ({m2})",
                        )
                    continue

                print(f"[telegram] inbound chat_id={cid_i} text_len={len(text)}", flush=True)
                ui._enqueue_event("user", f"[Telegram] {text}")
                try:
                    reply = ui.handle_text(text, from_voice=False, blocking=True, skip_tts=True)
                except Exception as e:
                    reply = f"[error] {e}"
                    print(f"[telegram] handle_text error: {e}", flush=True)
                if ld.CROSS_CHAT_APPEND_HOME:
                    ld.append_cross_chat_log("telegram", text, reply)
                if not _reply_errors_enabled() and str(reply).lstrip().startswith("[error]"):
                    send_telegram_message(
                        token,
                        cid_i,
                        "Loki hit an error processing that — check the Mac Terminal / webui log.",
                    )
                else:
                    send_telegram_message(token, cid_i, reply)
                ui._enqueue_event("assistant", f"[Telegram] {reply}")
        except requests.RequestException as e:
            print(f"[telegram] poll network: {e}", flush=True)
            time.sleep(4)
        except Exception as e:
            print(f"[telegram] poll error: {e}", flush=True)
            time.sleep(2)


def _proactive_loop(ui: Any, token: str, allowed: List[int]) -> None:
    lo, hi = _proactive_interval_range()
    cap = _proactive_per_day()
    if cap <= 0:
        print("[telegram] proactive pings off (LOKI_TELEGRAM_PROACTIVE_PER_DAY=0)", flush=True)
        return
    time.sleep(random.uniform(120.0, min(600.0, hi)))
    while True:
        try:
            gap = random.uniform(lo, hi)
            time.sleep(gap)
            if _proactive_in_quiet_hours():
                continue
            if not _quota_try_consume():
                continue
            body = _compose_proactive_message(ui)
            if not body:
                print("[telegram] proactive compose empty; refunding quota slot", flush=True)
                _quota_refund_one()
                continue
            any_ok = False
            for chat_id in allowed:
                if send_telegram_message(token, chat_id, body):
                    any_ok = True
            if not any_ok:
                _quota_refund_one()
                continue
            ld.append_cross_chat_log("telegram_proactive", "[spontaneous outbound]", body)
            ui._enqueue_event("assistant", f"[Telegram → you] {body}")
        except Exception as e:
            print(f"[telegram] proactive error: {e}", flush=True)
            time.sleep(60)


def print_telegram_startup_hint() -> None:
    """
    Always log one line about Telegram env (see /tmp/loki_direct_webui.log).
    Helps when LOKI_TELEGRAM was missing because .env was not loaded from cwd.
    """

    _reload_repo_dotenv()
    repo = Path(__file__).resolve().parent
    env_file = repo / ".env"
    raw_flag = os.getenv("LOKI_TELEGRAM", "")
    tok = bool(_bot_token())
    ids = _allowed_chat_ids()
    print(
        f"[telegram] config: LOKI_TELEGRAM={raw_flag!r} token={'ok' if tok else 'MISSING'} "
        f"allowed_chat_ids={len(ids)} env_file={env_file} exists={env_file.is_file()}",
        flush=True,
    )
    if not _enabled():
        print(
            "[telegram] Bot not running — set LOKI_TELEGRAM=1 (or true) in the repo .env and restart Web UI.",
            flush=True,
        )
        return
    if not tok:
        print("[telegram] Bot not running — TELEGRAM_BOT_TOKEN is empty after loading .env.", flush=True)
        return
    if not ids:
        print("[telegram] Bot not running — TELEGRAM_ALLOWED_CHAT_IDS is empty.", flush=True)
        return


def telegram_status_dict() -> dict:
    """Safe JSON for /api/telegram/status (no secrets)."""

    _reload_repo_dotenv()
    repo = Path(__file__).resolve().parent
    env_file = repo / ".env"
    raw = os.getenv("LOKI_TELEGRAM", "")
    tok = _bot_token()
    masked = ""
    if tok and ":" in tok:
        masked = "…" + tok[-6:]
    elif tok:
        masked = "…" + tok[-4:]
    return {
        "repo_root": str(repo),
        "env_file": str(env_file),
        "env_file_exists": env_file.is_file(),
        "LOKI_TELEGRAM_raw": raw,
        "telegram_enabled_flag": _enabled(),
        "has_bot_token": bool(tok),
        "token_suffix_masked": masked,
        "allowed_chat_ids_count": len(_allowed_chat_ids()),
        "hint": "If has_bot_token is false, check TELEGRAM_BOT_TOKEN in repo .env. Restart Web UI after edits.",
    }


def maybe_start_telegram(ui: Any) -> None:
    _reload_repo_dotenv()
    if not _enabled():
        return
    token = _bot_token()
    allowed = _allowed_chat_ids()
    if not token or not allowed:
        print("[telegram] LOKI_TELEGRAM=1 but TELEGRAM_BOT_TOKEN or TELEGRAM_ALLOWED_CHAT_IDS is missing", flush=True)
        return
    me = _telegram_get(token, "getMe")
    if me.get("ok"):
        un = (me.get("result") or {}).get("username") or "?"
        print(f"[telegram] getMe ok @{un}", flush=True)
    else:
        print(f"[telegram] getMe failed (check token): {me}", flush=True)
    wh = _telegram_call(token, "deleteWebhook", drop_pending_updates=False)
    if not wh.get("ok"):
        print(f"[telegram] deleteWebhook (ok if no webhook was set): {wh}", flush=True)
    qh = (os.getenv("LOKI_TELEGRAM_PROACTIVE_QUIET_HOURS_LOCAL") or "").strip()
    if qh:
        print(f"[telegram] proactive quiet hours (local): {qh}", flush=True)
    if _setup_help_enabled():
        print("[telegram] LOKI_TELEGRAM_SETUP_HELP=1 — unknown chats get setup hints (disable after config)", flush=True)
    threading.Thread(target=_poll_loop, args=(ui, token, allowed), daemon=True, name="loki-telegram-poll").start()
    threading.Thread(target=_proactive_loop, args=(ui, token, allowed), daemon=True, name="loki-telegram-pro").start()
    print("[telegram] started (inbound chat + proactive pings)", flush=True)
