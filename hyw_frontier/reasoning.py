"""Explicit per-request reasoning controls, restricted to verified model support."""
from copy import deepcopy
import json
from pathlib import Path

_CONFIG = json.loads(Path(__file__).with_name("reasoning.json").read_text(encoding="utf-8"))


def reasoning_config() -> dict:
    return deepcopy(_CONFIG)


def resolve_reasoning(provider: str, model: str, value: str | None = None) -> str | None:
    supported = provider == _CONFIG["provider"] and model in _CONFIG["models"]
    if value is None:
        return _CONFIG["default"] if supported else None
    if not supported or not isinstance(value, str) or value not in _CONFIG["levels"]:
        raise ValueError("Invalid reasoning setting for this model")
    return value
