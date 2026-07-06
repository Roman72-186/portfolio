// js/app.js
//
// Главный вход приложения.
// Авторизация через portfolio-saas (window.LAB_USER устанавливается сервером).
//
import { initInsetsViewer } from "./insetsViewer.js";
import { INSETS } from "./insetsModels.js";
import { renderGallery } from "./gallery.js";
import { initViewer } from "./viewer.js";
import { initRoomsViewer } from "./roomsViewer.js";
import { MODELS } from "./models.js?v=20260617-simple-insets";
import { ROOMS } from "./roomsModels.js";
import { CONTENT_TREE } from "./content/contentTree.js?v=20260617-simple-insets";
import { NODE_TYPES } from "./content/contentTypes.js";
import { getCardById } from "./content/cardResolver.js";
import { VIEWER_PROFILES } from "./content/viewerProfiles.js";
import { initUniversalViewer } from "./universalViewer.js";
import { configureUniversalRenderer } from "./universalRenderer.js";
const SECTION_FLAGS = {
  arch: true,
  insets: true,
  rooms: false
};
const DEMO_HIDE_EMPTY_BRANCHES = false;
// ✅ Главное меню (как галерея, но карточки-разделы)
const MAIN_MENU = [
  SECTION_FLAGS.arch && {
    id: "section_arch",
    name: "Архитектурные детали",
    desc: "3D + Построение + Видео",
    preview: "textures/preview/preview1.png",
    thumbLetter: "А"
  },

  SECTION_FLAGS.insets && {
    id: "section_insets",
    name: "Врезки",
    desc: "3D + Построение + Видео",
    preview: "textures/preview/preview2.png",
    thumbLetter: "В"
  },

  SECTION_FLAGS.rooms && {
    id: "section_rooms",
    name: "Комнатки",
    desc: "3D",
    preview: "textures/doric/preview.png",
    thumbLetter: "К"
  }
].filter(Boolean);

// ✅ Временный список "врезок" — для теста дублируем мольберт
// ВАЖНО: id оставляем "molbert", чтобы viewer.js смог открыть его как обычную модель.
const TEMP_INSETS = [
  {
    id: "molbert", // тот же id, что в MODELS
    name: "Мольберт (врезка — тест)",
    desc: "Временно задублировано из Архитектурных деталей",
    // берём preview из обычных моделей (найдём ниже при рендере)
    // preview: можно не ставить, будет буква
    thumbLetter: "М"
  }
];

const ALLOWED_CHAT_INSTANCES = [
  "-561659029981423148", // группа 1
  "3659198091171037064", // группа 2
  "1533210958432912681",  // группа 3
  "-8865587346109190339"  // группа 4
];

window.TG_USER = null;
window.TG_CHAT_INSTANCE = null;
window.TG_INIT_DATA = "";

(function initTelegramRuntime() {
  const tg = window.Telegram?.WebApp;
  if (!tg) return;

  try {
    tg.ready();
    tg.expand();

    const unsafe = tg.initDataUnsafe || {};
    window.TG_INIT_DATA = tg.initData || "";
    window.TG_USER = unsafe.user || null;
    window.TG_CHAT_INSTANCE = unsafe.chat_instance || null;

    console.log("TG initDataUnsafe:", unsafe);
    console.log("TG initData RAW:", tg.initData);
    console.log("TG user:", window.TG_USER);
    console.log("TG chat_instance:", window.TG_CHAT_INSTANCE);
  } catch (e) {
    console.warn("Telegram WebApp init warning:", e);
  }
})();

/* ============================================================
   0. ДОСТУП — проверяем window.LAB_USER (устанавливается сервером)
   ============================================================ */

// Показывает красивый экран "Доступ ограничен"
function showLockScreen(message) {
  document.body.innerHTML = `
    <div style="
      display:flex;
      align-items:center;
      justify-content:center;
      flex-direction:column;
      height:100vh;
      padding:24px;
      font-family:system-ui,-apple-system,'Segoe UI',sans-serif;
      background:#111;
      color:#eee;
      text-align:center;
    ">
      <h1 style="font-size:22px;margin-bottom:12px;">Доступ ограничен</h1>
      <p style="opacity:0.8;max-width:360px;">${message}</p>
    </div>
  `;
}

// Проверяем, можно ли запускать приложение
function checkAccess() {
  // 1. Сайтный режим — главный и полностью сохраняется
  if (window.LAB_USER) {
    return true;
  }

  // 2. Telegram fallback
  const tg = window.Telegram?.WebApp;
  const unsafe = tg?.initDataUnsafe || null;
  const ci = window.TG_CHAT_INSTANCE || unsafe?.chat_instance || null;

  if (tg && unsafe && ci && ALLOWED_CHAT_INSTANCES.includes(ci)) {
    return true;
  }

showLockScreen(
  `Доступ только для участников группы.<br><br>` +
  `Войдите через <a href="https://apparchi.ru" style="color:#aaa">apparchi.ru</a> ` +
  `или откройте модуль из закрытой Telegram-группы, где у вас есть доступ.`
);

  return false;
}

/* ============================================================
   1. Водяной знак (использует ID из LAB_USER или TG_USER)
   ============================================================ */

(function () {
  const wm = document.getElementById("watermark");
  const viewer = document.getElementById("viewerWrapper");

  if (!wm || !viewer) return;

  let raf = null;

  const getWatermarkId = () => {
    if (window.LAB_USER?.id) return String(window.LAB_USER.id);
    if (window.TG_USER?.id) return String(window.TG_USER.id);
    return "";
  };

  const rebuild = () => {
    const id = getWatermarkId();
    if (!id) return;

    if (raf) cancelAnimationFrame(raf);

    raf = requestAnimationFrame(() => {
      const rect = viewer.getBoundingClientRect();

      const step = 140;
      const cols = Math.ceil(rect.width / step) + 18;
      const rows = Math.ceil(rect.height / step) + 18;

      const offsetX = -Math.floor(cols / 2) * step;
      const offsetY = -Math.floor(rows / 2) * step;

      let html = "";
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          html += `<span style="left:${offsetX + x * step}px; top:${offsetY + y * step}px">${id}</span>`;
        }
      }

      wm.innerHTML = html;
    });
  };

  rebuild();

  window.addEventListener("resize", rebuild);
  window.addEventListener("orientationchange", () => setTimeout(rebuild, 150));
  document.addEventListener("fullscreenchange", () => setTimeout(rebuild, 150));
  window.visualViewport?.addEventListener("resize", rebuild);
})();

/* ===========================================================
   2. Старт приложения с проверкой доступа
   ============================================================ */

function initApp() {
  // 🔥 1. Проверяем доступ
  if (!checkAccess()) {
    return; // останемся на lockScreen
  }

  document.getElementById("bootLoader")?.remove();

  // 🔥 2. Собираем элементы интерфейса (как было)
  const galleryEl = document.getElementById("gallery");
  const viewerWrapperEl = document.getElementById("viewerWrapper");

const modelLabelEl = document.getElementById("modelLabel");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const backBtn = document.getElementById("backBtn");
const bottomPrevBtn = document.getElementById("bottomPrevBtn");
const bottomNextBtn = document.getElementById("bottomNextBtn");
const bottomBackBtn = document.getElementById("bottomBackBtn");
const tab3dBtn = document.getElementById("tab3d");
const tabSchemeBtn = document.getElementById("tabScheme");
const tabPhotoBtn = document.getElementById("tabPhoto");
const tabVideoBtn = document.getElementById("tabVideo");

  const canvasEl = document.getElementById("canvas");

  const schemeOverlayEl = document.getElementById("schemeOverlay");
  const schemeImgEl = document.getElementById("schemeImage");

const videoOverlayEl = document.getElementById("videoOverlay"); // CHANGED
const videoListEl = document.getElementById("videoList"); // ADDED
const videoEmptyEl = document.getElementById("videoEmpty"); // ADDED

  const loadingEl = document.getElementById("loading");
  const loadingTextEl = document.getElementById("loadingText");
  const progressBarEl = document.getElementById("progressBar");

  const statusEl = document.getElementById("status");
  // ✅ Ползунок прозрачности для режима "Врезки"
const insetOpacityRow = document.getElementById("insetOpacityRow");
const insetOpacitySlider = document.getElementById("insetOpacitySlider");

  const breadcrumbBar = document.getElementById("breadcrumbBar");
const breadcrumbBackBtn = document.getElementById("breadcrumbBackBtn");
  const breadcrumbHomeBtn =
  document.getElementById("breadcrumbHomeBtn");
const breadcrumbSectionLabel = document.getElementById("breadcrumbSectionLabel");
  const brandBlock = document.getElementById("brandBlock");
  const headerCenterTitle = document.getElementById("headerCenterTitle");
  const cabinetBtn = document.getElementById("cabinetBtn");



  window.debugLog = { textContent: "" };
  const isTelegramRuntime = !!window.Telegram?.WebApp;

if (cabinetBtn) {
  cabinetBtn.style.display = isTelegramRuntime ? "none" : "";
}

  // ---------- WATERMARK VISIBILITY ----------
  const watermarkEl = document.getElementById("watermark");
  if (watermarkEl && viewerWrapperEl) {
    const sync = () => {
      watermarkEl.style.display =
        viewerWrapperEl.classList.contains("visible") ? "block" : "none";
    };
    sync();

    const observer = new MutationObserver(sync);
    observer.observe(viewerWrapperEl, {
      attributes: true,
      attributeFilter: ["class"]
    });
  }

  // 🔥 3. Инициализация Viewer
  const viewer = initViewer({
    galleryEl,
    viewerWrapperEl,

    modelLabelEl,
    prevBtn,
    nextBtn,
    backBtn,
bottomPrevBtn,
bottomNextBtn,
bottomBackBtn,

tab3dBtn,
tabSchemeBtn,
tabPhotoBtn,
tabVideoBtn,

    canvasEl,

    schemeOverlayEl,
    schemeImgEl,

videoOverlayEl,   // CHANGED
videoListEl,      // ADDED
videoEmptyEl,     // ADDED

    loadingEl,
    loadingTextEl,
    progressBarEl,
    statusEl,
    onBackToTreeGallery: returnToViewerGallery,
onOpenPrevTreeCard: () => openSiblingCard(-1),
onOpenNextTreeCard: () => openSiblingCard(1)
  });
  const insetViewer = initInsetsViewer({
  galleryEl,
  viewerWrapperEl,
  modelLabelEl,
  prevBtn,
  nextBtn,
  backBtn,
bottomPrevBtn,
bottomNextBtn,
bottomBackBtn,
tab3dBtn,
tabSchemeBtn,
tabPhotoBtn,
tabVideoBtn,
  canvasEl,
  loadingEl,
  loadingTextEl,
  progressBarEl,
  statusEl,
    onBackToTreeGallery: returnToViewerGallery,
onOpenPrevTreeCard: () => openSiblingCard(-1),
onOpenNextTreeCard: () => openSiblingCard(1),
  insetOpacityRow,
  insetOpacitySlider
});
const roomsViewer = initRoomsViewer({
  galleryEl,
  viewerWrapperEl,

  modelLabelEl,
  prevBtn,
  nextBtn,
  backBtn,
  bottomPrevBtn,
  bottomNextBtn,
  bottomBackBtn,

  tab3dBtn,
  tabSchemeBtn,
  tabPhotoBtn,
  tabVideoBtn,

  canvasEl,

  schemeOverlayEl,
  schemeImgEl,

  videoOverlayEl,
  videoListEl,
  videoEmptyEl,

  loadingEl,
  loadingTextEl,
  progressBarEl,
  statusEl,
  onBackToTreeGallery: returnToViewerGallery,
onOpenPrevTreeCard: () => openSiblingCard(-1),
onOpenNextTreeCard: () => openSiblingCard(1)
});

configureUniversalRenderer({
  openArchModel: (modelItem, card) => {
  if (modelItem && typeof modelItem === "object") {
    viewer.openUniversalArch(modelItem, card);
    return;
  }

  viewer.openModelById(modelItem);
},
openInsetModel: (modelItem, card) => {
  if (modelItem && typeof modelItem === "object") {
    insetViewer.openUniversalInset(modelItem, card);
    return;
  }

  insetViewer.openById(modelItem);
},
  openRoomModel: (modelItem, card) => {
  if (modelItem && typeof modelItem === "object") {
    roomsViewer.openUniversalRoom(modelItem, card);
    return;
  }

  roomsViewer.openRoomById(modelItem);
},

  setArchViewMode: (mode) => viewer.setViewMode(mode),
  setInsetViewMode: (mode) => insetViewer.setViewMode(mode),
  setRoomViewMode: (mode) => roomsViewer.setViewMode(mode)
});

const universalViewer = initUniversalViewer({
  blocksRowEl: document.getElementById("universalBlocksRow"),
  blocksEl: document.getElementById("universalBlocks"),
  subblocksRowEl: document.getElementById("universalSubblocksRow"),
  subblocksEl: document.getElementById("universalSubblocks"),

  tab3dBtn: document.getElementById("tab3d"),
  tabSchemeBtn: document.getElementById("tabScheme"),
  tabPhotoBtn: document.getElementById("tabPhoto"),
  tabVideoBtn: document.getElementById("tabVideo")
});
window.universalViewer = universalViewer;


  // 🔥 4. Инициализация галереи
// =======================
// ✅ Новая навигация по contentTree
// =======================

let currentNode = CONTENT_TREE;
let navStack = [];

// Контекст галереи конечных карточек, из которой открыт viewer
let viewerGalleryContext = {
  galleryNodeId: null,
  cards: [],
  currentNodeId: null
};

function setBreadcrumbVisible(visible) {
  if (!breadcrumbBar) return;
  breadcrumbBar.style.display = visible ? "flex" : "none";
}

function setBreadcrumbSection(title) {
  // Название текущего раздела показываем по центру.
  if (headerCenterTitle) {
    headerCenterTitle.textContent = title || "";
  }

  // Слева рядом со стрелкой текст НЕ показываем,
  // чтобы не было ощущения "куда вернусь".
  if (breadcrumbSectionLabel) {
    breadcrumbSectionLabel.textContent = "";
  }
}

function setBrandVisible(visible) {
  if (!brandBlock) return;
  brandBlock.style.display = visible ? "block" : "none";
}

function resetAllViewersToGallery() {
  insetViewer.showGallery();
  roomsViewer.showGallery();
  viewer.showGallery();
}

function nodeToGalleryItem(node) {
  const card = node.type === NODE_TYPES.CARD && node.ref
    ? getCardById(node.ref)
    : null;

  return {
    id: node.id,
    name: node.title,
    desc: node.desc || card?.desc || card?.legacyMeta?.desc || "",
    preview: node.preview || card?.preview || "",
    thumbLetter: node.title ? node.title.charAt(0) : "?",
    isBaseCard: !!node.isBaseCard
  };
}

  function hasVisibleContent(node) {
  if (!node) return false;

  if (node.type === NODE_TYPES.CARD) {
    return Boolean(node.ref && getCardById(node.ref));
  }

  const children = Array.isArray(node.children)
    ? node.children
    : [];

  return children.some(hasVisibleContent);
}

  function findNodeById(node, id) {
  if (!node) return null;
  if (node.id === id) return node;

  const children = Array.isArray(node.children)
    ? node.children
    : [];

  for (const child of children) {
    const found = findNodeById(child, id);
    if (found) return found;
  }

  return null;
}

function getCurrentCardNodes() {
  const children = Array.isArray(currentNode.children)
    ? currentNode.children
    : [];

  return children.filter(
    (node) =>
      node.type === NODE_TYPES.CARD &&
      !node.hidden
  );
}

function isBaseCardNode(node) {
  if (!node) return false;

  const id = String(node.id || "").toLowerCase();
  const title = String(node.title || "").toLowerCase();
  const ref = String(node.ref || "").toLowerCase();

  return (
    id.includes("base") ||
    title.includes("база") ||
    title.includes("базовая") ||
    ref.endsWith("_0") ||
    ref === "arch_0" ||
    ref === "room_0" ||
    ref === "inset_0"
  );
}

function updateViewerNavPanel(node) {
  const isBase = isBaseCardNode(node);
  const cards = viewerGalleryContext.cards || [];
  const canNavigate = !isBase && cards.length > 1;

  const setVisible = (el, visible) => {
    if (!el) return;
    el.style.display = visible ? "" : "none";
  };

  const bottomNav = document.getElementById("viewerBottomNav");

  setVisible(prevBtn, canNavigate);
  setVisible(nextBtn, canNavigate);

  setVisible(bottomPrevBtn, canNavigate);
  setVisible(bottomNextBtn, canNavigate);

  if (bottomNav) {
    bottomNav.style.display = canNavigate ? "flex" : "none";
  }
}

function renderCurrentNode() {
  resetAllViewersToGallery();
  // Возвращаем обычное breadcrumb-поведение
if (breadcrumbBackBtn) {
  breadcrumbBackBtn.onclick = null;
}

  const isRoot = currentNode === CONTENT_TREE;

  setBrandVisible(isRoot);
  setBreadcrumbVisible(!isRoot);
  setBreadcrumbSection(isRoot ? "" : currentNode.title);

let children = Array.isArray(currentNode.children)
  ? currentNode.children
  : [];

children = children.filter((node) => !node.hidden);

if (DEMO_HIDE_EMPTY_BRANCHES) {
  children = children.filter(hasVisibleContent);
}

const isLocationsGallery =
  children.length > 0 &&
  children.every((node) => node.type === NODE_TYPES.CARD) &&
  String(currentNode.id || "").includes("auditorium");

galleryEl.classList.toggle("locations-gallery", isLocationsGallery);
  renderGallery(galleryEl, children.map(nodeToGalleryItem), {
    onSelect: handleNodeSelect
  });
}

function handleNodeSelect(nodeId) {
  const children = Array.isArray(currentNode.children)
    ? currentNode.children
    : [];

  const node = children.find((child) => child.id === nodeId);
  if (!node) return;

  if (node.type === NODE_TYPES.CATEGORY) {
    navStack.push(currentNode);
    currentNode = node;
    renderCurrentNode();
    return;
  }

  if (node.type === NODE_TYPES.CARD) {
    openTreeCard(node);
  }
}

function openTreeCard(node) {
  const card = node.ref ? getCardById(node.ref) : null;
if (card?.viewerProfile === VIEWER_PROFILES.BASE) {
  viewer.openContentCard(card);
}

if (card) {
  universalViewer.openCard(card);
}

viewerGalleryContext = {
  galleryNodeId: currentNode.id,
  cards: getCurrentCardNodes().filter((item) => !isBaseCardNode(item)),
  currentNodeId: node.id
};

  updateViewerNavPanel(node);
  // Во viewer breadcrumb-back больше не должен
// работать как navStack-back
if (breadcrumbBackBtn) {
  breadcrumbBackBtn.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    returnToViewerGallery();
  };
}

  // Заглушка для будущих карточек без viewer / без ref
  if (!card) {
    showPlaceholderScreen(node);
    return;
  }

const isPureUniversalCard =
  card.blocks &&
  !card.legacyOpenRef &&
  !card.legacyMeta;

if (isPureUniversalCard) {
  return;
}

if (card.viewerProfile === VIEWER_PROFILES.ARCH) {
  viewer.openModelById(card.legacyOpenRef || node.ref);
  return;
}

  if (card.viewerProfile === VIEWER_PROFILES.ROOMS) {
    roomsViewer.openRoomById(node.ref);
    return;
  }

  if (card.viewerProfile === VIEWER_PROFILES.INSET) {
    insetViewer.openById(node.ref);
    return;
  }

  showPlaceholderScreen(node);
}

  function showPlaceholderScreen(node) {
  resetAllViewersToGallery();

  setBrandVisible(false);
  setBreadcrumbVisible(true);
  setBreadcrumbSection(node.title || "Раздел готовится");

  galleryEl.classList.remove("hidden");
  viewerWrapperEl.classList.remove("visible");

  galleryEl.innerHTML = `
    <div style="
      grid-column: 1 / -1;
      min-height: 45vh;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 32px 18px;
    ">
      <div style="
        max-width: 420px;
        padding: 22px 18px;
        border-radius: 18px;
        background: rgba(255,255,255,0.045);
        border: 1px solid rgba(255,255,255,0.08);
      ">
        <div style="font-size: 18px; font-weight: 700; margin-bottom: 8px;">
          ${node.title || "Контент готовится"}
        </div>
        <div style="font-size: 14px; line-height: 1.45; color: rgba(255,255,255,0.68);">
          Контент для этого раздела будет добавлен позже.
        </div>
      </div>
    </div>
  `;
}

function returnToViewerGallery() {
  const targetNode = findNodeById(
    CONTENT_TREE,
    viewerGalleryContext.galleryNodeId
  );

  if (targetNode) {
    currentNode = targetNode;
  }

  resetAllViewersToGallery();
  renderCurrentNode();
}

function openSiblingCard(direction) {
  const cards = viewerGalleryContext.cards || [];
  if (!cards.length || !viewerGalleryContext.currentNodeId) return;

  const currentIndex = cards.findIndex((node) => node.id === viewerGalleryContext.currentNodeId);
  if (currentIndex < 0) return;

  const nextIndex = (currentIndex + direction + cards.length) % cards.length;
  const nextNode = cards[nextIndex];

  viewerGalleryContext.currentNodeId = nextNode.id;
  openTreeCard(nextNode);
}

function goBackOneLevel() {
  if (navStack.length === 0) {
    currentNode = CONTENT_TREE;
    renderCurrentNode();
    return;
  }

  currentNode = navStack.pop();
  renderCurrentNode();
}

function goToMainMenu() {
  navStack = [];
  currentNode = CONTENT_TREE;
  renderCurrentNode();
}

// старт — показываем новое главное меню
renderCurrentNode();

function bindTap(el, handler) {
  if (!el) return;

  let lastTap = 0;

  const run = (event) => {
    event.preventDefault();
    event.stopPropagation();

    const now = Date.now();
    if (now - lastTap < 300) return;
    lastTap = now;

    handler();
  };

  el.addEventListener("click", run);
  el.addEventListener("touchend", run, { passive: false });
}

bindTap(breadcrumbBackBtn, () => {
  if (viewerWrapperEl.classList.contains("visible")) {
    returnToViewerGallery();
    return;
  }

  goBackOneLevel();
});

bindTap(breadcrumbHomeBtn, () => {
  goToMainMenu();
});

// кликабельный заголовок → в главное меню
const headerTitle = document.querySelector(".app-title");
headerTitle?.addEventListener("click", () => {
  goToMainMenu();
});


  console.log("App initialized: access granted.");
}

window.addEventListener("DOMContentLoaded", initApp);
