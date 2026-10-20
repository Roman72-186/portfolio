/*
Нажатие «Подключиться» у события дайджеста — запись в статистику ученика
(владелец 20.10.2026; правило — services/meeting_joins.py).

Ссылку открывает сам браузер, как и раньше: скрипт её не задерживает и не
подменяет, а только отправляет отметку рядом. Не дошла отметка — ученик этого
не заметит, занятие откроется всё равно.

Кнопки помечены `data-meeting-join="<номер события>"`: в списке «События
месяца» (partials/digest_list.html) и в окне дня (digest-day-pop.js — оно
собирает кнопки на лету, поэтому слушатель один на документ).

Ключ CSRF берётся заранее, на касании: ссылка уводит на созвон, и вкладка
может уйти в фон раньше, чем ответит `GET /csrf`. С ключом в кэше остаётся
один запрос, и `keepalive` даёт ему дожить до конца.
*/
(function () {
    function joinLink(target) {
        return target && target.closest ? target.closest('[data-meeting-join]') : null;
    }

    document.addEventListener('pointerdown', function (event) {
        if (joinLink(event.target) && window.csrfFresh) window.csrfFresh();
    }, {passive: true});

    document.addEventListener('click', function (event) {
        var link = joinLink(event.target);
        if (!link || !window.csrfFetch) return;
        var eventId = link.getAttribute('data-meeting-join');
        if (!/^\d+$/.test(eventId || '')) return;
        window.csrfFetch('/cabinet/tracker/digest/events/' + eventId + '/join', {
            method: 'POST',
            keepalive: true
        }).catch(function () {});
    });
})();
