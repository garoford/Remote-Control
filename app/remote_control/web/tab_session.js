(function () {
  "use strict";

  var LS_KEY = "rc-tab-sessions";
  var SS_KEY = "rc-tab-id";
  var TAB_RE = /^rc[a-z0-9]{10,32}$/;
  var MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

  function makeTabId() {
    var bytes = new Uint8Array(12);
    if (window.crypto && crypto.getRandomValues) {
      crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i++) bytes[i] = (Math.random() * 256) | 0;
    }
    var hex = "";
    for (var j = 0; j < bytes.length; j++) {
      hex += ("0" + bytes[j].toString(16)).slice(-2);
    }
    return "rc" + hex;
  }

  function readSessions() {
    try {
      var raw = localStorage.getItem(LS_KEY);
      var data = raw ? JSON.parse(raw) : {};
      return data && typeof data === "object" ? data : {};
    } catch (_) {
      return {};
    }
  }

  function writeSessions(data) {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(data));
    } catch (_) {}
  }

  function prune(all) {
    var now = Date.now();
    Object.keys(all).forEach(function (id) {
      var rec = all[id];
      if (!rec || !TAB_RE.test(id) || now - (rec.lastSeen || 0) > MAX_AGE_MS) {
        delete all[id];
      }
    });
    return all;
  }

  function getTabId() {
    var id = "";
    try {
      id = sessionStorage.getItem(SS_KEY) || "";
    } catch (_) {}
    if (!TAB_RE.test(id)) {
      var fromUrl = "";
      try {
        fromUrl = new URLSearchParams(location.search).get("arg") || "";
      } catch (_) {}
      id = TAB_RE.test(fromUrl) ? fromUrl : makeTabId();
      try {
        sessionStorage.setItem(SS_KEY, id);
      } catch (_) {}
    }
    var all = prune(readSessions());
    var prev = all[id] || {};
    all[id] = {
      created: prev.created || Date.now(),
      lastSeen: Date.now(),
    };
    writeSessions(all);
    return id;
  }

  function currentArg() {
    try {
      var args = new URLSearchParams(location.search).getAll("arg");
      return args.length === 1 ? args[0] : "";
    } catch (_) {
      return "";
    }
  }

  function ensureTabArg() {
    var id = getTabId();
    document.documentElement.dataset.rcTab = id;
    if (currentArg() === id) {
      return true;
    }
    window.__rcRedirecting = true;
    try {
      var params = new URLSearchParams(location.search);
      params.delete("arg");
      params.append("arg", id);
      var q = params.toString();
      location.replace(location.pathname + (q ? "?" + q : "") + location.hash);
    } catch (_) {}
    return false;
  }

  ensureTabArg();
})();
