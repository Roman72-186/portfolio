// Build the server-side card index from the same data modules the lab renders.
// Run: node scripts/build_lab_catalog.js [--check]
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../app/static/3dlab/js");
const output = path.resolve(__dirname, "../app/data/lab_catalog.json");
const base = "https://lab.example/lab/asset/";

function dataModule(file, names, globals = {}, before = null) {
  let source = fs.readFileSync(path.join(root, file), "utf8");
  if (before) source = source.slice(0, source.indexOf(before));
  source = source.replace(/^import .*;\s*$/gm, "").replace(/\bexport\s+(?=(?:const|function)\b)/g, "");
  const context = vm.createContext({ ASSET_BASE: base, ...globals });
  return vm.runInContext(`${source}\n({${names.join(",")}})`, context, { filename: file });
}

const types = dataModule("content/contentTypes.js", ["BLOCKS", "SUBBLOCKS", "CONTENT_TYPES", "NODE_TYPES"]);
const { CONTENT_TREE } = dataModule("content/contentTree.js", ["CONTENT_TREE"], { NODE_TYPES: types.NODE_TYPES });
const { ROOMS, ROOM_SOURCE_DEFS } = dataModule("roomsModels.js", ["ROOMS", "ROOM_SOURCE_DEFS"]);
const { INSETS, INSET_SOURCE_DEFS } = dataModule("insetsModels.js", ["INSETS", "INSET_SOURCE_DEFS"]);
const { MODELS } = dataModule("models.js", ["MODELS"], { ROOM_SOURCE_DEFS, INSET_SOURCE_DEFS }, "export function getModelMeta");
const { CARDS } = dataModule("content/cardsRegistry.js", ["CARDS"], types);
const refs = new Map([...MODELS, ...ROOMS, ...INSETS].map((item) => [item.id, item]));

function assetPath(value) {
  if (typeof value !== "string") return null;
  const raw = value.startsWith(base) ? value.slice(base.length) : value;
  if (/^(?:https?:|data:|\/)/i.test(raw) || raw.includes("..")) return null;
  return raw.replace(/^\/+/, "");
}

function keyFiles(value) {
  const files = new Set();
  function walk(node, key = "", inSchemes = false) {
    if (typeof node === "string") {
      const asset = assetPath(node);
      if (asset && (/\.(?:gltf|glb|mp4|webm)$/i.test(asset) ||
        (/\.(?:png|jpe?g|webp)$/i.test(asset) && (inSchemes || /^(?:schemes?|images?)$/i.test(key))))) {
        files.add(asset);
      }
    } else if (Array.isArray(node)) {
      node.forEach((item) => walk(item, key, inSchemes));
    } else if (node && typeof node === "object") {
      Object.entries(node).forEach(([name, item]) => walk(item, name, inSchemes || /scheme/i.test(name)));
    }
  }
  walk(value);
  for (const file of [...files]) {
    if (file.endsWith(".gltf")) files.add(file.replace(/\.gltf$/, ".bin"));
  }
  return [...files].sort();
}

const cards = [];
function visit(node, ancestors = []) {
  const trail = [...ancestors, node.title];
  if (node.type === types.NODE_TYPES.CARD) {
    if (!node.ref || !(node.ref in CARDS) && !refs.has(node.ref)) {
      throw new Error(`Unknown card reference: ${node.id} -> ${node.ref}`);
    }
    cards.push({
      id: node.id,
      ref: node.ref,
      title: node.title,
      path: trail.slice(1).join(" → "),
      hidden: !!node.hidden,
      assets: keyFiles(CARDS[node.ref] || refs.get(node.ref)),
    });
  }
  for (const child of node.children || []) visit(child, trail);
}
visit(CONTENT_TREE);
const ids = cards.map((item) => item.id);
if (new Set(ids).size !== ids.length) throw new Error("Duplicate card IDs");
const json = JSON.stringify({ cards }, null, 2) + "\n";
if (process.argv.includes("--check")) {
  if (!fs.existsSync(output) || fs.readFileSync(output, "utf8") !== json) {
    console.error("lab_catalog.json differs from lab data modules");
    process.exitCode = 1;
  }
} else {
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, json);
  const shared = new Map();
  for (const card of cards) for (const asset of card.assets) {
    if (!shared.has(asset)) shared.set(asset, []);
    shared.get(asset).push(card.id);
  }
  console.log(`${cards.length} cards, ${[...shared.values()].filter((owners) => owners.length > 1).length} shared key files`);
  for (const [asset, owners] of shared) {
    if (owners.length > 1 && asset.startsWith("models/")) console.log(`${asset}: ${owners.join(", ")}`);
  }
}
