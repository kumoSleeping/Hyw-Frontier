"use strict";

// Server durations: all work before rendering, then image generation/save.
export function formatCompletionTiming({ answer_ms, render_ms, kind }) {
  const seconds = (ms) => typeof ms === "number" && Number.isFinite(ms) && ms >= 0
    ? `${Number((ms / 1000).toFixed(1))}s` : "—";
  if (kind === "text") return `${seconds(answer_ms)} · 文字回复`;
  return `${seconds(answer_ms)} + ${seconds(render_ms)}(绘图)`;
}
