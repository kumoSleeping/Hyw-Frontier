"""A small Python-owned tool loop, not the Pi agent framework."""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
import time
from threading import Event
from typing import Callable

from .jina import JinaClient
from .image_crop import UserImageCrops
from .favicons import FaviconPipeline
from .media import ImagePipeline, MAX_IMAGES, MAX_IMAGES_PER_ROUND
from .reasoning import RECONSIDER_PROMPT, needs_reconsideration, resolve_reasoning, resolve_reasoning_mode
from .prompt_modes import render_mode_text
from .runtime import Bridge, DEFAULT_MODEL
from .parallel import SEARCH_MODE, SEARCH_MODES
from .tools import SEARCH_PROVIDER, SEARCH_PROVIDERS, ToolRuntime


class AgentLimitError(RuntimeError):
    pass


class SearchAgent:
    def __init__(self, bridge, tools: ToolRuntime | None = None, *, max_rounds: int = 30, max_calls: int | None = None,
                 on_event: Callable | None = None, cancel_event: Event | None = None, reasoning: dict[str, str] | None = None,
                 reasoning_mode: str = "auto", search_mode: str = SEARCH_MODE, search_provider: str = SEARCH_PROVIDER,
                 prefetch_icons: bool = True, turbo: bool = False):
        if type(turbo) is not bool:
            raise TypeError("turbo 必须是布尔值。")
        if tools is not None and tools.turbo != turbo:
            raise ValueError("SearchAgent 与 ToolRuntime 的 turbo 模式必须一致。")
        self.turbo = turbo
        if type(max_rounds) is not int or max_rounds < 1:
            raise ValueError("Invalid agent budget")
        if max_calls is None:
            max_calls = max_rounds * 8  # No hidden 24-call cap before the user-selected round budget.
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("Invalid tool budget")
        if search_mode not in SEARCH_MODES:
            raise ValueError("Unsupported search mode")
        if search_provider not in SEARCH_PROVIDERS:
            raise ValueError("Unsupported search provider")
        self.bridge = bridge
        self._owns_tools = tools is None
        self.tools = tools if tools is not None else ToolRuntime(
            JinaClient(bridge.home), search_mode=search_mode, search_provider=search_provider, turbo=turbo)
        self.max_rounds, self.max_calls = max_rounds, max_calls
        self.context: dict | None = None
        self.image_assets: dict[str, bytes] = {}
        self._media: ImagePipeline | None = None
        self._crops: UserImageCrops | None = None
        self.favicon_assets: dict[str, bytes] = {}
        self._favicons: FaviconPipeline | None = None
        self.prefetch_icons = prefetch_icons
        self.streaming = on_event is not None or (prefetch_icons and isinstance(bridge, Bridge))
        self.on_event = on_event or (lambda event: None)
        self.cancel_event = cancel_event
        self.reasoning = reasoning
        self.reasoning_mode = resolve_reasoning_mode(reasoning_mode)

    def release(self):
        """Drop task scratch data after the caller has copied messages/rendered images.

        Detach context rather than mutating it: other entry points may own its messages.
        Tool and bridge lifetimes are managed by their existing context managers.
        """
        self.context = None
        self.tools.set_reasoning = None
        self.tools.crop_user_image = None
        if self._crops is not None:
            self._crops.close()
            self._crops = None
        if self._media is not None:
            self._media.close()
            self._media = None
        self.image_assets.clear()
        self.favicon_assets.clear()
        if self._favicons is not None:
            self._favicons.close()
            self._favicons = None
        self.on_event = lambda event: None

    def _check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise AgentLimitError("请求已取消。")

    def _tool_finished(self, result: dict, round_number: int):
        self._check_cancelled()
        data = json.loads(result["content"][0]["text"])
        sources = []
        if result["toolName"] in ("web_search", "search_images", "jina_read_url"):
            for row in data.get("results", []):
                if not row.get("ok"):
                    continue
                candidates = row.get("results", []) if result["toolName"] in ("web_search", "search_images") else [row]
                sources.extend({"title": item.get("title", ""), "url": item.get("url", ""),
                                "kind": "search" if result["toolName"] in ("web_search", "search_images") else "page"}
                               for item in candidates)
        if self._favicons is not None:
            for source in sources:
                self._favicons.submit(source['url'])
        event_result = result
        if result["toolName"] == "crop_user_image":
            # User pixels belong in model history, not persisted debug logs or UI event JSON.
            event_result = {**result, "content": [
                {**{key: value for key, value in block.items() if key != "data"},
                 "base64_length": len(block.get("data", ""))} if block.get("type") == "image" else block
                for block in result["content"]]}
        self.on_event({"type": "tool_end", "round": round_number,
                       "id": result["toolCallId"], "name": result["toolName"], "result": event_result,
                       "ok": not result["isError"], "partial": data.get("partial", False), "sources": sources[:25]})
        if result["toolName"] == "send_process_intro" and not result["isError"]:
            self.on_event({"type": "process_intro", "round": round_number,
                           "id": result["toolCallId"], "text": data["text"],
                           "supplemental": data.get("supplemental", False)})

    def run(self, provider: str, model: str, context: dict) -> dict:
        if self._favicons is not None:
            self._favicons.close()
            self._favicons = None
        self.favicon_assets.clear()
        try:
            with ExitStack() as resources:
                if self._owns_tools:
                    resources.enter_context(self.tools)
                if isinstance(self.bridge, Bridge):
                    resources.enter_context(self.bridge.task(provider))
                if self.prefetch_icons:
                    self._favicons = FaviconPipeline(self.cancel_event)
                response = self._run(provider, model, context)
                if self._favicons is not None:
                    self.favicon_assets = self._favicons.freeze()
                    self.on_event({'type': 'favicon_prefetch', 'attempted': len(self._favicons.seen),
                                   'ready': len(self.favicon_assets), 'wait_for_pending': False})
                return response
        except BaseException:
            if self._favicons is not None:
                self._favicons.close()
                self._favicons = None
            self.favicon_assets.clear()
            raise
        finally:
            # Decoded user pixels are not needed by the renderer; release before rendering.
            self.tools.set_reasoning = None
            self.tools.crop_user_image = None
            if self._crops is not None:
                self._crops.close()
                self._crops = None
        # On success release() joins remaining cleanup after rendering the snapshot.

    def _model_event(self, event: dict, round_number: int):
        if self._favicons is not None and event.get('type') == 'text_delta':
            self._favicons.feed(event.get('delta', ''), event.get('index', 0))
        self.on_event({**event, 'round': round_number})

    def _run(self, provider: str, model: str, context: dict) -> dict:
        # Borrowed models retain their settings unless the caller explicitly opts into a mapping.
        reasoning = (None if self.reasoning is None and getattr(self.bridge, 'uses_model_settings', False) else
                     resolve_reasoning(provider, model, self.reasoning))
        initial_level = "medium" if self.reasoning_mode == "auto" else self.reasoning_mode
        if reasoning is None and self.reasoning_mode != "auto":
            raise ValueError("固定思考档位需要当前模型支持的三档映射")
        settings = {"reasoning": reasoning[initial_level], "reasoning_level": initial_level} if reasoning is not None else {}

        def set_reasoning(level):
            settings.update(reasoning=reasoning[level], reasoning_level=level)
            return {"ok": True, "level": level, "reasoning": reasoning[level], "effective": "next_round"}

        self.tools.set_reasoning = set_reasoning if reasoning is not None and self.reasoning_mode == "auto" else None
        self.context = deepcopy(context)
        self.context["systemPrompt"] = render_mode_text(self.context["systemPrompt"], turbo=self.turbo)
        if not self.context["systemPrompt"].strip():
            raise AgentLimitError("系统提示词不能为空。")
        base_prompt = self.context["systemPrompt"]
        self.on_event({"type": "effective_prompt", "system_prompt": base_prompt})
        self._check_cancelled()
        crops = None if self.turbo else UserImageCrops(self.context["messages"])
        self._crops = crops
        self.tools.crop_user_image = crops.crop if crops and crops.originals else None
        # Runtime definitions own provider/mode-specific descriptions as well as execution.
        requested = {tool["name"] for tool in self.context["tools"]}
        self.context["tools"] = [tool for tool in self.tools.definitions if tool["name"] in requested
                                 and (tool["name"] != "crop_user_image" or (crops and crops.originals))
                                 and (tool["name"] != "set_reasoning" or self.tools.set_reasoning is not None)]
        media = None if self.turbo else ImagePipeline(self.context["messages"])
        self._media = media
        self.image_assets = media.assets if media else {}
        media_enabled = media is not None and provider == "deepseek" and model in (DEFAULT_MODEL, "deepseek-v4-flash-vision-exp")
        def image_count():
            return sum(block.get("type") == "image" for message in self.context["messages"]
                       if message.get("role") == "toolResult" and message.get("toolName") != "crop_user_image"
                       for block in message.get("content", []))
        previously_sent = image_count()
        if not self.turbo and previously_sent > MAX_IMAGES:
            raise AgentLimitError("历史工具图片超过20张预算，请新建对话；未静默删除历史图片。")
        calls_used = 0
        for _ in range(self.max_rounds):
            self._check_cancelled()
            round_number = _ + 1
            reasoning_settings = {"mode": self.reasoning_mode, "mapping": reasoning,
                                  "level": settings.get("reasoning_level"), "effort": settings.get("reasoning")}
            previous = next((message for message in reversed(self.context["messages"])
                             if message.get("role") == "assistant"), None)
            reconsider = needs_reconsideration(previous, provider, model, reasoning_settings)
            # Only the first request after a change gets the reminder; never rewrite
            # the shared/session prompt or accumulate it in conversation messages.
            prompt = f"{base_prompt}\n\n{RECONSIDER_PROMPT}" if reconsider else base_prompt
            if prompt != self.context["systemPrompt"]:
                self.context["systemPrompt"] = prompt
                self.on_event({"type": "effective_prompt", "round": round_number,
                               "system_prompt": prompt, "reasoning_reconsider": reconsider})
            if self._favicons is not None:
                self._favicons.reset_stream()
            started = time.monotonic()
            times = {"model_ms": 0, "tool_ms": 0, "media_download_ms": 0, "image_processing_ms": 0}
            images_sent = image_count()
            def emit_timing(phase, complete=False):
                self.on_event({"type": "round_timing", "round": round_number, "phase": phase,
                               "complete": complete, "total_ms": round((time.monotonic() - started) * 1000, 2),
                               **{key: round(value, 2) for key, value in times.items()},
                               "images_sent": images_sent, "image_budget": MAX_IMAGES,
                               "image_round_budget": MAX_IMAGES_PER_ROUND})
            try:
                self.on_event({"type": "model_start", "round": round_number, **settings,
                               "reasoning_reconsider": reconsider,
                               "images_sent": images_sent, "new_images_sent": images_sent - previously_sent,
                               "image_budget": MAX_IMAGES, "image_round_budget": MAX_IMAGES_PER_ROUND})
                previously_sent = images_sent
                request = {"command": "stream" if self.streaming else "complete",
                           "provider": provider, "model": model, "context": self.context, **settings}
                model_started = time.monotonic()
                try:
                    response = self.bridge.call(request, on_event=lambda event: self._model_event(event, round_number)) \
                        if self.streaming else self.bridge.call(request)
                finally:
                    times["model_ms"] += (time.monotonic() - model_started) * 1000
                    emit_timing("model")
                self._check_cancelled()
                response = {**response, "reasoning_settings": deepcopy(reasoning_settings)}
                self.context["messages"].append(response)
                self.on_event({"type": "model_response", "round": round_number, "response": response})
                if self._favicons is not None:
                    for block in response['content']:
                        if block.get('type') == 'text':
                            self._favicons.feed(block.get('text', ''), final=True)
                calls = [block for block in response["content"] if block["type"] == "toolCall"]
                if not calls:
                    if response.get("stopReason") == "toolUse":
                        raise AgentLimitError("模型请求工具但没有给出工具调用，任务已停止。")
                    return response
                if round_number == self.max_rounds:
                    raise AgentLimitError("已无后续模型轮次，未执行本轮工具以避免无效检索；请缩小问题范围。")
                if len(calls) > 8 or calls_used + len(calls) > self.max_calls:
                    raise AgentLimitError("达到工具调用预算，已停止；未将尚未核实的信息作为完成结果。")
                if len({call["id"] for call in calls}) != len(calls):
                    raise AgentLimitError("模型返回重复工具调用 ID，任务已停止。")
                tools_started = time.monotonic()
                try:
                    for call in calls:
                        self.on_event({"type": "tool_start", "round": round_number, "id": call["id"], "name": call["name"],
                                       "arguments": call["arguments"]})
                    results = self.tools.execute_many(
                        calls, on_result=lambda result: self._tool_finished(result, round_number),
                        on_query=lambda event: self.on_event({**event, "round": round_number}))
                finally:
                    times["tool_ms"] += (time.monotonic() - tools_started) * 1000
                    emit_timing("tools")
                self._check_cancelled()
                if media_enabled:
                    media_times = media.prepare(results, self.cancel_event or Event(),
                                                lambda event: self.on_event({**event, "round": round_number}))
                    times["media_download_ms"] = media_times["download_ms"]
                    times["image_processing_ms"] = media_times["processing_ms"]
                    times["tool_ms"] += media_times["download_ms"]
                    times["model_ms"] += media_times["processing_ms"]
                self._check_cancelled()
                self.context["messages"].extend(results)
                calls_used += len(calls)
            finally:
                emit_timing("finished", complete=True)
        raise AgentLimitError("达到模型轮次上限，已停止；证据可能不足。可缩小问题范围后重新发起。")
