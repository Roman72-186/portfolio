// js/threeViewer.js
// Камера и управление — 100% поведение 8.html.

import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";

let scene = null;
let camera = null;
let renderer = null;

let currentModel = null;
// ===== Rooms flat mode =====
let roomsFlatMode = false;
let sceneLights = [];
let roomsMaterialRestore = [];
let roomCollisionBoxes = [];
let roomTexturesDir = "";
let roomTextureCache = new Map();
let roomDrapObject = null;
// ===== CAD overlay (точки/линии для врезок) =====
let cadGroup = null;
let cadScene = null;
let cadAlpha = 1;
let cadLineMat = null; // LineMaterial для толстых CAD-линий
// ===== Outline / Edges overlay (контуры для врезок) =====
let outlineEnabled = false;
let outlineGroup = null;       // общий контейнер линий/силуэта
let outlineMat = null;         // материал для рёбер
let hullMat = null;            // материал для силуэта (оболочка)
let hullMeshes = [];           // список оболочек (по каждому mesh)
let edgesMeshes = [];          // список edge-линий
let outlineThicknessPx = 1.5;  // как CAD-линии (примерно)
let edgesAngleDeg = 60;        // порог угла для рёбер (автомат)
// ===== Inset blend (70..100) =====
let insetBlendEnabled = false;
let insetBlendFactor = 0;           // 0..1 (0 = только passA, 1 = только passB)
let insetControlledMaterials = [];  // материалы, которые делаем opaque во втором проходе
// ===== Section blend (сечения: статичный mix) =====
let insetSectionBlendFactor = 0.5;   // 0..1 (фиксированный микс для сечений)
let insetSectionMaterials = [];      // материалы сечений, которые делаем opaque в одном из проходов
let outlineExcludedMaterials = [];   // все материалы, которые надо исключить из white outline
// ===== Simple colored edges for section meshes =====
let sectionEdgesScene = null;
let sectionEdgesGroup = null;
let sectionEdgesMeshes = [];
let sectionEdgesAlpha = 0;
let rtSE = null; // render target для цветных контуров сечений

let rtA = null;
let rtB = null;
let rtC = null;
let rtD = null;
let rtN = null; // normals + depth (для контуров)
let rtSamples = 4; // 4 / 2 / 0 — только для RenderTarget'ов inset-blend
let postScene = null;
let postCam = null;
let postQuad = null;

const state = {
  radius: 4.5,
  minRadius: 2.0,
  maxRadius: 12.0,

  rotX: 0.10,
  rotY: 0.00,
  targetRotX: 0.10,
  targetRotY: 0.00,

  target: new THREE.Vector3(0, 0, 0),
};

export function initThree(canvas) {
scene = new THREE.Scene();
scene.background = new THREE.Color(0x050506);
  // Контуры: рисуются в ОСНОВНОЙ сцене (чтобы попадали в inset-blend композит)
outlineGroup = new THREE.Group();
outlineGroup.name = "outline-overlay";
scene.add(outlineGroup);
  outlineGroup.visible = false; // ✅ больше не рисуем EdgesGeometry в сцене

// Линии рёбер (белые)
outlineMat = new THREE.LineBasicMaterial({
  color: 0xffffff,
  transparent: true,
  opacity: 1.0,
  depthTest: true,   // важно: скрытые рёбра не рисуем
  depthWrite: false
});



// CAD overlay: отдельная сцена, рисуется вторым проходом поверх всего
cadScene = new THREE.Scene();
cadGroup = new THREE.Group();
cadGroup.name = "cad-overlay";
cadScene.add(cadGroup);
// Простые цветные контуры сечений: отдельная сцена,
// потом подмешиваем в финальный композит ПЕРЕД белым outline
sectionEdgesScene = new THREE.Scene();
sectionEdgesGroup = new THREE.Group();
sectionEdgesGroup.name = "section-edges-overlay";
sectionEdgesScene.add(sectionEdgesGroup);

  camera = new THREE.PerspectiveCamera(
    40,
    window.innerWidth / window.innerHeight,
    0.1,
    50
  );

  updateCameraPosition();

  renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: false
  });

  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  // CAD thick lines material (pixels)
cadLineMat = new LineMaterial({
  color: 0xdf1a84,
  linewidth: 1.5,      
  transparent: true,
  opacity: cadAlpha,
  depthTest: false,
  depthWrite: false
});

// resolution обязателен для LineMaterial
cadLineMat.resolution.set(
  renderer.domElement.width,
  renderer.domElement.height
);
  renderer.shadowMap.enabled = true;

  setupLights();
  initControls(canvas);

renderer.setAnimationLoop(() => {
  state.rotX += (state.targetRotX - state.rotX) * 0.22;
  state.rotY += (state.targetRotY - state.rotY) * 0.22;

  updateCameraPosition();
    // обновляем толщину силуэта под текущий zoom (примерно px)


  if (insetBlendEnabled) {
    renderWithInsetBlend();  // ✅ всегда через композит, даже при 0 и 1
  } else {
    renderer.render(scene, camera);
  }

// ✅ CAD поверх финального кадра (НЕ очищаем экран повторно)
if (document.body.classList.contains("inset-mode") && cadScene && cadGroup && cadGroup.children.length) {
  const prevAutoClear = renderer.autoClear;
  renderer.autoClear = false;

  renderer.clearDepth();           // сбрасываем depth, чтобы CAD был поверх
  renderer.render(cadScene, camera);

  renderer.autoClear = prevAutoClear;
}
});
}

export function setModel(root) {
  if (currentModel) {
    scene.remove(currentModel);
  }

  currentModel = root;
  scene.add(currentModel);

  state.targetRotX = 0.10;
  state.targetRotY = 0.00;
  state.target.set(0, 0, 0);

  if (roomsFlatMode) {
    setupRoomCameraHelpers(root);
    applyRoomsFlatMaterials(root);
  } else {
    fitCameraToModel(root);
  }
}

export function clearModel(options = {}) {
  const { keepInsetPipeline = false } = options;

  if (!scene) return;

  if (currentModel) {
    scene.remove(currentModel);
    currentModel = null;
  }
    if (roomsFlatMode) {
    setRoomsFlatMode(false, null);
  }

  clearOutlines();
  clearCadOverlay();
  clearSectionEdgesOverlay();

  setCadAlpha(0);
  setSectionEdgesAlpha(0);

  setInsetBlendState(0, []);
  setInsetSectionBlendState(0.5, []);
  setOutlineExcludedMaterials([]);

  if (!keepInsetPipeline) {
    setOutlineEnabled(false);
    setInsetBlendEnabled(false);
  }
}

function rebuildOutlinesForModel(root) {
  clearOutlines();
  if (!outlineGroup || !root) return;

  // Пропускаем “служебные” объекты (точки a,b,c,d и т.п.)
  const pointNameRe = /^[a-z](\d+)?$/;

  root.traverse((obj) => {
    if (!obj.isMesh) return;
    const nm = String(obj.name || "").trim();

    // пропускаем точки/хелперы
    if (pointNameRe.test(nm)) return;

    const geom = obj.geometry;
    if (!geom) return;


    // 2) Рёбра: EdgesGeometry по углу
    const edgesGeom = new THREE.EdgesGeometry(geom, edgesAngleDeg);
    const edges = new THREE.LineSegments(edgesGeom, outlineMat);
    edges.matrixAutoUpdate = false;
    edges.renderOrder = 1501;         // чуть выше оболочки
    edges.onBeforeRender = () => {
      edges.matrixWorld.copy(obj.matrixWorld);
    };
    outlineGroup.add(edges);
    edgesMeshes.push(edges);
  });
}

function fitCameraToModel(root) {
  const box = new THREE.Box3().setFromObject(root);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const radius = sphere.radius || 1;

  const fovRad = camera.fov * Math.PI / 180;
  let dist = radius / Math.sin(fovRad / 2);

  const isMobile = /Android|webOS|iPhone|iPad|iPod/i.test(navigator.userAgent);
  if (isMobile) dist *= 1.55;

  state.radius = dist;
  state.minRadius = dist * 0.4;
  state.maxRadius = dist * 6.0;
}

function findHelperObject(root, name) {
  if (!root) return null;

  const wanted = String(name).trim().toLowerCase();
  let found = null;

  root.traverse((obj) => {
    if (found) return;

    const objName = String(obj.name || "").trim().toLowerCase();
    if (objName === wanted) {
      found = obj;
    }
  });

  return found;
}

function isRoomHelperObjectName(name) {
  const n = String(name || "").trim().toLowerCase();

  return (
    n === "point" ||
    n === "cam" ||
    n === "box" ||
    n.startsWith("box_")
  );
}

function hideRoomHelpers(root) {
  if (!root) return;

  root.traverse((obj) => {
    if (isRoomHelperObjectName(obj.name)) {
      obj.visible = false;
    }
  });
}

function collectRoomCollisionBoxes(root) {
  roomCollisionBoxes = [];
  if (!root) return;

  root.updateWorldMatrix(true, true);

  root.traverse((obj) => {
    const name = String(obj.name || "").trim().toLowerCase();

    if (name === "box" || name.startsWith("box_")) {
      obj.updateWorldMatrix(true, false);

      const box = new THREE.Box3().setFromObject(obj);

      if (!box.isEmpty()) {
        roomCollisionBoxes.push(box);
      }
    }
  });
}

function constrainCameraToRoomBoxes(position) {
  if (!roomsFlatMode) return;
  if (!roomCollisionBoxes.length) return;

  for (const box of roomCollisionBoxes) {
    if (box.containsPoint(position)) {
      return;
    }
  }

  let bestPoint = null;
  let bestDist = Infinity;

  for (const box of roomCollisionBoxes) {
    const p = new THREE.Vector3();
    box.clampPoint(position, p);

    const d = p.distanceToSquared(position);

    if (d < bestDist) {
      bestDist = d;
      bestPoint = p;
    }
  }

  if (bestPoint) {
    position.copy(bestPoint);
  }
}

function syncRoomOrbitFromCameraPosition() {
  if (!roomsFlatMode) return;

  const offset = camera.position.clone().sub(state.target);
  const radius = Math.max(offset.length(), 0.1);

  state.radius = THREE.MathUtils.clamp(
    radius,
    state.minRadius,
    state.maxRadius
  );

  state.rotX = Math.asin(
    THREE.MathUtils.clamp(offset.y / state.radius, -1, 1)
  );

  state.rotY = Math.atan2(offset.x, offset.z);

  state.targetRotX = state.rotX;
  state.targetRotY = state.rotY;
}

function setupRoomCameraHelpers(root) {
  hideRoomHelpers(root);
  collectRoomCollisionBoxes(root);

  const point = findHelperObject(root, "point");
  const cam = findHelperObject(root, "cam");

  if (point) {
    point.updateWorldMatrix(true, false);
    point.getWorldPosition(state.target);
  } else {
    state.target.set(0, 0, 0);
  }

  if (cam) {
    cam.updateWorldMatrix(true, false);

    const camWorldPos = new THREE.Vector3();
    cam.getWorldPosition(camWorldPos);

    const offset = camWorldPos.clone().sub(state.target);
    const radius = Math.max(offset.length(), 0.1);

    state.radius = radius;
state.minRadius = radius * 0.45;
state.maxRadius = radius * 2.5;

    state.targetRotX = Math.asin(
      THREE.MathUtils.clamp(offset.y / radius, -1, 1)
    );

    state.targetRotY = Math.atan2(offset.x, offset.z);

    state.rotX = state.targetRotX;
    state.rotY = state.targetRotY;
  } else {
    fitCameraToModel(root);
  }
}

function updateCameraPosition() {
  const r = state.radius;

  const x = r * Math.sin(state.rotY) * Math.cos(state.rotX);
  const z = r * Math.cos(state.rotY) * Math.cos(state.rotX);
  const y = r * Math.sin(state.rotX);

  const tx = roomsFlatMode ? state.target.x : 0;
  const ty = roomsFlatMode ? state.target.y : 0;
  const tz = roomsFlatMode ? state.target.z : 0;

camera.position.set(tx + x, ty + y, tz + z);

if (roomsFlatMode) {
  const before = camera.position.clone();

  constrainCameraToRoomBoxes(camera.position);

  if (!camera.position.equals(before)) {
    syncRoomOrbitFromCameraPosition();
  }
}

camera.lookAt(tx, ty, tz);
}

export function resize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();

  renderer.setSize(window.innerWidth, window.innerHeight);

  if (cadLineMat) {
    cadLineMat.resolution.set(
      renderer.domElement.width,
      renderer.domElement.height
    );
  }

  for (const e of sectionEdgesMeshes) {
    if (e.material && e.material.resolution) {
      e.material.resolution.set(
        renderer.domElement.width,
        renderer.domElement.height
      );
    }
  }

  // чтобы RenderTarget пересоздались под новый размер
  if (insetBlendEnabled) {
    ensureBlendResources();
  }
}

// Включаем/выключаем режим смешивания (используется ТОЛЬКО во врезках)
export function setInsetBlendEnabled(enabled) {
  insetBlendEnabled = !!enabled;
}

export function setInsetRtSamples(samples) {
  const s = Number(samples);
  rtSamples = (s === 0 || s === 2 || s === 4) ? s : 4;

  // чтобы изменения применились сразу — пересоздать RT под текущий размер
  if (insetBlendEnabled) ensureBlendResources();
}
// Обновляем фактор смешивания и список материалов, которые должны стать opaque во 2-м проходе
export function setInsetBlendState(factor01, controlledMats) {
  insetBlendFactor = THREE.MathUtils.clamp(Number(factor01) || 0, 0, 1);
  insetControlledMaterials = Array.isArray(controlledMats) ? controlledMats : [];
}

// Обновляем статичный микс сечений и список материалов-сечений
export function setInsetSectionBlendState(factor01, sectionMats) {
  insetSectionBlendFactor = THREE.MathUtils.clamp(Number(factor01) || 0, 0, 1);
  insetSectionMaterials = Array.isArray(sectionMats) ? sectionMats : [];
}

export function setOutlineExcludedMaterials(materials) {
  outlineExcludedMaterials = Array.isArray(materials) ? materials : [];
}

// ===============================
// CAD overlay API (для врезок)
// ===============================

export function setOutlineEnabled(enabled) {
  outlineEnabled = !!enabled; // влияет только на post-обводку (uOutlineOn)
}

export function setOutlineStyle({ thicknessPx, edgesAngle } = {}) {
  if (typeof thicknessPx === "number") outlineThicknessPx = thicknessPx;
  if (typeof edgesAngle === "number") edgesAngleDeg = edgesAngle;
}

export function setSectionEdgesOverlay(root, sectionMaterialNames = [], materialColors = {}) {
  buildSectionEdges(root, sectionMaterialNames, materialColors);
}

export function clearSectionEdgesOverlay() {
  clearSectionEdges();
}

export function setSectionEdgesAlpha(alpha) {
  sectionEdgesAlpha = Math.max(0, Math.min(1, Number(alpha) || 0));

  for (const obj of sectionEdgesMeshes) {
    if (!obj || !obj.material) continue;

    obj.material.transparent = true;
    obj.material.opacity = sectionEdgesAlpha;
    obj.material.needsUpdate = true;
  }
}

function clearOutlines() {
  if (!outlineGroup) return;

  for (const e of edgesMeshes) {
    e.geometry?.dispose?.();
  }

  edgesMeshes = [];
  outlineGroup.clear();
}

function clearSectionEdges() {
  if (!sectionEdgesGroup) return;

  for (const e of sectionEdgesMeshes) {
    e.geometry?.dispose?.();
    e.material?.dispose?.();
  }

  sectionEdgesMeshes = [];
  sectionEdgesGroup.clear();
  sectionEdgesAlpha = 0;
}

function buildSectionSubGeometryByMaterialName(obj, materialName) {
  const geom = obj?.geometry;
  if (!geom) return null;

  const posAttr = geom.getAttribute("position");
  if (!posAttr) return null;

  const materials = Array.isArray(obj.material) ? obj.material : [obj.material];

  const groups =
    Array.isArray(geom.groups) && geom.groups.length
      ? geom.groups
      : [{
          start: 0,
          count: geom.index ? geom.index.count : posAttr.count,
          materialIndex: 0
        }];

  const pickedVertexIndices = [];

  for (const group of groups) {
    const mat =
      materials[group.materialIndex] ||
      materials[0] ||
      null;

    if (!mat) continue;
    if (String(mat.name) !== String(materialName)) continue;

    if (geom.index) {
      const indexArray = geom.index.array;
      const end = group.start + group.count;

      for (let i = group.start; i < end; i++) {
        pickedVertexIndices.push(indexArray[i]);
      }
    } else {
      const end = group.start + group.count;

      for (let i = group.start; i < end; i++) {
        pickedVertexIndices.push(i);
      }
    }
  }

  if (!pickedVertexIndices.length) return null;

  const outPos = new Float32Array(pickedVertexIndices.length * 3);

  for (let i = 0; i < pickedVertexIndices.length; i++) {
    const vi = pickedVertexIndices[i];
    outPos[i * 3 + 0] = posAttr.getX(vi);
    outPos[i * 3 + 1] = posAttr.getY(vi);
    outPos[i * 3 + 2] = posAttr.getZ(vi);
  }

  const outGeom = new THREE.BufferGeometry();
  outGeom.setAttribute("position", new THREE.BufferAttribute(outPos, 3));

  return outGeom;
}

function buildSectionEdges(root, sectionMaterialNames = [], materialColors = {}) {
  clearSectionEdges();

  if (!root || !sectionEdgesGroup) return;
  if (!Array.isArray(sectionMaterialNames) || !sectionMaterialNames.length) return;

  const pointNameRe = /^[a-z](\d+)?$/;

  root.traverse((obj) => {
    if (!obj.isMesh) return;

    const nm = String(obj.name || "").trim();
    if (pointNameRe.test(nm)) return;

    for (const matName of sectionMaterialNames) {
      const subGeom = buildSectionSubGeometryByMaterialName(obj, matName);
      if (!subGeom) continue;

      const edgesGeom = new THREE.EdgesGeometry(subGeom, 1);
      subGeom.dispose();

      const pos = edgesGeom.getAttribute("position");
      if (!pos || pos.count === 0) {
        edgesGeom.dispose();
        continue;
      }

      const positions = Array.from(pos.array);
      edgesGeom.dispose();

      const wideGeom = new LineSegmentsGeometry();
      wideGeom.setPositions(positions);

      const colorValue =
        (materialColors && materialColors[String(matName)]) ||
        "#ffffff";

const lineMat = new LineMaterial({
  color: new THREE.Color(colorValue),
  linewidth: 2.0,
  transparent: true,
  opacity: sectionEdgesAlpha,
  depthTest: false,
  depthWrite: false,
  dashed: false
});

      lineMat.resolution.set(
        renderer.domElement.width,
        renderer.domElement.height
      );

      const lines = new LineSegments2(wideGeom, lineMat);
      lines.matrixAutoUpdate = false;
      lines.frustumCulled = false;
      lines.renderOrder = 1400;

      lines.matrix.copy(obj.matrixWorld);
      lines.matrixWorld.copy(obj.matrixWorld);

      lines.onBeforeRender = () => {
        lines.matrix.copy(obj.matrixWorld);
        lines.matrixWorld.copy(obj.matrixWorld);
      };

      sectionEdgesGroup.add(lines);
      sectionEdgesMeshes.push(lines);
    }
  });
}

export function clearCadOverlay() {
  if (!cadGroup) return;

  for (const child of cadGroup.children) {
    child.geometry?.dispose?.();

    const mat = child.material;

    if (Array.isArray(mat)) {
      for (const m of mat) {
        if (!m) continue;
        if (m === cadLineMat) continue; // общий CAD-материал НЕ диспозим
        m.dispose?.();
      }
    } else if (mat) {
      if (mat !== cadLineMat) {
        mat.dispose?.();
      }
    }
  }

  cadGroup.clear();
}

export function setCadAlpha(alpha) {
  cadAlpha = Math.max(0, Math.min(1, alpha));
if (cadLineMat) cadLineMat.opacity = cadAlpha;
  if (!cadGroup) return;

  cadGroup.traverse((obj) => {
    if (!obj.material) return;

    obj.material.transparent = true;
    obj.material.opacity = cadAlpha;
    obj.material.needsUpdate = true;
  });
}

export function setCadOverlay(spec) {
  clearCadOverlay();
  if (!cadGroup) return;
  if (!spec || !Array.isArray(spec.points) || spec.points.length === 0) return;

  // карта точек id -> Vector3
  const pointMap = new Map();
  for (const p of spec.points) {
    pointMap.set(String(p.id), new THREE.Vector3(p.x, p.y, p.z));
  }

  // ---- точки ----
  const pos = new Float32Array(spec.points.length * 3);
  spec.points.forEach((p, i) => {
    pos[i * 3 + 0] = p.x;
    pos[i * 3 + 1] = p.y;
    pos[i * 3 + 2] = p.z;
  });

  const pointsGeo = new THREE.BufferGeometry();
  pointsGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));

const pointsMat = new THREE.PointsMaterial({
  color: 0xdf1a84,
  size: 8,
  sizeAttenuation: false,
  depthTest: false,
  depthWrite: false,
  transparent: true,
  opacity: cadAlpha
});
  const pointsObj = new THREE.Points(pointsGeo, pointsMat);
  pointsObj.renderOrder = 2000;
  cadGroup.add(pointsObj);

  // ---- линии ----
  if (Array.isArray(spec.lines) && spec.lines.length) {
    const linePos = [];

    for (const seg of spec.lines) {
      const a = pointMap.get(String(seg[0]));
      const b = pointMap.get(String(seg[1]));
      if (!a || !b) continue;

      linePos.push(a.x, a.y, a.z, b.x, b.y, b.z);
    }

    if (linePos.length) {
      const lineGeo = new THREE.BufferGeometry();
      lineGeo.setAttribute(
        "position",
        new THREE.BufferAttribute(new Float32Array(linePos), 3)
      );

// Thick CAD lines via LineSegments2 (stable linewidth on mobile)
const segGeom = new LineSegmentsGeometry();
segGeom.setPositions(new Float32Array(linePos)); // linePos уже собран выше

// на всякий случай, если initThree ещё не успел создать материал
const mat = cadLineMat || new LineMaterial({
  color: 0xdf1a84,
  linewidth: 1.5,
  transparent: true,
  opacity: cadAlpha,
  depthTest: false,
  depthWrite: false
});

// если создали mat здесь (fallback) — нужен resolution
if (!cadLineMat && renderer) {
  mat.resolution.set(renderer.domElement.width, renderer.domElement.height);
}

const linesObj = new LineSegments2(segGeom, mat);
linesObj.renderOrder = 1999;
cadGroup.add(linesObj);
    }
  }
}

function ensureBlendResources() {
  if (!renderer) return;

const size = new THREE.Vector2();
renderer.getDrawingBufferSize(size);  // ✅ ВАЖНО: именно drawing buffer, а не CSS size
const w = Math.max(1, Math.floor(size.x));
const h = Math.max(1, Math.floor(size.y));

  // RT параметры (чтобы не было странного пересвета)
  const params = {
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
    format: THREE.RGBAFormat,
    depthBuffer: true,
    stencilBuffer: false,
  };

  if (!rtA || rtA.width !== w || rtA.height !== h) {
    rtA?.dispose?.();
    rtA = new THREE.WebGLRenderTarget(w, h, params);
  }

  if (!rtB || rtB.width !== w || rtB.height !== h) {
    rtB?.dispose?.();
    rtB = new THREE.WebGLRenderTarget(w, h, params);
  }
  if (!rtC || rtC.width !== w || rtC.height !== h) {
  rtC?.dispose?.();
  rtC = new THREE.WebGLRenderTarget(w, h, params);
}

if (!rtD || rtD.width !== w || rtD.height !== h) {
  rtD?.dispose?.();
  rtD = new THREE.WebGLRenderTarget(w, h, params);
}
  if (!rtSE || rtSE.width !== w || rtSE.height !== h) {
  rtSE?.dispose?.();
  rtSE = new THREE.WebGLRenderTarget(w, h, {
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
    format: THREE.RGBAFormat,
    depthBuffer: false,
    stencilBuffer: false
  });
}

  // ===== Normals RT (для контуров) =====
if (!rtN || rtN.width !== w || rtN.height !== h) {
  rtN?.dispose?.();
  rtN = new THREE.WebGLRenderTarget(w, h, {
    minFilter: THREE.LinearFilter,
    magFilter: THREE.LinearFilter,
    format: THREE.RGBAFormat,
    depthBuffer: true,
    stencilBuffer: false,
  });

  // depthTexture нужен, чтобы в шейдере понимать "что ближе"
  rtN.depthTexture = new THREE.DepthTexture(w, h);
  rtN.depthTexture.type = THREE.UnsignedShortType;
}
const isAndroid = /Android/i.test(navigator.userAgent);

// На Android MSAA в render targets ломает depth и даёт 1282
const samples = isAndroid ? 0 : rtSamples;

rtA.samples = samples;
rtB.samples = samples;
rtC.samples = samples;
rtD.samples = samples;

if (rtN) {
  rtN.samples = samples;
}

  if (!postScene) {
    postScene = new THREE.Scene();
    postCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

    const mat = new THREE.ShaderMaterial({
uniforms: {
  t00: { value: null }, // body semi + sec semi
  t10: { value: null }, // body opaque + sec semi
  t01: { value: null }, // body semi + sec opaque
  t11: { value: null }, // body opaque + sec opaque
  tSE: { value: null }, // цветные контуры сечений
  tN: { value: null },          // normal texture
tDepth: { value: null },      // depth texture (rtN.depthTexture)
uTexel: { value: new THREE.Vector2(1 / 1024, 1 / 1024) }, // будет обновляться
uOutlineOn: { value: 0.0 },   // 0/1
uDepthK: { value: 1.0 },      // чувствительность по depth
uNormK: { value: 1.0 },       // чувствительность по нормалям
  uBodyMix: { value: 0 },
  uSecMix: { value: 0.5 },
},
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = vec4(position.xy, 0.0, 1.0);
        }
      `,
fragmentShader: `
precision highp float;

varying vec2 vUv;

uniform sampler2D t00;
uniform sampler2D t10;
uniform sampler2D t01;
uniform sampler2D t11;
uniform sampler2D tSE;
uniform sampler2D tN;
uniform sampler2D tDepth;
uniform vec2 uTexel;
uniform float uOutlineOn;
uniform float uDepthK;
uniform float uNormK;
float edgeDepth(vec2 uv) {
  float d = texture2D(tDepth, uv).r;
  float dR = texture2D(tDepth, uv + vec2(uTexel.x, 0.0)).r;
  float dU = texture2D(tDepth, uv + vec2(0.0, uTexel.y)).r;
  return max(abs(d - dR), abs(d - dU));
}

float edgeNormal(vec2 uv) {
  vec3 n  = texture2D(tN, uv).xyz * 2.0 - 1.0;
  vec3 nR = texture2D(tN, uv + vec2(uTexel.x, 0.0)).xyz * 2.0 - 1.0;
  vec3 nU = texture2D(tN, uv + vec2(0.0, uTexel.y)).xyz * 2.0 - 1.0;
  float a = max(length(n - nR), length(n - nU));
  return a;
}

uniform float uBodyMix;
uniform float uSecMix;

// Простой вывод в sRGB (как у тебя)
vec3 toSRGB(vec3 c) {
  return pow(max(c, 0.0), vec3(1.0 / 2.2));
}

void main() {
  vec4 c00 = texture2D(t00, vUv);
  vec4 c10 = texture2D(t10, vUv);
  vec4 c01 = texture2D(t01, vUv);
  vec4 c11 = texture2D(t11, vUv);

  float b = clamp(uBodyMix, 0.0, 1.0);
  float s = clamp(uSecMix, 0.0, 1.0);

  // сначала микс по телу
  vec4 semiSec = mix(c00, c10, b);
  vec4 opaSec  = mix(c01, c11, b);

  // потом микс по сечениям
  vec4 outC = mix(semiSec, opaSec, s);
  vec3 col = outC.rgb;

  // Сначала подмешиваем цветные простые контуры сечений
  vec4 secEdge = texture2D(tSE, vUv);
  col = mix(col, secEdge.rgb, clamp(secEdge.a, 0.0, 1.0));

if (uOutlineOn > 0.5) {
  float ed = edgeDepth(vUv) * uDepthK;
  float en = edgeNormal(vUv) * uNormK;

  // пороги подбираем мягко (без резких скачков)
  float e = max(
    smoothstep(0.002, 0.01, ed),
    smoothstep(0.10, 0.35, en)
  );

  // белая линия
  col = mix(col, vec3(1.0), e);
}

gl_FragColor = vec4(toSRGB(col), outC.a);
}
`,
      depthTest: false,
      depthWrite: false,
      toneMapped: false,
    });

    const geo = new THREE.PlaneGeometry(2, 2);
    postQuad = new THREE.Mesh(geo, mat);
    postScene.add(postQuad);
  }
}

function renderWithInsetBlend() {
  if (!renderer || !camera) return;

  ensureBlendResources();

  // --- helpers: сохранить/применить override ---
  function saveStates(mats) {
    const saved = [];
    for (const m of mats) {
      if (!m) continue;
      saved.push({
        m,
        transparent: m.transparent,
        opacity: m.opacity,
        depthWrite: m.depthWrite,
        depthTest: m.depthTest,
      });
    }
    return saved;
  }

  function applyOpaque(mats) {
    for (const m of mats) {
      if (!m) continue;
      m.transparent = false;
      m.opacity = 1;
      m.depthWrite = true;
      m.depthTest = true;
      m.needsUpdate = true;
    }
  }

  function restoreStates(saved) {
    for (const s of saved) {
      const m = s.m;
      m.transparent = s.transparent;
      m.opacity = s.opacity;
      m.depthWrite = s.depthWrite;
      m.depthTest = s.depthTest;
      m.needsUpdate = true;
    }
  }

  // Сохраняем состояния обоих групп
  const savedBody = saveStates(insetControlledMaterials);
  const savedSec  = saveStates(insetSectionMaterials);

  // 1) T00: body как есть + sec как есть (semi)
  renderer.setRenderTarget(rtA);
  renderer.clear(true, true, true);
  renderer.render(scene, camera);

  // 2) T10: body opaque + sec semi
  applyOpaque(insetControlledMaterials);
  renderer.setRenderTarget(rtB);
  renderer.clear(true, true, true);
  renderer.render(scene, camera);
  restoreStates(savedBody);

  // 3) T01: body semi + sec opaque
  applyOpaque(insetSectionMaterials);
  renderer.setRenderTarget(rtC);
  renderer.clear(true, true, true);
  renderer.render(scene, camera);
  restoreStates(savedSec);

// 4) T11: body opaque + sec opaque
applyOpaque(insetControlledMaterials);
applyOpaque(insetSectionMaterials);
renderer.setRenderTarget(rtD);
renderer.clear(true, true, true);
renderer.render(scene, camera);

// ===== Простые цветные контуры сечений =====
{
  const prevClearColor = new THREE.Color();
  renderer.getClearColor(prevClearColor);
  const prevClearAlpha = renderer.getClearAlpha();

  renderer.setRenderTarget(rtSE);
  renderer.setClearColor(0x000000, 0);
  renderer.clear(true, true, true);

  if (sectionEdgesScene && sectionEdgesGroup && sectionEdgesGroup.children.length) {
    renderer.render(sectionEdgesScene, camera);
  }

  renderer.setClearColor(prevClearColor, prevClearAlpha);
}

// ===== Normals + Depth pass (для контуров) =====
if (outlineEnabled) {

  // ✅ временно прячем все сечения, чтобы их НЕ было в normals-pass
  // и белый контур не появлялся на их пересечениях
  const hiddenSections = [];
  if (currentModel && Array.isArray(outlineExcludedMaterials) && outlineExcludedMaterials.length) {
    const secSet = new Set(outlineExcludedMaterials);

    currentModel.traverse((obj) => {
      if (!obj.isMesh) return;

      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      const isSectionMesh = mats.some((m) => m && secSet.has(m));

      if (isSectionMesh && obj.visible) {
        hiddenSections.push(obj);
        obj.visible = false;
      }
    });
  }

  const prevOverride = scene.overrideMaterial;
  scene.overrideMaterial = new THREE.MeshNormalMaterial();

  renderer.setRenderTarget(rtN);
  renderer.clear(true, true, true);
  renderer.render(scene, camera);

  scene.overrideMaterial = prevOverride;

  // ✅ возвращаем видимость сечений обратно
  for (const obj of hiddenSections) obj.visible = true;
}

// Возвращаем всё как было
restoreStates(savedBody);
restoreStates(savedSec);

  // 5) Финальный вывод (2 независимых микса)
  renderer.setRenderTarget(null);

  postQuad.material.uniforms.t00.value = rtA.texture;
  postQuad.material.uniforms.t10.value = rtB.texture;
  postQuad.material.uniforms.t01.value = rtC.texture;
  postQuad.material.uniforms.t11.value = rtD.texture;
  postQuad.material.uniforms.tSE.value = rtSE ? rtSE.texture : null;
  postQuad.material.uniforms.uBodyMix.value = insetBlendFactor;
  postQuad.material.uniforms.uSecMix.value = insetSectionBlendFactor;
  // ===== Передаём normals и depth в шейдер =====
// ===== Передаём normals/depth в шейдер только когда контуры включены =====
postQuad.material.uniforms.uOutlineOn.value = outlineEnabled ? 1.0 : 0.0;

if (outlineEnabled) {
  postQuad.material.uniforms.tN.value = rtN.texture;
  postQuad.material.uniforms.tDepth.value = rtN.depthTexture;
  const k = Math.max(0.5, Number(outlineThicknessPx) || 1);
postQuad.material.uniforms.uTexel.value.set(k / rtN.width, k / rtN.height);
} else {
  // чтобы шейдер случайно не читал мусор
  postQuad.material.uniforms.tN.value = null;
  postQuad.material.uniforms.tDepth.value = null;
}

  renderer.clear(true, true, true);
  renderer.render(postScene, postCam);
}
function setupLights() {
  sceneLights = [];

  const zenith = new THREE.DirectionalLight(0xf5f8ff, 0.0);
  zenith.position.set(0, 11, 2);
  scene.add(zenith);
  sceneLights.push(zenith);

  const key = new THREE.DirectionalLight(0xffc4a0, 1.85);
  key.position.set(5.5, 6.0, 3.5);
  scene.add(key);
  sceneLights.push(key);

  const fill = new THREE.DirectionalLight(0xcad8ff, 0.35);
  fill.position.set(-7, 3.5, 2);
  scene.add(fill);
  sceneLights.push(fill);

  const rim = new THREE.DirectionalLight(0xffffff, 0.5);
  rim.position.set(-3.5, 5, -7.5);
  scene.add(rim);
  sceneLights.push(rim);

  const coldRim = new THREE.DirectionalLight(0xd8e4ff, 0.1);
  coldRim.position.set(2.5, 3.5, -5);
  scene.add(coldRim);
  sceneLights.push(coldRim);

  const ambient = new THREE.AmbientLight(0xffffff, 0.04);
  scene.add(ambient);
  sceneLights.push(ambient);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x0a0a0a, 0.07);
  scene.add(hemi);
  sceneLights.push(hemi);
}

export function setInsetNeutralLighting(enabled) {
  if (!scene) return;

  const k = 0.5; // общий коэффициент яркости для врезок

  scene.traverse((obj) => {
    if (!obj.isLight) return;

    if (enabled) {
      obj.color.set(0xffffff);

      if (obj.intensity === 1.85) obj.intensity = 1.85 * k; // key
      else if (obj.intensity === 0.35) obj.intensity = 0.35 * k; // fill
      else if (obj.intensity === 0.5) obj.intensity = 0.5 * k; // rim
      else if (obj.intensity === 0.1) obj.intensity = 0.1 * k; // coldRim
      else if (obj.intensity === 0.04) obj.intensity = 0.04 * k; // ambient
      else if (obj.intensity === 0.07) obj.intensity = 0.07 * k; // hemisphere
    } else {
      // возвращаем исходные значения
      if (obj.intensity === 1.85 * k) {
        obj.color.set(0xffc4a0);
        obj.intensity = 1.85;
      }
      else if (obj.intensity === 0.35 * k) {
        obj.color.set(0xcad8ff);
        obj.intensity = 0.35;
      }
      else if (obj.intensity === 0.5 * k) {
        obj.color.set(0xffffff);
        obj.intensity = 0.5;
      }
      else if (obj.intensity === 0.1 * k) {
        obj.color.set(0xd8e4ff);
        obj.intensity = 0.1;
      }
      else if (obj.intensity === 0.04 * k) {
        obj.intensity = 0.04;
      }
      else if (obj.intensity === 0.07 * k) {
        obj.intensity = 0.07;
      }
    }
  });
}

function clearRoomsMaterialOverrides() {
  if (!roomsMaterialRestore.length) return;

  for (const entry of roomsMaterialRestore) {
    if (!entry?.mesh) continue;
    entry.mesh.material = entry.material;
  }

  roomsMaterialRestore = [];
}

function normalizeRoomTextureUrl(url) {
  if (!url) return "";

  const s = String(url);

  const isAbsolute =
    /^https?:\/\//i.test(s) ||
    s.startsWith("/") ||
    s.startsWith("data:");

  return isAbsolute
    ? s
    : `https://api.apparchi.ru/?path=${encodeURIComponent(s)}`;
}

function getRoomObjectKey(obj) {
  let cur = obj;

  while (cur) {
    const name = String(cur.name || "").trim();
    const lower = name.toLowerCase();

    if (/^\d+$/.test(name)) {
      return name;
    }

    if (lower === "drap") {
      return "drap";
    }

    cur = cur.parent;
  }

  return "";
}

function isRoomDrapObject(obj) {
  let cur = obj;

  while (cur) {
    const name = String(cur.name || "").trim().toLowerCase();

    if (name === "drap") {
      return true;
    }

    cur = cur.parent;
  }

  return false;
}

function getRoomTextureForKey(key) {
  if (!roomTexturesDir || !key) return null;

  if (roomTextureCache.has(key)) {
    return roomTextureCache.get(key);
  }

  const cleanDir = roomTexturesDir.endsWith("/")
    ? roomTexturesDir
    : `${roomTexturesDir}/`;

  const url = normalizeRoomTextureUrl(`${cleanDir}${key}.jpg`);

console.log("[rooms texture] loading:", key, url);

const tex = new THREE.TextureLoader().load(
  url,
  () => {
    console.log("[rooms texture] loaded:", key, url);
  },
  undefined,
  (err) => {
    console.warn("[rooms texture] failed:", key, url, err);
  }
);

  tex.flipY = false;
  tex.colorSpace = THREE.SRGBColorSpace;

  roomTextureCache.set(key, tex);

  return tex;
}

function isRoomBlackObject(obj) {
  let cur = obj;

  while (cur) {
    const name = String(cur.name || "").trim().toLowerCase();
    if (name === "black") return true;
    cur = cur.parent;
  }

  return false;
}

function applyRoomsFlatMaterials(root) {
  clearRoomsMaterialOverrides();
  roomDrapObject = null;

  if (!root) return;

  root.traverse((obj) => {
    if (!obj.isMesh) return;

    const originalMat = obj.material;
    if (!originalMat) return;

    roomsMaterialRestore.push({ mesh: obj, material: originalMat });

    if (isRoomDrapObject(obj)) {
      roomDrapObject = obj;
    }

    if (isRoomBlackObject(obj)) {
      obj.material = new THREE.MeshBasicMaterial({
        color: new THREE.Color(0x151515),
        side: THREE.DoubleSide
      });
      return;
    }

    const key = getRoomObjectKey(obj);
    const roomMap = getRoomTextureForKey(key);

    const makeFlat = (srcMat) => {
      const flatMat = new THREE.MeshBasicMaterial({
        map: roomMap || srcMat.map || null,
        color: new THREE.Color(0xffffff),
        transparent: !!srcMat.transparent,
        opacity: typeof srcMat.opacity === "number" ? srcMat.opacity : 1,
        alphaTest: typeof srcMat.alphaTest === "number" ? srcMat.alphaTest : 0,
        side: srcMat.side ?? THREE.FrontSide
      });

      if (flatMat.map) {
        flatMat.map.flipY = false;
        flatMat.map.colorSpace = THREE.SRGBColorSpace;
      }

      return flatMat;
    };

    if (Array.isArray(originalMat)) {
      obj.material = originalMat.map(makeFlat);
    } else {
      obj.material = makeFlat(originalMat);
    }
  });
}

export function setRoomDrapVisible(visible) {
  if (!roomDrapObject) return;
  roomDrapObject.visible = !!visible;
}

export function setRoomTexturesDir(dir = "") {
  roomTexturesDir = String(dir || "");
  roomTextureCache.clear();
}

export function setRoomsFlatMode(enabled, root = currentModel) {
  roomsFlatMode = !!enabled;

  if (roomsFlatMode) {
    for (const light of sceneLights) {
      if (!light) continue;
      light.visible = false;
    }

    if (root) {
      setupRoomCameraHelpers(root);
      applyRoomsFlatMaterials(root);
    }
} else {
  roomCollisionBoxes = [];

  for (const light of sceneLights) {
      if (!light) continue;
      light.visible = true;
    }

    state.target.set(0, 0, 0);
    clearRoomsMaterialOverrides();
  }
}

function initControls(canvas) {
  let dragging = false;
  let lastX = 0, lastY = 0;

  let touchMode = null;
  let lastPinch = 0;

  canvas.addEventListener("mousedown", (e) => {
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
  });

  window.addEventListener("mouseup", () => dragging = false);

  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;

    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;

    lastX = e.clientX;
    lastY = e.clientY;

const rotSpeed = 0.005;

state.targetRotY += dx * -rotSpeed;
state.targetRotX += dy * rotSpeed;

    state.targetRotX = Math.max(
      -Math.PI / 2 + 0.2,
      Math.min(Math.PI / 2 - 0.2, state.targetRotX)
    );
  });

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();

  const delta = e.deltaY * (roomsFlatMode ? 0.00025 : 0.002);

  state.radius = THREE.MathUtils.clamp(
    state.radius + delta,
    state.minRadius,
    state.maxRadius
  );
}, { passive: false });

  canvas.addEventListener("touchstart", (e) => {
    e.preventDefault();

    if (e.touches.length === 1) {
      touchMode = "rotate";
      lastX = e.touches[0].clientX;
      lastY = e.touches[0].clientY;
    } else if (e.touches.length === 2) {
      touchMode = "zoom";
      lastPinch = pinch(e.touches[0], e.touches[1]);
    }
  }, { passive: false });

  canvas.addEventListener("touchmove", (e) => {
    if (!touchMode) return;
    e.preventDefault();

    if (touchMode === "rotate" && e.touches.length === 1) {
      const t = e.touches[0];
      const dx = t.clientX - lastX;
      const dy = t.clientY - lastY;

      lastX = t.clientX;
      lastY = t.clientY;

const rotSpeed = 0.008;

state.targetRotY += dx * -rotSpeed;
state.targetRotX += dy * rotSpeed;

      state.targetRotX = Math.max(
        -Math.PI / 2 + 0.2,
        Math.min(Math.PI / 2 - 0.2, state.targetRotX)
      );
    }

if (touchMode === "zoom" && e.touches.length === 2) {
  const dist = pinch(e.touches[0], e.touches[1]);
  const delta = (lastPinch - dist) * (roomsFlatMode ? 0.0015 : 0.01);

  lastPinch = dist;

  state.radius = THREE.MathUtils.clamp(
    state.radius + delta,
    state.minRadius,
    state.maxRadius
  );
}
  }, { passive: false });

  window.addEventListener("touchend", () => {
    touchMode = null;
  });

  function pinch(a, b) {
    const dx = a.clientX - b.clientX;
    const dy = a.clientY - b.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }
}
