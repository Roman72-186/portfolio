// js/roomsModels.js
//
// Новый раздел: "Комнатки" / "Разрезы помещений".
// Пока используем временно те же ресурсы, что и в "Архитектурных деталях",
// чтобы быстро поднять новый раздел.
// Позже ты просто заменишь preview / schemes / photos / video / sourcePath.
const BASE = "https://api.apparchi.ru/?path=";
const RAW_ROOMS = [
  {
    id: "room_0",
    name: "Общая теория / Введение",
    desc: "Схемы и видео",
    preview: `${BASE}textures/preview/preview3.webp`,
    thumbLetter: "0",

    // Нулевая карточка: БЕЗ 3D
    schemes: [
      `${BASE}textures/0/arch/s1.jpg`
    ],

    video: [
      "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/0/arch/v1.mp4",
      "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/0/arch/v2.mp4",
      "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/0/arch/v3.mp4",
      "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/0/arch/v4.mp4"
    ]
  },

  {
    id: "room_1",
    name: "Тестовая комнатка",
    desc: "Временно на ресурсах архдеталей",
    preview: `${BASE}textures/doric/preview.png`,
    thumbLetter: "К",

    sourcePath: "models/rooms/1/1.gltf",

    textures: {
      base: `${BASE}models/rooms/1/1.jpg`,
  //    normal: `${BASE}textures/doric/Normal.jpg`,
  //    rough: `${BASE}textures/doric/Roughness.jpg`,
  //    metalness: 0,
      roughness: 1,
  //    envIntensity: 0.7
    },
    

    schemes: [
      `${BASE}textures/doric/s1.jpg`,
      `${BASE}textures/doric/s2.jpg`,
      `${BASE}textures/doric/s3.jpg`
    ],

    video: [
      "https://s3.twcstorage.ru/540d791f-86c02015-75b1-462f-b960-b855e300451a/textures/doric/v1.mp4"
    ]
  }
];


// Основной список карточек раздела
export const ROOMS = RAW_ROOMS.map((m) => ({
  ...m,
  sourceId: m.sourceId || `${m.id}_source`
}));

// Source-модели для загрузчика models.js
export const ROOM_SOURCE_DEFS = ROOMS
  .filter((m) => !!m.sourcePath)
  .map((m) => ({
    id: m.sourceId,
    name: m.name,
    desc: m.desc,
    path: m.sourcePath,
    textures: m.textures || null
  }));

export function getRoomMeta(id) {
  return ROOMS.find((m) => m.id === id) || null;
}
