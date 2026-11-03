export function reportModelResult(modelResult, cardId) {
  if (!cardId || cardId !== window.LAB_ACTIVE_CARD_ID || !window.LAB_CSRF_TOKEN || !navigator.sendBeacon) return;
  navigator.sendBeacon("/lab/event", new Blob([JSON.stringify({
    card_id: cardId,
    csrf_token: window.LAB_CSRF_TOKEN,
    model_result: modelResult
  })], { type: "application/json" }));
}
