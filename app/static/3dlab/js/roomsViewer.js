// js/roomsViewer.js
//
// Новый viewer для раздела "Комнатки".
// По UX почти повторяет viewer.js, но работает на ROOMS,
// а не на MODELS.

import { loadModel } from "./models.js";
import {
  setModel as threeSetModel,
  clearModel as threeClearModel,
  resize as threeResize,
  setRoomsFlatMode,
  setRoomTexturesDir
} from "./threeViewer.js";
import {
  initScheme,
  setSchemeImages,
  activateScheme,
  deactivateScheme,
  resetSchemeView
} from "./scheme.js";
import {
  initVideo,
  setVideoList,
  activateVideo,
  deactivateVideo
} from "./video.js";
import { ROOMS, getRoomMeta } from "./roomsModels.js";

let dom = null;
let currentRoomId = null;
let activeView = "3d"; // "3d" | "scheme" | "photo" | "video"
let roomLoadSeq = 0;

function isInsetModeActive() {
  return document.body.classList.contains("inset-mode");
}

function isRoomsModeActive() {
  return document.body.classList.contains("rooms-mode");
}

export function initRoomsViewer(refs) {
  dom = { ...refs };

  if (!dom.viewerToolbarEl) {
    dom.viewerToolbarEl = document.querySelector(".viewer-toolbar");
  }

  if (!dom.schemePrevBtn) dom.schemePrevBtn = document.getElementById("schemePrevBtn");
  if (!dom.schemeNextBtn) dom.schemeNextBtn = document.getElementById("schemeNextBtn");

  initScheme({
    overlayEl: dom.schemeOverlayEl,
    imgEl: dom.schemeImgEl,
    prevBtnEl: dom.schemePrevBtn,
    nextBtnEl: dom.schemeNextBtn,
    context: "rooms",
    onUiVisibility: (hidden) => {
      if (activeView !== "scheme" && activeView !== "photo") {
        setUiHidden(false);
        return;
      }
      setUiHidden(hidden);
    }
  });

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
  openRoomById,
  openUniversalRoom,
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
    tabVideoBtn
  } = dom;

  const stopRoomsEvent = (e) => {
    if (!isRoomsModeActive()) return false;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    return true;
  };

backBtn?.addEventListener("click", (e) => {
  if (!stopRoomsEvent(e)) return;

  if (typeof dom.onBackToTreeGallery === "function") {
    dom.onBackToTreeGallery();
    return;
  }

  showGallery();
}, true);

  prevBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    if (typeof dom.onOpenPrevTreeCard === "function") {
  dom.onOpenPrevTreeCard();
  return;
}

    if (!currentRoomId) {
      openRoomById(ROOMS[0].id);
      return;
    }

    let idx = getRoomIndex(currentRoomId);
    idx = (idx - 1 + ROOMS.length) % ROOMS.length;
    openRoomById(ROOMS[idx].id);
  }, true);

  nextBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    if (typeof dom.onOpenNextTreeCard === "function") {
  dom.onOpenNextTreeCard();
  return;
}

    if (!currentRoomId) {
      openRoomById(ROOMS[0].id);
      return;
    }

    let idx = getRoomIndex(currentRoomId);
    idx = (idx + 1) % ROOMS.length;
    openRoomById(ROOMS[idx].id);
  }, true);

  bottomBackBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    if (typeof dom.onBackToTreeGallery === "function") {
  dom.onBackToTreeGallery();
  return;
}
    showGallery();
  }, true);

  bottomPrevBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
if (typeof dom.onOpenPrevTreeCard === "function") {
  dom.onOpenPrevTreeCard();
  return;
}

    if (!currentRoomId) {
      openRoomById(ROOMS[0].id);
      return;
    }

    let idx = getRoomIndex(currentRoomId);
    idx = (idx - 1 + ROOMS.length) % ROOMS.length;
    openRoomById(ROOMS[idx].id);
  }, true);

  bottomNextBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    if (typeof dom.onOpenNextTreeCard === "function") {
  dom.onOpenNextTreeCard();
  return;
}

    if (!currentRoomId) {
      openRoomById(ROOMS[0].id);
      return;
    }

    let idx = getRoomIndex(currentRoomId);
    idx = (idx + 1) % ROOMS.length;
    openRoomById(ROOMS[idx].id);
  }, true);

  tab3dBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    const meta = getCurrentRoomMeta();
    if (!meta || !getRoomCapabilities(meta).has3d) return;
    setViewMode("3d");
  }, true);

  tabSchemeBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    const meta = getCurrentRoomMeta();
    if (!meta || !getRoomCapabilities(meta).hasScheme) return;
    setViewMode("scheme");
  }, true);

  tabPhotoBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    const meta = getCurrentRoomMeta();
    if (!meta || !getRoomCapabilities(meta).hasPhoto) return;
    setViewMode("photo");
  }, true);

  tabVideoBtn?.addEventListener("click", (e) => {
    if (!stopRoomsEvent(e)) return;
    const meta = getCurrentRoomMeta();
    if (!meta || !getRoomCapabilities(meta).hasVideo) return;
    setViewMode("video");
  }, true);
}

function setupGlobalTouchBlock() {
  const { viewerWrapperEl } = dom;

document.addEventListener(
  "touchmove",
  (e) => {
    if (!viewerWrapperEl || !viewerWrapperEl.classList.contains("visible")) return;
    if (document.body.classList.contains("inset-mode")) return;
    if (!isRoomsModeActive()) return;

    if (activeView === "video" && !document.body.classList.contains("video-playing")) {
      const inVideoOverlay = e.target && e.target.closest && e.target.closest("#videoOverlay");
      if (inVideoOverlay) return;
    }

    e.preventDefault();
  },
  { passive: false }
);
}

function getRoomIndex(id) {
  return ROOMS.findIndex((m) => m.id === id);
}

function getCurrentRoomMeta() {
  if (!currentRoomId) return null;
  return getRoomMeta(currentRoomId);
}

function setCanvasInteractionEnabled(enabled) {
  if (!dom?.canvasEl) return;
  dom.canvasEl.style.pointerEvents = enabled ? "auto" : "none";
}

function getRoomCapabilities(meta) {
  return {
    has3d: !!(meta && meta.id !== "room_0"),
    hasScheme: Array.isArray(meta?.schemes) && meta.schemes.length > 0,
    hasPhoto: Array.isArray(meta?.photos) && meta.photos.length > 0,
    hasVideo: Array.isArray(meta?.video) && meta.video.length > 0
  };
}

function chooseStartView(meta) {
  const { has3d, hasScheme, hasPhoto, hasVideo } = getRoomCapabilities(meta);

  if (has3d) return "3d";
  if (hasScheme) return "scheme";
  if (hasPhoto) return "photo";
  if (hasVideo) return "video";
  return "3d";
}

function openUniversalRoom(modelItem, card) {
  if (isInsetModeActive()) return;
  if (!modelItem || !card) return;

  const meta = {
    id: modelItem.id || card.id,
    name: card.title,
    desc: card.desc,
    preview: card.preview,

    sourcePath: modelItem.sourcePath,
    textures: modelItem.textures || null,
    texturesDir: modelItem.texturesDir || "",

    schemes:
      card?.blocks?.schemes?.subblocks?.schemes?.items || [],

    photos:
      card?.blocks?.photos?.subblocks?.photos?.items || [],

    video:
      card?.blocks?.["3d"]?.subblocks?.videos?.items || []
  };

  currentRoomId = meta.id;

  document.body.classList.add("rooms-mode");
  setRoomsFlatMode(true);

  dom.modelLabelEl.textContent = meta.name;

  hideGallery();
  showViewer();
  setUiHidden(false);

  configureViewTabsForRoom(meta);

  const { has3d } = getRoomCapabilities(meta);
  setCanvasInteractionEnabled(has3d && chooseStartView(meta) === "3d");

  if (!has3d) {
    roomLoadSeq += 1;
    threeClearModel();
    hideLoading();
    setStatus("");
    return;
  }

  startRoomLoading(meta);
}

function openRoomById(roomId) {
  if (isInsetModeActive()) return;

  const meta = getRoomMeta(roomId);
  if (!meta) return;

  currentRoomId = roomId;
  document.body.classList.add("rooms-mode");
  setRoomsFlatMode(true);
  dom.modelLabelEl.textContent = meta.name;

  hideGallery();
  showViewer();
  setUiHidden(false);

  configureViewTabsForRoom(meta);

  const { has3d } = getRoomCapabilities(meta);
  setCanvasInteractionEnabled(has3d && chooseStartView(meta) === "3d");

  if (!has3d) {
    roomLoadSeq += 1;
    threeClearModel();
    hideLoading();
    setStatus("");
    return;
  }

  startRoomLoading(meta);
}

function startRoomLoading(meta) {
  if (isInsetModeActive()) return;

  const mySeq = ++roomLoadSeq;
  threeClearModel();
  showLoading("Загрузка…", 0);
  setStatus("Загрузка: " + meta.name);

  loadModel(
  meta.sourcePath
    ? {
        id: meta.id,
        sourcePath: meta.sourcePath,
        textures: meta.textures || null
      }
    : (meta.sourceId || meta.id),
{
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
      if (mySeq !== roomLoadSeq) return;
      if (isInsetModeActive()) return;

setRoomsFlatMode(true);
setRoomTexturesDir(meta.texturesDir || "");
threeSetModel(root);
setRoomsFlatMode(true, root);

      hideLoading();
      setViewMode("3d");
    })
    .catch((err) => {
      if (mySeq !== roomLoadSeq) return;
      if (isInsetModeActive()) return;

      console.error("Ошибка загрузки room-модели:", err);
      hideLoading();
      setStatus("Ошибка загрузки модели");
    });
}

function configureViewTabsForRoom(meta) {
  const { tab3dBtn, tabSchemeBtn, tabPhotoBtn, tabVideoBtn } = dom;
  const { has3d, hasScheme, hasPhoto, hasVideo } = getRoomCapabilities(meta);

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

  // Только 3D принимает pointer events от canvas
  setCanvasInteractionEnabled(is3dMode);

  // IMAGE MODE: Схема / Фото
  if (schemeOverlayEl) {
    schemeOverlayEl.style.display = isImageMode ? "flex" : "none";

    if (isImageMode) {
      const meta = getCurrentRoomMeta();

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
  roomLoadSeq += 1;

  deactivateScheme();
  deactivateVideo();

  if (dom?.schemeOverlayEl) dom.schemeOverlayEl.style.display = "none";
  if (dom?.videoOverlayEl) dom.videoOverlayEl.style.display = "none";

  document.body.classList.remove("video-playing");
  document.body.classList.remove("rooms-mode");
  setRoomsFlatMode(false);
  setRoomTexturesDir("");

  threeClearModel();

  currentRoomId = null;
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

  if (typeof percent === "number") {
    progressBarEl.style.width = percent.toFixed(0) + "%";
  } else {
    progressBarEl.style.width = "15%";
  }
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
  if (!isRoomsModeActive()) return;
  if (!isViewerVisible() || !is3dActive()) return;
  isDown = true;
  moved = false;
  const p = getPoint(e);
  startX = p.x;
  startY = p.y;
};

const onMove = (e) => {
  if (document.body.classList.contains("inset-mode")) return;
  if (!isRoomsModeActive()) return;
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
  if (!isRoomsModeActive()) return;
  if (!isViewerVisible() || !is3dActive()) return;

  if (!moved) setUiHidden(false);
  isDown = false;
};

  canvasEl.addEventListener("pointerdown", onDown, { passive: true });
  canvasEl.addEventListener("pointermove", onMove, { passive: true });
  window.addEventListener("pointerup", onUp, { passive: true });
}
