// Developer trace: never interpret model/tool text as HTML, never truncate it.
export function createInspection(article, anchor, scroll, storage = {
  getItem: (key) => localStorage.getItem(key), setItem: (key, value) => localStorage.setItem(key, value),
}) {
  const panel = document.createElement("details"); panel.className = "inspection-panel";
  const title = document.createElement("summary"); title.textContent = "实时审视 · 搜索结果 / 工具 / 原始输出";
  const body = document.createElement("div"); body.className = "inspection-body";
  const key = "frontier.debugOpen";
  try { panel.open = storage.getItem(key) !== "false"; } catch { panel.open = true; }
  panel.addEventListener("toggle", () => {
    try { storage.setItem(key, String(panel.open)); } catch { /* Keep the in-page choice. */ }
  });
  panel.append(title, body); article.insertBefore(panel, anchor);
  // Recheck at callback time: a queued frame must not scroll after the user closes the panel.
  const trace = createTrace(body, null, () => { if (panel.open) scroll(); });
  return {
    panel, body,
    accept(item) {
      trace.accept(item);
      const state = item.type === "done" ? "完成" : ["error", "cancelled", "client_error"].includes(item.type) ? "未完成" : "记录中";
      title.textContent = `实时审视 · ${state} · ${((item.elapsed_ms || 0) / 1000).toFixed(1)}s`;
      // Never assign panel.open here; incoming output cannot override the user's fold state.
    },
    finishScroll: trace.finishScroll,
  };
}

export function createTrace(article, anchor, scroll) {
  const root = document.createElement("section");
  root.className = "developer-trace";
  const toolbar = document.createElement("div");
  toolbar.className = "trace-toolbar";
  const caption = document.createElement("strong");
  caption.textContent = "开发者输出 · 等待模型";
  const followLabel = document.createElement("label");
  const follow = document.createElement("input");
  follow.type = "checkbox"; follow.checked = true;
  followLabel.append(follow, document.createTextNode("跟随输出"));
  const download = document.createElement("button");
  download.type = "button"; download.textContent = "导出 JSONL";
  const logStatus = document.createElement("span"); logStatus.className = "hint";
  toolbar.append(caption, logStatus, followLabel, download);
  root.append(toolbar);
  const raw = document.createElement("details"); raw.className = "trace-raw";
  const rawTitle = document.createElement("summary"); rawTitle.textContent = "全部原始事件 · JSONL（不截断）";
  const rawPre = document.createElement("pre");
  const rawText = document.createTextNode(""); rawPre.append(rawText);
  raw.append(rawTitle, rawPre); root.append(raw);
  article.insertBefore(root, anchor);
  const events = [];
  const rounds = new Map();
  let scheduled = false;

  function scheduleScroll() {
    if (scheduled || !follow.checked) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; if (follow.checked) scroll(); });
  }

  function round(number = 1) {
    if (rounds.has(number)) return rounds.get(number);
    const node = document.createElement("details");
    node.className = "trace-round"; node.open = true; node.dataset.round = String(number);
    const title = document.createElement("summary");
    title.textContent = `第 ${number} 轮 · 模型输出`;
    node.append(title); root.insertBefore(node, raw);
    const value = { node, title, reasoning: "模型默认", blocks: new Map(), tools: new Map() };
    rounds.set(number, value);
    return value;
  }

  function box(parent, label, kind = "") {
    const node = document.createElement("div"); node.className = `trace-block ${kind}`;
    const heading = document.createElement("div"); heading.className = "trace-label"; heading.textContent = label;
    const pre = document.createElement("pre");
    const text = document.createTextNode(""); pre.append(text);
    node.append(heading, pre);
    if (parent === root) root.insertBefore(node, raw);
    else parent.append(node);
    return { node, heading, pre, text };
  }

  function block(item) {
    const current = round(item.round);
    if (current.blocks.has(item.index)) return current.blocks.get(item.index);
    const kind = item.type.split("_")[0];
    const names = { text: "正文原文", thinking: "接口公开返回的推理", toolcall: "工具参数原始增量" };
    const value = box(current.node, `#${item.index} · ${names[kind] || kind}`, kind);
    value.node.dataset.index = item.index;
    if (item.redacted) value.heading.append(document.createTextNode(" · 服务商未公开内容"));
    current.blocks.set(item.index, value);
    return value;
  }

  function tool(item) {
    const current = round(item.round);
    if (current.tools.has(item.id)) return current.tools.get(item.id);
    const node = document.createElement("details"); node.className = "trace-tool"; node.open = true;
    const title = document.createElement("summary"); title.textContent = `${item.name} · ${item.id}`;
    node.append(title); current.node.append(node);
    const args = box(node, "实际调用参数", "tool-arguments");
    const result = box(node, "工具完整返回值（含搜索证据片段）", "tool-result");
    result.pre.textContent = "等待工具返回…";
    const value = { node, title, args, result, queries: new Map() };
    current.tools.set(item.id, value);
    return value;
  }

  download.addEventListener("click", () => {
    const blob = new Blob([events.map((item) => JSON.stringify(item)).join("\n") + "\n"], { type: "application/x-ndjson" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url;
    link.download = `frontier-trace-${new Date().toISOString().replaceAll(":", "-")}.jsonl`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  function accept(item) {
    events.push(item);
    rawText.appendData(JSON.stringify(item) + "\n");    if (item.logging) {
      root.dataset.requestId = item.logging.request_id;
      logStatus.textContent = item.logging.saved ? (item.logging.truncated ? "本地日志已达大小上限" : "已自动记录日志") : "本地日志写入失败";
      logStatus.title = `请求 ID：${item.logging.request_id}`;
    }
    root.dataset.eventCount = events.length;
    caption.textContent = `开发者输出 · ${events.length} 个事件 · ${((item.elapsed_ms || 0) / 1000).toFixed(1)}s`;
    if (item.type === "client_request") {
      box(root, "本次问题与请求配置", "request-settings").pre.textContent = JSON.stringify(item, null, 2);
    } else if (item.type === 'round_limit_warning') {
      box(round(item.round).node, `即将达到 ${item.max_rounds} 轮上限 · 已注入模型提示词`).pre.textContent = item.text;
    } else if (item.type === "model_start") {
      const current = round(item.round);
      const tier = { high: "高", medium: "中", low: "低" }[item.reasoning_level];
      current.reasoning = tier ? `${tier} → ${item.reasoning}` : (item.reasoning ?? "模型默认");
      current.node.dataset.reasoning = current.reasoning;
      current.title.textContent = `第 ${item.round} 轮 · 思考 ${current.reasoning} · 模型输出`;
    } else if (["text_start", "thinking_start", "toolcall_start"].includes(item.type)) block(item);
    else if (["text_delta", "thinking_delta", "toolcall_delta"].includes(item.type)) {
      block(item).text.appendData(item.delta || "");
    } else if (item.type === "thinking_replace") {
      block(item).text.data = item.content || "";
    } else if (item.type === "text_end" || item.type === "thinking_end") {
      const value = block(item);
      if (!value.text.data) value.text.appendData(item.content || "");
      else if (typeof item.content === "string" && value.text.data !== item.content) {
        // Preserve the literal deltas even if the provider corrects the block at end.
        box(value.node, "结束事件的权威内容（与增量不同）", "authoritative").pre.textContent = item.content;
      }
    } else if (item.type === "toolcall_end") {
      const value = block(item);
      value.heading.textContent = `#${item.index} · ${item.toolCall.name} · ${item.toolCall.id} · 参数增量原文`;
      box(value.node, "模型最终生成的参数 JSON", "parsed-arguments").pre.textContent = JSON.stringify(item.toolCall.arguments, null, 2);
    } else if (item.type === "tool_start") {
      tool(item).args.pre.textContent = JSON.stringify(item.arguments, null, 2);
    } else if (item.type === "query_start" || item.type === "query_end") {
      const current = tool(item);
      let row = current.queries.get(item.query_index);
      if (!row) {
        row = box(current.node, `查询 ${item.query_index + 1} · ${item.provider || ""} ${item.search_mode || ""}`, "query-timing");
        current.queries.set(item.query_index, row);
      }
      row.pre.textContent = item.type === "query_start" ? `${item.query}\n正在执行…`
        : `${item.query}\n${item.ok ? "完成" : "失败"} · ${item.duration_ms} ms${item.cached ? " · 本地缓存" : ""}`;
    } else if (item.type === "tool_end") {
      const value = tool(item);
      value.title.textContent = `${item.name} · ${item.id} · ${item.partial ? "部分完成" : item.ok ? "完成" : "失败"}`;
      value.result.pre.textContent = JSON.stringify(item.result, null, 2);
      for (const content of item.result?.content ?? []) {
        if (content.type !== "text") continue;
        const readable = box(value.node, "工具返回正文（完整文本）", "tool-content");
        try { readable.pre.textContent = JSON.stringify(JSON.parse(content.text), null, 2); }
        catch { readable.pre.textContent = content.text; }
      }
      value.node.classList.toggle("trace-failed", !item.ok);
    } else if (item.type === "model_stream_end") {
      const current = round(item.round);
      current.title.textContent = `第 ${item.round} 轮 · 思考 ${current.reasoning} · ${item.reason}`;
      const metadata = document.createElement("details"); metadata.className = "trace-usage";
      const title = document.createElement("summary"); title.textContent = "本轮用量";
      const pre = document.createElement("pre"); pre.textContent = JSON.stringify(item.usage, null, 2);
      metadata.append(title, pre); current.node.append(metadata);
    } else if (item.type === "model_stream_error") {
      box(round(item.round).node, "流错误 · 已收到的字词保留", "trace-failed").pre.textContent = JSON.stringify(item, null, 2);
    } else if (item.type === "answer_ready") {
      box(root, "最终回答原文（展示处理前）", "final-text").pre.textContent = item.text;
    } else if (["done", "error", "cancelled", "client_error"].includes(item.type)) {
      caption.textContent += item.type === "done" ? " · 完成" : " · 未完成，已保留输出";
      root.dataset.terminal = item.type;
      box(root, item.type === "done" ? "完成信息" : "错误 / 中止信息", item.type === "done" ? "" : "trace-failed")
        .pre.textContent = JSON.stringify(item, null, 2);
    }
    scheduleScroll();
  }

  return { accept, finishScroll: scheduleScroll };
}
