"""Private, bounded Bot request traces, readable by the existing Hyw logs CLI."""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from threading import Lock, current_thread
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

from hyw_frontier.request_log import RequestLog

from .config import Config

_ACTIVE: set[Path] = set()
_FILES_LOCK = Lock()
_INLINE = re.compile(r"data:image/[^;,\s]+;base64,[A-Za-z0-9+/=]+")
_URL = re.compile(r"https?://[^\s<>\"']+")
_IMAGE = re.compile(r"!\[[^\]\n]*\]\(<?(https?://[^\s)>]+)")
_SECRET_KEYS = {"authorization", "api_key", "apikey", "password", "access_token", "refresh_token",
                "thoughtsignature", "signature", "providermetadata", "providerdetails"}


def _url(value: str) -> str:
    try:
        parts = urlsplit(value)
        # Keep enough information to locate the source, but no signed query/userinfo.
        return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
    except ValueError:
        return "[invalid URL]"


def redact(value, depth=0):
    if depth > 16:
        return "[depth limit]"
    if isinstance(value, dict):
        if value.get("type") == "image":
            return {"type": "image", "mimeType": value.get("mimeType"),
                    "base64_chars_omitted": len(value.get("data", ""))}
        if value.get("type") == "thinking":
            return {"type": "thinking", "chars_omitted": len(value.get("thinking", ""))}
        return {str(k): redact(v, depth + 1) for k, v in list(value.items())[:300]
                if str(k).lower() not in _SECRET_KEYS}
    if isinstance(value, (list, tuple)):
        return [redact(item, depth + 1) for item in value[:200]]
    if isinstance(value, str):
        # Tool results carry JSON inside their text block. Redact nested image payloads too.
        if value.startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except ValueError:
                pass
            else:
                return json.dumps(redact(decoded, depth + 1), ensure_ascii=False)
        text = _INLINE.sub("[inline image omitted]", value)
        text = _URL.sub(lambda m: _url(m[0]), text)
        return text if len(text) <= 64000 else text[:64000] + "[text truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"[{type(value).__name__} omitted]"


def _prune(directory: Path, config: Config):
    if not directory.exists() or directory.is_symlink():
        return
    with _FILES_LOCK:
        try:
            files = sorted((p for p in directory.glob("*.jsonl") if not p.is_symlink()), reverse=True)
        except OSError:
            return
        total = 0
        cutoff = time.time() - config.log_retention_days * 86400
        for index, path in enumerate(files):
            try:
                stat = path.stat()
                total += stat.st_size
                if path not in _ACTIVE and (index >= config.log_max_files or stat.st_mtime < cutoff
                                           or total > config.log_total_bytes):
                    path.unlink()
            except OSError:
                continue


class BotTrace:
    def __init__(self, config: Config, question: str, scope: tuple, message_id: str):
        self.started = time.monotonic()
        self.lock = Lock()
        self.log = None
        self.config = config
        self.counts = Counter()
        self.tools = Counter()
        self.queries = []
        self.media = []
        self.available: set[str] = set()
        self.selected: list[str] = []
        self.details_truncated = False
        self.failure: dict = {}
        if config.log_enabled:
            home = Path(config.log_home).expanduser() if config.log_home else (
                Path(config.home or os.environ.get("HYW_FRONTIER_HOME", "~/.hyw-frontier")).expanduser() / "entari")
            _prune(home / "logs", config)
            self.log = RequestLog(home, redact({
                "message": question, "scope": list(scope), "message_id": message_id,
                "provider": config.provider, "model": config.model, "reasoning": config.reasoning,
                "search_provider": config.search_provider, "max_tool_images": config.max_tool_images,
                "max_reader_images": config.max_reader_images, "reader_engine": config.reader_engine,
                "search_mode": config.search_mode if config.search_provider == "parallel" else None,
                "entry": "entari_plugin_hyw_frontier",
            }), max_bytes=config.log_max_bytes)
            with _FILES_LOCK:
                _ACTIVE.add(self.log.path)
            logger.info("Frontier request {}: model={} reasoning={} search={} log={}",
                        self.id, config.model, config.reasoning, config.search_provider, self.log.path)
            if self.log.failed:
                logger.warning("Frontier request {}: private trace file unavailable", self.id)

    @property
    def id(self):
        return self.log.id if self.log else "logging-disabled"

    def event(self, event: dict):
        try:
            self._event(event)
        except Exception as exc:  # noqa: BLE001 - never fail the send callback or model for a log error
            logger.warning("Frontier trace {} event failed ({})", self.id, type(exc).__name__)

    def _event(self, event: dict):
        kind = event.get("type", "")
        # Full model responses are logged at round boundaries, not once per token.
        if kind.endswith("_delta") or kind in ("toolcall_start", "toolcall_end", "thinking_start", "text_start"):
            return
        with self.lock:
            if kind == "request_failed":
                self.failure = {key: event[key] for key in
                                ("error_type", "code", "http_status", "retryable", "phase", "message") if key in event}
            elif kind == "model_response":
                self.counts["model_rounds"] += 1
                for block in event.get("response", {}).get("content", []):
                    if block.get("type") == "toolCall":
                        self.tools[block.get("name", "unknown")] += 1
            elif kind == "query_end":
                self.queries.append({key: event.get(key) for key in (
                    "round", "name", "provider", "query", "ok", "code", "result_count", "duration_ms", "cached")})
            elif kind == "media_end":
                self.media.extend(event.get("images", []))
            elif kind == "answer_ready":
                self.available = set(event.get("available_image_urls", []))
                self.selected = list(dict.fromkeys(_IMAGE.findall(event.get("text", ""))))
            elif kind in ("intro_callback_start", "intro_delivered", "intro_delivery_failed",
                          "image_delivered", "image_delivery_failed", "text_delivered", "text_fallback_delivered"):
                self.counts[kind] += 1
            if self.log:
                record = {**redact(event), "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                          "thread": current_thread().name}
                size = len(json.dumps(record, ensure_ascii=False).encode())
                terminal = kind in ("bot_summary", "done", "error", "cancelled")
                if not terminal and self.log.size + size > self.config.log_max_bytes - 128 * 1024:
                    if not self.details_truncated:
                        self.log.write({"type": "details_truncated", "message": "详细事件达到预算，仍保留最终诊断摘要。"})
                        self.details_truncated = True
                    return
                if kind == "bot_summary" and size > 100 * 1024:
                    record.pop("search_queries", None)
                    record.pop("image_downloads", None)
                    record.pop("selected_image_urls", None)
                    record["summary_details_truncated"] = True
                self.log.write(record)

    def close(self, status: str):
        try:
            summary = {**dict(self.counts), "tools": dict(self.tools), "search_queries": self.queries,
                       "image_downloads": self.media,
                       "image_status_counts": dict(Counter(row.get("status", "unknown") for row in self.media)),
                       "selected_image_urls": self.selected,
                       "selected_ready_images": sum(url in self.available for url in self.selected)}
            self.event({"type": "bot_summary", **summary})
            self.event({"type": status, **self.failure})
            logger.info("Frontier request {} {}: rounds={} tools={} intro_delivered={} image_statuses={} selected_ready={}",
                        self.id, status, self.counts["model_rounds"], dict(self.tools),
                        self.counts["intro_delivered"], summary["image_status_counts"], summary["selected_ready_images"])
        finally:
            if self.log:
                self.log.close()
                with _FILES_LOCK:
                    _ACTIVE.discard(self.log.path)
                _prune(self.log.path.parent, self.config)
