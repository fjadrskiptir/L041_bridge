#!/usr/bin/env python3
"""
OpenAI-compatible HTTP surface for Brave Leo "custom model" and similar clients.

Brave posts to POST /v1/chat/completions on the same host as Loki Web UI.
Forwards to Grok (xAI) without tools, logs turns to cross_chat_log.jsonl so
home Loki sees browser chat in the system prompt.

Streaming (stream=true) is not implemented; turn streaming off in the client if offered.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import loki_direct as ld


def _assistant_content(msg: Dict[str, Any]) -> str:
    content = msg.get("content") or ""
    if isinstance(content, list):
        parts: List[str] = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text", "") or ""))
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts).strip()
    return str(content)


def verify_bridge_auth(headers: Any) -> Optional[str]:
    """Return error string if unauthorized, else None."""

    key = ld.LOKI_LEO_BRIDGE_API_KEY
    if not key:
        return None
    auth = ""
    try:
        auth = (headers.get("Authorization") or headers.get("authorization") or "").strip()
    except Exception:
        pass
    if not auth.startswith("Bearer "):
        return "Authorization: Bearer <API key> required (set LOKI_LEO_BRIDGE_API_KEY in .env to match Brave)."
    if auth[7:].strip() != key:
        return "Invalid bearer token."
    return None


def normalize_openai_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user").strip().lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        c = m.get("content")
        if isinstance(c, list):
            texts: List[str] = []
            for part in c:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        texts.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    texts.append(part)
            c = "\n".join(texts)
        elif c is None:
            c = ""
        elif not isinstance(c, str):
            c = str(c)
        out.append({"role": role, "content": c})
    return out


def openai_models_payload() -> Dict[str, Any]:
    """Single model id = active Grok model (Brave "Model request name" must match)."""

    mid = ld.XAI_MODEL
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "loki-xai-bridge",
            }
        ],
    }


def openai_chat_completions(body: Dict[str, Any], xai: Any) -> Tuple[Dict[str, Any], int]:
    if body.get("stream"):
        return {
            "error": {
                "message": "Streaming not supported. Disable 'stream' in the client (Brave Leo: use non-streaming if available).",
                "type": "invalid_request_error",
            }
        }, 400

    raw_msgs = body.get("messages")
    if not isinstance(raw_msgs, list) or not raw_msgs:
        return {"error": {"message": "messages[] is required", "type": "invalid_request_error"}}, 400

    norm = normalize_openai_messages(raw_msgs)
    if not norm:
        return {"error": {"message": "No valid messages", "type": "invalid_request_error"}}, 400

    if ld.LOKI_BRAVE_LEO_INJECT_SYNC:
        sync = ld.load_cross_chat_for_system_prompt().strip()
        if sync:
            norm.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "Recent conversation history from the user's home Loki session and prior browser turns "
                        "(same log file). Use for continuity unless the user contradicts it.\n\n" + sync
                    ),
                },
            )

    try:
        resp = xai.chat(norm, tools=None)
    except Exception as e:
        return {"error": {"message": str(e), "type": "api_error"}}, 502

    assistant_msg = ld.extract_assistant_message(resp)
    content = _assistant_content(assistant_msg)

    last_user = ""
    for m in reversed(norm):
        if m.get("role") == "user":
            last_user = str(m.get("content", ""))[:80_000]
            break

    ld.append_cross_chat_log("brave_leo", last_user, content)

    model_id = body.get("model") or ld.XAI_MODEL
    if not isinstance(model_id, str):
        model_id = str(ld.XAI_MODEL)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }, 200
