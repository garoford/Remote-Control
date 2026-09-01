/* Remote Control service worker: cache fonts/JS, keep HTML+history network-first. */
/* v=1.2.0 */
var CACHE = "rc-tty-v1.2.0";

function isWs(request) {
  if (request.url.indexOf("/ws") !== -1) return true;
  var dest = request.destination;
  return dest === "websocket";
}

function cacheFirst(request) {
  return caches.open(CACHE).then(function (cache) {
    return cache.match(request).then(function (hit) {
      if (hit) return hit;
      return fetch(request).then(function (resp) {
        if (resp && resp.ok) cache.put(request, resp.clone());
        return resp;
      });
    });
  });
}

function networkFirst(request) {
  return fetch(request)
    .then(function (resp) {
      if (resp && resp.ok) {
        var copy = resp.clone();
        caches.open(CACHE).then(function (cache) {
          cache.put(request, copy);
        });
      }
      return resp;
    })
    .catch(function () {
      return caches.match(request).then(function (hit) {
        return hit || new Response("offline", { status: 503, statusText: "Offline" });
      });
    });
}

self.addEventListener("install", function (event) {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(["/", "/rc-assets/cache.js"]).catch(function () {});
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
  var url;
  try {
    url = new URL(request.url);
  } catch (_) {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.indexOf("/ws") === 0) return;
  if (url.pathname.indexOf("/rc-assets/") === 0) {
    event.respondWith(cacheFirst(request));
    return;
  }
  if (/\.(js|css|wasm|woff2)$/.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
    return;
  }
  if (url.pathname === "/rc-history" || url.pathname === "/" || url.pathname === "/index.html") {
    event.respondWith(networkFirst(request));
  }
});
