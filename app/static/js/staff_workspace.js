(() => {
    const finder = document.getElementById('staff-finder');
    if (!finder || typeof finder.showModal !== 'function') return;

    const input = document.getElementById('staff-finder-query');
    const links = Array.from(finder.querySelectorAll('.staff-finder-list a'));
    const empty = finder.querySelector('.staff-finder-empty');
    const secret = finder.querySelector('.staff-finder-secret');
    const triggers = document.querySelectorAll('[data-staff-find]');
    const normalize = (value) => value.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е');
    let visible = links;

    function filter() {
        const query = normalize(input.value);
        const words = query.split(/\s+/);
        visible = links.filter((link) => {
            const text = normalize(link.textContent);
            const matches = words.every((word) => text.includes(word));
            link.parentElement.hidden = !matches;
            return matches;
        });
        secret.hidden = query !== 'секретный чертеж';
        empty.hidden = visible.length > 0 || !secret.hidden;
    }

    function openFinder() {
        if (document.querySelector('dialog[open]')) return;
        input.value = '';
        filter();
        finder.showModal();
    }

    triggers.forEach((button) => {
        button.hidden = false;
        button.addEventListener('click', openFinder);
    });
    input.addEventListener('input', filter);
    finder.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') event.stopPropagation();
        const index = visible.indexOf(document.activeElement);
        if (event.key === 'Enter' && event.target === input && visible.length > 0) {
            event.preventDefault();
            visible[0].click();
        } else if (event.key === 'ArrowDown' && (index >= 0 || event.target === input) && visible.length > 0) {
            event.preventDefault();
            visible[Math.min(index + 1, visible.length - 1)].focus();
        } else if (event.key === 'ArrowUp' && index >= 0) {
            event.preventDefault();
            (visible[index - 1] || input).focus();
        }
    });
    document.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && !event.altKey && event.code === 'KeyK' &&
            !event.defaultPrevented && !event.target.closest?.('input, textarea, [contenteditable="true"]') &&
            !document.querySelector('dialog[open]')) {
            event.preventDefault();
            openFinder();
        }
    });
})();
