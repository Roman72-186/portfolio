// Service Worker для Web Push (Фаза 3). Отдаётся не из /static/, а отдельным
// роутом GET /sw.js с заголовком Service-Worker-Allowed: /, чтобы область
// действия покрывала весь origin, а не только /static/*.
//
// Namespace 'push' у sw.js уникален для всего сайта — держать этот файл
// минимальным (только push + click), не подключать сюда офлайн-кэш/precache:
// у Apparchi нет offline-режима, а расширение области ответственности этого
// воркера рискует зацепить видео/3D-лабораторию, у которых свои кэш-стратегии
// на уровне HTTP-заголовков (см. app/main.py::cache_control).

self.addEventListener('push', function (event) {
    let payload = { title: 'Apparchi', body: '' };
    if (event.data) {
        try {
            payload = event.data.json();
        } catch (e) {
            payload.body = event.data.text();
        }
    }
    const title = payload.title || 'Apparchi';
    const options = {
        body: payload.body || '',
        icon: payload.icon || '/static/img/logo-192.png',
        badge: payload.badge || '/static/img/logo-192.png',
        data: { url: payload.url || '/cabinet/notifications' },
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    const url = (event.notification.data && event.notification.data.url) || '/cabinet/notifications';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
            for (const client of clientList) {
                if (client.url.includes(url) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});
