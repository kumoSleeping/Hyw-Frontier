"""Entari configuration; credentials stay in Hyw's home or environment."""
import math
from dataclasses import dataclass
from pathlib import Path

from arclet.entari import BasicConfModel

from hyw_frontier.parallel import SEARCH_MODES
from hyw_frontier.runtime import DEFAULT_MODEL
from hyw_frontier.reasoning import resolve_reasoning, resolve_reasoning_mode
from hyw_frontier.tools import SEARCH_PROVIDERS


@dataclass
class Config(BasicConfModel):
    command: str = "/q"
    stop_command: str = "/qstop"
    help_command: str = "/qhelp"
    link_command: str = "/link"
    provider: str = "deepseek"
    model: str = DEFAULT_MODEL
    api: str | None = None
    base_url: str | None = None
    api_key_env: str = ""
    home: str = ""
    language: str = "中文"
    reasoning: dict[str, str] | None = None
    reasoning_mode: str = "auto"
    search_provider: str = "jina"
    search_mode: str = "turbo"
    max_rounds: int = 30
    request_timeout: float = 90
    timeout: float = 300
    send_timeout: float = 30
    max_concurrent: int = 2
    source_ttl: float = 3600
    max_source_records: int = 1280
    max_source_bytes: int = 64 * 1024 * 1024
    max_question_chars: int = 12000
    quote: bool = False
    log_enabled: bool = True
    log_home: str = ""
    log_retention_days: int = 7
    log_max_files: int = 100
    log_max_bytes: int = 2 * 1024 * 1024
    log_total_bytes: int = 64 * 1024 * 1024
    jpeg_quality: int = 85
    allow_users: list[str] | None = None
    allow_channels: list[str] | None = None

    def validate(self):
        commands = [self.command, self.stop_command, self.help_command, self.link_command]
        if len(set(commands)) != 4 or any(not c.startswith("/") or any(x.isspace() for x in c) for c in commands):
            raise ValueError("Frontier commands must be distinct slash commands without whitespace")
        if {self.command, self.stop_command, self.help_command} & {'/link', '/qlink'}:
            raise ValueError('/link and /qlink are reserved source-link aliases')
        for name in ("request_timeout", "timeout", "send_timeout", "source_ttl"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("max_rounds", "max_concurrent", "max_source_records", "max_source_bytes",
                     "max_question_chars", "log_retention_days",
                     "log_max_files", "log_max_bytes", "log_total_bytes"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.search_provider not in SEARCH_PROVIDERS or self.search_mode not in SEARCH_MODES:
            raise ValueError("Invalid search provider/mode")
        if self.log_max_bytes < 256 * 1024 or self.log_total_bytes < self.log_max_bytes:
            raise ValueError("Log limits must reserve at least 256KiB per request and enough total space")
        if not 50 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be between 50 and 95")
        if not self.model.strip():
            raise ValueError("An explicit model ID is required")
        mapping = resolve_reasoning(self.provider, self.model, self.reasoning)
        resolve_reasoning_mode(self.reasoning_mode)
        if mapping is None and self.reasoning_mode != "auto":
            raise ValueError("Fixed reasoning tiers require a supported model")

    def answer_options(self):
        import os

        options = {name: getattr(self, name) for name in (
            "provider", "model", "api", "base_url", "language", "reasoning", "reasoning_mode",
            "search_provider", "search_mode", "max_rounds",
        )}
        options["timeout"] = self.request_timeout
        if self.home:
            options["home"] = Path(self.home).expanduser()
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise ValueError("Configured model credential environment variable is missing")
            options["api_key"] = key
        return options
