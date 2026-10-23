// Поиск ученика по номеру на платформе (владелец 14.10.2026: «добавить везде
// поиск по id»). Запрос из одних цифр — «256», «#256», «№ 256», «id 256» —
// ищет ученика с этим номером и только его: в имени цифр не бывает, ник в
// Telegram с цифры не начинается. Python-двойник — `services/student_search.py`.
// Подключён в base.html, поэтому доступен на любой странице кабинета.
(function () {
    var ID_QUERY = /^\s*(?:[#№]\s*|id\s*)?(\d{1,9})\s*$/i;

    // Номер из запроса или null, если запрос — не номер.
    window.studentIdFromQuery = function (query) {
        var match = ID_QUERY.exec(String(query || ''));
        return match ? parseInt(match[1], 10) : null;
    };

    function normalize(value) {
        var text = String(value || '');
        if (typeof text.normalize === 'function') text = text.normalize('NFKC');
        return text.toLowerCase().replace(/ё/g, 'е').trim().replace(/\s+/g, ' ');
    }

    // Подходит ли ученик под запрос — двойник `student_search.student_matches`:
    // номер — точно, иначе каждое слово запроса (без ведущего «@») есть в
    // имени или нике. `haystack` — имя и ник одной строкой. Пустой запрос
    // подходит всем.
    window.studentMatchesQuery = function (query, id, haystack) {
        var queryId = window.studentIdFromQuery(query);
        if (queryId !== null) return Number(id) === queryId;
        var text = normalize(haystack).replace(/@/g, '');
        return normalize(query).split(' ').every(function (token) {
            token = token.replace(/^@+/, '');
            return !token || text.indexOf(token) !== -1;
        });
    };
})();
