const labels = { off: "关闭 · 不启用思考", low: "Low · 轻度思考", high: "High · 深度思考", max: "Max · 最高强度" };
const storageKey = "frontier.reasoning";
const searchModeLabels = {
  turbo: "Turbo · 优先速度",
  fast: "Fast · 快速高质量",
  basic: "Basic · 基础检索",
  advanced: "Advanced · 高级检索",
};

export function createSearchModeControl({ select, config, storage }) {
  const key = "frontier.searchMode";
  select.replaceChildren();
  for (const mode of config.modes) {
    const option = select.ownerDocument.createElement("option");
    option.value = mode; option.textContent = searchModeLabels[mode] ?? mode;
    select.append(option);
  }
  let saved;
  try { saved = storage.getItem(key); } catch { /* Storage may be unavailable. */ }
  select.value = config.modes.includes(saved) ? saved : config.mode;
  select.addEventListener("change", () => {
    if (config.modes.includes(select.value)) {
      try { storage.setItem(key, select.value); } catch { /* Keep the in-page choice. */ }
    }
  });
  return { requestSettings: () => ({ search_mode: select.value }) };
}

export function createSearchProviderControl({ select, modeSelect, hint, config, storage }) {
  const key = "frontier.searchProvider";
  createSearchModeControl({ select: modeSelect, config, storage });
  select.replaceChildren();
  for (const provider of config.providers) {
    const option = select.ownerDocument.createElement("option");
    option.value = provider; option.textContent = { jina: "Jina", parallel: "Parallel", ddgs: "DDGS · 免密钥" }[provider] ?? provider;
    select.append(option);
  }
  let saved, busy = false;
  try { saved = storage.getItem(key); } catch { /* Storage may be unavailable. */ }
  select.value = config.providers.includes(saved) ? saved : config.provider;
  const update = (value = busy) => {
    busy = value;
    select.disabled = busy;
    modeSelect.disabled = busy || select.value !== "parallel";
    hint.textContent = select.value === "parallel"
      ? "网页搜索使用 Parallel，图片搜索使用 Jina SVIP（需 Jina 凭据）；下一次提问生效，本浏览器记住选择。"
      : select.value === "ddgs"
        ? "DDGS 网页及图片搜索均无需 API Key，自动选择搜索引擎；下一次提问生效，本浏览器记住选择。"
        : "Jina 网页及图片搜索使用 SVIP，不使用 Parallel 模式；下一次提问生效，本浏览器记住选择。";
  };
  select.addEventListener("change", () => {
    if (config.providers.includes(select.value)) {
      try { storage.setItem(key, select.value); } catch { /* Keep the in-page choice. */ }
    }
    update();
  });
  update();
  return {
    update,
    requestSettings: () => ({ search_provider: select.value,
      ...(select.value === "parallel" ? { search_mode: modeSelect.value } : {}) }),
  };
}

export function createReasoningControl({ select, hint, provider, model, config, storage }) {
  select.replaceChildren();
  for (const level of config.levels) {
    const option = select.ownerDocument.createElement("option");
    option.value = level;
    option.textContent = labels[level] ?? level;
    select.append(option);
  }
  let saved;
  try { saved = storage.getItem(storageKey); } catch { /* Storage may be unavailable. */ }
  select.value = config.levels.includes(saved) ? saved : config.default;
  const supported = () => provider.value === config.provider && config.models.includes(model.value.trim());
  select.addEventListener("change", () => {
    if (supported() && config.levels.includes(select.value)) {
      try { storage.setItem(storageKey, select.value); } catch { /* Keep the in-page choice. */ }
    }
  });
  return {
    update(busy = false) {
      select.disabled = busy || !supported();
      hint.textContent = supported()
        ? "从下一次提问起生效，覆盖该次全部模型轮次；本浏览器记住选择。"
        : "此控件仅支持已验证的 DeepSeek 4.1 内测模型；当前模型保持原有默认行为。";
    },
    requestSettings() {
      return supported() ? { reasoning: select.value } : {};
    },
  };
}
