const labels = { off: "关闭 · 不启用思考", low: "Low · 轻度思考", high: "High · 深度思考", max: "Max · 最高强度" };
const storageKey = "frontier.reasoning.mapping";
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

export function createReasoningControl({ modeSelect, selects, hint, provider, model, config, storage }) {
  const modes = ["auto", ...config.tiers];
  const modeKey = "frontier.reasoning.mode";
  let savedMode;
  try { savedMode = storage.getItem(modeKey); } catch { /* Storage may be unavailable. */ }
  modeSelect.value = modes.includes(savedMode) ? savedMode : "auto";
  modeSelect.addEventListener("change", () => {
    if (modes.includes(modeSelect.value)) {
      try { storage.setItem(modeKey, modeSelect.value); } catch { /* Keep the in-page choice. */ }
    }
  });
  let saved;
  try { saved = JSON.parse(storage.getItem(storageKey)); } catch { /* Storage may be unavailable. */ }
  const valid = saved && !Array.isArray(saved) && typeof saved === "object"
    && Object.keys(saved).length === config.tiers.length
    && config.tiers.every(tier => config.levels.includes(saved[tier]));
  const mapping = () => Object.fromEntries(config.tiers.map(tier => [tier, selects[tier].value]));
  const supported = () => provider.value === config.provider && config.models.includes(model.value.trim());
  for (const tier of config.tiers) {
    const select = selects[tier];
    select.replaceChildren();
    for (const level of config.levels) {
      const option = select.ownerDocument.createElement("option");
      option.value = level;
      option.textContent = labels[level] ?? level;
      select.append(option);
    }
    select.value = valid ? saved[tier] : config.mapping[tier];
    select.addEventListener("change", () => {
      if (supported() && config.levels.includes(select.value)) {
        try { storage.setItem(storageKey, JSON.stringify(mapping())); } catch { /* Keep the in-page choice. */ }
      }
    });
  }
  return {
    update(busy = false) {
      modeSelect.disabled = busy || !supported();
      for (const select of Object.values(selects)) select.disabled = busy || !supported();
      hint.textContent = supported()
        ? "自动思考默认中档，模型按难度切换后续轮次；固定档位则不自动切换。无论哪种模式都传入三个映射，可全部相同；本浏览器记住选择。"
        : "此控件仅支持已验证的 DeepSeek 模型；当前模型保持原有默认行为。";
    },
    requestSettings() {
      return supported() ? { reasoning: mapping(), reasoning_mode: modeSelect.value } : {};
    },
  };
}
