(function () {
  "use strict";

  var NativeWS = window.WebSocket;
  var INPUT = 48;
  var encoder = new TextEncoder();
  var mods = { ctrl: false, alt: false };
  var locks = { ctrl: false, alt: false };
  var lastTap = { ctrl: 0, alt: 0 };
  var keepFocusUntil = 0;
  var lastSentAt = 0;
  var layoutRaf = 0;
  var bar = null;

  var ROWS = [
    [
      { id: "esc", label: "ESC", seq: "\u001b" },
      { id: "slash", label: "/", text: "/" },
      { id: "dash", label: "-", text: "-" },
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
    [{ id: "paste", label: "PEGAR", paste: true }],
  ];

  var PASTE_CHUNK = 2048;

  var ARROW_LETTER = {
    ArrowUp: "A",
    ArrowDown: "B",
    ArrowRight: "C",
    ArrowLeft: "D",
    Home: "H",
    End: "F",
  };

  function wrapWebSocket() {
    if (!NativeWS || NativeWS.__rcWrapped) return;
    function RCWebSocket(url, protocols) {
      var ws =
        protocols === undefined
          ? new NativeWS(url)
          : new NativeWS(url, protocols);
      var list = Array.isArray(protocols)
        ? protocols
        : protocols
          ? [protocols]
          : [];
      if (list.indexOf("tty") !== -1 || /\/ws/.test(String(url))) {
        window.__rcTermSocket = ws;
        ws.addEventListener("open", function () {
          setTimeout(function () {
            window.dispatchEvent(new CustomEvent("rc-ws-open"));
          }, 0);
        });
        ws.addEventListener("close", function () {
          window.dispatchEvent(new CustomEvent("rc-ws-close"));
        });
      }
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
    var platform = navigator.platform || "";
    var touches = navigator.maxTouchPoints || 0;
    var coarse = false;
    var fineHover = false;
    try {
      coarse = window.matchMedia("(pointer: coarse)").matches;
      fineHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    } catch (_) {}
    var iPad =
      /iPad/.test(ua) || (platform === "MacIntel" && touches > 1);
    if (iPad) return "tablet";
    if (/iPhone|iPod/.test(ua)) return "phone";

    var minCss = Math.min(window.innerWidth || 0, window.innerHeight || 0);
    var dpr = window.devicePixelRatio || 1;
    var minScreen = Math.min(screen.width || 0, screen.height || 0) / dpr;
    var size = Math.max(minCss, minScreen);
    var android = /Android/i.test(ua);
    var mobileToken = /Mobile/i.test(ua);
    var touchy =
      coarse ||
      touches > 1 ||
      android ||
      /Mobi|Tablet|Silk|Kindle/i.test(ua);

    if (!touchy && fineHover && touches <= 1) return "pc";

    if (touchy) {
      if (/Tablet|Nexus 7|Nexus 9|Nexus 10|SM-T|Kindle|Silk/i.test(ua)) {
        return "tablet";
      }
      if (android && !mobileToken && size >= 500) return "tablet";
      if (size >= 600) return "tablet";
      return "phone";
    }
    if (fineHover && touches === 0) return "pc";
    if (coarse || touches > 0) return size >= 600 ? "tablet" : "phone";
    return "pc";
  }

  function isPasteUi(el) {
    return !!(el && el.closest && el.closest("#rc-paste, .is-paste"));
  }

  function termTextarea() {
    return document.querySelector(".xterm-helper-textarea");
  }

  function focusTerm() {
    var ta = termTextarea();
    if (!ta) return;
    try {
      ta.focus({ preventScroll: true });
    } catch (_) {
      ta.focus();
    }
  }

  function armKeepFocus() {
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
    var i = 0;
    function step() {
      if (i >= out.length) {
        focusTerm();
        return;
      }
      sendInput(out.slice(i, i + PASTE_CHUNK));
      i += PASTE_CHUNK;
      if (i < out.length) setTimeout(step, 16);
      else focusTerm();
    }
    step();
    return true;
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

  function sendImageFile(file) {
    if (!file) return Promise.resolve(false);
    return fetch("/rc-paste-image", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    })
      .then(function (resp) {
        return resp.json().then(function (data) {
          return { ok: resp.ok, data: data };
        });
      })
      .then(function (result) {
        var path = result.ok && result.data && result.data.path;
        if (!path) {
          showToast("No pude guardar la imagen");
          return false;
        }
        sendPaste(shellQuote(path));
        showToast("Imagen: " + (result.data.name || path));
        return true;
      })
      .catch(function () {
        showToast("No pude guardar la imagen");
        return false;
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
      sendImageFile(file);
      return true;
    }
    return false;
  }

  function pasteTextOnly() {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      showToast("Pegá con Ctrl+V");
      return;
    }
    navigator.clipboard
      .readText()
      .then(function (text) {
        if (text) sendPaste(text);
        else showToast("El clipboard está vacío");
      })
      .catch(function () {
        showToast("Pegá con Ctrl+V");
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
        return sendImageFile(blob);
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
    ["ctrl", "alt"].forEach(function (name) {
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

  function applyMods(text) {
    var out = text;
    if (mods.ctrl) out = ctrlify(out);
    if (mods.alt) out = "\u001b" + out;
    return out;
  }

  function modifierParam() {
    var n = 1;
    if (mods.alt) n += 2;
    if (mods.ctrl) n += 4;
    return n;
  }

  function specialWithMods(def) {
    if (!mods.ctrl && !mods.alt) return def.seq || def.text || "";
    if (def.text) return applyMods(def.text);
    var letter = ARROW_LETTER[def.key];
    var n = modifierParam();
    if (letter) return "\u001b[1;" + n + letter;
    if (def.id === "pgup") return "\u001b[5;" + n + "~";
    if (def.id === "pgdn") return "\u001b[6;" + n + "~";
    if (def.id === "tab" && mods.ctrl) return "";
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
    ["ctrl", "alt"].forEach(function (name) {
      var el = bar.querySelector('[data-rc-id="' + name + '"]');
      if (!el) return;
      el.classList.toggle("is-on", mods[name] && !locks[name]);
      el.classList.toggle("is-lock", !!locks[name]);
    });
  }

  function pressKey(def) {
    if (def.paste) {
      pasteFromClipboard();
      return;
    }
    if (def.mod) {
      toggleMod(def.mod);
      return;
    }
    sendInput(specialWithMods(def));
    clearSticky();
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
    var term = document.getElementById("terminal-container");
    if (term) {
      term.style.top = top + "px";
      term.style.left = left + "px";
      term.style.width = viewW + "px";
      term.style.height = Math.max(48, viewH - barH) + "px";
    }
    window.dispatchEvent(new Event("resize"));
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
        if (def.paste) key.classList.add("is-paste");
        if (!def.paste) bindKeepFocus(key);
        key.addEventListener(
          "pointerdown",
          function (ev) {
            key.classList.add("is-down");
            if (def.paste) ev.preventDefault();
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
      if (bar.contains(ev.target)) {
        focusTerm();
      }
    });
    document.addEventListener("focusout", function () {
      if (Date.now() < keepFocusUntil) {
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
    if ((!mods.ctrl && !mods.alt) || !text) return false;
    if (recentlySent()) return true;
    sendInput(applyMods(text));
    clearSticky();
    return true;
  }

  function bootInterceptors() {
    document.addEventListener(
      "keydown",
      function (ev) {
        if (!mods.ctrl && !mods.alt) return;
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
        if (!mods.ctrl && !mods.alt) return;
        if (!ev.data) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        interceptTyped(ev.data);
      },
      true
    );
  }

  function scrollLocal(lines) {
    var vp = document.querySelector(".xterm-viewport");
    if (!vp) return false;
    var max = vp.scrollHeight - vp.clientHeight;
    if (max <= 2) return false;
    var next = Math.max(0, Math.min(max, vp.scrollTop + lines * 20));
    if (next === vp.scrollTop) return false;
    vp.scrollTop = next;
    return true;
  }

  function scrollTmux(lines) {
    if (!lines) return;
    var btn = lines < 0 ? 64 : 65;
    var count = Math.min(12, Math.abs(lines));
    var seq = "";
    for (var i = 0; i < count; i++) seq += "\u001b[<" + btn + ";10;10M";
    sendInput(seq);
  }

  function scrollByLines(lines) {
    if (!lines) return;
    if (!scrollLocal(lines)) scrollTmux(lines);
  }

  function bootTouchScroll() {
    var startY = 0;
    var lastY = 0;
    var acc = 0;
    var scrolling = false;
    var active = false;
    var PX = 16;

    function fromKeys(ev) {
      var t = ev.target;
      return !!(
        t &&
        t.closest &&
        t.closest("#rc-extra-keys, #rc-paste")
      );
    }

    function onStart(ev) {
      if (!ev.touches || ev.touches.length !== 1 || fromKeys(ev)) {
        active = false;
        return;
      }
      startY = lastY = ev.touches[0].clientY;
      acc = 0;
      scrolling = false;
      active = true;
    }

    function onMove(ev) {
      if (!active || !ev.touches || ev.touches.length !== 1) return;
      var y = ev.touches[0].clientY;
      if (!scrolling && Math.abs(y - startY) < 8) return;
      scrolling = true;
      ev.preventDefault();
      acc += lastY - y;
      lastY = y;
      var lines = acc / PX;
      var step = lines < 0 ? Math.ceil(lines) : Math.floor(lines);
      if (!step) return;
      acc -= step * PX;
      scrollByLines(step);
    }

    function onEnd() {
      active = false;
      scrolling = false;
      acc = 0;
    }

    function bind(el) {
      if (!el || el.dataset.rcTouchScroll === "1") return;
      el.dataset.rcTouchScroll = "1";
      el.addEventListener("touchstart", onStart, { passive: true });
      el.addEventListener("touchmove", onMove, { passive: false });
      el.addEventListener("touchend", onEnd, { passive: true });
      el.addEventListener("touchcancel", onEnd, { passive: true });
    }

    bind(document.body);
    var mo = new MutationObserver(function () {
      bind(document.getElementById("terminal-container"));
    });
    mo.observe(document.documentElement, { childList: true, subtree: true });
    bind(document.getElementById("terminal-container"));
    setTimeout(function () {
      mo.disconnect();
      bind(document.getElementById("terminal-container"));
    }, 4000);
  }

  function bootTapToFocus() {
    function fromKeys(ev) {
      var t = ev.target;
      return !!(
        t &&
        t.closest &&
        t.closest("#rc-extra-keys, #rc-paste")
      );
    }
    function focus() {
      focusTerm();
    }
    document.addEventListener(
      "pointerup",
      function (ev) {
        if (fromKeys(ev)) return;
        if (ev.pointerType === "mouse" && ev.button !== 0) return;
        focus();
      },
      { passive: true }
    );
    document.addEventListener(
      "touchend",
      function (ev) {
        if (fromKeys(ev)) return;
        focus();
        setTimeout(focus, 50);
      },
      { passive: true }
    );
  }

  function mountPasteChip() {
    if (document.getElementById("rc-paste")) return;
    var btn = document.createElement("button");
    btn.id = "rc-paste";
    btn.type = "button";
    btn.textContent = "Pegar";
    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      pasteFromClipboard();
    });
    document.body.appendChild(btn);
  }

  function bootPaste() {
    document.addEventListener(
      "paste",
      function (ev) {
        if (ev.defaultPrevented) return;
        if (applyClipboardData(ev.clipboardData)) {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
        }
      },
      true
    );
  }

  function boot() {
    var device = detectDevice();
    document.documentElement.dataset.rcDevice = device;
    document.documentElement.classList.add("rc-" + device);
    bootPaste();
    if (device === "pc") {
      mountPasteChip();
      return;
    }
    document.documentElement.classList.add("rc-touch");
    mountBar();
    bootInterceptors();
    bootTouchScroll();
    bootTapToFocus();
    setTimeout(focusTerm, 300);
  }

  if (window.__rcRedirecting) return;
  wrapWebSocket();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
