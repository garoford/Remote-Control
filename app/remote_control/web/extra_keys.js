(function () {
  "use strict";

  var NativeWS = window.WebSocket;
  var INPUT = 48;
  var encoder = new TextEncoder();
  var mods = { ctrl: false, alt: false };
  var locks = { ctrl: false, alt: false };
  var lastTap = { ctrl: 0, alt: 0 };
  var keepFocusUntil = 0;
  var writing = false;
  var writeBusy = false;
  var writeQ = "";
  var lastFit = "";
  var lastSentAt = 0;
  var layoutRaf = 0;
  var scrollBusy = false;
  var scrollPend = 0;
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
  ];

  var PASTE_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path fill="currentColor" d="M15 3h-1.2A2.8 2.8 0 0 0 11 1H9a2.8 2.8 0 0 0-2.8 2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2Zm-6 0h2a.8.8 0 0 1 0 1.6H9A.8.8 0 0 1 9 3Zm6 16H5V5h1.2A2.8 2.8 0 0 0 9 7h2a2.8 2.8 0 0 0 2.8-2H15v14Z"/>' +
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
    return !!(
      el &&
      el.closest &&
      el.closest(".is-paste, #rc-ek-paste, #rc-file-pick")
    );
  }

  function termTextarea() {
    return document.querySelector(".xterm-helper-textarea");
  }

  function viewportTop() {
    var vp = document.querySelector(".xterm-viewport");
    return vp ? vp.scrollTop : null;
  }

  function restoreViewport(top) {
    var vp = document.querySelector(".xterm-viewport");
    if (!vp || top === null || top === undefined) return;
    if (vp.scrollTop !== top) vp.scrollTop = top;
  }

  function xterm() {
    var term = window.term;
    return term && typeof term.focus === "function" ? term : null;
  }

  function isTypeTarget(el) {
    if (!el) return true;
    if (el.id === "rc-file-pick") return false;
    return !(el.closest && el.closest("#rc-file-pick"));
  }

  function focusTerm() {
    var top = viewportTop();
    var term = xterm();
    if (term) {
      try {
        term.focus();
      } catch (_) {}
    }
    var ta = termTextarea();
    if (ta) {
      try {
        ta.focus({ preventScroll: true });
      } catch (_) {
        ta.focus();
      }
    }
    restoreViewport(top);
    requestAnimationFrame(function () {
      restoreViewport(top);
    });
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
    var term = xterm();
    if (term) {
      try {
        if (typeof term.scrollToBottom === "function") term.scrollToBottom();
      } catch (_) {}
      try {
        var buf = term.buffer && term.buffer.active;
        if (buf && typeof term.scrollToLine === "function") {
          term.scrollToLine(buf.baseY + buf.cursorY);
        }
      } catch (_) {}
      try {
        if (typeof term.scrollLines === "function") term.scrollLines(9999);
      } catch (_) {}
      try {
        term.focus();
      } catch (_) {}
    }
    var vp = document.querySelector(".xterm-viewport");
    if (vp) vp.scrollTop = vp.scrollHeight;
    var ta = termTextarea();
    if (ta) {
      try {
        ta.focus({ preventScroll: true });
      } catch (_) {
        ta.focus();
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

  function preferredImageExt() {
    if (preferredImageExt._ext) return Promise.resolve(preferredImageExt._ext);
    return new Promise(function (resolve) {
      var canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 1;
      try {
        canvas.toBlob(function (blob) {
          preferredImageExt._ext =
            blob && blob.type === "image/webp" ? "webp" : "jpg";
          resolve(preferredImageExt._ext);
        }, "image/webp", PASTE_QUALITY);
      } catch (_) {
        preferredImageExt._ext = "jpg";
        resolve("jpg");
      }
    });
  }

  function reserveName(name) {
    return fetch("/rc-paste-reserve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name }),
    }).then(function (resp) {
      return resp.json().then(function (data) {
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
      if (!resp.ok) throw new Error("put");
      return resp.json();
    });
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
        var mime = ext === "jpg" ? "image/jpeg" : "image/webp";
        canvas.toBlob(
          function (out) {
            resolve(out && out.size ? out : blob);
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
    if (!blob) return Promise.resolve(false);
    var extP = asImage
      ? preferredImageExt()
      : Promise.resolve(extFromName(blob.name) || extFromType(blob.type) || "bin");
    return extP
      .then(function (ext) {
        var name = mintName(ext);
        return reserveName(name).then(function (info) {
          sendPaste(shellQuote(info.path));
          showToast(info.name);
          var work = asImage
            ? compressImage(blob, ext)
            : Promise.resolve(blob);
          return work.then(function (out) {
            var type = asImage
              ? ext === "jpg"
                ? "image/jpeg"
                : "image/webp"
              : blob.type || "application/octet-stream";
            return putPasteFile(name, out, type).then(function () {
              return true;
            });
          });
        });
      })
      .catch(function () {
        showToast("No pude guardar el archivo");
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
      openFilePicker();
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
    var fit = viewW + "x" + viewH + "@" + top + "," + left + ":" + barH;
    if (fit !== lastFit) {
      lastFit = fit;
      window.dispatchEvent(new Event("resize"));
    }
    if (writing) {
      requestAnimationFrame(function () {
        scrollToWrite();
      });
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
    pasteBtn.setAttribute("aria-label", "Pegar archivo");
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

  function postScroll(lines) {
    var tab = tabId();
    if (!tab || !lines) return Promise.resolve();
    var n = Math.max(-80, Math.min(80, lines | 0));
    if (!n) return Promise.resolve();
    return fetch(
      "/rc-scroll?tab=" + encodeURIComponent(tab) + "&lines=" + n,
      { method: "POST", cache: "no-store" }
    ).then(
      function () {},
      function () {}
    );
  }

  function pumpScroll() {
    if (scrollBusy || !scrollPend) return;
    scrollBusy = true;
    var n = scrollPend;
    scrollPend = 0;
    postScroll(n).then(function () {
      scrollBusy = false;
      if (scrollPend) pumpScroll();
    });
  }

  function scrollByLines(lines) {
    if (!lines) return;
    scrollPend += lines;
    pumpScroll();
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
        t.closest("#rc-extra-keys, #rc-file-pick")
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
      writing = false;
      ev.preventDefault();
      acc += lastY - y;
      lastY = y;
      var lines = acc / PX;
      var step = lines < 0 ? Math.ceil(lines) : Math.floor(lines);
      if (step) {
        acc -= step * PX;
        scrollByLines(step);
      }
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

  function beginWriting() {
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
      var ta = termTextarea();
      if (ta) ta.value = "";
      writeBusy = false;
      if (writeQ) pumpWrite();
    });
  }

  function flushTyped(text) {
    if (!text) return;
    writeQ += text;
    pumpWrite();
  }

  function bootTypeToTty() {
    document.addEventListener(
      "beforeinput",
      function (ev) {
        if (!ev.data || ev.isComposing) return;
        if (ev.inputType === "insertFromPaste" || ev.inputType === "insertFromYank") {
          return;
        }
        if (!isTypeTarget(ev.target)) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        flushTyped(ev.data);
      },
      true
    );
    document.addEventListener(
      "compositionend",
      function (ev) {
        if (!ev.data || !isTypeTarget(ev.target)) return;
        flushTyped(ev.data);
      },
      true
    );
    document.addEventListener(
      "keydown",
      function (ev) {
        if (ev.defaultPrevented || ev.isComposing) return;
        if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
        if (!isTypeTarget(ev.target)) return;
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
        flushTyped(seq);
      },
      true
    );
    document.addEventListener(
      "input",
      function (ev) {
        var ta = termTextarea();
        if (!ta || ev.target !== ta) return;
        if (!ta.value) return;
        flushTyped(ta.value);
      },
      true
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

  function openFilePicker() {
    filePicker().click();
  }

  function termSelection() {
    var sel = window.getSelection && window.getSelection();
    var text = sel && sel.toString ? sel.toString() : "";
    return text || "";
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
        },
        function () {
          showToast("No pude copiar");
        }
      );
      return;
    }
    showToast("No pude copiar");
  }

  function bootPaste() {
    document.addEventListener(
      "paste",
      function (ev) {
        if (ev.defaultPrevented) return;
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

  function boot() {
    var device = detectDevice();
    document.documentElement.dataset.rcDevice = device;
    document.documentElement.classList.add("rc-" + device);
    bootPaste();
    if (device === "pc") {
      return;
    }
    document.documentElement.classList.add("rc-touch");
    mountBar();
    bootInterceptors();
    bootTypeToTty();
    bootTouchScroll();
  }

  if (window.__rcRedirecting) return;
  wrapWebSocket();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
