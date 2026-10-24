(function () {
  "use strict";

  // Paint the chosen theme before anything renders (extra_keys.js owns it).
  try {
    var pref = localStorage.getItem("rc-theme") || "system";
    var light =
      pref === "light" ||
      (pref !== "dark" && window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
    document.documentElement.dataset.rcTheme = light ? "light" : "dark";
  } catch (_) {}

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

  // Tabs only exist when someone asks for one: the server starts the tmux
  // session first, and tab_session.sh never creates one on connect.
  var creating = false;
  window.__rcNewSession = function () {
    if (creating) return;
    creating = true;
    fetch("/rc-fs/op", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: "openTab", path: "~" }),
    })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (r) {
        creating = false;
        if (r && TAB_RE.test(r.tab || "")) window.__rcOpenSession(r.tab);
        else if (typeof window.__rcToast === "function") window.__rcToast((r && r.error) || "No pude abrir la terminal");
      })
      .catch(function () {
        creating = false;
        if (typeof window.__rcToast === "function") window.__rcToast("No pude abrir la terminal");
      });
  };

  // No tab to show: drop the tab from this browser and reload into the
  // active workspace (its last live tab, or the empty screen).
  window.__rcGoEmpty = function () {
    try {
      sessionStorage.removeItem(SS_KEY);
    } catch (_) {}
    var params = new URLSearchParams(location.search);
    params.delete("arg");
    var q = params.toString();
    location.replace(location.pathname + (q ? "?" + q : "") + location.hash);
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

  function liveTarget(payload) {
    var live = {};
    ((payload && payload.sessions) || []).forEach(function (item) {
      if (item && item.id) live[item.id] = true;
    });
    var target = "";
    ((payload && payload.workspaces) || []).forEach(function (ws) {
      if (ws.id !== payload.active || target) return;
      if (live[ws.lastTab]) target = ws.lastTab;
      for (var i = (ws.tabs || []).length - 1; i >= 0 && !target; i--) {
        if (live[ws.tabs[i]]) target = ws.tabs[i];
      }
    });
    return { live: live, target: target };
  }

  // Nothing to attach: the UI boots without a terminal (extra_keys.js shows
  // the empty screen) and ttyd's socket stays held.
  function enterEmpty() {
    window.__rcEmpty = true;
    window.__rcRedirecting = false;
    var late = window.__rcLateBoot || [];
    window.__rcLateBoot = null;
    late.forEach(function (fn) {
      try {
        fn();
      } catch (_) {}
    });
  }

  // A browser with no tab yet (new device, or a new tunnel URL) lands on the
  // last live tab of the active workspace. It never opens a fresh one.
  function landOnWorkspace() {
    window.__rcRedirecting = true;
    // Hold ttyd's socket so it does not reach tab_session.sh at all.
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
        var target = liveTarget(payload).target;
        if (TAB_RE.test(target)) goToTab(target);
        else enterEmpty();
      })
      .catch(enterEmpty);
  }

  // A remembered tab that no longer exists (closed elsewhere, or tmux was
  // restarted) is not brought back: move to a live one or the empty screen.
  function checkTabAlive(id) {
    fetch("/rc-workspaces", { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (payload) {
        if (payload && Array.isArray(payload.sessions) && !liveTarget(payload).live[id]) window.__rcGoEmpty();
      })
      .catch(function () {});
  }

  function ensureTabArg() {
    if (!knownTabId()) {
      landOnWorkspace();
      return false;
    }
    var id = getTabId();
    document.documentElement.dataset.rcTab = id;
    if (currentArg() === id) {
      checkTabAlive(id);
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
