"""Fast checks with no media: the grade filter string and the title/end cards (cards need Chrome, skipped without it)."""
import os, subprocess, unittest
import cards, common
from .helpers import HAS_FFMPEG, scratch


class Grade(unittest.TestCase):
    def test_lut_or_fallback(self):
        self.assertIn("lut3d=file='/x/a\\:b.cube'", common.grade_vf("/x/a:b.cube"))
        fb = common.grade_vf(None)
        self.assertNotIn("lut3d", fb)
        self.assertIn("curves", fb)

    def test_look_zero_is_neutral(self):
        vf = common.grade_vf("/l.cube", {"contrast": 0, "saturation": 0, "vibrance": 0, "warmth": 0, "vignette": 0})
        self.assertEqual(vf, "format=gbrp16le,lut3d=file='/l.cube':interp=tetrahedral,format=yuv420p10le")

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg not on PATH")
    def test_filters_run(self):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=0.2,format=yuv420p10le",
                        "-vf", common.grade_vf(None, {"warmth": 1, "vignette": 1}), "-f", "null", "-"], check=True)


@unittest.skipUnless(cards.chrome() and HAS_FFMPEG, "Chrome or ffmpeg missing")
class Cards(unittest.TestCase):
    def test_sizes_and_alpha(self):
        d = scratch(self)
        for kind, size in (("title", (640, 360)), ("end", (360, 640))):
            out = cards.render_card(kind, "Héllo <b>", "sub · line", os.path.join(d, kind + ".png"), *size)
            p = common.probe(out)
            self.assertEqual((p["width"], p["height"]), size)
            self.assertEqual(p["pix_fmt"], "rgba")
