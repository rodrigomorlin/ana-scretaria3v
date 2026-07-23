// Service Worker — Ana Secretária Virtual
const CACHE_NAME = 'ana-v3-cache-v2';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

// ── PUSH NOTIFICATIONS ──────────────────────────────────────
self.addEventListener('push', e => {
  let data = { title: 'Ana', body: 'Nova notificação', url: '/' };
  try {
    if (e.data) data = JSON.parse(e.data.text());
  } catch(err) {}

  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: data.tag || ('ana-' + Date.now()),
      renotify: true,
      requireInteraction: false,
      vibrate: [120, 60, 120],
      data: { url: data.url || '/' },
      actions: [
        { action: 'open', title: 'Abrir Ana' },
        { action: 'close', title: 'Fechar' }
      ]
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  if (e.action === 'close') return;
  const url = e.notification.data?.url || '/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
      const existing = cs.find(c => c.url.includes(self.location.origin));
      if (existing) { existing.focus(); existing.navigate(url); }
      else clients.openWindow(url);
    })
  );
});
