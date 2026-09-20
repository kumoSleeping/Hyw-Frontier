"""Loopback-only testing UI. Secrets and conversation state remain in Python."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
from threading import BoundedSemaphore, Event, Lock
import time
from urllib.parse import urlsplit

from .agent import AgentLimitError, SearchAgent
from .credentials import CredentialStore
from .image_bridge import bridge_status, save_bridge
from .ddgs import SEARCH_CONFIG as DDGS_SEARCH_CONFIG
from .image_input import IMAGE_INPUT_CONFIG, MAX_MESSAGE_BYTES, MAX_MESSAGE_BLOCKS, ImageInputError, validate_images
from .image_crop import CROP_CONFIG
from .reverse_image import REVERSE_IMAGE_CONFIG
from .jina import SEARCH_ENDPOINT as JINA_SEARCH_ENDPOINT, READER_CONFIG, READER_ENGINES, JinaError, load_jina_key
from .prompt_files import PROMPT_DIR
from .media import MEDIA_CONFIG, MAX_IMAGES, MAX_READER_IMAGES
from .model_limits import known_output_limit
from .favicons import FAVICON_CONFIG
from .parallel import load_parallel_key, SEARCH_MODE, SEARCH_MODES
from .reasoning import reasoning_config, resolve_reasoning, resolve_reasoning_mode
from .request_log import RequestLog, utc_now
from .rendering import CardRenderer, ImageStore, RenderError, RENDER_ENGINE
from .render_protocol import answer_text, parse_answer
from .runtime import Bridge, DEFAULT_LANGUAGE, DEFAULT_MODEL, DEFAULT_PROVIDER, FrontierError, PROVIDERS, build_context, load_prompt
from .source_titles import source_titles
from .tools import SEARCH_PROVIDER, SEARCH_PROVIDERS, tool_definitions

STATIC = Path(__file__).with_name("static")
MAX_BODY = 15 * 1024 * 1024  # 10MB images after base64 plus text/JSON overhead.
MAX_HISTORY = 32 * 1024 * 1024


@dataclass
class Session:
    history: list = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)
    cancel: Event = field(default_factory=Event)
    touched: float = field(default_factory=time.monotonic)
    turns: int = 0
    system_prompt: str = field(default_factory=load_prompt)


class App:
    def __init__(self, home: Path | None = None, timeout: float = 300, agent_factory=SearchAgent, renderer=None):
        self.home = Bridge(home).home
        self.timeout = timeout
        self.token = secrets.token_urlsafe(32)  # CSRF capability, not a provider API key.
        self.sessions: dict[str, Session] = {}
        self.lock = Lock()
        self.slots = BoundedSemaphore(2)
        self.agent_factory = agent_factory
        self.renderer = renderer if renderer is not None else CardRenderer()
        self.images = ImageStore(self.home)

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                session.cancel.set()
        self.renderer.close()

    def new_session(self) -> str:
        with self.lock:
            for key, session in list(self.sessions.items()):
                if time.monotonic() - session.touched > 7200 and not session.lock.locked():
                    del self.sessions[key]
            if len(self.sessions) >= 32:
                raise ValueError("测试会话已满，请关闭旧会话或重启服务")
            identifier = secrets.token_urlsafe(24)
            self.sessions[identifier] = Session()
            return identifier

    def session(self, identifier: str) -> Session:
        with self.lock:
            session = self.sessions.get(identifier)
            if not session:
                raise ValueError("会话已失效，请新建对话")
            session.touched = time.monotonic()
            return session

    def config(self) -> dict:
        store = CredentialStore(self.home)
        presets = store.provider_presets()
        configured, model_options = {}, []
        for provider in PROVIDERS:
            try:
                store.connection(provider)
                configured[provider] = True
            except FrontierError:
                configured[provider] = False
            preset = presets[provider]
            model = preset.get("model", DEFAULT_MODEL if provider == DEFAULT_PROVIDER else None)
            if configured[provider] and model:
                model_options.append({"provider": provider, "model": model,
                                      "label": preset.get("label", f"{provider} · {model}")})
        try:
            load_jina_key(self.home)
            jina_configured = True
        except JinaError:
            jina_configured = False
        try:
            load_parallel_key(self.home)
            parallel_configured = True
        except JinaError:
            parallel_configured = False
        return {"provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL, "providers": list(PROVIDERS),
                "provider_presets": presets, "model_options": model_options,
                "language": DEFAULT_LANGUAGE, "media": MEDIA_CONFIG,
                'prompt_files': sorted(path.name for path in PROMPT_DIR.iterdir() if path.suffix in ('.md', '.json')),
                'round_limit_notice': {'rounds_before_limit': 2, 'prompt_file': 'round_limit.md'},
                "message_input": {"max_total_bytes": MAX_MESSAGE_BYTES, "max_blocks": MAX_MESSAGE_BLOCKS},
                "development": {"auto_reload": os.environ.get("HYW_FRONTIER_RELOAD") == "1", "pid": os.getpid()},
                "search": {"provider": SEARCH_PROVIDER, "providers": list(SEARCH_PROVIDERS),
                           "mode": SEARCH_MODE, "modes": list(SEARCH_MODES),
                           "ddgs": DDGS_SEARCH_CONFIG},
                "image_search_providers": {"jina": "jina", "parallel": "jina", "ddgs": "ddgs"},
                "reverse_image_search": {**REVERSE_IMAGE_CONFIG, **bridge_status(self.home)},
                "search_endpoints": {"jina": JINA_SEARCH_ENDPOINT},
                "reader": {**READER_CONFIG, 'engines': list(READER_ENGINES),
                           'max_reader_images': MAX_READER_IMAGES},
                "tools_by_search_provider": {provider: [tool["name"] for tool in tool_definitions(provider)]
                                             for provider in SEARCH_PROVIDERS},
                "search_result_policy": {"scope": "provider_default", "local_truncation": False},
                "transport": {"search": {"client": "urllib3", "connection_pool": True,
                                         "scope": "task", "max_connections_per_host": 6,
                                         "cache_cleared_on_close": True,
                                         "clients_by_provider": {"jina": "urllib3", "parallel": "urllib3", "ddgs": "primp"}},
                              "model": {"library": "pydantic-ai-slim", "runtime": "python", "input": "model_instance",
                                        "scope": "task", "automatic_retries": False,
                                        "output_limit_policy": "configured_deployment_budget_or_model_maximum",
                                        "max_output_tokens": known_output_limit(DEFAULT_PROVIDER, DEFAULT_MODEL)},
                              "deepseek": {"mode": "python_async_client", "api": "responses",
                                           "credential_scope": "task_snapshot"}},
                "completion_timing": "answer_plus_render", "image_input": {**IMAGE_INPUT_CONFIG, "crop": CROP_CONFIG},
                "reply": {"kinds": ["text", "image"], "text_field": "display_text",
                          "raw_field": "text", "text_rendering": False},
                "logging": {"enabled": True, "format": "jsonl"}, "reasoning": reasoning_config(),
                "rendering": {"enabled": True, "engine": RENDER_ENGINE, "format": "png", "width": 840,
                              "overflow_wrap": "break-word",
                              "profile": "markdown-reading-v2",
                              "math": "ziamath-python",
                              "math_pipeline": "latex2mathml/ziamath/aggdraw",
                              "math_currency_labels": "literal_parenthesized_dollar",
                              "math_external_runtime": False,
                              "math_font_fallback": "bundled_noto_babelstone",
                              "table_layout": "adaptive_columns_or_records",
                              "preserve_case": True, "rich_title": True,
                              "preamble_policy": "trim_before_first_heading",
                              "missing_glyph": "unicode_label",
                              "image_viewer": "zoom_and_original_size",
                              "inline_image_layout": "full_width_preserve_aspect_ratio",
                              "auto_citations": False, "sources_card": True,
                              "link_style": "ink_dashed_underline",
                              "link_underline": {"color": "#45454b", "thickness": 3, "dash": 4, "gap": 3},
                              "source_style": "readable_v3_icon_only",
                              "source_numbering": False, "source_link_icon": False,
                              "source_text_inset": 38,
                              "source_url_wrap": "multiline_url",
                              "source_url_display": "percent_decoded",
                              "source_title": "tool_page_title_or_hostname",
                              "source_title_normalization": "html_to_plain_text",
                              "source_title_wrap": "single_line_ellipsis",
                              "source_icons": "prefetched_favicon_with_offline_fallback",
                              "favicons": FAVICON_CONFIG,
                              "citation_alignment": "ink_bounds_pixel",
                              "nested_card_spacing": "block_margin",
                              "nested_card_alignment": "content_bounds",
                              "worker": self.renderer.status() if isinstance(self.renderer, CardRenderer) else {"mode": "custom"}},
                "tools": [tool["name"] for tool in tool_definitions()], "system_prompt": load_prompt(),
                "credentials": {**configured, "jina": jina_configured, "parallel": parallel_configured}}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app: App):
        self.app = app
        super().__init__(address, Handler)

    def server_close(self):
        self.app.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *_):
        pass  # Do not log user queries, sessions, headers or credentials.

    def _allowed(self, write=False) -> bool:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            return False  # Includes DNS-rebinding protection.
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}" for host in hosts}:
            return False
        return not write or secrets.compare_digest(self.headers.get("X-Frontier-Token", "").encode(), self.server.app.token.encode())

    def _headers(self, status: int, content_type: str, length: int | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _json(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self._headers(status, "application/json; charset=utf-8", len(raw))
        self.wfile.write(raw)

    def do_GET(self):
        if not self._allowed():
            return self._json(403, {"error": "仅允许本机同源访问"})
        path = urlsplit(self.path).path
        if path == "/api/config":
            return self._json(200, self.server.app.config())
        if path == "/api/image-bridge":
            if not self._allowed(write=True):
                return self._json(403, {"error": "图床设置需要本机页面鉴权"})
            return self._json(200, bridge_status(self.server.app.home, include_url=True))
        if path.startswith("/api/images/"):
            if not self._allowed(write=True):
                return self._json(403, {"error": "图片需要本机页面鉴权"})
            try:
                content = self.server.app.images.read(path.removeprefix("/api/images/"))
            except OSError:
                return self._json(404, {"error": "图片不存在"})
            self._headers(200, "image/png", len(content))
            self.wfile.write(content)
            return
        files = {"/": (STATIC / "index.html", "text/html"), "/app.js": (STATIC / "app.js", "text/javascript"),
                 "/debug.js": (STATIC / "debug.js", "text/javascript"), "/style.css": (STATIC / "style.css", "text/css"),
                 "/settings.js": (STATIC / "settings.js", "text/javascript"),
                 "/timing.js": (STATIC / "timing.js", "text/javascript"),
                 "/image-result.js": (STATIC / "image-result.js", "text/javascript"),
                 "/image-input.js": (STATIC / "image-input.js", "text/javascript"),
                 "/process-intro.js": (STATIC / "process-intro.js", "text/javascript"),
                 "/search-progress.js": (STATIC / "search-progress.js", "text/javascript")}
        if path not in files:
            return self._json(404, {"error": "Not found"})
        file, mime = files[path]
        raw = file.read_text(encoding="utf-8")
        if path == "/":
            raw = raw.replace("__FRONTIER_TOKEN__", self.server.app.token)
        content = raw.encode()
        self._headers(200, mime + "; charset=utf-8", len(content))
        self.wfile.write(content)

    def _body(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValueError("请求必须为 application/json")
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("不支持分块请求体")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_BODY:
            raise ImageInputError("请求体为空或超过15MB上限。")
        self.connection.settimeout(15)
        data = json.loads(self.rfile.read(length))
        self.connection.settimeout(None)
        if not isinstance(data, dict):
            raise ValueError("请求必须是 JSON 对象")
        return data

    def do_POST(self):
        if not self._allowed(write=True):
            return self._json(403, {"error": "同源校验失败，请刷新本机页面"})
        try:
            data = self._body()
            if self.path == "/api/image-bridge":
                try:
                    return self._json(200, save_bridge(self.server.app.home, data))
                except JinaError as exc:
                    return self._json(400, {"error": str(exc)})
                except OSError:
                    return self._json(500, {"error": "无法保存本机图床配置"})
            if self.path == "/api/session":
                old = data.get("replace")
                if old is not None:
                    session = self.server.app.session(old)
                    if session.lock.locked():
                        return self._json(409, {"error": "请等待当前请求停止后再新建对话"})
                    with self.server.app.lock:
                        self.server.app.sessions.pop(old, None)
                return self._json(200, {"session": self.server.app.new_session()})
            if self.path == "/api/cancel":
                self.server.app.session(data.get("session")).cancel.set()
                return self._json(200, {"ok": True})
            if self.path == "/api/chat":
                return self._chat(data)
            self._json(404, {"error": "Not found"})
        except ImageInputError as exc:
            self._json(400, {"error": str(exc)})
        except (ValueError, TypeError, TimeoutError):
            self._json(400, {"error": "请求无效或会话失效，请检查输入或新建对话"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _chat(self, data):
        text, provider, model = data.get("message"), data.get("provider", DEFAULT_PROVIDER), data.get("model", DEFAULT_MODEL)
        images = validate_images(data.get("images", []))
        rounds = data.get("max_rounds", 30)
        max_tool_images = data.get("max_tool_images", MAX_IMAGES)
        max_reader_images = data.get('max_reader_images', MAX_READER_IMAGES)
        if type(max_reader_images) is not int or max_reader_images < 0:
            raise ValueError('max_reader_images must be a non-negative integer')
        reader_engine = data.get('reader_engine', 'browser')
        if reader_engine not in READER_ENGINES:
            raise ValueError('Invalid reader engine')
        if type(max_tool_images) is not int or max_tool_images < 0:
            raise ValueError('max_tool_images must be a non-negative integer')
        search_mode = data.get("search_mode", SEARCH_MODE)
        search_provider = data.get("search_provider", SEARCH_PROVIDER)
        if (not isinstance(text, str) or (not text.strip() and not images) or len(text) > 6000 or provider not in PROVIDERS
                or not isinstance(model, str) or not 1 <= len(model) <= 150 or type(rounds) is not int or rounds < 1):
            raise ValueError("Invalid chat request")
        if search_mode not in SEARCH_MODES or search_provider not in SEARCH_PROVIDERS:
            raise ValueError("Invalid search settings")
        effective_search_mode = search_mode if search_provider == "parallel" else None
        reasoning = resolve_reasoning(provider, model, data.get("reasoning"))
        reasoning_mode = resolve_reasoning_mode(data.get("reasoning_mode", "auto"))
        if reasoning is None and reasoning_mode != "auto":
            raise ValueError("当前模型不支持固定思考档位")
        session = self.server.app.session(data.get("session"))
        if not session.lock.acquire(blocking=False):
            return self._json(409, {"error": "此会话已有请求正在运行"})
        if not self.server.app.slots.acquire(blocking=False):
            session.lock.release()
            return self._json(429, {"error": "测试服务繁忙，请稍后再试"})
        session.cancel.clear()
        started = time.monotonic()
        sequence = 0
        event_lock = Lock()
        agent = None
        trace = RequestLog(self.server.app.home, {"message": text, "provider": provider, "model": model,
                                                 "reasoning": reasoning, "reasoning_mode": reasoning_mode,
                                                 "search_provider": search_provider,
                                                 "images": [{"mimeType": image["mimeType"], "base64_length": len(image["data"])} for image in images],
                                                 "search_mode": effective_search_mode, "max_rounds": rounds,
                                                 "max_tool_images": max_tool_images,
                                                 'max_reader_images': max_reader_images, 'reader_engine': reader_engine})

        def send(event):
            nonlocal sequence
            # Query workers can emit concurrently; serialize sequence, log and NDJSON writes together.
            with event_lock:
                sequence += 1
                record = {"seq": sequence, "timestamp": utc_now(),
                          "elapsed_ms": round((time.monotonic() - started) * 1000), **event}
                if event["type"] in ("start", "done", "error", "cancelled"):
                    record["logging"] = {"request_id": trace.id, "saved": not trace.failed, "truncated": trace.truncated}
                trace.write(record)
                if "logging" in record:
                    record["logging"].update(saved=not trace.failed, truncated=trace.truncated)
                try:
                    self.wfile.write((json.dumps(record, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    trace.write({"type": "client_disconnected", "elapsed_ms": record["elapsed_ms"]})
                    session.cancel.set()
                    raise

        try:
            self._headers(200, "application/x-ndjson; charset=utf-8")
            if session.turns >= 20:
                raise AgentLimitError("本测试会话已达20轮，请新建对话；不会自动压缩或删除上下文。")
            send({"type": "start", "provider": provider, "model": model, "reasoning": reasoning,
                  "reasoning_mode": reasoning_mode,
                  'max_reader_images': max_reader_images, 'reader_engine': reader_engine,
                  "search_provider": search_provider, "search_mode": effective_search_mode})
            # Lazily construct the configured native Model on the task's own loop.
            # The instance then uses the same settings-preserving adapter as answer(model=...).
            from .credentials import CredentialStore
            from .model_backend import configured_model
            home, timeout = self.server.app.home, self.server.app.timeout
            def model_factory(name):
                connection = CredentialStore(home, timeout, session.cancel).connection(provider)
                initial_level = "medium" if reasoning_mode == "auto" else reasoning_mode
                return configured_model(connection, name, timeout, reasoning[initial_level] if reasoning is not None else None)
            bridge = Bridge(home, timeout, session.cancel, model_factory=model_factory)
            agent = self.server.app.agent_factory(bridge, on_event=send, cancel_event=session.cancel,
                                                  max_reader_images=max_reader_images, reader_engine=reader_engine,
                                                  max_rounds=rounds, max_tool_images=max_tool_images, reasoning=reasoning, reasoning_mode=reasoning_mode,
                                                  search_mode=search_mode,
                                                  search_provider=search_provider)
            prompt = session.system_prompt
            trace.write({"type": "prompt", "system_prompt": prompt})
            context = build_context(text, prompt, session.history, images=images)
            if len(json.dumps(context["messages"]).encode()) > MAX_HISTORY:
                raise AgentLimitError("会话上下文超过32MB测试上限，请新建对话；未自动压缩内容。")
            response = agent.run(provider, model, context)
            if session.cancel.is_set():
                raise AgentLimitError("请求已取消。")
            history = agent.context["messages"]
            if len(json.dumps(history).encode()) > MAX_HISTORY:
                raise AgentLimitError("会话上下文超过32MB测试上限，请新建对话；未自动压缩内容。")
            answer = "".join(block["text"] for block in response["content"] if block["type"] == "text")
            # Preserve raw output for diagnostics; delivery uses the shared parsed body.
            parsed = parse_answer(answer)
            kind = 'text' if parsed['mode'] == 'text' else 'image'
            display_text = answer_text(parsed)
            send({"type": "answer_ready", "text": answer, "kind": kind, "display_text": display_text,
                  "truncated": response.get("stopReason") == "length"})
            render_started = time.monotonic()
            image, render_ms = None, 0
            if kind == 'image':
                send({"type": "render_start", "engine": RENDER_ENGINE})
                try:
                    card = self.server.app.renderer.render(answer, metadata={"date": date.today().isoformat(),
                        "search_provider": search_provider, "search_mode": effective_search_mode,
                        "answer_ms": round((render_started - started) * 1000),
                        "source_titles": source_titles(history),
                        "truncated": response.get("stopReason") == "length"}, cancel=session.cancel,
                        max_tool_images=max_tool_images,
                        **({'image_assets': agent.image_assets} if getattr(agent, 'image_assets', None) else {}),
                        **({'favicon_assets': agent.favicon_assets} if getattr(agent, 'favicon_assets', None) else {}))
                    if session.cancel.is_set():
                        raise RenderError("cancelled")
                    image = self.server.app.images.save(card)
                except RenderError as exc:
                    send({"type": "render_error", "code": exc.code,
                          "duration_ms": round((time.monotonic() - render_started) * 1000)})
                    raise
                render_ms = round((time.monotonic() - render_started) * 1000)
                send({"type": "render_end", "image": image, "format_recovered": card.recovered, "answer_mode": card.mode,
                      "duration_ms": render_ms, "diagnostics": list(card.diagnostics), "stages_ms": card.timings})
            if session.cancel.is_set():
                raise AgentLimitError("请求已取消。")
            # Only complete requests update history; failed/cancelled turns do not pollute it.
            send({"type": "done", "text": answer, "kind": kind, "display_text": display_text, "image": image,
                  "answer_ms": round((render_started - started) * 1000), "render_ms": render_ms,
                  "truncated": response.get("stopReason") == "length"})
            session.history = history
            session.turns += 1
        except (BrokenPipeError, ConnectionResetError):
            session.cancel.set()
        except (FrontierError, AgentLimitError) as exc:
            send({"type": "cancelled" if session.cancel.is_set() else "error", "message": str(exc),
                  **(exc.diagnostics if isinstance(exc, FrontierError) else {})})
        except Exception:
            send({"type": "error", "message": "请求失败，内部错误已隐藏以保护凭据。"})
        finally:
            if isinstance(agent, SearchAgent):
                agent.release()
            trace.close()
            session.touched = time.monotonic()
            self.server.app.slots.release()
            session.lock.release()


def serve(home: Path | None = None, port: int = 8767, timeout: float = 300):
    app = App(home, timeout)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    def stop_service(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_service)
    try:
        print('正在预热隔离渲染 worker（字体、协议解析、公式和 PNG）…', flush=True)
        app.renderer.start()
        # Do not advertise/listen until prewarm succeeds. A failed startup closes children.
        with Server(("127.0.0.1", port), app) as server:
            print(f"Hyw-Frontier 测试页面：http://127.0.0.1:{server.server_port}", flush=True)
            print("渲染已预热；仅本机访问，原始事件自动记入日志。Ctrl+C 停止。", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        signal.signal(signal.SIGTERM, previous_sigterm)
