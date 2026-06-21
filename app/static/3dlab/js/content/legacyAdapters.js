// js/content/legacyAdapters.js
//
// Переходный слой между старой системой данных и новой архитектурой.
//
// Задача файла:
// - НЕ менять старые MODELS / ROOMS / INSETS;
// - НЕ трогать viewer'ы;
// - НЕ придумывать новые S3-пути;
// - аккуратно превращать старые карточки в новый формат.
//
// Это временный мост для Этапа 2.

import { MODELS } from "../models.js";
import { ROOMS } from "../roomsModels.js";
import { INSETS } from "../insetsModels.js";

import { BLOCKS, SUBBLOCKS } from "./contentTypes.js";
import { VIEWER_PROFILES } from "./viewerProfiles.js";

function hasList(value) {
  return Array.isArray(value) && value.length > 0;
}

function createEmptyCard({ id, title, desc, preview, viewerProfile, legacyType, legacyMeta }) {
  return {
    id,
    title,
    desc: desc || "",
    preview: preview || "",
    viewerProfile,
    legacyType,
    legacyMeta,
    blocks: {}
  };
}

function addModelBlock(card, modelSrc) {
  if (!modelSrc) return;

  card.blocks[BLOCKS.MODEL_3D] = {
    subblocks: {
      [SUBBLOCKS.MODEL_3D]: {
        model: modelSrc
      }
    }
  };
}

function addPhotosTo3dBlock(card, photos) {
  if (!hasList(photos)) return;

  if (!card.blocks[BLOCKS.MODEL_3D]) {
    card.blocks[BLOCKS.MODEL_3D] = { subblocks: {} };
  }

  card.blocks[BLOCKS.MODEL_3D].subblocks[SUBBLOCKS.PHOTOS] = {
    images: photos
  };
}

function addSchemesBlock(card, schemes) {
  if (!hasList(schemes)) return;

  card.blocks[BLOCKS.SCHEMES] = {
    subblocks: {
      [SUBBLOCKS.SCHEMES]: {
        images: schemes
      }
    }
  };
}

function addVideoBlock(card, videos) {
  if (!hasList(videos)) return;

  card.blocks[BLOCKS.VIDEO] = {
    subblocks: {
      [SUBBLOCKS.VIDEOS]: {
        videos
      }
    }
  };
}

export function adaptArchModel(model) {
  const card = createEmptyCard({
    id: model.id,
title: model.name,
desc: model.desc,
preview: model.preview,
    viewerProfile: VIEWER_PROFILES.ARCH,
    legacyType: "arch",
    legacyMeta: model
  });

  addModelBlock(card, model.url || model.sourcePath);
  addPhotosTo3dBlock(card, model.photos);
  addSchemesBlock(card, model.schemes);
  addVideoBlock(card, model.video);

  return card;
}

export function adaptRoomModel(room) {
  const card = createEmptyCard({
    id: room.id,
title: room.name,
desc: room.desc,
preview: room.preview,
    viewerProfile: VIEWER_PROFILES.ROOMS,
    legacyType: "rooms",
    legacyMeta: room
  });

  addModelBlock(card, room.url || room.sourcePath);
  addPhotosTo3dBlock(card, room.photos);
  addSchemesBlock(card, room.schemes);
  addVideoBlock(card, room.video);

  return card;
}

export function adaptInsetModel(inset) {
  const card = createEmptyCard({
    id: inset.id,
title: inset.name,
desc: inset.desc,
preview: inset.preview,
    viewerProfile: VIEWER_PROFILES.INSET,
    legacyType: "inset",
    legacyMeta: inset
  });

  addModelBlock(card, inset.url || inset.sourcePath);
  addSchemesBlock(card, inset.schemes);
  addVideoBlock(card, inset.video);

  return card;
}

export function buildLegacyCards() {
  const cards = {};

  for (const model of MODELS) {
    const card = adaptArchModel(model);
    cards[card.id] = card;
  }

  for (const room of ROOMS) {
    const card = adaptRoomModel(room);
    cards[card.id] = card;
  }

  for (const inset of INSETS) {
    const card = adaptInsetModel(inset);
    cards[card.id] = card;
  }

  return cards;
}
