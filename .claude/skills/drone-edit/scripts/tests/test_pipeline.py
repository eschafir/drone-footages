"""The pipeline on synthetic media: analyze flags a shaky stretch, beats finds the tempo and the drop, plan-cut keeps
its timing invariants, render is frame exact and passes verify, verify catches a black flash. The project is built
once (ingest + analyze + beats) and shared, since the 60p sources take a few seconds to make."""
import os, shutil, unittest
from .helpers import ffmpeg, load, make_footage, make_music, needs_ffmpeg, probe_frames, run, save, scratch


@needs_ffmpeg
class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = scratch()
        cls.p = os.path.join(cls.d, "p")
        make_footage(os.path.join(cls.d, "raw"))
        os.makedirs(os.path.join(cls.p, "music"))
        make_music(os.path.join(cls.p, "music", "track.wav"))
        for script, *a in (("ingest.py", "--raw", os.path.join(cls.d, "raw"), "--project", cls.p),
                           ("analyze.py", "--project", cls.p),
                           ("beats.py", "--music", os.path.join(cls.p, "music", "track.wav"))):
            r = run(script, *a)
            assert r.returncode == 0, script + r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, True)

    def plan(self, *extra):
        r = run("plan-cut.py", "--project", self.p, "--target", 16, "--vertical-target", 8, "--title", "T", "--end", "E", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return load(os.path.join(self.p, "cutlist.json"))

    def test_ingest(self):
        clips = load(os.path.join(self.p, "clips.json"))["clips"]
        self.assertEqual([c["id"] for c in clips], ["0010", "0020", "0030"])
        self.assertTrue(all(os.path.exists(os.path.join(self.p, c["proxy"])) for c in clips))

    def test_analyze_flags_the_shake(self):
        a = load(os.path.join(self.p, "analysis.json"))["clips"]
        shaky = [w for w in a["0030"]["windows"] if w["t"] >= 5.5]
        steady = [w for w in a["0030"]["windows"] if 1.0 <= w["t"] <= 4.0]
        self.assertTrue(all(w["smooth"] < 0.1 for w in shaky), shaky)
        self.assertGreater(min(w["smooth"] for w in steady), 0.25)
        for c in a["0030"]["candidates"]:
            self.assertLessEqual(c["out"], 5.6)
        self.assertTrue(a["0010"]["candidates"])
        self.assertTrue(os.path.exists(os.path.join(self.p, "sheets", "overview.png")))

    def test_beats(self):
        b = load(os.path.join(self.p, "beats.json"))
        self.assertAlmostEqual(b["bpm"], 120, delta=1)
        gaps = [b["beats"][i + 1] - b["beats"][i] for i in range(len(b["beats"]) - 1)]
        self.assertTrue(all(abs(g - 0.5) < 0.04 for g in gaps))
        self.assertTrue(any(abs(s[0] - 12) < 1.1 and s[1] > 0 for s in b["sections"]), b["sections"])

    def test_plan_cut_invariants(self):
        C = self.plan()
        clips = {c["id"]: c for c in load(os.path.join(self.p, "clips.json"))["clips"]}
        for seq in (C, C["vertical"]):
            shots = seq["shots"]
            overlap = sum(s["transition"]["dur"] for s in shots[1:])
            self.assertAlmostEqual(sum(s["dur"] for s in shots) - overlap, seq["duration"], delta=0.01)
            for a, b in zip(shots, shots[1:]):
                self.assertNotEqual(a["clip"], b["clip"])
            for s in shots:
                self.assertLessEqual(s["in"] + s["dur"] * s["speed"], clips[s["clip"]]["duration"] + 0.01)
        beats = load(os.path.join(self.p, "beats.json"))["beats"]
        for s in C["shots"][1:]:
            self.assertLess(min(abs(s["at"] - b) for b in beats), 0.01)       # every cut on a beat (the film starts at 0)
        self.assertIn("zoomin", [s["transition"]["type"] for s in C["shots"]])   # the drop gets the whip

    def test_plan_cut_max_is_a_ceiling_without_repeats(self):
        C = self.plan("--max", 13, "--max-uses", 1)
        self.assertLessEqual(C["duration"], 13.01)
        clips = [s["clip"] for s in C["shots"]]
        self.assertEqual(len(clips), len(set(clips)))                  # three clips, each used once at most

    def test_render_is_frame_exact_and_verifies(self):
        self.plan()
        cl = os.path.join(self.p, "cutlist.json")
        for extra, res in (([], "320x180"), (["--vertical"], "180x320")):
            out = os.path.join(self.p, "out", "t%s.mp4" % len(extra))
            r = run("render.py", "--cutlist", cl, "--res", res, "--out", out, *extra)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            T = load(os.path.splitext(out)[0] + ".timeline.json")
            planned = load(cl)["vertical"]["duration"] if extra else load(cl)["duration"]
            self.assertLessEqual(abs(T["frames"] - planned * 30), 1, "length drifted from the beat grid")
            st = probe_frames(out)
            self.assertEqual(int(st["nb_frames"]), T["frames"])
            self.assertEqual("%sx%s" % (st["width"], st["height"]), res)
            v = run("verify.py", "--render", out)
            self.assertEqual(v.returncode, 0, v.stdout + v.stderr)

    def test_cut_then_dissolve(self):
        """A hard cut followed by a dissolve: concat's output timebase once broke the next xfade."""
        C = self.plan()
        C["shots"] = [dict(s, transition=t) for s, t in zip(C["shots"][:3], ({"type": "cut", "dur": 0}, {"type": "cut", "dur": 0},
                                                                            {"type": "fade", "dur": 0.4}))]
        cl = save(os.path.join(self.p, "cut-then-fade.json"), C)
        out = os.path.join(self.p, "out", "cut-then-fade.mp4")
        r = run("render.py", "--cutlist", cl, "--res", "320x180", "--out", out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(int(probe_frames(out)["nb_frames"]), load(os.path.splitext(out)[0] + ".timeline.json")["frames"])

    def test_verify_catches_a_black_flash(self):
        d = scratch(self)
        out = os.path.join(d, "bad.mp4")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
               "-vf", "drawbox=enable='between(n,60,62)':c=black:t=fill,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
               "-colorspace", "bt709", "-c:a", "aac", "-shortest", out)
        save(os.path.join(d, "bad.timeline.json"), {"width": 320, "height": 180, "fps": 30, "frames": 120, "duration": 4.0, "preview": True,
                                                   "lufs": None, "black_ok": [], "cuts": [{"start": 0, "end": 4.0}]})
        v = run("verify.py", "--render", out)
        self.assertEqual(v.returncode, 1, v.stdout + v.stderr)
        self.assertIn("| black | **FAIL**", v.stdout)
