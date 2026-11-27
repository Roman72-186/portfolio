/* Ручное завершение обычного задания живёт в «Обучении». Трекер остаётся
   обзорным экраном и этого обработчика не подключает. */
(function () {
    var script = document.currentScript;
    var csrfToken = script ? script.getAttribute('data-csrf-token') : '';

    // Подпись рядом с кнопкой — тот же вид, что у серверной причины
    // (`partials/task_completion_button.html`), текст остаётся на экране.
    function showNote(button, text) {
        var id = 'task-completion-note-' + button.getAttribute('data-toggle-task');
        var note = document.getElementById(id);
        if (!note) {
            note = document.createElement('p');
            note.className = 'lrn-card-note';
            note.id = id;
            note.setAttribute('role', 'status');
            button.parentNode.insertBefore(note, button);
            button.setAttribute('aria-describedby', id);
        }
        note.textContent = text;
        return note;
    }

    var feed = document.querySelector('.lrn-feed');
    if (!feed) return;
    feed.addEventListener('click', function (event) {
            var button = event.target.closest('[data-toggle-task]');
            if (!button || button.disabled) return;
            if (!window.confirm('Завершить задание? Отменить это действие не получится.')) return;

            var taskId = button.getAttribute('data-toggle-task');
            var originalText = button.textContent;
            button.disabled = true;
            button.textContent = 'Завершаем…';

            fetch('/cabinet/tracker/tasks/' + taskId + '/toggle', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Accept': 'application/json', 'X-CSRF-Token': csrfToken},
                cache: 'no-store'
            }).then(function (response) {
                if (response.ok) return response.json();
                // Отказ сервера — причина словами («Сначала ответь на вопросы
                // задания», «Цикл пройден…»). До 30.09.2026 её выбрасывали, кнопка
                // писала «не получилось, ещё раз» — и ученик жал снова.
                return response.json().catch(function () { return {}; }).then(function (data) {
                    var error = new Error();
                    error.detail = data && typeof data.detail === 'string' ? data.detail : '';
                    throw error;
                });
            }).then(function () {
                var note = showNote(button, 'Задание сохранено.');
                button.textContent = 'Задание выполнено';
                feed.dispatchEvent(new CustomEvent('lrn-task-saved', {detail: {hint: note}}));
            }).catch(function (error) {
                button.disabled = false;
                button.textContent = originalText;
                showNote(button, (error && error.detail) || 'Не получилось. Попробуй ещё раз');
            });
    });
})();
