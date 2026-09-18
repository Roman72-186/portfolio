/* Ручное завершение обычного задания живёт в «Обучении». Трекер остаётся
   обзорным экраном и этого обработчика не подключает. */
(function () {
    var script = document.currentScript;
    var csrfToken = script ? script.getAttribute('data-csrf-token') : '';

    document.querySelectorAll('[data-toggle-task]').forEach(function (button) {
        if (button.disabled) return;
        button.addEventListener('click', function () {
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
                if (!response.ok) throw new Error();
                return response.json();
            }).then(function () {
                window.location.reload();
            }).catch(function () {
                button.disabled = false;
                button.textContent = 'Не получилось. Попробовать ещё раз';
                window.setTimeout(function () {
                    if (!button.disabled) button.textContent = originalText;
                }, 3000);
            });
        });
    });
})();
