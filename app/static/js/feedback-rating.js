/* «Завершить ОС» и оценка ученика — ОС, фаза 2 (01.10.2026).
   Разметка — `partials/feedback_rating.html`, роуты — `api/task_block_feedback.py`
   и `api/feedback.py`. Оба роута отвечают JSON с `error` при отказе. */
(function () {
    function send(url, body) {
        return window.csrfFetch(url, {
            method: 'POST',
            body: body
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok || !data || data.ok !== true) {
                    throw new Error(data.error || data.detail || 'Не получилось. Обнови страницу и попробуй ещё раз.');
                }
                return data;
            });
        });
    }

    function showError(node, text) {
        node.className = 'alert alert-error';
        node.textContent = text;
    }

    var close = document.querySelector('[data-feedback-close]');
    if (close) {
        var closeButton = close.querySelector('button');
        var closeStatus = close.querySelector('[data-feedback-close-status]');
        closeButton.addEventListener('click', function () {
            if (!window.confirm('Завершить обратную связь? Диалог закроется для вас обоих, открыть его снова нельзя.')) return;
            closeButton.disabled = true;
            closeStatus.className = 'card-desc';
            closeStatus.textContent = 'Завершаем обратную связь…';
            send(close.dataset.url, null)
                .then(function () { window.location.reload(); })
                .catch(function (error) {
                    closeButton.disabled = false;
                    showError(closeStatus, error.message);
                });
        });
    }

    var form = document.querySelector('[data-rating-form]');
    if (!form) return;
    var status = form.querySelector('[data-rating-status]');
    var files = form.querySelector('input[name="screenshots"]');
    var names = form.querySelector('[data-rating-files]');
    var maxFiles = Number(form.dataset.maxScreenshots) || 3;
    var namesDefault = names.textContent;

    files.addEventListener('change', function () {
        var picked = Array.prototype.map.call(files.files, function (file) { return file.name; });
        names.textContent = picked.length ? picked.join(', ') : namesDefault;
    });

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        var button = form.querySelector('button[type=submit]');
        if (button.disabled) return;
        if (!form.querySelector('input[name="score"]:checked')) {
            showError(status, 'Выбери оценку от 1 до 5.');
            return;
        }
        if (!form.elements.comment.value.trim()) {
            showError(status, 'Напиши комментарий – без него оценку не отправить.');
            form.elements.comment.focus();
            return;
        }
        if (files.files.length > maxFiles) {
            showError(status, 'Можно приложить до ' + maxFiles + ' скриншотов.');
            return;
        }
        button.disabled = true;
        status.className = 'card-desc';
        status.textContent = 'Отправляем…';
        send(form.action, new FormData(form))
            .then(function () { window.location.reload(); })
            .catch(function (error) {
                button.disabled = false;
                showError(status, error.message);
            });
    });
})();
