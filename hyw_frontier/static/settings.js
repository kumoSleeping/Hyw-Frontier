export function createReasoningControl({ select, hint, config, storage, getModel }) {
  let currentKey, profile;
  const labels = { off: "关闭", low: "低", medium: "中", high: "高", max: "最高" };
  const update = (busy = false) => {
    const model = getModel();
    const key = model ? `frontier.reasoning.${model.provider}.${model.model}` : null;
    if (key !== currentKey) {
      currentKey = key;
      profile = config.profiles.find(item => item.providers.includes(model?.provider) && item.models.includes(model?.model));
      select.replaceChildren();
      const option = select.ownerDocument.createElement("option");
      option.value = "default";
      option.textContent = profile ? `默认 · ${labels[profile.mapping[config.default]]}` : "模型默认";
      select.append(option);
      for (const effort of profile?.levels || []) {
        const option = select.ownerDocument.createElement("option");
        option.value = effort; option.textContent = `${labels[effort]} · ${effort}`;
        select.append(option);
      }
      let saved;
      try { saved = storage.getItem(key); } catch { /* Storage may be unavailable. */ }
      select.value = profile?.levels.includes(saved) ? saved : "default";
    }
    select.disabled = busy || !profile;
    hint.textContent = profile
      ? `可选 ${profile.levels.join(" / ")}；全程使用所选强度，下一次提问生效，按模型记住选择。`
      : "该模型未配置可调思考等级，保留模型自身设置。";
  };
  select.addEventListener("change", () => {
    try { storage.setItem(currentKey, select.value); } catch { /* Keep the in-page choice. */ }
  });
  update();
  return {
    update,
    requestSettings: () => {
      const effort = select.value;
      if (!profile || !profile.levels.includes(effort)) return {};
      return { reasoning: { high: effort, medium: effort, low: effort },
        reasoning_mode: ["high", "max"].includes(effort) ? "high" : ["low", "off"].includes(effort) ? "low" : "medium" };
    },
  };
}

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
