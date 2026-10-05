(function () {
  "use strict";

  // tmux keeps the shell. Switching tabs navigates, and ttyd's beforeunload
  // would pop "Leave site?" even though nothing is lost.
  var addListener = window.addEventListener.bind(window);
  window.addEventListener = function (type, fn, opts) {
    if (type === "beforeunload") return;
    return addListener(type, fn, opts);
  };
  try {
    window.onbeforeunload = null;
  } catch (_) {}

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

  function rememberTab(id) {
    try {
      sessionStorage.setItem(SS_KEY, id);
    } catch (_) {}
    document.documentElement.dataset.rcTab = id;
    var all = prune(readSessions());
    var prev = all[id] || {};
    all[id] = {
      created: prev.created || Date.now(),
      lastSeen: Date.now(),
    };
    writeSessions(all);
  }

  function urlForTab(id) {
    var params = new URLSearchParams(location.search);
    params.delete("arg");
    params.append("arg", id);
    var q = params.toString();
    return location.pathname + (q ? "?" + q : "") + location.hash;
  }

  window.__rcOpenSession = function (id) {
    if (!TAB_RE.test(id)) return;
    rememberTab(id);
    var next = urlForTab(id);
    try {
      history.replaceState(null, "", next);
    } catch (_) {}
    try {
      window.dispatchEvent(new CustomEvent("rc-tab-changed"));
    } catch (_) {}
    if (typeof window.__rcSyncTabs === "function") window.__rcSyncTabs();
    if (typeof window.__rcAttachSession === "function" && window.__rcAttachSession()) {
      return;
    }
    location.assign(next);
  };

  window.__rcNewSession = function () {
    window.__rcOpenSession(makeTabId());
  };

  function knownTabId() {
    var id = "";
    try {
      id = sessionStorage.getItem(SS_KEY) || "";
    } catch (_) {}
    if (TAB_RE.test(id)) return id;
    var fromUrl = currentArg();
    return TAB_RE.test(fromUrl) ? fromUrl : "";
  }

  function goToTab(id) {
    try {
      sessionStorage.setItem(SS_KEY, id);
    } catch (_) {}
    location.replace(urlForTab(id));
  }

  // A browser with no tab yet (new device, or a new tunnel URL) lands on the
  // last tab of the active workspace instead of opening a fresh one.
  function landOnWorkspace() {
    window.__rcRedirecting = true;
    // Hold ttyd's socket so it does not start a throwaway tmux session.
    var Stub = function () {
      this.readyState = 0;
    };
    Stub.prototype.send = Stub.prototype.close = function () {};
    Stub.prototype.addEventListener = Stub.prototype.removeEventListener = function () {};
    Stub.CONNECTING = 0;
    Stub.OPEN = 1;
    Stub.CLOSING = 2;
    Stub.CLOSED = 3;
    window.WebSocket = Stub;
    fetch("/rc-workspaces", { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (payload) {
        var live = {};
        ((payload && payload.sessions) || []).forEach(function (item) {
          if (item && item.id) live[item.id] = true;
        });
        var target = "";
        ((payload && payload.workspaces) || []).forEach(function (ws) {
          if (ws.id === payload.active && live[ws.lastTab]) target = ws.lastTab;
        });
        goToTab(TAB_RE.test(target) ? target : makeTabId());
      })
      .catch(function () {
        goToTab(makeTabId());
      });
  }

  function ensureTabArg() {
    if (!knownTabId()) {
      landOnWorkspace();
      return false;
    }
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
