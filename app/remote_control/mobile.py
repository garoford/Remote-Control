"""Mobile UA detection, index rewrite, and a small terminal woff2."""

from __future__ import annotations

import re
from pathlib import Path

MOBILE_UA_RE = re.compile(
    r"iPhone|iPod|iPad|Android|Mobile|Silk|Kindle|Tablet|Mobi",
    re.I,
)

# Latin + box drawing + blocks + arrows. No Nerd Font private-use glyphs.
MOBILE_UNICODES = (
    list(range(0x20, 0x7F))
    + list(range(0xA0, 0x100))
    + list(range(0x2190, 0x2200))
    + list(range(0x2500, 0x2600))
)

DESKTOP_STACK = (
    "'FiraCode Nerd Font Mono',ui-monospace,'Cascadia Mono',"
    "'Courier New',monospace"
)
MOBILE_STACK = (
    "'RC Mono',ui-monospace,'Cascadia Mono','SF Mono',Menlo,Consolas,monospace"
)

TOUCH_BOOT_JS = (
    "(function(){try{"
    "var ua=navigator.userAgent||'';"
    "var mobile=false;"
    "try{mobile=!!(navigator.userAgentData&&navigator.userAgentData.mobile);}catch(e){}"
    "if(mobile||/Mobi|Android|iPhone|iPad|iPod/i.test(ua)||(navigator.maxTouchPoints||0)>1)"
    "{document.documentElement.classList.add('rc-touch');}"
    "}catch(e){}})();"
)


def request_is_mobile(headers: dict[str, str]) -> bool:
    ch = (headers.get("sec-ch-ua-mobile") or "").strip()
    if ch == "?1":
        return True
    ua = headers.get("user-agent") or ""
    return bool(MOBILE_UA_RE.search(ua))


def subset_mobile_woff2(src: Path, dst: Path) -> bool:
    if not src.is_file():
        return False
    if (
        dst.is_file()
        and dst.stat().st_size > 0
        and dst.stat().st_mtime >= src.stat().st_mtime
    ):
        return True
    try:
        from fontTools.subset import Options, Subsetter
        from fontTools.ttLib import TTFont
    except ImportError:
        return False
    try:
        options = Options()
        options.flavor = "woff2"
        options.desubroutinize = True
        font = TTFont(str(src))
        subsetter = Subsetter(options=options)
        subsetter.populate(unicodes=MOBILE_UNICODES)
        subsetter.subset(font)
        font.flavor = "woff2"
        dst.parent.mkdir(parents=True, exist_ok=True)
        font.save(str(dst))
        return dst.is_file() and dst.stat().st_size > 0
    except Exception:
        return False


def mobile_font_css(font_url: str) -> str:
    preload = ""
    faces = ""
    if font_url:
        preload = (
            f'<link id="rc-font-preload-mobile" rel="preload" as="font" '
            f'type="font/woff2" crossorigin href="{font_url}">'
        )
        faces = (
            "@font-face{font-family:'RC Mono';font-style:normal;font-weight:400;"
            f"font-display:swap;src:url('{font_url}') format('woff2');}}"
        )
    return (
        f"{preload}"
        '<style id="cf-remote-theme-mobile">'
        f"{faces}"
        "html,body,.xterm,.xterm-viewport,.xterm-rows,.xterm-screen,"
        f".xterm-helper-textarea{{font-family:{MOBILE_STACK}!important;}}"
        "</style>"
    )


def _strip_void(html: str, tag: str, ident: str) -> str:
    token = f'<{tag} id="{ident}"'
    start = html.find(token)
    if start < 0:
        return html
    end = html.find(">", start)
    if end < 0:
        return html
    return html[:start] + html[end + 1 :]


def _strip_tagged(html: str, tag: str, ident: str) -> str:
    open_tag = f'<{tag} id="{ident}"'
    start = html.find(open_tag)
    if start < 0:
        return html
    close = f"</{tag}>"
    end = html.find(close, start)
    if end < 0:
        return html
    return html[:start] + html[end + len(close) :]


def rewrite_index_for_mobile(html: str, mobile_font_url: str) -> str:
    """Drop desktop Nerd Font preloads/faces and inject the small mobile stack."""
    html = _strip_void(html, "link", "rc-font-preload-reg")
    html = _strip_void(html, "link", "rc-font-preload-bold")
    html = _strip_void(html, "link", "rc-font-preload-mobile")
    html = _strip_tagged(html, "style", "cf-remote-theme")
    html = _strip_tagged(html, "style", "cf-remote-theme-mobile")
    inject = mobile_font_css(mobile_font_url)
    if "<head>" in html:
        return html.replace("<head>", "<head>" + inject, 1)
    return inject + html


def manifest_for_client(manifest: dict, mobile: bool) -> dict:
    desktop = list(manifest.get("fonts") or [])
    small = list(manifest.get("mobileFonts") or [])
    return {"fonts": small if mobile else desktop, "mobileFonts": small}
