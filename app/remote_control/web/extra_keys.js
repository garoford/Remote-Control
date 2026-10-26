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

  var FOLDER_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M4 4h5.6l2 2H20a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm0 4v10h16V8H4Z"/>' +
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

  // A tab opened by another profile keeps its owner's size (tab_session.sh).
  // Instead of tmux's dots or a cut-off bottom, this page shows the owner's
  // columns and rows scaled to fit: the font follows, the grid stays.
  var BASE_FONT = 15;
  var guestSize = {};

  function fitGuest(term, host, size) {
    var rect = host && host.getBoundingClientRect();
    var cell = cellOf(term);
    if (!rect || rect.width < 20 || rect.height < 20 || !cell) return;
    var font = term.options.fontSize || BASE_FONT;
    var want = Math.min(rect.width / ((size.cols * cell.width) / font), rect.height / ((size.rows * cell.height) / font));
    var next = Math.max(6, Math.min(BASE_FONT, Math.floor(want * 2) / 2));
    for (var tries = 0; tries < 6; tries++) {
      if (term.options.fontSize !== next) term.options.fontSize = next;
      cell = cellOf(term);
      if (!cell || next <= 6 || (size.cols * cell.width <= rect.width && size.rows * cell.height <= rect.height)) break;
      next -= 0.5;
    }
    term.__rcGuest = true;
    if (term.cols !== size.cols || term.rows !== size.rows) term.resize(size.cols, size.rows);
  }

  // Back to owning the size: normal font, the grid follows the screen again.
  function unguest(term) {
    if (!term.__rcGuest) return;
    term.__rcGuest = false;
    term.options.fontSize = BASE_FONT;
  }

  // Whose herdr tab this is: the profile owning most of the panes on screen
  // (ties: the top-left pane). Every device sees the same panes, so they all
  // agree; the others size their view to that profile's (history.herdr_size).
  // Kept a few seconds while the marks reload after a resize.
  var herdrAuth = { id: null, at: 0 };

  function herdrAuthority() {
    var count = {};
    var first = null;
    var firstAt = Infinity;
    paneMarks.marks.forEach(function (mark) {
      var box = paneMarks.boxes[mark.box];
      if (!box || typeof mark.owner !== "string") return;
      count[mark.owner] = (count[mark.owner] || 0) + 1;
      if (box.y * 10000 + box.x < firstAt) {
        firstAt = box.y * 10000 + box.x;
        first = mark.owner;
      }
    });
    var best = null;
    var bestN = 0;
    Object.keys(count).forEach(function (owner) {
      if (count[owner] > bestN || (count[owner] === bestN && owner === first)) {
        best = owner;
        bestN = count[owner];
      }
    });
    if (best !== null) herdrAuth = { id: best, at: Date.now() };
    else if (Date.now() - herdrAuth.at > 6000) herdrAuth = { id: null, at: 0 };
    return herdrAuth.id;
  }

  function pollGuestSize() {
    var id = tabId();
    if (!id || document.hidden || window.__rcEmpty) return;
    var extra = "";
    if (onHerdrTab()) {
      var auth = herdrAuthority();
      extra = "&dev=" + encodeURIComponent(deviceId()) + "&auth=" + (auth === null ? "" : encodeURIComponent("p" + auth));
    }
    fetch("/rc-tab-size?tab=" + encodeURIComponent(id) + "&prof=" + encodeURIComponent(profileArg()) + extra, { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (info) {
        var pane = panes[id] || (id === bootTabId ? ensurePrimary() : null);
        if (!info || !pane || !pane.term) return;
        var term = pane.term;
        if (info.guest && info.cols > 1 && info.rows > 1) {
          guestSize[id] = { cols: info.cols, rows: info.rows };
          if (term.__rcGuest && term.cols === info.cols && term.rows === info.rows) return;
        } else {
          delete guestSize[id];
          if (!term.__rcGuest) return;
        }
        fitPane(pane);
      })
      .catch(function () {});
  }

  function bootGuestSize() {
    setInterval(pollGuestSize, 2000);
    window.addEventListener("rc-tab-changed", pollGuestSize);
    window.addEventListener("rc-ws-open", pollGuestSize);
    pollGuestSize();
  }

  // ttyd's FitAddon: window resizes and font changes call it directly.
  function fitAddonOf(term) {
    try {
      var list = term._addonManager._addons;
      for (var i = 0; i < list.length; i++) {
        var addon = list[i] && list[i].instance;
        if (addon && typeof addon.fit === "function" && typeof addon.proposeDimensions === "function") return addon;
      }
    } catch (_) {}
    return null;
  }

  function hookPrimaryFit(term) {
    if (!term || term.__rcFitHook || typeof term.fit !== "function") return;
    function guarded(native) {
      return function () {
        var host = primaryPane && primaryPane.host;
        if (host && host.hidden) return;
        var size = guestSize[bootTabId];
        if (size) return fitGuest(term, host || document.getElementById("terminal-container"), size);
        unguest(term);
        return native();
      };
    }
    term.fit = guarded(term.fit.bind(term));
    // Without this a window resize puts back the screen's own size and tmux
    // pans the owner's wider window (left columns cut off) until the next poll.
    var addon = fitAddonOf(term);
    if (addon) addon.fit = guarded(addon.fit.bind(addon));
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
    lockTheme(term);
    hookOsc52(term);
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
    opts.theme = termPalette();
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

  // The browser tab title is the active tab's name. ttyd and the terminal
  // (OSC title) keep trying to set it; those writes are dropped.
  var nativeTitle = Object.getOwnPropertyDescriptor(Document.prototype, "title");
  if (nativeTitle && nativeTitle.set) {
    try {
      Object.defineProperty(document, "title", {
        configurable: true,
        get: function () {
          return nativeTitle.get.call(document);
        },
        set: function () {},
      });
    } catch (_) {}
  }

  function setPageTitle(text) {
    if (!nativeTitle || !nativeTitle.set || !text) return;
    if (nativeTitle.get.call(document) !== text) nativeTitle.set.call(document, text);
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
    if (cmd === 49) return; // window title: the browser tab shows our tab name

    if (cmd !== 50 || !pane.term || !pane.term.options) return;
    try {
      var prefs = JSON.parse(new TextDecoder().decode(bytes.subarray(1)));
      if (!prefs || typeof prefs !== "object") return;
      if (prefs.fontSize) pane.term.options.fontSize = prefs.fontSize;
      if (prefs.fontFamily) pane.term.options.fontFamily = prefs.fontFamily;
      if (prefs.theme) pane.term.options.theme = termPalette();
      pane.term.options.scrollback = 20000;
      if (guestSize[pane.id]) fitPane(pane);
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
    if (!pane.primary && guestSize[pane.id]) {
      fitGuest(pane.term, pane.host, guestSize[pane.id]);
      return;
    }
    if (!pane.primary) unguest(pane.term);
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
    Object.keys(panes).forEach(function (key) {
      if (panes[key] && panes[key] !== pane && panes[key].term) dropGpu(panes[key].term);
    });
    syncRenderer(pane.term);
    if (pane.ws && pane.ws.readyState !== NativeWS.CLOSED) window.__rcTermSocket = pane.ws;
    resetMirror();
    tmuxScroll.offset = 0;
    tmuxScroll.pending = 0;
    armPaneScroll(pane.term);
    fitPane(pane);
    claimSizeSoon();
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
      if (typeof pullSessions === "function") pullSessions();
      setTimeout(function () {
        if (!pane.intentional && panes[pane.id] === pane && !closedTabs[pane.id]) connectPane(pane);
      }, 200);
    });
  }

  // ttyd loads WebGL only for its own terminal; ours would fall back to the DOM
  // renderer, which draws block characters (QR codes, ASCII art) from the font
  // and leaves thin seams between cells. Reuse ttyd's addon class instead.
  var GpuClass = null;

  function gpuOf(term) {
    try {
      var addons = (term && term._addonManager && term._addonManager._addons) || [];
      for (var i = 0; i < addons.length; i++) {
        var inst = addons[i] && addons[i].instance;
        if (inst && typeof inst.clearTextureAtlas === "function" && !inst.__rcDisposed) return inst;
      }
    } catch (_) {}
    return null;
  }

  function gpuAddonClass() {
    if (!GpuClass) {
      var inst = gpuOf(window.term);
      if (inst) GpuClass = inst.constructor;
    }
    return GpuClass;
  }

  function loadGpu(term) {
    var Gpu = gpuAddonClass();
    if (!Gpu || gpuOf(term)) return;
    try {
      var addon = new Gpu();
      term.loadAddon(addon);
      if (typeof addon.onContextLoss === "function") {
        addon.onContextLoss(function () {
          dropGpu(term);
        });
      }
    } catch (_) {}
  }

  function dropGpu(term) {
    var inst = gpuOf(term);
    if (!inst) return;
    if (!GpuClass) GpuClass = inst.constructor;
    try {
      inst.__rcDisposed = true;
      inst.dispose();
    } catch (_) {}
  }

  // The terminal on screen draws with WebGL (solid blocks, speed). Hidden ones
  // give their context back: Chrome keeps about 16 per process and silently
  // drops the oldest, and a terminal whose context was dropped stays blank
  // (just the cursor) though its text is all there. With two browser tabs and
  // a pane per visited tab, that limit came quickly.
  function syncRenderer(term) {
    if (!term) return;
    if (term === window.term) loadGpu(term);
    else dropGpu(term);
  }

  function gpuLost(term) {
    try {
      var gl = gpuOf(term)._renderer._gl;
      return !!gl && gl.isContextLost();
    } catch (_) {
      return false;
    }
  }

  function repaint(term) {
    try {
      term.refresh(0, Math.max(0, (term.rows || 1) - 1));
    } catch (_) {}
  }

  // A context lost anyway: draw without WebGL now, try WebGL again shortly.
  // Coming back to the browser tab repaints too.
  function bootGpuWatch() {
    setInterval(function () {
      var term = xterm();
      if (document.hidden || !term || !gpuLost(term)) return;
      dropGpu(term);
      repaint(term);
      setTimeout(function () {
        if (term !== window.term) return;
        loadGpu(term);
        repaint(term);
      }, 1500);
    }, 3000);
    document.addEventListener("visibilitychange", function () {
      var term = xterm();
      if (document.hidden || !term) return;
      syncRenderer(term);
      repaint(term);
    });
  }

  // ttyd switches its own terminal to Unicode 11 widths. A pane left on the
  // default (Unicode 6) counts emoji like 🔒 or ⚠ as one column while the app
  // printing them counts two, so the rest of that line slides over (TUI borders,
  // QR codes, prompts). Give every pane the primary's width tables.
  function matchUnicode(term) {
    try {
      var src = window.term && window.term._core && window.term._core.unicodeService;
      var dst = term._core && term._core.unicodeService;
      var version = window.term.unicode.activeVersion;
      if (!src || !dst || !version || term.unicode.activeVersion === version) return;
      var provider = src._providers && src._providers[version];
      if (provider && !(dst._providers && dst._providers[version])) dst.register(provider);
      term.unicode.activeVersion = version;
    } catch (_) {}
  }

  // On phones the 1.2 MB Nerd Font arrives after the terminals are built, so
  // they start on the system mono, which has none of the CLI icons (they show
  // as empty boxes). cache.js swaps the font on ttyd's terminal only; this does
  // it for every pane, and redraws the glyph atlas the GPU renderer kept.
  var NERD_STACK = "'FiraCode Nerd Font Mono', ui-monospace, 'Cascadia Mono', 'SF Mono', Menlo, Consolas, monospace";

  function nerdFontReady() {
    try {
      return !!(document.fonts && document.fonts.check && document.fonts.check("16px 'FiraCode Nerd Font Mono'", "\uE0B0"));
    } catch (_) {
      return false;
    }
  }

  function useNerdFont(term) {
    if (!term || !term.options || !nerdFontReady()) return;
    try {
      if (term.options.fontFamily !== NERD_STACK) term.options.fontFamily = NERD_STACK;
      if (typeof term.clearTextureAtlas === "function") term.clearTextureAtlas();
      term.refresh(0, Math.max(0, (term.rows || 1) - 1));
    } catch (_) {}
  }

  function useNerdFontEverywhere() {
    Object.keys(panes).forEach(function (key) {
      var pane = panes[key];
      if (pane && pane.term && pane.term !== window.term) useNerdFont(pane.term);
    });
  }

  if (document.fonts) {
    try {
      document.fonts.addEventListener("loadingdone", useNerdFontEverywhere);
    } catch (_) {}
    try {
      document.fonts.load("16px 'FiraCode Nerd Font Mono'", "\uE0B0").then(useNerdFontEverywhere, function () {});
    } catch (_) {}
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
      matchUnicode(term);
      lockTheme(term);
      syncRenderer(term);
      useNerdFont(term);
      host.__rcTerm = term;
      hookOsc52(term);
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
    // The empty screen has no ttyd terminal behind it: load the tab instead.
    if (window.__rcEmpty) return false;
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

  // One id per browser. tab_session.sh gets it as the second ?arg= and gives
  // each device its own herdr client when the tab's pane runs herdr.
  function deviceId() {
    var id = "";
    try {
      id = localStorage.getItem("rc-device-id") || "";
    } catch (_) {}
    if (!/^d[a-z0-9]{8,32}$/.test(id)) {
      var bytes = new Uint8Array(8);
      if (window.crypto && crypto.getRandomValues) {
        crypto.getRandomValues(bytes);
      } else {
        for (var i = 0; i < bytes.length; i++) bytes[i] = (Math.random() * 256) | 0;
      }
      id = "d";
      for (var j = 0; j < bytes.length; j++) id += ("0" + bytes[j].toString(16)).slice(-2);
      try {
        localStorage.setItem("rc-device-id", id);
      } catch (_) {}
    }
    return id;
  }

  // Third ?arg=: this browser's profile ("p" + id, "p" is the primary). The
  // tab's size follows the profile that opened it (tab_session.sh). Read raw:
  // the socket opens before the profile list arrives.
  function profileArg() {
    var id = null;
    try {
      id = localStorage.getItem("rc-profile");
    } catch (_) {}
    if (id === null && wsState && wsState.profilePick) id = wsState.profilePick[foldDevice()];
    id = id || "";
    return /^([a-z0-9][a-z0-9_-]{0,31})?$/.test(id) ? "p" + id : "p";
  }

  function withDevice(url) {
    var text = String(url);
    var dev = deviceId();
    if (text.indexOf("arg=") === -1 || text.indexOf("arg=" + dev) !== -1) return text;
    return text + (text.indexOf("?") === -1 ? "?" : "&") + "arg=" + dev + "&arg=" + profileArg();
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
      if (tty) url = withDevice(url);
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
    // The read-only/blur dance only keeps the phone keyboard down; on a PC it
    // just took the focus away after every paste or upload.
    if (imeOpen || !document.documentElement.classList.contains("rc-touch")) {
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
      showToast("Seleccionando");
      kickHandles();
      return;
    }
    lastSel = null;
    hideCopyChip();
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

  // --- links ------------------------------------------------------------------
  // Apps like herdr catch Ctrl+click and run xdg-open on the PC. The browser
  // keeps the click and opens the link on this device instead.

  var URL_RE = /(?:https?:\/\/|www\.)[^\s<>"'`]+/g;

  function trimUrl(url) {
    var pairs = { ")": "(", "]": "[", "}": "{" };
    for (;;) {
      var last = url.charAt(url.length - 1);
      if (/[.,;:!?'"]/.test(last)) {
        url = url.slice(0, -1);
        continue;
      }
      var open = pairs[last];
      if (open && url.split(open).length < url.split(last).length) {
        url = url.slice(0, -1);
        continue;
      }
      return url;
    }
  }

  function urlAt(clientX, clientY) {
    var term = xterm();
    var buf = term && term.buffer && term.buffer.active;
    var cell = cellAt(clientX, clientY);
    if (!buf || !cell || typeof buf.getLine !== "function") return "";
    var row = cell.row + bufferTop();
    var first = row;
    while (first > 0 && buf.getLine(first) && buf.getLine(first).isWrapped) first--;
    var text = "";
    var at = -1;
    for (var r = first; ; r++) {
      var line = buf.getLine(r);
      if (!line || (r > first && !line.isWrapped)) break;
      if (r === row) at = text.length + cell.col;
      text += line.translateToString(false);
    }
    if (at < 0) return "";
    URL_RE.lastIndex = 0;
    var m;
    while ((m = URL_RE.exec(text))) {
      var url = trimUrl(m[0]);
      if (at >= m.index && at < m.index + url.length) {
        return /^www\./.test(url) ? "https://" + url : url;
      }
    }
    return "";
  }

  function openOnClient(url) {
    try {
      window.open(url, "_blank", "noopener");
    } catch (_) {}
    showToast("Abriendo enlace");
  }

  function bootLinks() {
    var swallow = false;
    document.addEventListener(
      "mousedown",
      function (ev) {
        if (ev.button !== 0 || !(ev.ctrlKey || ev.metaKey)) return;
        var t = ev.target;
        if (!t || !t.closest || !t.closest(".xterm")) return;
        var url = urlAt(ev.clientX, ev.clientY);
        if (!url) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        swallow = true;
        openOnClient(url);
      },
      true
    );
    // No browser menu on right click (it offered Paste); the press still
    // reaches apps that track the mouse, like herdr's pane menu.
    document.addEventListener(
      "contextmenu",
      function (ev) {
        var t = ev.target;
        if (t && /^(INPUT|TEXTAREA)$/.test(t.tagName) && !(t.closest && t.closest(".xterm"))) return;
        ev.preventDefault();
      },
      true
    );
    ["mouseup", "click"].forEach(function (type) {
      document.addEventListener(
        type,
        function (ev) {
          if (!swallow) return;
          ev.preventDefault();
          ev.stopImmediatePropagation();
          if (type === "click") swallow = false;
        },
        true
      );
    });
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
    if (el.closest && el.closest("#rc-sessions, #rc-file-pick, #rc-ws-drawer, #rc-auto-modal, #rc-files")) return false;
    return true;
  }

  function focusTerm() {
    if (!document.documentElement.classList.contains("rc-touch")) {
      // PC: no on-screen keyboard to manage, the terminal just takes focus
      // (unless the user moved on to a field, like renaming a workspace).
      var ae = document.activeElement;
      if (ae && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName) && !(ae.closest && ae.closest(".xterm"))) return;
      var pc = xterm();
      if (pc) {
        try {
          pc.focus();
        } catch (_) {}
      }
      return;
    }
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

  // Apps that redraw the whole screen (Cursor Agent, Ink UIs) never scroll
  // lines into xterm, but tmux keeps them (scroll-on-clear). When tmux holds
  // more history than xterm, a swipe drives tmux copy-mode instead.
  var tmuxScroll = { offset: 0, busy: false, pending: 0 };

  function tmuxHasMoreHistory() {
    var term = xterm();
    var buf = term && term.buffer && term.buffer.active;
    var tmuxLines = histSeen[tabId()];
    if (!buf || typeof tmuxLines !== "number") return false;
    return tmuxLines > buf.baseY + 3;
  }

  function pumpTmuxScroll() {
    if (tmuxScroll.busy || !tmuxScroll.pending) return;
    var tab = tabId();
    if (!tab) {
      tmuxScroll.pending = 0;
      return;
    }
    var n = Math.max(-80, Math.min(80, tmuxScroll.pending));
    tmuxScroll.pending -= n;
    tmuxScroll.busy = true;
    fetch("/rc-scroll?tab=" + encodeURIComponent(tab) + "&lines=" + n, {
      method: "POST",
      cache: "no-store",
    })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (state) {
        if (state && state.ok !== false) {
          tmuxScroll.offset = state.in_mode ? state.position || 0 : 0;
        }
      })
      .catch(function () {})
      .then(function () {
        tmuxScroll.busy = false;
        pumpTmuxScroll();
      });
  }

  function scrollTmux(lines) {
    if (!lines) return;
    tmuxScroll.pending += lines;
    pumpTmuxScroll();
  }

  function cancelCopyMode() {
    tmuxScroll.offset = 0;
    tmuxScroll.pending = 0;
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
    pingProfile(/[\r\n]/.test(text));
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

  // A paste (above all an image upload) can outlast holdImeForPaste; give
  // the focus, and the phone keyboard if it was up, back when it ends.
  function restoreFocusAfterPaste(keyboardWasUp) {
    if (keyboardWasUp && !imeOpen) {
      unlockIme();
      return;
    }
    holdImeForPaste();
    focusTerm();
    if (imeOpen) keepImeFocus();
  }

  // Glass pill under the tab strip: icon by tone, slides in, a thin bar runs
  // down while it stays, fades out. Same look as the rest of the UI.
  var TOAST_MS = 2600;
  var TOAST_ICONS = {
    ok: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.6 8.4l2.9 2.9 5.9-6.4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    warn: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 4.6v4.2M8 11.4v.1" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
    info: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 7.4v4M8 4.6v.1" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
    live: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5.6" y="2" width="4.8" height="8" rx="2.4" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.6 7.6a4.4 4.4 0 0 0 8.8 0M8 12v2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  };

  function toastTone(text) {
    var t = String(text || "").toLowerCase();
    if (/^no |no pude|error|falló|fallo|sin conexi|demasiad|pasa de|no es una/.test(t)) return "warn";
    if (/copiad|guardad|listo|lista|cread|subid|cerrad|generad|movid|pegad|enviad|^perfil|actualizad/.test(t)) return "ok";
    return "info";
  }

  // live: dictation. One steady mic pill that stays while you speak and only
  // its text changes; no tone guessing, no countdown, no re-entrance.
  function showToast(text, live) {
    var el = document.getElementById("rc-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "rc-toast";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      el.innerHTML = '<span class="rc-toast-icon"></span><span class="rc-toast-text"></span><span class="rc-toast-bar"></span>';
      document.body.appendChild(el);
    }
    var tone = live ? "live" : toastTone(text);
    var shown = !el.hidden && el.classList.contains("is-on");
    var box = el.querySelector(".rc-toast-text");
    box.textContent = "";
    // ltr inside: the live pill is rtl only so the overflow is cut on the left
    var words = document.createElement("bdi");
    words.setAttribute("dir", "ltr");
    words.textContent = text;
    box.appendChild(words);
    el.style.setProperty("--rc-toast-ms", TOAST_MS + "ms");
    if (shown) {
      // Already on screen (dictation sends many updates): just swap the text
      // and tone and restart the countdown bar, no entrance again.
      if (!el.classList.contains("is-" + tone)) {
        el.classList.remove("is-ok", "is-warn", "is-info", "is-live");
        el.classList.add("is-" + tone);
        el.querySelector(".rc-toast-icon").innerHTML = TOAST_ICONS[tone];
      }
      var bar = el.querySelector(".rc-toast-bar");
      bar.style.animation = "none";
      void bar.offsetWidth;
      bar.style.animation = "";
    } else {
      el.className = "is-" + tone;
      el.querySelector(".rc-toast-icon").innerHTML = TOAST_ICONS[tone];
      el.hidden = false;
      void el.offsetWidth;
      el.classList.add("is-on");
    }
    clearTimeout(showToast._t);
    clearTimeout(showToast._h);
    if (live) return; // stays until hideToast()
    showToast._t = setTimeout(hideToast, TOAST_MS);
  }

  function hideToast() {
    var el = document.getElementById("rc-toast");
    clearTimeout(showToast._t);
    clearTimeout(showToast._h);
    if (!el || el.hidden) return;
    el.classList.remove("is-on");
    el.classList.add("is-out");
    showToast._h = setTimeout(function () {
      el.hidden = true;
    }, 260);
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

  function reserveFresh(ext) {
    function attempt(left) {
      var name = mintName(ext);
      return reserveName(name).catch(function (err) {
        if (left > 1) return attempt(left - 1);
        throw err;
      });
    }
    return attempt(3);
  }

  function uploadReserved(name, blob, type) {
    function attempt(left) {
      return putPasteFile(name, blob, type).catch(function (err) {
        if (left > 1) return attempt(left - 1);
        throw err;
      });
    }
    return attempt(3);
  }

  // Reserve the name and paste its path right away; compression and upload
  // finish behind it. Enter is not held back, so a very fast Enter can reach
  // the file before its bytes do.
  function ingestBlob(blob, asImage) {
    if (!blob || pasteBusy) return Promise.resolve(false);
    pasteBusy = true;
    pasteGuard = Date.now() + 800;
    var keyboardWasUp = imeOpen || pickerKeyboardUp;
    pickerKeyboardUp = false;
    holdImeForPaste();
    var hold = setInterval(holdImeForPaste, 500);
    var ext = extFromName(blob.name) || extFromType(blob.type) || "bin";
    if (ext === "bin") ext = extFromType(blob.type) || "bin";
    var squeeze = asImage && CANVAS_MIME[ext];
    function prepare() {
      var work = squeeze ? compressImage(blob, ext) : Promise.resolve(blob);
      return work.then(function (out) {
        return { blob: out, type: out.type || blob.type || "application/octet-stream" };
      });
    }
    return reserveFresh(ext)
      .then(
        function (info) {
          sendPaste(shellQuote(info.path));
          showToast("Subiendo…");
          restoreFocusAfterPaste(keyboardWasUp);
          return prepare().then(function (file) {
            return uploadReserved(info.name || info.path.split("/").pop(), file.blob, file.type);
          });
        },
        function () {
          // No reservation: fall back to writing first, then pasting the path.
          showToast("Guardando…");
          return prepare()
            .then(function (file) {
              return saveBlob(ext, file.blob, file.type);
            })
            .then(function (info) {
              sendPaste(shellQuote(info.path));
              return info;
            });
        }
      )
      .then(function (info) {
        showToast(info.name);
        return true;
      })
      .catch(function () {
        showToast("No pude subir el archivo");
        return false;
      })
      .then(function (ok) {
        pasteBusy = false;
        clearInterval(hold);
        restoreFocusAfterPaste(keyboardWasUp);
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
    var viaTmux = false;
    var edgeTimer = 0;
    var edgeDir = 0;
    var startedSelect = false;
    var dragging = false;
    var handleRaf = 0;
    var HANDLE_LIFT = 24;
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

    // Phones have no right button: a two-finger tap sends one to apps that
    // track the mouse (herdr's pane menu and the like).
    function sendRightClick(x, y) {
      var cell = cellAt(x, y) || { col: 0, row: 0 };
      var at = ";" + (cell.col + 1) + ";" + (cell.row + 1);
      var esc = String.fromCharCode(27);
      sendInput(esc + "[<2" + at + "M" + esc + "[<2" + at + "m");
    }

    var twoTap = null;

    function onTwoStart(ev) {
      if (!ev.touches || fromKeys(ev)) return;
      if (ev.touches.length === 2) {
        clearHold();
        var a = ev.touches[0];
        var b = ev.touches[1];
        twoTap = {
          at: Date.now(),
          pts: [
            { id: a.identifier, x: a.clientX, y: a.clientY },
            { id: b.identifier, x: b.clientX, y: b.clientY },
          ],
          x: (a.clientX + b.clientX) / 2,
          y: (a.clientY + b.clientY) / 2,
        };
      } else if (ev.touches.length > 2) {
        twoTap = null;
      }
    }

    function onTwoMove(ev) {
      if (!twoTap || !ev.touches) return;
      for (var i = 0; i < ev.touches.length; i++) {
        var t = ev.touches[i];
        twoTap.pts.forEach(function (p) {
          if (p.id === t.identifier && (Math.abs(t.clientX - p.x) > 10 || Math.abs(t.clientY - p.y) > 10)) {
            twoTap = null;
          }
        });
        if (!twoTap) return;
      }
    }

    function onTwoEnd(ev) {
      if (!twoTap || (ev.touches && ev.touches.length)) return;
      var tap = twoTap;
      twoTap = null;
      if (Date.now() - tap.at > 350 || !appWantsWheel()) return;
      if (ev.cancelable) ev.preventDefault();
      sendRightClick(tap.x, tap.y);
    }

    function fromKeys(ev) {
      var t = ev.target;
      return !!(
        t &&
        t.closest &&
        t.closest("#rc-extra-keys, #rc-file-pick")
      );
    }

    // Dragging a selection into the top or bottom edge scrolls xterm so the
    // selection can grow past what fits on screen.
    function edgeSpeed(y) {
      var term = xterm();
      var el = term && term.element && term.element.querySelector(".xterm-screen");
      if (!el || !el.getBoundingClientRect || !term.rows) return 0;
      var rect = el.getBoundingClientRect();
      var cell = rect.height / term.rows;
      if (y < rect.top) return -3;
      if (y < rect.top + cell * 1.5) return -1;
      if (y > rect.bottom) return 3;
      if (y > rect.bottom - cell * 1.5) return 1;
      return 0;
    }

    function stopEdgeScroll() {
      if (edgeTimer) clearInterval(edgeTimer);
      edgeTimer = 0;
      edgeDir = 0;
    }

    function edgeTick() {
      var term = xterm();
      if (!term || !selFrom || typeof term.scrollLines !== "function") {
        stopEdgeScroll();
        return;
      }
      term.scrollLines(edgeDir);
      var to = toBufferCell(cellAt(lastX, lastY));
      if (to) applyCellSelect(selFrom, to);
    }

    function trackEdge(y) {
      var dir = edgeSpeed(y);
      if (dir === edgeDir) return;
      stopEdgeScroll();
      if (!dir) return;
      edgeDir = dir;
      edgeTimer = setInterval(edgeTick, 70);
    }

    function handleEl(which) {
      var id = "rc-sel-" + which;
      var el = document.getElementById(id);
      if (el) return el;
      el = document.createElement("div");
      el.id = id;
      el.className = "rc-sel-handle";
      el.hidden = true;
      el.addEventListener(
        "touchstart",
        function (ev) {
          if (!lastSel || !ev.touches || ev.touches.length !== 1) return;
          ev.preventDefault();
          ev.stopPropagation();
          hideCopyChip();
          var anchor = which === "a" ? lastSel.to : lastSel.from;
          selFrom = { col: anchor.col, row: anchor.row };
          dragging = true;
        },
        { passive: false }
      );
      el.addEventListener(
        "touchmove",
        function (ev) {
          if (!dragging || !ev.touches || !ev.touches.length) return;
          ev.preventDefault();
          ev.stopPropagation();
          var t = ev.touches[0];
          // The handle hangs under its line; aim at the text above the finger.
          lastX = t.clientX;
          lastY = t.clientY - HANDLE_LIFT;
          var to = toBufferCell(cellAt(lastX, lastY));
          if (selFrom && to) applyCellSelect(selFrom, to);
          trackEdge(lastY);
        },
        { passive: false }
      );
      function release(ev) {
        if (!dragging) return;
        if (ev) ev.stopPropagation();
        dragging = false;
        stopEdgeScroll();
        selFrom = null;
        showChipForSelection();
      }
      el.addEventListener("touchend", release);
      el.addEventListener("touchcancel", release);
      document.body.appendChild(el);
      return el;
    }

    function placeHandle(el, at) {
      if (!at) {
        el.hidden = true;
        return;
      }
      el.hidden = false;
      el.style.left = at.x + "px";
      el.style.top = at.bottom + "px";
    }

    function syncHandles() {
      handleRaf = 0;
      var a = handleEl("a");
      var b = handleEl("b");
      if (!selectMode || !lastSel) {
        a.hidden = true;
        b.hidden = true;
        return;
      }
      placeHandle(a, cellToClient(lastSel.from.col, lastSel.from.row, false));
      placeHandle(b, cellToClient(lastSel.to.col, lastSel.to.row, true));
      handleRaf = requestAnimationFrame(syncHandles);
    }

    kickHandles = function () {
      if (!handleRaf) handleRaf = requestAnimationFrame(syncHandles);
    };

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
      startedSelect = selectMode;
      hideCopyChip();
      wheel = appWantsWheel();
      viaTmux = !wheel && (tmuxScroll.offset > 0 || tmuxHasMoreHistory());
      if (viaTmux) pinBottom();
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
        if (selFrom) trackEdge(y);
        return;
      }
      if (wheel || viaTmux) ev.preventDefault();
      if (!scrolling && Math.abs(y - startY) < 8 && Math.abs(x - startX) < 8) return;
      clearHold();
      scrolling = true;
      writing = false;
      if (imeOpen && !imeGuarded()) lockIme();
      if (!wheel && !viaTmux) return;
      var step = cellHeight();
      var steps = Math.trunc((y - wheelY) / step);
      if (!steps) return;
      wheelY += steps * step;
      // Finger down means older lines: wheel up, or a negative copy-mode move.
      if (viaTmux) scrollTmux(-steps);
      else sendWheel(steps > 0, Math.min(Math.abs(steps), 20), x, y);
    }

    function onEnd() {
      clearHold();
      stopEdgeScroll();
      var tapped = Math.abs(lastX - startX) < 8 && Math.abs(lastY - startY) < 8;
      if (selecting && startedSelect && tapped) {
        // A plain tap while selecting leaves select mode.
        endSelect();
      } else if (selecting) {
        var end = toBufferCell(cellAt(lastX, lastY));
        if (selFrom && end) applyCellSelect(selFrom, end);
        requestAnimationFrame(restoreSel);
        setTimeout(restoreSel, 0);
        setTimeout(restoreSel, 50);
        setTimeout(showChipForSelection, 60);
        kickHandles();
      } else if (!scrolling) {
        var link = tapped && mods.ctrl ? urlAt(lastX, lastY) : "";
        if (link) {
          clearSticky();
          openOnClient(link);
        }
        lockIme();
      }
      active = false;
      scrolling = false;
      selecting = false;
      selFrom = null;
      wheel = false;
      viaTmux = false;
    }

    function bind(el) {
      if (!el || el.dataset.rcTouchScroll === "1") return;
      el.dataset.rcTouchScroll = "1";
      el.addEventListener("touchstart", onStart, { passive: false });
      el.addEventListener("touchmove", onMove, { passive: false });
      el.addEventListener("touchend", onEnd, { passive: true });
      el.addEventListener("touchcancel", onEnd, { passive: true });
      el.addEventListener("touchstart", onTwoStart, { passive: true });
      el.addEventListener("touchmove", onTwoMove, { passive: true });
      el.addEventListener("touchend", onTwoEnd, { passive: false });
      el.addEventListener("touchcancel", function () {
        twoTap = null;
      });
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
    // When the keyboard session ends (blur, app switch) the browser or Gboard
    // can empty the textarea. A stale mirror would then turn the next key
    // into "erase everything typed before", so each session starts clean.
    document.addEventListener(
      "focusout",
      function (ev) {
        if (isTermTextarea(ev.target)) resetMirror();
      },
      true
    );
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState !== "hidden") return;
      resetMirror();
      // The keyboard is gone; show it as closed so one tap brings it back.
      if (imeOpen) {
        imeGuard = 0;
        lockIme();
      }
    });
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
        restoreFocusAfterPaste(pickerKeyboardUp);
        pickerKeyboardUp = false;
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
    ["rc-ek-mic", "rc-pc-mic"].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.classList.toggle("is-on", !!dictation);
    });
    syncWakeLock();
  }

  // Dictation dies when the screen turns off, so keep it on while the mic
  // is live. The browser drops the lock when the page is hidden; take it
  // again on return.
  var wakeLock = null;
  var wakeLockAsking = false;

  function syncWakeLock() {
    if (!navigator.wakeLock || typeof navigator.wakeLock.request !== "function") return;
    if (dictation) {
      if (wakeLock || wakeLockAsking || document.visibilityState !== "visible") return;
      wakeLockAsking = true;
      navigator.wakeLock.request("screen").then(
        function (lock) {
          wakeLockAsking = false;
          wakeLock = lock;
          lock.addEventListener("release", function () {
            if (wakeLock === lock) wakeLock = null;
          });
          if (!dictation) syncWakeLock();
        },
        function () {
          wakeLockAsking = false;
        }
      );
      return;
    }
    if (wakeLock) {
      var held = wakeLock;
      wakeLock = null;
      try {
        held.release();
      } catch (_) {}
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") syncWakeLock();
  });

  // PC has no extra-keys bar. The mic and the attach button each float on
  // their own and can be dragged anywhere. The spot is kept as fractions of
  // the free space, on the server (the tunnel URL changes) and cached locally.
  var MIC_POS_KEY = "rc-pc-mic-pos";
  var FILE_POS_KEY = "rc-pc-file-pos";
  var MIC_MIN = 28;
  var MIC_MAX = 160;

  function readFloatPos(key) {
    try {
      var saved = JSON.parse(localStorage.getItem(key) || "null");
      if (saved && saved.x >= 0 && saved.x <= 1 && saved.y >= 0 && saved.y <= 1) return saved;
    } catch (_) {}
    return null;
  }

  function floatSizeOk(size) {
    return typeof size === "number" && size >= MIC_MIN && size <= MIC_MAX;
  }

  function sizeFloat(btn, size) {
    btn.style.width = size + "px";
    btn.style.height = size + "px";
    btn.style.borderRadius = Math.round(size * 0.27) + "px";
  }

  function makeFloat(opts) {
    var pos = readFloatPos(opts.key);

    function place() {
      var btn = document.getElementById(opts.id);
      if (!btn || !pos) return;
      if (floatSizeOk(pos.size)) sizeFloat(btn, pos.size);
      var w = btn.offsetWidth || 44;
      var h = btn.offsetHeight || 44;
      var freeX = Math.max(0, window.innerWidth - w);
      var freeY = Math.max(0, window.innerHeight - h);
      btn.style.left = Math.round(pos.x * freeX) + "px";
      btn.style.top = Math.round(pos.y * freeY) + "px";
      btn.style.right = "auto";
      btn.style.bottom = "auto";
    }

    function setPos(next, fromServer) {
      if (!next || !(next.x >= 0 && next.x <= 1 && next.y >= 0 && next.y <= 1)) return;
      var drag = document.getElementById(opts.id);
      if (fromServer && drag && (drag.classList.contains("is-dragging") || drag.classList.contains("is-resizing"))) return;
      pos = { x: next.x, y: next.y };
      if (floatSizeOk(next.size)) pos.size = next.size;
      try {
        localStorage.setItem(opts.key, JSON.stringify(pos));
      } catch (_) {}
      place();
      if (!fromServer) wsPost({ op: opts.op, pos: pos });
    }

    function commit(btn) {
      var rect = btn.getBoundingClientRect();
      var freeX = Math.max(1, window.innerWidth - rect.width);
      var freeY = Math.max(1, window.innerHeight - rect.height);
      setPos({
        x: Math.round(Math.max(0, Math.min(1, rect.left / freeX)) * 10000) / 10000,
        y: Math.round(Math.max(0, Math.min(1, rect.top / freeY)) * 10000) / 10000,
        size: Math.round(rect.width),
      });
    }

    function resizeTo(btn, left, top, size) {
      size = Math.round(Math.max(MIC_MIN, Math.min(MIC_MAX, size)));
      size = Math.min(size, window.innerWidth - left, window.innerHeight - top);
      size = Math.max(MIC_MIN, size);
      sizeFloat(btn, size);
      btn.style.left = Math.max(0, Math.min(window.innerWidth - size, left)) + "px";
      btn.style.top = Math.max(0, Math.min(window.innerHeight - size, top)) + "px";
      btn.style.right = "auto";
      btn.style.bottom = "auto";
    }

    function bootResize(btn, grip) {
      var start = null;
      grip.addEventListener("pointerdown", function (ev) {
        if (ev.button !== 0) return;
        ev.preventDefault();
        ev.stopPropagation();
        var rect = btn.getBoundingClientRect();
        start = { x: ev.clientX, y: ev.clientY, left: rect.left, top: rect.top, size: rect.width };
        btn.classList.add("is-resizing");
        try {
          grip.setPointerCapture(ev.pointerId);
        } catch (_) {}
      });
      grip.addEventListener("pointermove", function (ev) {
        if (!start) return;
        ev.stopPropagation();
        var grow = Math.max(ev.clientX - start.x, ev.clientY - start.y);
        resizeTo(btn, start.left, start.top, start.size + grow);
      });
      function finish(ev) {
        if (!start) return;
        ev.stopPropagation();
        start = null;
        btn.classList.remove("is-resizing");
        commit(btn);
        focusTerm();
      }
      grip.addEventListener("pointerup", finish);
      grip.addEventListener("pointercancel", finish);
      var wheelSave = 0;
      btn.addEventListener(
        "wheel",
        function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          var rect = btn.getBoundingClientRect();
          var size = rect.width + (ev.deltaY < 0 ? 4 : -4);
          size = Math.max(MIC_MIN, Math.min(MIC_MAX, size));
          var d = (size - rect.width) / 2;
          resizeTo(btn, rect.left - d, rect.top - d, size);
          clearTimeout(wheelSave);
          wheelSave = setTimeout(function () {
            commit(btn);
          }, 400);
        },
        { passive: false }
      );
    }

    function bootDrag(btn) {
      var start = null;
      var moved = false;
      btn.addEventListener("pointerdown", function (ev) {
        if (ev.button !== 0) return;
        if (ev.target && ev.target.closest && ev.target.closest(".rc-mic-grip")) return;
        ev.preventDefault();
        var rect = btn.getBoundingClientRect();
        start = { x: ev.clientX, y: ev.clientY, left: rect.left, top: rect.top };
        moved = false;
        try {
          btn.setPointerCapture(ev.pointerId);
        } catch (_) {}
      });
      btn.addEventListener("pointermove", function (ev) {
        if (!start) return;
        var dx = ev.clientX - start.x;
        var dy = ev.clientY - start.y;
        if (!moved && Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
        moved = true;
        btn.classList.add("is-dragging");
        var w = btn.offsetWidth;
        var h = btn.offsetHeight;
        var left = Math.max(0, Math.min(window.innerWidth - w, start.left + dx));
        var top = Math.max(0, Math.min(window.innerHeight - h, start.top + dy));
        btn.style.left = left + "px";
        btn.style.top = top + "px";
        btn.style.right = "auto";
        btn.style.bottom = "auto";
      });
      function finish(ev) {
        if (!start) return;
        start = null;
        btn.classList.remove("is-dragging");
        if (!moved) {
          if (ev.type === "pointerup") opts.onTap();
          return;
        }
        commit(btn);
        focusTerm();
      }
      btn.addEventListener("pointerup", finish);
      btn.addEventListener("pointercancel", finish);
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      });
      window.addEventListener("resize", place);
    }

    function mount() {
      if (document.getElementById(opts.id)) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.id = opts.id;
      btn.setAttribute("aria-label", opts.aria);
      btn.title = opts.title;
      btn.innerHTML = opts.icon;
      var grip = document.createElement("span");
      grip.className = "rc-mic-grip";
      grip.setAttribute("aria-hidden", "true");
      btn.appendChild(grip);
      bootDrag(btn);
      bootResize(btn, grip);
      (document.body || document.documentElement).appendChild(btn);
      place();
      if (opts.afterMount) opts.afterMount();
    }

    return { setPos: setPos, mount: mount };
  }

  var pcMic = makeFloat({
    id: "rc-pc-mic",
    key: MIC_POS_KEY,
    op: "micPos",
    aria: "Dictar",
    title: "Dictar (arrastra para moverlo; la esquina o la rueda cambian el tamaño)",
    icon: MIC_ICON,
    onTap: function () {
      toggleDictation();
      focusTerm();
    },
    afterMount: syncMicBtn,
  });

  var pcFile = makeFloat({
    id: "rc-pc-file",
    key: FILE_POS_KEY,
    op: "filePos",
    aria: "Adjuntar archivo",
    title: "Adjuntar archivo (arrastra para moverlo; la esquina o la rueda cambian el tamaño)",
    icon: PASTE_ICON,
    onTap: function () {
      openFilePicker();
    },
  });

  var FILES_POS_KEY = "rc-files-pos";
  var pcFiles = makeFloat({
    id: "rc-pc-files",
    key: FILES_POS_KEY,
    op: "filesPos",
    aria: "Archivos",
    title: "Archivos (arrastra para moverlo; la esquina o la rueda cambian el tamaño)",
    icon: FOLDER_ICON,
    onTap: function () {
      if (window.__rcFiles) window.__rcFiles.open();
    },
  });

  function setFilesPos(pos, fromServer) {
    pcFiles.setPos(pos, fromServer);
  }

  function setMicPos(pos, fromServer) {
    pcMic.setPos(pos, fromServer);
  }

  function setFilePos(pos, fromServer) {
    pcFile.setPos(pos, fromServer);
  }

  function mountPcMic() {
    pcMic.mount();
  }

  function mountPcFile() {
    pcFile.mount();
  }

  function stopDictation() {
    var rec = dictation;
    if (!rec) return;
    dictation = null;
    rec.__rcStopping = true;
    syncMicBtn();
    var el = document.getElementById("rc-toast");
    if (el && el.classList.contains("is-live")) hideToast();
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
      // The phrase can arrive in pieces (settled words + the word still being
      // guessed). Join them; a piece that already repeats what came before
      // (Android sends growing copies) replaces it instead of doubling it.
      var text = "";
      var final = false;
      for (var i = 0; i < ev.results.length; i++) {
        var res = ev.results[i];
        var piece = String(res[0] && res[0].transcript ? res[0].transcript : "").trim();
        if (res.isFinal) final = true;
        if (!piece) continue;
        var low = text.toLowerCase();
        var plow = piece.toLowerCase();
        if (!text || plow.indexOf(low) === 0) text = piece;
        else if (low.slice(-plow.length) !== plow) text += " " + piece;
      }
      if (final) said = text;
      showToast(text || "Escuchando…", true);
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
    if (dictation) showToast("Escuchando…", true);
  }

  function toggleDictation() {
    if (dictation) stopDictation();
    else startDictation();
  }

  var pickerKeyboardUp = false;

  function openFilePicker() {
    // The picker closes the phone keyboard; reopen it once the file is in.
    pickerKeyboardUp = imeOpen;
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

  var kickHandles = function () {};

  function screenBox() {
    var term = xterm();
    var el = term && term.element && term.element.querySelector(".xterm-screen");
    if (!el || !el.getBoundingClientRect || !term.cols || !term.rows) return null;
    var rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return null;
    return { rect: rect, cellW: rect.width / term.cols, cellH: rect.height / term.rows, rows: term.rows };
  }

  // Buffer cell -> viewport pixels; null when that row is scrolled away.
  function cellToClient(col, bufRow, after) {
    var box = screenBox();
    if (!box) return null;
    var vis = bufRow - bufferTop();
    if (vis < 0 || vis >= box.rows) return null;
    return {
      x: box.rect.left + (col + (after ? 1 : 0)) * box.cellW,
      top: box.rect.top + vis * box.cellH,
      bottom: box.rect.top + (vis + 1) * box.cellH,
    };
  }

  function copyChip() {
    var chip = document.getElementById("rc-copy-chip");
    if (chip) return chip;
    chip = document.createElement("button");
    chip.type = "button";
    chip.id = "rc-copy-chip";
    chip.textContent = "Copiar";
    chip.hidden = true;
    function swallow(ev) {
      ev.preventDefault();
      ev.stopPropagation();
    }
    chip.addEventListener("pointerdown", swallow);
    chip.addEventListener("mousedown", swallow);
    chip.addEventListener("touchstart", swallow, { passive: false });
    chip.addEventListener("touchend", function (ev) {
      swallow(ev);
      copyFromChip();
    });
    chip.addEventListener("click", function (ev) {
      swallow(ev);
      copyFromChip();
    });
    document.body.appendChild(chip);
    return chip;
  }

  function copyFromChip() {
    hideCopyChip();
    copySelection();
  }

  function hideCopyChip() {
    var chip = document.getElementById("rc-copy-chip");
    if (chip) chip.hidden = true;
  }

  function showCopyChip(x, y) {
    var chip = copyChip();
    chip.hidden = false;
    var w = chip.offsetWidth || 80;
    var h = chip.offsetHeight || 36;
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    var left = Math.max(8, Math.min(vw - w - 8, x - w / 2));
    var top = y - h - 12;
    if (top < 8) top = y + 16;
    top = Math.max(8, Math.min(vh - h - 8, top));
    chip.style.left = left + "px";
    chip.style.top = top + "px";
  }

  function showChipForSelection() {
    if (!lastSel) return;
    var start = cellToClient(lastSel.from.col, lastSel.from.row, false);
    var end = cellToClient(lastSel.to.col, lastSel.to.row, true);
    if (start) showCopyChip(end && end.top === start.top ? (start.x + end.x) / 2 : start.x, start.top);
    else if (end) showCopyChip(end.x, end.bottom + 40);
  }

  function bootCopyChip() {
    document.addEventListener(
      "pointerdown",
      function (ev) {
        var t = ev.target;
        if (t && t.closest && t.closest("#rc-copy-chip, .rc-sel-handle")) return;
        hideCopyChip();
      },
      true
    );
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") hideCopyChip();
    });
    document.addEventListener("mouseup", function (ev) {
      if (document.documentElement.classList.contains("rc-touch")) return;
      var t = ev.target;
      if (!t || !t.closest || !t.closest(".xterm")) return;
      var x = ev.clientX;
      var y = ev.clientY;
      setTimeout(function () {
        var term = xterm();
        if (!term || typeof term.hasSelection !== "function" || !term.hasSelection()) return;
        if (!String(term.getSelection() || "").trim()) return;
        showCopyChip(x, y);
      }, 0);
    });
  }

  // Apps inside the terminal (herdr, nvim, tmux copy) copy with OSC 52; tmux
  // forwards it here, so the text lands in this device's clipboard.
  var osc52Pending = "";

  function decodeOsc52(data) {
    var at = String(data || "").indexOf(";");
    if (at < 0) return null;
    var b64 = data.slice(at + 1);
    if (!b64 || b64 === "?") return null;
    try {
      var bin = atob(b64);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new TextDecoder().decode(bytes);
    } catch (_) {
      return null;
    }
  }

  function writeClipboard(text) {
    if (!navigator.clipboard || !navigator.clipboard.writeText) return Promise.reject(new Error("no clipboard"));
    return navigator.clipboard.writeText(text);
  }

  function flushOsc52() {
    if (!osc52Pending) return;
    var text = osc52Pending;
    writeClipboard(text).then(function () {
      if (osc52Pending === text) osc52Pending = "";
      showToast("Copiado");
    }, function () {});
  }

  function hookOsc52(term) {
    if (!term || term.__rcOsc52 || !term.parser || typeof term.parser.registerOscHandler !== "function") return;
    term.__rcOsc52 = true;
    term.parser.registerOscHandler(52, function (data) {
      var text = decodeOsc52(data);
      if (!text) return true;
      osc52Pending = "";
      writeClipboard(text).then(
        function () {
          showToast("Copiado");
        },
        function () {
          // The browser wants a tap first (phone, or page not focused).
          osc52Pending = text;
          showToast("Toca la pantalla para copiar");
        }
      );
      return true;
    });
  }

  // tmux sizes a shared window to the latest active client (window-size
  // latest). Viewing or tapping does not count as activity, so a device
  // that only looks keeps the other device's size. A focus-in report does
  // count, and tmux only forwards it to apps that asked for focus events.
  // Throttled per connection: switching tabs must claim the new tab at once
  // even if the old one was just claimed by the same tap.
  function claimSize() {
    // herdr gives each device its own view (no size to claim), and a focus
    // report makes herdr re-ask the terminal's colors, repainting the others.
    if (document.hidden || onHerdrTab()) return;
    var ws = window.__rcTermSocket;
    if (!ws || ws.readyState !== 1) return;
    var now = Date.now();
    if (now - (ws.__rcClaimAt || 0) < 1500) return;
    ws.__rcClaimAt = now;
    sendInput(String.fromCharCode(27) + "[I");
  }

  // A tab already connected in the background keeps the size of whichever
  // device used it last; showing it here must take the size back.
  function claimSizeSoon() {
    setTimeout(claimSize, 0);
    setTimeout(claimSize, 400);
  }

  function bootSizeClaim() {
    document.addEventListener("visibilitychange", claimSize);
    window.addEventListener("focus", claimSize);
    window.addEventListener("pageshow", claimSize);
    window.addEventListener("rc-tab-changed", claimSizeSoon);
    window.addEventListener("rc-ws-open", claimSizeSoon);
    ["pointerdown", "touchstart", "wheel"].forEach(function (type) {
      document.addEventListener(type, claimSize, { capture: true, passive: true });
    });
  }

  function bootOsc52() {
    ["pointerdown", "keydown", "touchend"].forEach(function (type) {
      document.addEventListener(type, flushOsc52, true);
    });
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
        var key = ev.key;
        // Plain Ctrl+V does nothing; pasting is Ctrl+Shift+V (or the menu).
        if (chord && !ev.shiftKey && !ev.altKey && (key === "v" || key === "V")) {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
          return;
        }
        if (!chord || !ev.shiftKey || ev.altKey) return;
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
    var named = wsState && wsState.labels && wsState.labels[item.id];
    if (named) return named;
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
    // The workspace keeps the order (drag to reorder); tabs it does not list
    // yet go last, oldest first.
    var ws = typeof currentWs === "function" ? currentWs() : null;
    var rank = {};
    ((ws && ws.tabs) || []).forEach(function (id, i) {
      rank[id] = i;
    });
    items.sort(function (a, b) {
      var ra = a.id in rank ? rank[a.id] : Infinity;
      var rb = b.id in rank ? rank[b.id] : Infinity;
      if (ra !== rb) return ra < rb ? -1 : 1;
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
    forgetSession(id);
    fetch("/rc-session-close?tab=" + encodeURIComponent(id), {
      method: "POST",
      cache: "no-store",
      keepalive: true,
    }).catch(function () {});
  }

  // Tabs closed on any device (server list). Their sockets must not
  // reconnect, and a browser showing one moves to its neighbour.
  var closedTabs = {};

  function takeClosed(list) {
    if (!Array.isArray(list)) return;
    list.forEach(function (id) {
      closedTabs[id] = true;
    });
    Object.keys(panes).forEach(function (id) {
      if (closedTabs[id]) forgetSession(id);
    });
    if (closedTabs[tabId()]) forgetSession(tabId());
  }

  function forgetSession(id) {
    closedTabs[id] = true;
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
      // The last tab closed: show the empty screen, never a fresh tab.
      if (next) openSession(next);
      else if (typeof window.__rcGoEmpty === "function") window.__rcGoEmpty();
    }
    disposePane(id);
    sessionCache = sessionCache.filter(function (item) {
      return item.id !== id;
    });
    paintSessions(sessionCache);
  }

  // "folder · command": the folder recedes, the command carries the tab.
  function paintTabLabel(text, label) {
    text.dataset.label = label;
    text.textContent = "";
    var cut = label.indexOf(" · ");
    if (cut < 0) {
      var name = document.createElement("span");
      name.className = "rc-tab-name";
      name.textContent = label;
      text.appendChild(name);
      return;
    }
    var dir = document.createElement("span");
    dir.className = "rc-tab-dir";
    dir.textContent = label.slice(0, cut);
    var sep = document.createElement("span");
    sep.className = "rc-tab-sep";
    sep.textContent = "·";
    var cmd = document.createElement("span");
    cmd.className = "rc-tab-cmd";
    cmd.textContent = label.slice(cut + 3);
    text.appendChild(dir);
    text.appendChild(sep);
    text.appendChild(cmd);
  }

  // Who opened the tab: their avatar leads the pill (herdr tabs mark each
  // pane instead). While the tab prints, the avatar carries the live pulse.
  function paintTabOwner(el, item) {
    var owner = item.command !== "herdr" && typeof item.owner === "string" ? item.owner : null;
    var dot = el.querySelector(".rc-tab-owner");
    el.classList.toggle("has-owner", owner !== null);
    if (owner === null) {
      if (dot) dot.remove();
      return;
    }
    if (!dot) {
      dot = document.createElement("span");
      dot.className = "rc-tab-owner";
      dot.setAttribute("aria-hidden", "true");
      el.insertBefore(dot, el.firstChild);
    }
    var name = profileLabel(owner) || "?";
    dot.textContent = name.charAt(0).toUpperCase();
    var a = profileAccent(owner);
    dot.style.background = a ? a.dot : "";
    dot.style.color = a ? (themeMode() === "light" ? "#fff" : "#111214") : "";
    el.title = "Abierta por " + name + (item.path ? " · " + item.path : "");
  }

  function fillTab(el, item, counts, current) {
    var label = sessionLabel(item, counts);
    el.dataset.id = item.id;
    el.classList.toggle("is-active", item.id === current);
    el.classList.toggle("is-busy", !!item.busy);
    el.setAttribute("aria-selected", item.id === current ? "true" : "false");
    el.title = item.path || label;
    var text = el.querySelector(".rc-tab-label");
    if (text && text.dataset.label !== label) paintTabLabel(text, label);
    paintTabOwner(el, item);
    var close = el.querySelector(".rc-tab-close");
    if (close) close.setAttribute("aria-label", "Cerrar " + label);
  }

  // --- drag to reorder ----------------------------------------------------------
  // Mouse: press and move. Touch: hold ~300ms first, so a swipe still scrolls
  // the strip. The new order goes to the workspace on the server.
  var tabDrag = null;

  function slideSiblings(host, moved, before) {
    host.querySelectorAll(".rc-tab").forEach(function (tab) {
      if (tab === moved || !(tab.dataset.id in before)) return;
      var dx = before[tab.dataset.id] - tab.offsetLeft;
      if (!dx) return;
      tab.style.transition = "none";
      tab.style.transform = "translateX(" + dx + "px)";
      requestAnimationFrame(function () {
        tab.style.transition = "transform 160ms ease";
        tab.style.transform = "";
      });
    });
  }

  function dragMove(x) {
    var d = tabDrag;
    var host = d.host;
    var hr = host.getBoundingClientRect();
    if (x < hr.left + 32) host.scrollLeft -= 10;
    else if (x > hr.right - 32) host.scrollLeft += 10;
    var tabs = Array.prototype.slice.call(host.querySelectorAll(".rc-tab"));
    var me = d.el;
    var idx = tabs.indexOf(me);
    var target = null;
    var after = false;
    tabs.forEach(function (tab, i) {
      if (tab === me || target) return;
      var r = tab.getBoundingClientRect();
      var mid = r.left + r.width / 2;
      if (i < idx && x < mid) target = tab;
      else if (i > idx && x > mid) {
        target = tab;
        after = true;
      }
    });
    if (target) {
      var before = {};
      tabs.forEach(function (tab) {
        before[tab.dataset.id] = tab.offsetLeft;
      });
      if (after) host.insertBefore(me, target.nextSibling);
      else host.insertBefore(me, target);
      slideSiblings(host, me, before);
    }
    // Screen coordinates on both sides: where the tab sits without the
    // transform, and where the pointer wants it (clamped to the strip).
    var natural = me.getBoundingClientRect().left - (d.tx || 0);
    var want = x - d.grab;
    want = Math.max(hr.left, Math.min(hr.right - me.offsetWidth, want));
    d.tx = Math.round(want - natural);
    me.style.transform = "translateX(" + d.tx + "px)";
  }

  function dragEnd(commit) {
    var d = tabDrag;
    if (!d) return;
    clearTimeout(d.hold);
    tabDrag = null;
    document.documentElement.classList.remove("rc-tab-dragging");
    var me = d.el;
    me.classList.remove("is-dragging");
    if (!d.active) {
      me.style.transform = "";
      return;
    }
    me.__rcDragged = true;
    setTimeout(function () {
      me.__rcDragged = false;
    }, 400);
    me.style.transition = "transform 140ms ease";
    me.style.transform = "";
    setTimeout(function () {
      me.style.transition = "";
    }, 160);
    var order = Array.prototype.map.call(d.host.querySelectorAll(".rc-tab"), function (tab) {
      return tab.dataset.id;
    });
    if (!commit || order.join() === d.start.join()) {
      setTimeout(function () {
        paintSessions(sessionCache);
      }, 170);
      return;
    }
    var ws = currentWs();
    if (!ws) return;
    var rest = ws.tabs.filter(function (id) {
      return order.indexOf(id) === -1;
    });
    ws.tabs = order
      .filter(function (id) {
        return ws.tabs.indexOf(id) !== -1;
      })
      .concat(rest);
    wsPost({ op: "order", id: ws.id, tabs: ws.tabs });
  }

  function dragPointerMove(ev) {
    var d = tabDrag;
    if (!d) return;
    // The button came up where we could not hear it (outside the window).
    if (!d.touch && ev.pointerType === "mouse" && !(ev.buttons & 1)) {
      dragEnd(d.active);
      return;
    }
    if (ev.pointerId !== d.id) return;
    var dx = ev.clientX - d.x0;
    var dy = ev.clientY - d.y0;
    if (!d.active) {
      if (d.touch) {
        // moved before the hold: it is a scroll, not a drag
        if (Math.abs(dx) > 8 || Math.abs(dy) > 8) dragEnd(false);
        return;
      }
      if (Math.abs(dx) < 5) return;
      d.begin();
    }
    ev.preventDefault();
    dragMove(ev.clientX);
  }

  function dragPointerUp(ev) {
    if (tabDrag && ev.pointerId === tabDrag.id) dragEnd(ev.type === "pointerup" || tabDrag.active);
  }

  // Listened on the window: moving the tab in the DOM drops pointer capture.
  window.addEventListener("pointermove", dragPointerMove, true);
  window.addEventListener("pointerup", dragPointerUp, true);
  window.addEventListener("pointercancel", dragPointerUp, true);
  window.addEventListener(
    "mouseup",
    function () {
      if (tabDrag && !tabDrag.touch) dragEnd(tabDrag.active);
    },
    true
  );
  window.addEventListener("blur", function () {
    if (tabDrag) dragEnd(tabDrag.active);
  });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden && tabDrag) dragEnd(tabDrag.active);
  });

  function armTabDrag(el) {
    el.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0 || tabDrag) return;
      if (ev.target && ev.target.closest && ev.target.closest(".rc-tab-close")) return;
      var host = el.parentNode;
      if (!host) return;
      var r = el.getBoundingClientRect();
      var touch = ev.pointerType !== "mouse";
      var d = {
        el: el,
        host: host,
        id: ev.pointerId,
        x0: ev.clientX,
        y0: ev.clientY,
        grab: ev.clientX - r.left,
        active: false,
        touch: touch,
        hold: 0,
        start: Array.prototype.map.call(host.querySelectorAll(".rc-tab"), function (tab) {
          return tab.dataset.id;
        }),
      };
      d.begin = function () {
        if (tabDrag !== d) return;
        d.active = true;
        el.classList.add("is-dragging");
        document.documentElement.classList.add("rc-tab-dragging");
        if (touch && navigator.vibrate) {
          try {
            navigator.vibrate(12);
          } catch (_) {}
        }
      };
      tabDrag = d;
      if (touch) d.hold = setTimeout(d.begin, 300);
    });
    el.addEventListener("contextmenu", function (ev) {
      // long-press menu on phones would fight the hold-to-drag
      if (tabDrag && tabDrag.touch) ev.preventDefault();
    });
  }

  // Once a held tab is being dragged, the finger must not scroll the strip.
  document.addEventListener(
    "touchmove",
    function (ev) {
      if (tabDrag && tabDrag.active) ev.preventDefault();
    },
    { passive: false }
  );

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
      if (el.__rcDragged) {
        el.__rcDragged = false;
        return;
      }
      if (ev.target && ev.target.closest && ev.target.closest(".rc-tab-close")) return;
      openSession(el.dataset.id);
    });
    armTabDrag(el);
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

  // herdr is where several people share a tab: every herdr pane carries, on
  // its top-right corner, the avatar of the profile that opened it. Each
  // device has its own herdr view, so the page finds the pane frames on its
  // own screen and the server says whose each pane is (herdr.pane_marks).
  // Only the marks take clicks; everything else goes through to the terminal.
  var FRAME_TL = "┌┏╔╭";
  var FRAME_TR = "┐┓╗╮";
  var FRAME_V = "│┃║";
  var FRAME_BL = "└┗╚╰";
  var paneMarks = { sig: "", at: 0, busy: false, boxes: [], marks: [] };

  function screenCells(term) {
    var buf = term.buffer.active;
    var rows = [];
    for (var r = 0; r < term.rows; r++) {
      var line = buf.getLine(buf.viewportY + r);
      var cells = [];
      for (var x = 0; x < term.cols; x++) {
        var cell = line && line.getCell(x);
        cells.push(cell ? cell.getChars() : "");
      }
      rows.push(cells);
    }
    return rows;
  }

  function isIn(set, ch) {
    return !!ch && set.indexOf(ch) !== -1;
  }

  function findFrames(rows) {
    var out = [];
    rows.forEach(function (row, y) {
      for (var x = 0; x < row.length; x++) {
        if (!isIn(FRAME_TL, row[x])) continue;
        var x2 = -1;
        for (var k = x + 1; k < row.length; k++) {
          if (isIn(FRAME_TR, row[k])) {
            x2 = k;
            break;
          }
          if (isIn(FRAME_TL, row[k])) break;
        }
        var next = rows[y + 1];
        if (x2 < 0 || !next || !isIn(FRAME_V, next[x]) || !isIn(FRAME_V, next[x2])) continue;
        var y2 = rows.length - 1;
        for (var j = y + 1; j < rows.length; j++) {
          if (isIn(FRAME_BL, rows[j][x])) {
            y2 = j;
            break;
          }
          if (!isIn(FRAME_V, rows[j][x])) {
            y2 = j - 1;
            break;
          }
        }
        var lines = [];
        for (var i = y + 1; i < y2 && lines.length < 8; i++) {
          var text = rows[i].slice(x + 1, x2).join("").replace(/\s+/g, " ").trim();
          if (text.length >= 4) lines.push(text);
        }
        out.push({ x: x, y: y, w: x2 - x + 1, h: y2 - y + 1, lines: lines });
        x = x2;
      }
    });
    return out;
  }

  // A herdr tab with one pane draws no frame: the pane is everything right
  // of the sidebar's vertical rule and below the tab strip.
  function singlePane(rows) {
    var cols = rows.length ? rows[0].length : 0;
    var rule = -1;
    var bottom = rows.length - 1;
    for (var x = 0; x < cols && rule < 0; x++) {
      var hits = 0;
      var last = 0;
      for (var y = 0; y < rows.length; y++) {
        if (isIn(FRAME_V, rows[y][x])) {
          hits++;
          last = y;
        }
      }
      if (hits >= Math.max(4, rows.length * 0.7)) {
        rule = x;
        bottom = last;
      }
    }
    var left = rule + 1;
    if (rows.length < 4 || cols - left < 10) return null;
    var lines = [];
    for (var i = 1; i <= bottom && lines.length < 8; i++) {
      var text = rows[i].slice(left).join("").replace(/\s+/g, " ").trim();
      if (text.length >= 4) lines.push(text);
    }
    return { x: left, y: 1, w: cols - left, h: bottom, lines: lines };
  }

  function onHerdrTab() {
    if (window.__rcEmpty) return false;
    var id = tabId();
    return sessionCache.some(function (item) {
      return item.id === id && item.command === "herdr";
    });
  }

  function placePaneMarks(term) {
    var host = document.getElementById("rc-pane-marks");
    var screen = term && term.element && term.element.querySelector(".xterm-screen");
    if (!paneMarks.marks.length || !screen) {
      if (host) host.hidden = true;
      return;
    }
    if (!host) {
      host = document.createElement("div");
      host.id = "rc-pane-marks";
      // The marks take clicks (who the pane is); the rest passes through.
      host.addEventListener("pointerdown", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      });
      host.addEventListener("click", function (ev) {
        var dot = ev.target && ev.target.closest && ev.target.closest(".rc-pane-mark");
        if (!dot) return;
        ev.preventDefault();
        ev.stopPropagation();
        var pop = document.getElementById("rc-profile-pop");
        if (pop && pop.dataset.pane === dot.dataset.pane) closeProfilePop();
        else openPaneOwnerPop(dot);
      });
      (document.body || document.documentElement).appendChild(host);
    }
    var rect = screen.getBoundingClientRect();
    var cellW = rect.width / term.cols;
    var cellH = rect.height / term.rows;
    var size = Math.round(Math.max(20, Math.min(30, cellH * 1.7)));
    var light = themeMode() === "light";
    var dots = host.children;
    var n = 0;
    paneMarks.marks.forEach(function (mark) {
      var box = paneMarks.boxes[mark.box];
      if (!box || typeof mark.owner !== "string") return;
      var dot = dots[n] || host.appendChild(document.createElement("span"));
      n++;
      dot.className = "rc-ws-profile-dot rc-pane-mark";
      dot.textContent = (profileLabel(mark.owner) || "?").charAt(0).toUpperCase();
      dot.dataset.pane = mark.pane || "";
      dot.dataset.owner = mark.owner;
      dot.setAttribute("role", "button");
      dot.title = "Panel de " + profileLabel(mark.owner) + " · clic para cambiar de usuario";
      dot.setAttribute("aria-label", dot.title);
      var a = profileAccent(mark.owner);
      dot.style.background = a ? a.dot : "";
      dot.style.color = a ? (light ? "#fff" : "#111214") : "";
      dot.style.width = dot.style.height = size + "px";
      // Tucked into the frame's top-right corner, over the corner glyph.
      dot.style.left = Math.round(rect.left + (box.x + box.w) * cellW - size * 0.82) + "px";
      dot.style.top = Math.round(rect.top + box.y * cellH + cellH * 0.62 - size * 0.42) + "px";
    });
    while (dots.length > n) host.removeChild(dots[dots.length - 1]);
    host.hidden = n === 0;
  }

  function scanPaneMarks() {
    var term = xterm();
    if (!onHerdrTab() || !term || !term.buffer || document.hidden) {
      paneMarks.marks = [];
      paneMarks.sig = "";
      placePaneMarks(null);
      return;
    }
    var cells = screenCells(term);
    var boxes = findFrames(cells);
    if (!boxes.length) {
      var whole = singlePane(cells);
      if (whole) boxes = [whole];
    }
    var sig =
      tabId() +
      "|" +
      boxes
        .map(function (b) {
          return [b.x, b.y, b.w, b.h].join(",");
        })
        .join(";");
    // New frames: old marks would sit in the wrong places until the answer.
    if (sig !== paneMarks.sig) paneMarks.marks = [];
    paneMarks.boxes = boxes;
    placePaneMarks(term);
    var now = Date.now();
    if (paneMarks.busy || (sig === paneMarks.sig && now - paneMarks.at < 4000)) return;
    paneMarks.sig = sig;
    paneMarks.at = now;
    if (!boxes.length) return;
    paneMarks.busy = true;
    fetch("/rc-herdr-marks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ boxes: boxes }),
    })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (payload) {
        paneMarks.busy = false;
        if (paneMarks.sig !== sig) return;
        paneMarks.marks = (payload && payload.marks) || [];
        placePaneMarks(xterm());
      })
      .catch(function () {
        paneMarks.busy = false;
      });
  }

  // A pane's mark opens this menu: picking a profile gives it the pane, so
  // the CLIs there run with that account (herdr.set_pane_owner). A Claude
  // waiting in the pane comes back with the new account, same chat.
  function openPaneOwnerPop(dot) {
    var pane = dot.dataset.pane;
    if (!pane) return;
    var owner = dot.dataset.owner || "";
    closeProfilePop();
    var pop = document.createElement("div");
    pop.id = "rc-profile-pop";
    pop.dataset.pane = pane;
    pop.setAttribute("role", "menu");
    var title = document.createElement("div");
    title.className = "rc-profile-pop-title";
    title.textContent = "Usuario de este panel";
    pop.appendChild(title);
    [{ id: "", label: profState.primary || "Principal" }].concat(profState.profiles).forEach(function (p) {
      var b = wsButton("", "rc-profile-pop-item" + (p.id === owner ? " is-on" : ""), "Usar la cuenta de " + p.label + " en este panel", function () {
        closeProfilePop();
        if (p.id !== owner) setPaneOwner(pane, p.id);
      });
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", p.id === owner ? "true" : "false");
      var mark = document.createElement("span");
      mark.className = "rc-ws-profile-dot";
      mark.textContent = (p.label || "?").charAt(0).toUpperCase();
      var name = document.createElement("span");
      name.className = "rc-ws-profile-name";
      name.textContent = p.label;
      paintProfileLabel(mark, name, p.id);
      b.appendChild(mark);
      b.appendChild(name);
      pop.appendChild(b);
    });
    (document.body || document.documentElement).appendChild(pop);
    var r = dot.getBoundingClientRect();
    var w = pop.offsetWidth;
    var h = pop.offsetHeight;
    var top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    pop.style.top = Math.round(top) + "px";
    pop.style.left = Math.round(Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))) + "px";
    document.addEventListener("pointerdown", profilePopOutside, true);
    document.addEventListener("keydown", profilePopKey, true);
  }

  function setPaneOwner(pane, prof) {
    var label = profileLabel(prof);
    paneMarks.marks.forEach(function (mark) {
      if (mark.pane === pane) mark.owner = prof;
    });
    placePaneMarks(xterm());
    showToast("Panel → " + label + "…");
    fetch("/rc-herdr-owner", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pane: pane, profile: prof }),
    })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (res) {
        paneMarks.at = 0;
        if (!res || !res.ok) {
          showToast((res && res.error) || "No se pudo cambiar el usuario");
          return;
        }
        if (res.note) showToast(res.note);
        else if (res.restarted) showToast("Claude volvió con la cuenta de " + label);
        else showToast("Este panel ahora usa la cuenta de " + label);
      })
      .catch(function () {
        paneMarks.at = 0;
        showToast("No se pudo cambiar el usuario");
      });
  }

  function bootPaneMarks() {
    setInterval(scanPaneMarks, 1000);
    window.addEventListener("resize", function () {
      placePaneMarks(xterm());
    });
    window.addEventListener("rc-tab-changed", scanPaneMarks);
  }

  function paintSessions(list) {
    if (Array.isArray(list)) sessionCache = list;
    var host = document.getElementById("rc-session-tabs");
    if (!host || (tabDrag && tabDrag.active)) return;
    var current = tabId();
    var items = orderedSessions(
      inWorkspace(sessionCache).filter(function (item) {
        return !closedTabs[item.id];
      }),
      current
    );
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
    items.forEach(function (item) {
      if (item.id === current) setPageTitle(sessionLabel(item, counts));
    });
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
    fetch("/rc-workspaces", { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(takeWorkspaces)
      .catch(function () {});
  }

  // --- workspaces -----------------------------------------------------------
  // The server keeps them (the tunnel URL changes on every start). A browser
  // shows the workspace that holds its current tab.

  var wsState = null;
  var wsPinned = "";

  function takeWorkspaces(payload) {
    if (!payload || !Array.isArray(payload.workspaces)) return payload;
    wsState = payload;
    // Only this device's own choice; another device's theme never applies here.
    var myTheme = payload.themes && payload.themes[foldDevice()];
    if (myTheme) setThemePref(myTheme, true);
    if (payload.micPos) setMicPos(payload.micPos, true);
    if (payload.filePos) setFilePos(payload.filePos, true);
    if (payload.filesPos) setFilesPos(payload.filesPos, true);
    renderProfiles();
    refreshLook();
    takeAgentNotes(payload.agentNotes);
    var emptySub = document.querySelector("#rc-empty .rc-empty-sub");
    var emptyWs = emptySub && currentWs();
    if (emptyWs) emptySub.textContent = "Workspace " + emptyWs.name;
    if (window.__rcFiles && window.__rcFiles.takePrefs) window.__rcFiles.takePrefs(payload.fsPrefs);
    if (Array.isArray(payload.sessions)) {
      sessionCache = payload.sessions;
      takeClosed(payload.closed);
      paintSessions(sessionCache);
    }
    renderDrawer();
    return payload;
  }

  // `rc prompt` from an agent of another user to one of this profile's agents
  // (agents.py). Notes older than this page are only marked as seen.
  var agentNoteSeen = Date.now() / 1000;

  function takeAgentNotes(notes) {
    if (!Array.isArray(notes)) return;
    var me = myProfile();
    notes.forEach(function (note) {
      if (!note || typeof note.t !== "number" || note.t <= agentNoteSeen) return;
      agentNoteSeen = note.t;
      if (note.to !== me) return;
      showToast("El agente de " + (note.fromLabel || "otro usuario") + " le escribió a tu " + (note.cli || "agente") + " (" + note.target + ")");
    });
  }

  function findWs(id) {
    if (!wsState || !id) return null;
    for (var i = 0; i < wsState.workspaces.length; i++) {
      if (wsState.workspaces[i].id === id) return wsState.workspaces[i];
    }
    return null;
  }

  function wsOfTab(tab) {
    if (!wsState || !tab) return null;
    for (var i = 0; i < wsState.workspaces.length; i++) {
      if (wsState.workspaces[i].tabs.indexOf(tab) !== -1) return wsState.workspaces[i];
    }
    return null;
  }

  function currentWs() {
    if (!wsState) return null;
    return wsOfTab(tabId()) || findWs(wsPinned) || findWs(wsState.active) || wsState.workspaces[0] || null;
  }

  function inWorkspace(list) {
    var ws = currentWs();
    if (!ws) return list;
    var mine = {};
    ws.tabs.forEach(function (id) {
      mine[id] = true;
    });
    return (list || []).filter(function (item) {
      return mine[item.id] || item.id === tabId();
    });
  }

  function wsPost(op) {
    return fetch("/rc-workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(op),
      cache: "no-store",
    })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(takeWorkspaces)
      .catch(function () {
        return null;
      });
  }

  // Every tab opened from here lands in the workspace it was opened from.
  function hookTabOpen() {
    var open = window.__rcOpenSession;
    if (typeof open !== "function" || open.__rcWs) return;
    var wrapped = function (id) {
      var home = wsOfTab(id) || findWs(wsPinned) || currentWs();
      if (home) wsPinned = home.id;
      open(id);
      wsPost({ op: "open", tab: id, id: home ? home.id : "" });
    };
    wrapped.__rcWs = true;
    window.__rcOpenSession = wrapped;
  }

  function enterWorkspace(ws) {
    if (!ws) return;
    wsPinned = ws.id;
    closeDrawer();
    var live = {};
    sessionCache.forEach(function (item) {
      live[item.id] = true;
    });
    var target = live[ws.lastTab] ? ws.lastTab : "";
    if (!target) {
      for (var i = ws.tabs.length - 1; i >= 0 && !target; i--) {
        if (live[ws.tabs[i]]) target = ws.tabs[i];
      }
    }
    var activated = wsPost({ op: "activate", id: ws.id });
    if (target && target === tabId()) {
      paintSessions(sessionCache);
      return;
    }
    if (target) openSession(target);
    else if (typeof window.__rcGoEmpty === "function") {
      // A workspace with no tabs opens empty; reload once the server knows.
      activated.then(window.__rcGoEmpty, window.__rcGoEmpty);
    }
  }

  function deleteWorkspace(ws) {
    if (!ws || !wsState) return;
    var leaving = currentWs() === ws;
    if (leaving) {
      var other = null;
      wsState.workspaces.forEach(function (item) {
        if (!other && item !== ws) other = item;
      });
      if (other) enterWorkspace(other);
      else {
        // Last workspace: start a clean one first so there is somewhere to go.
        wsPost({ op: "create", name: "General" }).then(function (payload) {
          if (payload && payload.id) enterWorkspace(findWs(payload.id));
          dropWorkspace(ws);
        });
        return;
      }
    }
    dropWorkspace(ws);
  }

  function dropWorkspace(ws) {
    // Close the panes first so their reconnect does not revive the sessions.
    ws.tabs.forEach(function (id) {
      if (id !== tabId()) disposePane(id);
    });
    wsPost({ op: "delete", id: ws.id });
  }

  var drawerEdit = "";
  var drawerConfirm = "";

  function openDrawer() {
    if (document.documentElement.classList.contains("rc-touch")) lockIme();
    hideCopyChip();
    document.documentElement.classList.add("rc-ws-open");
    drawerEdit = "";
    drawerConfirm = "";
    renderDrawer();
    pullSessions();
    loadProfiles();
  }

  function closeDrawer() {
    document.documentElement.classList.remove("rc-ws-open");
    drawerEdit = "";
    drawerConfirm = "";
    var find = document.getElementById("rc-ws-search");
    if (find) {
      find.value = "";
      find.blur();
    }
    if (wsFilter) {
      wsFilter = "";
      renderDrawer();
    }
  }

  var wsFilter = "";

  function liveCount(ws) {
    var live = {};
    sessionCache.forEach(function (item) {
      live[item.id] = true;
    });
    return ws.tabs.filter(function (id) {
      return live[id];
    }).length;
  }

  // Drawer glyphs drawn like the rest of the bar (16px grid, 1.6 stroke).
  var WS_ICONS = {
    "✎":
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10.2 3.3l2.5 2.5M3 13l.6-3 7-7a1.4 1.4 0 0 1 2 0l.4.4a1.4 1.4 0 0 1 0 2l-7 7z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>',
    "🗑":
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.8 4.4h10.4M6.4 4.4V3a.8.8 0 0 1 .8-.8h1.6a.8.8 0 0 1 .8.8v1.4M4.2 4.4l.6 8.4a1 1 0 0 0 1 .9h4.4a1 1 0 0 0 1-.9l.6-8.4M6.8 7v4.2M9.2 7v4.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>',
    "+": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  };
  var WS_CARET =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 6.2L8 9.8l3.5-3.6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function wsButton(label, cls, title, fn) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    if (WS_ICONS[label] && cls.indexOf("rc-ws-act") !== -1) {
      b.innerHTML = WS_ICONS[label];
      if (title) b.title = title;
    } else b.textContent = label;
    if (title) b.setAttribute("aria-label", title);
    b.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      fn();
    });
    return b;
  }

  var groupEdit = "";
  var groupConfirm = "";
  var FOLD_KEY = "rc-ws-folded";

  // Each device keeps its own folded groups. The tunnel URL (and with it the
  // browser storage) changes on every start, so the server keeps them under a
  // key made from the device kind and its browser, which survive that.
  function foldDevice() {
    var ua = navigator.userAgent || "";
    var h = 5381;
    for (var i = 0; i < ua.length; i++) h = ((h * 33) ^ ua.charCodeAt(i)) >>> 0;
    return (document.documentElement.dataset.rcDevice || "pc") + "-" + h.toString(36);
  }

  function readFolded() {
    var server = wsState && wsState.folded && wsState.folded[foldDevice()];
    if (Array.isArray(server)) {
      var out = {};
      server.forEach(function (gid) {
        out[gid] = true;
      });
      return out;
    }
    try {
      var raw = JSON.parse(localStorage.getItem(FOLD_KEY) || "{}");
      return raw && typeof raw === "object" ? raw : {};
    } catch (_) {
      return {};
    }
  }

  function toggleFold(gid) {
    var folded = readFolded();
    if (folded[gid]) delete folded[gid];
    else folded[gid] = true;
    try {
      localStorage.setItem(FOLD_KEY, JSON.stringify(folded));
    } catch (_) {}
    var device = foldDevice();
    if (wsState) {
      wsState.folded = wsState.folded || {};
      wsState.folded[device] = Object.keys(folded);
    }
    renderDrawer();
    wsPost({ op: "fold", device: device, groups: Object.keys(folded) });
  }

  function wsGroups() {
    var groups = wsState && Array.isArray(wsState.groups) ? wsState.groups : [];
    return groups.length ? groups : [{ id: "gpc", name: "PC", fixed: true }];
  }

  function nameInput(id, value, done) {
    var input = document.createElement("input");
    input.id = id;
    input.className = "rc-ws-input";
    input.type = "text";
    input.maxLength = 40;
    input.value = value;
    var settled = false;
    function finish(save) {
      if (settled) return;
      settled = true;
      done(save ? input.value.trim() : "");
    }
    input.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      if (ev.key === "Enter") {
        ev.preventDefault();
        finish(true);
        input.blur();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        finish(false);
        input.blur();
      }
    });
    input.addEventListener("blur", function () {
      finish(true);
    });
    setTimeout(function () {
      try {
        input.focus();
        input.select();
      } catch (_) {}
    }, 0);
    return input;
  }

  function wsRow(ws, cur) {
    var row = document.createElement("div");
    row.className = "rc-ws-item" + (ws === cur ? " is-current" : "");
    if (drawerEdit === ws.id) {
      row.classList.add("is-editing");
      row.appendChild(
        nameInput("rc-ws-rename", ws.name, function (name) {
          if (drawerEdit !== ws.id) return;
          drawerEdit = "";
          if (name && name !== ws.name) wsPost({ op: "rename", id: ws.id, name: name });
          else setTimeout(renderDrawer, 0);
        })
      );
      var groups = wsGroups();
      if (groups.length > 1) {
        var pick = document.createElement("select");
        pick.className = "rc-ws-move";
        pick.setAttribute("aria-label", "Mover a grupo");
        groups.forEach(function (g) {
          var opt = document.createElement("option");
          opt.value = g.id;
          opt.textContent = g.name;
          if (g.id === ws.group) opt.selected = true;
          pick.appendChild(opt);
        });
        // Picking a group must not count as leaving the name field.
        pick.addEventListener("pointerdown", function (ev) {
          ev.preventDefault();
        });
        pick.addEventListener("mousedown", function (ev) {
          ev.stopPropagation();
        });
        pick.addEventListener("change", function () {
          drawerEdit = "";
          wsPost({ op: "move", id: ws.id, group: pick.value });
        });
        row.appendChild(pick);
      }
      return row;
    }
    if (drawerConfirm === ws.id) {
      var n = liveCount(ws);
      var ask = document.createElement("span");
      ask.className = "rc-ws-ask";
      ask.textContent =
        "¿Estás seguro? " +
        (n ? "Se cerrará" + (n === 1 ? " 1 pestaña." : "n " + n + " pestañas.") : "Se borrará el workspace.");
      row.appendChild(ask);
      row.appendChild(
        wsButton("Sí", "rc-ws-yes", "Confirmar", function () {
          drawerConfirm = "";
          deleteWorkspace(ws);
        })
      );
      row.appendChild(
        wsButton("No", "rc-ws-no", "Cancelar", function () {
          drawerConfirm = "";
          renderDrawer();
        })
      );
      return row;
    }
    var main = document.createElement("button");
    main.type = "button";
    main.className = "rc-ws-main";
    var name = document.createElement("span");
    name.className = "rc-ws-name";
    name.textContent = ws.name;
    var count = document.createElement("span");
    count.className = "rc-ws-count";
    count.textContent = String(liveCount(ws));
    main.appendChild(name);
    main.appendChild(count);
    main.addEventListener("click", function () {
      enterWorkspace(ws);
    });
    row.appendChild(main);
    row.appendChild(
      wsButton("✎", "rc-ws-act", "Renombrar o mover " + ws.name, function () {
        drawerEdit = ws.id;
        drawerConfirm = "";
        renderDrawer();
      })
    );
    row.appendChild(
      wsButton("🗑", "rc-ws-act is-danger", "Borrar " + ws.name, function () {
        drawerConfirm = ws.id;
        drawerEdit = "";
        renderDrawer();
      })
    );
    return row;
  }

  function groupHead(group, members, folded) {
    var head = document.createElement("div");
    head.className = "rc-ws-group" + (folded ? " is-folded" : "");
    if (groupEdit === group.id) {
      head.appendChild(
        nameInput("rc-ws-group-name", group.name, function (name) {
          if (groupEdit !== group.id) return;
          groupEdit = "";
          if (group.id === "new") {
            if (name) wsPost({ op: "groupCreate", name: name });
            else setTimeout(renderDrawer, 0);
          } else if (name && name !== group.name) wsPost({ op: "groupRename", group: group.id, name: name });
          else setTimeout(renderDrawer, 0);
        })
      );
      return head;
    }
    if (groupConfirm === group.id) {
      var ask = document.createElement("span");
      ask.className = "rc-ws-ask";
      ask.textContent = members.length
        ? "¿Borrar el grupo? Sus workspaces pasan a PC."
        : "¿Borrar el grupo?";
      head.appendChild(ask);
      head.appendChild(
        wsButton("Sí", "rc-ws-yes", "Confirmar", function () {
          groupConfirm = "";
          wsPost({ op: "groupDelete", group: group.id });
        })
      );
      head.appendChild(
        wsButton("No", "rc-ws-no", "Cancelar", function () {
          groupConfirm = "";
          renderDrawer();
        })
      );
      return head;
    }
    var fold = document.createElement("button");
    fold.type = "button";
    fold.className = "rc-ws-group-name";
    fold.setAttribute("aria-expanded", folded ? "false" : "true");
    var caret = document.createElement("span");
    caret.className = "rc-ws-caret";
    caret.innerHTML = WS_CARET;
    var label = document.createElement("span");
    label.className = "rc-ws-group-label";
    label.textContent = group.name;
    fold.appendChild(caret);
    fold.appendChild(label);
    if (folded && members.length) {
      var hint = document.createElement("span");
      hint.className = "rc-ws-count";
      hint.textContent = String(members.length);
      fold.appendChild(hint);
    }
    fold.setAttribute("aria-label", (folded ? "Mostrar " : "Ocultar ") + group.name);
    fold.addEventListener("click", function () {
      toggleFold(group.id);
    });
    head.appendChild(fold);
    if (group.id === "gphone") {
      head.appendChild(
        wsButton("Auto", "rc-ws-auto", "Copiar herdr a Phone", function () {
          openAutoModal();
        })
      );
    }
    if (members.length) {
      head.appendChild(
        wsButton("Clear", "rc-ws-clear", "Vaciar " + group.name, function () {
          openClearModal(group);
        })
      );
    }
    if (!group.fixed) {
      head.appendChild(
        wsButton("✎", "rc-ws-act", "Renombrar grupo " + group.name, function () {
          groupEdit = group.id;
          groupConfirm = "";
          renderDrawer();
        })
      );
      head.appendChild(
        wsButton("🗑", "rc-ws-act is-danger", "Borrar grupo " + group.name, function () {
          groupConfirm = group.id;
          groupEdit = "";
          renderDrawer();
        })
      );
    }
    head.appendChild(
      wsButton("+", "rc-ws-act rc-ws-plus", "Nuevo workspace en " + group.name, function () {
        var n = wsState ? wsState.workspaces.length + 1 : 1;
        wsPost({ op: "create", name: "Workspace " + n, group: group.id }).then(function (payload) {
          var ws = payload && findWs(payload.id);
          if (ws) enterWorkspace(ws);
        });
      })
    );
    return head;
  }

  function renderDrawer() {
    var list = document.getElementById("rc-ws-list");
    if (!list || !wsState) return;
    var active = document.activeElement;
    if (active && list.contains(active) && active.tagName === "INPUT") return;
    var cur = currentWs();
    var folded = readFolded();
    list.innerHTML = "";
    var groups = wsGroups();
    var known = {};
    groups.forEach(function (g) {
      known[g.id] = true;
    });
    groups.forEach(function (group) {
      var members = wsState.workspaces.filter(function (ws) {
        return ws.group === group.id || (!known[ws.group] && group.id === groups[0].id);
      });
      if (wsFilter) {
        members = members.filter(function (ws) {
          return String(ws.name || "").toLowerCase().indexOf(wsFilter) !== -1;
        });
        if (!members.length) return;
      }
      var isFolded = !!folded[group.id] && !wsFilter;
      var section = document.createElement("section");
      section.className = "rc-ws-section";
      section.appendChild(groupHead(group, members, isFolded));
      if (!isFolded) {
        if (!members.length && !wsFilter) {
          // An empty group offers to start its first workspace right there.
          section.appendChild(
            wsButton("+ Nuevo workspace", "rc-ws-add rc-ws-add-in", "Nuevo workspace en " + group.name, function () {
              var n = wsState ? wsState.workspaces.length + 1 : 1;
              wsPost({ op: "create", name: "Workspace " + n, group: group.id }).then(function (payload) {
                var ws = payload && findWs(payload.id);
                if (ws) enterWorkspace(ws);
              });
            })
          );
        }
        members.forEach(function (ws) {
          section.appendChild(wsRow(ws, cur));
        });
      }
      list.appendChild(section);
    });
    if (wsFilter && !list.childNodes.length) {
      var none = document.createElement("div");
      none.className = "rc-ws-empty";
      none.textContent = "Ningún workspace coincide.";
      list.appendChild(none);
    }
    if (groupEdit === "new") {
      var section = document.createElement("section");
      section.className = "rc-ws-section";
      section.appendChild(groupHead({ id: "new", name: "", fixed: false }, [], false));
      list.appendChild(section);
    }
  }

  // --- Auto: copy herdr into Phone ---------------------------------------------

  var autoBusy = false;

  function closeAutoModal() {
    if (autoBusy) return;
    var modal = document.getElementById("rc-auto-modal");
    if (modal) modal.remove();
  }

  function autoModalShell() {
    closeAutoModal();
    var modal = document.createElement("div");
    modal.id = "rc-auto-modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "rc-auto-title");
    modal.addEventListener("click", function (ev) {
      if (ev.target === modal) closeAutoModal();
    });
    modal.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      if (ev.key === "Escape") closeAutoModal();
    });
    var card = document.createElement("div");
    card.className = "rc-auto-card";
    var title = document.createElement("div");
    title.id = "rc-auto-title";
    title.className = "rc-auto-title";
    title.textContent = "Copiar herdr a Phone";
    card.appendChild(title);
    modal.appendChild(card);
    (document.body || document.documentElement).appendChild(modal);
    return card;
  }

  function shortPath(path) {
    var parts = String(path || "").split("/").filter(Boolean);
    return parts.length ? parts.slice(-2).join("/") : "~";
  }

  function openAutoModal() {
    if (autoBusy) return;
    var card = autoModalShell();
    var wait = document.createElement("div");
    wait.className = "rc-auto-note";
    wait.textContent = "Leyendo herdr…";
    card.appendChild(wait);
    fetch("/rc-workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op: "herdrPlan" }),
      cache: "no-store",
    })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (payload) {
        if (!document.getElementById("rc-auto-modal")) return;
        takeWorkspaces(payload);
        if (!payload || !payload.ok) {
          wait.textContent = (payload && payload.error) || "No pude leer herdr";
          wait.classList.add("is-error");
          card.appendChild(autoActions(null));
          return;
        }
        fillAutoModal(card, wait, payload);
      })
      .catch(function () {
        wait.textContent = "No pude leer herdr";
        wait.classList.add("is-error");
        card.appendChild(autoActions(null));
      });
  }

  function fillAutoModal(card, note, payload) {
    var plan = payload.plan || [];
    var panes = 0;
    plan.forEach(function (ws) {
      panes += ws.panes.length;
    });
    note.className = "rc-auto-note is-warn";
    note.textContent =
      (payload.closing
        ? "Se cerrará todo lo que hay en Phone (" + payload.closing + (payload.closing === 1 ? " pestaña" : " pestañas") + "). "
        : "") +
      "Se crearán " + plan.length + (plan.length === 1 ? " workspace" : " workspaces") +
      " con " + panes + (panes === 1 ? " pestaña" : " pestañas") + ". herdr no se toca.";
    var list = document.createElement("div");
    list.className = "rc-auto-list";
    plan.forEach(function (ws) {
      var block = document.createElement("div");
      block.className = "rc-auto-ws";
      var head = document.createElement("div");
      head.className = "rc-auto-ws-name";
      head.textContent = ws.name;
      block.appendChild(head);
      ws.panes.forEach(function (pane) {
        var row = document.createElement("div");
        row.className = "rc-auto-pane";
        var label = document.createElement("span");
        label.className = "rc-auto-pane-label";
        label.textContent = pane.label;
        var what = document.createElement("span");
        what.className = "rc-auto-pane-cmd" + (pane.command ? "" : " is-shell");
        what.textContent = (pane.command || "terminal") + " · " + shortPath(pane.cwd);
        what.title = (pane.command || "terminal") + " · " + pane.cwd;
        row.appendChild(label);
        row.appendChild(what);
        block.appendChild(row);
      });
      list.appendChild(block);
    });
    card.appendChild(list);
    card.appendChild(autoActions(plan.length ? applyAuto : null));
  }

  function autoActions(onApply) {
    var bar = document.createElement("div");
    bar.className = "rc-auto-actions";
    var cancel = wsButton(onApply ? "Cancelar" : "Cerrar", "rc-auto-cancel", "", closeAutoModal);
    bar.appendChild(cancel);
    if (onApply) {
      var go = wsButton("Reemplazar Phone", "rc-auto-go", "", function () {
        onApply(cancel, go);
      });
      bar.appendChild(go);
      setTimeout(function () {
        try {
          go.focus();
        } catch (_) {}
      }, 0);
    } else {
      setTimeout(function () {
        try {
          cancel.focus();
        } catch (_) {}
      }, 0);
    }
    return bar;
  }

  // --- Clear: empty a group ----------------------------------------------------

  function openClearModal(group) {
    if (autoBusy || !wsState) return;
    var members = wsState.workspaces.filter(function (ws) {
      return ws.group === group.id;
    });
    var card = autoModalShell();
    card.querySelector(".rc-auto-title").textContent = "Vaciar " + group.name;
    var tabs = 0;
    members.forEach(function (ws) {
      tabs += liveCount(ws);
    });
    var note = document.createElement("div");
    note.className = "rc-auto-note is-warn";
    note.textContent =
      "Se borrarán " + members.length + (members.length === 1 ? " workspace" : " workspaces") +
      (tabs ? " y se cerrarán " + tabs + (tabs === 1 ? " pestaña" : " pestañas") : "") +
      ". El grupo " + group.name + " queda vacío.";
    card.appendChild(note);
    var list = document.createElement("div");
    list.className = "rc-auto-list";
    var labels = {};
    sessionCache.forEach(function (item) {
      labels[item.id] = item;
    });
    var counts = {};
    sessionCache.forEach(function (item) {
      var key = sessionKey(item);
      counts[key] = (counts[key] || 0) + 1;
    });
    members.forEach(function (ws) {
      var block = document.createElement("div");
      block.className = "rc-auto-ws";
      var head = document.createElement("div");
      head.className = "rc-auto-ws-name";
      head.textContent = ws.name;
      block.appendChild(head);
      ws.tabs.forEach(function (id) {
        if (!labels[id]) return;
        var row = document.createElement("div");
        row.className = "rc-auto-pane";
        var label = document.createElement("span");
        label.className = "rc-auto-pane-label";
        label.textContent = sessionLabel(labels[id], counts);
        row.appendChild(label);
        block.appendChild(row);
      });
      list.appendChild(block);
    });
    card.appendChild(list);
    var bar = autoActions(function (cancel, go) {
      applyClear(group, cancel, go);
    });
    bar.querySelector(".rc-auto-go").textContent = "Vaciar " + group.name;
    card.appendChild(bar);
  }

  function applyClear(group, cancel, go) {
    if (autoBusy || !wsState) return;
    autoBusy = true;
    cancel.disabled = true;
    go.disabled = true;
    go.textContent = "Vaciando…";
    var current = tabId();
    var old = [];
    wsState.workspaces.forEach(function (ws) {
      if (ws.group === group.id) old = old.concat(ws.tabs);
    });
    old.forEach(function (id) {
      if (id !== current) disposePane(id);
    });
    var keep = old.indexOf(current) !== -1 ? current : "";
    wsPost({ op: "groupClear", group: group.id, keep: keep }).then(function (payload) {
      autoBusy = false;
      if (!payload || !payload.ok) {
        go.disabled = false;
        cancel.disabled = false;
        go.textContent = "Vaciar " + group.name;
        showToast("No pude vaciar " + group.name);
        return;
      }
      closeAutoModal();
      if (keep) {
        // Move to whatever workspace is left before closing the tab on screen.
        var next = findWs(payload.active);
        if (next) enterWorkspace(next);
        else closeDrawer();
        closeSession(keep);
      }
      showToast(group.name + " vacío");
    });
  }

  function applyAuto(cancel, go) {
    if (autoBusy) return;
    autoBusy = true;
    cancel.disabled = true;
    go.disabled = true;
    go.textContent = "Copiando…";
    var current = tabId();
    var old = [];
    (wsState ? wsState.workspaces : []).forEach(function (ws) {
      if (ws.group === "gphone") old = old.concat(ws.tabs);
    });
    // Close the old panes first so their reconnect does not revive them.
    old.forEach(function (id) {
      if (id !== current) disposePane(id);
    });
    var keep = old.indexOf(current) !== -1 ? current : "";
    wsPost({ op: "herdrApply", keep: keep }).then(function (payload) {
      autoBusy = false;
      if (!payload || !payload.ok) {
        go.disabled = false;
        cancel.disabled = false;
        go.textContent = "Reemplazar Phone";
        showToast((payload && payload.error) || "No pude copiar herdr");
        return;
      }
      closeAutoModal();
      closeDrawer();
      wsPinned = payload.id;
      if (payload.firstTab) openSession(payload.firstTab);
      if (keep) closeSession(keep);
      showToast("Phone listo");
    });
  }

  // --- profiles ---------------------------------------------------------------
  // Several people can share this Remote Control, each with their own CLI
  // accounts. Every device picks a profile; the device that typed last tells
  // the server, and a new AI CLI (claude, agent…) then runs with that
  // profile's folder as HOME (web/profiles.zsh). The primary user is "".

  var PROFILE_KEY = "rc-profile";
  var profState = { primary: "", profiles: [] };
  var profEditing = false;
  var profPinged = { at: 0, id: null };

  function myProfile() {
    // "" is a real choice (the primary user); null means this browser never chose.
    var id = null;
    try {
      id = localStorage.getItem(PROFILE_KEY);
    } catch (_) {}
    if (id === null && wsState && wsState.profilePick) id = wsState.profilePick[foldDevice()];
    id = id || "";
    var known = profState.profiles.some(function (p) {
      return p.id === id;
    });
    return known || !profState.primary ? id : "";
  }

  function profileLabel(id) {
    if (!id) return profState.primary || "Principal";
    for (var i = 0; i < profState.profiles.length; i++) {
      if (profState.profiles[i].id === id) return profState.profiles[i].label;
    }
    return id;
  }

  function takeProfiles(payload) {
    if (!payload || !Array.isArray(payload.profiles)) return payload;
    profState = { primary: payload.primary || "", profiles: payload.profiles };
    renderProfiles();
    return payload;
  }

  function loadProfiles() {
    return fetch("/rc-profiles", { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(takeProfiles)
      .catch(function () {
        return null;
      });
  }

  function postProfiles(op) {
    return fetch("/rc-profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(op),
      cache: "no-store",
    })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(takeProfiles)
      .catch(function () {
        return null;
      });
  }

  // Tell the server this device is the one typing, so a CLI started now
  // runs with this device's profile. Cheap; at most once a second unless forced.
  function pingProfile(force) {
    if (!profState.profiles.length && !force) return;
    var id = myProfile();
    var now = Date.now();
    if (!force && profPinged.id === id && now - profPinged.at < 1000) return;
    profPinged = { at: now, id: id };
    try {
      fetch("/rc-profile-ping", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: id }),
        cache: "no-store",
        keepalive: true,
      }).catch(function () {});
    } catch (_) {}
  }

  function pickProfile(id) {
    try {
      localStorage.setItem(PROFILE_KEY, id);
    } catch (_) {}
    wsPost({ op: "profilePick", device: foldDevice(), profile: id });
    pingProfile(true);
    renderProfiles();
    lookDraft = null;
    refreshLook();
    showToast("Perfil: " + profileLabel(id));
  }

  // Each profile shows its own accent: the one of the background it picked in
  // Themes (color, gradient or image). Default background: the usual teal.
  function profileAccent(pid) {
    var look = wsState && wsState.looks ? wsState.looks[pid] : null;
    if (!look || !look.kind || look.kind === "default") return null;
    var raw = "";
    if (look.kind === "color") raw = look.value;
    else if (look.kind === "preset") {
      var p = presetOf(look.value);
      raw = p ? p[4] : "";
    } else if (look.kind === "image") {
      var t = imageTint(look.value);
      raw = t ? t.accent : "";
    }
    if (!raw) return null;
    var hsl = rgbHsl(hexRgb(raw));
    var sat = hsl[1] < 0.12 ? hsl[1] : Math.max(0.55, hsl[1]);
    var light = themeMode() === "light";
    return {
      dot: rgbHex(hslRgb(hsl[0], sat, light ? 0.45 : 0.68)),
      text: rgbHex(hslRgb(hsl[0], sat, light ? 0.32 : 0.78)),
    };
  }

  function paintProfileLabel(dot, name, pid) {
    var a = profileAccent(pid);
    dot.style.background = a ? a.dot : "";
    dot.style.color = a ? (themeMode() === "light" ? "#fff" : "#111214") : "";
    name.style.color = a ? a.text : "";
  }

  function renderProfiles() {
    paintProfileChip();
    if (paneMarks.marks.length) placePaneMarks(xterm());
    var box = document.getElementById("rc-ws-profiles");
    if (!box) return;
    if (profEditing && document.activeElement && document.activeElement.id === "rc-ws-profile-name") return;
    var mine = myProfile();
    box.innerHTML = "";
    var items = [{ id: "", label: profState.primary || "Principal", primary: true }].concat(profState.profiles);
    items.forEach(function (p) {
      var row = document.createElement("div");
      row.className = "rc-ws-profile" + (p.id === mine ? " is-on" : "");
      var main = wsButton("", "rc-ws-profile-main", "Usar el perfil " + p.label, function () {
        pickProfile(p.id);
      });
      var dot = document.createElement("span");
      dot.className = "rc-ws-profile-dot";
      dot.textContent = (p.label || "?").charAt(0).toUpperCase();
      var name = document.createElement("span");
      name.className = "rc-ws-profile-name";
      name.textContent = p.label;
      paintProfileLabel(dot, name, p.id);
      main.appendChild(dot);
      main.appendChild(name);
      row.appendChild(main);
      if (!p.primary) {
        row.appendChild(
          wsButton("🗑", "rc-ws-act is-danger", "Borrar el perfil " + p.label, function () {
            confirmDeleteProfile(p);
          })
        );
      }
      box.appendChild(row);
    });
    if (profEditing) {
      var input = document.createElement("input");
      input.id = "rc-ws-profile-name";
      input.className = "rc-ws-input";
      input.type = "text";
      input.maxLength = 40;
      input.placeholder = "Nombre (ej. Luis)";
      var settled = false;
      var finish = function (save) {
        if (settled) return;
        settled = true;
        profEditing = false;
        var label = input.value.trim();
        if (save && label) {
          postProfiles({ op: "create", label: label }).then(function (payload) {
            if (payload && payload.ok === false && payload.error) showToast(payload.error);
            else if (payload && payload.id) showToast("Perfil «" + label + "» creado");
            renderProfiles();
          });
        } else setTimeout(renderProfiles, 0);
      };
      input.addEventListener("keydown", function (ev) {
        ev.stopPropagation();
        if (ev.key === "Enter") {
          ev.preventDefault();
          finish(true);
        } else if (ev.key === "Escape") {
          ev.preventDefault();
          finish(false);
        }
      });
      input.addEventListener("blur", function () {
        finish(true);
      });
      box.appendChild(input);
      setTimeout(function () {
        try {
          input.focus();
        } catch (_) {}
      }, 0);
    } else {
      box.appendChild(
        wsButton("+ Agregar perfil", "rc-ws-profile-add", "", function () {
          profEditing = true;
          renderProfiles();
        })
      );
    }
    var menu = document.getElementById("rc-ws-menu");
    if (menu) {
      menu.title = "Workspaces · perfil: " + profileLabel(mine);
    }
  }

  function confirmDeleteProfile(p) {
    if (autoBusy) return;
    var card = autoModalShell();
    card.querySelector(".rc-auto-title").textContent = "¿Borrar el perfil " + p.label + "?";
    var note = document.createElement("div");
    note.className = "rc-auto-note is-warn";
    note.textContent =
      "Se borrará su carpeta con sus sesiones y configuraciones de los CLIs (Claude, Cursor…). " +
      "Lo que ya está corriendo con ese perfil sigue abierto, pero tendrá que iniciar sesión otra vez si se vuelve a crear.";
    card.appendChild(note);
    var bar = autoActions(function (cancel, go) {
      cancel.disabled = true;
      go.disabled = true;
      postProfiles({ op: "delete", id: p.id }).then(function (payload) {
        if (myProfile() === "" || !profState.profiles.some(function (x) { return x.id === myProfile(); })) {
          try {
            if ((localStorage.getItem(PROFILE_KEY) || "") === p.id) localStorage.setItem(PROFILE_KEY, "");
          } catch (_) {}
        }
        closeAutoModal();
        showToast(payload && payload.ok === false ? payload.error || "No pude borrarlo" : "Perfil borrado");
        renderProfiles();
      });
    });
    bar.querySelector(".rc-auto-go").textContent = "Borrar perfil";
    card.appendChild(bar);
  }

  // Quick switch in the tab strip, right side.
  function buildProfileChip() {
    var chip = document.createElement("button");
    chip.id = "rc-profile-chip";
    chip.type = "button";
    chip.setAttribute("aria-haspopup", "menu");
    chip.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (document.getElementById("rc-profile-pop")) closeProfilePop();
      else openProfilePop();
    });
    return chip;
  }

  function paintProfileChip() {
    var chip = document.getElementById("rc-profile-chip");
    if (!chip) return;
    var mine = myProfile();
    var label = profileLabel(mine);
    chip.innerHTML = "";
    var dot = document.createElement("span");
    dot.className = "rc-ws-profile-dot";
    dot.textContent = label.charAt(0).toUpperCase();
    var name = document.createElement("span");
    name.className = "rc-profile-chip-name";
    name.textContent = label;
    paintProfileLabel(dot, name, mine);
    chip.appendChild(dot);
    chip.appendChild(name);
    chip.classList.toggle("is-extra", !!mine);
    chip.title = "Perfil para los CLIs nuevos: " + label + (mine ? "" : " (principal)");
    chip.setAttribute("aria-label", chip.title);
  }

  function closeProfilePop() {
    var pop = document.getElementById("rc-profile-pop");
    if (pop) pop.remove();
    document.removeEventListener("pointerdown", profilePopOutside, true);
    document.removeEventListener("keydown", profilePopKey, true);
  }

  function profilePopOutside(ev) {
    var t = ev.target;
    if (t && t.closest && t.closest("#rc-profile-pop, #rc-profile-chip, .rc-pane-mark")) return;
    closeProfilePop();
  }

  function profilePopKey(ev) {
    if (ev.key !== "Escape") return;
    ev.preventDefault();
    ev.stopPropagation();
    closeProfilePop();
  }

  function openProfilePop() {
    closeProfilePop();
    var chip = document.getElementById("rc-profile-chip");
    if (!chip) return;
    var pop = document.createElement("div");
    pop.id = "rc-profile-pop";
    pop.setAttribute("role", "menu");
    var title = document.createElement("div");
    title.className = "rc-profile-pop-title";
    title.textContent = "Perfil";
    pop.appendChild(title);
    var mine = myProfile();
    [{ id: "", label: profState.primary || "Principal", primary: true }].concat(profState.profiles).forEach(function (p) {
      var b = wsButton("", "rc-profile-pop-item" + (p.id === mine ? " is-on" : ""), "Usar el perfil " + p.label, function () {
        closeProfilePop();
        if (p.id !== mine) pickProfile(p.id);
      });
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", p.id === mine ? "true" : "false");
      var dot = document.createElement("span");
      dot.className = "rc-ws-profile-dot";
      dot.textContent = (p.label || "?").charAt(0).toUpperCase();
      var name = document.createElement("span");
      name.className = "rc-ws-profile-name";
      name.textContent = p.label;
      paintProfileLabel(dot, name, p.id);
      b.appendChild(dot);
      b.appendChild(name);
      pop.appendChild(b);
    });
    var sep = document.createElement("div");
    sep.className = "rc-profile-pop-sep";
    pop.appendChild(sep);
    pop.appendChild(
      wsButton(profState.profiles.length ? "Administrar perfiles…" : "+ Agregar perfil…", "rc-profile-pop-manage", "", function () {
        closeProfilePop();
        profEditing = !profState.profiles.length;
        openDrawer();
        renderProfiles();
      })
    );
    (document.body || document.documentElement).appendChild(pop);
    var r = chip.getBoundingClientRect();
    pop.style.top = Math.round(r.bottom + 6) + "px";
    pop.style.left = Math.max(8, Math.round(r.left)) + "px";
    document.addEventListener("pointerdown", profilePopOutside, true);
    document.addEventListener("keydown", profilePopKey, true);
    loadProfiles();
  }

  function buildProfileBox() {
    var wrap = document.createElement("div");
    wrap.className = "rc-ws-theme-wrap rc-ws-profiles-wrap";
    var label = document.createElement("div");
    label.className = "rc-ws-title rc-ws-title-row";
    var text = document.createElement("span");
    text.textContent = "Perfil";
    label.appendChild(text);
    var gear = wsButton("", "rc-ws-act rc-ws-gear", "Ajustes del perfil", function () {
      openSettings("themes");
    });
    gear.innerHTML = GEAR_ICON;
    gear.title = "Ajustes del perfil (fondo del terminal)";
    label.appendChild(gear);
    var box = document.createElement("div");
    box.id = "rc-ws-profiles";
    wrap.appendChild(label);
    wrap.appendChild(box);
    return wrap;
  }

  function bootProfiles() {
    loadProfiles().then(function () {
      pingProfile(true);
    });
    document.addEventListener(
      "keydown",
      function (ev) {
        pingProfile(ev.key === "Enter");
      },
      true
    );
    document.addEventListener(
      "beforeinput",
      function (ev) {
        pingProfile(ev.inputType === "insertLineBreak");
      },
      true
    );
  }

  // --- theme ----------------------------------------------------------------
  // "system" follows the device; the choice is kept on the server so it
  // survives a new tunnel URL, and cached locally for the first paint.

  var THEME_KEY = "rc-theme";
  var TERM_THEMES = {
    dark: {
      background: "#111214",
      foreground: "#e4e6eb",
      cursor: "#80A4C2",
      cursorAccent: "#111214",
      selectionBackground: "#33363c",
      selectionInactiveBackground: "#24262a",
      black: "#1b1c1f",
      red: "#EF5350",
      green: "#22DA6E",
      yellow: "#ADDB67",
      blue: "#82AAFF",
      magenta: "#C792EA",
      cyan: "#21C7A8",
      white: "#FFFFFF",
      brightBlack: "#575656",
      brightRed: "#EF5350",
      brightGreen: "#22DA6E",
      brightYellow: "#FFEB95",
      brightBlue: "#82AAFF",
      brightMagenta: "#C792EA",
      brightCyan: "#7FDBCA",
      brightWhite: "#FFFFFF",
    },
    light: {
      background: "#FBFBFB",
      foreground: "#403F53",
      cursor: "#90A7B2",
      cursorAccent: "#FBFBFB",
      selectionBackground: "#CCD8E6",
      selectionInactiveBackground: "#E0E7EF",
      black: "#403F53",
      red: "#DE3D3B",
      green: "#08916A",
      yellow: "#C08A00",
      blue: "#288ED7",
      magenta: "#D6438A",
      cyan: "#2AA298",
      white: "#C8CDD5",
      brightBlack: "#7A8181",
      brightRed: "#DE3D3B",
      brightGreen: "#08916A",
      brightYellow: "#B08300",
      brightBlue: "#288ED7",
      brightMagenta: "#D6438A",
      brightCyan: "#2AA298",
      brightWhite: "#F0F0F0",
    },
  };
  var THEME_ICONS = {
    system:
      '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 2.4a5.6 5.6 0 0 1 0 11.2z" fill="currentColor"/></svg>',
    light:
      '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="2.8" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.6v1.6M8 12.8v1.6M1.6 8h1.6M12.8 8h1.6M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M3.5 12.5l1.1-1.1M11.4 4.6l1.1-1.1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    dark: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  };
  var THEME_LABELS = [
    ["system", "Sistema"],
    ["light", "Claro"],
    ["dark", "Oscuro"],
  ];
  var themePref = readThemePref();
  var lightQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

  function readThemePref() {
    try {
      var v = localStorage.getItem(THEME_KEY);
      if (v === "light" || v === "dark" || v === "system") return v;
    } catch (_) {}
    return "system";
  }

  function themeMode() {
    if (themePref === "light" || themePref === "dark") return themePref;
    return lightQuery && lightQuery.matches ? "light" : "dark";
  }

  // CLIs (Claude Code and others) pick colors for dark terminals; on a light
  // background xterm darkens any text below WCAG AA contrast.
  function contrastFloor() {
    return themeMode() === "light" || lookSeeThrough() ? 4.5 : 1;
  }

  // Over a picture the terminal keeps a little opacity: at ~1% WebGL smooths
  // glyph edges against nothing and dark text comes out thin; from ~5% the
  // strokes are normal. The CSS veil is lowered by the same amount.
  var TERM_VEIL_A = 0.08;

  function termPalette() {
    var look = myLook();
    if (look.kind === "color") {
      var bg = colorFor(look.value);
      return Object.assign({}, TERM_THEMES[themeMode()], { background: bg, cursorAccent: bg });
    }
    if (look.kind === "preset" || look.kind === "image") {
      // The picture shows through; the veil (dim) matches the mode, so the
      // mode's own palette stays readable on top of it.
      // Not fully clear: xterm checks text contrast against this color, and a
      // little opacity keeps WebGL glyphs at their normal weight (TERM_VEIL_A).
      var veil = "rgba(" + behindText().join(",") + "," + TERM_VEIL_A + ")";
      return Object.assign({}, TERM_THEMES[themeMode()], { background: veil });
    }
    return TERM_THEMES[themeMode()];
  }

  // ttyd re-sends its own (dark) theme on every connect; keep ours.
  // Apps ask the terminal for its colors (OSC 10/11/4) and whether it is
  // dark (CSI ? 996 n). herdr asks on every resize or focus and keeps ONE
  // palette for all its clients, so a light device and a dark one answering
  // in turns repainted everyone's inverse cells (nano's bars) back and forth.
  // Every device answers the same: the dark palette.
  var REPORT_ORDER = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white", "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue", "brightMagenta", "brightCyan", "brightWhite"];

  function reportColor(hex) {
    var h = String(hex || "#000000").replace("#", "");
    return "rgb:" + [0, 2, 4].map(function (i) {
      var byte = h.substr(i, 2);
      return byte + byte;
    }).join("/");
  }

  function lockColorReports(term) {
    var parser = term && term.parser;
    if (!parser || term.__rcColorReports || typeof parser.registerOscHandler !== "function") return;
    term.__rcColorReports = true;
    var dark = TERM_THEMES.dark;
    function reply(text) {
      try {
        if (typeof term.input === "function") term.input(text, false);
        else term._core.coreService.triggerDataEvent(text, false);
      } catch (_) {}
    }
    function fixed(code, hex) {
      parser.registerOscHandler(code, function (data) {
        if (data !== "?") return false;
        reply("\x1b]" + code + ";" + reportColor(hex) + "\x1b\\");
        return true;
      });
    }
    fixed(10, dark.foreground);
    fixed(11, dark.background);
    fixed(12, dark.cursor);
    parser.registerOscHandler(4, function (data) {
      var parts = String(data).split(";");
      if (parts.indexOf("?") === -1) return false;
      for (var i = 0; i + 1 < parts.length; i += 2) {
        var n = parseInt(parts[i], 10);
        if (parts[i + 1] !== "?" || !(n >= 0 && n < 16)) continue;
        reply("\x1b]4;" + n + ";" + reportColor(dark[REPORT_ORDER[n]]) + "\x1b\\");
      }
      return true;
    });
    if (typeof parser.registerCsiHandler === "function") {
      parser.registerCsiHandler({ prefix: "?", final: "n" }, function (params) {
        if (params[0] !== 996) return false;
        reply("\x1b[?997;1n");
        return true;
      });
    }
  }

  function lockTheme(term) {
    lockColorReports(term);
    if (!term || !term.options || term.__rcTheme) return;
    term.__rcTheme = true;
    var opts = term.options;
    var desc = Object.getOwnPropertyDescriptor(opts, "theme");
    if (desc && desc.set && desc.configurable) {
      try {
        Object.defineProperty(opts, "theme", {
          configurable: true,
          enumerable: desc.enumerable,
          get: desc.get,
          set: function () {
            desc.set.call(opts, termPalette());
            // ttyd re-sends its preferences (renderer included) on reconnect.
            setTimeout(function () {
              syncRenderer(term);
            }, 50);
          },
        });
      } catch (_) {}
    }
    try {
      opts.allowTransparency = lookSeeThrough();
      opts.minimumContrastRatio = contrastFloor();
      opts.theme = termPalette();
    } catch (_) {}
  }

  function applyTheme() {
    var mode = themeMode();
    var root = document.documentElement;
    root.dataset.rcTheme = mode;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      (document.head || root).appendChild(meta);
    }
    var ui = paintUiTint();
    meta.content = ui ? ui.surface : mode === "light" ? "#eef1f5" : "#1a1b1e";
    var terms = [];
    Object.keys(panes).forEach(function (key) {
      if (panes[key] && panes[key].term) terms.push(panes[key].term);
    });
    if (window.term) terms.push(window.term);
    var clear = lookSeeThrough();
    var floor = contrastFloor();
    terms.forEach(function (term) {
      try {
        if (term.options.allowTransparency !== clear) term.options.allowTransparency = clear;
        if (term.options.minimumContrastRatio !== floor) term.options.minimumContrastRatio = floor;
      } catch (_) {}
      if (!term.__rcTheme) lockTheme(term);
      else {
        try {
          term.options.theme = termPalette();
        } catch (_) {}
      }
      syncRenderer(term);
    });
    paintLookLayer();
    paintThemeSwitch();
    // profile accents depend on the mode and on image colors read later
    if (typeof renderProfiles === "function" && profState) renderProfiles();
    if (document.getElementById("rc-settings")) paintSettings();
  }

  function setThemePref(value, fromServer) {
    if (value !== "light" && value !== "dark" && value !== "system") return;
    var changed = value !== themePref;
    themePref = value;
    try {
      localStorage.setItem(THEME_KEY, value);
    } catch (_) {}
    if (changed) applyTheme();
    if (!fromServer) wsPost({ op: "theme", theme: value, device: foldDevice() });
  }

  // --- terminal background per profile (Ajustes › Themes) ----------------------

  var LOOK_DEFAULT = { kind: "default", value: "", dim: 35, blur: 0, adaptive: false };
  var LOOK_COLORS = [
    ["#011627", "Night Owl"],
    ["#000000", "Negro"],
    ["#1e1e1e", "Grafito"],
    ["#0d1b3e", "Azul noche"],
    ["#1f1235", "Morado"],
    ["#0f2a1d", "Bosque"],
    ["#2b0f17", "Vino"],
    ["#2a1f14", "Café"],
    ["#f4efe6", "Hueso"],
    ["#e9edf2", "Gris claro"],
  ];
  // [id, name, dark css, tint for the UI surfaces, accent, light css]
  var LOOK_PRESETS = [
    ["aurora", "Aurora", "radial-gradient(120% 90% at 10% 0%, #0f5c63 0%, transparent 55%), radial-gradient(90% 80% at 100% 100%, #3a2a7a 0%, transparent 60%), linear-gradient(160deg, #04121f, #0a1630)", "#0b2a36", "#5fdccd", "radial-gradient(120% 90% at 10% 0%, #7fd6ca 0%, transparent 60%), radial-gradient(90% 80% at 100% 100%, #b4a8ef 0%, transparent 62%), linear-gradient(160deg, #c9ece6, #d3d8f4)"],
    ["sunset", "Atardecer", "radial-gradient(100% 80% at 80% 10%, #a1452e 0%, transparent 55%), radial-gradient(90% 90% at 0% 100%, #5a1e5c 0%, transparent 60%), linear-gradient(170deg, #1a0d1f, #2a0f1a)", "#2a1220", "#ff9e6e", "radial-gradient(100% 80% at 80% 10%, #ffb48a 0%, transparent 58%), radial-gradient(90% 90% at 0% 100%, #e3a6e3 0%, transparent 62%), linear-gradient(170deg, #f8dccf, #f1d3e2)"],
    ["ocean", "Océano", "radial-gradient(100% 70% at 50% 0%, #1b5f9e 0%, transparent 60%), linear-gradient(180deg, #06223f, #020c18)", "#082039", "#72b6ff", "radial-gradient(100% 70% at 50% 0%, #8fc3ff 0%, transparent 62%), linear-gradient(180deg, #cfe3fa, #bfd6f2)"],
    ["forest", "Bosque", "radial-gradient(90% 70% at 20% 10%, #1f6b46 0%, transparent 55%), linear-gradient(165deg, #06180f, #0b2418)", "#0c2517", "#6fdc9f", "radial-gradient(90% 70% at 20% 10%, #92d9ad 0%, transparent 58%), linear-gradient(165deg, #d2ecdb, #c4e2cf)"],
    ["nebula", "Nebulosa", "radial-gradient(60% 50% at 30% 30%, #7a2d9c 0%, transparent 60%), radial-gradient(50% 50% at 75% 70%, #1d6fa3 0%, transparent 60%), linear-gradient(135deg, #0a0614, #120a24)", "#1b1030", "#c191ff", "radial-gradient(60% 50% at 30% 30%, #cfa6f2 0%, transparent 62%), radial-gradient(50% 50% at 75% 70%, #9ccdf0 0%, transparent 62%), linear-gradient(135deg, #e6dcf6, #d8d4f2)"],
    ["graphite", "Grafito", "radial-gradient(80% 60% at 50% 0%, #3b4250 0%, transparent 60%), linear-gradient(180deg, #16181d, #0c0d10)", "#181a20", "#a8b5cc", "radial-gradient(80% 60% at 50% 0%, #bcc4d2 0%, transparent 62%), linear-gradient(180deg, #dde1e8, #d0d5de)"],
    ["rose", "Rosa", "radial-gradient(90% 70% at 85% 15%, #9c3a63 0%, transparent 55%), linear-gradient(160deg, #1d0d17, #12091a)", "#26101c", "#ff8fbb", "radial-gradient(90% 70% at 85% 15%, #f3a2c3 0%, transparent 58%), linear-gradient(160deg, #f6d6e3, #eccfe0)"],
    ["mint", "Menta", "radial-gradient(90% 70% at 15% 85%, #1f8a74 0%, transparent 55%), radial-gradient(70% 60% at 90% 10%, #2b6f8f 0%, transparent 60%), linear-gradient(150deg, #04161a, #071e22)", "#07211f", "#6fefd2", "radial-gradient(90% 70% at 15% 85%, #8fe6cf 0%, transparent 58%), radial-gradient(70% 60% at 90% 10%, #9fcfe8 0%, transparent 62%), linear-gradient(150deg, #cdf0e7, #c8e5ea)"],
  ];
  var lookDraft = null; // what the modal shows before the server confirms it

  function presetOf(id) {
    for (var i = 0; i < LOOK_PRESETS.length; i++) if (LOOK_PRESETS[i][0] === id) return LOOK_PRESETS[i];
    return null;
  }

  function presetCss(id) {
    var p = presetOf(id);
    if (!p) return "";
    return themeMode() === "light" ? p[5] : p[2];
  }

  // A swatch is a hue: dark mode gets a deep version of it, light mode a pale one.
  // Light mode never means white: any hue becomes a real pastel of itself
  // (greys stay a soft neutral grey).
  function pastel(hex) {
    var hsl = rgbHsl(hexRgb(hex));
    if (hsl[1] < 0.08) return rgbHex(hslRgb(hsl[0], 0.05, 0.87));
    return rgbHex(hslRgb(hsl[0], Math.max(0.5, Math.min(hsl[1], 0.75)), 0.79));
  }

  function colorFor(hex, mode) {
    var hsl = rgbHsl(hexRgb(hex));
    if ((mode || themeMode()) === "light") return pastel(hex);
    if (hsl[2] <= 0.16) return hex;
    return rgbHex(hslRgb(hsl[0], Math.min(hsl[1], 0.6), hsl[1] < 0.08 ? 0.1 : 0.12));
  }

  function hexLuma(hex) {
    var m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
    if (!m) return 0;
    var c = [m[1], m[2], m[3]].map(function (h) {
      var v = parseInt(h, 16) / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  function hexRgb(hex) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [0, 0, 0];
  }

  function rgbHsl(rgb) {
    var r = rgb[0] / 255;
    var g = rgb[1] / 255;
    var b = rgb[2] / 255;
    var max = Math.max(r, g, b);
    var min = Math.min(r, g, b);
    var l = (max + min) / 2;
    var h = 0;
    var sat = 0;
    if (max !== min) {
      var d = max - min;
      sat = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h /= 6;
    }
    return [h, sat, l];
  }

  function hslRgb(h, sat, l) {
    function f(n) {
      var k = (n + h * 12) % 12;
      var a = sat * Math.min(l, 1 - l);
      return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
    }
    return [f(0), f(8), f(4)];
  }

  function rgbHex(rgb) {
    return "#" + rgb.map(function (v) {
      return ("0" + Math.max(0, Math.min(255, v)).toString(16)).slice(-2);
    }).join("");
  }

  // Average color of an uploaded image, for tinting the UI (same origin, so the
  // canvas can be read). Cached by name; applyTheme runs again once it is known.
  var imageTints = {};

  function imageTint(name) {
    if (imageTints[name] !== undefined) return imageTints[name];
    imageTints[name] = null;
    var img = new Image();
    img.onload = function () {
      try {
        var c = document.createElement("canvas");
        c.width = c.height = 48;
        var ctx = c.getContext("2d");
        ctx.drawImage(img, 0, 0, 48, 48);
        var px = ctx.getImageData(0, 0, 48, 48).data;
        // Hue buckets (30 deg each), weighted by how vivid each pixel is: the
        // heaviest one tints the surfaces, the next distinct one is the second
        // accent. The plain average is kept for the contrast check.
        var sum = [0, 0, 0];
        var buckets = {};
        for (var i = 0; i < px.length; i += 4) {
          var rgb = [px[i], px[i + 1], px[i + 2]];
          sum[0] += rgb[0];
          sum[1] += rgb[1];
          sum[2] += rgb[2];
          var hsl = rgbHsl(rgb);
          if (hsl[1] < 0.12 || hsl[2] < 0.08 || hsl[2] > 0.95) continue;
          var key = Math.floor(hsl[0] * 12) % 12;
          var w = hsl[1] * (1 - Math.abs(hsl[2] - 0.5));
          var e = buckets[key] || (buckets[key] = { w: 0, r: 0, g: 0, b: 0, key: key });
          e.w += w;
          e.r += rgb[0] * w;
          e.g += rgb[1] * w;
          e.b += rgb[2] * w;
        }
        var n = px.length / 4;
        var avg = rgbHex([sum[0] / n, sum[1] / n, sum[2] / n].map(Math.round));
        var ranked = Object.keys(buckets)
          .map(function (k) {
            return buckets[k];
          })
          .sort(function (x, y) {
            return y.w - x.w;
          });
        var color = function (e) {
          return rgbHex([e.r / e.w, e.g / e.w, e.b / e.w].map(Math.round));
        };
        if (!ranked.length) {
          imageTints[name] = { avg: avg, tint: avg, accent: avg };
        } else {
          var main = ranked[0];
          var second = ranked.filter(function (e) {
            var gap = Math.abs(e.key - main.key);
            return Math.min(gap, 12 - gap) >= 1 && e.w > main.w * 0.15;
          })[0];
          imageTints[name] = { avg: avg, tint: color(main), accent: color(main), accent2: second ? color(second) : "" };
        }
      } catch (_) {
        imageTints[name] = false;
      }
      applyTheme();
    };
    img.onerror = function () {
      imageTints[name] = false;
    };
    img.src = "/rc-bg/" + name;
    return null;
  }

  // The look's colors {tint, accent, light}, or null for the default look.
  // The veil and the contrast check always use them.
  function lookColors() {
    var look = myLook();
    var raw = null;
    if (look.kind === "color") raw = { tint: colorFor(look.value), accent: look.value };
    else if (look.kind === "preset") {
      var p = presetOf(look.value);
      if (p) raw = { tint: p[3], accent: p[4] };
    } else if (look.kind === "image") raw = imageTint(look.value) || null;
    if (!raw) return null;
    var light = themeMode() === "light";
    return { tint: raw.tint, accent: raw.accent, accent2: raw.accent2 || "", light: light, kind: look.kind };
  }

  var UI_TINT_KEYS = ["--rc-text", "--rc-text-rgb", "--rc-muted", "--rc-shade", "--rc-bg", "--rc-surface", "--rc-raised", "--rc-surface-rgb", "--rc-blue", "--rc-blue-rgb", "--rc-teal", "--rc-teal-rgb", "--rc-on", "--rc-on-rgb"];

  // The accent (marks, active tab, buttons, chips) always follows the chosen
  // color or image. "Color adaptativo" also tints the surfaces (bars, panels).
  function paintUiTint() {
    var style = document.documentElement.style;
    var t = lookColors();
    var surfaces = !!(t && myLook().adaptive);
    if (!t) {
      UI_TINT_KEYS.forEach(function (k) {
        style.removeProperty(k);
      });
      return null;
    }
    var base = rgbHsl(hexRgb(t.tint));
    var hue = base[0];
    var sat = base[1];
    var acc = rgbHsl(hexRgb(t.accent));
    var acc2Hue = t.accent2 ? rgbHsl(hexRgb(t.accent2))[0] : (acc[0] + 0.06) % 1;
    var surf;
    var accent;
    var accent2;
    if (t.light) {
      // Same pastel family as the light veil (s 0.5-0.75), so the bars and
      // panels carry real color instead of near-white.
      var ls = sat < 0.08 ? 0.08 : Math.max(0.6, Math.min(sat, 0.85));
      surf = [hslRgb(hue, ls, 0.76), hslRgb(hue, ls, 0.8), hslRgb(hue, ls, 0.7)];
      var as = acc[1] < 0.12 ? 0 : Math.max(0.5, acc[1]);
      accent = as ? hslRgb(acc[0], as, 0.42) : [47, 127, 208];
      accent2 = as ? hslRgb(acc2Hue, as, 0.36) : [42, 162, 152];
    } else {
      var ds = Math.min(sat, 0.55);
      surf = [hslRgb(hue, ds, 0.075), hslRgb(hue, ds, 0.13), hslRgb(hue, ds, 0.2)];
      var das = acc[1] < 0.12 ? 0 : Math.max(0.55, acc[1]);
      accent = das ? hslRgb(acc[0], das, 0.7) : [130, 170, 255];
      accent2 = das ? hslRgb(acc2Hue, das, 0.72) : [127, 219, 202];
    }
    var set = function (k, v) {
      style.setProperty(k, v);
    };
    set("--rc-blue", rgbHex(accent));
    set("--rc-blue-rgb", accent.join(" "));
    set("--rc-teal", rgbHex(accent2));
    set("--rc-teal-rgb", accent2.join(" "));
    set("--rc-on", rgbHex(accent2));
    set("--rc-on-rgb", accent2.join(" "));
    if (!surfaces) {
      ["--rc-text", "--rc-text-rgb", "--rc-muted", "--rc-shade", "--rc-bg", "--rc-surface", "--rc-raised", "--rc-surface-rgb"].forEach(function (k) {
        style.removeProperty(k);
      });
      return null;
    }
    // A solid color is the terminal itself: the area around it must match exactly.
    set("--rc-bg", t.kind === "color" ? t.tint : rgbHex(surf[0]));
    // Backdrops and shadows (modal, drawer, cards) darken with the look's own
    // hue instead of Night Owl navy.
    var shade = t.light ? hslRgb(hue, Math.min(sat, 0.35), 0.3) : hslRgb(hue, sat < 0.08 ? 0.06 : Math.max(0.3, Math.min(sat, 0.6)), 0.05);
    set("--rc-shade", shade.join(" "));
    // Light mode text: deep shades of the same hue, so labels read clearly on
    // the pastel instead of a washed-out blue-grey.
    if (t.light) {
      var ink = hslRgb(hue, Math.min(sat, 0.5), 0.14);
      set("--rc-text", rgbHex(ink));
      set("--rc-text-rgb", ink.join(" "));
      set("--rc-muted", rgbHex(hslRgb(hue, Math.min(sat, 0.4), 0.3)));
    } else {
      style.removeProperty("--rc-text");
      style.removeProperty("--rc-text-rgb");
      style.removeProperty("--rc-muted");
    }
    set("--rc-surface", rgbHex(surf[1]));
    set("--rc-raised", rgbHex(surf[2]));
    set("--rc-surface-rgb", surf[1].join(" "));
    return { surface: rgbHex(surf[1]) };
  }

  function myLook() {
    var look = lookDraft;
    if (!look && wsState && wsState.looks) {
      try {
        look = wsState.looks[myProfile()];
      } catch (_) {}
    }
    return look && look.kind ? look : LOOK_DEFAULT;
  }

  function lookSeeThrough() {
    var kind = myLook().kind;
    return kind === "preset" || kind === "image";
  }

  // The veil over a gradient/photo: deep navy in dark mode, a pastel of the
  // look's own hue in light mode.
  function veilRgb() {
    // With "Color adaptativo" off the veil is plain white / black; on, it takes
    // the hue of the color or image.
    var t = myLook().adaptive ? lookColors() : null;
    if (themeMode() === "light") {
      if (!t) return [248, 249, 251];
      // Light mode: a pastel with real color, not near-white.
      var lh = rgbHsl(hexRgb(t.tint));
      return hslRgb(lh[0], lh[1] < 0.08 ? 0.08 : Math.max(0.6, Math.min(lh[1], 0.85)), 0.74);
    }
    if (!t) return [17, 18, 20];
    // Dark mode: a near-black of the look's own hue, not Night Owl navy.
    var hsl = rgbHsl(hexRgb(t.tint));
    return hslRgb(hsl[0], hsl[1] < 0.08 ? 0.06 : Math.max(0.3, Math.min(hsl[1], 0.6)), 0.06);
  }

  // Light mode puts dark text over a photo: its pale veil starts at 50%. The
  // light gradients are already pastel and keep the slider as is.
  function veilAlpha(look) {
    return (look.dim || 0) / 100;
  }

  // Roughly what sits behind the text: the picture's average under the veil.
  // xterm's contrast check runs against this color.
  function behindText() {
    var look = myLook();
    var veil = veilRgb();
    var a = veilAlpha(look);
    var under = null;
    if (look.kind === "image") {
      var t = imageTints[look.value];
      if (t && t.avg) under = hexRgb(t.avg);
    } else if (look.kind === "preset") {
      var p = presetOf(look.value);
      if (p) under = themeMode() === "light" ? hexRgb(pastel(p[3])) : hexRgb(p[3]);
    }
    if (!under) return veil;
    return [0, 1, 2].map(function (i) {
      return Math.round(under[i] * (1 - a) + veil[i] * a);
    });
  }

  function paintLookLayer() {
    var look = myLook();
    var root = document.documentElement;
    var layer = document.getElementById("rc-term-bg");
    if (!lookSeeThrough()) {
      root.classList.remove("rc-has-bg");
      if (layer) layer.hidden = true;
      return;
    }
    if (!layer) {
      layer = document.createElement("div");
      layer.id = "rc-term-bg";
      layer.setAttribute("aria-hidden", "true");
      layer.appendChild(document.createElement("div")).className = "rc-term-bg-img";
      var body = document.body || root;
      body.insertBefore(layer, body.firstChild);
    }
    var img = layer.firstChild;
    var css = look.kind === "preset" ? presetCss(look.value) : 'url("/rc-bg/' + look.value + '") center / cover no-repeat';
    if (img.dataset.src !== css) {
      img.dataset.src = css;
      img.style.background = css;
    }
    img.style.filter = look.blur ? "blur(" + look.blur + "px)" : "";
    img.style.transform = look.blur ? "scale(1.06)" : "";
    // The terminal already paints TERM_VEIL_A of the veil; the layer adds the rest.
    layer.style.setProperty("--rc-bg-dim", String(Math.max(0, 1 - (1 - veilAlpha(look)) / (1 - TERM_VEIL_A))));
    root.style.setProperty("--rc-veil-rgb", veilRgb().join(" "));
    layer.hidden = false;
    root.classList.add("rc-has-bg");
  }

  var lookSaveTimer = 0;

  function setLook(next, now, fromSlider) {
    lookDraft = Object.assign({}, LOOK_DEFAULT, myLook(), next);
    if (lookDraft.kind === "default") lookDraft.value = "";
    lookSig = JSON.stringify(lookDraft) + "|" + myProfile();
    // Sliders: the terminal's contrast check depends on the veil too.
    if (fromSlider) applyTheme();
    else {
      applyTheme();
      paintSettings(true);
    }
    clearTimeout(lookSaveTimer);
    var pid = myProfile();
    var look = lookDraft;
    lookSaveTimer = setTimeout(
      function () {
        wsPost({ op: "look", profile: pid, look: look }).then(function () {
          if (wsState) {
            wsState.looks = wsState.looks || {};
            wsState.looks[pid] = look;
          }
          if (lookDraft === look) lookDraft = null;
        });
      },
      now ? 0 : 300
    );
  }

  var lookSig = "";

  // Called when the workspace payload arrives or the profile changes.
  function refreshLook() {
    var sig = JSON.stringify(myLook()) + "|" + myProfile();
    if (sig === lookSig) return;
    lookSig = sig;
    applyTheme();
    paintSettings();
  }

  // --- settings modal --------------------------------------------------------

  var SETTINGS_TABS = [
    {
      id: "themes",
      label: "Themes",
      icon: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8a6.2 6.2 0 1 0 0 12.4c.9 0 1.3-.5 1.3-1.1 0-.7-.6-1-.6-1.7 0-.6.5-1.1 1.2-1.1h1.5a3.4 3.4 0 0 0 3.4-3.4C14.8 4.1 11.8 1.8 8 1.8z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><circle cx="4.8" cy="7.4" r="1" fill="currentColor"/><circle cx="7.2" cy="4.7" r="1" fill="currentColor"/><circle cx="10.6" cy="5.4" r="1" fill="currentColor"/></svg>',
    },
  ];
  var settingsTab = "themes";
  var bgImages = [];
  var GEAR_ICON =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.6 1.8h2.8l.4 1.8 1.2.7 1.8-.6 1.4 2.4-1.4 1.3v1.2l1.4 1.3-1.4 2.4-1.8-.6-1.2.7-.4 1.8H6.6l-.4-1.8-1.2-.7-1.8.6-1.4-2.4 1.4-1.3V7.4L1.8 6.1l1.4-2.4 1.8.6 1.2-.7z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';
  var CHECK_ICON =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.4l3 3 6-6.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function closeSettings() {
    var layer = document.getElementById("rc-settings");
    if (layer) layer.remove();
    document.documentElement.classList.remove("rc-settings-open");
  }

  function loadBgImages() {
    return fetch("/rc-bg", { cache: "no-store" })
      .then(function (resp) {
        return resp.ok ? resp.json() : null;
      })
      .then(function (payload) {
        if (payload && Array.isArray(payload.images)) bgImages = payload.images;
        paintSettings();
      })
      .catch(function () {});
  }

  function uploadBg(file) {
    if (!file) return;
    if (!/^image\//.test(file.type || "")) {
      showToast("Eso no es una imagen");
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      showToast("La imagen pasa de 15 MB");
      return;
    }
    showToast("Subiendo imagen…");
    fetch("/rc-bg", { method: "PUT", body: file, cache: "no-store" })
      .then(function (resp) {
        return resp.json().catch(function () {
          return {};
        });
      })
      .then(function (payload) {
        if (!payload || !payload.ok) {
          showToast((payload && payload.error) || "No pude subir la imagen");
          return;
        }
        bgImages = payload.images || bgImages;
        setLook({ kind: "image", value: payload.name }, true);
        showToast("Imagen lista");
      })
      .catch(function () {
        showToast("No pude subir la imagen");
      });
  }

  function deleteBg(name) {
    fetch("/rc-bg/" + encodeURIComponent(name), { method: "DELETE", cache: "no-store" })
      .then(function (resp) {
        return resp.json().catch(function () {
          return {};
        });
      })
      .then(function (payload) {
        if (payload && Array.isArray(payload.images)) bgImages = payload.images;
        var look = myLook();
        if (look.kind === "image" && look.value === name) {
          lookDraft = null;
          if (wsState && wsState.looks) wsState.looks[myProfile()] = Object.assign({}, look, { kind: "default", value: "" });
        }
        pullSessions();
        refreshLook();
        paintSettings();
      });
  }

  function lookTile(cls, label, on, onPick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "rc-look-tile " + cls + (on ? " is-on" : "");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.title = label;
    b.setAttribute("aria-label", label);
    var mark = document.createElement("span");
    mark.className = "rc-look-check";
    mark.innerHTML = CHECK_ICON;
    b.appendChild(mark);
    b.addEventListener("click", function (ev) {
      ev.preventDefault();
      onPick();
    });
    return b;
  }

  function lookGroup(title, hint) {
    var sec = document.createElement("section");
    sec.className = "rc-set-group";
    var h = document.createElement("div");
    h.className = "rc-set-group-title";
    h.textContent = title;
    sec.appendChild(h);
    if (hint) {
      var p = document.createElement("div");
      p.className = "rc-set-hint";
      p.textContent = hint;
      sec.appendChild(p);
    }
    return sec;
  }

  function lookBackgroundCss(look) {
    if (look.kind === "color") return colorFor(look.value);
    if (look.kind === "preset") return presetCss(look.value);
    if (look.kind === "image") return 'url("/rc-bg/' + look.value + '") center / cover no-repeat';
    return TERM_THEMES[themeMode()].background;
  }

  // A small fake terminal with the chosen background and palette.
  function lookHero(look) {
    var pal = termPalette();
    var hero = document.createElement("div");
    hero.className = "rc-look-hero";
    var bg = document.createElement("div");
    bg.className = "rc-look-hero-bg";
    bg.style.background = lookBackgroundCss(look);
    var see = look.kind === "preset" || look.kind === "image";
    if (see && look.blur) {
      bg.style.filter = "blur(" + Math.round(look.blur / 2) + "px)";
      bg.style.transform = "scale(1.08)";
    }
    hero.appendChild(bg);
    if (see) {
      var veil = document.createElement("div");
      veil.className = "rc-look-hero-veil";
      veil.style.opacity = String(veilAlpha(look));
      veil.style.background = "rgb(" + veilRgb().join(" ") + ")";
      hero.appendChild(veil);
    }
    var win = document.createElement("div");
    win.className = "rc-look-hero-win";
    var bar = document.createElement("div");
    bar.className = "rc-look-hero-bar";
    bar.innerHTML = "<i></i><i></i><i></i>";
    var name = document.createElement("span");
    name.textContent = "~ · zsh";
    bar.appendChild(name);
    win.appendChild(bar);
    var lines = [
      [[pal.green, "➜ "], [pal.cyan, "proyecto "], [pal.blue, "git:("], [pal.magenta, "main"], [pal.blue, ") "], [pal.foreground, "npm run dev"]],
      [[pal.brightBlack, "> vite --host"]],
      [[pal.green, "  ✓ "], [pal.foreground, "listo en "], [pal.yellow, "312 ms"]],
      [[pal.foreground, "  ➜ Local:   "], [pal.cyan, "http://localhost:5173/"]],
      [[pal.red, "  ✗ "], [pal.foreground, "1 advertencia en "], [pal.magenta, "src/app.tsx"]],
    ];
    var pre = document.createElement("div");
    pre.className = "rc-look-hero-text";
    lines.forEach(function (parts) {
      var line = document.createElement("div");
      parts.forEach(function (part) {
        var span = document.createElement("span");
        span.style.color = part[0];
        span.textContent = part[1];
        line.appendChild(span);
      });
      pre.appendChild(line);
    });
    var cursor = document.createElement("span");
    cursor.className = "rc-look-hero-cursor";
    cursor.style.background = pal.cursor;
    pre.lastChild.appendChild(cursor);
    win.appendChild(pre);
    hero.appendChild(win);
    var tag = document.createElement("span");
    tag.className = "rc-look-hero-tag";
    tag.textContent = look.kind === "default" ? "Tema" : look.kind === "color" ? "Color" : look.kind === "preset" ? "Degradado" : "Imagen";
    hero.appendChild(tag);
    return hero;
  }

  function lookCard(title, hint, icon) {
    var sec = document.createElement("section");
    sec.className = "rc-set-group";
    var h = document.createElement("div");
    h.className = "rc-set-group-head";
    if (icon) {
      var ic = document.createElement("span");
      ic.className = "rc-set-group-icon";
      ic.innerHTML = icon;
      h.appendChild(ic);
    }
    var txt = document.createElement("div");
    var t = document.createElement("div");
    t.className = "rc-set-group-title";
    t.textContent = title;
    txt.appendChild(t);
    if (hint) {
      var p = document.createElement("div");
      p.className = "rc-set-hint";
      p.textContent = hint;
      txt.appendChild(p);
    }
    h.appendChild(txt);
    sec.appendChild(h);
    return sec;
  }

  function swatch(label, on, paint, onPick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "rc-look-swatch" + (on ? " is-on" : "");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.title = label;
    var dot = document.createElement("span");
    dot.className = "rc-look-dot";
    paint(dot);
    var mark = document.createElement("span");
    mark.className = "rc-look-check";
    mark.innerHTML = CHECK_ICON;
    dot.appendChild(mark);
    var name = document.createElement("span");
    name.className = "rc-look-swatch-name";
    name.textContent = label;
    b.appendChild(dot);
    b.appendChild(name);
    b.addEventListener("click", function (ev) {
      ev.preventDefault();
      onPick();
    });
    return b;
  }

  var ICON_SWATCH =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="5.5" cy="6" r="2.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="10.5" cy="6" r="2.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="10.4" r="2.6" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
  var ICON_IMAGE =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="6" cy="6.6" r="1.2" fill="currentColor"/><path d="M2.6 12l3.6-3.4 2.6 2.4 1.8-1.6L13.4 12" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  var ICON_TUNE =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4.5h7M12.5 4.5h1M2.5 11.5h1M6.5 11.5h7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><circle cx="11" cy="4.5" r="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="5" cy="11.5" r="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';

  function renderThemesPane(box) {
    var look = myLook();
    var head = document.createElement("div");
    head.className = "rc-set-head";
    var t = document.createElement("div");
    t.className = "rc-set-title";
    t.textContent = "Fondo del terminal";
    head.appendChild(t);
    var adapt = document.createElement("button");
    adapt.type = "button";
    adapt.className = "rc-set-toggle" + (look.adaptive ? " is-on" : "");
    adapt.setAttribute("role", "switch");
    adapt.setAttribute("aria-checked", look.adaptive ? "true" : "false");
    adapt.title = "Teñir toda la interfaz con el color o la imagen";
    adapt.innerHTML = '<span class="rc-set-toggle-label">Color adaptativo</span><span class="rc-set-toggle-track"><span></span></span>';
    adapt.addEventListener("click", function () {
      setLook({ adaptive: !myLook().adaptive });
    });
    head.appendChild(adapt);
    box.appendChild(head);

    // Light / dark, the same choice as Tema in the drawer.
    var modes = document.createElement("div");
    modes.className = "rc-set-modes";
    modes.setAttribute("role", "group");
    modes.setAttribute("aria-label", "Modo");
    THEME_LABELS.forEach(function (item) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "rc-set-mode" + (themePref === item[0] ? " is-on" : "");
      b.setAttribute("aria-pressed", themePref === item[0] ? "true" : "false");
      b.innerHTML = THEME_ICONS[item[0]];
      b.appendChild(document.createElement("span")).textContent = item[1];
      b.addEventListener("click", function () {
        setThemePref(item[0]);
      });
      modes.appendChild(b);
    });
    box.appendChild(modes);
    box.appendChild(lookHero(look));

    // Colors
    var colors = lookCard("Colores", "", ICON_SWATCH);
    var row = document.createElement("div");
    row.className = "rc-look-swatches";
    row.appendChild(
      swatch("Tema", look.kind === "default", function (dot) {
        dot.classList.add("is-default");
      }, function () {
        setLook({ kind: "default", value: "" });
      })
    );
    LOOK_COLORS.forEach(function (c) {
      row.appendChild(
        swatch(c[1], look.kind === "color" && look.value === c[0], function (dot) {
          var shown = colorFor(c[0]);
          dot.style.background = shown;
          if (hexLuma(shown) > 0.55) dot.classList.add("is-light");
        }, function () {
          setLook({ kind: "color", value: c[0] });
        })
      );
    });
    var custom = look.kind === "color" && !LOOK_COLORS.some(function (c) {
      return c[0] === look.value;
    });
    var pick = swatch("Elegir…", custom, function (dot) {
      dot.classList.add("is-custom");
      if (custom) dot.style.background = colorFor(look.value);
    }, function () {});
    var input = document.createElement("input");
    input.type = "color";
    input.value = custom ? look.value : "#123456";
    input.setAttribute("aria-label", "Color personalizado");
    input.addEventListener("input", function () {
      setLook({ kind: "color", value: input.value.toLowerCase() });
    });
    pick.appendChild(input);
    row.appendChild(pick);
    colors.appendChild(row);
    box.appendChild(colors);

    // Images
    var images = lookCard("Imágenes", "", ICON_IMAGE);
    var igrid = document.createElement("div");
    igrid.className = "rc-look-grid is-images";
    var up = document.createElement("label");
    up.className = "rc-look-tile is-upload";
    up.title = "Subir imagen";
    up.innerHTML =
      '<span class="rc-look-up-icon"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 11V3.5M4.8 6.4L8 3.2l3.2 3.2M3 12.8h10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span>Subir imagen</span>';
    var file = document.createElement("input");
    file.type = "file";
    file.accept = "image/png,image/jpeg,image/webp,image/gif";
    file.addEventListener("change", function () {
      uploadBg(file.files && file.files[0]);
      file.value = "";
    });
    up.appendChild(file);
    igrid.appendChild(up);
    bgImages.forEach(function (name) {
      var tile = lookTile("is-image is-upload-img", "Imagen subida", look.kind === "image" && look.value === name, function () {
        setLook({ kind: "image", value: name });
      });
      var art = document.createElement("span");
      art.className = "rc-look-art";
      art.style.background = 'url("/rc-bg/' + name + '") center / cover no-repeat';
      tile.insertBefore(art, tile.firstChild);
      var del = document.createElement("span");
      del.className = "rc-look-del";
      del.setAttribute("role", "button");
      del.setAttribute("aria-label", "Borrar imagen");
      del.title = "Borrar imagen";
      del.innerHTML = WS_ICONS["🗑"];
      del.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        deleteBg(name);
      });
      tile.appendChild(del);
      igrid.appendChild(tile);
    });
    LOOK_PRESETS.forEach(function (p) {
      var tile = lookTile("is-image", p[1], look.kind === "preset" && look.value === p[0], function () {
        setLook({ kind: "preset", value: p[0] });
      });
      var art = document.createElement("span");
      art.className = "rc-look-art";
      art.style.background = presetCss(p[0]);
      tile.insertBefore(art, tile.firstChild);
      var name = document.createElement("span");
      name.className = "rc-look-name";
      var chip = document.createElement("i");
      chip.style.background = p[4];
      name.appendChild(chip);
      name.appendChild(document.createTextNode(p[1]));
      tile.appendChild(name);
      igrid.appendChild(tile);
    });
    ["dragenter", "dragover"].forEach(function (type) {
      images.addEventListener(type, function (ev) {
        ev.preventDefault();
        images.classList.add("is-drop");
      });
    });
    images.addEventListener("dragleave", function (ev) {
      if (!images.contains(ev.relatedTarget)) images.classList.remove("is-drop");
    });
    images.addEventListener("drop", function (ev) {
      ev.preventDefault();
      images.classList.remove("is-drop");
      var f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
      uploadBg(f);
    });
    images.appendChild(igrid);
    box.appendChild(images);

    // Readability
    var see = lookSeeThrough();
    var tune = lookCard("Legibilidad", "", ICON_TUNE);
    tune.classList.toggle("is-off", !see);
    [
      ["dim", "Oscurecer", 0, 85, "%"],
      ["blur", "Desenfocar", 0, 20, " px"],
    ].forEach(function (cfg) {
      var wrap = document.createElement("label");
      wrap.className = "rc-set-range";
      var label = document.createElement("span");
      label.textContent = cfg[1];
      var out = document.createElement("span");
      out.className = "rc-set-range-val";
      out.textContent = look[cfg[0]] + cfg[4];
      var range = document.createElement("input");
      range.type = "range";
      range.min = String(cfg[2]);
      range.max = String(cfg[3]);
      range.value = String(look[cfg[0]]);
      range.disabled = !see;
      var fill = function () {
        range.style.setProperty("--pct", ((range.value - cfg[2]) / (cfg[3] - cfg[2])) * 100 + "%");
      };
      fill();
      range.addEventListener("input", function () {
        out.textContent = range.value + cfg[4];
        fill();
        var next = {};
        next[cfg[0]] = parseInt(range.value, 10) || 0;
        setLook(next, false, true);
        var hero = box.querySelector(".rc-look-hero");
        if (hero) hero.replaceWith(lookHero(myLook()));
      });
      wrap.appendChild(label);
      wrap.appendChild(range);
      wrap.appendChild(out);
      tune.appendChild(wrap);
    });
    box.appendChild(tune);
  }

  function paintSettings(force) {
    var layer = document.getElementById("rc-settings");
    if (!layer) return;
    var main = layer.querySelector(".rc-set-body");
    // Keep a slider being dragged alive: only the tiles' state is repainted then.
    var active = document.activeElement;
    if (!force && active && main.contains(active) && active.type === "range") return;
    var keep = main.scrollTop;
    main.innerHTML = "";
    layer.querySelectorAll(".rc-set-tab").forEach(function (b) {
      b.classList.toggle("is-on", b.dataset.tab === settingsTab);
    });
    if (settingsTab === "themes") renderThemesPane(main);
    main.scrollTop = keep;
  }

  function openSettings(tab) {
    closeSettings();
    closeDrawer();
    if (tab) settingsTab = tab;
    var layer = document.createElement("div");
    layer.id = "rc-settings";
    layer.setAttribute("role", "dialog");
    layer.setAttribute("aria-modal", "true");
    layer.setAttribute("aria-label", "Ajustes");
    var card = document.createElement("div");
    card.className = "rc-set";
    var side = document.createElement("aside");
    side.className = "rc-set-side";
    var brand = document.createElement("div");
    brand.className = "rc-set-brand";
    brand.textContent = "Ajustes";
    side.appendChild(brand);
    var tabs = document.createElement("nav");
    tabs.className = "rc-set-tabs";
    SETTINGS_TABS.forEach(function (tab) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "rc-set-tab";
      b.dataset.tab = tab.id;
      b.innerHTML = tab.icon;
      b.appendChild(document.createElement("span")).textContent = tab.label;
      b.addEventListener("click", function () {
        settingsTab = tab.id;
        paintSettings(true);
      });
      tabs.appendChild(b);
    });
    side.appendChild(tabs);
    var me = document.createElement("div");
    me.className = "rc-set-me";
    var dot = document.createElement("span");
    dot.className = "rc-ws-profile-dot";
    dot.textContent = (profileLabel(myProfile()) || "?").charAt(0).toUpperCase();
    var meText = document.createElement("div");
    var meName = document.createElement("div");
    meName.className = "rc-set-me-name";
    meName.textContent = profileLabel(myProfile());
    var meSub = document.createElement("div");
    meSub.className = "rc-set-me-sub";
    meSub.textContent = "Perfil activo";
    meText.appendChild(meName);
    meText.appendChild(meSub);
    me.appendChild(dot);
    me.appendChild(meText);
    side.appendChild(me);
    var main = document.createElement("section");
    main.className = "rc-set-main";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "rc-set-close";
    close.setAttribute("aria-label", "Cerrar");
    close.innerHTML =
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.6 4.6l6.8 6.8M11.4 4.6l-6.8 6.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    close.addEventListener("click", closeSettings);
    var body = document.createElement("div");
    body.className = "rc-set-body";
    main.appendChild(close);
    main.appendChild(body);
    card.appendChild(side);
    card.appendChild(main);
    layer.appendChild(card);
    layer.addEventListener("mousedown", function (ev) {
      if (ev.target === layer) closeSettings();
    });
    layer.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeSettings();
      }
    });
    (document.body || document.documentElement).appendChild(layer);
    document.documentElement.classList.add("rc-settings-open");
    paintSettings(true);
    loadBgImages();
    try {
      close.focus({ preventScroll: true });
    } catch (_) {}
  }

  function paintThemeSwitch() {
    var box = document.getElementById("rc-ws-theme");
    if (!box) return;
    Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
      var on = b.dataset.theme === themePref;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function buildThemeSwitch() {
    var wrap = document.createElement("div");
    wrap.className = "rc-ws-theme-wrap";
    var label = document.createElement("div");
    label.className = "rc-ws-title";
    label.textContent = "Tema";
    var box = document.createElement("div");
    box.id = "rc-ws-theme";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Tema");
    THEME_LABELS.forEach(function (item) {
      var b = wsButton("", "rc-ws-theme-opt", "Tema " + item[1].toLowerCase(), function () {
        setThemePref(item[0]);
      });
      b.innerHTML = THEME_ICONS[item[0]];
      var text = document.createElement("span");
      text.textContent = item[1];
      b.appendChild(text);
      b.dataset.theme = item[0];
      box.appendChild(b);
    });
    wrap.appendChild(label);
    wrap.appendChild(box);
    return wrap;
  }

  // Signs this browser out (the server expires its cookie); other devices
  // stay in. First tap arms it, the second one leaves.
  var LOGOUT_ICON =
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 2.5H4A1.5 1.5 0 0 0 2.5 4v8A1.5 1.5 0 0 0 4 13.5h2.5M10.5 11 13.5 8l-3-3M13.5 8H6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function buildLogout() {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "rc-ws-logout";
    var armed = 0;
    function paint(text) {
      b.innerHTML = LOGOUT_ICON;
      var span = document.createElement("span");
      span.textContent = text;
      b.appendChild(span);
    }
    paint("Cerrar sesión");
    b.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (!armed) {
        b.classList.add("is-armed");
        paint("Toca otra vez para salir");
        armed = setTimeout(function () {
          armed = 0;
          b.classList.remove("is-armed");
          paint("Cerrar sesión");
        }, 3000);
        return;
      }
      clearTimeout(armed);
      b.disabled = true;
      paint("Saliendo…");
      fetch("/rc-logout", { method: "POST", cache: "no-store" })
        .catch(function () {})
        .then(function () {
          location.reload();
        });
    });
    return b;
  }

  function bootTheme() {
    applyTheme();
    if (!lightQuery) return;
    var onChange = function () {
      if (themePref === "system") applyTheme();
    };
    if (typeof lightQuery.addEventListener === "function") lightQuery.addEventListener("change", onChange);
    else if (typeof lightQuery.addListener === "function") lightQuery.addListener(onChange);
  }

  function bootWorkspaces(bar) {
    var menu = document.createElement("button");
    menu.id = "rc-ws-menu";
    menu.type = "button";
    menu.setAttribute("aria-label", "Workspaces");
    menu.innerHTML =
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4h11M2.5 8h11M2.5 12h11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    menu.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (document.documentElement.classList.contains("rc-ws-open")) closeDrawer();
      else openDrawer();
    });
    bar.insertBefore(menu, bar.firstChild);

    var backdrop = document.createElement("div");
    backdrop.id = "rc-ws-backdrop";
    backdrop.addEventListener("click", closeDrawer);
    var drawer = document.createElement("aside");
    drawer.id = "rc-ws-drawer";
    drawer.setAttribute("aria-label", "Workspaces");
    var title = document.createElement("div");
    title.className = "rc-ws-title";
    title.textContent = "Workspaces";
    var find = document.createElement("label");
    find.className = "rc-ws-find";
    find.innerHTML =
      '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4l3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    var findInput = document.createElement("input");
    findInput.id = "rc-ws-search";
    findInput.type = "search";
    findInput.placeholder = "Buscar workspace…";
    findInput.autocomplete = "off";
    findInput.spellcheck = false;
    findInput.setAttribute("aria-label", "Buscar workspace");
    find.appendChild(findInput);
    findInput.addEventListener("input", function () {
      wsFilter = findInput.value.trim().toLowerCase();
      renderDrawer();
    });
    findInput.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      if (ev.key === "Escape") {
        ev.preventDefault();
        if (findInput.value) {
          findInput.value = "";
          wsFilter = "";
          renderDrawer();
        } else closeDrawer();
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        var first = document.querySelector("#rc-ws-list .rc-ws-main");
        if (first) first.click();
      }
    });
    var list = document.createElement("div");
    list.id = "rc-ws-list";
    var add = wsButton("+ Nuevo grupo", "rc-ws-add", "", function () {
      groupEdit = "new";
      groupConfirm = "";
      renderDrawer();
    });
    drawer.appendChild(title);
    drawer.appendChild(find);
    drawer.appendChild(list);
    drawer.appendChild(add);
    drawer.appendChild(buildProfileBox());
    drawer.appendChild(buildThemeSwitch());
    drawer.appendChild(buildLogout());
    var root = document.body || document.documentElement;
    root.appendChild(backdrop);
    root.appendChild(drawer);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && document.documentElement.classList.contains("rc-ws-open")) closeDrawer();
    });
    hookTabOpen();
    paintThemeSwitch();
    wsPost({ op: "open", tab: tabId() });
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
    // ☰ (bootWorkspaces puts it first), then who is typing, then the tabs.
    host.appendChild(buildProfileChip());
    host.appendChild(tabs);
    host.appendChild(fresh);
    (document.body || document.documentElement).appendChild(host);
    paintProfileChip();
    bootWorkspaces(host);
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

  // What the file explorer (files.js) needs from the terminal page.
  window.__rcHost = {
    sendInput: function (text) {
      return sendInput(text);
    },
    focusTerm: function () {
      focusTerm();
    },
    // Hide the phone keyboard and park the terminal while the explorer is up.
    releaseKeyboard: function () {
      imeGuard = 0;
      if (document.documentElement.classList.contains("rc-touch")) lockIme();
      var ta = termTextarea();
      if (ta) {
        try {
          ta.blur();
        } catch (_) {}
      }
    },
    toast: function (text, live) {
      showToast(text, live);
    },
    device: function () {
      return foldDevice();
    },
    cwd: function () {
      var id = tabId();
      for (var i = 0; i < sessionCache.length; i++) {
        if (sessionCache[i].id === id) return sessionCache[i].path || "";
      }
      return "";
    },
    post: function (op) {
      return wsPost(op);
    },
    prefs: function () {
      return wsState && wsState.fsPrefs ? wsState.fsPrefs[foldDevice()] || null : null;
    },
    openTab: function (id) {
      if (typeof window.__rcOpenSession === "function") window.__rcOpenSession(id);
    },
  };

  // No tab open in this workspace: the terminal stays blank and this screen
  // asks for the first one. Tabs only open from here or the + button.
  function mountEmpty() {
    if (!window.__rcEmpty || document.getElementById("rc-empty")) return;
    var box = document.createElement("div");
    box.id = "rc-empty";
    var card = document.createElement("div");
    card.className = "rc-empty-card";
    var icon = document.createElement("div");
    icon.className = "rc-empty-icon";
    icon.innerHTML =
      '<svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7.5 9.5 3 2.5-3 2.5M13 15h3.5"/></svg>';
    var title = document.createElement("div");
    title.className = "rc-empty-title";
    title.textContent = "Sin terminales abiertas";
    var sub = document.createElement("div");
    sub.className = "rc-empty-sub";
    var ws = currentWs();
    sub.textContent = ws ? "Workspace " + ws.name : "Abre una terminal para empezar";
    var go = document.createElement("button");
    go.type = "button";
    go.className = "rc-empty-go";
    go.textContent = "+ Nueva terminal";
    go.addEventListener("click", function (ev) {
      ev.preventDefault();
      go.disabled = true;
      go.textContent = "Abriendo…";
      if (typeof window.__rcNewSession === "function") window.__rcNewSession();
      setTimeout(function () {
        go.disabled = false;
        go.textContent = "+ Nueva terminal";
      }, 4000);
    });
    card.appendChild(icon);
    card.appendChild(title);
    card.appendChild(sub);
    card.appendChild(go);
    box.appendChild(card);
    (document.body || document.documentElement).appendChild(box);
    document.documentElement.classList.add("rc-is-empty");
  }

  function boot() {
    window.__rcToast = showToast;
    mountEmpty();
    var device = detectDevice();
    document.documentElement.dataset.rcDevice = device;
    document.documentElement.classList.add("rc-" + device);
    bootTheme();
    bootProfiles();
    bootSessions();
    watchNativeClear();
    bootPaste();
    bootCopyChip();
    bootOsc52();
    bootSizeClaim();
    bootPaneMarks();
    bootGuestSize();
    bootGpuWatch();
    bootLinks();
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
    pcFiles.mount();
    if (device === "pc") {
      document.documentElement.classList.remove("rc-touch");
      mountPcMic();
      mountPcFile();
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

  function start() {
    // ttyd runs execCommand("copy") on every selection change, so any drag
    // replaced the clipboard. Copying now waits for the Copiar chip.
    (function blockCopyOnSelect() {
      var nativeExec = document.execCommand;
      if (!nativeExec || nativeExec.__rcWrapped) return;
      var wrapped = function (cmd) {
        if (String(cmd || "").toLowerCase() === "copy") throw new Error("copy on select disabled");
        return nativeExec.apply(document, arguments);
      };
      wrapped.__rcWrapped = true;
      document.execCommand = wrapped;
    })();
    wrapWebSocket();
    bootPrimaryRetry();
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }

  // tab_session.js is still choosing a tab; it runs start() if it ends on
  // the empty screen instead of navigating.
  if (window.__rcRedirecting) (window.__rcLateBoot = window.__rcLateBoot || []).push(start);
  else start();
})();
