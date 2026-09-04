(function () {
  "use strict";

  var VERSION = "1.4.0";
  var SYSTEM_MONO =
    "ui-monospace, 'SF Mono', Menlo, Consolas, 'Courier New', monospace";
  var FONT_STACK =
    "'FiraCode Nerd Font Mono', ui-monospace, 'Cascadia Mono', 'SF Mono', Menlo, Consolas, monospace";
  var FONT_SAMPLE = "Il|\uE0B0\uE0B2";
  var FONT_WAIT_MS = 8000;
  var fontsP = null;
  var fontsOk = false;

  function findTerm() {
    var term = window.term;
    if (term && typeof term.write === "function") return term;
    return null;
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
    el.textContent = kind === "offline" ? "Sin red" : "Reconectando…";
    el.dataset.kind = kind;
  }

  function isTouch() {
    return document.documentElement.classList.contains("rc-touch");
  }

  function onWsOpen() {
    if (!navigator.onLine) {
      setBadge("offline");
      return;
    }
    setBadge("reconnecting");
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
    window.addEventListener("online", function () {
      setBadge("");
    });
    window.addEventListener("offline", function () {
      setBadge("offline");
    });
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
