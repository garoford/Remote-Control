import tempfile
import unittest
from pathlib import Path

from remote_control.mobile import (
    manifest_for_client,
    request_is_mobile,
    rewrite_index_for_mobile,
    subset_mobile_woff2,
)


class MobileUaTests(unittest.TestCase):
    def test_sec_ch_ua_mobile(self) -> None:
        self.assertTrue(request_is_mobile({"sec-ch-ua-mobile": "?1"}))
        self.assertFalse(request_is_mobile({"sec-ch-ua-mobile": "?0"}))

    def test_iphone_ua(self) -> None:
        self.assertTrue(
            request_is_mobile(
                {
                    "user-agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                        "Mobile/15E148 Safari/604.1"
                    )
                }
            )
        )

    def test_android_ua(self) -> None:
        self.assertTrue(
            request_is_mobile(
                {
                    "user-agent": (
                        "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Mobile Safari/537.36"
                    )
                }
            )
        )

    def test_desktop_ua(self) -> None:
        self.assertFalse(
            request_is_mobile(
                {
                    "user-agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                    )
                }
            )
        )


class RewriteIndexTests(unittest.TestCase):
    def test_mobile_rewrite_drops_nerd_preload(self) -> None:
        html = (
            "<html><head>"
            '<link id="rc-font-preload-reg" rel="preload" as="font" '
            'href="/rc-assets/font-regular-aaa.woff2">'
            '<link id="rc-font-preload-bold" rel="preload" as="font" '
            'href="/rc-assets/font-bold-bbb.woff2">'
            '<style id="cf-remote-theme">@font-face{font-family:\'FiraCode Nerd Font Mono\'}</style>'
            "</head><body>tty</body></html>"
        )
        out = rewrite_index_for_mobile(html, "/rc-assets/font-mobile-ccc.woff2")
        self.assertNotIn("rc-font-preload-reg", out)
        self.assertNotIn("rc-font-preload-bold", out)
        self.assertNotIn("font-regular-aaa", out)
        self.assertNotIn("FiraCode Nerd Font Mono", out)
        self.assertIn("rc-font-preload-mobile", out)
        self.assertIn("/rc-assets/font-mobile-ccc.woff2", out)
        self.assertIn("RC Mono", out)
        self.assertIn("ui-monospace", out)
        self.assertIn("font-display:swap", out)

    def test_manifest_picks_mobile_fonts(self) -> None:
        man = {
            "fonts": ["/rc-assets/font-regular-aaa.woff2"],
            "mobileFonts": ["/rc-assets/font-mobile-ccc.woff2"],
        }
        self.assertEqual(
            manifest_for_client(man, True)["fonts"],
            ["/rc-assets/font-mobile-ccc.woff2"],
        )
        self.assertEqual(
            manifest_for_client(man, False)["fonts"],
            ["/rc-assets/font-regular-aaa.woff2"],
        )


class SubsetFontTests(unittest.TestCase):
    def test_subset_is_small_when_ttf_exists(self) -> None:
        src = Path.home() / ".local/share/fonts/FiraCode/FiraCodeNerdFontMono-Regular.ttf"
        bundled = Path(__file__).resolve().parents[1] / "remote_control" / "web" / "font-mobile.woff2"
        if not src.is_file() and not bundled.is_file():
            self.skipTest("no source font")
        if src.is_file():
            with tempfile.TemporaryDirectory() as tmp:
                dst = Path(tmp) / "mobile.woff2"
                self.assertTrue(subset_mobile_woff2(src, dst))
                self.assertLess(dst.stat().st_size, 200_000)
                self.assertGreater(dst.stat().st_size, 10_000)
        if bundled.is_file():
            self.assertLess(bundled.stat().st_size, 200_000)
            self.assertGreater(bundled.stat().st_size, 10_000)
            self.assertEqual(bundled.suffix, ".woff2")
