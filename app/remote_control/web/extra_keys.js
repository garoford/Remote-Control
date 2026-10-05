(function () {
  "use strict";

  var NativeWS = window.WebSocket;
  var INPUT = 48;
  var encoder = new TextEncoder();
  var mods = { shift: false, ctrl: false, alt: false };
  var locks = { shift: false, ctrl: false, alt: false };
  var lastTap = { shift: 0, ctrl: 0, alt: 0 };
  var keepFocusUntil = 0;
  var writing = false;
  var writeBusy = false;
  var writeQ = "";
  var lastFit = "";
  var lastSentAt = 0;
  var layoutRaf = 0;
  var pasteGuard = 0;
  var pasteBusy = false;
  var selectMode = false;
  var imeOpen = false;
  var imeGuard = 0;
  var lastSel = null;
  var stickBottom = true;
  var bar = null;

  var ROWS = [
    [
      { id: "esc", label: "ESC", seq: "\u001b" },
      { id: "slash", label: "/", text: "/" },
      { id: "shift", label: "SHIFT", mod: "shift" },
      { id: "home", label: "HOME", seq: "\u001b[H", key: "Home" },
      { id: "up", label: "↑", seq: "\u001b[A", key: "ArrowUp" },
      { id: "end", label: "END", seq: "\u001b[F", key: "End" },
      { id: "pgup", label: "PGUP", seq: "\u001b[5~", key: "PageUp" },
    ],
    [
      { id: "tab", label: "TAB", seq: "\t" },
      { id: "ctrl", label: "CTRL", mod: "ctrl" },
      { id: "alt", label: "ALT", mod: "alt" },
      { id: "left", label: "←", seq: "\u001b[D", key: "ArrowLeft" },
      { id: "down", label: "↓", seq: "\u001b[B", key: "ArrowDown" },
      { id: "right", label: "→", seq: "\u001b[C", key: "ArrowRight" },
      { id: "pgdn", label: "PGDN", seq: "\u001b[6~", key: "PageDown" },
    ],
  ];

  var PASTE_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M16.5 6.5v10a4.5 4.5 0 0 1-9 0V5a3 3 0 0 1 6 0v10.5a1.5 1.5 0 0 1-3 0V6.5H9v9a3 3 0 0 0 6 0V5a4.5 4.5 0 0 0-9 0v11.5a6 6 0 0 0 12 0v-10h-1.5Z"/>' +
    "</svg>";
  var IME_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M20 5H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2Zm0 12H4V7h16v10ZM5 9h2v2H5V9Zm3 0h2v2H8V9Zm3 0h2v2h-2V9Zm3 0h2v2h-2V9Zm3 0h3v2h-3V9ZM5 12h2v2H5v-2Zm3 0h2v2H8v-2Zm3 0h2v2h-2v-2Zm3 0h6v2h-6v-2ZM7 15h10v2H7v-2Z"/>' +
    "</svg>";

  var MIC_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm-1-9a1 1 0 0 1 2 0v6a1 1 0 0 1-2 0V5Zm6 6a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z"/>' +
    "</svg>";

  var PASTE_CHUNK = 2048;
  var PASTE_EDGE = 1920;
  var PASTE_QUALITY = 0.82;
  var PASTE_EXT = {
    webp: 1,
    jpg: 1,
    png: 1,
    gif: 1,
    svg: 1,
    txt: 1,
    md: 1,
    json: 1,
    csv: 1,
    pdf: 1,
    zip: 1,
    gz: 1,
    xz: 1,
    tar: 1,
    mp4: 1,
    webm: 1,
    mp3: 1,
    wav: 1,
    bin: 1,
    doc: 1,
    docx: 1,
    xls: 1,
    xlsx: 1,
    ppt: 1,
    pptx: 1,
  };

  var ARROW_LETTER = {
    ArrowUp: "A",
    ArrowDown: "B",
    ArrowRight: "C",
    ArrowLeft: "D",
    Home: "H",
    End: "F",
  };

  function softenPrefs(ev) {
    try {
      var data = ev && ev.data;
      if (!data || typeof data === "string") return ev;
      var bytes = new Uint8Array(data);
      if (!bytes.length || bytes[0] !== 50) return ev;
      var prefs = JSON.parse(new TextDecoder().decode(bytes.subarray(1)));
      if (!prefs || typeof prefs !== "object") return ev;
      prefs.scrollback = 20000;
      var encoded = new TextEncoder().encode(JSON.stringify(prefs));
      var out = new Uint8Array(encoded.length + 1);
      out[0] = 50;
      out.set(encoded, 1);
      return { data: out.buffer };
    } catch (_) {
      return ev;
    }
  }

  function feedScrollLines(count, rows, restoreRow) {
    var esc = String.fromCharCode(27);
    var text = esc + "7" + esc + "[" + rows + ";1H";
    for (var i = 0; i < count; i++) text += "\n";
    // DECRC follows the scrolled line. A following CUU expects the cursor to stay put.
    text += esc + "8";
    if (restoreRow) text += esc + "[" + count + "B";
    var out = new Uint8Array(text.length);
    for (var n = 0; n < text.length; n++) out[n] = text.charCodeAt(n);
    return out;
  }

  var boundTerm = null;

  function rewriteScrollUp(payload) {
    var term = boundTerm || xterm();
    var rows = term && term.rows > 0 ? term.rows : 999;
    var parts = [];
    var start = 0;
    var changed = false;
    var i = 0;
    while (i < payload.length) {
      if (payload[i] !== 27 || i + 1 >= payload.length || payload[i + 1] !== 91) {
        i++;
        continue;
      }
      var j = i + 2;
      var num = 0;
      var digits = 0;
      while (j < payload.length && payload[j] >= 48 && payload[j] <= 57 && digits < 6) {
        num = num * 10 + (payload[j] - 48);
        digits++;
        j++;
      }
      if (j < payload.length && payload[j] === 83) {
        var count = digits ? num : 1;
        if (count > rows) count = rows;
        var restoreRow = false;
        var k = j + 1;
        if (
          k + 1 < payload.length &&
          payload[k] === 27 &&
          payload[k + 1] === 91
        ) {
          var p = k + 2;
          while (p < payload.length && payload[p] >= 48 && payload[p] <= 57 && p - k < 8) p++;
          if (p < payload.length && payload[p] === 65) restoreRow = true;
        }
        changed = true;
        if (i > start) parts.push(payload.subarray(start, i));
        if (count > 0) parts.push(feedScrollLines(count, rows, restoreRow));
        i = j + 1;
        start = i;
        continue;
      }
      i++;
    }
    if (!changed) return null;
    if (start < payload.length) parts.push(payload.subarray(start));
    var total = 0;
    for (var p = 0; p < parts.length; p++) total += parts[p].length;
    var out = new Uint8Array(total);
    var at = 0;
    for (var q = 0; q < parts.length; q++) {
      out.set(parts[q], at);
      at += parts[q].length;
    }
    return out;
  }

  function incompleteSuTail(payload) {
    var n = payload.length;
    if (!n) return 0;
    var i = n - 1;
    var digits = 0;
    while (i >= 0 && payload[i] >= 48 && payload[i] <= 57 && digits < 6) {
      digits++;
      i--;
    }
    if (digits > 0 && i >= 1 && payload[i] === 91 && payload[i - 1] === 27) {
      var len = n - (i - 1);
      return len <= 16 ? len : 0;
    }
    if (payload[n - 1] === 27) return 1;
    if (n >= 2 && payload[n - 1] === 91 && payload[n - 2] === 27) return 2;
    return 0;
  }

  function completeSuTail(payload) {
    var n = payload.length;
    if (n < 3 || payload[n - 1] !== 83) return 0;
    var i = n - 2;
    var digits = 0;
    while (i >= 0 && payload[i] >= 48 && payload[i] <= 57 && digits < 6) {
      digits++;
      i--;
    }
    if (i >= 1 && payload[i] === 91 && payload[i - 1] === 27) {
      var len = n - (i - 1);
      return len <= 16 ? len : 0;
    }
    return 0;
  }

  function preserveScrollUp(ev, hold) {
    try {
      var data = ev && ev.data;
      if (!data || typeof data === "string") return ev;
      var bytes = new Uint8Array(data);
      if (!bytes.length || bytes[0] !== 48) return ev;
      var payload = bytes.subarray(1);
      var merged = false;
      if (hold && hold.bytes && hold.bytes.length) {
        var both = new Uint8Array(hold.bytes.length + payload.length);
        both.set(hold.bytes, 0);
        both.set(payload, hold.bytes.length);
        payload = both;
        hold.bytes = null;
        merged = true;
      }
      var cut = incompleteSuTail(payload) || completeSuTail(payload);
      if (cut > 0 && hold) {
        hold.bytes = new Uint8Array(payload.subarray(payload.length - cut));
        payload = payload.subarray(0, payload.length - cut);
        merged = true;
      }
      var body = rewriteScrollUp(payload);
      if (!body && !merged) return ev;
      if (!body) body = payload;
      var framed = new Uint8Array(body.length + 1);
      framed[0] = 48;
      framed.set(body, 1);
      return { data: framed.buffer };
    } catch (_) {
      return ev;
    }
  }

  function decodeSend(data) {
    try {
      if (typeof data === "string") return data;
      if (data instanceof ArrayBuffer) return new TextDecoder().decode(new Uint8Array(data));
      if (ArrayBuffer.isView(data)) return new TextDecoder().decode(data);
    } catch (_) {}
    return "";
  }

  function holdStartupSize(ws) {
    var existing = xterm();
    if (existing && existing.__rcSized) return;
    var nativeSend = ws.send.bind(ws);
    var outQueue = [];
    var sizeReady = false;
    var holdStarted = Date.now();
    var timer = 0;
    ws.send = function (data) {
      if (sizeReady) return nativeSend(data);
      outQueue.push(data);
    };
    function dump(cols, rows) {
      sizeReady = true;
      var auth = null;
      var rest = [];
      outQueue.forEach(function (data) {
        var text = decodeSend(data);
        if (text.charAt(0) === "{") {
          try {
            var obj = JSON.parse(text);
            if (obj && Object.prototype.hasOwnProperty.call(obj, "AuthToken")) {
              obj.columns = cols;
              obj.rows = rows;
              auth = new TextEncoder().encode(JSON.stringify(obj));
              return;
            }
          } catch (_) {}
        }
        if (text.charAt(0) === "1" && text.charAt(1) === "{") return;
        rest.push(data);
      });
      outQueue = [];
      if (auth) nativeSend(auth);
      rest.forEach(function (data) {
        nativeSend(data);
      });
      var sized = xterm();
      if (sized) sized.__rcSized = true;
    }
    function prepareFont(term) {
      try {
        term.options.fontFamily =
          "FiraCode Nerd Font Mono, ui-monospace, Cascadia Mono, Courier New, monospace";
        term.options.fontSize = 15;
        term.options.fontWeight = 400;
        term.options.fontWeightBold = 700;
      } catch (_) {}
      if (typeof term.fit === "function") {
        try {
          term.fit();
        } catch (_) {}
      }
      try {
        var dims = term._core._renderService.dimensions.css.cell;
        var width = Math.round(dims.width);
        var height = Math.round(dims.height);
        if (width > 0 && Math.abs(dims.width - width) > 0.05) {
          dims.width = width;
          if (height > 0) dims.height = height;
          term.fit();
        }
      } catch (_) {}
    }
    function finish() {
      if (sizeReady) return;
      var now = xterm();
      var waited = Date.now() - holdStarted;
      if (!now || !now.rows) {
        if (waited > 1200) {
          sizeReady = true;
          var pending = outQueue;
          outQueue = [];
          pending.forEach(function (data) {
            nativeSend(data);
          });
          return;
        }
        setTimeout(pump, 30);
        return;
      }
      prepareFont(now);
      dump(now.cols, now.rows);
    }
    function armTimer() {
      clearTimeout(timer);
      timer = setTimeout(finish, Date.now() - holdStarted > 1100 ? 0 : 250);
    }
    function pump() {
      if (sizeReady) return;
      var now = xterm();
      if (now && typeof now.onResize === "function" && !now.__rcHoldResize) {
        now.__rcHoldResize = true;
        now.onResize(function () {
          if (!sizeReady) armTimer();
        });
      }
      try {
        window.dispatchEvent(new Event("resize"));
      } catch (_) {}
      armTimer();
    }
    var fonts = document.fonts;
    var loaded =
      fonts && typeof fonts.load === "function"
        ? fonts.load('15px "FiraCode Nerd Font Mono"')
        : Promise.resolve();
    Promise.resolve(loaded).then(
      function () {
        requestAnimationFrame(function () {
          requestAnimationFrame(pump);
        });
      },
      pump
    );
  }

  function currentWsUrl() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var path = location.pathname.replace(/[/]+$/, "");
    return proto + "//" + location.host + path + "/ws" + location.search;
  }

  var bootTabId = tabId();
  var panes = {};
  var primaryPane = null;
  var activePaneId = bootTabId;
  var tokenCache = null;
  var bindTouchHost = function () {};
  var FLOW_LIMIT = 100000;

  function wsUrlFor(id) {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var path = location.pathname.replace(/[/]+$/, "");
    var params = new URLSearchParams(location.search);
    params.delete("arg");
    params.append("arg", id);
    return proto + "//" + location.host + path + "/ws?" + params.toString();
  }

  function tokenUrl() {
    var path = location.pathname.replace(/[/]+$/, "");
    return location.protocol + "//" + location.host + path + "/token";
  }

  function fetchToken() {
    if (tokenCache) return tokenCache;
    var pending = fetch(tokenUrl(), { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : {};
      })
      .then(function (body) {
        return (body && body.token) || "";
      })
      .catch(function () {
        tokenCache = null;
        return "";
      });
    tokenCache = pending;
    return pending;
  }

  function cellOf(term) {
    try {
      var dims = term._core._renderService.dimensions.css.cell;
      if (dims && dims.width > 2 && dims.height > 2) {
        return { width: dims.width, height: dims.height };
      }
    } catch (_) {}
    return null;
  }

  function hookPrimaryFit(term) {
    if (!term || term.__rcFitHook || typeof term.fit !== "function") return;
    var native = term.fit.bind(term);
    term.fit = function () {
      var host = primaryPane && primaryPane.host;
      if (host && host.hidden) return;
      return native();
    };
    term.__rcFitHook = true;
  }

  function ensurePrimary() {
    if (primaryPane && panes[primaryPane.id] === primaryPane) {
      hookPrimaryFit(primaryPane.term);
      return primaryPane;
    }
    var term = window.term;
    var host = document.getElementById("terminal-container");
    if (!bootTabId || !term || typeof term.focus !== "function" || !host) return null;
    hookPrimaryFit(term);
    var pane = panes[bootTabId];
    if (!pane || pane.primary === false) {
      pane = {
        id: bootTabId,
        host: host,
        term: term,
        ws: window.__rcPrimarySocket || window.__rcTermSocket || null,
        primary: true,
        intentional: false,
        authed: true,
        written: 0,
        pending: 0,
      };
      panes[bootTabId] = pane;
    } else {
      pane.term = term;
      pane.host = host;
      pane.primary = true;
      if (!pane.ws) pane.ws = window.__rcPrimarySocket || window.__rcTermSocket || null;
    }
    primaryPane = pane;
    host.classList.add("rc-pane");
    host.dataset.rcPane = bootTabId;
    return pane;
  }

  function termOptions(src) {
    var opts = { scrollback: 20000, allowProposedApi: true };
    var keys = [
      "fontFamily",
      "fontSize",
      "fontWeight",
      "fontWeightBold",
      "lineHeight",
      "letterSpacing",
      "theme",
      "cursorBlink",
      "cursorStyle",
      "cursorWidth",
      "drawBoldTextInBrightColors",
      "minimumContrastRatio",
      "windowsPty",
      "macOptionIsMeta",
    ];
    keys.forEach(function (key) {
      if (src && src[key] != null) opts[key] = src[key];
    });
    if (!opts.fontFamily) {
      opts.fontFamily =
        "FiraCode Nerd Font Mono, ui-monospace, Cascadia Mono, Courier New, monospace";
    }
    if (!opts.fontSize) opts.fontSize = 15;
    return opts;
  }

  function sendCtrl(pane, ch) {
    var ws = pane && pane.ws;
    if (!ws || ws.readyState !== 1) return;
    try {
      ws.send(encoder.encode(ch));
    } catch (_) {}
  }

  function writeOutput(pane, bytes) {
    var term = pane.term;
    if (!term || !bytes || !bytes.length) return;
    pane.written = (pane.written || 0) + bytes.length;
    if (pane.written > FLOW_LIMIT) {
      term.write(bytes, function () {
        pane.pending = Math.max((pane.pending || 1) - 1, 0);
        if (pane.pending < 4) sendCtrl(pane, "3");
      });
      pane.pending = (pane.pending || 0) + 1;
      pane.written = 0;
      if (pane.pending > 10) sendCtrl(pane, "2");
      return;
    }
    term.write(bytes);
  }

  function writePane(pane, data) {
    var bytes = null;
    if (data instanceof ArrayBuffer) bytes = new Uint8Array(data);
    else if (ArrayBuffer.isView(data)) bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    if (!bytes || !bytes.length) return;
    var cmd = bytes[0];
    if (cmd === 48) {
      writeOutput(pane, bytes.subarray(1));
      return;
    }
    if (cmd === 49 && pane.id === tabId()) {
      try {
        document.title = new TextDecoder().decode(bytes.subarray(1));
      } catch (_) {}
      return;
    }
    if (cmd !== 50 || !pane.term || !pane.term.options) return;
    try {
      var prefs = JSON.parse(new TextDecoder().decode(bytes.subarray(1)));
      if (!prefs || typeof prefs !== "object") return;
      if (prefs.fontSize) pane.term.options.fontSize = prefs.fontSize;
      if (prefs.fontFamily) pane.term.options.fontFamily = prefs.fontFamily;
      if (prefs.theme) pane.term.options.theme = prefs.theme;
      pane.term.options.scrollback = 20000;
    } catch (_) {}
  }

  function sendPaneInput(pane, text) {
    var ws = pane && pane.ws;
    if (!ws || ws.readyState !== 1 || !text) return;
    var body = encoder.encode(text);
    var payload = new Uint8Array(body.length + 1);
    payload[0] = INPUT;
    payload.set(body, 1);
    try {
      ws.send(payload);
    } catch (_) {}
  }

  function sendPaneBinary(pane, data) {
    var ws = pane && pane.ws;
    if (!ws || ws.readyState !== 1 || !data) return;
    var payload = new Uint8Array(data.length + 1);
    payload[0] = INPUT;
    for (var i = 0; i < data.length; i++) payload[i + 1] = data.charCodeAt(i) & 255;
    try {
      ws.send(payload);
    } catch (_) {}
  }

  function sendPaneResize(pane, cols, rows) {
    if (!pane || !pane.authed || !cols || !rows) return;
    var ws = pane.ws;
    if (!ws || ws.readyState !== 1) return;
    try {
      ws.send(encoder.encode("1" + JSON.stringify({ columns: cols, rows: rows })));
    } catch (_) {}
  }

  function fitPane(pane) {
    if (!pane || !pane.term || !pane.host || pane.host.hidden) return;
    if (pane.primary && typeof pane.term.fit === "function") {
      try {
        pane.term.fit();
      } catch (_) {}
      return;
    }
    var cell = cellOf(pane.term) || (primaryPane && cellOf(primaryPane.term)) || {
      width: 9,
      height: 17,
    };
    var rect = pane.host.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    var cols = Math.max(2, Math.floor(rect.width / cell.width));
    var rows = Math.max(1, Math.floor(rect.height / cell.height));
    if (pane.term.cols === cols && pane.term.rows === rows) return;
    try {
      pane.term.resize(cols, rows);
    } catch (_) {}
  }

  function showPane(pane) {
    if (!pane || !pane.host || !pane.term) return;
    activePaneId = pane.id;
    Object.keys(panes).forEach(function (key) {
      var item = panes[key];
      if (!item || !item.host) return;
      var on = item === pane;
      item.host.hidden = !on;
      item.host.setAttribute("aria-hidden", on ? "false" : "true");
    });
    window.term = pane.term;
    if (pane.ws && pane.ws.readyState !== NativeWS.CLOSED) window.__rcTermSocket = pane.ws;
    resetMirror();
    armPaneScroll(pane.term);
    fitPane(pane);
    try {
      if (typeof pane.term.refresh === "function") {
        pane.term.refresh(0, Math.max(0, (pane.term.rows || 1) - 1));
      }
    } catch (_) {}
    try {
      pane.term.focus();
    } catch (_) {}
    requestAnimationFrame(function () {
      if (panes[pane.id] !== pane || activePaneId !== pane.id) return;
      fitPane(pane);
      try {
        pane.term.focus();
      } catch (_) {}
    });
  }

  function connectPane(pane) {
    if (!pane || pane.intentional || pane.primary) return;
    var existing = pane.ws;
    if (existing && (existing.readyState === NativeWS.CONNECTING || existing.readyState === NativeWS.OPEN)) {
      return;
    }
    window.__rcNextPane = pane;
    var ws;
    try {
      ws = new WebSocket(wsUrlFor(pane.id), ["tty"]);
    } catch (_) {
      window.__rcNextPane = null;
      return;
    }
    window.__rcNextPane = null;
    ws.binaryType = "arraybuffer";
    pane.ws = ws;
    pane.authed = false;
    if (pane.id === tabId()) window.__rcTermSocket = ws;
    ws.addEventListener("open", function () {
      if (pane.ws !== ws || pane.intentional) return;
      fitPane(pane);
      var cols = pane.term.cols || 80;
      var rows = pane.term.rows || 24;
      fetchToken().then(function (token) {
        if (pane.ws !== ws || ws.readyState !== 1) return;
        pane.authed = true;
        try {
          ws.send(
            encoder.encode(
              JSON.stringify({
                AuthToken: token || "",
                columns: cols,
                rows: rows,
              })
            )
          );
        } catch (_) {}
        if (pane.id === tabId()) {
          window.__rcTermSocket = ws;
          window.dispatchEvent(new CustomEvent("rc-ws-open"));
        }
        setTimeout(function () {
          if (typeof pullSessions === "function") pullSessions();
        }, 350);
      });
    });
    ws.addEventListener("message", function (ev) {
      if (pane.ws !== ws) return;
      writePane(pane, ev && ev.data);
    });
    ws.addEventListener("close", function () {
      if (pane.intentional || pane.ws !== ws) return;
      pane.authed = false;
      pane.ws = null;
      if (pane.id === tabId()) {
        window.__rcTermSocket = null;
        window.dispatchEvent(new CustomEvent("rc-ws-close"));
      }
      setTimeout(function () {
        if (!pane.intentional && panes[pane.id] === pane) connectPane(pane);
      }, 200);
    });
  }

  function buildPane(id) {
    var proto = window.term;
    var Term = proto && proto.constructor;
    if (!Term) return null;
    var host = document.createElement("div");
    host.className = "rc-pane";
    host.dataset.rcPane = id;
    host.hidden = true;
    (document.body || document.documentElement).appendChild(host);
    bindTouchHost(host);
    var term;
    try {
      term = new Term(termOptions(proto.options));
      term.open(host);
    } catch (_) {
      host.remove();
      return null;
    }
    term.__rcSized = true;
    var pane = {
      id: id,
      host: host,
      term: term,
      ws: null,
      primary: false,
      intentional: false,
      authed: false,
      written: 0,
      pending: 0,
    };
    panes[id] = pane;
    if (typeof term.onData === "function") {
      term.onData(function (data) {
        sendPaneInput(pane, data);
      });
    }
    if (typeof term.onBinary === "function") {
      term.onBinary(function (data) {
        sendPaneBinary(pane, data);
      });
    }
    if (typeof term.onResize === "function") {
      term.onResize(function (size) {
        sendPaneResize(pane, size.cols, size.rows);
      });
    }
    return pane;
  }

  function disposePane(id) {
    var pane = panes[id];
    if (!pane) return;
    pane.intentional = true;
    var ws = pane.ws;
    if (ws) {
      ws.__rcIntentional = true;
      try {
        if (typeof ws.__rcDropClose === "function") ws.__rcDropClose();
      } catch (_) {}
      try {
        if (ws.readyState === NativeWS.CONNECTING || ws.readyState === NativeWS.OPEN) ws.close(1000);
      } catch (_) {}
    }
    try {
      if (pane.term && typeof pane.term.dispose === "function") pane.term.dispose();
    } catch (_) {}
    if (pane.host) {
      if (pane.primary) pane.host.hidden = true;
      else {
        try {
          pane.host.remove();
        } catch (_) {}
      }
    }
    if (primaryPane === pane) primaryPane = null;
    if (activePaneId === id) activePaneId = "";
    delete panes[id];
    if (typeof histSeen !== "undefined") delete histSeen[id];
  }

  window.__rcAttachSession = function () {
    var id = tabId();
    if (!id || !window.term || typeof window.term.focus !== "function") return false;
    ensurePrimary();
    var pane = panes[id];
    if (!pane) {
      if (id === bootTabId) return false;
      pane = buildPane(id);
      if (!pane) return false;
    }
    showPane(pane);
    if (!pane.primary) connectPane(pane);
    return true;
  };

  window.__rcPaneState = function () {
    return {
      active: activePaneId,
      panes: Object.keys(panes).map(function (key) {
        var pane = panes[key];
        return {
          id: pane.id,
          primary: !!pane.primary,
          hidden: !!(pane.host && pane.host.hidden),
          state: pane.ws ? pane.ws.readyState : -1,
        };
      }),
    };
  };

  // ttyd gives up after a socket "error" and waits for an Enter that our key
  // interceptors swallow, so only F5 brought the tab back. Retry on our own
  // with backoff, and only fall back to "Press Enter" after a run of misses.
  var RETRY_MS = [300, 1000, 2000, 3000, 5000, 5000, 8000, 8000, 10000, 10000];
  var primaryRetry = { tries: 0, timer: 0, fire: null, parked: false };

  function deliverPrimaryClose(code) {
    var fire = primaryRetry.fire;
    if (!fire) return;
    if (primaryRetry.timer) clearTimeout(primaryRetry.timer);
    primaryRetry.timer = 0;
    primaryRetry.fire = null;
    primaryRetry.parked = code === 1000;
    fire(code);
  }

  function schedulePrimaryRetry(fire) {
    primaryRetry.fire = fire;
    if (primaryRetry.timer) clearTimeout(primaryRetry.timer);
    var at = primaryRetry.tries++;
    if (at >= RETRY_MS.length) {
      // Out of tries: let ttyd show its prompt, but keep the handler so a tap,
      // Enter, or the network coming back can still restart it.
      var parked = fire;
      deliverPrimaryClose(1000);
      primaryRetry.fire = parked;
      return;
    }
    primaryRetry.timer = setTimeout(function () {
      deliverPrimaryClose(1006);
    }, RETRY_MS[at]);
  }

  function resumePrimary() {
    if (!primaryRetry.fire) return false;
    primaryRetry.tries = 0;
    deliverPrimaryClose(1006);
    return true;
  }

  function bootPrimaryRetry() {
    window.addEventListener("online", resumePrimary);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) resumePrimary();
    });
    window.addEventListener("pageshow", resumePrimary);
    document.addEventListener(
      "keydown",
      function (ev) {
        if (!primaryRetry.parked || ev.key !== "Enter") return;
        if (!resumePrimary()) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
      },
      true
    );
    document.addEventListener(
      "pointerdown",
      function (ev) {
        if (!primaryRetry.parked) return;
        if (ev.target && ev.target.closest && ev.target.closest("#rc-extra-keys, #rc-sessions")) return;
        resumePrimary();
      },
      true
    );
  }

  function wrapWebSocket() {
    if (!NativeWS || NativeWS.__rcWrapped) return;
    function RCWebSocket(url, protocols) {
      var list = Array.isArray(protocols)
        ? protocols
        : protocols
          ? [protocols]
          : [];
      var tty = list.indexOf("tty") !== -1 || /\/ws/.test(String(url));
      var pane = tty ? window.__rcNextPane : null;
      if (tty) window.__rcNextPane = null;
      var ws =
        protocols === undefined
          ? new NativeWS(url)
          : new NativeWS(url, protocols);
      if (!tty) return ws;
      window.__rcLastWsUrl = String(url);
      if (pane) {
        ws.__rcPaneId = pane.id;
      } else {
        window.__rcPrimarySocket = ws;
        ensurePrimary();
        if (primaryPane) primaryPane.ws = ws;
        if (!activePaneId || (primaryPane && activePaneId === primaryPane.id)) {
          window.__rcTermSocket = ws;
        }
      }
      var msgListeners = [];
      var closeFns = [];
      var queue = [];
      var live = false;
      var origAdd = ws.addEventListener.bind(ws);
      var origRemove = ws.removeEventListener.bind(ws);
      var scrollHold = { bytes: null };
      function ownerTerm() {
        if (pane && pane.term) return pane.term;
        if (primaryPane && primaryPane.ws === ws && primaryPane.term) return primaryPane.term;
        return null;
      }
      function dispatch(ev) {
        var prev = boundTerm;
        var owner = ownerTerm();
        if (owner) boundTerm = owner;
        var next = preserveScrollUp(softenPrefs(ev), scrollHold);
        boundTerm = prev;
        msgListeners.slice().forEach(function (fn) {
          try {
            fn.call(ws, next);
          } catch (_) {}
        });
      }
      function flush() {
        if (live) return;
        var term = ownerTerm() || xterm();
        if (!term) {
          setTimeout(flush, 20);
          return;
        }
        armScrollback(term);
        live = true;
        var pending = queue;
        queue = [];
        pending.forEach(dispatch);
      }
      ws.addEventListener = function (type, fn, opts) {
        if (type === "message" && typeof fn === "function") {
          msgListeners.push(fn);
          return;
        }
        if (type === "close" && typeof fn === "function") {
          closeFns.push(fn);
          if (!pane) return;
        }
        // ttyd turns reconnect off on any error; the retry above owns that call.
        if (type === "error" && !pane) return;
        return origAdd(type, fn, opts);
      };
      ws.removeEventListener = function (type, fn, opts) {
        if (type === "message") {
          msgListeners = msgListeners.filter(function (item) {
            return item !== fn;
          });
          return;
        }
        if (type === "close") {
          closeFns = closeFns.filter(function (item) {
            return item !== fn;
          });
        }
        return origRemove(type, fn, opts);
      };
      ws.__rcDropClose = function () {
        closeFns.slice().forEach(function (fn) {
          try {
            origRemove("close", fn);
          } catch (_) {}
        });
        closeFns = [];
        ws.__rcIntentional = true;
      };
      origAdd("message", function (ev) {
        if (!live) {
          queue.push(ev);
          return;
        }
        dispatch(ev);
      });
      origAdd("open", function () {
        setTimeout(flush, 0);
        if (pane) return;
        primaryRetry.tries = 0;
        primaryRetry.parked = false;
        window.dispatchEvent(new CustomEvent("rc-ws-open"));
      });
      origAdd("close", function (ev) {
        if (ws.__rcIntentional || pane) return;
        window.dispatchEvent(new CustomEvent("rc-ws-close"));
        var fns = closeFns.slice();
        if (!fns.length) return;
        schedulePrimaryRetry(function (code) {
          if (ws.__rcIntentional) return;
          var fake = { code: code, reason: (ev && ev.reason) || "", wasClean: code === 1000, type: "close" };
          fns.forEach(function (fn) {
            try {
              fn.call(ws, fake);
            } catch (_) {}
          });
        });
      });
      if (!pane) holdStartupSize(ws);
      return ws;
    }
    RCWebSocket.prototype = NativeWS.prototype;
    RCWebSocket.CONNECTING = NativeWS.CONNECTING;
    RCWebSocket.OPEN = NativeWS.OPEN;
    RCWebSocket.CLOSING = NativeWS.CLOSING;
    RCWebSocket.CLOSED = NativeWS.CLOSED;
    RCWebSocket.__rcWrapped = true;
    window.WebSocket = RCWebSocket;
  }

  function detectDevice() {
    var ua = navigator.userAgent || "";
    var fineHover = false;
    var coarse = false;
    try {
      coarse = window.matchMedia("(pointer: coarse)").matches;
      fineHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    } catch (_) {}
    if (/iPhone|iPod/.test(ua)) return "phone";
    if (/iPad/.test(ua)) return "tablet";
    var android = /Android/i.test(ua);
    var mobileToken = /Mobile/i.test(ua);
    if (android && mobileToken) return "phone";
    if (android || /Tablet|Silk|Kindle/i.test(ua)) return "tablet";
    if (fineHover) return "pc";
    if (coarse || /Mobi/i.test(ua)) {
      var minCss = Math.min(window.innerWidth || 0, window.innerHeight || 0);
      var dpr = window.devicePixelRatio || 1;
      var minScreen = Math.min(screen.width || 0, screen.height || 0) / dpr;
      var size = Math.max(minCss, minScreen);
      return size >= 600 ? "tablet" : "phone";
    }
    return "pc";
  }

  function isPasteUi(el) {
    return !!(
      el &&
      el.closest &&
      el.closest(
        ".is-paste, .is-ime, .is-mic, #rc-ek-paste, #rc-ek-ime, #rc-ek-mic, #rc-file-pick"
      )
    );
  }

  function termTextarea() {
    var term = xterm();
    var root = term && term.element;
    if (root && root.querySelector) {
      var own = root.querySelector(".xterm-helper-textarea");
      if (own) return own;
    }
    return document.querySelector(".xterm-helper-textarea");
  }

  function syncImeBtn() {
    var btn = document.getElementById("rc-ek-ime");
    if (btn) btn.classList.toggle("is-on", imeOpen);
    document.documentElement.classList.toggle("rc-ime", imeOpen);
  }

  function armTextarea(ta) {
    if (!ta) return;
    if (imeOpen) {
      ta.removeAttribute("readonly");
      ta.removeAttribute("inputmode");
      return;
    }
    ta.setAttribute("readonly", "readonly");
    ta.setAttribute("inputmode", "none");
    if (selectMode) return;
    try {
      ta.blur();
    } catch (_) {}
  }

  function imeGuarded() {
    return Date.now() < imeGuard;
  }

  function keepImeFocus() {
    if (!imeOpen) return;
    var ta = termTextarea();
    if (!ta) return;
    ta.removeAttribute("readonly");
    ta.removeAttribute("inputmode");
    ta.removeAttribute("disabled");
    try {
      ta.focus({ preventScroll: true });
    } catch (_) {
      try {
        ta.focus();
      } catch (__) {}
    }
  }

  function lockIme() {
    if (imeOpen && imeGuarded()) return;
    imeOpen = false;
    imeGuard = 0;
    writing = false;
    keepFocusUntil = 0;
    syncImeBtn();
    var ta = termTextarea();
    if (!ta) return;
    ta.setAttribute("readonly", "readonly");
    ta.setAttribute("inputmode", "none");
    if (selectMode) return;
    try {
      ta.blur();
    } catch (_) {}
  }

  function unlockIme() {
    endSelect();
    imeOpen = true;
    imeGuard = Date.now() + 1000;
    syncImeBtn();
    var ta = termTextarea();
    if (ta) {
      ta.removeAttribute("readonly");
      ta.removeAttribute("inputmode");
      ta.removeAttribute("disabled");
    }
    focusTerm();
    keepImeFocus();
    requestAnimationFrame(keepImeFocus);
    setTimeout(keepImeFocus, 0);
    setTimeout(keepImeFocus, 80);
  }

  function toggleIme() {
    if (imeOpen) {
      imeGuard = 0;
      lockIme();
      return;
    }
    unlockIme();
  }

  function xterm() {
    var term = window.term;
    return term && typeof term.focus === "function" ? term : null;
  }

  function atBottom() {
    var term = xterm();
    var buf = term && term.buffer && term.buffer.active;
    if (!buf) return true;
    return buf.viewportY >= buf.baseY;
  }

  function pinBottom() {
    stickBottom = true;
    var term = xterm();
    if (term && typeof term.scrollToBottom === "function") term.scrollToBottom();
  }

  function armScrollback(term) {
    if (!term || !term.options) return;
    try {
      term.options.scrollback = 20000;
    } catch (_) {}
  }

  function wipeScrollback() {
    var term = xterm();
    if (!term || typeof term.write !== "function") return;
    term.write(String.fromCharCode(27) + "[3J");
  }

  var histSeen = {};

  function watchNativeClear() {
    function tick() {
      var id = tabId();
      if (!id || document.hidden) return;
      fetch("/rc-hist-size?tab=" + encodeURIComponent(id), { cache: "no-store" })
        .then(function (resp) {
          return resp.ok ? resp.json() : null;
        })
        .then(function (payload) {
          if (!payload || typeof payload.size !== "number") return;
          if (tabId() !== id) return;
          var prev = histSeen[id];
          if (typeof prev === "number" && prev > payload.size && payload.size <= 1) {
            var pane = panes[id];
            var term = pane && pane.term;
            if (term && typeof term.write === "function") {
              term.write(String.fromCharCode(27) + "[3J");
            } else {
              wipeScrollback();
            }
          }
          histSeen[id] = payload.size;
        })
        .catch(function () {});
    }
    tick();
    setInterval(tick, 400);
    window.addEventListener("rc-ws-open", tick);
  }

  function setSelectMode(on) {
    selectMode = !!on;
    document.documentElement.classList.toggle("rc-select", selectMode);
    if (selectMode) {
      lockIme();
      showToast("Seleccioná");
      return;
    }
    lastSel = null;
    var term = xterm();
    if (term && typeof term.clearSelection === "function") {
      try {
        term.clearSelection();
      } catch (_) {}
    }
  }

  function endSelect() {
    if (!selectMode) return;
    setSelectMode(false);
  }

  function cellAt(clientX, clientY) {
    var term = xterm();
    if (!term || !term.cols || !term.rows) return null;
    var screen =
      (term.element && term.element.querySelector(".xterm-screen canvas")) ||
      (term.element && term.element.querySelector(".xterm-screen")) ||
      term.element;
    if (!screen || !screen.getBoundingClientRect) return null;
    var rect = screen.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return null;
    var col = Math.floor((clientX - rect.left) / (rect.width / term.cols));
    var row = Math.floor((clientY - rect.top) / (rect.height / term.rows));
    return {
      col: Math.max(0, Math.min(term.cols - 1, col)),
      row: Math.max(0, Math.min(term.rows - 1, row)),
    };
  }

  function bufferTop() {
    var term = xterm();
    var buf = term && term.buffer && term.buffer.active;
    if (buf && typeof buf.viewportY === "number") return buf.viewportY;
    return 0;
  }

  function toBufferCell(cell) {
    if (!cell) return null;
    return { col: cell.col, row: cell.row + bufferTop() };
  }

  function wordBounds(col, bufRow) {
    var term = xterm();
    var buf = term && term.buffer && term.buffer.active;
    if (!buf || typeof buf.getLine !== "function") {
      return { col: col, end: col };
    }
    var line = buf.getLine(bufRow);
    var text =
      line && typeof line.translateToString === "function"
        ? line.translateToString(false)
        : "";
    if (!text) return { col: col, end: col };
    if (col >= text.length) col = text.length - 1;
    if (col < 0) return { col: 0, end: 0 };
    function span(at) {
      var a = at;
      var b = at;
      while (a > 0 && !/\s/.test(text.charAt(a - 1))) a--;
      while (b + 1 < text.length && !/\s/.test(text.charAt(b + 1))) b++;
      return { col: a, end: b };
    }
    if (!/\s/.test(text.charAt(col))) return span(col);
    var left = col - 1;
    while (left >= 0 && /\s/.test(text.charAt(left))) left--;
    var right = col + 1;
    while (right < text.length && /\s/.test(text.charAt(right))) right++;
    if (left >= 0 && (right >= text.length || col - left <= right - col)) return span(left);
    if (right < text.length) return span(right);
    return { col: col, end: col };
  }

  function applyCellSelect(from, to) {
    var term = xterm();
    if (!term || !from || !to || typeof term.select !== "function") return;
    var a = from;
    var b = to;
    if (b.row < a.row || (b.row === a.row && b.col < a.col)) {
      a = to;
      b = from;
    }
    lastSel = { from: { col: a.col, row: a.row }, to: { col: b.col, row: b.row } };
    term.select(a.col, a.row, Math.max(1, (b.row - a.row) * term.cols + (b.col - a.col) + 1));
  }

  function restoreSel() {
    if (!selectMode || !lastSel) return;
    applyCellSelect(lastSel.from, lastSel.to);
  }

  function readSelText() {
    var term = xterm();
    var from = lastSel && lastSel.from;
    var to = lastSel && lastSel.to;
    if ((!from || !to) && term && typeof term.getSelectionPosition === "function") {
      try {
        var pos = term.getSelectionPosition();
        if (pos && pos.startColumn != null) {
          from = { col: pos.startColumn, row: pos.startRow };
          to = {
            col: Math.max(0, (pos.endColumn || 1) - 1),
            row: pos.endRow,
          };
        } else if (pos && pos.start && pos.end) {
          from = { col: pos.start.x, row: pos.start.y };
          to = { col: Math.max(0, pos.end.x - 1), row: pos.end.y };
        }
      } catch (_) {}
    }
    if (!term || !from || !to) return "";
    var buf = term.buffer && term.buffer.active;
    if (!buf || typeof buf.getLine !== "function") return "";
    if (to.row < from.row || (to.row === from.row && to.col < from.col)) {
      var swap = from;
      from = to;
      to = swap;
    }
    var lines = [];
    var y;
    for (y = from.row; y <= to.row; y++) {
      var line = buf.getLine(y);
      if (!line || typeof line.translateToString !== "function") {
        lines.push("");
        continue;
      }
      var start = y === from.row ? from.col : 0;
      var end = y === to.row ? to.col + 1 : term.cols;
      lines.push(line.translateToString(false, start, end).replace(/[ \t]+$/g, ""));
    }
    return lines.join("\n").replace(/\n+$/g, "");
  }

  function isTypeTarget(el) {
    if (!el) return true;
    if (el.id === "rc-file-pick") return false;
    if (el.closest && el.closest("#rc-sessions, #rc-file-pick")) return false;
    return true;
  }

  function focusTerm() {
    if (!imeOpen) {
      armTextarea(termTextarea());
      return;
    }
    var term = xterm();
    if (term) {
      try {
        term.focus();
      } catch (_) {}
    }
    var ta = termTextarea();
    if (ta) {
      armTextarea(ta);
      try {
        ta.focus({ preventScroll: true });
      } catch (_) {
        ta.focus();
      }
    }
  }

  function tabId() {
    var id = "";
    try {
      id = document.documentElement.dataset.rcTab || "";
    } catch (_) {}
    if (!/^rc[a-z0-9]{10,32}$/.test(id)) {
      try {
        id = sessionStorage.getItem("rc-tab-id") || "";
      } catch (_) {}
    }
    return /^rc[a-z0-9]{10,32}$/.test(id) ? id : "";
  }

  function scrollToWrite() {
    pinBottom();
    var term = xterm();
    if (imeOpen && term) {
      try {
        term.focus();
      } catch (_) {}
    }
    var ta = termTextarea();
    if (ta) {
      armTextarea(ta);
      if (imeOpen) {
        try {
          ta.focus({ preventScroll: true });
        } catch (_) {
          ta.focus();
        }
      }
    }
  }

  function cancelCopyMode() {
    var tab = tabId();
    if (!tab) return Promise.resolve();
    return fetch("/rc-copy-cancel?tab=" + encodeURIComponent(tab), {
      method: "POST",
      cache: "no-store",
    }).then(
      function () {},
      function () {}
    );
  }

  function armKeepFocus() {
    if (!imeOpen) return;
    keepFocusUntil = Date.now() + 900;
    focusTerm();
  }

  function setLinkState(open) {
    if (!bar) return;
    bar.classList.toggle("is-offline", !open);
    var label = bar.querySelector(".rc-ek-link");
    if (!label) {
      label = document.createElement("div");
      label.className = "rc-ek-link";
      label.setAttribute("aria-live", "polite");
      bar.insertBefore(label, bar.firstChild);
    }
    label.textContent = open ? "" : "Sin conexión";
  }

  function sendInput(text) {
    if (!text) return false;
    var ws = window.__rcTermSocket;
    if (!ws || ws.readyState !== 1) {
      setLinkState(false);
      return false;
    }
    var body = encoder.encode(text);
    var payload = new Uint8Array(body.length + 1);
    payload[0] = INPUT;
    payload.set(body, 1);
    ws.send(payload);
    lastSentAt = Date.now();
    setLinkState(true);
    return true;
  }

  function recentlySent() {
    return Date.now() - lastSentAt < 60;
  }

  function normalizePaste(text) {
    return String(text || "").replace(/\r\n/g, "\n").replace(/\n/g, "\r");
  }

  function sendPaste(text) {
    var out = normalizePaste(text);
    if (!out) return false;
    resetMirror();
    holdImeForPaste();
    var i = 0;
    function step() {
      if (i >= out.length) {
        holdImeForPaste();
        focusTerm();
        return;
      }
      sendInput(out.slice(i, i + PASTE_CHUNK));
      i += PASTE_CHUNK;
      if (i < out.length) setTimeout(step, 16);
      else {
        holdImeForPaste();
        focusTerm();
      }
    }
    beginWriting().then(step);
    return true;
  }

  // The redraw after a paste can scroll the viewport for a moment, which would
  // otherwise trip lockIme() and close the keyboard mid-sentence.
  function holdImeForPaste() {
    if (!imeOpen) return;
    var until = Date.now() + 1500;
    imeGuard = Math.max(imeGuard, until);
    keepFocusUntil = Math.max(keepFocusUntil, until);
  }

  function showToast(text) {
    var el = document.getElementById("rc-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "rc-toast";
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(function () {
      el.hidden = true;
    }, 2600);
  }

  function shellQuote(path) {
    return "'" + String(path).replace(/'/g, "'\\''") + "'";
  }

  function mintName(ext) {
    var bytes = new Uint8Array(4);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      bytes[0] = Math.floor(Math.random() * 256);
      bytes[1] = Math.floor(Math.random() * 256);
      bytes[2] = Math.floor(Math.random() * 256);
      bytes[3] = Math.floor(Math.random() * 256);
    }
    var hex = "";
    for (var i = 0; i < bytes.length; i++) {
      hex += ("0" + bytes[i].toString(16)).slice(-2);
    }
    return "paste-" + hex + "." + ext;
  }

  function safeExt(raw) {
    var ext = String(raw || "")
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "");
    if (ext === "jpeg") ext = "jpg";
    return PASTE_EXT[ext] ? ext : "bin";
  }

  function extFromType(type) {
    var t = String(type || "").toLowerCase();
    if (t.indexOf("image/webp") === 0) return "webp";
    if (t.indexOf("image/jpeg") === 0) return "jpg";
    if (t.indexOf("image/jpg") === 0) return "jpg";
    if (t.indexOf("image/png") === 0) return "png";
    if (t.indexOf("image/gif") === 0) return "gif";
    var slash = t.lastIndexOf("/");
    if (slash !== -1) return safeExt(t.slice(slash + 1));
    return "";
  }

  function extFromName(name) {
    var base = String(name || "").split(/[\\/]/).pop() || "";
    var dot = base.lastIndexOf(".");
    if (dot < 0) return "";
    return safeExt(base.slice(dot + 1));
  }

  // Formats a canvas can re-encode; anything else (gif, svg…) is kept as-is so
  // it does not lose animation or vectors.
  var CANVAS_MIME = { jpg: "image/jpeg", png: "image/png", webp: "image/webp" };

  function readJson(resp) {
    return resp.json().catch(function () {
      return {};
    });
  }

  function reserveName(name) {
    return fetch("/rc-paste-reserve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name }),
    }).then(function (resp) {
      return readJson(resp).then(function (data) {
        if (!resp.ok || !data.path) {
          throw new Error((data && data.error) || "reserve");
        }
        return data;
      });
    });
  }

  function putPasteFile(name, blob, type) {
    return fetch("/rc-paste-file?name=" + encodeURIComponent(name), {
      method: "PUT",
      headers: { "Content-Type": type || "application/octet-stream" },
      body: blob,
    }).then(function (resp) {
      return readJson(resp).then(function (data) {
        if (!resp.ok || !data.path) throw new Error((data && data.error) || "put");
        return data;
      });
    });
  }

  function saveBlob(ext, blob, type) {
    function attempt(left) {
      var name = mintName(ext);
      return reserveName(name)
        .catch(function () {
          return { name: name };
        })
        .then(function () {
          return putPasteFile(name, blob, type);
        })
        .catch(function (err) {
          if (left > 1) return attempt(left - 1);
          throw err;
        });
    }
    return attempt(3);
  }

  function compressImage(blob, ext) {
    return new Promise(function (resolve) {
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        var w = img.naturalWidth || img.width;
        var h = img.naturalHeight || img.height;
        if (!w || !h) {
          resolve(blob);
          return;
        }
        var scale = Math.min(1, PASTE_EDGE / Math.max(w, h));
        var canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(w * scale));
        canvas.height = Math.max(1, Math.round(h * scale));
        var ctx = canvas.getContext("2d");
        if (!ctx) {
          resolve(blob);
          return;
        }
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        var mime = CANVAS_MIME[ext];
        canvas.toBlob(
          function (out) {
            var ok = out && out.size && out.type === mime && out.size < blob.size;
            resolve(ok ? out : blob);
          },
          mime,
          PASTE_QUALITY
        );
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        resolve(blob);
      };
      img.src = url;
    });
  }

  function ingestBlob(blob, asImage) {
    if (!blob || pasteBusy) return Promise.resolve(false);
    pasteBusy = true;
    pasteGuard = Date.now() + 800;
    showToast("Guardando…");
    var ext = extFromName(blob.name) || extFromType(blob.type) || "bin";
    if (ext === "bin") ext = extFromType(blob.type) || "bin";
    var squeeze = asImage && CANVAS_MIME[ext];
    var work = squeeze ? compressImage(blob, ext) : Promise.resolve(blob);
    return work
      .then(function (out) {
        var type = out.type || blob.type || "application/octet-stream";
        return saveBlob(ext, out, type);
      })
      .then(function (info) {
        sendPaste(shellQuote(info.path));
        showToast(info.name);
        return true;
      })
      .catch(function () {
        showToast("No pude guardar el archivo");
        return false;
      })
      .then(function (ok) {
        pasteBusy = false;
        return ok;
      });
  }

  function fileFromClipboardData(data) {
    if (!data) return null;
    var items = data.items;
    if (items) {
      for (var i = 0; i < items.length; i++) {
        if (items[i].type && items[i].type.indexOf("image/") === 0) {
          var file = items[i].getAsFile();
          if (file) return file;
        }
      }
    }
    var files = data.files;
    if (files) {
      for (var j = 0; j < files.length; j++) {
        if (files[j].type && files[j].type.indexOf("image/") === 0) {
          return files[j];
        }
      }
    }
    return null;
  }

  function applyClipboardData(data) {
    var text = "";
    try {
      text = (data && data.getData && data.getData("text/plain")) || "";
    } catch (_) {}
    if (text) {
      sendPaste(text);
      return true;
    }
    var file = fileFromClipboardData(data);
    if (file) {
      ingestBlob(file, true);
      return true;
    }
    return false;
  }

  function pasteTextOnly() {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      showToast("Pegá con Ctrl+Shift+V");
      return;
    }
    navigator.clipboard
      .readText()
      .then(function (text) {
        if (text) sendPaste(text);
        else showToast("El clipboard está vacío");
      })
      .catch(function () {
        showToast("Pegá con Ctrl+Shift+V");
      });
  }

  function applyClipboardItems(items) {
    var i = 0;
    function next() {
      if (!items || i >= items.length) {
        pasteTextOnly();
        return;
      }
      var item = items[i++];
      var types = item.types || [];
      var textType = types.indexOf("text/plain") !== -1 ? "text/plain" : "";
      var imageType = "";
      for (var t = 0; t < types.length; t++) {
        if (types[t].indexOf("image/") === 0) {
          imageType = types[t];
          break;
        }
      }
      if (textType) {
        item
          .getType(textType)
          .then(function (blob) {
            return blob.text();
          })
          .then(function (text) {
            if (text) sendPaste(text);
            else if (imageType) return sendImageFromItem(item, imageType);
            else next();
          })
          .catch(next);
        return;
      }
      if (imageType) {
        sendImageFromItem(item, imageType);
        return;
      }
      next();
    }
    next();
  }

  function sendImageFromItem(item, type) {
    item
      .getType(type)
      .then(function (blob) {
        return ingestBlob(blob, true);
      })
      .catch(function () {
        showToast("No pude leer la imagen");
      });
  }

  function pasteFromClipboard() {
    if (navigator.clipboard && navigator.clipboard.read) {
      navigator.clipboard
        .read()
        .then(applyClipboardItems)
        .catch(pasteTextOnly);
      return;
    }
    pasteTextOnly();
  }

  function clearSticky() {
    var changed = false;
    ["shift", "ctrl", "alt"].forEach(function (name) {
      if (mods[name] && !locks[name]) {
        mods[name] = false;
        changed = true;
      }
    });
    if (changed) renderMods();
  }

  function ctrlify(text) {
    var out = "";
    for (var i = 0; i < text.length; i++) {
      var ch = text.charAt(i);
      var code = ch.toUpperCase().charCodeAt(0);
      if (code >= 64 && code <= 95) out += String.fromCharCode(code - 64);
      else if (ch === " ") out += "\u0000";
      else if (ch === "?") out += "\u007f";
      else out += ch;
    }
    return out;
  }

  function anyMod() {
    return mods.shift || mods.ctrl || mods.alt;
  }

  function applyMods(text) {
    var out = text;
    if (mods.shift) {
      // Shift+Tab is back-tab; Shift+Enter is the "new line, don't send" that
      // AI CLIs read as ESC CR.
      if (out === "\t") return (mods.alt ? "\u001b" : "") + "\u001b[Z";
      if (out === "\r") return "\u001b\r";
      out = out.toUpperCase();
    }
    if (mods.ctrl) out = ctrlify(out);
    if (mods.alt) out = "\u001b" + out;
    return out;
  }

  function modifierParam() {
    var n = 1;
    if (mods.shift) n += 1;
    if (mods.alt) n += 2;
    if (mods.ctrl) n += 4;
    return n;
  }

  function specialWithMods(def) {
    if (!anyMod()) return def.seq || def.text || "";
    if (def.text) return applyMods(def.text);
    var letter = ARROW_LETTER[def.key];
    var n = modifierParam();
    if (letter) return "\u001b[1;" + n + letter;
    if (def.id === "pgup") return "\u001b[5;" + n + "~";
    if (def.id === "pgdn") return "\u001b[6;" + n + "~";
    if (def.id === "tab" && mods.ctrl) return "";
    if (def.id === "esc") return mods.alt ? "\u001b\u001b" : "\u001b";
    return applyMods(def.seq || "");
  }

  function toggleMod(name) {
    var now = Date.now();
    if (now - lastTap[name] < 380) {
      locks[name] = !locks[name];
      mods[name] = locks[name];
    } else if (locks[name]) {
      locks[name] = false;
      mods[name] = false;
    } else {
      mods[name] = !mods[name];
    }
    lastTap[name] = now;
    renderMods();
  }

  function renderMods() {
    if (!bar) return;
    ["shift", "ctrl", "alt"].forEach(function (name) {
      var el = bar.querySelector('[data-rc-id="' + name + '"]');
      if (!el) return;
      el.classList.toggle("is-on", mods[name] && !locks[name]);
      el.classList.toggle("is-lock", !!locks[name]);
    });
  }

  function pressKey(def) {
    if (def.paste) {
      openFilePicker();
      return;
    }
    if (def.mod) {
      toggleMod(def.mod);
      return;
    }
    if ((def.id === "pgup" || def.id === "pgdn") && !anyMod()) {
      endSelect();
      scrollXtermPage(def.id === "pgup" ? -1 : 1);
      return;
    }
    endSelect();
    resetMirror();
    sendInput(specialWithMods(def));
    clearSticky();
  }

  function scrollXtermPage(dir) {
    var term = xterm();
    if (!term || typeof term.scrollPages !== "function") return;
    term.scrollPages(dir);
    stickBottom = atBottom();
  }

  function layout() {
    if (!bar || !document.documentElement.classList.contains("rc-touch")) return;
    var vv = window.visualViewport;
    var barH = bar.offsetHeight || 96;
    var viewH = vv ? vv.height : window.innerHeight;
    var viewW = vv ? vv.width : window.innerWidth;
    var top = vv ? vv.offsetTop : 0;
    var left = vv ? vv.offsetLeft : 0;
    document.documentElement.style.setProperty("--rc-ek-h", barH + "px");
    document.documentElement.style.setProperty("--rc-vv-h", viewH + "px");
    bar.style.top = top + viewH - barH + "px";
    bar.style.left = left + "px";
    bar.style.width = viewW + "px";
    var sessions = document.getElementById("rc-sessions");
    var sessionH = 40;
    if (sessions) {
      sessions.style.top = top + "px";
      sessions.style.left = left + "px";
      sessions.style.width = viewW + "px";
      sessionH = sessions.offsetHeight || 40;
    }
    document.documentElement.style.setProperty("--rc-bar-h", sessionH + "px");
    var hosts = document.querySelectorAll("#terminal-container, .rc-pane");
    hosts.forEach(function (host) {
      host.style.top = top + sessionH + "px";
      host.style.left = left + "px";
      host.style.right = "auto";
      host.style.bottom = "auto";
      host.style.width = viewW + "px";
      host.style.height = Math.max(48, viewH - sessionH - barH) + "px";
    });
    var fit = viewW + "x" + viewH + "@" + top + "," + left + ":" + barH;
    if (fit !== lastFit) {
      lastFit = fit;
      window.dispatchEvent(new Event("resize"));
    }
    if (imeOpen && imeGuarded()) {
      keepImeFocus();
    } else if (writing || stickBottom) {
      requestAnimationFrame(pinBottom);
    }
  }

  function requestLayout() {
    if (layoutRaf) cancelAnimationFrame(layoutRaf);
    layoutRaf = requestAnimationFrame(function () {
      layoutRaf = 0;
      layout();
    });
  }

  function bindKeepFocus(el) {
    function hold(ev) {
      if (isPasteUi(ev.target)) return;
      ev.preventDefault();
      armKeepFocus();
    }
    el.addEventListener("pointerdown", hold, { passive: false });
    el.addEventListener("touchstart", hold, { passive: false });
    el.addEventListener("mousedown", hold, { passive: false });
    el.addEventListener("click", function (ev) {
      if (isPasteUi(ev.target)) return;
      ev.preventDefault();
      ev.stopPropagation();
      armKeepFocus();
    });
    el.addEventListener("contextmenu", function (ev) {
      ev.preventDefault();
    });
  }

  function mountBar() {
    if (document.getElementById("rc-extra-keys")) return;
    bar = document.createElement("div");
    bar.id = "rc-extra-keys";
    bar.setAttribute("aria-label", "Teclas extra");
    ROWS.forEach(function (row) {
      var rowEl = document.createElement("div");
      rowEl.className = "rc-ek-row";
      row.forEach(function (def) {
        var key = document.createElement("div");
        key.className = "rc-ek-key" + (def.mod ? " is-mod" : "");
        key.setAttribute("role", "button");
        key.setAttribute("tabindex", "-1");
        key.dataset.rcId = def.id;
        key.textContent = def.label;
        bindKeepFocus(key);
        key.addEventListener(
          "pointerdown",
          function (ev) {
            key.classList.add("is-down");
            pressKey(def);
          },
          { passive: false }
        );
        key.addEventListener("pointerup", function () {
          key.classList.remove("is-down");
        });
        key.addEventListener("pointercancel", function () {
          key.classList.remove("is-down");
        });
        key.addEventListener("pointerleave", function () {
          key.classList.remove("is-down");
        });
        rowEl.appendChild(key);
      });
      bar.appendChild(rowEl);
    });
    var pasteBtn = document.createElement("button");
    pasteBtn.type = "button";
    pasteBtn.id = "rc-ek-paste";
    pasteBtn.className = "is-paste";
    pasteBtn.setAttribute("aria-label", "Adjuntar archivo");
    pasteBtn.innerHTML = PASTE_ICON;
    pasteBtn.addEventListener(
      "pointerdown",
      function (ev) {
        ev.stopPropagation();
        pasteBtn.classList.add("is-down");
        openFilePicker();
      },
      { passive: true }
    );
    pasteBtn.addEventListener("pointerup", function () {
      pasteBtn.classList.remove("is-down");
    });
    pasteBtn.addEventListener("pointercancel", function () {
      pasteBtn.classList.remove("is-down");
    });
    pasteBtn.addEventListener("pointerleave", function () {
      pasteBtn.classList.remove("is-down");
    });
    bar.appendChild(pasteBtn);
    var imeBtn = document.createElement("button");
    imeBtn.type = "button";
    imeBtn.id = "rc-ek-ime";
    imeBtn.className = "is-ime";
    imeBtn.setAttribute("aria-label", "Teclado");
    imeBtn.innerHTML = IME_ICON;
    imeBtn.addEventListener(
      "pointerdown",
      function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        imeBtn.classList.add("is-down");
        toggleIme();
      },
      { passive: false }
    );
    imeBtn.addEventListener(
      "click",
      function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      },
      { passive: false }
    );
    imeBtn.addEventListener("pointerup", function () {
      imeBtn.classList.remove("is-down");
    });
    imeBtn.addEventListener("pointercancel", function () {
      imeBtn.classList.remove("is-down");
    });
    imeBtn.addEventListener("pointerleave", function () {
      imeBtn.classList.remove("is-down");
    });
    bar.appendChild(imeBtn);
    var micBtn = document.createElement("button");
    micBtn.type = "button";
    micBtn.id = "rc-ek-mic";
    micBtn.className = "is-mic";
    micBtn.setAttribute("aria-label", "Dictar");
    micBtn.innerHTML = MIC_ICON;
    micBtn.addEventListener(
      "pointerdown",
      function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        micBtn.classList.add("is-down");
        toggleDictation();
      },
      { passive: false }
    );
    micBtn.addEventListener(
      "click",
      function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      },
      { passive: false }
    );
    micBtn.addEventListener("pointerup", function () {
      micBtn.classList.remove("is-down");
    });
    micBtn.addEventListener("pointercancel", function () {
      micBtn.classList.remove("is-down");
    });
    micBtn.addEventListener("pointerleave", function () {
      micBtn.classList.remove("is-down");
    });
    bar.appendChild(micBtn);
    syncImeBtn();
    bindKeepFocus(bar);
    document.body.appendChild(bar);
    setLinkState(!!(window.__rcTermSocket && window.__rcTermSocket.readyState === 1));
    window.addEventListener("rc-ws-open", function () {
      setLinkState(true);
    });
    window.addEventListener("rc-ws-close", function () {
      setLinkState(false);
    });
    requestLayout();

    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", requestLayout);
      window.visualViewport.addEventListener("scroll", requestLayout);
    }
    window.addEventListener("resize", requestLayout);
    window.addEventListener("orientationchange", function () {
      setTimeout(requestLayout, 80);
    });
    document.addEventListener("focusin", function (ev) {
      if (imeOpen && bar.contains(ev.target)) {
        focusTerm();
      }
    });
    document.addEventListener("focusout", function () {
      if (imeOpen && Date.now() < keepFocusUntil) {
        setTimeout(focusTerm, 0);
      }
    });

    var mo = new MutationObserver(function () {
      if (document.getElementById("terminal-container")) {
        requestLayout();
      }
    });
    mo.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(function () {
      mo.disconnect();
      requestLayout();
    }, 4000);
  }

  function interceptTyped(text) {
    if (!anyMod() || !text) return false;
    if (recentlySent()) return true;
    sendInput(applyMods(text));
    clearSticky();
    return true;
  }

  function bootInterceptors() {
    document.addEventListener(
      "keydown",
      function (ev) {
        if (!anyMod()) return;
        if (ev.isComposing) return;
        if (ev.key === "Control" || ev.key === "Alt" || ev.key === "Meta") return;
        if (ev.ctrlKey || ev.altKey) return;
        var data = "";
        if (ev.key && ev.key.length === 1) data = ev.key;
        else if (ev.key === "Enter") data = "\r";
        else if (ev.key === "Tab") data = "\t";
        else if (ev.key === "Escape") data = "\u001b";
        else if (ev.key === "Backspace") data = "\u007f";
        else {
          var fake = {
            id: "",
            key: ev.key,
            seq: "",
          };
          if (ARROW_LETTER[ev.key]) fake.seq = "\u001b[" + ARROW_LETTER[ev.key];
          data = specialWithMods(fake);
        }
        if (!data) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        if (ev.key && (ev.key.length === 1 || ev.key === "Enter" || ev.key === "Tab" || ev.key === "Escape" || ev.key === "Backspace")) {
          interceptTyped(data);
          return;
        }
        sendInput(data);
        clearSticky();
      },
      true
    );
    document.addEventListener(
      "beforeinput",
      function (ev) {
        if (!anyMod()) return;
        if (!ev.data) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        interceptTyped(ev.data);
      },
      true
    );
  }

  function bootTouchScroll() {
    var startX = 0;
    var startY = 0;
    var lastX = 0;
    var lastY = 0;
    var scrolling = false;
    var selecting = false;
    var active = false;
    var holdTimer = 0;
    var selFrom = null;
    var wheel = false;
    var wheelY = 0;
    var HOLD_MS = 450;

    // Fullscreen TUIs (Claude Code, Codex…) keep their history inside the app,
    // so dragging xterm's viewport never reaches it. When the app asked for the
    // mouse, turn the drag into wheel events like a desktop scroll would.
    function appWantsWheel() {
      var term = xterm();
      var mode = term && term.modes && term.modes.mouseTrackingMode;
      return !!mode && mode !== "none";
    }

    function cellHeight() {
      var term = xterm();
      var el = term && term.element && term.element.querySelector(".xterm-screen");
      var h = el && el.getBoundingClientRect ? el.getBoundingClientRect().height : 0;
      return term && term.rows && h > 0 ? h / term.rows : 18;
    }

    function sendWheel(up, steps, x, y) {
      var cell = cellAt(x, y) || { col: 0, row: 0 };
      var esc = String.fromCharCode(27);
      var one = esc + "[<" + (up ? 64 : 65) + ";" + (cell.col + 1) + ";" + (cell.row + 1) + "M";
      var out = "";
      for (var i = 0; i < steps; i++) out += one;
      sendInput(out);
    }

    function fromKeys(ev) {
      var t = ev.target;
      return !!(
        t &&
        t.closest &&
        t.closest("#rc-extra-keys, #rc-file-pick")
      );
    }

    function clearHold() {
      if (holdTimer) {
        clearTimeout(holdTimer);
        holdTimer = 0;
      }
    }

    function onStart(ev) {
      if (!ev.touches || ev.touches.length !== 1 || fromKeys(ev)) {
        active = false;
        return;
      }
      if (imeGuarded()) {
        active = false;
        return;
      }
      var t = ev.touches[0];
      startX = lastX = t.clientX;
      startY = lastY = t.clientY;
      scrolling = false;
      selecting = false;
      selFrom = null;
      wheel = appWantsWheel();
      wheelY = t.clientY;
      active = true;
      if (selectMode) {
        ev.preventDefault();
        selecting = true;
        selFrom = toBufferCell(cellAt(t.clientX, t.clientY));
        if (selFrom) applyCellSelect(selFrom, selFrom);
        return;
      }
      clearHold();
      holdTimer = setTimeout(function () {
        holdTimer = 0;
        if (!active || scrolling) return;
        selecting = true;
        setSelectMode(true);
        var at = toBufferCell(cellAt(lastX, lastY));
        if (at) {
          var word = wordBounds(at.col, at.row);
          selFrom = { col: word.col, row: at.row };
          applyCellSelect(selFrom, { col: word.end, row: at.row });
        }
      }, HOLD_MS);
    }

    function onMove(ev) {
      if (!active || !ev.touches || ev.touches.length !== 1) return;
      var t = ev.touches[0];
      var x = t.clientX;
      var y = t.clientY;
      lastX = x;
      lastY = y;
      if (selectMode || selecting) {
        ev.preventDefault();
        var to = toBufferCell(cellAt(x, y));
        if (selFrom && to) applyCellSelect(selFrom, to);
        return;
      }
      if (wheel) ev.preventDefault();
      if (!scrolling && Math.abs(y - startY) < 8 && Math.abs(x - startX) < 8) return;
      clearHold();
      scrolling = true;
      writing = false;
      if (imeOpen && !imeGuarded()) lockIme();
      if (!wheel) return;
      var step = cellHeight();
      var steps = Math.trunc((y - wheelY) / step);
      if (!steps) return;
      wheelY += steps * step;
      sendWheel(steps > 0, Math.min(Math.abs(steps), 20), x, y);
    }

    function onEnd() {
      clearHold();
      if (selecting) {
        var end = toBufferCell(cellAt(lastX, lastY));
        if (selFrom && end) applyCellSelect(selFrom, end);
        requestAnimationFrame(restoreSel);
        setTimeout(restoreSel, 0);
        setTimeout(restoreSel, 50);
      } else if (!scrolling) {
        lockIme();
      }
      active = false;
      scrolling = false;
      selecting = false;
      selFrom = null;
      wheel = false;
    }

    function bind(el) {
      if (!el || el.dataset.rcTouchScroll === "1") return;
      el.dataset.rcTouchScroll = "1";
      el.addEventListener("touchstart", onStart, { passive: false });
      el.addEventListener("touchmove", onMove, { passive: false });
      el.addEventListener("touchend", onEnd, { passive: true });
      el.addEventListener("touchcancel", onEnd, { passive: true });
    }

    bindTouchHost = bind;
    bind(document.getElementById("terminal-container"));
    var mo = new MutationObserver(function () {
      bind(document.getElementById("terminal-container"));
      document.querySelectorAll(".rc-pane").forEach(bind);
    });
    mo.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(function () {
      mo.disconnect();
      bind(document.getElementById("terminal-container"));
      document.querySelectorAll(".rc-pane").forEach(bind);
    }, 4000);
  }

  function beginWriting() {
    endSelect();
    writing = true;
    scrollToWrite();
    return cancelCopyMode().then(function () {
      scrollToWrite();
      requestAnimationFrame(function () {
        scrollToWrite();
      });
    });
  }

  function pumpWrite() {
    if (writeBusy || !writeQ) return;
    writeBusy = true;
    var pending = writeQ;
    writeQ = "";
    beginWriting().then(function () {
      if (pending) sendInput(normalizePaste(pending));
      writeBusy = false;
      if (writeQ) pumpWrite();
    });
  }

  function flushTyped(text) {
    if (!text) return;
    writeQ += text;
    pumpWrite();
  }

  // The hidden textarea mirrors what was typed on the current line. Gboard
  // autocorrect, swipe and voice typing rewrite that text in place, so each
  // input is turned into "erase what changed, type the new tail" instead of
  // only forwarding the inserted word.
  var mirrorSent = "";

  function resetMirror() {
    mirrorSent = "";
    var ta = termTextarea();
    if (ta && ta.value) ta.value = "";
  }

  function syncMirror(ta) {
    var now = ta.value;
    if (now === mirrorSent) return;
    var p = 0;
    var max = Math.min(now.length, mirrorSent.length);
    while (p < max && now.charCodeAt(p) === mirrorSent.charCodeAt(p)) p++;
    var lead = p > 0 ? now.charCodeAt(p - 1) : 0;
    if (lead >= 0xd800 && lead <= 0xdbff) p--;
    var erase = Array.from(mirrorSent.slice(p)).length;
    var out = "";
    for (var i = 0; i < erase; i++) out += "\u007f";
    out += now.slice(p);
    mirrorSent = now;
    flushTyped(out);
  }

  function isTermTextarea(el) {
    return !!(el && el.classList && el.classList.contains("xterm-helper-textarea"));
  }

  function bootTypeToTty() {
    document.addEventListener(
      "keydown",
      function (ev) {
        if (ev.defaultPrevented || ev.isComposing) return;
        if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
        if (!isTypeTarget(ev.target)) return;
        var ta = termTextarea();
        // With text in the mirror, let the browser delete it; the input diff
        // sends the DEL so both sides stay in step.
        if (ev.key === "Backspace" && ta && ev.target === ta && ta.value) return;
        var seq = "";
        if (ev.key === "Enter") seq = "\r";
        else if (ev.key === "Backspace") seq = "\u007f";
        else if (ev.key === "Tab") seq = "\t";
        else if (ev.key === "Escape") seq = "\u001b";
        else if (ev.key === "Delete") seq = "\u001b[3~";
        else if (ARROW_LETTER[ev.key]) seq = "\u001b[" + ARROW_LETTER[ev.key];
        if (!seq) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        if (seq !== "\u007f") resetMirror();
        flushTyped(seq);
      },
      true
    );
    document.addEventListener(
      "input",
      function (ev) {
        var ta = termTextarea();
        if (!ta || ev.target !== ta) return;
        ev.stopImmediatePropagation();
        if (/[\r\n]/.test(ta.value)) {
          var line = ta.value.replace(/\r\n?/g, "\n");
          var cut = line.lastIndexOf("\n");
          ta.value = line.slice(0, cut);
          syncMirror(ta);
          flushTyped("\r");
          ta.value = line.slice(cut + 1);
          mirrorSent = "";
          syncMirror(ta);
          return;
        }
        syncMirror(ta);
        if (ta.value.length > 400 && !ev.isComposing) resetMirror();
      },
      true
    );
    // xterm reads the same textarea on its own (keydown 229, composition,
    // keypress, input) and would send a second copy of every word.
    ["compositionstart", "compositionupdate", "compositionend", "keypress", "keydown", "keyup"].forEach(
      function (type) {
        document.addEventListener(
          type,
          function (ev) {
            if (!isTermTextarea(ev.target)) return;
            if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
            ev.stopImmediatePropagation();
          },
          true
        );
      }
    );
  }

  function filePicker() {
    var input = document.getElementById("rc-file-pick");
    if (input) return input;
    input = document.createElement("input");
    input.id = "rc-file-pick";
    input.type = "file";
    input.hidden = true;
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      input.value = "";
      if (!file) {
        focusTerm();
        return;
      }
      var image = (file.type || "").indexOf("image/") === 0;
      ingestBlob(file, image);
      focusTerm();
    });
    document.body.appendChild(input);
    return input;
  }

  var dictation = null;
  var dictationSent = false;

  function syncMicBtn() {
    var btn = document.getElementById("rc-ek-mic");
    if (btn) btn.classList.toggle("is-on", !!dictation);
  }

  function stopDictation() {
    var rec = dictation;
    if (!rec) return;
    dictation = null;
    rec.__rcStopping = true;
    syncMicBtn();
    try {
      rec.stop();
    } catch (_) {}
  }

  // Chrome on Android repeats every phrase as a growing "final" result when
  // continuous is on ("Me", "Me gustaría", "Me gustaría saber"…). Listen one
  // utterance at a time instead and reopen the mic while the button is on.
  function listenOnce() {
    var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    var rec = new Recognition();
    rec.lang = "es-ES";
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    var said = "";
    var silent = false;
    rec.onresult = function (ev) {
      if (dictation !== rec) return;
      var text = "";
      var final = false;
      for (var i = 0; i < ev.results.length; i++) {
        var res = ev.results[i];
        text = String(res[0] && res[0].transcript ? res[0].transcript : "").trim() || text;
        if (res.isFinal) final = true;
      }
      if (final) said = text;
      showToast(text || "Escuchando…");
    };
    rec.onerror = function (ev) {
      if (dictation !== rec) return;
      var code = ev && ev.error;
      if (code === "no-speech" || code === "aborted") {
        silent = true;
        return;
      }
      stopDictation();
      if (code === "not-allowed" || code === "service-not-allowed") {
        showToast("Permiso de micrófono denegado");
      } else {
        showToast("Error de dictado: " + code);
      }
    };
    rec.onend = function () {
      if (dictation !== rec && !rec.__rcStopping) return;
      if (said) {
        sendPaste((dictationSent ? " " : "") + said);
        dictationSent = true;
      }
      if (rec.__rcStopping) return;
      if (silent && !said) {
        stopDictation();
        showToast("Dictado detenido");
        return;
      }
      listenOnce();
    };
    dictation = rec;
    syncMicBtn();
    try {
      rec.start();
    } catch (_) {
      dictation = null;
      syncMicBtn();
      showToast("No pude iniciar el dictado");
    }
  }

  function startDictation() {
    if (!(window.SpeechRecognition || window.webkitSpeechRecognition)) {
      showToast("Este navegador no soporta dictado");
      return;
    }
    dictationSent = false;
    listenOnce();
    if (dictation) showToast("Escuchando…");
  }

  function toggleDictation() {
    if (dictation) stopDictation();
    else startDictation();
  }

  function openFilePicker() {
    filePicker().click();
  }

  function termSelection() {
    var own = readSelText();
    if (own) return own;
    var term = xterm();
    if (term && typeof term.getSelection === "function") {
      try {
        var fromTerm = term.getSelection();
        if (fromTerm && String(fromTerm).replace(/\s+/g, "")) {
          return String(fromTerm).replace(/[ \t]+$/gm, "");
        }
      } catch (_) {}
    }
    var sel = window.getSelection && window.getSelection();
    var text = sel && sel.toString ? sel.toString() : "";
    return text ? String(text).replace(/[ \t]+$/gm, "") : "";
  }

  function copySelection() {
    var text = termSelection();
    if (!text) {
      showToast("Nada seleccionado");
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () {
          showToast("Copiado");
          endSelect();
        },
        function () {
          showToast("No pude copiar");
        }
      );
      return;
    }
    showToast("No pude copiar");
  }

  function bootKeepSel() {
    var tries = 0;
    var restoring = false;
    function arm() {
      var term = xterm();
      if (!term || typeof term.onSelectionChange !== "function") {
        if (tries++ < 40) setTimeout(arm, 150);
        return;
      }
      term.onSelectionChange(function () {
        if (!selectMode || !lastSel || restoring) return;
        if (term.hasSelection && term.hasSelection()) return;
        restoring = true;
        try {
          restoreSel();
        } finally {
          restoring = false;
        }
      });
    }
    arm();
  }

  function bootImeLock() {
    document.addEventListener(
      "focusin",
      function (ev) {
        var t = ev.target;
        if (!t || !t.classList || !t.classList.contains("xterm-helper-textarea")) {
          return;
        }
        armTextarea(t);
      },
      true
    );
    var mo = new MutationObserver(function () {
      armTextarea(termTextarea());
      if (imeOpen) keepImeFocus();
    });
    mo.observe(document.documentElement, { childList: true, subtree: true });
    armTextarea(termTextarea());
  }

  function bootPaste() {
    document.addEventListener(
      "paste",
      function (ev) {
        if (ev.defaultPrevented) return;
        if (Date.now() < pasteGuard) {
          ev.preventDefault();
          return;
        }
        var t = ev.target;
        if (t && t.id === "rc-file-pick") return;
        if (applyClipboardData(ev.clipboardData)) {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
        }
      },
      true
    );
    document.addEventListener(
      "keydown",
      function (ev) {
        if (ev.defaultPrevented || ev.isComposing) return;
        var chord = ev.ctrlKey || ev.metaKey;
        if (!chord || !ev.shiftKey || ev.altKey) return;
        var key = ev.key;
        if (key === "V" || key === "v") {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
          pasteGuard = Date.now() + 800;
          pasteFromClipboard();
          return;
        }
        if (key === "C" || key === "c") {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
          copySelection();
        }
      },
      true
    );
  }

  function armPaneScroll(term) {
    if (!term || term.__rcScrollArmed || typeof term.onScroll !== "function") return;
    term.__rcScrollArmed = true;
    armScrollback(term);
    term.onScroll(function () {
      if (xterm() !== term) return;
      stickBottom = atBottom();
      if (imeGuarded()) return;
      if (imeOpen && !stickBottom) lockIme();
    });
  }

  function bootPinScroll() {
    var tries = 0;
    function arm() {
      var term = window.term;
      if (!term || typeof term.onScroll !== "function") {
        if (tries++ < 80) setTimeout(arm, 50);
        return;
      }
      armPaneScroll(term);
    }
    arm();
  }

  function sessionLabel(item, counts) {
    if (!item.path && item.command === "nueva") return "Nueva sesión";
    var path = String(item.path || "");
    var parts = path.split("/").filter(Boolean);
    var base = parts.length ? parts[parts.length - 1] : "~";
    var cmd = item.command || "shell";
    var label = base + " · " + cmd;
    if (counts[label] > 1) label += " · " + String(item.id || "").slice(-4);
    return label;
  }

  function sessionKey(item) {
    if (!item.path && item.command === "nueva") return "Nueva sesión";
    var path = String(item.path || "");
    var parts = path.split("/").filter(Boolean);
    var base = parts.length ? parts[parts.length - 1] : "~";
    return base + " · " + (item.command || "shell");
  }

  var TAB_CLOSE_SVG =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.6 4.6l6.8 6.8M11.4 4.6l-6.8 6.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

  function orderedSessions(list, current) {
    var items = [];
    var seen = {};
    (list || []).forEach(function (item) {
      if (!item || !item.id || seen[item.id]) return;
      seen[item.id] = true;
      items.push(item);
    });
    if (current && !seen[current]) {
      items.push({
        id: current,
        path: "",
        command: "nueva",
        activity: 0,
        created: Date.now() / 1000,
      });
    }
    items.sort(function (a, b) {
      var delta = (a.created || 0) - (b.created || 0);
      if (delta) return delta;
      return String(a.id).localeCompare(String(b.id));
    });
    return items;
  }

  function openSession(id) {
    if (!id || id === tabId()) return;
    if (typeof window.__rcOpenSession === "function") window.__rcOpenSession(id);
  }

  function closeSession(id, ev) {
    if (ev) {
      ev.preventDefault();
      ev.stopPropagation();
    }
    if (!id) return;
    var host = document.getElementById("rc-session-tabs");
    var ids = [];
    if (host) {
      host.querySelectorAll(".rc-tab").forEach(function (el) {
        if (el.dataset.id) ids.push(el.dataset.id);
      });
    }
    var index = ids.indexOf(id);
    var next = "";
    if (ids.length > 1 && index >= 0) next = ids[index + 1] || ids[index - 1] || "";
    var current = id === tabId();
    if (current) {
      if (next) openSession(next);
      else if (typeof window.__rcNewSession === "function") window.__rcNewSession();
    }
    disposePane(id);
    sessionCache = sessionCache.filter(function (item) {
      return item.id !== id;
    });
    paintSessions(sessionCache);
    fetch("/rc-session-close?tab=" + encodeURIComponent(id), {
      method: "POST",
      cache: "no-store",
      keepalive: true,
    }).catch(function () {});
  }

  function fillTab(el, item, counts, current) {
    var label = sessionLabel(item, counts);
    el.dataset.id = item.id;
    el.classList.toggle("is-active", item.id === current);
    el.setAttribute("aria-selected", item.id === current ? "true" : "false");
    el.title = item.path || label;
    var text = el.querySelector(".rc-tab-label");
    if (text && text.textContent !== label) text.textContent = label;
    var close = el.querySelector(".rc-tab-close");
    if (close) close.setAttribute("aria-label", "Cerrar " + label);
  }

  function makeTab() {
    var el = document.createElement("div");
    el.className = "rc-tab";
    el.setAttribute("role", "tab");
    el.tabIndex = 0;
    var label = document.createElement("span");
    label.className = "rc-tab-label";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "rc-tab-close";
    close.innerHTML = TAB_CLOSE_SVG;
    el.appendChild(label);
    el.appendChild(close);
    close.addEventListener("click", function (ev) {
      closeSession(el.dataset.id, ev);
    });
    el.addEventListener("click", function (ev) {
      if (ev.target && ev.target.closest && ev.target.closest(".rc-tab-close")) return;
      openSession(el.dataset.id);
    });
    el.addEventListener("auxclick", function (ev) {
      if (ev.button !== 1) return;
      closeSession(el.dataset.id, ev);
    });
    el.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      ev.preventDefault();
      openSession(el.dataset.id);
    });
    return el;
  }

  var sessionCache = [];

  function paintSessions(list) {
    if (Array.isArray(list)) sessionCache = list;
    var host = document.getElementById("rc-session-tabs");
    if (!host) return;
    var current = tabId();
    var items = orderedSessions(list, current);
    var counts = {};
    items.forEach(function (item) {
      var key = sessionKey(item);
      counts[key] = (counts[key] || 0) + 1;
    });
    var sig = items
      .map(function (item) {
        return item.id;
      })
      .join("\n");
    if (host.dataset.sig !== sig) {
      host.dataset.sig = sig;
      host.innerHTML = "";
      items.forEach(function (item) {
        var el = makeTab();
        fillTab(el, item, counts, current);
        host.appendChild(el);
      });
    } else {
      var nodes = host.querySelectorAll(".rc-tab");
      items.forEach(function (item, index) {
        if (nodes[index]) fillTab(nodes[index], item, counts, current);
      });
    }
    if (host.dataset.active !== current) {
      host.dataset.active = current;
      var active = host.querySelector(".is-active");
      if (active && active.scrollIntoView) {
        try {
          active.scrollIntoView({ inline: "nearest", block: "nearest" });
        } catch (_) {}
      }
    }
  }

  function pullSessions() {
    fetch("/rc-sessions", { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (payload) {
        var list = payload && payload.sessions;
        if (!Array.isArray(list)) return;
        paintSessions(list);
      })
      .catch(function () {});
  }

  function bootSessions() {
    if (document.getElementById("rc-sessions")) return;
    var host = document.createElement("div");
    host.id = "rc-sessions";
    var tabs = document.createElement("div");
    tabs.id = "rc-session-tabs";
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Sesiones de este túnel");
    var fresh = document.createElement("button");
    fresh.id = "rc-session-new";
    fresh.type = "button";
    fresh.setAttribute("aria-label", "Nueva pestaña");
    fresh.innerHTML =
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    host.appendChild(tabs);
    host.appendChild(fresh);
    (document.body || document.documentElement).appendChild(host);
    fresh.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (typeof window.__rcNewSession === "function") window.__rcNewSession();
    });
    pullSessions();
    setInterval(function () {
      if (!document.hidden) pullSessions();
    }, 3000);
    window.addEventListener("rc-ws-open", pullSessions);
    window.addEventListener("rc-tab-changed", function () {
      paintSessions(sessionCache);
    });
  }

  window.__rcSyncTabs = function () {
    paintSessions(sessionCache);
  };

  function boot() {
    var device = detectDevice();
    document.documentElement.dataset.rcDevice = device;
    document.documentElement.classList.add("rc-" + device);
    bootSessions();
    watchNativeClear();
    bootPaste();
    bootPinScroll();
    window.addEventListener("resize", function () {
      var pane = panes[tabId()];
      if (pane && !pane.primary) fitPane(pane);
    });
    var claimTries = 0;
    (function claimSoon() {
      var pane = ensurePrimary();
      if (pane && pane.term && pane.term.__rcFitHook) return;
      if (claimTries++ > 100) return;
      setTimeout(claimSoon, 50);
    })();
    if (device === "pc") {
      document.documentElement.classList.remove("rc-touch");
      return;
    }
    document.documentElement.classList.add("rc-touch");
    lockIme();
    bootImeLock();
    bootKeepSel();
    mountBar();
    bootInterceptors();
    bootTypeToTty();
    bootTouchScroll();
  }

  if (window.__rcRedirecting) return;
  wrapWebSocket();
  bootPrimaryRetry();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
