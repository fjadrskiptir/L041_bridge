from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Any


def register(tools) -> None:
    # Keep plugins self-contained: re-declare a tiny ToolSpec compatible with the registry.
    tools.register(
        ToolSpec(
            name="echo",
            description="Echo text back.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            fn=lambda text: json.dumps({"echo": str(text)}, ensure_ascii=False),
        )
    )

