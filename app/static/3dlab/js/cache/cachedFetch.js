import { getFile, saveFile } from "./db.js";

// Логирование для отладки
function log(msg) {
  console.log(msg);
}

// Версия IndexedDB-кэша ассетов (модели/текстуры/видео с S3).
// Раньше ключ кэша был только "path" из URL — обновление файла на S3 в тот же
// путь никогда не доходило до уже открывавших модель пользователей, потому что
// старый blob в IndexedDB оставался достижим по тому же ключу бессрочно.
// Теперь ключ = версия + полный URL. Чтобы форсировать переcкачивание всех
// закэшированных ассетов после обновления контента на S3 — поднять эту цифру.
const ASSET_CACHE_VERSION = 1;

// Дедупликация одновременных запросов одного и того же ресурса
const inflight = new Map();

export async function cachedFetch(url) {
  const cacheKey = `${ASSET_CACHE_VERSION}:${url}`;

  // Если этот же ресурс уже качается/пишется — ждём тот же промис
  if (inflight.has(cacheKey)) {
    return inflight.get(cacheKey);
  }

  const p = (async () => {
    // 1) кеш
    const cached = await getFile(cacheKey);
    if (cached) {
      log("HIT: " + cacheKey);
      return cached;
    }

    // 2) сеть
    log("SAVE: " + cacheKey);
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Fetch failed ${res.status} for ${cacheKey}`);
    const blob = await res.blob();

    // 3) попытка сохранить (на мобиле может упасть по квоте — не роняем всё приложение)
    try {
      await saveFile(cacheKey, blob);
    } catch (e) {
      console.warn("IDB save failed (quota/tx):", cacheKey, e);
    }

    return blob;
  })();

  inflight.set(cacheKey, p);

  try {
    return await p;
  } finally {
    inflight.delete(cacheKey);
  }
}
