(function () {
  "use strict";

  var VERSION = "1.2.0";
  var DB_NAME = "rc-term-history";
  var STORE = "tabs";
  var MAX_LINES = 20000;
  var PERSIST_MS = 600;
  var RECONCILE_HOLD_MS = 400;
  var TAB_RE = /^rc[a-z0-9]{10,32}$/;

  var persistTimer = 0;
  var holdUntil = 0;
  var restoring = false;
  var dbp = null;

  function tabId() {
    var id = "";
    try {
      id = document.documentElement.dataset.rcTab || "";
    } catch (_) {}
    if (!TAB_RE.test(id)) {
      try {
        id = sessionStorage.getItem("rc-tab-id") || "";
      } catch (_) {}
    }
    return TAB_RE.test(id) ? id : "";
  }

  function openDb() {
    if (dbp) return dbp;
    dbp = new Promise(function (resolve, reject) {
      if (!window.indexedDB) {
        reject(new Error("no idb"));
        return;
      }
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "tab" });
        }
      };
      req.onsuccess = function () {
        resolve(req.result);
      };
      req.onerror = function () {
        reject(req.error);
      };
    });
    return dbp;
  }

  function idbGet(id) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readonly");
        var req = tx.objectStore(STORE).get(id);
        req.onsuccess = function () {
          resolve(req.result || null);
        };
        req.onerror = function () {
          reject(req.error);
        };
      });
    });
  }

  function idbPut(rec) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).put(rec);
        tx.oncomplete = function () {
          resolve();
        };
        tx.onerror = function () {
          reject(tx.error);
        };
      });
    });
  }

  function findTerm() {
    var term = window.term;
    if (term && typeof term.write === "function") return term;
    return null;
  }

  function snapshotLines(term) {
    var buf = term && term.buffer && term.buffer.active;
    if (!buf || typeof buf.getLine !== "function") return null;
    var lines = [];
    var i;
    for (i = 0; i < buf.length; i++) {
      var line = buf.getLine(i);
      lines.push(line && typeof line.translateToString === "function" ? line.translateToString(true) : "");
    }
    while (lines.length && !String(lines[lines.length - 1] || "").replace(/\s+$/g, "")) {
      lines.pop();
    }
    if (lines.length > MAX_LINES) lines = lines.slice(-MAX_LINES);
    return lines;
  }

  function fingerprint(lines) {
    var out = [];
    var i;
    for (i = lines.length - 1; i >= 0 && out.length < 5; i--) {
      var text = String(lines[i] || "").replace(/\s+$/g, "");
      if (text) out.push(text);
    }
    return out.reverse();
  }

  function writeLines(term, lines, onDone) {
    if (!term || !lines || !lines.length) {
      if (onDone) onDone();
      return;
    }
    var i = 0;
    function chunk() {
      var part = lines.slice(i, i + 400);
      i += 400;
      if (!part.length) {
        if (onDone) onDone();
        return;
      }
      var text = part.join("\r\n") + "\r\n";
      var advanced = false;
      function next() {
        if (advanced) return;
        advanced = true;
        chunk();
      }
      try {
        term.write(text, next);
      } catch (_) {
        if (onDone) onDone();
        return;
      }
      setTimeout(next, 40);
    }
    chunk();
  }

  function setBadge(kind) {
    var el = document.getElementById("rc-net-badge");
    if (!kind) {
      if (el) el.hidden = true;
      return;
    }
    if (!el) {
      el = document.createElement("div");
      el.id = "rc-net-badge";
      document.documentElement.appendChild(el);
    }
    el.hidden = false;
    el.textContent =
      kind === "offline" ? "Sin red — historial local" : "Reconectando…";
    el.dataset.kind = kind;
  }

  function schedulePersist() {
    if (Date.now() < holdUntil || restoring) return;
    if (persistTimer) clearTimeout(persistTimer);
    persistTimer = setTimeout(persistNow, PERSIST_MS);
  }

  function persistNow() {
    persistTimer = 0;
    if (Date.now() < holdUntil || restoring) return;
    var id = tabId();
    var term = findTerm();
    if (!id || !term) return;
    var lines = snapshotLines(term);
    if (!lines || !lines.length) return;
    idbPut({
      tab: id,
      seq: Date.now(),
      lineCount: lines.length,
      fingerprint: fingerprint(lines),
      lines: lines,
      savedAt: Date.now(),
    }).catch(function () {});
  }

  function restoreIfEmpty(rec, done) {
    var term = findTerm();
    if (!term || !rec || !rec.lines || !rec.lines.length) {
      if (done) done();
      return;
    }
    var current = snapshotLines(term) || [];
    if (current.length >= 8) {
      if (done) done();
      return;
    }
    restoring = true;
    holdUntil = Date.now() + RECONCILE_HOLD_MS;
    writeLines(term, rec.lines, function () {
      restoring = false;
      if (done) done();
    });
  }

  function applyReconcile(payload) {
    var term = findTerm();
    if (!term || !payload || !payload.lines) return;
    holdUntil = Date.now() + RECONCILE_HOLD_MS;
    if (payload.mode === "full") {
      restoring = true;
      if (typeof term.reset === "function") {
        try {
          term.reset();
        } catch (_) {}
      }
      writeLines(term, payload.lines, function () {
        restoring = false;
        persistNow();
      });
      return;
    }
    if (!payload.lines.length) return;
    restoring = true;
    writeLines(term, payload.lines, function () {
      restoring = false;
      persistNow();
    });
  }

  function fetchHistory(lines) {
    var id = tabId();
    if (!id) return Promise.resolve(null);
    var fp = encodeURIComponent(JSON.stringify(fingerprint(lines || [])));
    var url = "/rc-history?tab=" + encodeURIComponent(id) + "&fp=" + fp;
    return fetch(url, { cache: "no-store" }).then(function (resp) {
      if (!resp.ok) return null;
      return resp.json();
    });
  }

  function onWsOpen() {
    if (!navigator.onLine) {
      setBadge("offline");
      return;
    }
    setBadge("reconnecting");
    holdUntil = Date.now() + RECONCILE_HOLD_MS;
    var id = tabId();
    if (!id) {
      setBadge("");
      return;
    }
    idbGet(id)
      .catch(function () {
        return null;
      })
      .then(function (rec) {
        return new Promise(function (resolve) {
          waitForTerm(function (term) {
            var current = snapshotLines(term) || [];
            if (rec && rec.lines && current.length < 8) {
              restoreIfEmpty(rec, function () {
                resolve(snapshotLines(term) || rec.lines);
              });
            } else {
              resolve(current.length ? current : (rec && rec.lines) || []);
            }
          });
        });
      })
      .then(function (lines) {
        return fetchHistory(lines).then(function (payload) {
          if (payload) applyReconcile(payload);
        });
      })
      .catch(function () {})
      .then(function () {
        setBadge(navigator.onLine ? "" : "offline");
      });
  }

  function onWsClose() {
    persistNow();
    setBadge(navigator.onLine ? "reconnecting" : "offline");
  }

  function waitForTerm(cb) {
    var tries = 0;
    function tick() {
      var term = findTerm();
      if (term) {
        cb(term);
        return;
      }
      tries += 1;
      if (tries > 80) return;
      setTimeout(tick, 50);
    }
    tick();
  }

  function bootRestore() {
    var id = tabId();
    if (!id) return;
    idbGet(id)
      .then(function (rec) {
        if (!rec) return;
        waitForTerm(function () {
          restoreIfEmpty(rec);
        });
      })
      .catch(function () {});
  }

  function registerSw() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/rc-assets/sw.js?v=" + VERSION, { scope: "/" }).catch(function () {});
  }

  function boot() {
    registerSw();
    bootRestore();
    window.addEventListener("online", function () {
      setBadge("");
    });
    window.addEventListener("offline", function () {
      persistNow();
      setBadge("offline");
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") persistNow();
    });
    window.addEventListener("pagehide", persistNow);
    setInterval(function () {
      if (!document.hidden) schedulePersist();
    }, 2000);
    if (!navigator.onLine) setBadge("offline");
  }

  if (window.__rcRedirecting) return;
  window.addEventListener("rc-ws-open", onWsOpen);
  window.addEventListener("rc-ws-close", onWsClose);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
