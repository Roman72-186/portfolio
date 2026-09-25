/*
 * Запись голосового и «кружка» прямо в браузере (владелец 25.09.2026:
 * «чтобы можно было записать голосовое и даже кружок как в тг»). Записывает
 * только преподаватель — контейнер рисуется лишь в staff-ветках шаблонов,
 * а сервер дополнительно игнорирует флаг кружка от ученика.
 *
 * Разметка: пустой `<div data-media-recorder>`. Скрипт сам наполняет его
 * кнопками, в том числе у контейнеров, появившихся позже (блоки конструктора
 * добавляются на лету) — за этим следит MutationObserver.
 *
 * Атрибуты контейнера:
 *   data-mrf-modes="voice note"   — какие кнопки показать (по умолчанию обе);
 *   data-mrf-upload-url="/..."    — режим «сразу загрузить»: файл уходит POST-ом
 *                                   на этот адрес, ответ {ok, url, path, kind}
 *                                   кладётся в data-media-url/-path/-kind.
 *                                   Без атрибута — режим формы: файл кладётся
 *                                   в `input[type=file][name=audio|video]`
 *                                   ближайшей формы, у кружка ещё и
 *                                   `video_note=1`, так что форма и роут
 *                                   отправки остаются прежними;
 *   data-mrf-csrf                 — токен для режима загрузки (иначе берётся
 *                                   первый `input[name=csrf_token]`);
 *   data-media-url/-kind          — уже сохранённая запись (редактор блока).
 *
 * Пока идёт запись или загрузка, на контейнере стоит `data-mrf-busy`: по нему
 * конструктор не даёт сохранить задание (иначе блок ушёл бы без файла и
 * пропал), а форма переписки — отправить сообщение без записи.
 *
 * По нажатию «Записать…» камера/микрофон захватываются сразу, но сама запись
 * стартует только по отдельной кнопке «Начать запись» — стадия «подготовка»
 * (владелец 25.09.2026): у кружка виден живой кадр, у голосового — индикатор
 * уровня микрофона. «Отмена» с этой стадии освобождает устройство, ничего не
 * записав.
 *
 * Формат выбирает браузер: Chrome/Firefox пишут webm, Safari — mp4. Имя
 * файла получает расширение под фактический формат: сервер узнаёт тип и по
 * расширению, а путь в S3 без расширения получил бы `.jpg`.
 *
 * «Прикрепить аудио/видео» рядом с каждой кнопкой «Записать…» (владелец
 * 25.09.2026: «чтобы можно было загрузить голосовое в любом формате и
 * видео») — путь для готового файла, а не только для того, что умеет
 * записать сам браузер. Устройство при этом не занимается: файл сразу идёт
 * в тот же useFile(), что и результат записи. Формат и размер проверяет
 * только сервер (read_audio_upload/read_video_upload, app/services/feedback.py) —
 * он шире, чем то, что пишет MediaRecorder: mp3/wav/m4a/aac/amr и
 * mp4/mov/avi/mkv/wmv, а не только webm/mp4.
 */
(function () {
    'use strict';
    if (window.MediaRecorderField) return;

    // Кружок как в Telegram — до минуты. Голосовое — до десяти минут:
    // при 64 кбит/с это около 5 МБ, с запасом от лимита сервера в 25 МБ.
    var LIMIT_SECONDS = {voice: 600, note: 60};
    var MIME_CANDIDATES = {
        voice: ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'],
        note: ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4']
    };
    var START_LABELS = {voice: 'Записать голосовое', note: 'Записать кружок'};
    // «Прикрепить файл» (владелец 25.09.2026: «чтобы можно было загрузить
    // голосовое в любом формате и видео») — рядом с живой записью, не вместо
    // неё. `accept` только подсказка для системного диалога выбора файла,
    // настоящую проверку формата и размера всегда делает сервер
    // (`read_audio_upload`/`read_video_upload`, app/services/feedback.py) —
    // он уже понимает mp3/wav/m4a/aac/amr и mp4/mov/avi/mkv/wmv, а не только
    // webm/mp4, которые пишет сам браузер через MediaRecorder.
    var ATTACH_LABELS = {voice: 'Прикрепить аудио', note: 'Прикрепить видео'};
    var ATTACH_ACCEPT = {voice: 'audio/*', note: 'video/*'};

    function isSupported() {
        return !!(window.MediaRecorder && navigator.mediaDevices
            && navigator.mediaDevices.getUserMedia);
    }

    function pickMime(kind) {
        if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return '';
        var list = MIME_CANDIDATES[kind];
        for (var i = 0; i < list.length; i++) {
            if (MediaRecorder.isTypeSupported(list[i])) return list[i];
        }
        return '';
    }

    function extensionFor(kind, mime) {
        var base = String(mime || '').split(';')[0];
        if (base.indexOf('mp4') !== -1) return kind === 'voice' ? 'm4a' : 'mp4';
        if (base.indexOf('ogg') !== -1) return 'ogg';
        return 'webm';
    }

    function formatTime(seconds) {
        var s = Math.max(0, Math.floor(seconds));
        var m = Math.floor(s / 60);
        var rest = s % 60;
        return m + ':' + (rest < 10 ? '0' : '') + rest;
    }

    function errorText(err, kind) {
        var name = err && err.name;
        if (name === 'NotAllowedError' || name === 'SecurityError') {
            return kind === 'voice'
                ? 'Нет доступа к микрофону. Разрешите его для этого сайта в настройках браузера и попробуйте ещё раз.'
                : 'Нет доступа к камере и микрофону. Разрешите их для этого сайта в настройках браузера и попробуйте ещё раз.';
        }
        if (name === 'NotFoundError' || name === 'OverconstrainedError') {
            return kind === 'voice' ? 'Микрофон не найден.' : 'Камера не найдена.';
        }
        if (name === 'NotReadableError') {
            return 'Микрофон или камера заняты другой программой. Закройте её и попробуйте ещё раз.';
        }
        return 'Не получилось начать запись. Можно прикрепить готовый файл.';
    }

    function markup(modes) {
        // Пара на каждый вид записи: «Записать голосовое» рядом с «Прикрепить
        // аудио», «Записать кружок» рядом с «Прикрепить видео» (владелец
        // 25.09.2026). Раньше шли обе записи, потом оба прикрепления, и на
        // узком экране кнопки растягивались на всю ширину — пары рассыпались.
        // Скрытый `input[type=file]` внутри пары колонку не занимает:
        // `.mrf [hidden] { display: none }` в media-recorder.css.
        var pairs = modes.map(function (kind) {
            return '<div class="mrf-row mrf-pair">'
                + '<button type="button" class="btn-outline mrf-btn" data-mrf-start="' + kind + '">'
                + START_LABELS[kind] + '</button>'
                + '<button type="button" class="btn-outline mrf-btn" data-mrf-attach="' + kind + '">'
                + ATTACH_LABELS[kind] + '</button>'
                + '<input type="file" class="mrf-file-input" data-mrf-file="' + kind + '" accept="' + ATTACH_ACCEPT[kind] + '" hidden>'
                + '</div>';
        }).join('');
        return ''
            + '<div class="mrf-idle" data-mrf-idle>' + pairs + '</div>'
            // Между нажатием «Записать…» и самой записью — стадия «подготовка»
            // (владелец 25.09.2026: раньше запись стартовала сразу по
            // getUserMedia, без паузы посмотреть в кадр или проверить микрофон).
            // Камера/микрофон уже захвачены (видео и индикатор ниже — общие
            // на подготовку и саму запись), а таймер и MediaRecorder стартуют
            // только по «Начать запись».
            + '<div class="mrf-stage" data-mrf-active hidden>'
            + '  <video class="mrf-circle mrf-circle--live" data-mrf-live-video muted playsinline hidden></video>'
            + '  <div class="mrf-mic-meter" data-mrf-mic-meter hidden><span class="mrf-mic-bar" data-mrf-mic-bar></span></div>'
            + '  <div class="mrf-row" data-mrf-ready-controls hidden>'
            + '    <span class="mrf-hint" data-mrf-ready-hint></span>'
            + '    <button type="button" class="btn-blue mrf-btn" data-mrf-begin>Начать запись</button>'
            + '    <button type="button" class="btn-outline mrf-btn" data-mrf-cancel>Отмена</button>'
            + '  </div>'
            + '  <div class="mrf-row" data-mrf-rec-controls hidden>'
            + '    <span class="mrf-rec-dot" aria-hidden="true"></span>'
            + '    <span class="mrf-time" data-mrf-time>0:00</span>'
            + '    <span class="mrf-hint" data-mrf-limit></span>'
            + '    <button type="button" class="btn-blue mrf-btn" data-mrf-stop>Готово</button>'
            + '    <button type="button" class="btn-outline mrf-btn" data-mrf-cancel>Отмена</button>'
            + '  </div>'
            + '</div>'
            + '<div class="mrf-stage" data-mrf-preview hidden>'
            + '  <div data-mrf-player></div>'
            + '  <div class="mrf-row">'
            + '    <span class="mrf-hint" data-mrf-status aria-live="polite"></span>'
            + '    <button type="button" class="btn-outline mrf-btn" data-mrf-redo>Переписать</button>'
            + '    <button type="button" class="btn-outline mrf-btn" data-mrf-remove>Удалить</button>'
            + '  </div>'
            + '</div>'
            + '<p class="mrf-error" data-mrf-error role="alert" hidden></p>';
    }

    function Recorder(root) {
        this.root = root;
        this.uploadUrl = root.getAttribute('data-mrf-upload-url') || '';
        var modes = (root.getAttribute('data-mrf-modes') || 'voice note')
            .split(/\s+/).filter(function (m) { return LIMIT_SECONDS[m]; });
        root.classList.add('mrf');
        root.innerHTML = markup(modes.length ? modes : ['voice', 'note']);
        this.stream = null;
        this.recorder = null;
        this.chunks = [];
        this.kind = null;
        this.timer = null;
        this.startedAt = 0;
        this.cancelled = false;
        this.objectUrl = null;
        this.bind();

        var savedUrl = root.getAttribute('data-media-url');
        var savedKind = root.getAttribute('data-media-kind');
        if (savedUrl && LIMIT_SECONDS[savedKind]) {
            this.showPreview(savedUrl, savedKind, 'Сохранено в задании');
        }
    }

    Recorder.prototype.q = function (selector) {
        return this.root.querySelector(selector);
    };

    Recorder.prototype.bind = function () {
        var self = this;
        this.root.addEventListener('click', function (event) {
            var start = event.target.closest('[data-mrf-start]');
            if (start) { self.start(start.getAttribute('data-mrf-start')); return; }
            var attach = event.target.closest('[data-mrf-attach]');
            if (attach) { self.q('[data-mrf-file="' + attach.getAttribute('data-mrf-attach') + '"]').click(); return; }
            if (event.target.closest('[data-mrf-begin]')) { self.beginRecording(); return; }
            if (event.target.closest('[data-mrf-stop]')) { self.stop(false); return; }
            if (event.target.closest('[data-mrf-cancel]')) { self.stop(true); return; }
            if (event.target.closest('[data-mrf-redo]')) {
                var kind = self.kind;
                self.clear();
                self.start(kind);
                return;
            }
            if (event.target.closest('[data-mrf-remove]')) self.clear();
        });
        // Свой `<input type=file>` на кнопку «Прикрепить…» (не тот, что у
        // формы переписки: этот — часть разметки самого компонента).
        this.root.addEventListener('change', function (event) {
            var input = event.target.closest('[data-mrf-file]');
            if (!input) return;
            var kind = input.getAttribute('data-mrf-file');
            var file = input.files && input.files[0];
            input.value = '';
            if (file) self.attachFile(kind, file);
        });
        // Преподаватель выбрал обычный файл в то же поле руками — запись
        // больше не про него: сбрасываем её и флаг кружка.
        var form = this.root.closest('form');
        if (form && !this.uploadUrl) {
            form.addEventListener('change', function (event) {
                if (event.mrfSynthetic) return;
                var input = event.target;
                if (!input || input.type !== 'file') return;
                if (input === self.inputFor(self.kind)) self.clear({keepInput: true});
            });
            // Фаза захвата: срабатывает раньше обработчика отправки страницы.
            form.addEventListener('submit', function (event) {
                if (!self.root.hasAttribute('data-mrf-busy')) return;
                event.preventDefault();
                event.stopImmediatePropagation();
                self.setError('Запись ещё идёт. Нажмите «Готово», потом отправьте сообщение.');
            }, true);
        }
    };

    Recorder.prototype.show = function (state) {
        this.q('[data-mrf-idle]').hidden = state !== 'idle';
        this.q('[data-mrf-active]').hidden = state !== 'ready' && state !== 'live';
        this.q('[data-mrf-ready-controls]').hidden = state !== 'ready';
        this.q('[data-mrf-rec-controls]').hidden = state !== 'live';
        this.q('[data-mrf-preview]').hidden = state !== 'preview';
    };

    Recorder.prototype.setError = function (text) {
        var box = this.q('[data-mrf-error]');
        box.textContent = text || '';
        box.hidden = !text;
    };

    Recorder.prototype.setBusy = function (busy) {
        if (busy) this.root.setAttribute('data-mrf-busy', '');
        else this.root.removeAttribute('data-mrf-busy');
    };

    Recorder.prototype.setStatus = function (text) {
        this.q('[data-mrf-status]').textContent = text || '';
    };

    Recorder.prototype.start = function (kind) {
        var self = this;
        this.setError('');
        if (!isSupported()) {
            this.setError('Запись в этом браузере недоступна. Можно прикрепить готовый файл.');
            return;
        }
        var constraints = kind === 'voice'
            ? {audio: true}
            : {audio: true, video: {facingMode: 'user', width: {ideal: 480}, height: {ideal: 480}}};
        navigator.mediaDevices.getUserMedia(constraints).then(function (stream) {
            self.prepare(kind, stream);
        }).catch(function (err) {
            self.setError(errorText(err, kind));
        });
    };

    // Камера/микрофон захвачены, но запись ещё не идёт — стадия «подготовка»
    // (владелец 25.09.2026, ответ на вопрос «кнопка или отсчёт» — кнопка: сам
    // решает, когда готов, без отсчёта на фоне). У кружка живой кадр с камеры,
    // у голосового — индикатор уровня микрофона; в обоих случаях дальше идёт
    // «Начать запись» (см. beginRecording) или «Отмена» (releaseStream через stop).
    Recorder.prototype.prepare = function (kind, stream) {
        this.kind = kind;
        this.stream = stream;
        this.cancelled = false;
        this.setBusy(true);
        var live = this.q('[data-mrf-live-video]');
        live.hidden = kind !== 'note';
        if (kind === 'note') {
            live.srcObject = stream;
            var playing = live.play();
            if (playing && playing.catch) playing.catch(function () {});
        } else {
            this.startMicMeter(stream);
        }
        this.q('[data-mrf-ready-hint]').textContent = kind === 'voice'
            ? 'Проверьте микрофон и нажмите «Начать запись»'
            : 'Проверьте кадр и нажмите «Начать запись»';
        this.show('ready');
    };

    Recorder.prototype.beginRecording = function () {
        var self = this;
        var kind = this.kind;
        this.chunks = [];
        var mime = pickMime(kind);
        var options = {audioBitsPerSecond: 64000};
        if (mime) options.mimeType = mime;
        if (kind === 'note') options.videoBitsPerSecond = 1000000;
        try {
            this.recorder = new MediaRecorder(this.stream, options);
        } catch (e) {
            this.recorder = new MediaRecorder(this.stream);
        }
        this.recorder.ondataavailable = function (event) {
            if (event.data && event.data.size) self.chunks.push(event.data);
        };
        this.recorder.onstop = function () { self.finish(mime); };

        this.q('[data-mrf-limit]').textContent = 'из ' + formatTime(LIMIT_SECONDS[kind]);
        this.q('[data-mrf-time]').textContent = '0:00';
        this.show('live');
        this.startedAt = Date.now();
        this.recorder.start(1000);
        this.timer = window.setInterval(function () {
            var elapsed = (Date.now() - self.startedAt) / 1000;
            self.q('[data-mrf-time]').textContent = formatTime(elapsed);
            if (elapsed >= LIMIT_SECONDS[kind]) self.stop(false);
        }, 250);
    };

    // Уровень микрофона на подготовке к голосовому — тот же живой сигнал, что
    // у кадра камеры кружка: подтверждает, что запись возьмёт звук, до того
    // как жать «Начать запись». AnalyserNode, не MediaRecorder — тут не пишем.
    Recorder.prototype.startMicMeter = function (stream) {
        var self = this;
        var bar = this.q('[data-mrf-mic-bar]');
        var meter = this.q('[data-mrf-mic-meter]');
        try {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            this.audioCtx = new Ctx();
            var source = this.audioCtx.createMediaStreamSource(stream);
            var analyser = this.audioCtx.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            this.micAnalyser = analyser;
            meter.hidden = false;
            var data = new Uint8Array(analyser.frequencyBinCount);
            (function tick() {
                if (!self.micAnalyser) return;
                analyser.getByteFrequencyData(data);
                var sum = 0;
                for (var i = 0; i < data.length; i++) sum += data[i];
                var level = Math.max(0.06, Math.min(1, (sum / data.length) / 80));
                bar.style.transform = 'scaleX(' + level + ')';
                self.micMeterFrame = window.requestAnimationFrame(tick);
            })();
        } catch (e) {
            meter.hidden = true;
        }
    };

    Recorder.prototype.stopMicMeter = function () {
        if (this.micMeterFrame) {
            window.cancelAnimationFrame(this.micMeterFrame);
            this.micMeterFrame = null;
        }
        this.micAnalyser = null;
        if (this.audioCtx) {
            try { this.audioCtx.close(); } catch (e) {}
            this.audioCtx = null;
        }
        var meter = this.q('[data-mrf-mic-meter]');
        if (meter) meter.hidden = true;
        var bar = this.q('[data-mrf-mic-bar]');
        if (bar) bar.style.transform = '';
    };

    Recorder.prototype.releaseStream = function () {
        window.clearInterval(this.timer);
        this.timer = null;
        this.stopMicMeter();
        if (this.stream) {
            this.stream.getTracks().forEach(function (track) { track.stop(); });
        }
        this.stream = null;
        var live = this.q('[data-mrf-live-video]');
        live.srcObject = null;
        live.hidden = true;
    };

    Recorder.prototype.stop = function (cancel) {
        this.cancelled = !!cancel;
        if (this.recorder && this.recorder.state !== 'inactive') {
            this.recorder.stop();
        } else {
            this.releaseStream();
            this.setBusy(false);
            this.show('idle');
        }
    };

    Recorder.prototype.finish = function (requestedMime) {
        this.releaseStream();
        if (this.cancelled) { this.setBusy(false); this.show('idle'); return; }
        var type = (this.recorder && this.recorder.mimeType) || requestedMime
            || (this.kind === 'voice' ? 'audio/webm' : 'video/webm');
        var blob = new Blob(this.chunks, {type: type});
        this.chunks = [];
        if (!blob.size) {
            this.setBusy(false);
            this.show('idle');
            this.setError('Запись получилась пустой. Попробуйте ещё раз.');
            return;
        }
        var name = (this.kind === 'voice' ? 'voice' : 'circle') + '-' + Date.now()
            + '.' + extensionFor(this.kind, type);
        this.useFile(this.kind, new File([blob], name, {type: type}));
    };

    // Готовый файл — записанный только что (finish) или выбранный через
    // «Прикрепить…» (attachFile) — от этой точки идёт одним путём: либо сразу
    // на сервер (конструктор), либо в скрытое поле формы (переписка).
    Recorder.prototype.useFile = function (kind, file) {
        this.kind = kind;
        if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
        this.objectUrl = URL.createObjectURL(file);
        if (this.uploadUrl) {
            this.showPreview(this.objectUrl, kind, 'Загружаю…');
            this.upload(file);
        } else {
            this.attachToForm(file);
            this.setBusy(false);
            this.showPreview(this.objectUrl, kind, 'Уйдёт вместе с сообщением');
        }
    };

    // «Прикрепить аудио/видео» (владелец 25.09.2026) — файл уже готов, писать
    // нечего: устройство не занимаем, стадию «подготовка»/«запись» не
    // показываем, сразу в useFile. `data-mrf-busy` всё равно ставим — до
    // ответа сервера (или до `attachToForm` в форме переписки) блок не готов.
    Recorder.prototype.attachFile = function (kind, file) {
        this.setError('');
        this.setBusy(true);
        this.useFile(kind, file);
    };

    Recorder.prototype.showPreview = function (url, kind, status) {
        this.kind = kind;
        var player = this.q('[data-mrf-player]');
        player.innerHTML = '';
        var media = document.createElement(kind === 'voice' ? 'audio' : 'video');
        media.controls = true;
        media.preload = 'metadata';
        if (kind === 'note') {
            media.className = 'mrf-circle';
            media.setAttribute('playsinline', '');
        } else {
            media.className = 'mrf-audio';
        }
        media.src = url;
        player.appendChild(media);
        this.setStatus(status);
        this.show('preview');
    };

    Recorder.prototype.inputFor = function (kind) {
        var form = this.root.closest('form');
        if (!form || !kind) return null;
        return form.querySelector('input[type=file][name="' + (kind === 'voice' ? 'audio' : 'video') + '"]');
    };

    Recorder.prototype.noteField = function (create) {
        var form = this.root.closest('form');
        if (!form) return null;
        var field = form.querySelector('input[name="video_note"]');
        if (!field && create) {
            field = document.createElement('input');
            field.type = 'hidden';
            field.name = 'video_note';
            form.appendChild(field);
        }
        return field;
    };

    Recorder.prototype.setInputFile = function (input, file) {
        if (!input) return;
        try {
            var transfer = new DataTransfer();
            if (file) transfer.items.add(file);
            input.files = transfer.files;
        } catch (e) {
            // Старый браузер без конструктора DataTransfer: подставим файл
            // в FormData в момент отправки.
            this.fallbackFile = file;
            this.fallbackName = input.name;
            if (!file) input.value = '';
        }
        var changed = new Event('change', {bubbles: true});
        changed.mrfSynthetic = true;
        input.dispatchEvent(changed);
    };

    Recorder.prototype.attachToForm = function (file) {
        var self = this;
        var form = this.root.closest('form');
        this.setInputFile(this.inputFor(this.kind), file);
        var note = this.noteField(this.kind === 'note');
        if (note) note.value = this.kind === 'note' ? '1' : '';
        if (form && !this.formDataHooked) {
            this.formDataHooked = true;
            form.addEventListener('formdata', function (event) {
                if (self.fallbackFile) {
                    event.formData.set(self.fallbackName, self.fallbackFile, self.fallbackFile.name);
                }
            });
        }
    };

    Recorder.prototype.upload = function (file) {
        var self = this;
        var csrf = this.root.getAttribute('data-mrf-csrf');
        if (!csrf) {
            var field = document.querySelector('input[name="csrf_token"]');
            csrf = field ? field.value : '';
        }
        var data = new FormData();
        data.append('file', file, file.name);
        data.append('kind', this.kind);
        data.append('csrf_token', csrf);
        var send = window.fetchWithTimeout || function (url, options) { return fetch(url, options); };
        // 'Accept: application/json' — без него сервер на 403/401/500 отдаёт
        // HTML-страницу (см. app/main.py, обработчики этих кодов смотрят на
        // Accept и Content-Type), fetch не может её разобрать, и вместо
        // настоящей причины пользователь видел общий текст ошибки.
        send(this.uploadUrl, {
            method: 'POST', credentials: 'same-origin',
            headers: {'Accept': 'application/json', 'X-CSRF-Token': csrf}, body: data
        }, 300000).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
                if (!response.ok || !body.ok || !body.url) {
                    throw new Error(body.error || body.detail || 'Не получилось сохранить запись. Попробуйте ещё раз.');
                }
                return body;
            });
        }).then(function (body) {
            self.root.setAttribute('data-media-url', body.url);
            self.root.setAttribute('data-media-path', body.path || '');
            self.root.setAttribute('data-media-kind', body.kind || self.kind);
            self.setBusy(false);
            self.setStatus('Сохранится вместе с заданием');
        }).catch(function (err) {
            self.clear();
            self.setError((err && err.message) || 'Не получилось сохранить запись. Попробуйте ещё раз.');
        });
    };

    Recorder.prototype.clear = function (options) {
        var keepInput = options && options.keepInput;
        if (!keepInput && !this.uploadUrl && this.kind) {
            this.setInputFile(this.inputFor(this.kind), null);
        }
        if (!this.uploadUrl) {
            var note = this.noteField(false);
            if (note) note.value = '';
        }
        this.fallbackFile = null;
        this.setBusy(false);
        this.root.removeAttribute('data-media-url');
        this.root.removeAttribute('data-media-path');
        this.root.removeAttribute('data-media-kind');
        this.q('[data-mrf-player]').innerHTML = '';
        if (this.objectUrl) {
            URL.revokeObjectURL(this.objectUrl);
            this.objectUrl = null;
        }
        this.setStatus('');
        this.setError('');
        this.show('idle');
    };

    function enhance(root) {
        if (!root || root._mrf) return;
        root._mrf = new Recorder(root);
    }

    function enhanceAll(scope) {
        var base = scope || document;
        if (base.matches && base.matches('[data-media-recorder]')) enhance(base);
        if (base.querySelectorAll) base.querySelectorAll('[data-media-recorder]').forEach(enhance);
    }

    window.MediaRecorderField = {enhance: enhance, enhanceAll: enhanceAll, isSupported: isSupported};

    function boot() {
        enhanceAll(document);
        if (!window.MutationObserver) return;
        new MutationObserver(function (records) {
            records.forEach(function (record) {
                record.addedNodes.forEach(function (node) {
                    if (node.nodeType === 1) enhanceAll(node);
                });
            });
        }).observe(document.body, {childList: true, subtree: true});
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
