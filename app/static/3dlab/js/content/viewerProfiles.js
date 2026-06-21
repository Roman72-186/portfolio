// js/content/viewerProfiles.js
//
// Профили поведения viewer'а.
// Карточка НЕ хранит настройки рендера вручную.
// Она просто указывает viewerProfile.
//
// Например:
// viewerProfile: VIEWER_PROFILES.INSET
//
// А уже viewer решает:
// - включать ли flat mode;
// - включать ли inset pipeline;
// - использовать ли arch-поведение.

export const VIEWER_PROFILES = {
  ARCH: "arch",
  ROOMS: "rooms",
  BASE: "base",
  INSET: "inset",
  COMPOSITION: "composition",
  FIGURE: "figure"
};

export const VIEWER_PROFILE_LABELS = {
  [VIEWER_PROFILES.ARCH]: "Архитектурные детали",
  [VIEWER_PROFILES.ROOMS]: "Интерьеры / комнатки",
  [VIEWER_PROFILES.INSET]: "Врезки",
  [VIEWER_PROFILES.COMPOSITION]: "Композиции",
  [VIEWER_PROFILES.FIGURE]: "Фигуры"
};
