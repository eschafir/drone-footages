"""Mixed sources and the real-estate style: colour profile detection (D-Log M / Rec.709 / HLG), build-cut's style and
source rules, plan-cut in the real-estate style, and a render with a portrait 30 fps phone clip, a room label and a
listing end card with a logo."""
import os, shutil, unittest
import cards, common
from .helpers import ffmpeg, load, make_footage, make_music, needs_ffmpeg, probe_frames, run, save, scratch
from .test_build_cut import BAR, project, spec


def make_phone_clip(path, size="720x1280", rate=30, seconds=8, vf=""):
    """A phone-like clip: a texture panned slowly, any size and rate, 8-bit."""
    w, h = (int(v) for v in size.split("x"))
    ffmpeg("-f", "lavfi", "-i", "color=c=0x808070:s=%dx%d,noise=alls=60:allf=u,gblur=sigma=2" % (w + 400, h + 100), "-frames:v", 1, path + ".png")
    ffmpeg("-loop", 1, "-framerate", rate, "-t", seconds, "-i", path + ".png",
           "-vf", "crop=%d:%d:x='100+n':y=50,format=yuv420p%s" % (w, h, "," + vf if vf else ""), "-c:v", "libx264", "-preset", "ultrafast", path)
    os.remove(path + ".png")


@needs_ffmpeg
class Profiles(unittest.TestCase):
    def test_detect(self):
        d = scratch(self)
        flat = os.path.join(d, "DJI_2026_0001_D.MP4")              # D-Log M-like: grey, blacks lifted, 10-bit, from DJI
        ffmpeg("-f", "lavfi", "-i", "color=c=0x8a8a88:s=320x180,noise=c0s=40:allf=u", "-t", 2, "-r", 30,
               "-vf", "format=yuv420p10le", "-c:v", "libx264", "-preset", "ultrafast", flat)
        normal = os.path.join(d, "DJI_2026_0002_D.MP4")            # the same camera in its normal profile: colour, deep blacks
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30", "-t", 2, "-vf", "format=yuv420p10le", "-c:v", "libx264", "-preset", "ultrafast", normal)
        phone_flat = os.path.join(d, "IMG_0003.MOV")              # pale but not DJI: never D-Log M
        shutil.copy(flat, phone_flat)
        hlg = os.path.join(d, "IMG_0004.MOV")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30", "-t", 2,
               "-vf", "format=yuv420p10le,setparams=color_trc=arib-std-b67:color_primaries=bt2020:colorspace=bt2020nc",
               "-c:v", "libx264", "-preset", "ultrafast", hlg)
        self.assertEqual(common.detect_profile(flat)[0], "dlogm")
        self.assertEqual(common.detect_profile(normal)[0], "rec709")
        self.assertEqual(common.detect_profile(phone_flat)[0], "rec709")
        self.assertEqual(common.detect_profile(hlg)[0], "hlg")

    def test_grades_per_profile(self):
        self.assertNotIn("lut3d", common.grade_vf("/l.cube", None, "rec709"))
        self.assertIn("hlg-to-rec709.cube", common.grade_vf("/l.cube", None, "hlg"))
        self.assertIn("/l.cube", common.grade_vf("/l.cube", None, "dlogm"))
        self.assertEqual(common.grade_vf("/l.cube", {"vignette": 1, "warmth": 1}, "graded"), "format=yuv420p10le")   # untouched
        # the generated cube is a valid lut3d input
        ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=0.2,format=yuv420p10le",
               "-vf", common.grade_vf(None, None, "pq"), "-f", "null", "-")


    def test_graded_clips_pass_through(self):
        """--profile graded: the proxy (and so the render) keeps the source colour; auto would add the light look."""
        import numpy as np, subprocess
        d = scratch(self)
        raw = os.path.join(d, "raw")
        os.makedirs(raw)
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30", "-t", 2, "-vf", "format=yuv420p,setparams=colorspace=bt709",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "1", os.path.join(raw, "DJI_2026_0005_D.MP4"))

        def rgb(path):
            out = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", path, "-frames:v", "1", "-vf", "scale=96:54",
                                  "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
            return np.frombuffer(out, np.uint8).astype(float)
        src = rgb(os.path.join(raw, "DJI_2026_0005_D.MP4"))
        diffs = {}
        for prof in ("graded", "rec709"):
            p = os.path.join(d, prof)
            r = run("ingest.py", "--raw", raw, "--project", p, "--profile", prof)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(load(os.path.join(p, "clips.json"))["clips"][0]["profile"], prof)
            diffs[prof] = float(np.abs(rgb(os.path.join(p, "proxies", "0005.mp4")) - src).mean())
        self.assertLess(diffs["graded"], 3.0, diffs)                  # only compression noise
        self.assertGreater(diffs["rec709"], diffs["graded"] + 2, diffs)  # the look visibly changes it


class StyleRules(unittest.TestCase):
    """build-cut.py with --style realestate, on the fake project of test_build_cut (bars of 2 s)."""
    def build(self, d, sp, *extra):
        save(os.path.join(d, "cut-spec.json"), sp)
        return run("build-cut.py", "--project", d, "--title", "T", "--style", "realestate", *extra)

    def project(self):
        d = project(self)
        C = load(os.path.join(d, "clips.json"))
        C["clips"] += [{"id": "P", "duration": 20.0, "fps": 30.0, "orientation": "portrait"},
                       {"id": "S", "duration": 20.0, "fps": 29.97}]
        save(os.path.join(d, "clips.json"), C)
        return d

    def test_refusals(self):
        d = self.project()
        cases = [
            (spec(("A", 2.0, 2, {}), ("B", 2.0, 2, {"transition": ["zoomin", 0.4]})), "not used in the realestate style"),
            (spec(("A", 2.0, 1, {}), ("B", 2.0, 2, {})), "too short for the realestate style"),
            (spec(("S", 2.0, 2, {"speed": 0.5})), "30 fps: slow motion"),
            (spec(("P", 2.0, 2, {})), "portrait clip"),
            (spec(("A", 2.0, 2, {"speed": 0.5}), ("B", 2.0, 2, {"speed": 0.5}), ("A", 10.0, 2, {"speed": 0.5})), "3 slow-motion shots"),
            (spec(("A", 2.0, 2, {"label": "A very long label that does not fit"})), "longer than 28"),
        ]
        for sp, why in cases:
            r = self.build(d, sp)
            self.assertEqual(r.returncode, 1, why)
            self.assertIn(why, r.stdout, why)

    def test_drone_style_is_unchanged(self):
        d = self.project()
        sp = spec(("A", 2.0, 1, {}), ("B", 2.0, 1, {"transition": ["zoomin", 0.4]}))
        save(os.path.join(d, "cut-spec.json"), sp)
        r = run("build-cut.py", "--project", d, "--title", "T")
        self.assertEqual(r.returncode, 0, r.stdout)
        C = load(os.path.join(d, "cutlist.json"))
        self.assertEqual(C["end"]["sub"], "Shot on DJI Mini 4 Pro")
        self.assertEqual(C["look"], common.DEFAULT_LOOK)

    def test_labels_portrait_in_vertical_and_listing(self):
        d = self.project()
        sp = spec(("A", 2.0, 2, {"label": "Kitchen"}), ("B", 2.0, 2, {"transition": ["fade", 1.0]}),
                  vertical={"music_start_bar": 10, "shots": [{"clip": "P", "in": 2.0, "bars": 2, "label": "Primary suite"}]})
        r = self.build(d, sp, "--labels", "--end-line", "$1,250,000", "--end-line=---", "--end-line", "Jane Doe")
        self.assertEqual(r.returncode, 0, r.stdout)
        C = load(os.path.join(d, "cutlist.json"))
        self.assertEqual(C["shots"][0]["label"], "Kitchen")
        self.assertEqual(C["vertical"]["shots"][0]["label"], "Primary suite")
        self.assertTrue(C["labels"])
        self.assertEqual(C["end"]["lines"], ["$1,250,000", "---", "Jane Doe"])
        self.assertEqual(C["end"]["sub"], "")
        self.assertEqual(C["look"], common.load_style("realestate")["look"])
        r = self.build(d, sp)                                      # without --labels they are dropped
        self.assertNotIn("label", load(os.path.join(d, "cutlist.json"))["shots"][0])


@needs_ffmpeg
class MixedSources(unittest.TestCase):
    """Two 60p drone clips plus a portrait 30 fps phone clip, ingested and cut in the real-estate style."""
    @classmethod
    def setUpClass(cls):
        cls.d = scratch()
        cls.p = os.path.join(cls.d, "p")
        raw = os.path.join(cls.d, "raw")
        make_footage(raw, n=2, shaky=())
        make_phone_clip(os.path.join(raw, "IMG_0099.MOV"))
        os.makedirs(os.path.join(cls.p, "music"))
        make_music(os.path.join(cls.p, "music", "track.wav"), bpm=90, seconds=40)
        for script, *a in (("ingest.py", "--raw", raw, "--project", cls.p, "--style", "realestate"),
                           ("analyze.py", "--project", cls.p, "--style", "realestate"),
                           ("beats.py", "--music", os.path.join(cls.p, "music", "track.wav"))):
            r = run(script, *a)
            assert r.returncode == 0, script + r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, True)

    def test_ingest_records_the_phone_clip(self):
        c = {c["id"]: c for c in load(os.path.join(self.p, "clips.json"))["clips"]}["0099"]
        self.assertEqual((c["width"], c["height"], c["orientation"]), (720, 1280, "portrait"))
        self.assertEqual(c["profile"], "rec709")
        self.assertEqual(c["proxy_box"][3], 540)                   # letterboxed: full height, narrow
        self.assertLess(c["proxy_box"][2], 400)
        with open(os.path.join(self.p, "ANALYSIS.md")) as f:
            self.assertIn("| 0099 |", f.read())

    def test_plan_cut_realestate(self):
        r = run("plan-cut.py", "--project", self.p, "--max", 30, "--vertical-max", 20, "--style", "realestate", "--title", "T", "--end", "E")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        C = load(os.path.join(self.p, "cutlist.json"))
        types = {s["transition"]["type"] for s in C["shots"]}
        self.assertNotIn("zoomin", types)
        for s in C["shots"][:-1]:
            self.assertGreaterEqual(s["dur"] - s["transition"]["dur"] / 2, 2.5 - 0.3, s)
        self.assertNotIn("0099", [s["clip"] for s in C["shots"]])        # portrait: vertical only
        for s in C["shots"] + C["vertical"]["shots"]:
            if s["clip"] == "0099":
                self.assertEqual(s["speed"], 1.0)

    @unittest.skipUnless(cards.chrome(), "Chrome missing")
    def test_render_label_portrait_listing(self):
        logo = os.path.join(self.d, "logo.png")
        ffmpeg("-f", "lavfi", "-i", "color=c=0x1d4ed8:s=200x80", "-frames:v", 1, logo)
        bar = load(os.path.join(self.p, "beats.json"))
        sp = {"music_start_bar": 0,
              "shots": [{"clip": "0010", "in": 1.5, "bars": 2, "label": "Kitchen"},
                        {"clip": "0020", "in": 1.5, "bars": 2, "transition": ["fade", 1.0]}],
              "vertical": {"music_start_bar": 4, "shots": [{"clip": "0099", "in": 1.0, "bars": 2, "label": "Primary suite"},
                                                           {"clip": "0010", "in": 5.0, "bars": 1, "x": 0.3}]}}
        save(os.path.join(self.p, "cut-spec.json"), sp)
        r = run("build-cut.py", "--project", self.p, "--style", "realestate", "--labels", "--title", "12 Ocean Dr", "--sub", "Hollywood",
                "--end", "12 Ocean Dr", "--end-line", "$1,250,000", "--end-line=---", "--end-line", "Jane Doe", "--logo", logo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cl = os.path.join(self.p, "cutlist.json")
        for extra, res in (([], "640x360"), (["--vertical"], "360x640")):
            out = os.path.join(self.p, "out", "re%s.mp4" % len(extra))
            r = run("render.py", "--cutlist", cl, "--res", res, "--out", out, *extra)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            T = load(os.path.splitext(out)[0] + ".timeline.json")
            self.assertEqual(len(T["cards"]), 3)                      # title, end, one label
            st = probe_frames(out)
            self.assertEqual("%sx%s" % (st["width"], st["height"]), res)
            self.assertEqual(int(st["nb_frames"]), T["frames"])
            v = run("verify.py", "--render", out)
            self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
