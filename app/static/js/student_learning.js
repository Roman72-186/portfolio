(() => {
    const secret = document.querySelector('[data-student-secret]');
    if (!secret || typeof secret.showModal !== 'function') return;

    function reveal() {
        if (document.querySelector('dialog[open], [aria-modal="true"][aria-hidden="false"]')) return;
        secret.showModal();
    }

    document.addEventListener('keydown', (event) => {
        if (event.code !== 'KeyP' || !event.altKey || !event.shiftKey || event.ctrlKey ||
            event.metaKey || event.defaultPrevented || event.repeat ||
            event.target.closest?.('input, textarea, select, [contenteditable]')) return;
        reveal();
    });

    const progress = document.querySelector('[data-student-progress]');
    let count = 0;
    let lastTap = 0;
    progress?.addEventListener('click', () => {
        const now = performance.now();
        count = now - lastTap < 550 ? count + 1 : 1;
        lastTap = now;
        if (count === 5) {
            count = 0;
            reveal();
        }
    });
})();
