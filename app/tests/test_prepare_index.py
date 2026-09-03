import unittest

from remote_control.tunnel import TunnelService


class PrepareIndexTests(unittest.TestCase):
    def test_fonts_are_hashed_urls_not_base64(self) -> None:
        svc = TunnelService()
        svc.ensure_dirs()
        svc._prepare_rc_assets()
        svc._prepare_ttyd_index()
        html = svc.ttyd_index.read_text(encoding="utf-8")
        self.assertNotIn("data:font", html)
        self.assertNotIn("jsdelivr.net", html)
        self.assertIn("/rc-assets/cache.js", html)
        if svc.font_reg_url:
            self.assertIn(svc.font_reg_url, html)
            self.assertTrue((svc.assets_dir / svc.font_reg_url.rsplit("/", 1)[-1]).is_file())
        css = svc._font_css()
        self.assertIn("font-display:swap", css)
        self.assertIn('rel="preload"', css)
        self.assertNotIn("font-display:optional", css)
        self.assertNotIn("base64", css)
        self.assertIn("ui-monospace", css)
        self.assertIn("rc-touch-boot", html)
        self.assertIn("cache.js?v=1.3.4", html)
        self.assertIn("function bootTypeToTty", html)
        self.assertIn("function flushTyped", html)
        self.assertIn("rc-extra-keys-js", html)
        self.assertIn("rc-extra-keys-css", html)
        self.assertIn("function sendPaste", html)
        self.assertIn("/rc-paste-reserve", html)
        self.assertIn("/rc-paste-file", html)
        self.assertIn("rc-ek-paste", html)
        self.assertIn("Pegar archivo", html)
        self.assertNotIn("mountPasteChip", html)
        self.assertNotIn("rc-paste-catcher", html)
        manifest = svc.assets_dir / "manifest.json"
        self.assertTrue(manifest.is_file())
        text = manifest.read_text(encoding="utf-8")
        self.assertIn("fonts", text)
        self.assertIn("mobileFonts", text)
        if svc.font_reg_url:
            self.assertIn(svc.font_reg_url, text)
            self.assertNotIn("RC Mono", css)
