// Подготовка фото в браузере перед отправкой: ужать тяжёлое и повернуть.
//
// Поворот до загрузки — с 04.10.2026 (владелец: «поворот… для ученика при
// загрузке»). Ученик видит превью и жмёт ⟳, пока фото не встанет как надо;
// пиксели крутятся здесь, на canvas, и на сервер уходит уже повёрнутый JPEG.
// Серверу менять нечего: браузер рисует картинку на canvas с учётом
// EXIF-ориентации, а `toBlob` метку выбрасывает — `exif_transpose` в
// `compress_image` второй раз ничего не повернёт.
//
// Сколько раз повернуть, помнит сам файл (WeakMap по объекту File): у каждого
// экрана свой массив выбранных фото, и заводить рядом второй массив поворотов
// не нужно — превью перерисовываются, а поворот держится за файл.
//
// Подключать `<script src="/static/js/photo-prep.js?v=N">` в каждом шаблоне,
// где он нужен, а не в base.html. Стили кнопки — `.preview-rotate` в base.css.
(function () {
    // Длинная сторона повёрнутого фото. Сервер всё равно ужимает до 1600 px
    // (`compress_image`), а canvas у iOS Safari ограничен ~16 Мп.
    var ROTATE_MAX_PX = 2400;
    var ROTATE_QUALITY = 0.92;

    var turnsByFile = new WeakMap();

    function loadImage(file) {
        return new Promise(function (resolve, reject) {
            var url = URL.createObjectURL(file);
            var img = new Image();
            img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
            img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('decode')); };
            img.src = url;
        });
    }

    function canvasToBlob(canvas, quality) {
        return new Promise(function (resolve) { canvas.toBlob(resolve, 'image/jpeg', quality); });
    }

    function jpegFile(blob, original) {
        var name = (original.name || 'photo').replace(/\.[^.]*$/, '') + '.jpg';
        return new File([blob], name, { type: 'image/jpeg', lastModified: original.lastModified });
    }

    // Фото до `maxSize` уходит как есть. Тяжелее — пережимаем по шагам
    // `steps` ([длинная сторона, качество]), пока не влезет; не влезло — ошибка.
    async function shrink(file, maxSize, steps) {
        if (file.size <= maxSize) return file;
        var img = await loadImage(file);
        var longSide = Math.max(img.naturalWidth, img.naturalHeight);
        for (var s = 0; s < steps.length; s++) {
            var scale = Math.min(1, steps[s][0] / longSide);
            var canvas = document.createElement('canvas');
            canvas.width = Math.round(img.naturalWidth * scale);
            canvas.height = Math.round(img.naturalHeight * scale);
            canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
            var blob = await canvasToBlob(canvas, steps[s][1]);
            if (blob && blob.size <= maxSize) {
                var ready = jpegFile(blob, file);
                setTurns(ready, getTurns(file));
                return ready;
            }
        }
        throw new Error('too-big');
    }

    function normTurns(t) { return ((t % 4) + 4) % 4; }
    function getTurns(file) { return turnsByFile.get(file) || 0; }
    function setTurns(file, t) { turnsByFile.set(file, normTurns(t)); }

    // Повёрнутая копия файла: `turns` четвертей по часовой стрелке.
    async function rotate(file, turns) {
        turns = normTurns(turns);
        if (!turns) return file;
        var img = await loadImage(file);
        var w = img.naturalWidth, h = img.naturalHeight;
        var scale = Math.min(1, ROTATE_MAX_PX / Math.max(w, h));
        var dw = Math.round(w * scale), dh = Math.round(h * scale);
        var canvas = document.createElement('canvas');
        var sideways = turns % 2 === 1;
        canvas.width = sideways ? dh : dw;
        canvas.height = sideways ? dw : dh;
        var ctx = canvas.getContext('2d');
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate(turns * Math.PI / 2);
        ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
        var blob = await canvasToBlob(canvas, ROTATE_QUALITY);
        if (!blob) throw new Error('encode');
        return jpegFile(blob, file);
    }

    // Файл к отправке. Не вышло повернуть — уходит оригинал: ребёнок не
    // должен потерять работу из-за того, что браузер не открыл формат.
    async function prepare(file) {
        var t = getTurns(file);
        if (!t) return file;
        try { return await rotate(file, t); } catch (_) { return file; }
    }

    // По одному, а не разом: двадцать canvas по 2400 px одновременно телефон
    // может не вытянуть по памяти.
    async function prepareAll(files) {
        var out = [];
        var list = Array.from(files || []);
        for (var i = 0; i < list.length; i++) out.push(await prepare(list[i]));
        return out;
    }

    // Кнопка ⟳ в ячейке превью. Показывается, только когда превью
    // нарисовалось: не открыл браузер фото (HEIC в Chrome) — крутить нечего,
    // уйдёт как есть. `<img>` превью может появиться позже (FileReader).
    function attachRotate(cell, file) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'preview-rotate';
        btn.setAttribute('aria-label', 'Повернуть фото');
        btn.title = 'Повернуть';
        btn.textContent = '⟳';
        btn.hidden = true;

        function apply() {
            var img = cell.querySelector('img');
            if (img) img.style.transform = 'rotate(' + (getTurns(file) * 90) + 'deg)';
        }
        function watch(img) {
            function ready() { btn.hidden = false; apply(); }
            if (img.complete && img.naturalWidth) ready();
            else img.addEventListener('load', ready, { once: true });
        }

        btn.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            setTurns(file, getTurns(file) + 1);
            apply();
        });
        cell.appendChild(btn);

        var img = cell.querySelector('img');
        if (img) {
            watch(img);
        } else {
            var mo = new MutationObserver(function () {
                var found = cell.querySelector('img');
                if (found) { mo.disconnect(); watch(found); }
            });
            mo.observe(cell, { childList: true });
        }
        return btn;
    }

    window.PhotoPrep = {
        loadImage: loadImage,
        canvasToBlob: canvasToBlob,
        shrink: shrink,
        rotate: rotate,
        getTurns: getTurns,
        setTurns: setTurns,
        prepare: prepare,
        prepareAll: prepareAll,
        attachRotate: attachRotate
    };
})();
