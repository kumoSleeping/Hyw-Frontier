"""Render shared Markdown and tool descriptions for the selected answer mode."""
from __future__ import annotations

import re

_COMMENT = re.compile(r"<!--(.*?)-->", re.DOTALL)
_START = "turbo:omit:start"
_END = "turbo:omit:end"


def render_mode_text(text: str, *, turbo: bool = False) -> str:
    """Drop all HTML comments; Turbo also drops paired turbo:omit sections.

    Markers may surround whole sections or inline fragments. Reject unbalanced
    or nested markers rather than silently sending the wrong instructions.
    """
    if type(turbo) is not bool:
        raise TypeError("turbo 必须是布尔值。")
    parts = []
    offset = 0
    omitted = False
    for match in _COMMENT.finditer(text):
        fragment = text[offset:match.start()]
        if "<!--" in fragment or "<!--" in match.group(1):
            raise ValueError("Markdown 注释未正确闭合。")
        if not (turbo and omitted):
            parts.append(fragment)
        marker = match.group(1).strip()
        if marker == _START:
            if omitted:
                raise ValueError("Turbo 提示词标记不能嵌套。")
            omitted = True
        elif marker == _END:
            if not omitted:
                raise ValueError("Turbo 提示词结束标记缺少开始标记。")
            omitted = False
        offset = match.end()
    if omitted:
        raise ValueError("Turbo 提示词开始标记缺少结束标记。")
    if "<!--" in text[offset:]:
        raise ValueError("Markdown 注释未正确闭合。")
    parts.append(text[offset:])
    return "".join(parts)
