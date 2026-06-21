// js/gallery.js
//
// Модуль отвечает за:
// - рендеринг галереи (любой список карточек),
// - создание карточек,
// - обработку кликов,
// - вызов колбэка onSelect(id).
//
// НЕ содержит three.js, viewer, UI вкладок и т.п.

import { MODELS } from "./models.js";
import { cachedFetch } from "./cache/cachedFetch.js";

/**
 * Универсальный рендер галереи для любого списка карточек.
 *
 * @param {HTMLElement} containerEl — DOM-элемент галереи (#gallery)
 * @param {Array} items — массив элементов (models/sections)
 * @param {object} options
 * @param {function(string):void} options.onSelect — вызывается при клике по карточке
 */
function resolvePreviewUrl(preview) {
  if (!preview) return "";

  const isAbsolute =
    /^https?:\/\//i.test(preview) ||
    preview.startsWith("/") ||
    preview.startsWith("data:");

  return isAbsolute
    ? preview
    : `https://api.apparchi.ru/?path=${encodeURIComponent(preview)}`;
}

function warmPreview(preview) {
  const url = resolvePreviewUrl(preview);
  if (!url) return;

  cachedFetch(url).catch(() => {});
}

async function applyCachedPreview(img, preview) {
  const url = resolvePreviewUrl(preview);
  if (!url || !img) return;

  try {
    const blob = await cachedFetch(url);
    const objectUrl = URL.createObjectURL(blob);

    img.onload = () => {
      URL.revokeObjectURL(objectUrl);
    };

    img.src = objectUrl;
  } catch (e) {
    // fallback на прямой URL, если вдруг что-то пошло не так
    img.src = url;
  }
}

export function renderGallery(containerEl, items, { onSelect }) {
  if (!containerEl) {
    console.error("renderGallery: containerEl is null");
    return;
  }

  if (typeof onSelect !== "function") {
    console.error("renderGallery: onSelect must be a function");
    return;
  }

  containerEl.innerHTML = "";

  items.forEach((m) => {
    const card = document.createElement("div");
    card.className = "model-card";
    if (m.isBaseCard) {
  card.classList.add("model-card-base");
}
    card.dataset.model = m.id;

    const thumb = document.createElement("div");
    thumb.className = "model-thumb";

if (m.preview) {
  const img = document.createElement("img");

  img.alt = m.name || "";
  img.loading = "lazy";
  img.decoding = "async";

  thumb.appendChild(img);

  // сначала ставим превью через кэш
  applyCachedPreview(img, m.preview);

  // и параллельно прогреваем кэш на будущее
  warmPreview(m.preview);
} else {
      thumb.textContent = m.thumbLetter || (m.name ? m.name.charAt(0) : "?");
    }

    const caption = document.createElement("div");
    caption.className = "model-caption";
    caption.textContent = m.name || "";

    const desc = document.createElement("div");
    desc.className = "model-desc";
    desc.textContent = m.desc || "";

    card.appendChild(thumb);
    card.appendChild(caption);
    card.appendChild(desc);

    card.addEventListener("click", () => onSelect(m.id));
    containerEl.appendChild(card);
  });
}

/**
 * Совместимость со старым поведением:
 * initGallery рендерит MODELS (как раньше).
 */
export function initGallery(containerEl, { onSelect }) {
  renderGallery(containerEl, MODELS, { onSelect });
}

/**
 * Показать галерею
 */
export function showGallery(containerEl, viewerWrapperEl) {
  if (containerEl) containerEl.classList.remove("hidden");
  if (viewerWrapperEl) viewerWrapperEl.classList.remove("visible");
}

/**
 * Скрыть галерею
 */
export function hideGallery(containerEl) {
  if (containerEl) containerEl.classList.add("hidden");
}
