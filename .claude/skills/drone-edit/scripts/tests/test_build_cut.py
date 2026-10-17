"""build-cut.py on a fake project (clips.json + beats.json only, no media): cuts land on bars, a dissolve borrows
source before the cut, and every kind of bad spec is refused with a readable reason."""
import os, unittest
from .helpers import load, run, save, scratch

BAR = 2.0                                                        # 120 BPM, 4/4


def project(test):
    d = scratch(test)
    save(os.path.join(d, "clips.json"), {"lut": None, "clips": [{"id": "A", "duration": 20.0}, {"id": "B", "duration": 20.0},
                                                                 {"id": "C", "duration": 6.0}]})
    save(os.path.join(d, "beats.json"), {"file": os.path.join(d, "music", "m.mp3"), "duration": 120.0, "bpm": 120.0,
                                         "beats": [0.5 * i for i in range(240)], "bars": [BAR * i + 0.25 for i in range(60)],
                                         "energy": [], "sections": []})
    return d


def spec(*shots, start=2, vertical=None):
    s = {"music_start_bar": start, "shots": [dict(clip=c, bars=b, **{"in": i}, **kw) for c, i, b, kw in shots]}
    if vertical:
        s["vertical"] = vertical
    return s


class BuildCut(unittest.TestCase):
    def build(self, d, sp, *extra):
        save(os.path.join(d, "cut-spec.json"), sp)
        return run("build-cut.py", "--project", d, "--title", "T", *extra)

    def test_cuts_on_bars_and_dissolve_overlap(self):
        d = project(self)
        r = self.build(d, spec(("A", 2.0, 2, {"speed": 0.5}), ("B", 3.0, 1, {"transition": ["fade", 0.8]}), ("A", 10.0, 2, {})))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        C = load(os.path.join(d, "cutlist.json"))
        self.assertEqual([s["at"] for s in C["shots"]], [0.0, 4.0, 6.0])
        self.assertAlmostEqual(C["duration"], 10.0)
        self.assertAlmostEqual(C["music"]["start"], 4.25)            # bar 2 of the track
        a, b, c = C["shots"]
        self.assertAlmostEqual(a["dur"], 4.4)                        # 2 bars + half the dissolve
        self.assertAlmostEqual(b["dur"], 2.4)
        self.assertAlmostEqual(b["in"], 2.6)                         # the dissolve starts 0.4 s before the cut
        self.assertEqual(b["transition"], {"type": "fade", "dur": 0.8})
        self.assertAlmostEqual(sum(s["dur"] for s in C["shots"]) - 0.8, C["duration"])

    def test_refusals(self):
        d = project(self)
        cases = [
            (spec(("A", 2.0, 2, {}), ("A", 8.0, 2, {})), "twice in a row"),
            (spec(("A", 2.0, 2, {}), ("B", 2.0, 1, {}), ("A", 3.0, 2, {})), "same moment twice"),
            (spec(("A", 1.0, 1, {}), ("B", 1.0, 1, {}), ("A", 5.0, 1, {}), ("B", 5.0, 1, {}), ("A", 9.0, 1, {})), "used 3 times"),
            (spec(("C", 1.0, 3, {})), "too short"),
            (spec(("A", 0.2, 1, {})), "the clip allows"),
            (spec(("A", 2.0, 1, {"speed": 0.25})), "speed"),
            (spec(("A", 2.0, 1, {}), ("B", 2.0, 1, {"transition": ["spin", 1]})), "transition 'spin'"),
            (spec(("Z", 2.0, 1, {})), "unknown clip"),
            (spec(("A", 2.0, 2, {}), start=58), "past the track"),
        ]
        for sp, why in cases:
            r = self.build(d, sp)
            self.assertEqual(r.returncode, 1, why + ": " + r.stdout)
            self.assertIn(why, r.stdout)

    def test_max_length_is_a_ceiling(self):
        d = project(self)
        sp = spec(("A", 2.0, 3, {}), ("B", 2.0, 3, {}))              # 12 s
        self.assertEqual(self.build(d, sp, "--max", 12).returncode, 0)
        r = self.build(d, sp, "--max", 10)
        self.assertEqual(r.returncode, 1)
        self.assertIn("longer than the 10 s maximum", r.stdout)

    def test_vertical(self):
        d = project(self)
        sp = spec(("A", 2.0, 2, {}), vertical={"music_start_bar": 10, "shots": [{"clip": "B", "in": 2.0, "bars": 1, "pan": [0.7, 0.4]},
                                                                                {"clip": "A", "in": 9.0, "bars": 1}]})
        r = self.build(d, sp)
        self.assertEqual(r.returncode, 0, r.stdout)
        V = load(os.path.join(d, "cutlist.json"))["vertical"]
        self.assertAlmostEqual(V["music"]["start"], 20.25)
        self.assertEqual(V["shots"][0]["pan"], [0.7, 0.4])
        self.assertEqual(V["shots"][1]["x"], 0.5)
        self.assertAlmostEqual(V["duration"], 4.0)
