(() => {
    const ns = 'http://www.w3.org/2000/svg';
    const radius = 100;
    const turn = 2 * Math.PI;
    const categories = [
        ['done', 'var(--blue-fill)'],
        ['started', 'var(--blue-mid)'],
        ['none', 'var(--line)'],
    ];

    function point(angle) {
        return [radius * Math.cos(angle), radius * Math.sin(angle)].join(' ');
    }

    function render(container) {
        const sectors = categories.map(([key, color]) => ({
            color, count: Number(container.dataset[key]),
        })).filter(({count}) => Number.isFinite(count) && count > 0);
        const total = sectors.reduce((sum, {count}) => sum + count, 0);
        if (!total) return;

        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('viewBox', '-100 -100 200 200');
        svg.setAttribute('focusable', 'false');
        let angle = -Math.PI / 2;
        for (const {color, count} of sectors) {
            const sector = document.createElementNS(ns, sectors.length === 1 ? 'circle' : 'path');
            if (sectors.length === 1) {
                sector.setAttribute('r', radius);
            } else {
                const end = angle + turn * count / total;
                const largeArc = count > total / 2 ? 1 : 0;
                sector.setAttribute('d', `M 0 0 L ${point(angle)} A ${radius} ${radius} 0 ${largeArc} 1 ${point(end)} Z`);
                angle = end;
            }
            sector.setAttribute('fill', color);
            svg.append(sector);
        }
        container.replaceChildren(svg);
    }

    document.querySelectorAll('[data-curator-pie]').forEach(render);
})();
