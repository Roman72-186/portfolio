/*
Свежий CSRF-ключ перед мутирующим запросом — один слой на все экраны.

Зачем. Токен печатается в HTML при рендере страницы и старится вместе с ней, а
вкладка ученика на телефоне живёт сутками. За 20 часов 25-26.09.2026 прод отдал
141 отказ «Неверный CSRF-токен» на живых сессиях, и среди них — отправка работы
в задании: человек нажимал «Отправить работу» и читал «Не удалось загрузить.
Попробуй ещё раз», хотя повтор не помогал никогда. Лечение в owner-слое: перед
каждой мутацией берём токен у сервера (`GET /csrf`), а не надеемся на тот, что
лежит в разметке с утра. Тот же запрос продлевает сессию — путь `/csrf` стоит в
`_FORCE_SESSION_REFRESH_PATHS` (`app/main.py`).

Как пользоваться:

    window.csrfFetch(url, {method: 'POST', body: formData})
    window.csrfFresh().then(function (token) { ... })   // для XHR с прогрессом
    window.csrfMessage(body, 'Не удалось загрузить.')   // текст ошибки сервера
    window.csrfFailure(status, body, 'Не удалось сохранить. Попробуйте ещё раз.')
                                                        // текст отказа по коду ответа

`csrfFetch` сам ставит `Accept: application/json` — без него обработчик 403 в
`app/main.py` отдаёт HTML-страницу «Нет доступа», разбор ответа падает, и до
человека не доходит ни причина, ни совет обновить страницу.

Кэш на 5 минут: ключ живёт столько же, сколько сессия, так что пять минут для
него ничто, а прогресс видео шлётся каждые несколько секунд — без кэша каждый
такой запрос тащил бы за собой второй. На 403 делается один повтор с
принудительно свежим ключом: тот редкий случай, когда сессия сменилась под
открытой страницей.

Ключ из разметки (`meta[name="csrf-token"]`) — запасной путь: если `/csrf` не
ответил (нет сети, сессия кончилась), шлём то, что было, и пусть сервер решает.
Хуже прежнего поведения не будет.
*/
(function () {
    var CACHE_MS = 5 * 60 * 1000;
    var cached = '';
    var cachedAt = 0;
    var inflight = null;

    function pageToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return (meta && meta.getAttribute('content')) || '';
    }

    function fresh(force) {
        if (!force && cached && (Date.now() - cachedAt) < CACHE_MS) {
            return Promise.resolve(cached);
        }
        if (inflight) return inflight;
        inflight = fetch('/csrf', {
            credentials: 'same-origin',
            headers: {'Accept': 'application/json'},
            cache: 'no-store'
        }).then(function (resp) {
            if (!resp.ok) throw new Error('csrf');
            return resp.json();
        }).then(function (body) {
            cached = (body && body.csrf_token) || '';
            cachedAt = Date.now();
            inflight = null;
            return cached || pageToken();
        }).catch(function () {
            inflight = null;
            return pageToken();
        });
        return inflight;
    }

    function send(url, options, token) {
        options = options || {};
        var headers = {'Accept': 'application/json'};
        var given = options.headers || {};
        Object.keys(given).forEach(function (key) { headers[key] = given[key]; });
        headers['X-CSRF-Token'] = token;
        var opts = {};
        Object.keys(options).forEach(function (key) { opts[key] = options[key]; });
        opts.credentials = options.credentials || 'same-origin';
        opts.headers = headers;
        return fetch(url, opts);
    }

    function csrfFetch(url, options) {
        return fresh(false).then(function (token) {
            return send(url, options, token).then(function (resp) {
                if (resp.status !== 403) return resp;
                return fresh(true).then(function (retryToken) {
                    // Тот же ключ — значит отказ не про срок токена, а про
                    // права: второй такой же запрос ничего не изменит.
                    if (!retryToken || retryToken === token) return resp;
                    return send(url, options, retryToken);
                });
            });
        });
    }

    // Сервер отвечает по-разному: свои проверки кладут причину в `error`
    // (JSONResponse роутов), а HTTPException — в `detail`. Читаем оба, иначе
    // человеку достаётся общая заглушка вместо «обнови страницу».
    function csrfMessage(body, fallback) {
        if (!body) return fallback;
        return body.error || body.detail || fallback;
    }

    // Что сказать сотруднику об отказе, по коду ответа. «Попробуйте ещё раз»
    // честно только для сети и 5xx: на 401 и 403 повтор ничего не меняет.
    // Прецедент 04.10.2026: модератор с АОП на просмотр девять раз сохраняла
    // событие дайджеста и девять раз читала «Не удалось сохранить событие.
    // Попробуйте ещё раз», хотя сервер отвечал «Раздел открыт только на
    // просмотр». На 403 и 422 у сервера своя причина словами — её и
    // показываем; у 422 от проверки полей `detail` бывает списком, тогда
    // заглушка. Машинные коды вроде `type_in_use` экран разбирает сам до
    // вызова. Голос «вы»: помощник ходит по экранам персонала, у ученика свои
    // тексты (`task-blocks-render.js`).
    function csrfFailure(status, body, fallback) {
        if (status === 401) return 'Сессия закончилась. Обновите страницу и войдите заново.';
        if (status === 403 || status === 422) {
            var reason = csrfMessage(body, '');
            if (typeof reason === 'string' && reason) return reason;
        }
        return fallback;
    }

    window.csrfFresh = fresh;
    window.csrfFetch = csrfFetch;
    window.csrfMessage = csrfMessage;
    window.csrfFailure = csrfFailure;
})();
