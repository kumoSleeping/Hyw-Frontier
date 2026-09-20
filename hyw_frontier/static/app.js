"use strict";
import { createReasoningControl, createSearchProviderControl } from "./settings.js";
import { createImageResult } from "./image-result.js";
import { createImageInput } from "./image-input.js";
import { createProcessIntro } from "./process-intro.js";
import { createSearchProgress } from "./search-progress.js";
import { createInspection } from "./debug.js";
import { formatCompletionTiming } from "./timing.js";

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="frontier-token"]').content;
$("toggle-settings").addEventListener("click", () => {
  const expanded = document.querySelector(".sidebar").classList.toggle("settings-open");
  $("toggle-settings").setAttribute("aria-expanded", String(expanded));
});
let modelOptions = [];
const imageResult = createImageResult($("result"));
const processIntro = createProcessIntro($("process-intro"));
const searchProgress = createSearchProgress($("search-progress"));
let session = null;
let busy = false;
let initialized = false;
let searchControl = null;
let reasoningControl = null;
const imageInput = createImageInput({
  input: $("message"), preview: $("image-input"), onError: message => status(message, true),
  onChange: () => { $("send").disabled = busy || !initialized || imageInput.loading; },
});

async function post(path, data) {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Frontier-Token": token }, body: JSON.stringify(data),
  });
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return response;
}

async function loadBridgeSettings() {
  try {
    const response = await fetch("/api/image-bridge", { headers: { "X-Frontier-Token": token } });
    if (!response.ok) throw new Error("无法读取图床设置");
    const config = await response.json();
    $("bridge-url").value = config.url || "";
    $("bridge-status").textContent = config.configured ? "已配置" : "";
  } catch (error) { $("bridge-status").textContent = error.message; }
}
$("save-bridge").addEventListener("click", async () => {
  $("save-bridge").disabled = true;
  try {
    await post("/api/image-bridge", { url: $("bridge-url").value, api_key: $("bridge-key").value });
    $("bridge-status").textContent = "已保存，下一次提问生效";
  } catch (error) { $("bridge-status").textContent = error.message; }
  finally { $("bridge-key").value = ""; $("save-bridge").disabled = false; }
});
loadBridgeSettings();

function status(text, error = false) {
  $("activity").textContent = text;
  $("activity").classList.toggle("error", error);
}

function setBusy(value) {
  busy = value;
  for (const id of ["model", "rounds", "search-provider", "search-mode", "max-reader-images", "reader-engine", "new-chat", "message", "send"]) {
    $(id).disabled = value || !initialized;
  }
  imageInput.setDisabled(value || !initialized);
  $("send").disabled = value || !initialized || imageInput.loading;
  searchControl?.update(value || !initialized);
  reasoningControl?.update(value || !initialized);
  $("stop").hidden = !value;
  $("stop").disabled = false;
  $("stop").textContent = "停止";
  $("connection").textContent = value ? "处理中" : "仅本机 · 已连接";
}

async function newChat() {
  if (busy) return;
  setBusy(true);
  try {
    const response = await post("/api/session", session ? { replace: session } : {});
    session = (await response.json()).session;
    imageResult.clear(); processIntro.clear(); searchProgress.clear(); $("debug").replaceChildren();
    document.querySelector("main").classList.remove("has-result");
    $("message").value = "";
    imageInput.clear();
    status("");
  } finally {
    setBusy(false);
    $("message").focus();
  }
}

async function submit(event) {
  event.preventDefault();
  const text = $("message").value.trim();
  const images = imageInput.images;
  if (busy || !session || imageInput.loading || (!text && !images.length)) return;
  const selectedModel = modelOptions.find(option => option.provider === $("model").value);
  if (!selectedModel) return status("没有可用的已配置模型，请检查后端配置并刷新页面", true);
  const maxRounds = Number($("rounds").value);
  if (!Number.isSafeInteger(maxRounds) || maxRounds < 1) return status("模型轮次安全阈值必须是正整数", true);
  const maxReaderImages = Number($("max-reader-images").value);
  if (!$("max-reader-images").value.trim() || !Number.isSafeInteger(maxReaderImages) || maxReaderImages < 0) return status("单页面图片上限必须是非负整数", true);
  setBusy(true); imageResult.clear(); processIntro.clear(); searchProgress.clear();
  document.querySelector("main").classList.add("has-result");
  const request = {
    session, message: text, images, provider: selectedModel.provider, model: selectedModel.model, max_rounds: maxRounds,
    ...searchControl.requestSettings(), ...reasoningControl.requestSettings(),
    max_reader_images: maxReaderImages, reader_engine: $('reader-engine').value,
  };
  const inspection = createInspection($("debug"), null, () => {
    inspection.body.scrollTop = inspection.body.scrollHeight;
  });
  const { session: _session, images: _images, ...publicSettings } = request;
  inspection.accept({ type: "client_request", timestamp: new Date().toISOString(), ...publicSettings,
    images: images.map(image => ({ mimeType: image.mimeType, base64_length: image.data.length })) });
  status("正在生成回答…");
  let terminal = false;
  let loggingWarning = "";

  async function handle(item) {
    inspection.accept(item);
    if (item.logging) {
      $("activity").title = `请求日志：${item.logging.request_id}`;
      loggingWarning = !item.logging.saved ? " · 日志写入失败" : item.logging.truncated ? " · 日志达到大小上限" : "";
    }
    processIntro.accept(item);
    searchProgress.accept(item);
    if (item.type === "model_start") status("正在检索与生成回答…");
    else if (item.type === "tool_start" && item.name === "web_search") status("正在搜索…");
    else if (item.type === "tool_start" && item.name === "search_images") status("正在搜索图片…");
    else if (item.type === "tool_start" && item.name === "crop_user_image") status("正在裁剪用户图片…");
    else if (item.type === "tool_start" && item.name === "reverse_image_search") status("正在以图搜图…");
    else if (item.type === "tool_start" && item.name === "jina_read_url") status("正在读取网页…");
    else if (item.type === "tool_start" && item.name === "jina_pageshot") status("正在获取整页截图…");
    else if (item.type === "render_start") status("正在生成图片…");
    else if (item.type === "done") {
      terminal = true;
      imageInput.clear();
      $("stop").hidden = true;
      if (item.kind === "text") imageResult.showText(item.display_text);
      else await imageResult.show(item.image, token, `Hyw 回答：${item.display_text || "回答图片"}`);
      document.querySelector("main").classList.add("has-result");
      status((item.truncated ? "回答已完成；模型回答达到长度上限，内容可能不完整" : formatCompletionTiming(item)) + loggingWarning);
    } else if (item.type === "error" || item.type === "cancelled") {
      terminal = true;
      status((item.message || "请求未完成") + loggingWarning, item.type === "error");
    }
    // Every event is kept in the visible trace and JSONL export, including failed requests.
  }

  try {
    const response = await post("/api/chat", request);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1);
        if (line.trim()) await handle(JSON.parse(line));
      }
      if (done) break;
    }
    if (buffer.trim()) await handle(JSON.parse(buffer));
    if (!terminal) throw new Error("连接提前结束，未收到最终回答；请检查本地日志");
  } catch (error) {
    inspection.accept({ type: "client_error", message: error.message, timestamp: new Date().toISOString() });
    status(error.message, true);
    if (!terminal) post("/api/cancel", { session }).catch(() => {});
  } finally {
    setBusy(false);
    $("message").focus({ preventScroll: true });
  }
}

$("chat-form").addEventListener("submit", submit);
$("message").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); if (!busy) $("chat-form").requestSubmit();
  }
});
$("new-chat").addEventListener("click", () => newChat().catch(error => status(error.message, true)));
$("model").addEventListener("change", () => {
  try { localStorage.setItem("frontier.modelProvider", $("model").value); } catch { /* Keep the in-page choice. */ }
  reasoningControl?.update(busy || !initialized);
});
$("stop").addEventListener("click", async () => {
  $("stop").disabled = true; $("stop").textContent = "停止中…";
  status("正在停止…");
  try { await post("/api/cancel", { session }); }
  catch (error) { status(error.message, true); $("stop").disabled = false; }
});
window.addEventListener("pagehide", () => { imageResult.clear(); imageInput.clear(); });

(async () => {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error("无法读取本地配置");
    const config = await response.json();
    imageInput.configure(config.image_input);
    modelOptions = config.model_options || [];
    $("model").replaceChildren();
    for (const preset of modelOptions) {
      const option = document.createElement("option"); option.value = preset.provider;
      option.textContent = preset.label;
      $("model").append(option);
    }
    if (!modelOptions.length) throw new Error("后端尚未配置可用模型");
    let savedModel;
    try { savedModel = localStorage.getItem("frontier.modelProvider"); } catch { /* Storage may be unavailable. */ }
    $("model").value = modelOptions.find(option => option.provider === savedModel)?.provider
      || modelOptions.find(option => option.provider === config.provider)?.provider || modelOptions[0].provider;
    const storage = { getItem: key => localStorage.getItem(key), setItem: (key, value) => localStorage.setItem(key, value) };
    reasoningControl = createReasoningControl({ select: $("reasoning-effort"), hint: $("reasoning-hint"),
      config: config.reasoning, storage, getModel: () => modelOptions.find(option => option.provider === $("model").value) });
    searchControl = createSearchProviderControl({ select: $("search-provider"), modeSelect: $("search-mode"),
      hint: $("search-hint"), config: config.search, storage });
    $("prompt").textContent = config.system_prompt;
    for (const [id, field] of [['reader-engine', 'engine']]) {
      let saved;
      try { saved = localStorage.getItem(`frontier.${id}`); } catch { /* Use server default. */ }
      $(id).value = [...$(id).options].some(option => option.value === saved) ? saved : config.reader[field];
      $(id).addEventListener('change', () => {
        try { localStorage.setItem(`frontier.${id}`, $(id).value); } catch { /* Keep page choice. */ }
      });
    }
    const pageLimit = $('max-reader-images');
    let savedPageLimit = null;
    try { savedPageLimit = localStorage.getItem('frontier.max-reader-images'); } catch { /* Use server default. */ }
    const limit = savedPageLimit === null || !savedPageLimit.trim() ? NaN : Number(savedPageLimit);
    pageLimit.value = Number.isSafeInteger(limit) && limit >= 0 ? limit : config.reader.max_reader_images;
    pageLimit.addEventListener('change', () => {
      const value = Number(pageLimit.value);
      if (pageLimit.value.trim() && Number.isSafeInteger(value) && value >= 0) {
        try { localStorage.setItem('frontier.max-reader-images', String(value)); } catch { /* Keep page choice. */ }
      }
    });
    $("credentials").textContent = `${modelOptions.length} 个已配置模型 / Jina ${config.credentials.jina ? "已配置" : "未配置"} / Parallel ${config.credentials.parallel ? "已配置" : "未配置"} / DDGS 无需密钥`;
    await newChat();
    initialized = true; setBusy(false); $("message").focus();
  } catch (error) {
    $("connection").textContent = "连接失败";
    status(error.message + "，请确认服务仍在运行并刷新页面", true);
  }
})();
