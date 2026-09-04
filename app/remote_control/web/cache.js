(function () {
  "use strict";

  var VERSION = "1.3.23";
  var SYSTEM_MONO =
    "ui-monospace, 'SF Mono', Menlo, Consolas, 'Courier New', monospace";
  var FONT_STACK =
    "'FiraCode Nerd Font Mono', ui-monospace, 'Cascadia Mono', 'SF Mono', Menlo, Consolas, monospace";
  var FONT_SAMPLE = "Il|\uE0B0\uE0B2";
  var FONT_WAIT_MS = 8000;
  var fontsP = null;
  var fontsOk = false;
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

  function normLine(s) {
    return String(s || "").replace(/\s+$/g, "");
  }

  function readTermLines(term) {
    var buf = term && term.buffer && term.buffer.active;
    if (!buf || typeof buf.getLine !== "function") return [];
    var lines = [];
    var i;
    for (i = 0; i < buf.length; i++) {
      var line = buf.getLine(i);
      lines.push(
        line && typeof line.translateToString === "function"
          ? line.translateToString(true)
          : ""
      );
    }
    while (lines.length && !normLine(lines[lines.length - 1])) lines.pop();
    return lines;
  }

  function mergeUnique(hist, live) {
    if (!hist || !hist.length) return (live || []).slice();
    if (!live || !live.length) return hist.slice();
    var max = Math.min(hist.length, live.length, 80);
    var n = 0;
    var i;
    var j;
    for (i = max; i > 0; i--) {
      var ok = true;
      for (j = 0; j < i; j++) {
        if (normLine(hist[hist.length - i + j]) !== normLine(live[j])) {
          ok = false;
          break;
        }
      }
      if (ok) {
        n = i;
        break;
      }
    }
    return hist.concat(live.slice(n));
  }

  function snapshotLines(term) {
    var live = readTermLines(term);
    var hist = window.__rcHistoryLines || [];
    var lines = mergeUnique(hist, live);
    if (!lines.length) return null;
    if (lines.length > MAX_LINES) lines = lines.slice(-MAX_LINES);
    return lines;
  }

  function paintSaved(lines) {
    var out = lines || [];
    if (typeof window.__rcPaintHistory === "function") {
      window.__rcPaintHistory(out);
      return true;
    }
    window.__rcPendingHistory = out;
    return false;
  }

  function pinPage() {
    if (typeof window.__rcPinBottom === "function") {
      window.__rcPinBottom();
    }
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
    if (!rec || !rec.lines || !rec.lines.length) {
      if (done) done();
      return;
    }
    restoring = true;
    holdUntil = Date.now() + RECONCILE_HOLD_MS;
    paintSaved(rec.lines);
    restoring = false;
    refreshTermFont();
    pinPage();
    if (done) done();
  }

  function isTouch() {
    return document.documentElement.classList.contains("rc-touch");
  }

  function historyLinesFrom(payload) {
    if (!payload) return [];
    if (payload.all && payload.all.length) return payload.all;
    if (payload.mode === "full" && payload.lines && payload.lines.length) {
      return payload.lines;
    }
    return [];
  }

  function applyReconcile(payload) {
    var all = historyLinesFrom(payload);
    if (all.length) {
      holdUntil = Date.now() + RECONCILE_HOLD_MS;
      restoring = true;
      paintSaved(all);
      restoring = false;
      refreshTermFont();
      pinPage();
      return;
    }
    if (!payload || !payload.lines || !payload.lines.length) return;
    holdUntil = Date.now() + RECONCILE_HOLD_MS;
    restoring = true;
    if (typeof window.__rcAppendHistory === "function") {
      window.__rcAppendHistory(payload.lines);
    } else {
      paintSaved((window.__rcHistoryLines || []).concat(payload.lines));
    }
    restoring = false;
    refreshTermFont();
    pinPage();
  }

  function fetchHistory(full) {
    var id = tabId();
    if (!id) return Promise.resolve(null);
    var fp = full ? encodeURIComponent("[]") : encodeURIComponent(JSON.stringify(fingerprint(window.__rcHistoryLines || [])));
    var url = "/rc-history?tab=" + encodeURIComponent(id) + "&fp=" + fp;
    return fetch(url, { cache: "no-store" }).then(function (resp) {
      if (!resp.ok) return null;
      return resp.json();
    });
  }

  function pullHistory(retry) {
    var id = tabId();
    if (!id) return;
    fetchHistory(true)
      .then(function (payload) {
        var all = historyLinesFrom(payload);
        if (all.length) {
          applyReconcile(payload);
          return;
        }
        if (retry) {
          setTimeout(function () {
            pullHistory(false);
          }, 400);
          setTimeout(function () {
            pullHistory(false);
          }, 1200);
        }
        return idbGet(id)
          .then(function (rec) {
            if ((!window.__rcHistoryLines || !window.__rcHistoryLines.length) && rec) {
              restoreIfEmpty(rec);
            }
          })
          .catch(function () {});
      })
      .catch(function () {
        if (retry) {
          setTimeout(function () {
            pullHistory(false);
          }, 600);
        }
      });
  }

  function onWsOpen() {
    if (!navigator.onLine) {
      setBadge("offline");
      return;
    }
    setBadge("reconnecting");
    pullHistory(true);
    waitFonts()
      .then(function () {
        refreshTermFont();
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
    pullHistory(true);
  }

  function registerSw() {
    if (!("serviceWorker" in navigator)) return;
    var ua = navigator.userAgent || "";
    var iosWebkit = /iPhone|iPad|iPod/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
    if (iosWebkit) return;
    navigator.serviceWorker.register("/rc-assets/sw.js?v=" + VERSION, { scope: "/" }).catch(function () {});
  }

  function waitFonts() {
    if (fontsP) return fontsP;
    var size = isTouch() ? "16px" : "15px";
    var work = Promise.resolve();
    if (document.fonts && document.fonts.load) {
      work = document.fonts
        .load("400 " + size + " 'FiraCode Nerd Font Mono'", FONT_SAMPLE)
        .then(function () {
          if (!isTouch()) {
            return document.fonts.load("700 15px 'FiraCode Nerd Font Mono'", FONT_SAMPLE);
          }
        })
        .then(function () {
          if (document.fonts.ready) return document.fonts.ready;
        })
        .then(function () {
          fontsOk = true;
        })
        .catch(function () {});
    }
    var cap = new Promise(function (resolve) {
      setTimeout(resolve, FONT_WAIT_MS);
    });
    fontsP = Promise.race([work, cap]).then(function () {
      waitForTerm(function () {
        applyTermFont(fontsOk);
      });
    });
    return fontsP;
  }

  function forceAtlasRefresh(term) {
    if (!term) return;
    try {
      if (typeof term.clearTextureAtlas === "function") term.clearTextureAtlas();
    } catch (_) {}
    try {
      var svc = term._core && term._core._renderService;
      if (svc && typeof svc.clearTextureAtlas === "function") svc.clearTextureAtlas();
    } catch (_) {}
    try {
      if (typeof term.refresh === "function") {
        term.refresh(0, Math.max(0, (term.rows || 1) - 1));
      }
    } catch (_) {}
    try {
      if (typeof term.fit === "function") term.fit();
    } catch (_) {}
  }

  function applyTermFont(ready) {
    var term = findTerm();
    if (!term) return;
    var stack = ready || !isTouch() ? FONT_STACK : SYSTEM_MONO;
    try {
      if (term.options) {
        if (term.options.fontFamily === stack) {
          term.options.fontFamily = SYSTEM_MONO;
        }
        term.options.fontFamily = stack;
      }
    } catch (_) {}
    forceAtlasRefresh(term);
  }

  function refreshTermFont() {
    applyTermFont(fontsOk || !isTouch());
  }

  function bootFonts() {
    if (isTouch()) {
      waitForTerm(function () {
        applyTermFont(false);
      });
    }
    waitFonts();
    if (document.fonts && document.fonts.addEventListener) {
      document.fonts.addEventListener("loadingdone", function () {
        fontsOk = true;
        waitForTerm(function () {
          applyTermFont(true);
        });
      });
    }
  }

  function boot() {
    registerSw();
    bootFonts();
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
  if (window.__rcTermSocket && window.__rcTermSocket.readyState === 1) {
    onWsOpen();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
