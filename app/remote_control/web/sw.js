/* Remote Control service worker: cache fonts/JS. Never fake a 503 page. */
/* v=1.2.7 */
var CACHE = "rc-tty-v1.2.7";

function isWs(request) {
  if (request.url.indexOf("/ws") !== -1) return true;
  return request.destination === "websocket";
}

function addBestEffort(cache, urls) {
  return Promise.all(
    urls.map(function (url) {
      return cache.add(url).catch(function () {});
    })
  );
}

function cacheFirst(request, ignoreSearch) {
  return caches.open(CACHE).then(function (cache) {
    return cache.match(request, { ignoreSearch: !!ignoreSearch }).then(function (hit) {
      if (hit) return hit;
      return fetch(request).then(function (resp) {
        if (resp && resp.ok) cache.put(request, resp.clone());
        return resp;
      });
    });
  });
}

function networkFirst(request, store) {
  return fetch(request)
    .then(function (resp) {
      if (store && resp && resp.ok) {
        var copy = resp.clone();
        caches.open(CACHE).then(function (cache) {
          cache.put(request, copy);
        });
      }
      return resp;
    })
    .catch(function () {
      return caches.match(request).then(function (hit) {
        return hit || fetch(request);
      });
    });
}

self.addEventListener("install", function (event) {
  event.waitUntil(
    fetch("/rc-assets/manifest.json", { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : { fonts: [] };
      })
      .catch(function () {
        return { fonts: [] };
      })
      .then(function (man) {
        var urls = ["/rc-assets/cache.js?v=1.2.7", "/rc-assets/sw.js?v=1.2.7"].concat(
          man.fonts || []
        );
        return caches.open(CACHE).then(function (cache) {
          return addBestEffort(cache, urls);
        });
      })
      .then(function () {
        return self.skipWaiting();
      })
      .catch(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (key) {
              return key !== CACHE;
            })
            .map(function (key) {
              return caches.delete(key);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET" || isWs(request)) return;
  if (request.mode === "navigate") return;
  var url;
  try {
    url = new URL(request.url);
  } catch (_) {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.indexOf("/ws") === 0) return;
  if (url.pathname === "/rc-assets/manifest.json") {
    event.respondWith(networkFirst(request, false));
    return;
  }
  if (url.pathname.indexOf("/rc-assets/") === 0) {
    event.respondWith(cacheFirst(request, /\.woff2$/.test(url.pathname)));
    return;
  }
  if (/\.woff2$/.test(url.pathname)) {
    event.respondWith(cacheFirst(request, true));
    return;
  }
  if (/\.(js|css|wasm)$/.test(url.pathname)) {
    event.respondWith(cacheFirst(request, false));
    return;
  }
  if (url.pathname === "/rc-history") {
    event.respondWith(networkFirst(request, false));
  }
});
