/* Ссылка содержит только заказ Продамуса; сессия ученика остаётся на сайте. */
(function () {
    var button = document.getElementById('parent-payment-copy');
    if (!button) return;
    var status = document.getElementById('parent-payment-status');
    var fallback = document.getElementById('parent-payment-fallback');
    var input = document.getElementById('parent-payment-url');

    button.addEventListener('click', async function () {
        button.disabled = true;
        status.textContent = 'Готовим ссылку…';
        fallback.hidden = true;
        input.value = '';
        try {
            var response = await window.csrfFetch('/purchase/payment-link', {method: 'POST'});
            var body = await response.json();
            if (!response.ok) {
                status.textContent = response.status === 409
                    ? window.csrfMessage(body, 'Не удалось получить ссылку. Обнови статус покупки.')
                    : window.csrfFailure(response.status, body, 'Не удалось получить ссылку. Попробуй ещё раз.');
                return;
            }
            // Браузер может запретить буфер после сетевого запроса: оставляем ручное копирование.
            try {
                await copyText(body.url);
                status.textContent = 'Ссылка скопирована. Отправь её родителю.';
            } catch (error) {
                input.value = body.url;
                fallback.hidden = false;
                input.focus();
                input.select();
                status.textContent = 'Браузер не разрешил копирование. Скопируй ссылку из поля ниже.';
            }
        } catch (error) {
            status.textContent = 'Не удалось получить ссылку. Проверь соединение и попробуй ещё раз.';
        } finally {
            button.disabled = false;
        }
    });
})();
