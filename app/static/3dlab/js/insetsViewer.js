// js/insetsViewer.js
// Viewer для "Врезок": только 3D, без схем и видео.
// UI: Prev / Галерея / Next остаётся тем же.
import * as THREE from "three";
import {
  setModel as threeSetModel,
  clearModel as threeClearModel,
  setInsetBlendEnabled,
  setInsetBlendState,
  setInsetSectionBlendState,
  setOutlineExcludedMaterials,
  setCadOverlay,
  clearCadOverlay,
  setOutlineEnabled,
  setOutlineStyle,
  setCadAlpha,
  setInsetNeutralLighting,
  setSectionEdgesOverlay,
  clearSectionEdgesOverlay,
  setSectionEdgesAlpha
} from "./threeViewer.js";
import { loadModel } from "./models.js";
import { INSETS, getInsetMeta } from "./insetsModels.js";
import { initScheme, setSchemeImages, activateScheme, deactivateScheme, resetSchemeView } from "./scheme.js";
import { initVideo, setVideoList, activateVideo, deactivateVideo } from "./video.js";

let dom = null;
let currentId = null;
let currentMeta = null;
let activeView = "3d";
// ✅ защита от гонок при быстрых переключениях врезок
let insetLoadSeq = 0;
// ✅ Материалы, которыми управляет ползунок прозрачности
let controlledMaterials = [];
let sectionMaterials = [];
let currentOpacity = 1; // 0..1


export function initInsetsViewer(refs) {
  dom = { ...refs };
  if (!dom.canvasEl) throw new Error("initInsetsViewer: canvasEl missing");

  if (!dom.viewerToolbarEl) dom.viewerToolbarEl = document.querySelector(".viewer-toolbar");
  if (!dom.schemeOverlayEl) dom.schemeOverlayEl = document.getElementById("schemeOverlay");
  if (!dom.schemeImgEl) dom.schemeImgEl = document.getElementById("schemeImage");
  if (!dom.videoOverlayEl) dom.videoOverlayEl = document.getElementById("videoOverlay");
  if (!dom.videoListEl) dom.videoListEl = document.getElementById("videoList");
  if (!dom.videoEmptyEl) dom.videoEmptyEl = document.getElementById("videoEmpty");
  if (!dom.schemePrevBtn) dom.schemePrevBtn = document.getElementById("schemePrevBtn");
  if (!dom.schemeNextBtn) dom.schemeNextBtn = document.getElementById("schemeNextBtn");

  initScheme({
    overlayEl: dom.schemeOverlayEl,
    imgEl: dom.schemeImgEl,
    prevBtnEl: dom.schemePrevBtn,
    nextBtnEl: dom.schemeNextBtn,
    context: "inset",
    onUiVisibility: (hidden) => {
      if (activeView !== "scheme") {
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
  setupInset3dUiAutoHide();
window.addEventListener("resize", () => {
  if (activeView === "scheme") {
    scheduleSchemeRelayout();
  }

  if (activeView === "video") {
    syncVideoOverlayOffset();
  }
});

  window.addEventListener("orientationchange", () => {
  if (activeView === "scheme") {
    scheduleSchemeRelayout();
  }

  if (activeView === "video") {
    syncVideoOverlayOffset();
  }
});

if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => {
    if (activeView === "scheme") {
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

return {
  openById,
  openUniversalInset,
  showGallery,
  enterInsetMode,
  exitInsetMode,
  setViewMode
};
}

function enterInsetMode() {
  document.body.classList.add("inset-mode");

  setInsetBlendEnabled(true);
  setInsetNeutralLighting(true);
    // Контуры только во Врезках
  setOutlineEnabled(true);
  setOutlineStyle({ thicknessPx: 2.0, edgesAngle: 60 });
  setInsetBlendState(0, []); // ✅ пока модель не загружена — материалов ещё нет
  setInsetSectionBlendState(0.5, []); // пока не загрузили — материалов нет, но коэффициент фиксируем

  // ✅ Сбрасываем прозрачность на 100% при входе во Врезки
  currentOpacity = 1;
  if (dom?.insetOpacitySlider) dom.insetOpacitySlider.value = "100";
}


function exitInsetMode() {
  document.body.classList.remove("inset-mode");
  document.body.classList.remove("inset-has-3d");
  document.body.classList.remove("inset-no-tabs");
  document.body.classList.remove("inset-view-3d");
  document.body.classList.remove("inset-view-scheme");
  document.body.classList.remove("inset-view-video");
  document.body.classList.remove("video-playing");
  setInsetBlendState(0, []);
  setInsetSectionBlendState(0.5, []);
  setOutlineExcludedMaterials([]);
  setInsetBlendEnabled(false);
  setInsetNeutralLighting(false);

  clearCadOverlay();
  clearSectionEdgesOverlay();
  setOutlineEnabled(false);

  deactivateScheme();
  deactivateVideo();

  if (dom?.schemeOverlayEl) dom.schemeOverlayEl.style.display = "none";
  if (dom?.videoOverlayEl) dom.videoOverlayEl.style.display = "none";
    setCanvasInteractionEnabled(true);
  activeView = "3d";
}

function hardResetInsetRuntime() {
  exitInsetMode();
  threeClearModel();

  controlledMaterials = [];
  sectionMaterials = [];
  currentOpacity = 1;

  if (dom?.insetOpacitySlider) {
    dom.insetOpacitySlider.value = "100";
  }

  setCadAlpha(0);
  setSectionEdgesAlpha(0);

  setCanvasInteractionEnabled(true);
  setUiHidden(false);
}
// ✅ Собрать все материалы с нужным именем (например "3") внутри загруженной модели
function collectMaterialsByName(root, name) {
  const out = [];
  if (!root || !name) return out;

  root.traverse((obj) => {
    if (!obj.isMesh) return;
    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
    for (const m of mats) {
      if (!m) continue;
      if (m.name === name) out.push(m);
    }
  });

  // ✅ Убираем дубликаты (часто один material шарится несколькими mesh)
  return Array.from(new Set(out));
}

function getSectionNameSets(meta) {
  const primary = Array.isArray(meta?.primarySectionMaterialNames)
    ? meta.primarySectionMaterialNames.map(String)
    : (
        Array.isArray(meta?.sectionMaterialNames)
          ? meta.sectionMaterialNames.map(String)
          : []
      );

  const auxiliary = Array.isArray(meta?.auxSectionMaterialNames)
    ? meta.auxSectionMaterialNames.map(String)
    : [];

  const all = Array.from(new Set([...primary, ...auxiliary]));

  return { primary, auxiliary, all };
}

// ✅ Применить текущую прозрачность ко всем "управляемым" материалам
function applyOpacityToControlled() {
  for (const m of controlledMaterials) {
    if (!m) continue;

    // ✅ рисуем обе стороны (чтобы изнутри тоже было видно)
    m.side = THREE.DoubleSide;

    const needTransparent = currentOpacity < 0.999;

    // Важно: opacity работает только если transparent=true
    m.transparent = needTransparent;
    m.opacity = currentOpacity;

    // чтобы прозрачность работала адекватно (и видеть "внутри")
    m.depthWrite = !needTransparent;

    m.needsUpdate = true;
  }
}


// ✅ Применить “плоские” цвета материалам сечений (например "2" и "3")
// meta.materialColors ожидается как объект: { "2": "#ff3b30", "3": "#34c759" }
function applyInsetColors(root, meta) {
  if (!root || !meta) return;

  const colors = meta.materialColors || {};
  const { primary, auxiliary, all } = getSectionNameSets(meta);

  root.traverse((obj) => {
    if (!obj.isMesh) return;

    const mats = Array.isArray(obj.material) ? obj.material : [obj.material];

    for (const m of mats) {
      if (!m) continue;

      const key = String(m.name);
      const isPrimary = primary.includes(key);
      const isAuxiliary = auxiliary.includes(key);
      const isAnySection = all.includes(key);
      const hex = colors[key];

      if (!isAnySection && !hex) continue;

      // цвет — если задан
      if (hex && m.color) m.color.set(hex);

      m.side = THREE.DoubleSide;

      if ("metalness" in m) m.metalness = 0;
      if ("roughness" in m) m.roughness = 1;

      // ОСНОВНЫЕ СЕЧЕНИЯ: заливка + контур
      if (isPrimary) {
        m.transparent = true;
        m.opacity = 0.6;
        m.depthWrite = false;
        m.depthTest = true;

        m.forceSinglePass = true;

        m.polygonOffset = true;
        m.polygonOffsetFactor = 1;
        m.polygonOffsetUnits = 1;

        m.blending = THREE.NormalBlending;

        const idx = primary.indexOf(key);
        obj.renderOrder = 20 + (idx >= 0 ? idx : 0);
      }

      // ВСПОМОГАТЕЛЬНЫЕ СЕЧЕНИЯ: без заливки, только контур
      if (isAuxiliary) {
        m.transparent = true;
        m.opacity = 0.0;
        m.depthWrite = false;
        m.depthTest = true;

        m.forceSinglePass = true;

        m.polygonOffset = true;
        m.polygonOffsetFactor = 1;
        m.polygonOffsetUnits = 1;

        m.blending = THREE.NormalBlending;

        const idx = auxiliary.indexOf(key);
        obj.renderOrder = 40 + (idx >= 0 ? idx : 0);
      }

      m.needsUpdate = true;
    }
  });
}

// ===============================
// CAD points from glTF nodes (Dummy/Point helpers)
// ===============================
// ===============================
// CAD points from glTF nodes (small meshes named a,b,c,d...)
// ===============================
function buildCadSpecFromModel(root, cadMeta) {
  if (!root || !cadMeta || !cadMeta.fromNodes) return null;

  // Какие имена считаем "точками":
  // - одиночные буквы: a, b, c...
  // - или буква + цифры: a1, b12 (на будущее)
  const pointNameRe = /^[a-z](\d+)?$/;

  const points = [];
  const pointMap = new Map(); // name -> {id,x,y,z}

  root.traverse((obj) => {
    const name = String(obj.name || "").trim();
    if (!pointNameRe.test(name)) return;

    // Берём позицию уже после всех трансформаций (в т.ч. normalizeModel)
    const p = new THREE.Vector3();
    obj.getWorldPosition(p);

    const pt = { id: name, x: p.x, y: p.y, z: p.z };
    pointMap.set(name, pt);
    points.push(pt);

    // Скрываем "точку", если она экспортнулась как видимый mesh
    obj.visible = false;
  });

  // Стабильный порядок: сортируем по имени (a,b,c... a1,a2...)
  points.sort((p1, p2) => p1.id.localeCompare(p2.id, "en"));

  const lines = Array.isArray(cadMeta.lines) ? cadMeta.lines : [];

  // (опционально) можно отфильтровать линии, у которых нет точек
  const filteredLines = lines.filter(([a, b]) => pointMap.has(String(a)) && pointMap.has(String(b)));

  return { points, lines: filteredLines };
}
function setupUiHandlers() {
  const { prevBtn, nextBtn, backBtn } = dom;

  // Prev / Next / Галерея для Врезок:
  // вешаем в capture-режиме и глушим событие,
  // чтобы архитектурный viewer не вмешивался
  prevBtn?.addEventListener("click", (e) => {
    if (!document.body.classList.contains("inset-mode")) return;

    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    if (typeof dom.onOpenPrevTreeCard === "function") {
  dom.onOpenPrevTreeCard();
  return;
}

    if (!currentId) return;
    const idx = getIndex(currentId);
    if (idx < 0) return;

    const nextIdx = (idx - 1 + INSETS.length) % INSETS.length;
    openById(INSETS[nextIdx].id);
  }, true);

  nextBtn?.addEventListener("click", (e) => {
    if (!document.body.classList.contains("inset-mode")) return;

    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    if (typeof dom.onOpenNextTreeCard === "function") {
  dom.onOpenNextTreeCard();
  return;
}

    if (!currentId) return;
    const idx = getIndex(currentId);
    if (idx < 0) return;

    const nextIdx = (idx + 1) % INSETS.length;
    openById(INSETS[nextIdx].id);
  }, true);

  // Кнопка "Галерея" возвращает в галерею
  backBtn?.addEventListener("click", (e) => {
    if (!document.body.classList.contains("inset-mode")) return;

    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    if (typeof dom.onBackToTreeGallery === "function") {
  dom.onBackToTreeGallery();
  return;
}

    showGallery();
  }, true);

  dom.tab3dBtn?.addEventListener("click", () => {
    if (!currentMeta) return;
    if (!getInsetCapabilities(currentMeta).has3d) return;
    setViewMode("3d");
  });

  dom.tabSchemeBtn?.addEventListener("click", () => {
    if (!currentMeta) return;
    if (!getInsetCapabilities(currentMeta).hasScheme) return;
    setViewMode("scheme");
  });

  dom.tabVideoBtn?.addEventListener("click", () => {
    if (!currentMeta) return;
    if (!getInsetCapabilities(currentMeta).hasVideo) return;
    setViewMode("video");
  });

  // ✅ Ползунок прозрачности (работает только для выбранного материала, например "3")
dom.insetOpacitySlider?.addEventListener("input", () => {
const v = Number(dom.insetOpacitySlider.value || 100); // 0..100
const uiOpacity = Math.max(0, Math.min(1, v / 100));   // 0..1
const cadAlpha = Math.min(1, Math.max(0, (1 - uiOpacity) / 0.3));
setCadAlpha(cadAlpha);
setSectionEdgesAlpha(cadAlpha);
  // 0..0.7 — реальная прозрачность как раньше
  if (uiOpacity <= 0.7) {
    currentOpacity = uiOpacity;
    applyOpacityToControlled();

    // смешивание выключаем
    setInsetBlendState(0, controlledMaterials);
    return;
  }

  // 0.7..1.0 — фиксируем "реальную" прозрачность на 0.7
  // и плавно смешиваем картинку 70% -> 100% opaque
  const t = (uiOpacity - 0.7) / 0.3; // 0..1
  currentOpacity = 0.7;
  applyOpacityToControlled();

  setInsetBlendState(t, controlledMaterials);
});
// ✅ Важно: на телефоне не отдаём тач/drag дальше (в canvas), иначе первый drag не цепляется
if (dom.insetOpacitySlider) {
  const stop = (e) => e.stopPropagation();
  const opt = { passive: true, capture: true };

  dom.insetOpacitySlider.addEventListener("pointerdown", stop, opt);
  dom.insetOpacitySlider.addEventListener("pointermove", stop, opt);
  dom.insetOpacitySlider.addEventListener("touchstart", stop, opt);
  dom.insetOpacitySlider.addEventListener("touchmove", stop, opt);
}

}

function getIndex(id) {
  return INSETS.findIndex((m) => m.id === id);
}

function setUiHidden(hidden) {
  const toolbar = dom?.viewerToolbarEl || document.querySelector(".viewer-toolbar");
  const statusEl = dom?.statusEl;
  const bottomNavEl = document.getElementById("viewerBottomNav");
  if (!toolbar || !statusEl) return;

  toolbar.classList.toggle("ui-hidden", !!hidden);
  statusEl.classList.toggle("ui-hidden", !!hidden);
  bottomNavEl?.classList.toggle("ui-hidden", !!hidden);
}

function setupInset3dUiAutoHide() {
  const { canvasEl, viewerWrapperEl } = dom;
  if (!canvasEl || !viewerWrapperEl) return;

  let isDown = false;
  let moved = false;
  let startX = 0;
  let startY = 0;

  const MOVE_THRESHOLD = 6;

  const isViewerVisible = () => viewerWrapperEl.classList.contains("visible");
  const is3dActive = () => document.body.classList.contains("inset-mode") && activeView === "3d";

  const getPoint = (e) => {
    if (typeof e.clientX === "number") return { x: e.clientX, y: e.clientY };
    const t = e.touches && e.touches[0];
    return { x: t ? t.clientX : 0, y: t ? t.clientY : 0 };
  };

  const onDown = (e) => {
    if (!isViewerVisible() || !is3dActive()) return;
    isDown = true;
    moved = false;
    const p = getPoint(e);
    startX = p.x;
    startY = p.y;
  };

  const onMove = (e) => {
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
    if (!isViewerVisible() || !is3dActive()) return;

    if (!moved) {
      setUiHidden(false);
    }

    isDown = false;
  };

  canvasEl.addEventListener("pointerdown", onDown, { passive: true });
  canvasEl.addEventListener("pointermove", onMove, { passive: true });
  window.addEventListener("pointerup", onUp, { passive: true });
}

function normalizeInsetSchemeUrl(url) {
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

function getInsetCapabilities(meta) {
  return {
    has3d: !!(meta && (meta.sourcePath || meta.sourceId) && meta.id !== "inset_0"),
    hasScheme: Array.isArray(meta?.schemes) && meta.schemes.length > 0,
    hasVideo: Array.isArray(meta?.video) && meta.video.length > 0
  };
}

function chooseStartView(meta) {
  const { has3d, hasScheme, hasVideo } = getInsetCapabilities(meta);
  if (has3d) return "3d";
  if (hasScheme) return "scheme";
  if (hasVideo) return "video";
  return "3d";
}

function setInsetViewClass(mode) {
  document.body.classList.remove("inset-view-3d", "inset-view-scheme", "inset-view-video");

  if (mode === "3d") document.body.classList.add("inset-view-3d");
  if (mode === "scheme") document.body.classList.add("inset-view-scheme");
  if (mode === "video") document.body.classList.add("inset-view-video");
}

function setCanvasInteractionEnabled(enabled) {
  if (!dom?.canvasEl) return;
  dom.canvasEl.style.pointerEvents = enabled ? "auto" : "none";
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

function configureViewTabsForInset(meta) {
  currentMeta = meta;

  const { has3d, hasScheme, hasVideo } = getInsetCapabilities(meta);

  document.body.classList.toggle("inset-has-3d", has3d);
  document.body.classList.remove("inset-no-tabs");

  if (dom.tab3dBtn) {
    dom.tab3dBtn.style.display = has3d ? "" : "none";
    dom.tab3dBtn.classList.toggle("disabled", !has3d);
  }

  if (dom.tabSchemeBtn) {
    dom.tabSchemeBtn.style.display = hasScheme ? "" : "none";
    dom.tabSchemeBtn.classList.toggle("disabled", !hasScheme);
  }

    if (dom.tabPhotoBtn) {
    dom.tabPhotoBtn.style.display = "none";
    dom.tabPhotoBtn.classList.add("disabled");
  }

  if (dom.tabVideoBtn) {
    dom.tabVideoBtn.style.display = hasVideo ? "" : "none";
    dom.tabVideoBtn.classList.toggle("disabled", !hasVideo);
  }

  setSchemeImages(
    hasScheme ? meta.schemes.map(normalizeInsetSchemeUrl) : []
  );

  setVideoList(
    hasVideo ? meta.video : []
  );
}

function setViewMode(mode) {
  const prevView = activeView;
  activeView = mode;
  setInsetViewClass(mode);

  const is3dMode = mode === "3d";
  const isSchemeMode = mode === "scheme";
  const isVideoMode = mode === "video";

  if (dom.tab3dBtn) dom.tab3dBtn.classList.toggle("active", is3dMode);
  if (dom.tabSchemeBtn) dom.tabSchemeBtn.classList.toggle("active", isSchemeMode);
  if (dom.tabPhotoBtn) dom.tabPhotoBtn.classList.toggle("active", false);
  if (dom.tabVideoBtn) dom.tabVideoBtn.classList.toggle("active", isVideoMode);

  // Только 3D принимает pointer events от canvas
  setCanvasInteractionEnabled(is3dMode);

  // SCHEME
  if (dom.schemeOverlayEl) {
    dom.schemeOverlayEl.style.display = isSchemeMode ? "flex" : "none";

    if (isSchemeMode) {
      activateScheme();
    } else if (prevView === "scheme") {
      deactivateScheme();
    }
  }

  // VIDEO
  if (dom.videoOverlayEl) {
    dom.videoOverlayEl.style.display = isVideoMode ? "block" : "none";

    if (isVideoMode) {
      syncVideoOverlayOffset();
      activateVideo();
    } else if (prevView === "video") {
      deactivateVideo();
      document.body.classList.remove("video-playing");
    }
  }

  if (!isSchemeMode) {
    setUiHidden(false);
  }
}

export function showGallery() {
  const { galleryEl, viewerWrapperEl, statusEl } = dom;

  deactivateScheme();
  deactivateVideo();

  if (dom?.schemeOverlayEl) dom.schemeOverlayEl.style.display = "none";
  if (dom?.videoOverlayEl) dom.videoOverlayEl.style.display = "none";

  document.body.classList.remove("video-playing");
  document.body.classList.remove("inset-has-3d");
  document.body.classList.remove("inset-no-tabs");
  document.body.classList.remove("inset-view-3d");
  document.body.classList.remove("inset-view-scheme");
  document.body.classList.remove("inset-view-video");

  galleryEl?.classList.remove("hidden");
  viewerWrapperEl?.classList.remove("visible");

  if (statusEl) statusEl.textContent = "";

currentId = null;
currentMeta = null;
activeView = "3d";
setCanvasInteractionEnabled(true);

exitInsetMode();
threeClearModel();

controlledMaterials = [];
currentOpacity = 1;
sectionMaterials = [];
}

export function openUniversalInset(modelItem, card) {
  if (!card || !modelItem) return;

  const rendererSettings = modelItem.rendererSettings || {};

  const meta = {
    id: modelItem.id || card.id,
    name: card.title,
    desc: card.desc,
    preview: card.preview,

    sourcePath: modelItem.sourcePath,
    textures: modelItem.textures || null,

schemes:
  card?.blocks?.schemes?.subblocks?.schemes?.items || [],

video:
  card?.blocks?.["3d"]?.subblocks?.videos?.items || [],

    ...rendererSettings
  };

  openInsetMeta(meta);
}

export function openById(id) {
  const meta = getInsetMeta(id);
  if (!meta) {
    console.error("No inset:", id);
    return;
  }

  openInsetMeta(meta);
}

function openInsetMeta(meta) {
  hardResetInsetRuntime();

  currentId = meta.id;
  currentMeta = meta;
  enterInsetMode();

  const mySeq = ++insetLoadSeq;

  if (dom?.schemeOverlayEl) dom.schemeOverlayEl.style.display = "none";
  if (dom?.videoOverlayEl) dom.videoOverlayEl.style.display = "none";
  document.body.classList.remove("video-playing");

  dom.galleryEl?.classList.add("hidden");
  dom.viewerWrapperEl?.classList.add("visible");

  if (dom.modelLabelEl) dom.modelLabelEl.textContent = meta.name;

  configureViewTabsForInset(meta);

  const startView = chooseStartView(meta);
  const { has3d } = getInsetCapabilities(meta);

  setInsetViewClass(startView);
  setCanvasInteractionEnabled(startView === "3d");

  if (!has3d) {
    controlledMaterials = [];
    sectionMaterials = [];

    setInsetBlendState(0, []);
    setInsetSectionBlendState(0.5, []);
    setOutlineExcludedMaterials([]);
    setCadAlpha(0);
    setSectionEdgesAlpha(0);

    hideLoading();
    setStatus("");
    setViewMode(startView);
    return;
  }

  showLoading(`Загрузка: ${meta.name}`);

  loadModel(
  meta.sourcePath
    ? {
        id: meta.id,
        sourcePath: meta.sourcePath,
        textures: meta.textures || null
      }
    : (meta.sourceId || meta.id),
{
    onProgress: (p) => setProgress(p),
    onStatus: (s) => setStatus(s)
  })
    .then(({ root }) => {
      if (mySeq !== insetLoadSeq) return;
      if (!document.body.classList.contains("inset-mode")) return;

      applyInsetColors(root, meta);

      setInsetBlendEnabled(true);
      setOutlineEnabled(true);

      threeSetModel(root);

      const cadSpec = meta?.cad?.fromNodes
        ? buildCadSpecFromModel(root, meta.cad)
        : meta.cad;

      setCadOverlay(cadSpec);

      const cadAlpha = Math.min(1, Math.max(0, (1 - currentOpacity) / 0.3));
      setCadAlpha(cadAlpha);

      controlledMaterials = collectMaterialsByName(root, meta.opacityMaterialName);
      setInsetBlendState(0, controlledMaterials);

      sectionMaterials = [];
      let outlineExcludedSectionMaterials = [];

      const { primary, all } = getSectionNameSets(meta);

      for (const n of primary) {
        sectionMaterials.push(...collectMaterialsByName(root, n));
      }

      sectionMaterials = Array.from(new Set(sectionMaterials));

      setInsetSectionBlendState(0.5, sectionMaterials);

      for (const n of all) {
        outlineExcludedSectionMaterials.push(...collectMaterialsByName(root, n));
      }

      outlineExcludedSectionMaterials = Array.from(new Set(outlineExcludedSectionMaterials));

      setOutlineExcludedMaterials(outlineExcludedSectionMaterials);

      setSectionEdgesOverlay(
        root,
        all,
        meta.materialColors || {}
      );

      const edgeAlpha = Math.min(1, Math.max(0, (1 - currentOpacity) / 0.3));
      setSectionEdgesAlpha(edgeAlpha);

      applyOpacityToControlled();

      if (controlledMaterials.length === 0) {
        setStatus(`Материал "${meta.opacityMaterialName}" не найден`);
      } else {
        setStatus("");
      }

      hideLoading();
      setViewMode(startView);
    })
    .catch((err) => {
      if (mySeq !== insetLoadSeq) return;
      if (!document.body.classList.contains("inset-mode")) return;

      console.error(err);
      hideLoading();
      setStatus("Ошибка загрузки");
    });
}

function showLoading(text) {
  if (dom.loadingTextEl) dom.loadingTextEl.textContent = text || "";
  if (dom.loadingEl) dom.loadingEl.style.display = "flex";
  setProgress(0);
}

function hideLoading() {
  if (dom.loadingEl) dom.loadingEl.style.display = "none";
  setProgress(0);
}

function setProgress(p) {
  if (!dom.progressBarEl) return;
  const v = Math.max(0, Math.min(100, Number(p) || 0));
  dom.progressBarEl.style.width = `${v}%`;
}

function setStatus(text) {
  if (dom.statusEl) dom.statusEl.textContent = text || "";
}
