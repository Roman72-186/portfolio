// js/insetsModels.js
//
// ✅ Единый конфиг ВРЕЗОК.
// Добавить новую врезку = добавить один объект в RAW_INSETS.
// models.js сам сможет загрузить source-модели по sourcePath (без правок models.js).

import { ASSET_BASE } from "./assetUrl.js";

// Базовый адрес контента — один на всю лабораторию, см. assetUrl.js
const BASE = ASSET_BASE;

const RAW_INSETS = [

  {
  id: "inset_0",
  name: "Общая теория / Введение",
  desc: "Схемы и видео",
  preview: "textures/preview/preview4.webp",

video: [
  `${BASE}textures/0/vrez/v1.mp4`,
  `${BASE}textures/0/vrez/v2.mp4`,
  `${BASE}textures/0/vrez/v3.mp4`,
  `${BASE}textures/0/vrez/v5.mp4`,
  `${BASE}textures/0/vrez/v8.mp4`,
],
},
  
{
  id: "inset_1",
  name: "Куб и конус",
  desc: "Врезка прямой плоскости в тело вращения",
  preview: "textures/1/preview.png",
  schemes: [
    "textures/1/S1.jpg",
    "textures/1/S2.jpg",
    "textures/1/S3.jpg",
    "textures/1/S4.jpg",
    "textures/1/S5.jpg",
  ],

  // путь в защищённом API (после ?path=)
  sourcePath: "models/1.1.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],
auxSectionMaterialNames: ["4"],

  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},

  {
  id: "inset_2",
  name: "Горизонтальная треугольная призма и конус",
  desc: "Врезка наклонной плоскости в тело вращения",
    preview: "textures/2/preview.png",
  schemes: [
    "textures/2/S1.jpg",
    "textures/2/S2.jpg",
    "textures/2/S3.jpg",
    "textures/2/S4.jpg",
    "textures/2/S5.jpg",
 ],

  // путь в защищённом API (после ?path=)
  sourcePath: "models/2.3.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],
auxSectionMaterialNames: ["4"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},

    {
  id: "inset_3",
  name: "Пирамида и горизонтальная треугольная призма",
  desc: "Врезка двух наклонных плоскостей",
      preview: "textures/3/preview.png",
        schemes: [
          "textures/3/SS1.jpg",
          "textures/3/SS2.jpg",
          "textures/3/SS3.jpg",
          "textures/3/SS4.jpg",
          "textures/3/SS5.jpg",
 ],

  // путь в защищённом API (после ?path=)
  sourcePath: "models/3.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],
auxSectionMaterialNames: ["4"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"]
  ]
}
},
  
    {
  id: "inset_4",
  name: "Треугольная призма и шестигранник",
  desc: "Врезка двух наклонных плоскостей",
      preview: "textures/4/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/4.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["3"],
auxSectionMaterialNames: ["2"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5" // эллипс
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"]
  ]
}
},

  {
  id: "inset_5",
  name: "Горизонтальный цилиндр и пирамида",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "textures/5/preview.png",
      schemes: [
    "textures/5/S1.jpg",
    "textures/5/S2.jpg",
    "textures/5/S3.jpg",
    "textures/5/S4.jpg",
    "textures/5/S5.jpg",
 ],

  // путь в защищённом API (после ?path=)
  sourcePath: "models/5.1.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["3"],
auxSectionMaterialNames: ["2", "4"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},
  
  {
  id: "inset_6",
  name: "Два горизонтальных шестигранника",
  desc: "Врезка наклонных плоскостей",
  preview: "textures/6/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/6.2.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],
auxSectionMaterialNames: ["4", "5"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406", // вспомогательное
    "5": "#12d6c3" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"],
    ["i", "j"],
    ["k", "l"]
  ]
}
},

  
  {
  id: "inset_7",
  name: "Горизонтальный цилиндр и треугольная призма",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "textures/7/preview.png",
      schemes: [
    "textures/7/S1.jpg",
    "textures/7/S2.jpg",
    "textures/7/S3.jpg",
    "textures/7/S4.jpg",
    "textures/7/S5.jpg",
 ],

  // путь в защищённом API (после ?path=)
  sourcePath: "models/7.1.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],
auxSectionMaterialNames: ["4"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},
      {
  id: "inset_8",
  name: "Вертикальная треугольная призма и цилиндр",
  desc: "Врезка наклонной плоскости в тело вращения",
      preview: "textures/8/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/8.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["3"],
auxSectionMaterialNames: ["2", "4"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},
  {
  id: "inset_9",
  name: "Вертикальный цилинлр и треугольная призма",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "textures/9/preview.png",
      schemes: [
    "textures/9/S1.jpg",
    "textures/9/S2.jpg",
    "textures/9/S3.jpg",
    "textures/9/S4.jpg",
    "textures/9/S5.jpg",
 ],

  // путь в защищённом API (после ?path=)
  sourcePath: "models/9.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["3"],
auxSectionMaterialNames: ["2", "4"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},
    {
  id: "inset_10",
  name: "Горизонтальный цилиндр и шестигранник",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "textures/10/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/10.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],
auxSectionMaterialNames: ["4", "5"],

  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406", // вспомогательное
    "5": "#12d6c3" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"],
    ["e", "f"],
    ["g", "h"]
  ]
}
},
    {
  id: "inset_11",
  name: "Вертикальный цилиндр и пирамида",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "textures/11/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/11.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],
auxSectionMaterialNames: ["4", "5", "6"],

  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406", // вспомогательное
    "5": "#12d6c3", // вспомогательное
    "6": "#FF5500" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"],
    ["e", "f"],
    ["g", "h"]
  ]
}
},
    {
  id: "inset_12",
  name: "Пирамида и горизонтальный шестигранник",
  desc: "Врезка наклонных плоскостей",
  preview: "textures/12/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/12.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["3"],
auxSectionMaterialNames: ["2", "4"],


  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
    "4": "#ddf406" // вспомогательное
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},
  {
  id: "inset_13",
  name: "Вертикальная пирамида и треугольная призма",
  desc: "Врезка наклонных плоскостей",
  preview: "textures/13/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/13.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "3"],

  // цвета сечений
  materialColors: {
    "2": "#d929c1", // круг
    "3": "#1c58e5", // эллипс
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},
    {
  id: "inset_14",
  name: "Вертикальная треугольная призма и конус",
  desc: "Врезка наклонной плоскости в тело вращения",
  preview: "textures/14/preview.png",

  // путь в защищённом API (после ?path=)
  sourcePath: "models/14.gltf",

  // материал тела (управляется ползунком)
  opacityMaterialName: "1",

// материалы-сечения
primarySectionMaterialNames: ["2", "5"],
auxSectionMaterialNames: ["3", "4"],

        // цвета сечений
  materialColors: {
    "2": "#1c58e5", // порабола
    "3": "#ddf406", // вспомогательное
    "4": "#12d6c3", // вспомогательное
    "5": "#d929c1" // круг
  },

  // ===== CAD-обвязка =====
cad: {
  fromNodes: true,
  lines: [
    ["a", "b"],
    ["c", "d"]
  ]
}
},
  ]

// ✅ Автоматически генерим sourceId, если не задан вручную
export const INSETS = RAW_INSETS.map((m) => ({
  ...m,
  sourceId: m.sourceId || `${m.id}_source`
}));

// ✅ Описание “source-моделей” для загрузчика (models.js)
export const INSET_SOURCE_DEFS = INSETS
  .filter((m) => !!m.sourcePath)
  .map((m) => ({
    id: m.sourceId,
    name: m.name,
    desc: m.desc,
    path: m.sourcePath
  }));

export function getInsetMeta(id) {
  return INSETS.find((m) => m.id === id) || null;
}
