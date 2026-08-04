// Service Worker — A.N.A (Anesthesia Neural Assistant)
const CACHE = 'ana-v4';
const SHELL = ['/', '/manifest.json', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => {})));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => clients.claim())
  );
});

// ── OFFLINE ────────────────────────────────────────────────
// App e agenda continuam acessíveis sem internet (corredor de hospital, elevador, etc.)
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                       // só leitura
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;        // não intercepta Supabase/Google

  // Agenda e contexto: rede primeiro, cache como reserva
  if (url.pathname.startsWith('/api/eventos') ||
      url.pathname.startsWith('/api/medicos') ||
      url.pathname.startsWith('/api/setores')) {
    e.respondWith(
      fetch(req)
        .then(res => {
          const copia = res.clone();
          caches.open(CACHE).then(c => c.put(req, copia));
          return res;
        })
        .catch(() => caches.match(req).then(hit => hit || new Response(
          JSON.stringify({ offline: true, erro: 'sem conexão' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } })))
    );
    return;
  }

  // Documento (navegação): rede primeiro, cai para a última versão guardada
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req)
        .then(res => {
          const copia = res.clone();
          caches.open(CACHE).then(c => c.put('/', copia));
          return res;
        })
        .catch(() => caches.match('/').then(hit => hit || Response.error()))
    );
    return;
  }

  // Ícones e estáticos: cache primeiro
  if (SHELL.includes(url.pathname) || /\.(png|svg|ico|css|js)$/.test(url.pathname)) {
    e.respondWith(
      caches.match(req).then(hit => hit || fetch(req).then(res => {
        const copia = res.clone();
        caches.open(CACHE).then(c => c.put(req, copia));
        return res;
      }).catch(() => hit))
    );
  }
});

// ── PUSH ───────────────────────────────────────────────────
self.addEventListener('push', e => {
  let data = { title: 'A.N.A', body: 'Nova notificação', url: '/' };
  try { if (e.data) data = JSON.parse(e.data.text()); } catch (err) {}

  const actions = data.evento_id
    ? [{ action: 'ciente', title: '✓ Ciente' }, { action: 'open', title: 'Abrir' }]
    : [{ action: 'open', title: 'Abrir A.N.A' }, { action: 'close', title: 'Fechar' }];

  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: data.tag || ('ana-' + Date.now()),
      renotify: true,
      requireInteraction: false,
      vibrate: [120, 60, 120],
      data: { url: data.url || '/', evento_id: data.evento_id || null },
      actions
    })
  );
});

self.addEventListener('notificationclick', e => {
  const evId = e.notification.data?.evento_id;
  e.notification.close();
  if (e.action === 'close') return;

  // "Ciente": abre o app já confirmando (o app tem o token do usuário)
  const url = (e.action === 'ciente' && evId)
    ? '/?ciente=' + encodeURIComponent(evId)
    : (e.notification.data?.url || '/');
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cs => {
      const existing = cs.find(c => c.url.includes(self.location.origin));
      if (existing) { existing.focus(); existing.navigate(url); }
      else clients.openWindow(url);
    })
  );
});
