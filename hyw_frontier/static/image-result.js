// Fetch private PNGs with the page capability; never send it to a model-controlled URL.
export function createImageResult(container, {
  fetchImage = (...args) => fetch(...args), urls = URL, decodeImage = (image) => image.decode(),
} = {}) {
  let currentUrl = null;
  let closeReader = null;
  function openReader(source, trigger) {
    closeReader?.();
    const doc = container.ownerDocument;
    const dialog = doc.createElement("dialog");
    dialog.className = "image-reader";
    dialog.setAttribute("aria-label", "回答图片阅读器");
    const toolbar = doc.createElement("div"); toolbar.className = "image-reader-toolbar";
    const pane = doc.createElement("div"); pane.className = "image-reader-pane";
    pane.tabIndex = 0; pane.setAttribute("aria-label", "图片，可横向和纵向滚动");
    const picture = doc.createElement("img");
    picture.src = source.src; picture.alt = source.alt; picture.className = "image-reader-picture";
    const scaleLabel = doc.createElement("output"); scaleLabel.setAttribute("aria-live", "polite");
    let scale = 1;
    const resize = value => {
      scale = Math.max(.2, Math.min(4, value));
      picture.style.width = `${source.naturalWidth * scale}px`;
      scaleLabel.textContent = `${Math.round(scale * 100)}%`;
    };
    const addButton = (label, action) => {
      const button = doc.createElement("button"); button.type = "button";
      button.textContent = label; button.addEventListener("click", action);
      toolbar.append(button); return button;
    };
    addButton("缩小", () => resize(scale / 1.25));
    addButton("放大", () => resize(scale * 1.25));
    addButton("原尺寸", () => resize(1));
    addButton("适应宽度", () => resize((pane.clientWidth - 32) / source.naturalWidth));
    toolbar.append(scaleLabel);
    const close = addButton("关闭", () => dialog.close());
    close.autofocus = true;
    pane.append(picture); dialog.append(toolbar, pane); doc.body.append(dialog);
    const dismiss = () => {
      dialog.remove();
      if (closeReader === dismiss) closeReader = null;
      if (trigger.isConnected) trigger.focus({ preventScroll: true });
    };
    closeReader = dismiss;
    dialog.addEventListener("close", dismiss, { once: true });
    resize(1); dialog.showModal();
  }
  function clear() {
    closeReader?.();
    container.replaceChildren(); container.hidden = true;
    if (currentUrl) urls.revokeObjectURL(currentUrl);
    currentUrl = null;
  }
  return {
    clear,
    showText(text) {
      if (typeof text !== "string" || !text.trim()) throw new Error("没有收到有效的回答文字");
      clear();
      const body = container.ownerDocument.createElement("div");
      body.className = "answer-text";
      body.textContent = text;
      container.append(body); container.hidden = false;
    },
    async show(image, token, alt = "Hyw 回答图片") {
      if (!image || !/^[a-f0-9]{32}$/.test(image.id) || image.url !== `/api/images/${image.id}`) {
        throw new Error("没有收到有效的回答图片");
      }
      const response = await fetchImage(image.url, {
        headers: { "X-Frontier-Token": token }, redirect: "error", signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw new Error("图片加载失败，生成记录已保存在本地日志");
      const blob = await response.blob();
      if (blob.type !== "image/png" || blob.size > 12 * 1024 * 1024) throw new Error("图片格式或大小无效");
      const src = urls.createObjectURL(blob);
      try {
        const doc = container.ownerDocument;
        const figure = doc.createElement("figure");
        const img = doc.createElement("img"); img.alt = alt; img.src = src; img.className = "answer-image";
        await decodeImage(img);
        const open = doc.createElement("button"); open.type = "button";
        open.className = "answer-image-open";
        open.setAttribute("aria-label", "放大查看回答图片");
        open.title = "点击放大，可查看原尺寸、缩放和滚动";
        open.addEventListener("click", () => openReader(img, open));
        open.append(img);
        const caption = doc.createElement("figcaption");
        const view = doc.createElement("button"); view.type = "button";
        view.textContent = "放大阅读"; view.addEventListener("click", () => openReader(img, view));
        const download = doc.createElement("a"); download.href = src;
        download.download = `hyw-${image.id}.png`; download.textContent = "保存图片";
        caption.append(view, download); figure.append(open, caption);
        clear(); currentUrl = src;
        container.append(figure); container.hidden = false;
      } catch (error) {
        urls.revokeObjectURL(src);
        throw error;
      }
    },
  };
}
