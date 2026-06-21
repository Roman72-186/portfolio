// js/viewer.js
//
// Viewer (tabs: 3D / Scheme / Video)
// Поддерживает video overlay: #videoOverlay с #videoList и #videoEmpty
// Video.js сам управляет: карточки <-> режим плеера + панель в тулбаре

import { MODELS, loadModel, getModelMeta } from "./models.js";
import { initThree, setModel as threeSetModel, clearModel as threeClearModel, resize as threeResize } from "./threeViewer.js";
import { initScheme, setSchemeImages, activateScheme, deactivateScheme, resetSchemeView } from "./scheme.js";
import { initVideo, setVideoList, activateVideo, deactivateVideo } from "./video.js";

let dom = null;
let currentModelId = null;
let currentMeta = null;
let activeView = "3d"; // "3d" | "scheme" | "video"
// ✅ если открыты "Врезки" — архитектурный viewer должен молчать
function isInsetModeActive() {
  return document.body.classList.contains("inset-mode");
}
function isRoomsModeActive() {
  return document.body.classList.contains("rooms-mode");
}

// ✅ защита от гонок: если быстро переключили модель/раздел — старый промис игнорируем
let archLoadSeq = 0;

export function initViewer(refs) {
  dom = { ...refs };

  if (!dom.viewerToolbarEl) {
    dom.viewerToolbarEl = document.querySelector(".viewer-toolbar");
  }

    if (!dom.schemePrevBtn) dom.schemePrevBtn = document.getElementById("schemePrevBtn");
  if (!dom.schemeNextBtn) dom.schemeNextBtn = document.getElementById("schemeNextBtn");

  initThree(dom.canvasEl);

  initScheme({
    overlayEl: dom.schemeOverlayEl,
    imgEl: dom.schemeImgEl,
    prevBtnEl: dom.schemePrevBtn,
    nextBtnEl: dom.schemeNextBtn,
    context: "arch",
onUiVisibility: (hidden) => {
  if (activeView !== "scheme" && activeView !== "photo") {
    setUiHidden(false);
    return;
  }
  setUiHidden(hidden);
}
  });
  // VIDEO init (важно: refs совпадают с новым video.js)
initVideo(
  {
    overlayEl: dom.videoOverlayEl,
    listEl: dom.videoListEl,
    emptyEl: dom.videoEmptyEl,

    toolbarEl: dom.viewerToolbarEl,
    tab3dBtn: dom.tab3dBtn,
    tabSchemeBtn: dom.tabSchemeBtn,
    tabPhotoBtn: dom.tabPhotoBtn,
    tabVideoBtn: dom.tabVideoBtn
  },
    {
onPlay: () => {
  // НЕ прячем тулбар, иначе navPanel не появится никогда
  setUiHidden(false);
  document.body.classList.add("video-playing");
},

onPause: () => {
  setUiHidden(false);
  document.body.classList.remove("video-playing");
}

    }
  );

  setupUiHandlers();
  setup3dUiAutoHide();
  setupGlobalTouchBlock();

window.addEventListener("resize", handleResize);

window.addEventListener("orientationchange", () => {
  if (activeView === "scheme" || activeView === "photo") {
    scheduleSchemeRelayout();
  }

  if (activeView === "video") {
    syncVideoOverlayOffset();
  }
});

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    if (activeView === "scheme" || activeView === "photo") {
      scheduleSchemeRelayout();
    }

    if (activeView === "video") {
      syncVideoOverlayOffset();
    }
  });
}

requestAnimationFrame(() => {
  syncVideoOverlayOffset();
});

  showGallery();

return {
  openModelById,
  openUniversalArch,
  openContentCard,
  showGallery,
  handleResize,
  setViewMode
};
}

function handleResize() {
  threeResize();

  if (activeView === "scheme" || activeView === "photo") {
    scheduleSchemeRelayout();
  }

  if (activeView === "video") {
    syncVideoOverlayOffset();
  }
}

function syncVideoOverlayOffset() {
  const { viewerToolbarEl, viewerWrapperEl, videoOverlayEl } = dom || {};
  if (!viewerToolbarEl || !viewerWrapperEl || !videoOverlayEl) return;

  const toolbarRect = viewerToolbarEl.getBoundingClientRect();
  const wrapperRect = viewerWrapperEl.getBoundingClientRect();

  const topOffset = Math.max(0, Math.ceil(toolbarRect.bottom - wrapperRect.top + 12));
  videoOverlayEl.style.setProperty("--video-overlay-top", `${topOffset}px`);
}

function scheduleSchemeRelayout() {
  const rerun = () => {
    resetSchemeView();
  };

  requestAnimationFrame(() => {
    rerun();

    requestAnimationFrame(() => {
      rerun();
    });
  });

  setTimeout(rerun, 120);
}

function setupUiHandlers() {
const {
  backBtn,
  prevBtn,
  nextBtn,
  bottomBackBtn,
  bottomPrevBtn,
  bottomNextBtn,
  tab3dBtn,
  tabSchemeBtn,
  tabPhotoBtn,
  tabVideoBtn } = dom;

backBtn.addEventListener("click", () => {
  if (isInsetModeActive() || isRoomsModeActive()) return;

  if (typeof dom.onBackToTreeGallery === "function") {
    dom.onBackToTreeGallery();
    return;
  }

  showGallery();
});

nextBtn.addEventListener("click", () => {
  if (isInsetModeActive() || isRoomsModeActive()) return;

  if (typeof dom.onOpenNextTreeCard === "function") {
    dom.onOpenNextTreeCard();
    return;
  }

  if (!currentModelId) {
    openModelById(MODELS[0].id);
    return;
  }

  let idx = getModelIndex(currentModelId);
  idx = (idx + 1) % MODELS.length;
  openModelById(MODELS[idx].id);
});

prevBtn.addEventListener("click", () => {
  if (isInsetModeActive() || isRoomsModeActive()) return;

  if (typeof dom.onOpenPrevTreeCard === "function") {
    dom.onOpenPrevTreeCard();
    return;
  }

  if (!currentModelId) {
    openModelById(MODELS[0].id);
    return;
  }

  let idx = getModelIndex(currentModelId);
  idx = (idx - 1 + MODELS.length) % MODELS.length;
  openModelById(MODELS[idx].id);
});

bottomBackBtn?.addEventListener("click", () => backBtn.click());
bottomPrevBtn?.addEventListener("click", () => prevBtn.click());
bottomNextBtn?.addEventListener("click", () => nextBtn.click());
tab3dBtn.addEventListener("click", () => {
  if (isInsetModeActive() || isRoomsModeActive()) return;
  const meta = getCurrentModelMeta();
  if (!meta || !getModelCapabilities(meta).has3d) return;
  setViewMode("3d");
});

tabSchemeBtn.addEventListener("click", () => {
  if (isInsetModeActive() || isRoomsModeActive()) return;
  const meta = getCurrentModelMeta();
  if (!meta || !meta.schemes || meta.schemes.length === 0) return;
  setViewMode("scheme");
});

tabPhotoBtn?.addEventListener("click", () => {
  if (isInsetModeActive() || isRoomsModeActive()) return;
  const meta = getCurrentModelMeta();
  if (!meta || !meta.photos || meta.photos.length === 0) return;
  setViewMode("photo");
});

tabVideoBtn.addEventListener("click", () => {
  if (isInsetModeActive() || isRoomsModeActive()) return;
  const meta = getCurrentModelMeta();
  if (!meta || !meta.video || meta.video.length === 0) return;
  setViewMode("video");
});
}

function setupGlobalTouchBlock() {
  const { viewerWrapperEl } = dom;

document.addEventListener(
  "touchmove",
  (e) => {
    if (!viewerWrapperEl || !viewerWrapperEl.classList.contains("visible")) return;
    if (document.body.classList.contains("inset-mode")) return;
    if (document.body.classList.contains("rooms-mode")) return;

    if (activeView === "video" && !document.body.classList.contains("video-playing")) {
      const inVideoOverlay = e.target && e.target.closest && e.target.closest("#videoOverlay");
      if (inVideoOverlay) return;
    }

    e.preventDefault();
  },
  { passive: false }
);
}

function getModelIndex(id) {
  return MODELS.findIndex((m) => m.id === id);
}

function getCurrentModelMeta() {
  if (currentMeta) return currentMeta;
  if (!currentModelId) return null;
  return getModelMeta(currentModelId);
}

function setCanvasInteractionEnabled(enabled) {
  if (!dom?.canvasEl) return;
  dom.canvasEl.style.pointerEvents = enabled ? "auto" : "none";
}

function getModelCapabilities(meta) {
  return {
    has3d: !!(meta && meta.id !== "arch_0"),
    hasScheme: Array.isArray(meta?.schemes) && meta.schemes.length > 0,
    hasPhoto: Array.isArray(meta?.photos) && meta.photos.length > 0,
    hasVideo: Array.isArray(meta?.video) && meta.video.length > 0
  };
}
function chooseStartView(meta) {
  const { has3d, hasScheme, hasPhoto, hasVideo } = getModelCapabilities(meta);

  if (has3d) return "3d";
  if (hasScheme) return "scheme";
  if (hasPhoto) return "photo";
  if (hasVideo) return "video";
  return "3d";
}

function openUniversalArch(modelItem, card) {
  if (!modelItem || isInsetModeActive() || isRoomsModeActive()) return;

  const meta = {
    id: modelItem.id || modelItem.sourcePath || card.id,
    name: card?.title || modelItem.name || "Model",
    desc: card?.desc || "",
    preview: card?.preview || "",

    schemes: [],
    photos: [],
    video: [],

    modelItem
  };

  currentModelId = meta.id;

  dom.modelLabelEl.textContent = meta.name;

  hideGallery();
  showViewer();
  setUiHidden(false);

  configureViewTabsForModel(meta);

  setCanvasInteractionEnabled(true);

  startModelLoading(meta);
}

function normalizeContentAssetUrl(url) {
  if (!url) return url;

  const s = String(url);

  const isAbsolute =
    /^https?:\/\//i.test(s) ||
    s.startsWith("/") ||
    s.startsWith("data:");

  return isAbsolute
    ? s
    : `https://api.apparchi.ru/?path=${encodeURIComponent(s)}`;
}

function openContentCard(card) {
  if (!card || isInsetModeActive() || isRoomsModeActive()) return;

  const meta = {
    id: card.id,
    name: card.title,
    desc: card.desc,
    preview: card.preview,

schemes:
  (card?.blocks?.schemes?.subblocks?.schemes?.items || [])
    .map(normalizeContentAssetUrl),

photos:
  (card?.blocks?.drawing?.subblocks?.photos?.items || [])
    .map(normalizeContentAssetUrl),

    video:
      card?.blocks?.video?.subblocks?.videos?.items || []
  };

  currentModelId = meta.id;
  currentMeta = meta;

  if (dom.modelLabelEl) dom.modelLabelEl.textContent = meta.name;

  hideGallery();
  showViewer();
  setUiHidden(false);

  configureViewTabsForModel(meta);

  threeClearModel();
  setCanvasInteractionEnabled(false);

  hideLoading();
  setStatus("");

  setViewMode(chooseStartView(meta));
}

function openModelById(modelId) {
  if (isInsetModeActive() || isRoomsModeActive()) return;
  const meta = getModelMeta(modelId);
  if (!meta) return;
  currentMeta = meta;

  currentModelId = modelId;

  dom.modelLabelEl.textContent = meta.name;

  hideGallery();
  showViewer();
  setUiHidden(false);

  configureViewTabsForModel(meta);

  const { has3d } = getModelCapabilities(meta);
  setCanvasInteractionEnabled(has3d && chooseStartView(meta) === "3d");

if (!has3d) {
  archLoadSeq += 1;
  threeClearModel();
  hideLoading();
  setStatus("");
  return;
}
  startModelLoading(meta);
}

function startModelLoading(meta) {
  if (isInsetModeActive() || isRoomsModeActive()) return;
const mySeq = ++archLoadSeq; // ✅ номер этой загрузки
threeClearModel();
showLoading("Загрузка…", 0);
setStatus("Загрузка: " + meta.name);

  loadModel(meta.modelItem || meta.id, {
    onProgress: (percent) => {
      if (typeof percent === "number") {
        showLoading("Загрузка: " + percent.toFixed(0) + "%", percent);
      } else {
        showLoading("Загрузка…", null);
      }
    },
    onStatus: (text) => setStatus(text)
  })
.then(({ root }) => {
  if (mySeq !== archLoadSeq) return;     // ✅ устаревшая загрузка
  if (isInsetModeActive()) return;       // ✅ уже в "Врезках"

  threeSetModel(root);
  hideLoading();
  setViewMode("3d");
})
.catch((err) => {
  if (mySeq !== archLoadSeq) return; // ✅ устаревшая загрузка
  if (isInsetModeActive()) return;   // ✅ уже в "Врезках"

  console.error("Ошибка загрузки модели:", err);
  hideLoading();
  setStatus("Ошибка загрузки модели");
});
}

function configureViewTabsForModel(meta) {
const { tab3dBtn, tabSchemeBtn, tabPhotoBtn, tabVideoBtn } = dom;
const { has3d, hasScheme, hasPhoto, hasVideo } = getModelCapabilities(meta);

  if (tab3dBtn) {
    tab3dBtn.style.display = has3d ? "" : "none";
    tab3dBtn.classList.toggle("disabled", !has3d);
  }

  if (tabSchemeBtn) {
    tabSchemeBtn.style.display = hasScheme ? "" : "none";
    tabSchemeBtn.classList.toggle("disabled", !hasScheme);
  }
    if (tabPhotoBtn) {
    tabPhotoBtn.style.display = hasPhoto ? "" : "none";
    tabPhotoBtn.classList.toggle("disabled", !hasPhoto);
  }

  if (tabVideoBtn) {
    tabVideoBtn.style.display = hasVideo ? "" : "none";
    tabVideoBtn.classList.toggle("disabled", !hasVideo);
  }

  if (hasVideo) {
    setVideoList(meta.video);
  } else {
    setVideoList([]);
  }

  setViewMode(chooseStartView(meta));
}

function setViewMode(mode) {
  activeView = mode;

  const {
    tab3dBtn,
    tabSchemeBtn,
    tabPhotoBtn,
    tabVideoBtn,
    schemeOverlayEl,
    videoOverlayEl
  } = dom;

  const isImageMode = mode === "scheme" || mode === "photo";
  const isVideoMode = mode === "video";
  const is3dMode = mode === "3d";

  tab3dBtn?.classList.toggle("active", is3dMode);
  tabSchemeBtn?.classList.toggle("active", mode === "scheme");
  tabPhotoBtn?.classList.toggle("active", mode === "photo");
  tabVideoBtn?.classList.toggle("active", isVideoMode);

  // ВАЖНО: только 3D имеет право принимать pointer events от canvas
  setCanvasInteractionEnabled(is3dMode);

  // IMAGE MODE: Схема / Фото
  if (schemeOverlayEl) {
    schemeOverlayEl.style.display = isImageMode ? "flex" : "none";

    if (isImageMode) {
      const meta = getCurrentModelMeta();

      if (meta) {
        if (mode === "scheme") {
          setSchemeImages(Array.isArray(meta.schemes) ? meta.schemes : []);
        } else {
          setSchemeImages(Array.isArray(meta.photos) ? meta.photos : []);
        }
      }

      activateScheme();
    } else {
      deactivateScheme();
    }
  }

  // VIDEO MODE
  if (videoOverlayEl) {
    videoOverlayEl.style.display = isVideoMode ? "block" : "none";

    if (isVideoMode) {
      syncVideoOverlayOffset();
      activateVideo();
    } else {
      deactivateVideo();
      document.body.classList.remove("video-playing");
    }
  }

  // Вне image-mode UI должен вернуться
  if (!isImageMode) {
    setUiHidden(false);
  }
}

function showGallery() {
  archLoadSeq += 1;

  deactivateScheme();
  deactivateVideo();

  if (dom?.schemeOverlayEl) dom.schemeOverlayEl.style.display = "none";
  if (dom?.videoOverlayEl) dom.videoOverlayEl.style.display = "none";

  document.body.classList.remove("video-playing");

  threeClearModel();

  currentModelId = null;
  currentMeta = null;
  activeView = "3d";
  setCanvasInteractionEnabled(true);
  setUiHidden(false);

  const { galleryEl, viewerWrapperEl } = dom;
  if (galleryEl) galleryEl.classList.remove("hidden");
  if (viewerWrapperEl) viewerWrapperEl.classList.remove("visible");
  setStatus("");
}

function hideGallery() {
  const { galleryEl } = dom;
  if (galleryEl) galleryEl.classList.add("hidden");
}

function showViewer() {
  const { viewerWrapperEl } = dom;
  if (viewerWrapperEl) viewerWrapperEl.classList.add("visible");
}

function showLoading(text, percent) {
  const { loadingEl, loadingTextEl, progressBarEl } = dom;
  if (!loadingEl || !loadingTextEl || !progressBarEl) return;

  loadingEl.style.display = "flex";
  loadingTextEl.textContent = text;

  if (typeof percent === "number") progressBarEl.style.width = percent.toFixed(0) + "%";
  else progressBarEl.style.width = "15%";
}

function hideLoading() {
  const { loadingEl } = dom;
  if (!loadingEl) return;
  loadingEl.style.display = "none";
}

function setStatus(text) {
  const { statusEl } = dom;
  if (!statusEl) return;
  statusEl.textContent = text || "";
}

function setUiHidden(hidden) {
  const { viewerToolbarEl, statusEl } = dom;
  const bottomNavEl = document.getElementById("viewerBottomNav");
  if (!viewerToolbarEl || !statusEl) return;

  if (hidden) {
    viewerToolbarEl.classList.add("ui-hidden");
    statusEl.classList.add("ui-hidden");
    bottomNavEl?.classList.add("ui-hidden");
  } else {
    viewerToolbarEl.classList.remove("ui-hidden");
    statusEl.classList.remove("ui-hidden");
    bottomNavEl?.classList.remove("ui-hidden");
  }
}

function setup3dUiAutoHide() {
  const { canvasEl, viewerWrapperEl } = dom;
  if (!canvasEl || !viewerWrapperEl) return;

  let isDown = false;
  let moved = false;
  let startX = 0;
  let startY = 0;

  const MOVE_THRESHOLD = 6;

  const isViewerVisible = () => viewerWrapperEl.classList.contains("visible");
  const is3dActive = () => activeView === "3d";

  const getPoint = (e) => {
    if (typeof e.clientX === "number") return { x: e.clientX, y: e.clientY };
    const t = e.touches && e.touches[0];
    return { x: t ? t.clientX : 0, y: t ? t.clientY : 0 };
  };

  const onDown = (e) => {
    if (document.body.classList.contains("inset-mode")) return;
    if (!isViewerVisible() || !is3dActive()) return;
    isDown = true;
    moved = false;
    const p = getPoint(e);
    startX = p.x;
    startY = p.y;
  };

  const onMove = (e) => {
    if (document.body.classList.contains("inset-mode")) return;
    if (!isDown) return;
    if (!isViewerVisible() || !is3dActive()) return;
    const p = getPoint(e);
    const dx = p.x - startX;
    const dy = p.y - startY;
    if (!moved && Math.hypot(dx, dy) >= MOVE_THRESHOLD) {
      moved = true;
      setUiHidden(true);
    }
  };

  const onUp = () => {
    if (document.body.classList.contains("inset-mode")) return;
    if (!isViewerVisible() || !is3dActive()) return;
    if (!moved) setUiHidden(false);
    isDown = false;
  };

  canvasEl.addEventListener("pointerdown", onDown, { passive: true });
  canvasEl.addEventListener("pointermove", onMove, { passive: true });
  window.addEventListener("pointerup", onUp, { passive: true });
}
