(function () {
    function applicationKey(value) {
        var padding = '='.repeat((4 - value.length % 4) % 4);
        return Uint8Array.from(atob((value + padding).replace(/-/g, '+').replace(/_/g, '/')),
            function (char) { return char.charCodeAt(0); });
    }

    async function post(path, body) {
        var response = await window.csrfFetch(path, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
            cache: 'no-store'
        });
        if (!response.ok || (await response.json()).ok !== true) throw new Error();
    }

    function readyWithin(promise) {
        var timer;
        var deadline = new Promise(function (_, reject) {
            timer = setTimeout(reject, 15000);
        });
        return Promise.race([promise, deadline]).finally(function () { clearTimeout(timer); });
    }

    window.mountPushToggle = async function (input, note, vapid, supportMessage) {
        if (!input || !note) return;
        note.setAttribute('role', 'status');
        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
            note.textContent = supportMessage;
            return;
        }
        if (!vapid) { note.textContent = 'Push пока не настроен на сервере.'; return; }
        if (Notification.permission === 'denied') {
            note.textContent = 'Уведомления заблокированы в настройках браузера.';
            return;
        }

        var manager;
        try {
            var registration = await readyWithin(navigator.serviceWorker.register('/sw.js').then(function () {
                return navigator.serviceWorker.ready;
            }));
            manager = registration.pushManager;
            input.checked = !!(await readyWithin(manager.getSubscription()));
        } catch (_) {
            note.textContent = 'Не удалось подготовить уведомления. Обнови страницу, чтобы повторить.';
            return;
        }

        function showCurrent() {
            note.textContent = input.checked ? 'Включены на этом устройстве.' : 'Выключены на этом устройстве.';
        }
        input.disabled = false;
        showCurrent();
        input.addEventListener('change', async function () {
            var enable = input.checked;
            input.disabled = true;
            note.textContent = enable ? 'Включаем уведомления…' : 'Выключаем уведомления…';
            var created = null;
            try {
                if (enable && await Notification.requestPermission() !== 'granted') {
                    input.checked = false;
                    note.textContent = 'Разрешение на уведомления не выдано.';
                    return;
                }
                var active = await manager.getSubscription();
                if (enable) {
                    if (!active) {
                        created = await manager.subscribe({userVisibleOnly: true, applicationServerKey: applicationKey(vapid)});
                        active = created;
                    }
                    await post('/cabinet/push/subscribe', active.toJSON());
                } else if (active) {
                    await post('/cabinet/push/unsubscribe', {endpoint: active.endpoint});
                    try {
                        if (!await active.unsubscribe() && await manager.getSubscription()) throw new Error();
                    } catch (error) {
                        await post('/cabinet/push/subscribe', active.toJSON()).catch(function () {});
                        throw error;
                    }
                }
                showCurrent();
            } catch (_) {
                if (created) await created.unsubscribe().catch(function () {});
                input.checked = !enable;
                note.textContent = enable ? 'Не удалось включить уведомления. Попробуй ещё раз.'
                    : 'Не удалось выключить уведомления. Попробуй ещё раз.';
            } finally {
                input.disabled = false;
            }
        });
    };
})();
