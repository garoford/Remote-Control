/* Remote Control — file explorer.
 *
 * A Nautilus-like window over the terminal: places sidebar, tabs, a path bar
 * that turns into a text field, grid and list views, preview, selection,
 * context menu, trash, uploads and downloads (several items are zipped on
 * their own). Talks to /rc-fs/* in proxy.py; the terminal page lends a few
 * helpers through window.__rcHost (extra_keys.js).
 */
(function () {
  "use strict";
  if (window.__rcFiles) return;

  // --- icons ----------------------------------------------------------------

  function svg(body, box) {
    return '<svg viewBox="' + (box || "0 0 24 24") + '" aria-hidden="true">' + body + "</svg>";
  }
  function stroke(d) {
    return svg('<path d="' + d + '" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>');
  }
  var I = {
    back: stroke("M15 5l-7 7 7 7"),
    fwd: stroke("M9 5l7 7-7 7"),
    up: stroke("M5 15l7-7 7 7"),
    close: stroke("M6 6l12 12M18 6L6 18"),
    search: stroke("M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13zM20 20l-4.8-4.8"),
    grid: stroke("M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"),
    list: stroke("M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"),
    menu: stroke("M4 7h16M4 12h16M4 17h16"),
    dots: svg('<circle cx="12" cy="5" r="1.8" fill="currentColor"/><circle cx="12" cy="12" r="1.8" fill="currentColor"/><circle cx="12" cy="19" r="1.8" fill="currentColor"/>'),
    caret: stroke("M7 10l5 5 5-5"),
    sidebar: stroke("M4 5h16v14H4zM9 5v14"),
    plus: stroke("M12 5v14M5 12h14"),
    clear: stroke("M9 6h11v12H9l-5-6 5-6zM12 10l4 4M16 10l-4 4"),
    upload: stroke("M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"),
    download: stroke("M12 4v12M7 11l5 5 5-5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"),
    extract: stroke("M4 8l2-4h12l2 4M4 8h16v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM12 11v6M9 14l3 3 3-3"),
    terminal: stroke("M4 5h16v14H4zM7 9l3 3-3 3M12 15h5"),
    insert: stroke("M4 12h11M11 8l4 4-4 4M19 5v14"),
    copy: stroke("M9 9h10v10H9zM5 15V5h10"),
    cut: stroke("M6 6a2.5 2.5 0 1 0 0 .1M6 18a2.5 2.5 0 1 0 0-.1M8 7.5L19 18M8 16.5L19 6"),
    paste: stroke("M9 4h6v3H9zM7 5H5v15h14V5h-2"),
    rename: stroke("M4 20h4L19 9l-4-4L4 16v4zM13 7l4 4"),
    trash: stroke("M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"),
    restore: stroke("M4 12a8 8 0 1 0 2.4-5.7M4 4v5h5"),
    info: stroke("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8h.01"),
    open: stroke("M4 6h6l2 2h8v10H4z"),
    tab: stroke("M3 8h18v12H3zM3 8l2-4h6l2 4"),
    check: stroke("M5 12l5 5 9-10"),
    folderPlus: stroke("M4 6h6l2 2h8v10H4zM12 11v5M9.5 13.5h5"),
    filePlus: stroke("M6 3h8l4 4v14H6zM14 3v4h4M12 11v6M9 14h6"),
    eye: stroke("M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"),
    reload: stroke("M20 12a8 8 0 1 1-2.4-5.7M20 4v5h-5"),
    selectAll: stroke("M4 4h16v16H4zM8 12l3 3 5-6"),
    home: stroke("M4 11l8-7 8 7M6 9.5V20h12V9.5"),
    recent: stroke("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2"),
    desktop: stroke("M3 5h18v11H3zM8 20h8M12 16v4"),
    documents: stroke("M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6"),
    downloads: stroke("M12 4v11M7 10l5 5 5-5M5 20h14"),
    music: stroke("M9 18V5l11-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM20 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"),
    pictures: stroke("M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9h.01"),
    videos: stroke("M4 6h12v12H4zM16 10l4-2v8l-4-2"),
    drive: stroke("M3 14h18v5H3zM5 14l2-9h10l2 9M7 16.5h.01"),
    folder: stroke("M4 6h6l2 2h8v10H4z"),
    star: stroke("M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"),
  };

  // Adwaita-like big icons for the grid and list.
  var FOLDER_BIG = svg(
    '<path d="M6 12a4 4 0 0 1 4-4h14l5 5h25a4 4 0 0 1 4 4v33a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="var(--rcf-folder-back)"/>' +
      '<path d="M6 21a4 4 0 0 1 4-4h44a4 4 0 0 1 4 4v29a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="var(--rcf-folder)"/>' +
      '<path d="M6 21a4 4 0 0 1 4-4h44a4 4 0 0 1 4 4v1H6z" fill="#fff" opacity=".18"/>',
    "0 0 64 64"
  );
  function fileBig(color, glyph) {
    return svg(
      '<path d="M15 5h24l13 13v37a4 4 0 0 1-4 4H15a4 4 0 0 1-4-4V9a4 4 0 0 1 4-4z" fill="var(--rcf-page)"/>' +
        '<path d="M39 5v9a4 4 0 0 0 4 4h9z" fill="var(--rcf-page-fold)"/>' +
        '<path d="M15 5h24l13 13v37a4 4 0 0 1-4 4H15a4 4 0 0 1-4-4V9a4 4 0 0 1 4-4z" fill="none" stroke="var(--rcf-page-edge)" stroke-width="1"/>' +
        (color ? '<rect x="11" y="44" width="41" height="11" fill="' + color + '"/><path d="M11 55h41v0a4 4 0 0 1-4 4H15a4 4 0 0 1-4-4z" fill="' + color + '"/>' : "") +
        (glyph || ""),
      "0 0 64 64"
    );
  }
  // Kind colors from the theme's palette, so they follow it like the rest.
  var KIND_ICON = {
    file: fileBig("", '<path d="M19 26h26M19 32h26M19 38h18" stroke="var(--rcf-page-edge)" stroke-width="2.4" stroke-linecap="round"/>'),
    text: fileBig("", '<path d="M19 24h26M19 30h26M19 36h26M19 42h16" stroke="var(--rc-muted)" stroke-width="2.4" stroke-linecap="round"/>'),
    code: fileBig("var(--rc-blue)", '<path d="M26 24l-7 7 7 7M38 24l7 7-7 7" fill="none" stroke="var(--rc-blue)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'),
    image: fileBig("var(--rc-green)", '<circle cx="24" cy="25" r="4" fill="var(--rc-yellow)"/><path d="M15 40l10-9 7 6 6-5 11 8z" fill="var(--rc-green)"/>'),
    video: fileBig("var(--rc-red)", '<path d="M27 22v18l14-9z" fill="var(--rc-red)"/>'),
    audio: fileBig("var(--rc-orange)", '<path d="M28 39V24l14-3v15" fill="none" stroke="var(--rc-orange)" stroke-width="3"/><circle cx="25" cy="39" r="4" fill="var(--rc-orange)"/><circle cx="39" cy="36" r="4" fill="var(--rc-orange)"/>'),
    pdf: fileBig("var(--rc-red)", '<text x="32" y="37" text-anchor="middle" font-family="sans-serif" font-weight="700" font-size="12" fill="var(--rc-red)">PDF</text>'),
    archive: fileBig("var(--rc-warn)", '<path d="M30 8h4v4h-4zM30 16h4v4h-4zM30 24h4v4h-4zM28 30h8v8h-8z" fill="var(--rc-warn)"/>'),
    doc: fileBig("var(--rc-blue)", '<path d="M19 24h26M19 30h26M19 36h20" stroke="var(--rc-blue)" stroke-width="2.4" stroke-linecap="round"/>'),
    sheet: fileBig("var(--rc-teal)", '<path d="M19 24h26v16H19zM19 32h26M28 24v16M37 24v16" fill="none" stroke="var(--rc-teal)" stroke-width="2"/>'),
    exec: fileBig("var(--rc-muted)", '<path d="M22 28l6 4-6 4M31 37h10" fill="none" stroke="var(--rc-muted)" stroke-width="2.6" stroke-linecap="round"/>'),
  };

  var EXT_KIND = {};
  function kinds(kind, list) {
    list.split(" ").forEach(function (e) {
      EXT_KIND[e] = kind;
    });
  }
  kinds("image", "jpg jpeg png gif webp avif heic bmp tif tiff ico svg");
  kinds("video", "mp4 mkv webm mov avi m4v mpg mpeg 3gp ogv");
  kinds("audio", "mp3 m4a ogg opus flac wav aac wma mid");
  kinds("pdf", "pdf");
  kinds("archive", "zip gz tgz bz2 xz zst 7z rar tar deb rpm apk jar iso");
  kinds("doc", "doc docx odt rtf");
  kinds("sheet", "xls xlsx ods csv");
  kinds("code", "js mjs cjs ts tsx jsx py rb go rs c h cpp hpp java kt swift php html htm css scss vue svelte sh bash zsh fish lua sql toml yaml yml json xml dart");
  kinds("text", "txt md log ini conf cfg env gitignore srt");
  kinds("exec", "exe appimage run bin");

  function ext(name) {
    var i = name.lastIndexOf(".");
    return i > 0 ? name.slice(i + 1).toLowerCase() : "";
  }
  function kindOf(item) {
    if (item.kind === "dir") return "dir";
    var k = EXT_KIND[ext(item.name)];
    if (k) return k;
    var m = item.mime || "";
    if (m.indexOf("image/") === 0) return "image";
    if (m.indexOf("video/") === 0) return "video";
    if (m.indexOf("audio/") === 0) return "audio";
    if (m.indexOf("text/") === 0) return "text";
    return "file";
  }
  var KIND_LABEL = {
    dir: "Carpeta",
    image: "Imagen",
    video: "Vídeo",
    audio: "Audio",
    pdf: "Documento PDF",
    archive: "Archivo comprimido",
    doc: "Documento",
    sheet: "Hoja de cálculo",
    code: "Código",
    text: "Texto",
    exec: "Programa",
    file: "Archivo",
  };

  // --- small helpers ----------------------------------------------------------

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function iconBtn(icon, label, fn, cls) {
    var b = el("button", "rcf-btn" + (cls ? " " + cls : ""));
    b.type = "button";
    b.innerHTML = icon;
    b.title = label;
    b.setAttribute("aria-label", label);
    if (fn)
      b.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        fn(ev, b);
      });
    return b;
  }
  function host() {
    return window.__rcHost || {};
  }
  function toast(text) {
    if (host().toast) host().toast(text);
  }
  function isTouch() {
    return document.documentElement.classList.contains("rc-touch");
  }
  function joinPath(dir, name) {
    return dir === "/" ? "/" + name : dir + "/" + name;
  }
  function baseName(path) {
    if (path === "/") return "/";
    var parts = String(path).split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : path;
  }
  function parentOf(path) {
    if (!path || path === "/") return "";
    var cut = path.replace(/\/+$/, "").lastIndexOf("/");
    return cut <= 0 ? "/" : path.slice(0, cut);
  }
  function fmtSize(n) {
    if (n == null) return "";
    if (n < 1000) return n + (n === 1 ? " byte" : " bytes");
    var units = ["kB", "MB", "GB", "TB"];
    var v = n;
    var u = -1;
    do {
      v /= 1000;
      u++;
    } while (v >= 1000 && u < units.length - 1);
    return (v < 10 ? v.toFixed(1) : Math.round(v)).toString().replace(".", ",") + " " + units[u];
  }
  var MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"];
  function fmtDate(sec) {
    if (!sec) return "";
    var d = new Date(sec * 1000);
    var now = new Date();
    var hm = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    var day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (day === today) return hm;
    if (day === today - 86400000) return "Ayer";
    var s = d.getDate() + " " + MONTHS[d.getMonth()];
    return d.getFullYear() === now.getFullYear() ? s : s + " " + d.getFullYear();
  }
  function fmtFull(sec) {
    if (!sec) return "";
    var d = new Date(sec * 1000);
    return d.toLocaleString("es", { dateStyle: "long", timeStyle: "short" });
  }
  function shellQuote(p) {
    if (/^[A-Za-z0-9_\/.,:+@%=-]+$/.test(p)) return p;
    return "'" + p.replace(/'/g, "'\\''") + "'";
  }
  function fold(s) {
    return String(s || "")
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "");
  }
  var collator = new Intl.Collator("es", { numeric: true, sensitivity: "base" });

  function api(route, params, opts) {
    var q = "";
    if (params) {
      q =
        "?" +
        Object.keys(params)
          .map(function (k) {
            return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]);
          })
          .join("&");
    }
    return fetch("/rc-fs/" + route + q, Object.assign({ cache: "no-store", credentials: "same-origin" }, opts || {})).then(
      function (resp) {
        return resp
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!resp.ok && !data.error) data.error = "Error " + resp.status;
            if (!resp.ok) data.ok = false;
            return data;
          });
      },
      function () {
        return { ok: false, error: "Sin conexión" };
      }
    );
  }
  function op(body) {
    return api("op", null, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  function fileUrl(path, dl) {
    return "/rc-fs/file?path=" + encodeURIComponent(path) + (dl ? "&dl=1" : "");
  }
  function thumbUrl(item) {
    return "/rc-fs/thumb?path=" + encodeURIComponent(item.path) + "&m=" + item.mtime;
  }
  function saveUrl(url, name) {
    var a = document.createElement("a");
    a.href = url;
    a.download = name || "";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      a.remove();
    }, 0);
  }

  // --- state ----------------------------------------------------------------------

  var PREF_KEY = "rc-files-prefs";
  var prefs = { view: "grid", hidden: false, sort: "name", desc: false, sidebar: true, zoom: 2, tabs: [], active: 0 };
  var prefsLoaded = false;
  var tabs = [];
  var active = 0;
  var places = null;
  var clip = null; // {mode:"copy"|"cut", paths:[]}
  var root = null;
  var ui = {};
  var openNow = false;
  var pollTimer = 0;

  function readLocalPrefs() {
    try {
      var raw = JSON.parse(localStorage.getItem(PREF_KEY) || "null");
      if (raw && typeof raw === "object") Object.assign(prefs, raw);
    } catch (_) {}
  }
  function takePrefs(all) {
    if (prefsLoaded || !all) return;
    var mine = host().device ? all[host().device()] : null;
    if (mine) {
      Object.assign(prefs, mine);
      prefsLoaded = true;
    }
  }
  var savePrefsTimer = 0;
  function savePrefs() {
    prefs.tabs = tabs
      .map(function (t) {
        return t.path;
      })
      .filter(function (p) {
        return p && p.indexOf("://") === -1;
      })
      .slice(0, 12);
    prefs.active = Math.max(0, Math.min(prefs.tabs.length - 1, active));
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
    } catch (_) {}
    clearTimeout(savePrefsTimer);
    savePrefsTimer = setTimeout(function () {
      if (host().post && host().device) host().post({ op: "fsPrefs", device: host().device(), prefs: prefs });
    }, 600);
  }

  function newTab(path) {
    return { path: path, hist: [], fwd: [], data: null, sel: {}, anchor: "", search: null, error: "", scroll: 0, loading: false };
  }
  function tab() {
    return tabs[active];
  }

  // --- building the window ----------------------------------------------------------

  function build() {
    root = el("div");
    root.id = "rc-files";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "Archivos");
    root.tabIndex = -1;
    // The tap that opened the window (pointerup on the files button) is
    // followed by its click; it must not land on whatever is now under it.
    ["click", "mousedown", "mouseup", "dblclick", "contextmenu"].forEach(function (type) {
      root.addEventListener(
        type,
        function (ev) {
          if (Date.now() < openGuardUntil) {
            ev.preventDefault();
            ev.stopPropagation();
          }
        },
        true
      );
    });
    root.addEventListener("mousedown", function (ev) {
      if (ev.target === root) close();
    });

    var win = el("div", "rcf-win");
    ui.win = win;

    // sidebar
    var side = el("aside", "rcf-side");
    var sideHead = el("div", "rcf-side-head");
    sideHead.appendChild(el("div", "rcf-app", "Archivos"));
    sideHead.appendChild(
      iconBtn(I.close, "Ocultar barra lateral", function () {
        toggleSidebar(false);
      }, "rcf-side-hide")
    );
    side.appendChild(sideHead);
    ui.places = el("nav", "rcf-places");
    side.appendChild(ui.places);
    ui.side = side;
    ui.sideShade = el("div", "rcf-side-shade");
    ui.sideShade.addEventListener("click", function () {
      toggleSidebar(false);
    });

    // header
    var main = el("section", "rcf-main");
    var head = el("header", "rcf-head");
    ui.sideBtn = iconBtn(I.sidebar, "Barra lateral", function () {
      toggleSidebar();
    }, "rcf-side-btn");
    ui.back = iconBtn(I.back, "Atrás (Alt+←)", goBack);
    ui.fwd = iconBtn(I.fwd, "Adelante (Alt+→)", goFwd);
    head.appendChild(ui.sideBtn);
    var nav = el("div", "rcf-nav");
    nav.appendChild(ui.back);
    nav.appendChild(ui.fwd);
    head.appendChild(nav);

    var loc = el("div", "rcf-loc");
    ui.crumbs = el("div", "rcf-crumbs");
    ui.crumbs.addEventListener("click", function (ev) {
      if (ev.target === ui.crumbs) editPath();
    });
    ui.pathForm = el("form", "rcf-path-form");
    ui.pathInput = el("input", "rcf-path-input");
    ui.pathInput.type = "text";
    ui.pathInput.spellcheck = false;
    ui.pathInput.autocomplete = "off";
    ui.pathInput.setAttribute("autocapitalize", "off");
    ui.pathInput.setAttribute("aria-label", "Ruta");
    ui.pathForm.appendChild(ui.pathInput);
    var clearBtn = iconBtn(I.clear, "Borrar", function () {
      ui.pathInput.value = "";
      ui.pathInput.focus();
    }, "rcf-in-btn");
    ui.pathForm.appendChild(clearBtn);
    ui.pathForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var v = ui.pathInput.value.trim();
      if (tab().search != null) return;
      endEditPath();
      if (v) go(v);
    });
    ui.pathInput.__rcKey = function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        if (tab().search != null) endSearch();
        else endEditPath();
        return true;
      }
      if (ev.key === "ArrowDown" && tab().search != null) {
        ev.preventDefault();
        ui.body.focus();
        moveSel(1, 0, false);
        return true;
      }
      return false;
    };
    ui.pathInput.addEventListener("input", function () {
      if (tab().search != null) scheduleSearch();
    });
    ui.pathInput.addEventListener("blur", function () {
      setTimeout(function () {
        if (!openNow || document.activeElement === ui.pathInput) return;
        if (tab().search == null) endEditPath();
      }, 120);
    });
    loc.appendChild(ui.crumbs);
    loc.appendChild(ui.pathForm);
    ui.loc = loc;
    head.appendChild(loc);

    var tools = el("div", "rcf-tools");
    ui.searchBtn = iconBtn(I.search, "Buscar (Ctrl+F)", function () {
      if (tab().search != null) endSearch();
      else startSearch("");
    });
    ui.viewBtn = iconBtn(I.grid, "Vista", function () {
      setView(prefs.view === "grid" ? "list" : "grid");
    }, "rcf-view-btn");
    ui.viewMenuBtn = iconBtn(I.caret, "Opciones de vista", function (ev, b) {
      viewMenu(b);
    }, "rcf-caret-btn");
    var viewGroup = el("div", "rcf-split");
    viewGroup.appendChild(ui.viewBtn);
    viewGroup.appendChild(ui.viewMenuBtn);
    ui.menuBtn = iconBtn(I.dots, "Menú", function (ev, b) {
      mainMenu(b);
    });
    tools.appendChild(
      iconBtn(I.plus, "Pestaña nueva (Alt+T)", function () {
        openInNewTab(tab().path);
      }, "rcf-newtab")
    );
    tools.appendChild(ui.searchBtn);
    tools.appendChild(viewGroup);
    tools.appendChild(ui.menuBtn);
    tools.appendChild(iconBtn(I.close, "Cerrar (Esc)", close, "rcf-close"));
    head.appendChild(tools);
    main.appendChild(head);

    ui.tabs = el("div", "rcf-tabs");
    main.appendChild(ui.tabs);
    ui.banner = el("div", "rcf-banner");
    main.appendChild(ui.banner);

    ui.body = el("div", "rcf-body");
    ui.body.tabIndex = 0;
    ui.body.addEventListener("scroll", function () {
      if (tab()) tab().scroll = ui.body.scrollTop;
    });
    main.appendChild(ui.body);

    ui.status = el("div", "rcf-status");
    main.appendChild(ui.status);
    ui.selbar = el("div", "rcf-selbar");
    main.appendChild(ui.selbar);
    ui.drop = el("div", "rcf-drop", "Suelta para subir aquí");
    main.appendChild(ui.drop);

    win.appendChild(ui.sideShade);
    win.appendChild(side);
    win.appendChild(main);
    root.appendChild(win);

    ui.ops = el("div", "rcf-ops");
    root.appendChild(ui.ops);

    bootBody();
    bootDrop();
    (document.body || document.documentElement).appendChild(root);
  }

  // --- open / close -------------------------------------------------------------------

  var openGuardUntil = 0;

  function open(path) {
    if (!root) build();
    if (!openNow) openGuardUntil = Date.now() + 450;
    if (!prefsLoaded) {
      readLocalPrefs();
      var mine = host().prefs ? host().prefs() : null;
      if (mine) {
        Object.assign(prefs, mine);
        prefsLoaded = true;
      }
    }
    if (!tabs.length) {
      var saved = (prefs.tabs || []).filter(Boolean);
      if (saved.length) {
        tabs = saved.map(newTab);
        active = Math.min(prefs.active || 0, tabs.length - 1);
      } else {
        tabs = [newTab(host().cwd ? host().cwd() || "~" : "~")];
        active = 0;
      }
    }
    if (path) {
      tabs.push(newTab(path));
      active = tabs.length - 1;
    }
    openNow = true;
    root.classList.add("is-open");
    document.documentElement.classList.add("rcf-open");
    if (host().releaseKeyboard) host().releaseKeyboard();
    if (isTouch()) toggleSidebar(false, true);
    else toggleSidebar(prefs.sidebar !== false, true);
    applyZoom();
    loadPlaces();
    render();
    load(tab(), true);
    setTimeout(function () {
      ui.body.focus({ preventScroll: true });
    }, 30);
    clearInterval(pollTimer);
    pollTimer = setInterval(poll, 4000);
  }

  function close() {
    if (!openNow) return;
    if (closeLayer()) return;
    openNow = false;
    root.classList.remove("is-open");
    document.documentElement.classList.remove("rcf-open");
    clearInterval(pollTimer);
    savePrefs();
    if (host().focusTerm) setTimeout(host().focusTerm, 0);
  }

  // Closes the topmost popup (menu, dialog, preview); true if there was one.
  function closeLayer() {
    var layers = root.querySelectorAll(".rcf-layer");
    if (!layers.length) return false;
    var top = layers[layers.length - 1];
    if (top.__rcClose) top.__rcClose();
    else top.remove();
    return true;
  }

  function toggleSidebar(on, quiet) {
    if (on == null) on = !ui.win.classList.contains("side-on");
    ui.win.classList.toggle("side-on", !!on);
    if (!quiet && !isTouch()) {
      prefs.sidebar = !!on;
      savePrefs();
    }
  }

  // --- navigation ------------------------------------------------------------------------

  function go(path, opts) {
    var t = tab();
    opts = opts || {};
    if (!path) return;
    if (t.search != null) endSearch(true);
    if (!opts.noHist && t.path && t.path !== path) {
      t.hist.push(t.path);
      if (t.hist.length > 100) t.hist.shift();
      t.fwd = [];
    }
    t.path = path;
    t.sel = {};
    t.anchor = "";
    t.data = null;
    t.scroll = 0;
    t.error = "";
    t.focusName = opts.focusName || "";
    render();
    load(t);
    savePrefs();
    if (isTouch()) toggleSidebar(false, true);
  }
  // Phone shows only the current folder, so with no history "back" goes up.
  function upInstead(t) {
    return isTouch() && !t.hist.length && t.search == null && t.path.indexOf("://") === -1 && !!parentOf(t.path);
  }

  function goBack() {
    var t = tab();
    if (upInstead(t)) {
      go(parentOf(t.path), { noHist: true, focusName: baseName(t.path) });
      return;
    }
    if (!t.hist.length) return;
    t.fwd.push(t.path);
    var to = t.hist.pop();
    var from = t.path;
    go(to, { noHist: true, focusName: baseName(from) });
  }
  function goFwd() {
    var t = tab();
    if (!t.fwd.length) return;
    t.hist.push(t.path);
    go(t.fwd.pop(), { noHist: true });
  }
  function goUp() {
    var t = tab();
    if (!t.data || !t.data.parent) return;
    go(t.data.parent, { focusName: baseName(t.data.path) });
  }

  function load(t, keepSel) {
    t.loading = true;
    var path = t.path;
    renderLoading(t);
    return api("list", { path: path }).then(function (data) {
      if (t.path !== path) return;
      t.loading = false;
      if (data.error) {
        t.error = data.error;
        t.data = null;
      } else {
        t.error = "";
        if (data.path && data.path !== t.path && t.path.indexOf("://") === -1) {
          t.path = data.path; // "~" and "./" come back resolved
        }
        data.entries.forEach(function (it) {
          if (!it.path) it.path = joinPath(data.path, it.name);
        });
        t.data = data;
        if (!keepSel) t.sel = {};
        if (t.focusName) {
          var hit = data.entries.filter(function (it) {
            return it.name === t.focusName;
          })[0];
          if (hit) {
            t.sel = {};
            t.sel[hit.path] = true;
            t.anchor = hit.path;
          }
        }
      }
      if (t === tab()) render(true);
      else renderTabs();
    });
  }

  function poll() {
    var t = tab();
    if (!openNow || !t || t.loading || t.search != null || document.hidden) return;
    if (!t.data || root.querySelector(".rcf-layer, .rcf-renaming")) return;
    var path = t.path;
    api("list", { path: path }).then(function (data) {
      if (!openNow || t !== tab() || t.path !== path || data.error || !t.data) return;
      data.entries.forEach(function (it) {
        if (!it.path) it.path = joinPath(data.path, it.name);
      });
      if (signature(data.entries) === signature(t.data.entries)) return;
      t.data = data;
      Object.keys(t.sel).forEach(function (p) {
        if (!data.entries.some(function (it) { return it.path === p; })) delete t.sel[p];
      });
      render(true);
    });
  }
  function signature(list) {
    return list
      .map(function (it) {
        return it.name + "|" + it.size + "|" + it.mtime;
      })
      .join("\n");
  }

  function reload() {
    var t = tab();
    if (t.search != null) runSearch();
    else load(t, true);
    loadPlaces();
  }

  // --- places ------------------------------------------------------------------------------

  function loadPlaces() {
    api("places").then(function (data) {
      if (data.error) return;
      places = data;
      renderPlaces();
      renderTabs();
      if (tab()) renderHead();
    });
  }

  function renderPlaces() {
    var nav = ui.places;
    if (!nav || !places) return;
    nav.innerHTML = "";
    var cur = tab() ? tab().path : "";
    function row(item) {
      var b = el("button", "rcf-place");
      b.type = "button";
      b.innerHTML = I[item.icon] || I.folder;
      var name = el("span", "rcf-place-name", item.name);
      b.appendChild(name);
      b.title = item.path.indexOf("://") === -1 ? item.path : item.name;
      if (item.path === cur) b.classList.add("is-on");
      if (item.usage && item.usage.total) {
        var used = 1 - item.usage.free / item.usage.total;
        var bar = el("span", "rcf-usage");
        var fill = el("span");
        fill.style.width = Math.round(used * 100) + "%";
        if (used > 0.9) fill.className = "is-full";
        bar.appendChild(fill);
        b.appendChild(bar);
        b.title = item.path + " — " + fmtSize(item.usage.free) + " libres de " + fmtSize(item.usage.total);
        b.classList.add("has-usage");
      }
      b.addEventListener("click", function (ev) {
        if (ev.button === 1 || ev.ctrlKey) openInNewTab(item.path);
        else go(item.path);
      });
      b.addEventListener("auxclick", function (ev) {
        if (ev.button === 1) {
          ev.preventDefault();
          openInNewTab(item.path);
        }
      });
      if (item.path.indexOf("://") === -1) dropTarget(b, item.path);
      return b;
    }
    places.main.forEach(function (p) {
      nav.appendChild(row(p));
    });
    if (places.bookmarks.length) {
      nav.appendChild(el("div", "rcf-sep"));
      places.bookmarks.forEach(function (p) {
        nav.appendChild(row(Object.assign({ icon: "folder" }, p)));
      });
    }
    nav.appendChild(el("div", "rcf-sep"));
    nav.appendChild(el("div", "rcf-place-title", "Otras ubicaciones"));
    places.other.forEach(function (p) {
      nav.appendChild(row(p));
    });
  }

  // --- rendering ------------------------------------------------------------------------------

  function visibleEntries(t) {
    if (!t.data) return [];
    var list = t.data.entries.filter(function (it) {
      return prefs.hidden || !it.hidden;
    });
    var dir = prefs.desc ? -1 : 1;
    var key = prefs.sort;
    if (t.path === "recent://" && key === "name") return list; // newest first, like Nautilus
    list.sort(function (a, b) {
      // Folders first, as Nautilus does.
      var da = a.kind === "dir" ? 0 : 1;
      var db = b.kind === "dir" ? 0 : 1;
      if (da !== db) return da - db;
      var r = 0;
      if (key === "size") r = (a.size || 0) - (b.size || 0);
      else if (key === "mtime") r = (a.mtime || 0) - (b.mtime || 0);
      else if (key === "type") r = collator.compare(KIND_LABEL[kindOf(a)], KIND_LABEL[kindOf(b)]) || collator.compare(ext(a.name), ext(b.name));
      if (!r) r = collator.compare(a.name, b.name);
      return r * dir;
    });
    return list;
  }

  function renderLoading(t) {
    if (t !== tab() || !ui.body) return;
    if (!t.data) {
      ui.body.classList.add("is-loading");
    }
  }

  function render(keepScroll) {
    if (!root) return;
    var t = tab();
    renderTabs();
    renderHead();
    renderPlaces();
    renderBanner();
    ui.body.classList.toggle("is-loading", !!t.loading && !t.data);
    ui.body.classList.toggle("is-list", prefs.view === "list");
    ui.body.classList.toggle("is-grid", prefs.view !== "list");
    var scroll = keepScroll ? ui.body.scrollTop : t.scroll;
    ui.body.innerHTML = "";
    if (t.error) {
      ui.body.appendChild(emptyState(I.info, "No se pudo abrir", t.error));
      renderSel();
      return;
    }
    if (!t.data) {
      ui.body.appendChild(el("div", "rcf-spinner"));
      renderSel();
      return;
    }
    var list = visibleEntries(t);
    t.visible = list;
    if (!list.length) {
      if (t.search != null) ui.body.appendChild(emptyState(I.search, t.search ? "Sin resultados" : "Escribe para buscar", t.search ? "Prueba con otras palabras" : "Busca en " + baseName(t.path) + " y sus subcarpetas"));
      else if (t.path === "trash://") ui.body.appendChild(emptyState(I.trash, "La papelera está vacía", ""));
      else ui.body.appendChild(emptyState(I.folder, "La carpeta está vacía", prefs.hidden ? "" : "Puede tener archivos ocultos (Ctrl+H)"));
      renderSel();
      return;
    }
    var box = el("div", prefs.view === "list" ? "rcf-rows" : "rcf-grid");
    if (prefs.view === "list") box.appendChild(listHeader(t));
    var observer = thumbObserver();
    list.forEach(function (it) {
      box.appendChild(prefs.view === "list" ? rowNode(t, it, observer) : tileNode(t, it, observer));
    });
    ui.body.appendChild(box);
    ui.body.scrollTop = scroll || 0;
    if (t.focusName) {
      var focused = box.querySelector(".is-sel");
      if (focused && focused.scrollIntoView) focused.scrollIntoView({ block: "nearest" });
      t.focusName = "";
    }
    renderSel();
  }

  function emptyState(icon, title, sub) {
    var box = el("div", "rcf-empty");
    var ic = el("div", "rcf-empty-icon");
    ic.innerHTML = icon;
    box.appendChild(ic);
    box.appendChild(el("div", "rcf-empty-title", title));
    if (sub) box.appendChild(el("div", "rcf-empty-sub", sub));
    return box;
  }

  var observerCache = null;
  function thumbObserver() {
    if (!("IntersectionObserver" in window)) return null;
    if (observerCache) observerCache.disconnect();
    observerCache = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          var img = en.target;
          observerCache.unobserve(img);
          img.src = img.dataset.src;
        });
      },
      { root: ui.body, rootMargin: "200px" }
    );
    return observerCache;
  }

  function iconNode(it, observer) {
    var box = el("span", "rcf-icon");
    var k = kindOf(it);
    box.innerHTML = k === "dir" ? FOLDER_BIG : KIND_ICON[k] || KIND_ICON.file;
    if (it.thumb && it.path.indexOf("://") === -1) {
      var img = el("img", "rcf-thumb");
      img.alt = "";
      img.decoding = "async";
      img.draggable = false;
      img.dataset.src = thumbUrl(it);
      img.addEventListener("load", function () {
        box.classList.add("has-thumb");
      });
      img.addEventListener("error", function () {
        img.remove();
      });
      box.appendChild(img);
      if (observer) observer.observe(img);
      else img.src = img.dataset.src;
    }
    if (it.link) box.appendChild(el("span", "rcf-link-badge", "↪"));
    return box;
  }

  function bindItem(node, t, it) {
    node.dataset.path = it.path;
    node.setAttribute("role", "option");
    if (t.sel[it.path]) {
      node.classList.add("is-sel");
      node.setAttribute("aria-selected", "true");
    }
    if (clip && clip.mode === "cut" && clip.paths.indexOf(it.path) !== -1) node.classList.add("is-cut");
    if (it.hidden) node.classList.add("is-hidden");
    if (!isTouch() && t.path !== "trash://") {
      node.draggable = true;
      node.addEventListener("dragstart", function (ev) {
        if (!t.sel[it.path]) selectOnly(t, it.path);
        var paths = selPaths(t);
        dragPaths = paths;
        ev.dataTransfer.effectAllowed = "copyMove";
        ev.dataTransfer.setData("text/plain", paths.join("\n"));
        ev.dataTransfer.setData("application/x-rc-files", JSON.stringify(paths));
      });
      node.addEventListener("dragend", function () {
        dragPaths = null;
      });
      if (it.kind === "dir") dropTarget(node, it.path);
    }
  }

  function tileNode(t, it, observer) {
    var node = el("div", "rcf-tile");
    node.appendChild(iconNode(it, observer));
    var name = el("span", "rcf-name", it.name);
    node.appendChild(name);
    node.title = tooltip(it);
    bindItem(node, t, it);
    return node;
  }

  function rowNode(t, it, observer) {
    var node = el("div", "rcf-row");
    var nameCell = el("span", "rcf-cell rcf-cell-name");
    nameCell.appendChild(iconNode(it, observer));
    var label = el("span", "rcf-name", it.name);
    nameCell.appendChild(label);
    if (it.where || it.orig) {
      label.appendChild(el("span", "rcf-where", " " + shortHome(it.orig ? parentOf(it.orig) : it.where)));
    }
    node.appendChild(nameCell);
    node.appendChild(el("span", "rcf-cell rcf-cell-size", it.kind === "dir" ? "" : fmtSize(it.size)));
    node.appendChild(el("span", "rcf-cell rcf-cell-type", KIND_LABEL[kindOf(it)]));
    node.appendChild(el("span", "rcf-cell rcf-cell-date", fmtDate(it.mtime)));
    node.title = tooltip(it);
    bindItem(node, t, it);
    return node;
  }

  function tooltip(it) {
    var parts = [it.name];
    if (it.kind !== "dir") parts.push(fmtSize(it.size));
    if (it.mtime) parts.push(fmtFull(it.mtime));
    if (it.orig) parts.push("Origen: " + it.orig);
    else if (it.where) parts.push(it.where);
    return parts.join("\n");
  }

  function shortHome(p) {
    var home = places && places.home;
    if (home && (p === home || p.indexOf(home + "/") === 0)) return "~" + p.slice(home.length);
    return p;
  }

  function listHeader(t) {
    var head = el("div", "rcf-row rcf-row-head");
    [
      ["name", "Nombre", "rcf-cell-name"],
      ["size", "Tamaño", "rcf-cell-size"],
      ["type", "Tipo", "rcf-cell-type"],
      ["mtime", t.path === "trash://" ? "Eliminado" : "Modificado", "rcf-cell-date"],
    ].forEach(function (col) {
      var b = el("button", "rcf-cell " + col[2], col[1]);
      b.type = "button";
      if (prefs.sort === col[0]) {
        b.classList.add("is-sorted");
        b.appendChild(el("span", "rcf-sort-arrow", prefs.desc ? "▾" : "▴"));
      }
      b.addEventListener("click", function () {
        if (prefs.sort === col[0]) prefs.desc = !prefs.desc;
        else {
          prefs.sort = col[0];
          prefs.desc = col[0] === "mtime" || col[0] === "size";
        }
        savePrefs();
        render(true);
      });
      head.appendChild(b);
    });
    return head;
  }

  function renderTabs() {
    var bar = ui.tabs;
    bar.innerHTML = "";
    bar.classList.toggle("is-on", tabs.length > 1);
    if (tabs.length < 2) return;
    tabs.forEach(function (t, i) {
      var b = el("div", "rcf-tab" + (i === active ? " is-on" : ""));
      b.setAttribute("role", "tab");
      var label = el("span", "rcf-tab-name", titleOf(t));
      b.appendChild(label);
      b.title = t.path;
      b.addEventListener("click", function () {
        switchTab(i);
      });
      b.addEventListener("auxclick", function (ev) {
        if (ev.button === 1) {
          ev.preventDefault();
          closeTab(i);
        }
      });
      b.appendChild(
        iconBtn(I.close, "Cerrar pestaña", function () {
          closeTab(i);
        }, "rcf-tab-x")
      );
      bar.appendChild(b);
    });
    // When they no longer fit, keep the active one in view.
    var on = bar.querySelector(".rcf-tab.is-on");
    if (on && bar.scrollWidth > bar.clientWidth) {
      requestAnimationFrame(function () {
        on.scrollIntoView({ block: "nearest", inline: "nearest" });
      });
    }
  }

  function titleOf(t) {
    if (t.search != null) return "Buscar: " + (t.search || "…");
    if (t.path === "recent://") return "Recientes";
    if (t.path === "trash://") return "Papelera";
    if (t.path === "~" || (places && t.path === places.home)) return "Carpeta personal";
    return baseName(t.path);
  }

  function renderHead() {
    var t = tab();
    ui.back.disabled = !t.hist.length && !upInstead(t);
    ui.fwd.disabled = !t.fwd.length;
    ui.viewBtn.innerHTML = prefs.view === "list" ? I.grid : I.list;
    ui.viewBtn.title = prefs.view === "list" ? "Vista de cuadrícula (Ctrl+2)" : "Vista de lista (Ctrl+1)";
    ui.searchBtn.classList.toggle("is-on", t.search != null);
    if (t.search != null) {
      ui.loc.classList.add("is-edit", "is-search");
      ui.pathInput.placeholder = "Buscar en " + titleOf({ path: t.path, search: null });
      if (document.activeElement !== ui.pathInput) ui.pathInput.value = t.search;
      return;
    }
    ui.loc.classList.remove("is-search");
    if (document.activeElement !== ui.pathInput) ui.loc.classList.remove("is-edit");
    ui.pathInput.placeholder = "";
    renderCrumbs(t);
  }

  function renderCrumbs(t) {
    var c = ui.crumbs;
    c.innerHTML = "";
    var path = t.path;
    if (path.indexOf("://") !== -1) {
      var only = el("span", "rcf-crumb is-last");
      only.innerHTML = path === "trash://" ? I.trash : I.recent;
      only.appendChild(el("span", null, titleOf(t)));
      c.appendChild(only);
      return;
    }
    var home = places ? places.home : "";
    var parts = [];
    var start = "/";
    if (home && (path === home || path.indexOf(home + "/") === 0)) {
      parts.push({ name: "Carpeta personal", path: home, icon: I.home });
      start = home;
    } else {
      parts.push({ name: "", path: "/", icon: I.drive });
    }
    var rest = path.slice(start.length).split("/").filter(Boolean);
    var acc = start;
    rest.forEach(function (seg) {
      acc = joinPath(acc, seg);
      parts.push({ name: seg, path: acc });
    });
    parts.forEach(function (p, i) {
      if (i) c.appendChild(el("span", "rcf-crumb-sep", "/"));
      var b = el("button", "rcf-crumb" + (i === parts.length - 1 ? " is-last" : ""));
      b.type = "button";
      if (p.icon) b.innerHTML = p.icon;
      if (p.name) b.appendChild(el("span", null, p.name));
      b.title = p.path;
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (i === parts.length - 1) openTree(b);
        else go(p.path);
      });
      dropTarget(b, p.path);
      c.appendChild(b);
    });
    requestAnimationFrame(function () {
      c.scrollLeft = c.scrollWidth;
    });
  }

  function renderBanner() {
    var t = tab();
    var b = ui.banner;
    b.innerHTML = "";
    b.className = "rcf-banner";
    if (t.path === "trash://" && t.data && t.data.entries.length) {
      b.classList.add("is-on");
      b.appendChild(el("span", null, "Los elementos se pueden restaurar o eliminar para siempre."));
      var empty = el("button", "rcf-pill is-danger", "Vaciar papelera");
      empty.type = "button";
      empty.addEventListener("click", confirmEmptyTrash);
      b.appendChild(empty);
    } else if (t.search != null && t.data && t.data.truncated) {
      b.classList.add("is-on");
      b.appendChild(el("span", null, "Se muestran los primeros resultados. Escribe algo más concreto para afinar."));
    } else if (t.data && t.data.writable === false && t.path.indexOf("://") === -1) {
      b.classList.add("is-on", "is-muted");
      b.appendChild(el("span", null, "Solo lectura: no puedes cambiar nada en esta carpeta."));
    }
  }

  // --- selection ----------------------------------------------------------------------------------

  function selPaths(t) {
    t = t || tab();
    var order = (t.visible || []).map(function (it) {
      return it.path;
    });
    return order.filter(function (p) {
      return t.sel[p];
    });
  }
  function selItems(t) {
    t = t || tab();
    return (t.visible || []).filter(function (it) {
      return t.sel[it.path];
    });
  }
  function itemOf(path) {
    var t = tab();
    return (t.visible || []).filter(function (it) {
      return it.path === path;
    })[0];
  }
  function selectOnly(t, path) {
    t.sel = {};
    if (path) t.sel[path] = true;
    t.anchor = path || "";
    paintSel();
  }
  function paintSel() {
    var t = tab();
    ui.body.querySelectorAll("[data-path]").forEach(function (n) {
      var on = !!t.sel[n.dataset.path];
      n.classList.toggle("is-sel", on);
      if (on) n.setAttribute("aria-selected", "true");
      else n.removeAttribute("aria-selected");
    });
    renderSel();
  }
  function selectRange(t, to) {
    var list = (t.visible || []).map(function (it) {
      return it.path;
    });
    var a = list.indexOf(t.anchor || to);
    var b = list.indexOf(to);
    if (a < 0) a = b;
    t.sel = {};
    for (var i = Math.min(a, b); i <= Math.max(a, b); i++) t.sel[list[i]] = true;
    paintSel();
  }
  var selectMode = false;

  function renderSel() {
    var t = tab();
    var items = selItems(t);
    var bar = ui.selbar;
    bar.innerHTML = "";
    var count = items.length;
    selectMode = selectMode && count > 0;
    root.classList.toggle("rcf-selecting", selectMode);
    // status pill, like Nautilus' floating bar
    var st = ui.status;
    st.textContent = "";
    st.classList.remove("is-on");
    if (count) {
      var files = items.filter(function (it) {
        return it.kind !== "dir";
      });
      var size = files.reduce(function (s, it) {
        return s + (it.size || 0);
      }, 0);
      var text =
        count === 1
          ? "«" + items[0].name + "» seleccionado" + (items[0].kind === "dir" ? "" : " (" + fmtSize(items[0].size) + ")")
          : count + " elementos seleccionados" + (files.length ? " (" + fmtSize(size) + ")" : "");
      st.textContent = text;
      st.classList.add("is-on");
    } else if (t.data && t.visible) {
      var n = t.visible.length;
      st.textContent = n + (n === 1 ? " elemento" : " elementos");
      st.classList.add("is-on", "is-quiet");
    }
    bar.classList.toggle("is-on", count > 0);
    if (!count) return;
    var trash = t.path === "trash://";
    if (isTouch()) {
      bar.appendChild(
        iconBtn(I.close, "Quitar selección", function () {
          selectMode = false;
          selectOnly(t, "");
        })
      );
      bar.appendChild(el("span", "rcf-selcount", String(count)));
    }
    if (trash) {
      bar.appendChild(pill(I.restore, "Restaurar", restoreSel));
      bar.appendChild(pill(I.trash, "Eliminar", deleteForeverSel, "is-danger"));
      return;
    }
    bar.appendChild(pill(I.download, "Descargar", downloadSel, "is-accent"));
    if (count === 1 && items[0].kind !== "dir") bar.appendChild(pill(I.eye, "Ver", function () { preview(items[0]); }));
    if (items.every(isArchive)) bar.appendChild(pill(I.extract, "Extraer", extractSel));
    bar.appendChild(pill(I.insert, "Insertar ruta", insertSel));
    bar.appendChild(pill(I.trash, "Papelera", trashSel));
    bar.appendChild(
      iconBtn(I.dots, "Más acciones", function (ev, b) {
        itemMenu(b, null);
      }, "rcf-selmore")
    );
  }
  function pill(icon, label, fn, cls) {
    var b = el("button", "rcf-pill" + (cls ? " " + cls : ""));
    b.type = "button";
    b.innerHTML = icon;
    b.appendChild(el("span", null, label));
    b.title = label;
    b.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      fn();
    });
    return b;
  }

  function moveSel(dx, dy, extend) {
    var t = tab();
    var list = t.visible || [];
    if (!list.length) return;
    var paths = list.map(function (it) {
      return it.path;
    });
    var cur = paths.indexOf(t.focus || t.anchor);
    var cols = 1;
    if (prefs.view !== "list") {
      var tiles = ui.body.querySelectorAll(".rcf-tile");
      if (tiles.length > 1) {
        var top = tiles[0].offsetTop;
        cols = 0;
        for (var i = 0; i < tiles.length && tiles[i].offsetTop === top; i++) cols++;
      }
    }
    var next = cur < 0 ? 0 : cur + dx + dy * cols;
    next = Math.max(0, Math.min(paths.length - 1, next));
    if (extend) {
      if (!t.anchor) t.anchor = paths[next];
      t.focus = paths[next];
      selectRange(t, paths[next]);
    } else {
      selectOnly(t, paths[next]);
      t.focus = paths[next];
    }
    var node = ui.body.querySelector('[data-path="' + cssEsc(paths[next]) + '"]');
    if (node && node.scrollIntoView) node.scrollIntoView({ block: "nearest" });
  }
  function cssEsc(s) {
    return window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&");
  }

  // --- body events: click, double click, rubber band, long press ----------------------------------

  var dragPaths = null;

  function bootBody() {
    var body = ui.body;
    var press = null;
    var band = null;

    body.addEventListener("click", function (ev) {
      var node = ev.target.closest("[data-path]");
      var t = tab();
      if (!node) {
        if (!ev.target.closest(".rcf-row-head") && !band) {
          if (!ev.ctrlKey && !ev.shiftKey) selectOnly(t, "");
        }
        return;
      }
      var path = node.dataset.path;
      var it = itemOf(path);
      if (!it) return;
      if (press && press.fired) return;
      if (isTouch()) {
        if (selectMode) {
          if (t.sel[path]) delete t.sel[path];
          else t.sel[path] = true;
          paintSel();
        } else activate(it);
        return;
      }
      if (ev.shiftKey) selectRange(t, path);
      else if (ev.ctrlKey || ev.metaKey) {
        if (t.sel[path]) delete t.sel[path];
        else t.sel[path] = true;
        t.anchor = path;
        paintSel();
      } else selectOnly(t, path);
      t.focus = path;
    });
    body.addEventListener("dblclick", function (ev) {
      if (isTouch()) return;
      var node = ev.target.closest("[data-path]");
      if (!node) return;
      var it = itemOf(node.dataset.path);
      if (it) activate(it);
    });
    body.addEventListener("auxclick", function (ev) {
      if (ev.button !== 1) return;
      var node = ev.target.closest("[data-path]");
      var it = node && itemOf(node.dataset.path);
      if (it && it.kind === "dir") {
        ev.preventDefault();
        openInNewTab(it.path);
      }
    });
    body.addEventListener("contextmenu", function (ev) {
      ev.preventDefault();
      if (isTouch()) return;
      var node = ev.target.closest("[data-path]");
      var t = tab();
      if (node) {
        if (!t.sel[node.dataset.path]) selectOnly(t, node.dataset.path);
        itemMenu(null, { x: ev.clientX, y: ev.clientY });
      } else {
        selectOnly(t, "");
        bgMenu({ x: ev.clientX, y: ev.clientY });
      }
    });

    // long press = selection mode (touch); rubber band (mouse)
    body.addEventListener("pointerdown", function (ev) {
      var node = ev.target.closest("[data-path]");
      if (ev.pointerType !== "mouse") {
        if (!node) return;
        press = { x: ev.clientX, y: ev.clientY, fired: false, node: node };
        press.timer = setTimeout(function () {
          if (!press) return;
          press.fired = true;
          var t = tab();
          var p = node.dataset.path;
          if (!selectMode) {
            selectMode = true;
            t.sel = {};
          }
          t.sel[p] = true;
          t.anchor = p;
          paintSel();
          if (navigator.vibrate) {
            try {
              navigator.vibrate(12);
            } catch (_) {}
          }
        }, 450);
        return;
      }
      if (ev.button !== 0 || node || ev.target.closest(".rcf-row-head")) return;
      var rect = body.getBoundingClientRect();
      band = {
        x: ev.clientX - rect.left + body.scrollLeft,
        y: ev.clientY - rect.top + body.scrollTop,
        base: ev.ctrlKey || ev.shiftKey ? Object.assign({}, tab().sel) : {},
        moved: false,
        box: null,
      };
    });
    body.addEventListener("pointermove", function (ev) {
      if (press && (Math.abs(ev.clientX - press.x) > 10 || Math.abs(ev.clientY - press.y) > 10)) {
        clearTimeout(press.timer);
        if (!press.fired) press = null;
      }
      if (!band) return;
      var rect = body.getBoundingClientRect();
      var x = ev.clientX - rect.left + body.scrollLeft;
      var y = ev.clientY - rect.top + body.scrollTop;
      if (!band.moved && Math.abs(x - band.x) < 4 && Math.abs(y - band.y) < 4) return;
      if (!band.moved) {
        band.moved = true;
        band.box = el("div", "rcf-band");
        body.appendChild(band.box);
        try {
          body.setPointerCapture(ev.pointerId);
        } catch (_) {}
      }
      var l = Math.min(x, band.x);
      var tp = Math.min(y, band.y);
      var w = Math.abs(x - band.x);
      var h = Math.abs(y - band.y);
      band.box.style.cssText = "left:" + l + "px;top:" + tp + "px;width:" + w + "px;height:" + h + "px";
      var t = tab();
      t.sel = Object.assign({}, band.base);
      body.querySelectorAll("[data-path]").forEach(function (n) {
        var r = n.getBoundingClientRect();
        var nl = r.left - rect.left + body.scrollLeft;
        var nt = r.top - rect.top + body.scrollTop;
        if (nl < l + w && nl + r.width > l && nt < tp + h && nt + r.height > tp) t.sel[n.dataset.path] = true;
      });
      paintSel();
    });
    function endPress() {
      if (press) {
        clearTimeout(press.timer);
        var fired = press.fired;
        setTimeout(function () {
          if (press && press.fired === fired) press = null;
        }, 50);
        if (!fired) press = null;
      }
      if (band) {
        var b = band;
        if (b.box) b.box.remove();
        setTimeout(function () {
          if (band === b) band = null;
        }, 0);
      }
    }
    body.addEventListener("pointerup", endPress);
    body.addEventListener("pointercancel", function () {
      if (press) clearTimeout(press.timer);
      press = null;
      if (band && band.box) band.box.remove();
      band = null;
    });
  }

  function activate(it) {
    var t = tab();
    if (t.path === "trash://") {
      selectOnly(t, it.path);
      itemMenu(null, centerOf(it.path));
      return;
    }
    if (it.kind === "dir") {
      go(it.path);
      return;
    }
    preview(it);
  }
  function centerOf(path) {
    var node = ui.body.querySelector('[data-path="' + cssEsc(path) + '"]');
    if (!node) return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    var r = node.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }

  // --- menus -----------------------------------------------------------------------------------

  function popover(anchor, point, items) {
    closeMenus();
    var layer = el("div", "rcf-layer rcf-menu-layer");
    var menu = el("div", "rcf-menu");
    menu.setAttribute("role", "menu");
    items.forEach(function (item) {
      if (!item) return;
      if (item === "-") {
        menu.appendChild(el("div", "rcf-menu-sep"));
        return;
      }
      if (item.title) {
        menu.appendChild(el("div", "rcf-menu-title", item.title));
        return;
      }
      if (item.node) {
        // A live control (a slider) that stays open while it is used.
        var holder = el("div", "rcf-menu-node");
        holder.appendChild(item.node);
        menu.appendChild(holder);
        return;
      }
      var b = el("button", "rcf-menu-item" + (item.danger ? " is-danger" : "") + (item.checked ? " is-checked" : ""));
      b.type = "button";
      b.setAttribute("role", item.checked != null ? "menuitemcheckbox" : "menuitem");
      b.innerHTML = item.icon || (item.checked != null ? (item.checked ? I.check : "<span></span>") : "<span></span>");
      b.appendChild(el("span", "rcf-menu-label", item.label));
      if (item.key && !isTouch()) b.appendChild(el("span", "rcf-menu-key", item.key));
      b.disabled = !!item.disabled;
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (!item.keep) layer.__rcClose();
        item.fn();
      });
      menu.appendChild(b);
    });
    layer.appendChild(menu);
    layer.addEventListener("mousedown", function (ev) {
      if (ev.target === layer) {
        ev.preventDefault();
        layer.__rcClose();
      }
    });
    layer.addEventListener("contextmenu", function (ev) {
      ev.preventDefault();
      if (ev.target === layer) layer.__rcClose();
    });
    layer.__rcClose = function () {
      layer.remove();
      if (openNow) ui.body.focus({ preventScroll: true });
    };
    root.appendChild(layer);
    // position
    var mw = menu.offsetWidth;
    var mh = menu.offsetHeight;
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var x;
    var y;
    if (isTouch() && !anchor) {
      menu.classList.add("is-sheet");
      return layer;
    }
    if (anchor) {
      var r = anchor.getBoundingClientRect();
      x = r.right - mw;
      y = r.bottom + 6;
      if (x < 8) x = r.left;
      if (y + mh > vh - 8) y = r.top - mh - 6;
    } else {
      x = point.x;
      y = point.y;
      if (x + mw > vw - 8) x = point.x - mw;
      if (y + mh > vh - 8) y = point.y - mh;
    }
    menu.style.left = Math.max(8, Math.min(vw - mw - 8, x)) + "px";
    menu.style.top = Math.max(8, Math.min(vh - mh - 8, y)) + "px";
    var first = menu.querySelector("button:not(:disabled)");
    if (first && !isTouch()) first.focus({ preventScroll: true });
    return layer;
  }
  function closeMenus() {
    root.querySelectorAll(".rcf-menu-layer").forEach(function (l) {
      l.remove();
    });
  }

  function itemMenu(anchor, point) {
    var t = tab();
    var items = selItems(t);
    if (!items.length) return;
    var one = items.length === 1 ? items[0] : null;
    if (t.path === "trash://") {
      popover(anchor, point, [
        { icon: I.restore, label: "Restaurar", fn: restoreSel },
        "-",
        { icon: I.trash, label: "Eliminar permanentemente", danger: true, fn: deleteForeverSel, key: "Supr" },
      ]);
      return;
    }
    var dir = one && one.kind === "dir";
    var inRecent = t.path === "recent://";
    popover(anchor, point, [
      one ? { icon: dir ? I.open : I.eye, label: dir ? "Abrir" : "Vista previa", fn: function () { activate(one); }, key: dir ? "Intro" : "Espacio" } : null,
      dir ? { icon: I.tab, label: "Abrir en pestaña nueva", fn: function () { openInNewTab(one.path); } } : null,
      one && (dir || inRecent || t.search != null)
        ? { icon: I.folder, label: dir ? "Abrir en terminal" : "Abrir carpeta contenedora", fn: function () { dir ? openTerminal(one.path) : go(parentOf(one.path), { focusName: one.name }); } }
        : null,
      { icon: I.insert, label: "Insertar ruta en la terminal", fn: insertSel },
      "-",
      { icon: I.download, label: items.length > 1 || dir ? "Descargar como .zip" : "Descargar", fn: downloadSel },
      items.every(isArchive) ? { icon: I.extract, label: "Extraer aquí", fn: extractSel } : null,
      "-",
      { icon: I.cut, label: "Cortar", fn: function () { setClip("cut"); }, key: "Ctrl+X" },
      { icon: I.copy, label: "Copiar", fn: function () { setClip("copy"); }, key: "Ctrl+C" },
      dir && clip ? { icon: I.paste, label: "Pegar en la carpeta", fn: function () { pasteInto(one.path); } } : null,
      { icon: I.copy, label: items.length > 1 ? "Copiar rutas" : "Copiar ruta", fn: copyPathText },
      "-",
      one ? { icon: I.rename, label: "Renombrar…", fn: function () { renameItem(one); }, key: "F2" } : null,
      { icon: I.trash, label: "Mover a la papelera", fn: trashSel, key: "Supr", danger: true },
      "-",
      { icon: I.info, label: "Propiedades", fn: function () { properties(one || null); } },
    ]);
  }

  function bgMenu(point) {
    var t = tab();
    var real = t.path.indexOf("://") === -1 && t.search == null;
    var writable = real && t.data && t.data.writable !== false;
    if (t.path === "trash://") {
      popover(null, point, [{ icon: I.trash, label: "Vaciar papelera", danger: true, fn: confirmEmptyTrash }]);
      return;
    }
    popover(null, point, [
      real ? { icon: I.folderPlus, label: "Carpeta nueva…", fn: newFolder, disabled: !writable, key: "Alt+N" } : null,
      real ? { icon: I.filePlus, label: "Archivo nuevo…", fn: newFile, disabled: !writable } : null,
      real ? { icon: I.upload, label: "Subir archivos…", fn: pickUpload, disabled: !writable } : null,
      real ? { icon: I.paste, label: "Pegar", fn: function () { pasteInto(t.path); }, disabled: !clip || !writable, key: "Ctrl+V" } : null,
      "-",
      { icon: I.selectAll, label: "Seleccionar todo", fn: selectAll, key: "Ctrl+A" },
      real ? { icon: I.terminal, label: "Abrir en terminal", fn: function () { openTerminal(t.path); } } : null,
      real ? { icon: I.insert, label: "Insertar ruta de esta carpeta", fn: function () { insertPaths([t.path]); } } : null,
      real ? { icon: I.download, label: "Descargar carpeta como .zip", fn: function () { downloadPaths([t.path]); } } : null,
      "-",
      { icon: I.reload, label: "Recargar", fn: reload, key: "F5" },
      real ? { icon: I.info, label: "Propiedades", fn: function () { properties({ path: t.path, name: baseName(t.path), kind: "dir" }); } } : null,
    ]);
  }

  function viewMenu(anchor) {
    popover(anchor, null, [
      { title: "Ordenar" },
      sortItem("name", "A–Z", false),
      sortItem("name", "Z–A", true),
      sortItem("mtime", "Modificados recientemente", true),
      sortItem("mtime", "Modificados antes", false),
      sortItem("size", "Tamaño", true),
      sortItem("type", "Tipo", false),
      "-",
      { label: "Mostrar archivos ocultos", checked: !!prefs.hidden, fn: toggleHidden, key: "Ctrl+H" },
      "-",
      { icon: I.plus, label: "Ampliar iconos", fn: function () { zoom(1); }, key: "Ctrl++", keep: true },
      { icon: stroke("M5 12h14"), label: "Reducir iconos", fn: function () { zoom(-1); }, key: "Ctrl+−", keep: true },
    ]);
  }
  function sortItem(key, label, desc) {
    return {
      label: label,
      checked: prefs.sort === key && !!prefs.desc === desc,
      fn: function () {
        prefs.sort = key;
        prefs.desc = desc;
        savePrefs();
        render(true);
      },
    };
  }

  function mainMenu(anchor) {
    var t = tab();
    var real = t.path.indexOf("://") === -1 && t.search == null;
    var cwd = host().cwd ? host().cwd() : "";
    popover(anchor, null, [
      // Phone header has no room for the view button; it lives here.
      isTouch()
        ? {
            icon: prefs.view === "grid" ? I.list : I.grid,
            label: prefs.view === "grid" ? "Ver como lista" : "Ver como cuadrícula",
            fn: function () { setView(prefs.view === "grid" ? "list" : "grid"); },
          }
        : null,
      isTouch() ? { icon: stroke("M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4"), label: "Ordenar y ajustes de vista…", fn: function () { viewMenu(anchor); } } : null,
      isTouch() ? "-" : null,
      { icon: I.tab, label: "Pestaña nueva", fn: function () { openInNewTab(t.path); }, key: "Alt+T" },
      real ? { icon: I.folderPlus, label: "Carpeta nueva…", fn: newFolder, key: "Alt+N" } : null,
      real ? { icon: I.filePlus, label: "Archivo nuevo…", fn: newFile } : null,
      real ? { icon: I.upload, label: "Subir archivos…", fn: pickUpload } : null,
      "-",
      real ? { icon: I.terminal, label: "Abrir en terminal", fn: function () { openTerminal(t.path); } } : null,
      cwd ? { icon: I.insert, label: "Ir a la carpeta de la terminal", fn: function () { go(cwd); } } : null,
      { icon: I.selectAll, label: "Seleccionar todo", fn: selectAll, key: "Ctrl+A" },
      { label: "Mostrar archivos ocultos", checked: !!prefs.hidden, fn: toggleHidden, key: "Ctrl+H" },
      "-",
      { icon: I.reload, label: "Recargar", fn: reload, key: "F5" },
      { icon: I.info, label: "Atajos de teclado", fn: shortcutsHelp, key: "Ctrl+?" },
      // The window's transparency, the same setting as in Themes.
      host().glassSlider ? "-" : null,
      host().glassSlider ? { node: host().glassSlider() } : null,
    ]);
  }

  // --- dialogs ----------------------------------------------------------------------------------------

  function dialog(title, opts) {
    var layer = el("div", "rcf-layer rcf-dialog-layer");
    var box = el("div", "rcf-dialog" + (opts.wide ? " is-wide" : ""));
    box.setAttribute("role", "alertdialog");
    box.appendChild(el("div", "rcf-dialog-title", title));
    if (opts.text) box.appendChild(el("div", "rcf-dialog-text", opts.text));
    if (opts.body) box.appendChild(opts.body);
    var input = null;
    var err = el("div", "rcf-dialog-err");
    if (opts.input != null) {
      input = el("input", "rcf-dialog-input");
      input.type = opts.password ? "password" : "text";
      input.value = opts.input;
      input.spellcheck = false;
      input.setAttribute("autocapitalize", "off");
      box.appendChild(input);
      box.appendChild(err);
      input.addEventListener("input", function () {
        var msg = opts.validate ? opts.validate(input.value) : "";
        err.textContent = msg || "";
        ok.disabled = !!msg || !input.value.trim();
      });
      input.__rcKey = function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          if (!ok.disabled) ok.click();
          return true;
        }
        if (ev.key === "Escape") {
          ev.preventDefault();
          layer.__rcClose();
          return true;
        }
        return false;
      };
    }
    var actions = el("div", "rcf-dialog-actions");
    var cancel = el("button", "rcf-dbtn", opts.cancel || "Cancelar");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      layer.__rcClose();
    });
    var ok = el("button", "rcf-dbtn " + (opts.danger ? "is-danger" : "is-accent"), opts.ok || "Aceptar");
    ok.type = "button";
    ok.addEventListener("click", function () {
      var value = input ? input.value.trim() : true;
      if (input && !value) return;
      ok.disabled = true;
      Promise.resolve(opts.onOk(value)).then(function (msg) {
        if (msg) {
          err.textContent = msg;
          ok.disabled = false;
          return;
        }
        layer.__rcClose();
      });
    });
    if (!opts.alert) actions.appendChild(cancel);
    actions.appendChild(ok);
    box.appendChild(actions);
    layer.appendChild(box);
    layer.addEventListener("mousedown", function (ev) {
      if (ev.target === layer) layer.__rcClose();
    });
    layer.__rcClose = function () {
      layer.remove();
      if (openNow) ui.body.focus({ preventScroll: true });
    };
    root.appendChild(layer);
    setTimeout(function () {
      if (input) {
        input.focus();
        var dot = opts.selectStem ? input.value.lastIndexOf(".") : -1;
        input.setSelectionRange(0, dot > 0 ? dot : input.value.length);
      } else ok.focus();
    }, 20);
    return layer;
  }

  function nameProblem(name, current) {
    var n = name.trim();
    if (!n) return "";
    if (n.indexOf("/") !== -1) return "El nombre no puede llevar «/»";
    if (n === "." || n === "..") return "Nombre no válido";
    var t = tab();
    var clash = (t.data ? t.data.entries : []).some(function (it) {
      return it.name === n && it.name !== current;
    });
    if (clash) return "Ya existe un elemento con ese nombre";
    if (n.charAt(0) === "." && n !== current) return "";
    return "";
  }

  function newFolder() {
    var t = tab();
    dialog("Carpeta nueva", {
      input: "Carpeta nueva",
      ok: "Crear",
      validate: function (v) {
        return nameProblem(v, "");
      },
      onOk: function (name) {
        return op({ op: "mkdir", dir: t.path, name: name }).then(function (r) {
          if (!r.ok) return r.error || "No pude crear la carpeta";
          t.focusName = baseName(r.path);
          load(t);
        });
      },
    });
  }
  function newFile() {
    var t = tab();
    dialog("Archivo nuevo", {
      input: "Archivo nuevo.txt",
      selectStem: true,
      ok: "Crear",
      validate: function (v) {
        return nameProblem(v, "");
      },
      onOk: function (name) {
        return op({ op: "newfile", dir: t.path, name: name }).then(function (r) {
          if (!r.ok) return r.error || "No pude crear el archivo";
          t.focusName = baseName(r.path);
          load(t);
        });
      },
    });
  }
  function renameItem(it) {
    var t = tab();
    dialog(it.kind === "dir" ? "Renombrar carpeta" : "Renombrar archivo", {
      input: it.name,
      selectStem: it.kind !== "dir",
      ok: "Renombrar",
      validate: function (v) {
        return nameProblem(v, it.name);
      },
      onOk: function (name) {
        return op({ op: "rename", path: it.path, name: name }).then(function (r) {
          if (!r.ok) return r.error || "No pude renombrar";
          t.focusName = baseName(r.path);
          load(t);
        });
      },
    });
  }

  function confirmEmptyTrash() {
    dialog("¿Vaciar la papelera?", {
      text: "Todos los elementos de la papelera se eliminarán para siempre.",
      ok: "Vaciar papelera",
      danger: true,
      onOk: function () {
        return op({ op: "emptyTrash" }).then(function (r) {
          if (!r.ok) return r.error;
          toast("Papelera vacía");
          load(tab());
        });
      },
    });
  }

  function properties(it) {
    var t = tab();
    var target = it || (selItems(t).length === 1 ? selItems(t)[0] : null);
    if (!target) {
      var items = selItems(t);
      var size = items.reduce(function (s, x) {
        return s + (x.size || 0);
      }, 0);
      dialog(items.length + " elementos", { text: "Tamaño de los archivos: " + fmtSize(size), alert: true, ok: "Cerrar", onOk: function () {} });
      return;
    }
    var body = el("div", "rcf-props");
    var ic = iconNode(target, null);
    ic.classList.add("rcf-props-icon");
    body.appendChild(ic);
    var grid = el("dl", "rcf-props-grid");
    body.appendChild(grid);
    function rowP(k, v) {
      if (!v && v !== 0) return;
      grid.appendChild(el("dt", null, k));
      var dd = el("dd", null, String(v));
      grid.appendChild(dd);
    }
    rowP("Ruta", target.path);
    rowP("Tipo", "Calculando…");
    var layer = dialog(target.name || target.path, { body: body, alert: true, ok: "Cerrar", wide: true, onOk: function () {} });
    op({ op: "props", path: target.path }).then(function (r) {
      if (!layer.isConnected) return;
      grid.innerHTML = "";
      if (!r.ok) {
        rowP("Error", r.error);
        return;
      }
      rowP("Ruta", r.path);
      rowP("Tipo", r.kind === "dir" ? "Carpeta" : KIND_LABEL[kindOf(target)] + " (" + r.mime + ")");
      rowP("Tamaño", r.kind === "dir" ? r.items + " elementos, " + fmtSize(r.size) : fmtSize(r.size) + " (" + r.size.toLocaleString("es") + " bytes)");
      if (r.link) rowP("Enlace a", r.link);
      rowP("Modificado", fmtFull(r.mtime));
      rowP("Accedido", fmtFull(r.atime));
      rowP("Permisos", r.mode + "  ·  " + r.owner + ":" + r.group);
      if (r.usage) rowP("Espacio libre", fmtSize(r.usage.free) + " de " + fmtSize(r.usage.total));
    });
  }

  function shortcutsHelp() {
    var body = el("dl", "rcf-props-grid rcf-keys");
    [
      ["Ctrl+L", "Escribir una ruta"],
      ["Ctrl+F o escribir", "Buscar"],
      ["Alt+← / Alt+→", "Atrás / Adelante"],
      ["Alt+↑ o Retroceso", "Carpeta superior"],
      ["Intro / Espacio", "Abrir / Vista previa"],
      ["F2", "Renombrar"],
      ["Supr / Mayús+Supr", "Papelera / Eliminar"],
      ["Ctrl+C / X / V", "Copiar / Cortar / Pegar"],
      ["Ctrl+A", "Seleccionar todo"],
      ["Ctrl+H", "Archivos ocultos"],
      ["Ctrl+1 / Ctrl+2", "Lista / Cuadrícula"],
      ["Alt+T / Alt+W", "Abrir / Cerrar pestaña"],
      ["Alt+RePág / Alt+AvPág", "Pestaña anterior / siguiente"],
      ["Alt+N", "Carpeta nueva"],
      ["F5", "Recargar"],
      ["Esc", "Cerrar"],
    ].forEach(function (r) {
      body.appendChild(el("dt", null, r[0]));
      body.appendChild(el("dd", null, r[1]));
    });
    dialog("Atajos de teclado", { body: body, alert: true, ok: "Cerrar", wide: true, onOk: function () {} });
  }

  // --- actions ------------------------------------------------------------------------------------------

  function selectAll() {
    var t = tab();
    t.sel = {};
    (t.visible || []).forEach(function (it) {
      t.sel[it.path] = true;
    });
    paintSel();
  }
  function toggleHidden() {
    prefs.hidden = !prefs.hidden;
    savePrefs();
    toast(prefs.hidden ? "Mostrando archivos ocultos" : "Archivos ocultos escondidos");
    render(true);
  }
  function setView(v) {
    prefs.view = v;
    savePrefs();
    render(true);
  }
  function zoom(d) {
    prefs.zoom = Math.max(0, Math.min(4, (prefs.zoom == null ? 2 : prefs.zoom) + d));
    applyZoom();
    savePrefs();
  }
  function applyZoom() {
    if (!root) return;
    var z = prefs.zoom == null ? 2 : prefs.zoom;
    var tile = [64, 80, 96, 128, 160][z];
    root.style.setProperty("--rcf-tile", tile + "px");
  }

  function setClip(mode) {
    var paths = selPaths();
    if (!paths.length) return;
    clip = { mode: mode, paths: paths };
    toast(
      (mode === "cut" ? "Cortado: " : "Copiado: ") + (paths.length === 1 ? "«" + baseName(paths[0]) + "»" : paths.length + " elementos")
    );
    paintCut();
  }
  function paintCut() {
    ui.body.querySelectorAll("[data-path]").forEach(function (n) {
      n.classList.toggle("is-cut", !!(clip && clip.mode === "cut" && clip.paths.indexOf(n.dataset.path) !== -1));
    });
  }
  function pasteInto(dest) {
    if (!clip) return;
    var c = clip;
    transfer(c.mode === "cut" ? "move" : "copy", c.paths, dest).then(function (ok) {
      if (ok && c.mode === "cut") clip = null;
    });
  }
  function transfer(kind, paths, dest) {
    var label = (kind === "move" ? "Moviendo " : "Copiando ") + (paths.length === 1 ? "«" + baseName(paths[0]) + "»" : paths.length + " elementos");
    var job = opCard(kind === "move" ? I.cut : I.copy, label);
    return op({ op: kind, paths: paths, dest: dest }).then(function (r) {
      if (!r.ok) {
        job.fail(r.error || "Falló");
        return false;
      }
      job.done((kind === "move" ? "Movido a " : "Copiado a ") + "«" + baseName(dest) + "»");
      var t = tab();
      if (r.paths && r.paths.length && parentOf(r.paths[0]) === t.path) {
        t.sel = {};
        r.paths.forEach(function (p) {
          t.sel[p] = true;
        });
        load(t, true);
      } else load(t);
      return true;
    });
  }

  function trashSel() {
    var t = tab();
    var paths = selPaths(t);
    if (!paths.length || t.path === "trash://") return;
    op({ op: "trash", paths: paths }).then(function (r) {
      if (r.noTrash) {
        dialog("¿Eliminar permanentemente?", {
          text: "Esta ubicación no tiene papelera. " + (paths.length === 1 ? "«" + baseName(paths[0]) + "»" : paths.length + " elementos") + " se eliminarán para siempre.",
          ok: "Eliminar",
          danger: true,
          onOk: function () {
            return op({ op: "delete", paths: paths }).then(function (d) {
              if (!d.ok) return d.error;
              load(t);
            });
          },
        });
        return;
      }
      if (!r.ok) {
        toast(r.error || "No pude moverlo a la papelera");
        return;
      }
      load(t);
      undoToast(
        (paths.length === 1 ? "«" + baseName(paths[0]) + "» se movió" : paths.length + " elementos se movieron") + " a la papelera",
        r.trashed && r.trashed.length
          ? function () {
              op({ op: "restore", uris: r.trashed }).then(function (u) {
                if (!u.ok) toast(u.error || "No pude restaurar");
                load(tab(), true);
              });
            }
          : null
      );
    });
  }
  function deletePermanentSel() {
    var t = tab();
    var paths = selPaths(t);
    if (!paths.length) return;
    if (t.path === "trash://") return deleteForeverSel();
    dialog("¿Eliminar permanentemente?", {
      text: (paths.length === 1 ? "«" + baseName(paths[0]) + "»" : paths.length + " elementos") + " se eliminarán para siempre. No se puede deshacer.",
      ok: "Eliminar",
      danger: true,
      onOk: function () {
        return op({ op: "delete", paths: paths }).then(function (d) {
          if (!d.ok) return d.error;
          load(t);
        });
      },
    });
  }
  function restoreSel() {
    var t = tab();
    var uris = selPaths(t);
    if (!uris.length) return;
    op({ op: "restore", uris: uris }).then(function (r) {
      if (!r.ok) toast(r.error || "No pude restaurar");
      else toast(uris.length === 1 ? "Restaurado" : uris.length + " elementos restaurados");
      load(t);
    });
  }
  function deleteForeverSel() {
    var t = tab();
    var uris = selPaths(t);
    if (!uris.length) return;
    dialog("¿Eliminar permanentemente?", {
      text: (uris.length === 1 ? "«" + selItems(t)[0].name + "»" : uris.length + " elementos") + " se eliminarán para siempre.",
      ok: "Eliminar",
      danger: true,
      onOk: function () {
        return op({ op: "deleteForever", uris: uris }).then(function (r) {
          if (!r.ok) return r.error;
          load(t);
        });
      },
    });
  }

  function copyPathText() {
    var text = selPaths().join("\n");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () {
          toast("Ruta copiada");
        },
        function () {
          toast("No pude copiar");
        }
      );
    }
  }

  function insertSel() {
    insertPaths(selPaths());
  }
  function insertPaths(paths) {
    if (!paths.length) return;
    var text = paths.map(shellQuote).join(" ") + " ";
    var sent = host().sendInput ? host().sendInput(text) : false;
    if (!sent) {
      toast("La terminal no está conectada");
      return;
    }
    close();
    toast(paths.length === 1 ? "Ruta insertada" : paths.length + " rutas insertadas");
  }

  function openTerminal(path) {
    op({ op: "openTab", path: path }).then(function (r) {
      if (!r.ok) {
        toast(r.error || "No pude abrir la terminal");
        return;
      }
      close();
      if (host().openTab) host().openTab(r.tab);
    });
  }

  function openInNewTab(path) {
    tabs.splice(active + 1, 0, newTab(path));
    switchTab(active + 1);
  }
  function switchTab(i) {
    if (i < 0 || i >= tabs.length) return;
    if (tab()) tab().scroll = ui.body.scrollTop;
    active = i;
    render();
    var t = tab();
    if (!t.data && !t.loading) load(t);
    savePrefs();
  }
  function closeTab(i) {
    if (tabs.length < 2) {
      close();
      return;
    }
    tabs.splice(i, 1);
    if (active >= tabs.length) active = tabs.length - 1;
    else if (i < active) active--;
    render();
    if (!tab().data) load(tab());
    savePrefs();
  }

  // --- search -------------------------------------------------------------------------------------------

  var searchTimer = 0;
  function startSearch(seed) {
    var t = tab();
    if (t.path.indexOf("://") !== -1) {
      toast("La búsqueda funciona dentro de carpetas");
      return;
    }
    if (t.search == null) {
      t.preSearch = t.data;
      t.search = seed || "";
    }
    ui.loc.classList.add("is-edit", "is-search");
    renderHead();
    ui.pathInput.value = t.search;
    ui.pathInput.focus();
    var len = ui.pathInput.value.length;
    ui.pathInput.setSelectionRange(len, len);
    if (seed) scheduleSearch();
    else {
      t.data = { path: t.path, entries: [], writable: false };
      render();
    }
  }
  function scheduleSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 250);
  }
  function runSearch() {
    var t = tab();
    if (t.search == null) return;
    var q = ui.pathInput.value;
    t.search = q;
    renderTabs();
    if (!q.trim()) {
      t.data = { path: t.path, entries: [], writable: false };
      render();
      return;
    }
    t.loading = true;
    ui.body.classList.add("is-searching");
    api("search", { path: t.path, q: q, hidden: prefs.hidden ? 1 : 0 }).then(function (data) {
      ui.body.classList.remove("is-searching");
      if (t.search !== q) return;
      t.loading = false;
      if (data.error) {
        t.error = data.error;
        t.data = null;
      } else {
        t.error = "";
        t.data = { path: t.path, entries: data.entries, truncated: data.truncated, writable: false };
      }
      t.sel = {};
      if (t === tab()) render();
    });
  }
  function endSearch(quiet) {
    var t = tab();
    if (t.search == null) return;
    t.search = null;
    t.data = t.preSearch || null;
    t.preSearch = null;
    t.error = "";
    ui.loc.classList.remove("is-edit", "is-search");
    if (!quiet) {
      render();
      if (!t.data) load(t);
      ui.body.focus({ preventScroll: true });
    }
  }

  // --- folder tree from the location pill --------------------------------------------------------------
  // The chain from / to here, the current folder open, every folder expandable
  // in place. Ancestors start trimmed to the chain; "Ver todas" lists siblings.

  var treeState = null;

  function treeList(path) {
    var node = treeState.nodes[path];
    if (node.children || node.loading) return;
    node.loading = true;
    api("list", { path: path }).then(function (data) {
      node.loading = false;
      if (!treeState || treeState.nodes[path] !== node) return;
      if (data.error) {
        node.error = data.error;
        node.children = [];
      } else {
        node.children = data.entries
          .filter(function (it) {
            return it.kind === "dir" && (prefs.hidden || !it.hidden);
          })
          .map(function (it) {
            return it.path || joinPath(data.path || path, it.name);
          })
          .sort(function (a, b) {
            return collator.compare(baseName(a), baseName(b));
          });
      }
      paintTree();
    });
  }

  function treeNode(path) {
    var nodes = treeState.nodes;
    if (!nodes[path]) nodes[path] = { mode: "closed", children: null, loading: false, error: "" };
    return nodes[path];
  }

  function treeLabel(path) {
    if (path === "/") return { name: "Sistema de archivos", icon: I.drive };
    if (places && path === places.home) return { name: "Carpeta personal", icon: I.home };
    return { name: baseName(path), icon: I.folder };
  }

  function paintTree() {
    if (!treeState) return;
    var box = treeState.list;
    var keep = box.scrollTop;
    box.innerHTML = "";
    var here = treeState.here;
    var chain = treeState.chain;
    var onChain = {};
    chain.forEach(function (p) {
      onChain[p] = true;
    });

    function row(path, depth) {
      var node = treeNode(path);
      var label = treeLabel(path);
      var r = el("div", "rcf-tree-row" + (path === here ? " is-here" : "") + (onChain[path] && path !== here ? " is-chain" : ""));
      r.style.setProperty("--depth", depth);
      var open = node.mode !== "closed";
      var tog = el("button", "rcf-tree-tog" + (open ? " is-open" : ""));
      tog.type = "button";
      tog.innerHTML = I.fwd;
      tog.setAttribute("aria-label", (open ? "Contraer " : "Expandir ") + label.name);
      if (node.children && !node.children.length && !node.error) tog.classList.add("is-empty");
      tog.addEventListener("click", function (ev) {
        ev.stopPropagation();
        node.mode = open ? "closed" : "full";
        if (!open) treeList(path);
        paintTree();
      });
      var go_ = el("button", "rcf-tree-name");
      go_.type = "button";
      go_.innerHTML = label.icon;
      go_.appendChild(el("span", null, label.name));
      if (path === here) go_.appendChild(el("span", "rcf-tree-here", "aquí"));
      var all = null;
      if (node.mode === "chain") {
        all = el("button", "rcf-tree-all", "Ver todas");
        all.type = "button";
        all.title = "Mostrar todas las carpetas de " + label.name;
        all.addEventListener("click", function (ev) {
          ev.stopPropagation();
          node.mode = "full";
          treeList(path);
          paintTree();
        });
      }
      go_.title = path;
      go_.addEventListener("click", function () {
        closeTree();
        if (path !== tab().path) go(path);
      });
      dropTarget(go_, path);
      r.appendChild(tog);
      r.appendChild(go_);
      if (all) r.appendChild(all);
      box.appendChild(r);
      if (!open) return;
      if (node.mode === "chain") {
        var next = chain[chain.indexOf(path) + 1];
        if (next) row(next, depth + 1);
        return;
      }
      if (!node.children) {
        var wait = el("div", "rcf-tree-note", "Cargando…");
        wait.style.setProperty("--depth", depth + 1);
        box.appendChild(wait);
        return;
      }
      if (node.error || !node.children.length) {
        var note = el("div", "rcf-tree-note", node.error || "Sin subcarpetas");
        note.style.setProperty("--depth", depth + 1);
        box.appendChild(note);
        return;
      }
      node.children.forEach(function (child) {
        row(child, depth + 1);
      });
    }

    row(chain[0], 0);
    box.scrollTop = keep;
    if (!treeState.scrolled) {
      var hit = box.querySelector(".is-here");
      if (hit && hit.offsetTop > box.clientHeight - 60) box.scrollTop = hit.offsetTop - 60;
      treeState.scrolled = !!box.querySelector(".is-here");
    }
  }

  function closeTree() {
    if (treeState && treeState.layer) treeState.layer.remove();
    treeState = null;
    if (openNow) ui.body.focus({ preventScroll: true });
  }

  function openTree(anchor) {
    var t = tab();
    if (t.search != null) return;
    if (t.path.indexOf("://") !== -1) {
      editPath();
      return;
    }
    closeMenus();
    closeTree();
    var chain = ["/"];
    t.path
      .split("/")
      .filter(Boolean)
      .forEach(function (seg) {
        chain.push(joinPath(chain[chain.length - 1], seg));
      });
    var layer = el("div", "rcf-layer rcf-menu-layer rcf-tree-layer");
    var panel = el("div", "rcf-tree");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Árbol de carpetas");
    var head = el("div", "rcf-tree-head");
    var where = el("div", "rcf-tree-path");
    var shown = places && places.home && (t.path === places.home || t.path.indexOf(places.home + "/") === 0) ? "~" + t.path.slice(places.home.length) : t.path;
    var pathText = el("bdi", null, shown);
    pathText.setAttribute("dir", "ltr");
    where.appendChild(pathText);
    where.title = t.path;
    var acts = el("div", "rcf-tree-acts");
    acts.appendChild(
      iconBtn(I.rename, "Escribir ruta", function () {
        closeTree();
        editPath();
      })
    );
    acts.appendChild(
      iconBtn(I.copy, "Copiar ruta", function () {
        var text = t.path;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(
            function () {
              toast("Ruta copiada");
            },
            function () {
              toast("No pude copiar");
            }
          );
        }
      })
    );
    acts.appendChild(
      iconBtn(I.terminal, "Abrir en terminal", function () {
        closeTree();
        openTerminal(t.path);
      })
    );
    head.appendChild(where);
    head.appendChild(acts);
    var list = el("div", "rcf-tree-list");
    panel.appendChild(head);
    panel.appendChild(list);
    layer.appendChild(panel);
    layer.addEventListener("mousedown", function (ev) {
      if (ev.target === layer) {
        ev.preventDefault();
        closeTree();
      }
    });
    panel.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        closeTree();
      }
    });
    layer.__rcClose = closeTree;
    root.appendChild(layer);

    treeState = { layer: layer, list: list, here: t.path, chain: chain, nodes: {}, scrolled: false };
    chain.forEach(function (p, i) {
      treeNode(p).mode = i === chain.length - 1 ? "full" : "chain";
    });
    treeList(t.path);
    paintTree();

    var loc = ui.loc.getBoundingClientRect();
    var vw = window.innerWidth;
    if (isTouch()) {
      panel.classList.add("is-touch");
      panel.style.top = Math.round(loc.bottom + 8) + "px";
    } else {
      var w = Math.min(Math.max(loc.width, 360), vw - 16);
      panel.style.width = w + "px";
      panel.style.left = Math.max(8, Math.min(vw - w - 8, Math.round(loc.left))) + "px";
      panel.style.top = Math.round(loc.bottom + 6) + "px";
    }
    var focusHere = list.querySelector(".is-here .rcf-tree-name");
    if (focusHere && !isTouch()) focusHere.focus({ preventScroll: true });
  }

  function editPath() {
    var t = tab();
    if (t.search != null) return;
    ui.loc.classList.add("is-edit");
    ui.pathInput.value = t.path.indexOf("://") === -1 ? t.path : "";
    ui.pathInput.focus();
    ui.pathInput.select();
  }
  function endEditPath() {
    ui.loc.classList.remove("is-edit");
    if (document.activeElement === ui.pathInput) ui.body.focus({ preventScroll: true });
  }

  // --- preview (like GNOME Sushi) -----------------------------------------------------------------------

  function preview(it) {
    var t = tab();
    var files = (t.visible || []).filter(function (x) {
      return x.kind !== "dir";
    });
    var idx = Math.max(0, files.indexOf(it));
    var layer = el("div", "rcf-layer rcf-preview");
    var head = el("div", "rcf-pv-head");
    var title = el("div", "rcf-pv-title");
    var name = el("div", "rcf-pv-name");
    var meta = el("div", "rcf-pv-meta");
    title.appendChild(name);
    title.appendChild(meta);
    var prev = iconBtn(I.back, "Anterior (←)", function () {
      show(idx - 1);
    });
    var next = iconBtn(I.fwd, "Siguiente (→)", function () {
      show(idx + 1);
    });
    var dl = iconBtn(I.download, "Descargar", function () {
      saveUrl(fileUrl(files[idx].path, true), files[idx].name);
    });
    var ins = iconBtn(I.insert, "Insertar ruta en la terminal", function () {
      layer.__rcClose();
      insertPaths([files[idx].path]);
    });
    var x = iconBtn(I.close, "Cerrar (Esc)", function () {
      layer.__rcClose();
    });
    head.appendChild(prev);
    head.appendChild(next);
    head.appendChild(title);
    head.appendChild(ins);
    head.appendChild(dl);
    head.appendChild(x);
    var stage = el("div", "rcf-pv-stage");
    layer.appendChild(head);
    layer.appendChild(stage);
    layer.__rcClose = function () {
      stage.innerHTML = "";
      layer.remove();
      if (openNow) ui.body.focus({ preventScroll: true });
    };
    layer.__rcKey = function (ev) {
      if (ev.key === "ArrowLeft") {
        show(idx - 1);
        return true;
      }
      if (ev.key === "ArrowRight") {
        show(idx + 1);
        return true;
      }
      if (ev.key === " ") {
        layer.__rcClose();
        return true;
      }
      return false;
    };
    stage.addEventListener("click", function (ev) {
      if (ev.target === stage) layer.__rcClose();
    });
    // swipe on phones
    var sx = null;
    stage.addEventListener("touchstart", function (ev) {
      if (ev.touches.length === 1) sx = ev.touches[0].clientX;
    }, { passive: true });
    stage.addEventListener("touchend", function (ev) {
      if (sx == null) return;
      var dx = ev.changedTouches[0].clientX - sx;
      sx = null;
      if (Math.abs(dx) > 60) show(idx + (dx < 0 ? 1 : -1));
    });
    root.appendChild(layer);

    function show(i) {
      if (i < 0 || i >= files.length) return;
      idx = i;
      var f = files[idx];
      selectOnly(t, f.path);
      name.textContent = f.name;
      meta.textContent = fmtSize(f.size) + (f.mtime ? " · " + fmtDate(f.mtime) : "") + (files.length > 1 ? " · " + (idx + 1) + " de " + files.length : "");
      prev.disabled = idx === 0;
      next.disabled = idx === files.length - 1;
      stage.innerHTML = "";
      var k = kindOf(f);
      var url = fileUrl(f.path, false);
      if (k === "image") {
        var img = el("img", "rcf-pv-img");
        img.alt = f.name;
        img.src = url;
        stage.appendChild(img);
      } else if (k === "video") {
        var v = el("video", "rcf-pv-media");
        v.controls = true;
        v.autoplay = true;
        v.playsInline = true;
        v.src = url;
        stage.appendChild(v);
      } else if (k === "audio") {
        var card = infoCard(f);
        var a = el("audio", "rcf-pv-audio");
        a.controls = true;
        a.autoplay = true;
        a.src = url;
        card.appendChild(a);
        stage.appendChild(card);
      } else if (k === "pdf") {
        var fr = el("iframe", "rcf-pv-frame");
        fr.src = url;
        fr.title = f.name;
        stage.appendChild(fr);
      } else if (k === "text" || k === "code" || (f.size < 2000000 && /^(text\/|application\/(json|xml|javascript))/.test(f.mime || ""))) {
        var pre = el("pre", "rcf-pv-text", "Cargando…");
        stage.appendChild(pre);
        api("text", { path: f.path }).then(function (r) {
          if (files[idx] !== f) return;
          if (r.error || r.binary) {
            stage.innerHTML = "";
            stage.appendChild(infoCard(f, r.error || "No hay vista previa para este archivo"));
            return;
          }
          pre.textContent = r.text + (r.truncated ? "\n\n… (se muestran los primeros 512 kB)" : "");
        });
      } else {
        stage.appendChild(infoCard(f, "No hay vista previa para este tipo de archivo"));
      }
    }
    function infoCard(f, note) {
      var card = el("div", "rcf-pv-card");
      var ic = iconNode(f, null);
      ic.classList.add("rcf-pv-icon");
      card.appendChild(ic);
      card.appendChild(el("div", "rcf-pv-card-name", f.name));
      card.appendChild(el("div", "rcf-pv-card-meta", KIND_LABEL[kindOf(f)] + " · " + fmtSize(f.size)));
      if (note) card.appendChild(el("div", "rcf-pv-card-note", note));
      var acts = el("div", "rcf-pv-card-acts");
      acts.appendChild(pill(I.download, "Descargar", function () {
        saveUrl(fileUrl(f.path, true), f.name);
      }, "is-accent"));
      acts.appendChild(pill(I.insert, "Insertar ruta", function () {
        layer.__rcClose();
        insertPaths([f.path]);
      }));
      card.appendChild(acts);
      return card;
    }
    show(idx);
  }

  // --- downloads (several items become one zip, on their own) --------------------------------------------

  function downloadSel() {
    downloadPaths(selPaths());
  }
  function downloadPaths(paths) {
    if (!paths.length) return;
    var t = tab();
    var items = paths.map(function (p) {
      return itemOf(p) || { path: p, name: baseName(p), kind: "dir" };
    });
    if (items.length === 1 && items[0].kind !== "dir") {
      saveUrl(fileUrl(items[0].path, true), items[0].name);
      toast("Descargando «" + items[0].name + "»");
      return;
    }
    var label = t.path.indexOf("://") === -1 && t.search == null ? baseName(t.path) : "Archivos";
    var job = opCard(I.download, "Comprimiendo " + (items.length === 1 ? "«" + items[0].name + "»" : items.length + " elementos") + "…");
    job.progress(-1);
    api("zip", null, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: paths, label: label }),
    }).then(function (r) {
      if (!r.ok) {
        job.fail(r.error || "No pude comprimir");
        return;
      }
      (function check() {
        api("zip-status", { token: r.token }).then(function (s) {
          if (!s.ok || s.state === "error") {
            job.fail(s.error || "No pude comprimir");
            return;
          }
          if (s.state === "working") {
            job.text("Comprimiendo… " + s.files + (s.files === 1 ? " archivo" : " archivos") + " · " + fmtSize(s.bytes));
            setTimeout(check, 500);
            return;
          }
          job.done("Descargando «" + s.name + "» (" + fmtSize(s.size) + ")");
          saveUrl("/rc-fs/zip?token=" + encodeURIComponent(r.token), s.name);
        });
      })();
    });
  }

  // --- extracting archives (unpack.py), one after another, each with its card -------------------------

  var ARCHIVE_RE = /\.(zip|jar|apk|xpi|epub|cbz|cbr|cb7|rar|7z|iso|cab|arj|lzh|lha|cpio|wim|dmg|vhdx?|msi|chm|tar|tgz|tbz2?|txz|tzst|tlz|gz|bz2|xz|lzma|zst)$|\.(zip|7z|rar)\.0*1$/i;

  function isArchive(it) {
    return !!it && it.kind !== "dir" && ARCHIVE_RE.test(it.name || "");
  }

  function extractSel() {
    var queue = selItems(tab()).filter(isArchive);
    (function next() {
      var it = queue.shift();
      if (it) extractOne(it, "").then(next);
    })();
  }

  function extractOne(it, password) {
    var folder = parentOf(it.path);
    var job = opCard(I.extract, "Extrayendo «" + it.name + "»…");
    job.progress(-1);
    return new Promise(function (resolve) {
      api("extract", null, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: it.path, password: password }),
      }).then(function (r) {
        if (!r.ok) {
          job.fail(r.error || "No pude extraerlo");
          resolve();
          return;
        }
        (function check() {
          api("extract-status", { token: r.token }).then(function (s) {
            if (!s.ok || s.state === "error" || s.state === "cancelled") {
              job.fail(s.error || "No pude extraerlo");
              resolve();
            } else if (s.state === "working") {
              job.progress(s.progress > 0 ? s.progress : -1);
              job.text("Extrayendo «" + it.name + "»… " + (s.progress > 0 ? Math.round(s.progress * 100) + "%" : s.files ? s.files + " archivos" : ""));
              setTimeout(check, 400);
            } else if (s.state === "password") {
              job.fail(s.error);
              askArchivePassword(it, !!password).then(function (pw) {
                if (pw) extractOne(it, pw).then(resolve);
                else resolve();
              });
            } else {
              var name = baseName(s.path);
              job.done("Extraído en «" + name + "»" + (s.skipped ? " (" + s.skipped + " omitidos por inseguros)" : ""));
              var t = tab();
              if (t.path === folder) {
                t.focusName = name;
                load(t);
              }
              resolve();
            }
          });
        })();
      });
    });
  }

  function askArchivePassword(it, retry) {
    return new Promise(function (resolve) {
      var answered = false;
      var layer = dialog(retry ? "Contraseña incorrecta" : "«" + it.name + "» tiene contraseña", {
        text: retry ? "Prueba otra vez." : "Escríbela para extraerlo.",
        input: "",
        password: true,
        ok: "Extraer",
        onOk: function (pw) {
          answered = true;
          resolve(pw);
        },
      });
      var close = layer.__rcClose;
      layer.__rcClose = function () {
        close();
        if (!answered) resolve("");
      };
    });
  }

  // --- uploads -------------------------------------------------------------------------------------------

  var CHUNK = 8 * 1024 * 1024;
  var uploadInput = null;

  function pickUpload() {
    var t = tab();
    if (!uploadInput) {
      uploadInput = el("input");
      uploadInput.type = "file";
      uploadInput.multiple = true;
      uploadInput.style.display = "none";
      root.appendChild(uploadInput);
      uploadInput.addEventListener("change", function () {
        var list = Array.prototype.slice.call(uploadInput.files || []);
        uploadInput.value = "";
        if (list.length) uploadFiles(list, uploadInput.__dest || tab().path);
      });
    }
    uploadInput.__dest = t.path;
    uploadInput.click();
  }

  function uploadFiles(list, dest) {
    if (!dest || dest.indexOf("://") !== -1) {
      toast("Abre una carpeta para subir archivos");
      return;
    }
    var queue = list.slice();
    (function nextFile() {
      var file = queue.shift();
      if (!file) {
        var t = tab();
        if (t.path === dest) load(t, true);
        return;
      }
      uploadOne(file, dest).then(nextFile);
    })();
  }

  function uploadOne(file, dest, sub, quiet) {
    var id = Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
    // A folder's files share one card (uploadTree): theirs only report errors.
    var job = quiet || opCard(I.upload, "Subiendo «" + file.name + "»");
    var total = file.size;
    var offset = 0;
    return new Promise(function (resolve) {
      (function send() {
        var end = Math.min(total, offset + CHUNK);
        var blob = file.slice(offset, end);
        fetch(
          "/rc-fs/upload?" +
            [
              "dir=" + encodeURIComponent(dest),
              "name=" + encodeURIComponent(file.name),
              "offset=" + offset,
              "total=" + total,
              "id=" + id,
              "sub=" + encodeURIComponent(sub || ""),
            ].join("&"),
          { method: "PUT", body: blob, credentials: "same-origin", cache: "no-store" }
        )
          .then(function (resp) {
            return resp.json().catch(function () {
              return { ok: false, error: "Error " + resp.status };
            });
          })
          .then(
            function (r) {
              if (!r.ok) {
                job.fail(r.error || "Falló la subida");
                resolve(false);
                return;
              }
              offset = end;
              job.progress(total ? offset / total : 1);
              job.text("Subiendo «" + file.name + "» · " + fmtSize(offset) + " de " + fmtSize(total));
              if (r.done) {
                job.done("Subido «" + (r.name || file.name) + "»");
                var t = tab();
                if (t.path === dest && r.name) t.focusName = r.name;
                resolve(true);
                return;
              }
              send();
            },
            function () {
              job.fail("Sin conexión");
              resolve(false);
            }
          );
      })();
    });
  }

  // --- folders dropped from the PC ------------------------------------------------
  // dataTransfer.files lists a folder as an unreadable empty "file". Its
  // entries (webkitGetAsEntry) walk the tree: the folder is made here (a free
  // name if taken) and every file goes to its place inside it.
  function readAll(dirEntry) {
    var reader = dirEntry.createReader();
    var all = [];
    return new Promise(function (resolve) {
      (function more() {
        reader.readEntries(
          function (batch) {
            if (!batch.length) return resolve(all);
            all = all.concat(Array.prototype.slice.call(batch));
            more();
          },
          function () {
            resolve(all);
          }
        );
      })();
    });
  }

  function walk(entry, sub, out) {
    if (entry.isFile) {
      return new Promise(function (resolve) {
        entry.file(
          function (file) {
            out.files.push({ file: file, sub: sub });
            resolve();
          },
          function () {
            out.unreadable++;
            resolve();
          }
        );
      });
    }
    var here = sub ? sub + "/" + entry.name : entry.name;
    out.dirs.push(here);
    return readAll(entry).then(function (children) {
      return children.reduce(function (chain, child) {
        return chain.then(function () {
          return walk(child, here, out);
        });
      }, Promise.resolve());
    });
  }

  function uploadTree(dirEntry, dest) {
    var job = opCard(I.upload, "Leyendo «" + dirEntry.name + "»…");
    var out = { files: [], dirs: [], unreadable: 0 };
    var failed = 0;
    return op({ op: "mkdir", dir: dest, name: dirEntry.name })
      .then(function (made) {
        if (!made || !made.ok) throw new Error((made && made.error) || "No pude crear la carpeta");
        var root = made.path;
        var shown = root.split("/").pop();
        // The tree is read from inside the folder: its own name is root's.
        return readAll(dirEntry)
          .then(function (children) {
            return children.reduce(function (chain, child) {
              return chain.then(function () {
                return walk(child, "", out);
              });
            }, Promise.resolve());
          })
          .then(function () {
            // Empty folders too, in order (parents first).
            return out.dirs.reduce(function (chain, sub) {
              return chain.then(function () {
                return op({ op: "mkdirs", dir: root, sub: sub });
              });
            }, Promise.resolve());
          })
          .then(function () {
            var total = out.files.length;
            var done = 0;
            var quiet = {
              progress: function () {},
              text: function () {},
              done: function () {},
              fail: function () {
                failed++;
              },
            };
            job.text("Subiendo carpeta «" + shown + "» · 0 de " + total + " archivos");
            return out.files
              .reduce(function (chain, item) {
                return chain.then(function () {
                  return uploadOne(item.file, root, item.sub, quiet).then(function () {
                    done++;
                    job.progress(total ? done / total : 1);
                    job.text("Subiendo carpeta «" + shown + "» · " + done + " de " + total + " archivos");
                  });
                });
              }, Promise.resolve())
              .then(function () {
                var lost = failed + out.unreadable;
                if (lost) job.fail("«" + shown + "»: " + lost + " de " + (total + out.unreadable) + " archivos no se subieron");
                else job.done("Subida la carpeta «" + shown + "» (" + total + " archivos)");
                var t = tab();
                if (t.path === dest) t.focusName = shown;
              });
          });
      })
      .catch(function (err) {
        job.fail(err.message || "Falló la subida");
      });
  }

  function uploadDropped(dataTransfer, dest) {
    if (!dest || dest.indexOf("://") !== -1) {
      toast("Abre una carpeta para subir archivos");
      return;
    }
    var items = Array.prototype.slice.call(dataTransfer.items || []);
    // Entries must be taken during the drop event itself.
    var entries = items
      .map(function (item) {
        return item.kind === "file" && item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
      })
      .filter(Boolean);
    if (!entries.length) {
      uploadFiles(Array.prototype.slice.call(dataTransfer.files || []), dest);
      return;
    }
    var files = [];
    var dirs = [];
    var chain = Promise.resolve();
    entries.forEach(function (entry) {
      if (entry.isDirectory) dirs.push(entry);
      else
        chain = chain.then(function () {
          return new Promise(function (resolve) {
            entry.file(
              function (file) {
                files.push(file);
                resolve();
              },
              function () {
                resolve();
              }
            );
          });
        });
    });
    dirs.forEach(function (entry) {
      chain = chain.then(function () {
        return uploadTree(entry, dest);
      });
    });
    chain.then(function () {
      if (files.length) uploadFiles(files, dest);
      else {
        var t = tab();
        if (t.path === dest) load(t, true);
      }
    });
  }

  function bootDrop() {
    var depth = 0;
    function hasFiles(ev) {
      var types = ev.dataTransfer && ev.dataTransfer.types;
      return types && Array.prototype.indexOf.call(types, "Files") !== -1 && !dragPaths;
    }
    root.addEventListener("dragenter", function (ev) {
      if (!hasFiles(ev)) return;
      ev.preventDefault();
      depth++;
      ui.drop.classList.add("is-on");
    });
    root.addEventListener("dragover", function (ev) {
      if (hasFiles(ev)) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "copy";
      }
    });
    root.addEventListener("dragleave", function (ev) {
      if (!hasFiles(ev)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) ui.drop.classList.remove("is-on");
    });
    root.addEventListener("drop", function (ev) {
      if (!hasFiles(ev)) return;
      ev.preventDefault();
      depth = 0;
      ui.drop.classList.remove("is-on");
      var target = ev.target.closest && ev.target.closest("[data-drop]");
      var dest = target ? target.dataset.drop : tab().path;
      uploadDropped(ev.dataTransfer, dest);
    });
  }

  // Internal drag: items onto a folder, a path crumb or a sidebar place.
  function dropTarget(node, path) {
    node.dataset.drop = path;
    node.addEventListener("dragover", function (ev) {
      if (!dragPaths) return;
      if (dragPaths.indexOf(path) !== -1) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = ev.ctrlKey ? "copy" : "move";
      node.classList.add("is-drop");
    });
    node.addEventListener("dragleave", function () {
      node.classList.remove("is-drop");
    });
    node.addEventListener("drop", function (ev) {
      node.classList.remove("is-drop");
      if (!dragPaths) return;
      ev.preventDefault();
      ev.stopPropagation();
      var paths = dragPaths.filter(function (p) {
        return p !== path;
      });
      dragPaths = null;
      if (paths.length) transfer(ev.ctrlKey ? "copy" : "move", paths, path);
    });
  }

  // --- operations panel and toasts ------------------------------------------------------------------------

  function opCard(icon, text) {
    var card = el("div", "rcf-op");
    var ic = el("span", "rcf-op-icon");
    ic.innerHTML = icon;
    var label = el("span", "rcf-op-text", text);
    var bar = el("span", "rcf-op-bar");
    var fill = el("span");
    bar.appendChild(fill);
    card.appendChild(ic);
    card.appendChild(label);
    card.appendChild(bar);
    ui.ops.appendChild(card);
    ui.ops.classList.add("is-on");
    function finish(cls, msg, wait) {
      card.classList.add(cls);
      label.textContent = msg;
      fill.style.width = "100%";
      setTimeout(function () {
        card.classList.add("is-gone");
        setTimeout(function () {
          card.remove();
          if (!ui.ops.children.length) ui.ops.classList.remove("is-on");
        }, 250);
      }, wait);
    }
    return {
      text: function (t) {
        label.textContent = t;
      },
      progress: function (p) {
        card.classList.toggle("is-busy", p < 0);
        if (p >= 0) fill.style.width = Math.round(p * 100) + "%";
      },
      done: function (msg) {
        card.classList.remove("is-busy");
        finish("is-done", msg, 2600);
      },
      fail: function (msg) {
        card.classList.remove("is-busy");
        finish("is-fail", msg, 6000);
      },
    };
  }

  function undoToast(text, undo) {
    var card = el("div", "rcf-op rcf-undo");
    card.appendChild(el("span", "rcf-op-text", text));
    if (undo) {
      var b = el("button", "rcf-pill", "Deshacer");
      b.type = "button";
      b.addEventListener("click", function () {
        card.remove();
        undo();
      });
      card.appendChild(b);
    }
    var x = iconBtn(I.close, "Cerrar", function () {
      card.remove();
    });
    card.appendChild(x);
    ui.ops.appendChild(card);
    ui.ops.classList.add("is-on");
    setTimeout(function () {
      card.remove();
      if (!ui.ops.children.length) ui.ops.classList.remove("is-on");
    }, 8000);
  }

  // --- keyboard (runs before the terminal's own handlers) -------------------------------------------------

  function onKey(ev) {
    if (!openNow) return;
    var target = ev.target;
    // Inputs inside the explorer (path, dialogs) keep native typing.
    if (target && target.__rcKey) {
      if (target.__rcKey(ev)) ev.stopImmediatePropagation();
      else ev.stopImmediatePropagation();
      return;
    }
    if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) && root.contains(target)) {
      ev.stopImmediatePropagation();
      return;
    }
    ev.stopImmediatePropagation();
    var layers = root.querySelectorAll(".rcf-layer");
    var layer = layers.length ? layers[layers.length - 1] : null;
    var k = ev.key;
    var ctrl = ev.ctrlKey || ev.metaKey;
    if (k === "Escape") {
      ev.preventDefault();
      if (layer) closeLayer();
      else if (tab().search != null) endSearch();
      else if (ui.loc.classList.contains("is-edit")) endEditPath();
      else if (selItems().length && !isTouch()) selectOnly(tab(), "");
      else close();
      return;
    }
    if (layer) {
      if (layer.__rcKey && layer.__rcKey(ev)) {
        ev.preventDefault();
        return;
      }
      if (layer.classList.contains("rcf-menu-layer") && (k === "ArrowDown" || k === "ArrowUp")) {
        ev.preventDefault();
        var btns = Array.prototype.slice.call(layer.querySelectorAll("button:not(:disabled)"));
        var i = btns.indexOf(document.activeElement);
        var n = btns[(i + (k === "ArrowDown" ? 1 : -1) + btns.length) % btns.length];
        if (n) n.focus();
      }
      return;
    }
    var t = tab();
    var handled = true;
    if (ctrl && (k === "l" || k === "L")) editPath();
    else if (ctrl && (k === "f" || k === "F")) startSearch("");
    else if (ctrl && (k === "h" || k === "H")) toggleHidden();
    else if (ctrl && (k === "a" || k === "A")) selectAll();
    else if (ctrl && (k === "c" || k === "C")) setClip("copy");
    else if (ctrl && (k === "x" || k === "X")) setClip("cut");
    else if (ctrl && (k === "v" || k === "V")) {
      if (t.path.indexOf("://") === -1 && t.search == null) pasteInto(t.path);
    }
    // Chrome keeps Ctrl+T, Ctrl+W, Ctrl+Tab and Ctrl+Shift+N for itself in a
    // normal tab, so the explorer's own are on Alt (by key position, so they
    // also work where Alt types symbols). The Ctrl ones still work when the
    // browser lets them through (installed app, fullscreen).
    else if ((ev.altKey && !ctrl && ev.code === "KeyT") || (ctrl && (k === "t" || k === "T"))) openInNewTab(t.path);
    else if ((ev.altKey && !ctrl && ev.code === "KeyW") || (ctrl && (k === "w" || k === "W"))) closeTab(active);
    else if ((ev.altKey && k === "PageDown") || (ctrl && k === "Tab" && !ev.shiftKey)) switchTab((active + 1) % tabs.length);
    else if ((ev.altKey && k === "PageUp") || (ctrl && k === "Tab" && ev.shiftKey)) switchTab((active - 1 + tabs.length) % tabs.length);
    else if ((ev.altKey && !ctrl && ev.code === "KeyN") || (ctrl && ev.shiftKey && (k === "N" || k === "n"))) newFolder();
    else if (ctrl && k === "1") setView("list");
    else if (ctrl && k === "2") setView("grid");
    else if (ctrl && (k === "+" || k === "=")) zoom(1);
    else if (ctrl && (k === "-" || k === "_")) zoom(-1);
    else if (ctrl && (k === "?" || k === "/")) shortcutsHelp();
    else if ((ctrl && (k === "r" || k === "R")) || k === "F5") reload();
    else if (ev.altKey && k === "ArrowLeft") goBack();
    else if (ev.altKey && k === "ArrowRight") goFwd();
    else if ((ev.altKey && k === "ArrowUp") || (k === "Backspace" && !ctrl)) goUp();
    else if (k === "F2") {
      var one = selItems();
      if (one.length === 1) renameItem(one[0]);
    } else if (k === "Delete" && ev.shiftKey) deletePermanentSel();
    else if (k === "Delete") {
      if (t.path === "trash://") deleteForeverSel();
      else trashSel();
    } else if (k === "Enter") {
      var sel = selItems();
      if (sel.length === 1) activate(sel[0]);
      else if (sel.length > 1)
        sel
          .filter(function (it) {
            return it.kind === "dir";
          })
          .forEach(function (it) {
            openInNewTab(it.path);
          });
    } else if (k === " " && !ctrl) {
      var s = selItems();
      if (s.length === 1 && s[0].kind !== "dir") preview(s[0]);
    } else if (k === "ArrowRight" && !ev.altKey) moveSel(1, 0, ev.shiftKey);
    else if (k === "ArrowLeft" && !ev.altKey) moveSel(-1, 0, ev.shiftKey);
    else if (k === "ArrowDown") moveSel(0, 1, ev.shiftKey);
    else if (k === "ArrowUp") moveSel(0, -1, ev.shiftKey);
    else if (k === "Home") moveSel(-1e6, 0, ev.shiftKey);
    else if (k === "End") moveSel(1e6, 0, ev.shiftKey);
    else if (!ctrl && !ev.altKey && k.length === 1 && /\S/.test(k)) startSearch(k);
    else handled = false;
    if (handled) ev.preventDefault();
    else if (!root.contains(target)) ev.preventDefault(); // nothing may reach the terminal
  }

  // Keeps the terminal page from seeing anything typed while the explorer is
  // open. Text fields of the explorer still get their own input events.
  function guard(ev) {
    if (!openNow) return;
    var inside = ev.target && ev.target.nodeType === 1 && root.contains(ev.target);
    if (ev.type === "input" || ev.type === "beforeinput") {
      if (inside) {
        if (ev.type === "beforeinput") ev.stopImmediatePropagation();
        return;
      }
      ev.stopImmediatePropagation();
      if (ev.type === "beforeinput" && ev.cancelable) ev.preventDefault();
      return;
    }
    if (ev.type === "paste") {
      var target = ev.target;
      ev.stopImmediatePropagation();
      if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName)) return;
      var files = ev.clipboardData && ev.clipboardData.files;
      var t = tab();
      if (files && files.length && t.path.indexOf("://") === -1 && t.search == null) {
        ev.preventDefault();
        uploadFiles(Array.prototype.slice.call(files), t.path);
      }
      return;
    }
    ev.stopImmediatePropagation();
  }

  window.addEventListener("keydown", onKey, true);
  ["keyup", "keypress", "beforeinput", "input", "paste", "copy", "cut"].forEach(function (type) {
    window.addEventListener(type, guard, true);
  });
  window.addEventListener("resize", function () {
    if (openNow) closeMenus();
  });

  window.__rcFiles = {
    open: open,
    close: close,
    isOpen: function () {
      return openNow;
    },
    takePrefs: takePrefs,
  };
})();
