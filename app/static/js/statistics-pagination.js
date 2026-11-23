(() => {
    const pageSize = 10;

    function paginate(list, anchor, feed = false) {
        const nav = document.createElement('nav');
        nav.className = 'stats-pagination';
        nav.setAttribute('aria-label', 'Страницы списка');
        nav.hidden = true;
        const previous = document.createElement('button');
        const next = document.createElement('button');
        previous.type = next.type = 'button';
        previous.textContent = 'Назад';
        next.textContent = 'Далее';
        const range = document.createElement('span');
        range.setAttribute('aria-live', 'polite');
        nav.append(previous, range, next);
        anchor.after(nav);

        let page = 0;
        let records = [];
        function showPage() {
            const start = page * pageSize;
            const end = Math.min(start + pageSize, records.length);
            const visible = new Set(records.slice(start, end));
            if (feed) {
                // Заголовок группы остаётся рядом с её действиями при перелистывании.
                let headings = [];
                let previousWasRecord = false;
                for (const row of list.children) {
                    if (row.querySelector('.sbn-feed-head')) {
                        if (previousWasRecord) headings = [];
                        headings.push(row);
                        previousWasRecord = false;
                    } else {
                        if (visible.has(row)) headings.forEach(heading => visible.add(heading));
                        previousWasRecord = true;
                    }
                }
                visible.add(list.firstElementChild);
            }
            for (const row of list.children) {
                const isEntry = feed || row.matches('tr') && !row.querySelector('td[colspan]');
                row.toggleAttribute('data-stats-page-hidden', isEntry && !visible.has(row));
            }
            nav.hidden = records.length <= pageSize;
            previous.disabled = page === 0;
            next.disabled = end === records.length;
            range.textContent = records.length ? `${start + 1}–${end} из ${records.length}` : '';
        }

        function refresh() {
            const updated = Array.from(list.children).filter(row => feed
                ? Boolean(row.querySelector('.sbn-feed-mark'))
                : row.matches('tr:not([hidden])') && row.style.display !== 'none' && !row.querySelector('td[colspan]'));
            if (updated.length !== records.length || updated.some((row, index) => row !== records[index])) {
                page = 0;
                records = updated;
            }
            showPage();
        }

        previous.addEventListener('click', () => { page -= 1; showPage(); });
        next.addEventListener('click', () => { page += 1; showPage(); });
        if (!feed) {
            new MutationObserver(changes => {
                if (changes.some(change => change.type === 'childList'
                    ? change.target === list
                    : change.target.parentElement === list)) refresh();
            }).observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'style'] });
        }
        refresh();
    }

    for (const root of document.querySelectorAll('[data-stats-pagination]')) {
        for (const table of root.querySelectorAll('table')) {
            if (table.tBodies.length === 1) paginate(table.tBodies[0], table);
        }
        const feeds = new WeakSet();
        function scanFeeds(node) {
            if (!(node instanceof Element)) return;
            const lists = node.matches('.sbn-feed') ? [node] : node.querySelectorAll('.sbn-feed');
            for (const list of lists) {
                if (!feeds.has(list)) {
                    feeds.add(list);
                    paginate(list, list, true);
                }
            }
        }
        scanFeeds(root);
        new MutationObserver(changes => {
            for (const change of changes) {
                for (const node of change.addedNodes) scanFeeds(node);
            }
        }).observe(root, { childList: true, subtree: true });
    }
})();
