// js/content/cardResolver.js
//
// Единая точка получения карточек новой архитектуры.
//
// Задача:
// - app.js не должен знать, карточка новая или legacy;
// - viewer не должен знать про MODELS / ROOMS / INSETS;
// - всё получение карточек идёт через getCardById().

import { CARDS } from "./cardsRegistry.js";
import { buildLegacyCards } from "./legacyAdapters.js";

const LEGACY_CARDS = buildLegacyCards();

export function getCardById(cardId) {
  if (!cardId) return null;

  return CARDS[cardId] || LEGACY_CARDS[cardId] || null;
}

export function getAllCards() {
  return {
    ...LEGACY_CARDS,
    ...CARDS
  };
}

export function hasCard(cardId) {
  return Boolean(getCardById(cardId));
}
