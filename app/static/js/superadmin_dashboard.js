(() => {
    const easterEgg = document.querySelector('[data-sa-easter]');
    if (!easterEgg || typeof easterEgg.showModal !== 'function') return;

    document.addEventListener('keydown', (event) => {
        if (event.code !== 'KeyP' || !event.altKey || !event.shiftKey || event.ctrlKey ||
            event.metaKey || event.defaultPrevented || event.repeat ||
            event.target.closest?.('input, textarea, select, [contenteditable]') ||
            document.querySelector('dialog[open]')) return;

        event.preventDefault();
        easterEgg.showModal();
    });
})();
