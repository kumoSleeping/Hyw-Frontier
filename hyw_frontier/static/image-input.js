// Local test composer: clipboard images only, no picker or drag/drop.
export async function readImage(file, { maxBytes = 5 * 1024 * 1024 } = {}) {
  const url = URL.createObjectURL(file);
  const image = new Image();
  const canvas = document.createElement("canvas");
  try {
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error("浏览器无法解码这张图片，请重新复制图片或截图后粘贴。"));
      image.src = url;
    });
    const width = image.naturalWidth, height = image.naturalHeight;
    if (!width || !height) throw new Error("图片尺寸无效，请重新粘贴。");
    // Bound canvas allocation by scaling, not by rejecting large source images.
    const scale = Math.min(1, 8192 / Math.max(width, height), Math.sqrt(32_000_000 / (width * height)));
    let targetWidth = Math.max(1, Math.round(width * scale));
    let targetHeight = Math.max(1, Math.round(height * scale));
    for (;;) {
      canvas.width = targetWidth; canvas.height = targetHeight;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("浏览器无法转换图片，请重新粘贴。");
      context.drawImage(image, 0, 0, targetWidth, targetHeight);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      if (!blob?.size) throw new Error("图片转换失败，请重新复制图片或截图后粘贴。");
      if (blob.size <= maxBytes) {
        return await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve({ type: "image", mimeType: "image/png", data: reader.result.split(",", 2)[1] });
          reader.onerror = () => reject(new Error("图片读取失败，请重新粘贴。"));
          reader.readAsDataURL(blob);
        });
      }
      if (targetWidth === 1 && targetHeight === 1) throw new Error("图片转换后仍超过大小上限。");
      targetWidth = Math.max(1, Math.floor(targetWidth * 0.75));
      targetHeight = Math.max(1, Math.floor(targetHeight * 0.75));
    }
  } finally {
    image.onload = image.onerror = null;
    image.removeAttribute("src");
    URL.revokeObjectURL(url);
    canvas.width = canvas.height = 0;
  }
}

export function createImageInput({ input, preview, onError, onChange = () => {}, read = readImage,
  createURL = file => URL.createObjectURL(file), revokeURL = url => URL.revokeObjectURL(url) }) {
  let config = null;
  let entries = [];
  let disabled = true;
  let loading = false;
  let generation = 0;
  const document = preview.ownerDocument;

  function render() {
    preview.replaceChildren();
    for (const entry of entries) {
      const item = document.createElement("div");
      const image = document.createElement("img");
      image.src = entry.url; image.alt = "待发送的粘贴图片";
      const remove = document.createElement("button");
      remove.type = "button"; remove.textContent = "移除"; remove.disabled = disabled || loading;
      remove.addEventListener("click", () => {
        if (disabled || loading) return;
        entries = entries.filter(value => value !== entry);
        revokeURL(entry.url); render(); onChange();
      });
      item.append(image, remove); preview.append(item);
    }
    preview.hidden = entries.length === 0;
  }

  async function paste(event) {
    const files = Array.from(event.clipboardData?.items || [])
      .filter(item => item.kind === "file" && item.type.startsWith("image/"))
      .map(item => item.getAsFile()).filter(Boolean);
    if (!files.length) return; // Ordinary text paste stays native.
    event.preventDefault();
    if (disabled || !config) return;
    if (loading) return onError("图片正在转换，请稍后再粘贴。");
    const current = generation;
    let added = [];
    try {
      if (entries.length + files.length > config.max_images) throw new Error(`每次最多粘贴${config.max_images}张图片。`);
      if (files.some(file => !file.size)) throw new Error("图片为空，请重新粘贴。");
      loading = true; render(); onChange();
      let total = entries.reduce((sum, entry) => sum + entry.size, 0);
      for (const file of files) {
        const block = await read(file, { maxBytes: config.max_image_bytes });
        if (current !== generation) return;
        const bytes = Uint8Array.from(atob(block.data), char => char.charCodeAt(0));
        if (!config.mime_types.includes(block.mimeType)) throw new Error("图片转换后的格式不受支持。");
        if (!bytes.length || bytes.length > config.max_image_bytes) throw new Error("图片转换后为空或超过单张5MB上限。");
        total += bytes.length;
        if (total > config.max_total_bytes) throw new Error("转换后的图片合计超过10MB上限，请减少图片数量。");
        const normalized = new Blob([bytes], { type: block.mimeType });
        added.push({ block, size: bytes.length, url: createURL(normalized) });
      }
      entries.push(...added); added = [];
    } catch (error) {
      if (current === generation) onError(error.message);
    } finally {
      for (const entry of added) revokeURL(entry.url);
      if (current === generation) { loading = false; render(); onChange(); }
    }
  }
  input.addEventListener("paste", paste);
  return {
    configure(value) { config = value; },
    setDisabled(value) { disabled = value; render(); },
    get loading() { return loading; },
    get images() { return entries.map(entry => ({ ...entry.block })); },
    clear() {
      generation++; loading = false;
      for (const entry of entries) revokeURL(entry.url);
      entries = []; render(); onChange();
    },
  };
}
