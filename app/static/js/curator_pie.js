(() => {
    const ns = 'http://www.w3.org/2000/svg';
    const turn = 2 * Math.PI;
    const radius = 89;
    const pitch = 0.66;
    const thickness = 19;
    const separation = 12;
    const center = [115, 72];
    const categories = [
        ['done', 'var(--pie-done, var(--blue-fill))'],
        ['started', 'var(--pie-started, var(--blue-mid))'],
        ['none', 'var(--pie-none, var(--line))'],
    ];

    function element(tag, attributes) {
        const node = document.createElementNS(ns, tag);
        for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
        return node;
    }

    // Project a horizontal circle into an ellipse; extrusion runs down the screen.
    function point(origin, angle, z = 0) {
        return [origin[0] + radius * Math.cos(angle), origin[1] + radius * pitch * Math.sin(angle) + z];
    }

    function arc(origin, from, to, z = 0) {
        const [x, y] = point(origin, to, z);
        const wide = Math.abs(to - from) > Math.PI ? 1 : 0;
        return `A ${radius} ${radius * pitch} 0 ${wide} ${to > from ? 1 : 0} ${x} ${y}`;
    }

    function frontArcs(from, to) {
        // The outer wall faces the viewer only on the lower half of the ellipse.
        const segments = [];
        for (let n = Math.floor(from / turn); n <= Math.floor(to / turn); n++) {
            const start = Math.max(from, n * turn);
            const end = Math.min(to, n * turn + Math.PI);
            if (start < end) segments.push([start, end]);
        }
        return segments;
    }

    function drawBase(svg, defs, index) {
        const [cx, cy] = [center[0], center[1] + thickness];
        const rx = 105;
        const ry = rx * pitch;
        const height = 9;
        const left = cx - rx;
        const right = cx + rx;
        const rimId = `curator-pie-rim-${index}`;
        const rim = element('linearGradient', {id: rimId, x1: '0%', y1: '0%', x2: '100%', y2: '100%'});
        rim.append(
            element('stop', {'stop-color': 'color-mix(in srgb, var(--line) 90%, var(--blue-deep))'}),
            element('stop', {offset: '100%', 'stop-color': 'color-mix(in srgb, var(--line) 70%, #302141)'}),
        );
        defs.append(rim);

        const base = element('g', {class: 'curator-pie-base'});
        base.append(
            element('path', {
                d: `M ${left} ${cy} A ${rx} ${ry} 0 0 0 ${right} ${cy} V ${cy + height} A ${rx} ${ry} 0 0 1 ${left} ${cy + height} Z`,
                fill: `url(#${rimId})`,
            }),
            element('ellipse', {
                cx, cy, rx, ry,
                fill: 'color-mix(in srgb, var(--surface) 30%, var(--line))',
                stroke: 'color-mix(in srgb, var(--line) 60%, var(--blue-deep))',
                'stroke-width': '.7',
            }),
        );
        svg.append(base);
    }

    function render(container, index) {
        const data = categories.map(([key, color]) => ({
            color, value: Number(container.dataset[key]),
        })).filter(({value}) => Number.isFinite(value) && value > 0);
        const total = data.reduce((sum, {value}) => sum + value, 0);
        if (!total || !Number.isFinite(total)) return;

        const svg = element('svg', {viewBox: '0 0 230 179', 'aria-hidden': 'true', focusable: 'false'});
        const defs = element('defs', {});
        const gradientId = `curator-pie-light-${index}`;
        const light = element('linearGradient', {id: gradientId, x1: '10%', y1: '0%', x2: '90%', y2: '100%'});
        light.append(element('stop', {'stop-color': 'white', 'stop-opacity': '.35'}),
            element('stop', {offset: '60%', 'stop-color': 'white', 'stop-opacity': '0'}));
        defs.append(light);
        svg.append(defs);
        drawBase(svg, defs, index);

        let angle = -Math.PI / 2;
        const sectors = data.map(({color, value}) => {
            const from = angle;
            angle += turn * (value / total);
            const to = angle;
            const middle = (from + to) / 2;
            const gap = data.length === 1 ? 0 : separation;
            const origin = [center[0] + gap * Math.cos(middle), center[1] + gap * pitch * Math.sin(middle)];
            // Depth of the centroid of a circular sector, projected onto the view.
            const span = to - from;
            const depth = (gap + 4 * radius * Math.sin(span / 2) / (3 * span)) * Math.sin(middle);
            return {color, from, to, origin, depth};
        }).filter(({from, to}) => to > from).sort((a, b) => a.depth - b.depth);

        for (const {color, from, to, origin} of sectors) {
            const group = element('g', {});
            const shade = (percent) => `color-mix(in srgb, ${color} ${percent}%, #20102f)`;
            const face = (d, fill) => group.append(element('path', {d, fill}));

            for (const [start, end] of frontArcs(from, to)) {
                face(`M ${point(origin, start)} ${arc(origin, start, end)} L ${point(origin, end, thickness)} ${arc(origin, end, start, thickness)} Z`, shade(70));
            }
            if (data.length > 1) {
                const bottom = [origin[0], origin[1] + thickness];
                // Only the outward-facing radial cuts contribute a visible wall.
                if (Math.cos(from) < -1e-8) face(`M ${origin} L ${point(origin, from)} ${point(origin, from, thickness)} ${bottom} Z`, shade(79));
                if (Math.cos(to) > 1e-8) face(`M ${origin} L ${point(origin, to)} ${point(origin, to, thickness)} ${bottom} Z`, shade(58));
            }

            const top = data.length === 1
                ? `M ${point(origin, from)} ${arc(origin, from, from + Math.PI)} ${arc(origin, from + Math.PI, to)} Z`
                : `M ${origin} L ${point(origin, from)} ${arc(origin, from, to)} Z`;
            face(top, color);
            face(top, `url(#${gradientId})`);
            group.append(element('path', {d: top, fill: 'none', stroke: 'white', 'stroke-opacity': '.38', 'stroke-width': '.8', 'stroke-linejoin': 'round'}));
            svg.append(group);
        }

        container.replaceChildren(svg);
    }

    document.querySelectorAll('[data-curator-pie]').forEach(render);
})();
