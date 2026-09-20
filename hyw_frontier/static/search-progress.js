// Query counts and wall-clock phase timings; parallel item durations are never summed.
export function createSearchProgress(container) {
  const rounds = new Map();
  const seenQueries = new Set();
  const pages = new Map();
  let renderMs = null;
  const seconds = ms => typeof ms === "number" && Number.isFinite(ms) && ms >= 0
    ? `${(ms / 1000).toFixed(2)}s` : "—";
  let detailed = false;
  let sent = 0;
  let prepared = 0;
  let budget = 600;
  let roundBudget = null;
  let pageBudget = 30;
  function render() {
    const entries = [...rounds.entries()].sort(([a], [b]) => a - b);
    container.hidden = entries.length === 0;
    if (!entries.length) { container.textContent = ""; return; }
    if (!detailed) {
      container.textContent = `共${entries.length}轮 · ${entries.map(([round, row]) => `第${round}轮搜索${row.queries}次`).join(" · ")}`;
      return;
    }
    container.textContent = `共${entries.length}轮 · 主模型已发送 ${sent}张 · 候选已处理 ${prepared}张 · 总尝试上限 ${budget}张 · 单页面上限 ${pageBudget}张 · ${roundBudget === null ? "每轮不限张数" : `每轮新增上限${roundBudget}张`}\n`
      + entries.map(([round, row]) => `第${round}轮 · 搜索${row.queries}次 · 总耗时 ${seconds(row.total_ms)}`
        + ` · 主模型 ${seconds(typeof row.model_ms === 'number' ? Math.max(0, row.model_ms - (row.image_processing_ms || 0)) : null)}`
        + ` · 工具获取 ${seconds(typeof row.tool_ms === 'number' ? Math.max(0, row.tool_ms - (row.media_download_ms || 0)) : null)}`
        + ` · 媒体下载 ${seconds(row.media_download_ms)} · 图片处理 ${seconds(row.image_processing_ms)}`
        + ` · 发送图片 ${row.images_sent ?? 0}张${row.new_images_sent > 0 ? `（新增${row.new_images_sent}张）` : ""}`)
        .join("\n")
      + [...pages.values()].map((page, index) => `\n页面${index + 1} · ${page.url}`
        + `\n读取 ${seconds(page.reader_ms)} · 图片下载 ${seconds(page.download_ms)} · 压缩 ${seconds(page.processing_ms)}`
        + ` · ${page.status || '读取中'}`
        + (Number.isInteger(page.images) ? ` · 附带 ${page.images}张` : '')).join('')
      + (renderMs === null ? '' : `\n最终排版 ${seconds(renderMs)}`);
  }
  return {
    clear() { rounds.clear(); seenQueries.clear(); pages.clear(); renderMs = null; detailed = false; sent = 0; prepared = 0; budget = 600; roundBudget = null; pageBudget = 30; render(); },
    accept(event) {
      if (event.type === 'render_end') { renderMs = event.duration_ms; render(); return; }
      if (!["model_start", "query_start", "query_end", "round_timing", "media_download_end", "media_end", 'page_timing'].includes(event.type)) return;
      if (!Number.isSafeInteger(event.round) || event.round < 1) return;
      if (event.name === 'jina_read_url' || event.type.startsWith('page_')) {
        const page = pages.get(event.id) || {};
        page.url = event.url || event.query || page.url;
        if (event.type === 'query_end') { page.reader_ms = event.duration_ms; page.status = event.ok ? '读取完成' : '读取失败'; }
        if (event.type === 'page_timing') { Object.assign(page, event); page.status = event.status === 'ok' ? '读取完成' : '读取失败'; }
        pages.set(event.id, page);
      }
      if (Number.isSafeInteger(event.image_budget) && event.image_budget >= 0) budget = event.image_budget;
      if (Number.isSafeInteger(event.image_page_budget) && event.image_page_budget >= 0) pageBudget = event.image_page_budget;
      if (event.image_round_budget === null) roundBudget = null;
      else if (Number.isSafeInteger(event.image_round_budget) && event.image_round_budget > 0) roundBudget = event.image_round_budget;
      if (event.type === "query_start") {
        if (event.name === 'jina_read_url') { render(); return; }
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
