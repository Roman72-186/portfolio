// js/content/contentTypes.js
//
// Единый словарь новой архитектуры данных.
// Здесь хранятся только названия блоков, подблоков,
// подписи для интерфейса и глобальный порядок показа.
//
// ВАЖНО:
// - порядок НЕ задаём в каждой карточке;
// - карточка хранит только контент;
// - viewer сам показывает блоки в этом порядке, если они есть.

export const BLOCKS = {
  MODEL_3D: "3d",
  SCHEMES: "schemes",
  DRAWING: "drawing",
  VIDEO: "video",
  TICKETS_2024: "tickets2024",
  TICKETS_2025: "tickets2025",
  TICKETS_2026: "tickets2026"
};

export const BLOCK_LABELS = {
  [BLOCKS.MODEL_3D]: "3D",
  [BLOCKS.SCHEMES]: "Схемы",
  [BLOCKS.DRAWING]: "Рисунок",
  [BLOCKS.VIDEO]: "Видео",
  [BLOCKS.TICKETS_2024]: "Билеты 2024",
  [BLOCKS.TICKETS_2025]: "Билеты 2025",
  [BLOCKS.TICKETS_2026]: "Билеты 2026"
};

export const BLOCK_ORDER = [
  BLOCKS.MODEL_3D,
  BLOCKS.SCHEMES,
  BLOCKS.DRAWING,
  BLOCKS.VIDEO,
  BLOCKS.TICKETS_2024,
  BLOCKS.TICKETS_2025,
  BLOCKS.TICKETS_2026
];

export const SUBBLOCKS = {
  MODEL_3D: "3d",
  PHOTOS: "photos",
  SCHEMES: "schemes",
  VIDEOS: "videos"
};

export const SUBBLOCK_LABELS = {
  [SUBBLOCKS.MODEL_3D]: "3D",
  [SUBBLOCKS.PHOTOS]: "Фото",
  [SUBBLOCKS.SCHEMES]: "Схемы",
  [SUBBLOCKS.VIDEOS]: "Видео"
};

export const SUBBLOCK_ORDER = [
  SUBBLOCKS.MODEL_3D,
  SUBBLOCKS.PHOTOS,
  SUBBLOCKS.SCHEMES,
  SUBBLOCKS.VIDEOS
];

export const CONTENT_TYPES = {
  MODEL: "model",
  IMAGES: "images",
  VIDEOS: "videos"
};

export const NODE_TYPES = {
  CATEGORY: "category",
  CARD: "card"
};
