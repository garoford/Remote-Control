"""Mobile UA detection, index rewrite, and font URL picking."""

from __future__ import annotations

import json
import re
from pathlib import Path

MOBILE_UA_RE = re.compile(
    r"iPhone|iPod|iPad|Android|Mobile|Silk|Kindle|Tablet|Mobi",
    re.I,
)

# Last-resort subset if Regular is missing. Latin + boxes + arrows.
MOBILE_UNICODES = (
    list(range(0x20, 0x7F))
    + list(range(0xA0, 0x100))
    + list(range(0x2190, 0x2200))
    + list(range(0x2500, 0x2600))
    + list(range(0xE0A0, 0xE0D5))
)

DESKTOP_STACK = (
    "'FiraCode Nerd Font Mono',ui-monospace,'Cascadia Mono',"
    "'Courier New',monospace"
)
# Same family as ttyd/xterm. System mono first in JS until the woff2 is ready.
MOBILE_STACK = (
    "'FiraCode Nerd Font Mono',ui-monospace,'Cascadia Mono',"
    "'SF Mono',Menlo,Consolas,monospace"
)
SYSTEM_STACK = "ui-monospace,'SF Mono',Menlo,Consolas,'Courier New',monospace"

TOUCH_BOOT_JS = (
    "(function(){try{"
    "var ua=navigator.userAgent||'';"
    "var mobile=false;"
    "var fine=false;"
    "try{mobile=!!(navigator.userAgentData&&navigator.userAgentData.mobile);}catch(e){}"
    "try{fine=window.matchMedia('(hover: hover) and (pointer: fine)').matches;}catch(e){}"
    "if(fine)return;"
    "if(mobile||/Mobi|Android|iPhone|iPad|iPod/i.test(ua))"
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


def _first_url(values: object) -> str:
    if not isinstance(values, list):
        return ""
    for item in values:
        text = str(item or "").strip()
        if text.startswith("/rc-assets/") and text.endswith(".woff2"):
            return text
    return ""


def pick_mobile_font_url(manifest: dict, assets: Path | None = None) -> str:
    """Prefer Regular Nerd Font. Never return empty if a woff2 exists on disk."""
    regular = ""
    for url in manifest.get("fonts") or []:
        text = str(url or "")
        if "font-regular-" in text or "font-mobile-" in text:
            regular = text
            break
    if not regular:
        regular = _first_url(manifest.get("fonts"))
    chosen = regular or _first_url(manifest.get("mobileFonts"))
    if chosen:
        return chosen
    if assets is not None and assets.is_dir():
        for pattern in ("font-regular-*.woff2", "font-mobile-*.woff2"):
            found = sorted(assets.glob(pattern))
            if found:
                return f"/rc-assets/{found[0].name}"
    return ""


def mobile_font_css(font_url: str) -> str:
    preload = ""
    faces = ""
    stack = MOBILE_STACK if font_url else SYSTEM_STACK
    if font_url:
        preload = (
            f'<link id="rc-font-preload-reg" rel="preload" as="font" '
            f'type="font/woff2" crossorigin href="{font_url}">'
        )
        faces = (
            "@font-face{font-family:'FiraCode Nerd Font Mono';font-style:normal;"
            f"font-weight:400;font-display:swap;src:url('{font_url}') "
            "format('woff2');}"
        )
    return (
        f"{preload}"
        '<style id="cf-remote-theme-mobile">'
        f"{faces}"
        "html,body,.xterm,.xterm-viewport,.xterm-rows,.xterm-screen,"
        f".xterm-helper-textarea{{font-family:{stack}!important;}}"
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
    """Keep Regular Nerd Font, drop Bold, never leave a family with no @font-face."""
    fallback = mobile_font_url
    if not fallback:
        match = re.search(
            r"""href=["'](/rc-assets/font-(?:regular|mobile)-[^"']+\.woff2)["']""",
            html,
        )
        if match:
            fallback = match.group(1)
    html = _strip_void(html, "link", "rc-font-preload-reg")
    html = _strip_void(html, "link", "rc-font-preload-bold")
    html = _strip_void(html, "link", "rc-font-preload-mobile")
    html = _strip_tagged(html, "style", "cf-remote-theme")
    html = _strip_tagged(html, "style", "cf-remote-theme-mobile")
    inject = mobile_font_css(fallback)
    if "<head>" in html:
        return html.replace("<head>", "<head>" + inject, 1)
    return inject + html


def manifest_for_client(
    manifest: dict, mobile: bool, assets: Path | None = None
) -> dict:
    desktop = list(manifest.get("fonts") or [])
    small = [url for url in [pick_mobile_font_url(manifest, assets)] if url]
    return {"fonts": small if mobile else desktop, "mobileFonts": small}


def load_manifest(assets: Path) -> dict:
    path = assets / "manifest.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}
