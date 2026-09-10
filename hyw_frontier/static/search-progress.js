// Query counts and wall-clock phase timings; parallel item durations are never summed.
export function createSearchProgress(container) {
  const rounds = new Map();
  const seenQueries = new Set();
  const seconds = ms => typeof ms === "number" && Number.isFinite(ms) && ms >= 0
    ? `${(ms / 1000).toFixed(2)}s` : "—";
  let detailed = false;
  let sent = 0;
  let prepared = 0;
  let budget = 20;
  let roundBudget = 5;
  function render() {
    const entries = [...rounds.entries()].sort(([a], [b]) => a - b);
    container.hidden = entries.length === 0;
    if (!entries.length) { container.textContent = ""; return; }
    if (!detailed) {
      container.textContent = `共${entries.length}轮 · ${entries.map(([round, row]) => `第${round}轮搜索${row.queries}次`).join(" · ")}`;
      return;
    }
    const pending = Math.max(0, prepared - sent);
    container.textContent = `共${entries.length}轮 · 图片已发送 ${sent}/${budget} · 每轮新增上限${roundBudget}张${pending ? ` · 待发送 ${pending}张` : ""}\n`
      + entries.map(([round, row]) => `第${round}轮 · 搜索${row.queries}次 · 总耗时 ${seconds(row.total_ms)}`
        + ` · 模型 ${seconds(row.model_ms)} · 工具 ${seconds(row.tool_ms)}`
        + ` · 媒体下载 ${seconds(row.media_download_ms)} · 图片处理 ${seconds(row.image_processing_ms)}`
        + ` · 发送图片 ${row.images_sent ?? 0}张${row.new_images_sent > 0 ? `（新增${row.new_images_sent}张）` : ""}`)
        .join("\n");
  }
  return {
    clear() { rounds.clear(); seenQueries.clear(); detailed = false; sent = 0; prepared = 0; budget = 20; roundBudget = 5; render(); },
    accept(event) {
      if (!["model_start", "query_start", "round_timing", "media_download_end", "media_end"].includes(event.type)) return;
      if (!Number.isSafeInteger(event.round) || event.round < 1) return;
      if (Number.isSafeInteger(event.image_budget) && event.image_budget > 0) budget = event.image_budget;
      if (Number.isSafeInteger(event.image_round_budget) && event.image_round_budget > 0) roundBudget = event.image_round_budget;
      if (event.type === "query_start") {
        if (!["web_search", "search_images"].includes(event.name) || typeof event.id !== "string"
            || !Number.isSafeInteger(event.query_index) || event.query_index < 0) return;
        const key = JSON.stringify([event.round, event.id, event.query_index]);
        if (seenQueries.has(key)) return;
        seenQueries.add(key);
      }
      const row = rounds.get(event.round) ?? { queries: 0 };
      rounds.set(event.round, row);
      if (event.type === "query_start") row.queries++;
      if (event.type === "model_start" && Number.isSafeInteger(event.images_sent)) {
        detailed = true;
        row.images_sent = event.images_sent;
        row.new_images_sent = event.new_images_sent;
        sent = Math.max(sent, event.images_sent);
      }
      if (event.type === "round_timing") {
        detailed = true;
        for (const key of ["total_ms", "model_ms", "tool_ms", "media_download_ms", "image_processing_ms"]) {
          if (Number.isFinite(event[key]) && event[key] >= 0) row[key] = event[key];
        }
      }
      if (event.type === "media_download_end") row.media_download_ms = event.duration_ms;
      if (event.type === "media_end") {
        row.media_download_ms = event.download_ms;
        row.image_processing_ms = event.processing_ms;
        prepared = Math.max(prepared, event.prepared || 0);
      }
      render();
    },
  };
}
