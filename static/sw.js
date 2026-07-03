// Gemini Tudástár — Service Worker
// Cache-first stratégia statikus asset-ekre, network-first API hívásokra,
// offline fallback a dashboard UI betöltésére.

const STATIC_CACHE = 'gemini-static-v1';
const CDN_CACHE = 'gemini-cdn-v1';

// ── Telepítéskor előtöltjük a shell-t ────────────────────────────────────
const PRECACHE_URLS = [
  '/',
  '/dashboard',
  '/static/manifest.json',
  '/static/icons/icon.svg',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

const CDN_PREFIXES = [
  'cdnjs.cloudflare.com',
  'd3js.org',
];

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(function(cache) {
      return Promise.allSettled(
        PRECACHE_URLS.map(function(url) {
          return cache.add(url).catch(function() {
            // Csendben kihagyjuk, ha offline történik az install
          });
        })
      );
    }).then(function() {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(key) {
          return key !== STATIC_CACHE && key !== CDN_CACHE;
        }).map(function(key) {
          return caches.delete(key);
        })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

// ── Fetch stratégiák ──────────────────────────────────────────────────────

function isCdnRequest(url) {
  return CDN_PREFIXES.some(function(p) { return url.includes(p); });
}

function isApiRequest(url) {
  return url.includes('/api/') || url.includes('/stream/') || url.includes('/status/');
}

function isHtmlRequest(request) {
  return request.mode === 'navigate' ||
    (request.headers.get('accept') || '').includes('text/html');
}

self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // API hívások: network-first (nem cache-eljük)
  if (isApiRequest(url.pathname) || url.pathname === '/start') {
    return; // Hagyjuk a böngésző alapértelmezett fetch-ét
  }

  // CDN asset-ek: cache-first, hosszú élettartam
  if (isCdnRequest(url.hostname)) {
    event.respondWith(
      caches.open(CDN_CACHE).then(function(cache) {
        return cache.match(event.request).then(function(cached) {
          var fetchPromise = fetch(event.request).then(function(response) {
            if (response && response.status === 200) {
              cache.put(event.request, response.clone());
            }
            return response;
          });
          return cached || fetchPromise;
        });
      })
    );
    return;
  }

  // HTML navigáció: network-first, offline fallback a cache-elt verzióra
  if (isHtmlRequest(event.request)) {
    event.respondWith(
      fetch(event.request).catch(function() {
        return caches.match(event.request).then(function(cached) {
          if (cached) return cached;
          // Ha dashboard nem elérhető, próbáljuk a gyökér útvonalat
          if (url.pathname.startsWith('/dashboard')) {
            return caches.match('/dashboard');
          }
          return caches.match('/');
        });
      })
    );
    return;
  }

  // Statikus asset-ek (CSS, JS, képek, fontok): cache-first
  event.respondWith(
    caches.open(STATIC_CACHE).then(function(cache) {
      return cache.match(event.request).then(function(cached) {
        var fetchPromise = fetch(event.request).then(function(response) {
          if (response && response.status === 200) {
            cache.put(event.request, response.clone());
          }
          return response;
        }).catch(function() {
          return cached || new Response('Offline — az erőforrás nem elérhető.', {
            status: 503,
            statusText: 'Service Unavailable'
          });
        });
        return cached || fetchPromise;
      });
    })
  );
});

// ── Üzenetkezelés a fő száltól ──────────────────────────────────────────

self.addEventListener('message', function(event) {
  if (event.data && event.data.action === 'skipWaiting') {
    self.skipWaiting();
  }
});
