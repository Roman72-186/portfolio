// Копирование в буфер — общее для экранов со списками учеников.
// Вынесено 02.10.2026 из «Статистики активности»: должникам цикла нужно то же.
//
// `copyText(text)` — Clipboard API, а без HTTPS (локальный стенд) — через
// скрытую textarea. Бросает ошибку, если не вышло: вызывающий пишет статус.
async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) throw new Error('copy failed');
}

// Любая кнопка с `data-copy` (макрос `components/tg_copy.html`): одна
// делегация на страницу, отметка «скопировано» на полторы секунды.
document.addEventListener('click', async function (event) {
    const button = event.target.closest('[data-copy]');
    if (!button) return;
    try {
        await copyText(button.dataset.copy);
        button.classList.add('is-copied');
        setTimeout(function () { button.classList.remove('is-copied'); }, 1500);
    } catch (error) {
        button.title = 'Не удалось скопировать';
    }
});
