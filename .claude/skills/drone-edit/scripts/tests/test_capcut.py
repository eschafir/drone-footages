"""capcut.py on synthetic media: the draft's shots tile the film with dissolves cut in their middle, every reference
in it resolves, a rerun replaces its own draft but never one made in CapCut, and it will not write while CapCut runs."""
import os, shutil, stat, subprocess, sys, unittest
from .helpers import SCRIPTS, load, make_footage, make_music, needs_ffmpeg, run, save, scratch
import capcut, common


def fake_bin(d, name, code):
    path = os.path.join(d, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\nexit %d\n" % code)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return d


@needs_ffmpeg
class CapCut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = scratch()
        raw = os.path.join(cls.d, "raw")
        make_footage(raw, n=2, shaky=())
        make_music(os.path.join(cls.d, "track.wav"), seconds=20)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, True)

    def project(self, **cut):
        p = scratch(self)
        raw = os.path.join(self.d, "raw")
        clips = [{"id": "00%d0" % k, "src": os.path.join(raw, "DJI_2026_00%d0_D.MP4" % k), "duration": 10.0, "fps": 59.94,
                  "width": 1280, "height": 720, "profile": "dlogm"} for k in (1, 2)]
        save(os.path.join(p, "clips.json"), {"clips": clips})
        C = {"project": "t", "fps": 30, "width": 3840, "height": 2160, "fade_in": 1.0, "fade_out": 1.5,
             "music": {"file": os.path.join(self.d, "track.wav"), "start": 2.0, "fade_in": 0.3, "fade_out": 2.0},
             "shots": [{"clip": "0010", "in": 1.0, "dur": 3.0, "speed": 1.0, "transition": {"type": "cut", "dur": 0}},
                       {"clip": "0020", "in": 2.0, "dur": 3.0, "speed": 0.5, "transition": {"type": "fade", "dur": 0.6}},
                       {"clip": "0010", "in": 5.0, "dur": 2.5, "speed": 1.0, "transition": {"type": "cut", "dur": 0}}]}
        C.update(cut)
        return p, save(os.path.join(p, "cutlist.json"), C)

    def export(self, cl, drafts, *extra):
        return run("capcut.py", "--cutlist", cl, "--drafts", drafts, *extra)

    def test_draft_tiles_the_film_and_resolves(self):
        p, cl = self.project()
        drafts = scratch(self)
        r = self.export(cl, drafts)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        folder = os.path.join(drafts, "t")
        info = load(os.path.join(folder, "draft_info.json"))
        frames, tframes, starts, total_f = common.frame_timeline(load(cl)["shots"])
        self.assertEqual(info["duration"], capcut.us(total_f))
        video, audio = [t for t in info["tracks"] if t["type"] == "video"], [t for t in info["tracks"] if t["type"] == "audio"]
        segs = video[0]["segments"]
        self.assertEqual(len(segs), 3)
        end = 0
        for seg in segs:
            self.assertEqual(seg["target_timerange"]["start"], end)       # no gap, no overlap
            end += seg["target_timerange"]["duration"]
        self.assertEqual(end, capcut.us(total_f))
        self.assertEqual(segs[1]["target_timerange"]["start"], capcut.us(starts[1] + tframes[1] // 2))   # the dissolve's middle
        self.assertEqual(segs[1]["speed"], 0.5)
        self.assertAlmostEqual(segs[1]["source_timerange"]["duration"], segs[1]["target_timerange"]["duration"] * 0.5, delta=2)
        self.assertEqual(segs[0]["source_timerange"]["start"], 1000000)
        ids = {m["id"] for coll in info["materials"].values() if isinstance(coll, list) for m in coll}
        for t in info["tracks"]:
            for seg in t["segments"]:
                self.assertIn(seg["material_id"], ids)
                self.assertTrue(set(seg["extra_material_refs"]) <= ids)
        vids = {m["id"]: m for m in info["materials"]["videos"]}
        self.assertEqual(len(vids), 2)                                    # one material per source file
        self.assertTrue(all(os.path.exists(m["path"]) for m in vids.values()))
        music = audio[0]["segments"][0]
        self.assertEqual(music["source_timerange"]["start"], 2000000)
        self.assertEqual(load(os.path.join(folder, "Timelines", info["id"], "draft_info.json"))["id"], info["id"])
        root = load(os.path.join(drafts, "root_meta_info.json"))
        self.assertEqual([e["draft_fold_path"] for e in root["all_draft_store"]], [folder])
        with open(os.path.join(p, "out", "capcut", "CUT-SHEET.md")) as f:
            sheet = f.read()
        self.assertIn("a fade (0.6 s)", sheet)
        self.assertIn("fade in from black", sheet)

    def test_rerun_replaces_its_own_draft_only(self):
        p, cl = self.project()
        drafts = scratch(self)
        os.makedirs(os.path.join(drafts, "t"))                          # a CapCut project that happens to share the name
        open(os.path.join(drafts, "t", "draft_info.json"), "w").close()
        for _ in range(2):
            r = self.export(cl, drafts)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(os.listdir(drafts)), ["root_meta_info.json", "t", "t 2"])
        self.assertEqual(os.path.getsize(os.path.join(drafts, "t", "draft_info.json")), 0)
        self.assertEqual(len(load(os.path.join(drafts, "root_meta_info.json"))["all_draft_store"]), 1)

    def test_refuses_while_capcut_runs(self):
        p, cl = self.project()
        drafts = scratch(self)
        env = dict(os.environ, CAPCUT_DRAFTS=drafts, PATH=fake_bin(scratch(self), "pgrep", 0) + os.pathsep + os.environ["PATH"])
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "capcut.py"), "--cutlist", cl], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, capcut.EXIT_RUNNING, r.stdout + r.stderr)
        self.assertIn("Quit CapCut", r.stdout)
        self.assertEqual(os.listdir(drafts), [])
        self.assertTrue(os.path.exists(os.path.join(p, "out", "capcut", "CUT-SHEET.md")))   # the fallback is still there

    def test_vertical_fill(self):
        scale, x = capcut.fill(1080, 1920, 3840, 2160, 0.5)
        self.assertAlmostEqual(scale, (16 / 9) / (9 / 16), places=4)
        self.assertEqual(x, 0.0)
        self.assertGreater(capcut.fill(1080, 1920, 3840, 2160, 0.0)[1], 0)   # the left edge in view: the frame moves right
        self.assertEqual(capcut.fill(3840, 2160, 1920, 1080), (1.0, 0.0))


if __name__ == "__main__":
    unittest.main()
