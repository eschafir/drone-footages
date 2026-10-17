"""Drone Studio end to end, on synthetic media: the API (validation, uploads, Range, the localhost guard), and whole
jobs run by the real pipeline with the automatic editor and with a stand-in `claude` CLI (one that writes a good cut,
one that writes a bad cut and must fall back). Run from the project folder:
  python3 -m unittest discover studio/tests"""
import http.client, json, os, shutil, stat, subprocess, sys, tempfile, threading, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp(prefix="drone-studio-test-")
os.environ["DRONE_STUDIO_JOBS"] = os.path.join(TMP, "jobs")
sys.path.insert(0, os.path.join(ROOT, "studio"))
sys.path.insert(0, os.path.join(ROOT, ".claude", "skills", "drone-edit", "scripts"))
import jobs, server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
from tests.helpers import HAS_FFMPEG, make_footage, make_music  # noqa: E402  (the skill's synthetic media)

GOOD = {"music_start_bar": 0, "shots": [{"clip": "0010", "in": 1.0, "bars": 2, "note": "open"},
                                        {"clip": "0020", "in": 2.0, "bars": 2, "transition": ["fade", 0.6]},
                                        {"clip": "0030", "in": 1.0, "bars": 1}]}
BAD = {"music_start_bar": 0, "shots": [{"clip": "0010", "in": 1.0, "bars": 2}, {"clip": "0010", "in": 5.0, "bars": 2}]}
# real estate: two-bar shots (bars are 2 s, the style wants 2.5 s at least), dissolves, a room label
RE_GOOD = {"music_start_bar": 0, "shots": [{"clip": "0010", "in": 1.0, "bars": 2, "note": "aerial", "label": "Pool"},
                                           {"clip": "0020", "in": 1.0, "bars": 2, "transition": ["fade", 1.0]},
                                           {"clip": "0030", "in": 1.0, "bars": 2}]}
RE_WHIP = {"music_start_bar": 0, "shots": [{"clip": "0010", "in": 1.0, "bars": 2},
                                           {"clip": "0020", "in": 1.0, "bars": 2, "transition": ["zoomin", 0.4]}]}
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000""1f15c4890000000d49444154789c6360f8cfc0f01f0005000201"
                    "a1d6d0c70000000049454e44ae426082")


def fake_claude(path, spec):
    """A stand-in for the claude CLI: prints stream-json events like the real one and writes cut-spec.json."""
    with open(path, "w") as f:
        f.write("#!%s\nimport json\n" % sys.executable)
        f.write("ev = lambda o: print(json.dumps(o), flush=True)\n")
        f.write("import sys; open('prompt.txt', 'w').write(sys.argv[sys.argv.index('-p') + 1])\n")
        f.write("ev({'type': 'system', 'subtype': 'init'})\n")
        f.write("ev({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'sheets/overview.png'}}]}})\n")
        f.write("json.dump(%r, open('cut-spec.json', 'w'))\n" % spec)
        f.write("ev({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Write', 'input': {'file_path': 'cut-spec.json'}}]}})\n")
        f.write("ev({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'Open on 0010, dissolve to 0020, end on 0030.'})\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Studio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.MANAGER = jobs.Manager()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.media = os.path.join(TMP, "media")
        if HAS_FFMPEG:
            make_footage(os.path.join(cls.media, "raw"))
            make_music(os.path.join(cls.media, "track.wav"))
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x1d4ed8:s=200x80", "-frames:v", "1",
                            os.path.join(cls.media, "logo.png")], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(TMP, True)

    def req(self, method, path, body=None, headers=None, raw=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": "127.0.0.1:%d" % self.port}
        h.update(headers or {})
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        if data is not None and "Content-Type" not in h:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        c.close()
        return r.status, (out if raw else json.loads(out or b"null")), dict(r.getheaders())

    def settings(self, **kw):
        s = {"title": "Test Film", "sub": "Somewhere", "end": "Me", "max_seconds": 14, "vertical_max_seconds": 10,
             "versions": ["1080p"], "editor": "auto", "quality": "fast", "clips_mode": "folder",
             "clips_folder": os.path.join(self.media, "raw"), "music_mode": "path", "music_path": os.path.join(self.media, "track.wav"),
             "output_dir": os.path.join(TMP, "delivered")}
        s.update(kw)
        return s

    def wait(self, jid, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            _, j, _ = self.req("GET", "/api/jobs/" + jid)
            if j["state"] in ("done", "failed", "cancelled"):
                return j
            time.sleep(0.5)
        self.fail("job did not finish")

    # ---- API
    def test_page_and_check(self):
        st, body, h = self.req("GET", "/", raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"Create video", body)
        st, c, _ = self.req("GET", "/api/check")
        self.assertEqual(st, 200)
        self.assertIn("claude", c)

    def test_foreign_host_and_origin_are_refused(self):
        self.assertEqual(self.req("GET", "/api/check", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/jobs", {}, headers={"Origin": "http://evil.example"})[0], 403)

    def test_validation(self):
        st, e, _ = self.req("POST", "/api/jobs", {"title": "", "versions": ["4k"], "clips_mode": "upload", "music_mode": "upload"})
        self.assertEqual(st, 400); self.assertIn("title", e["error"])
        st, e, _ = self.req("POST", "/api/jobs", {"title": "x", "versions": [], "clips_mode": "upload", "music_mode": "upload"})
        self.assertEqual(st, 400); self.assertIn("version", e["error"])
        st, e, _ = self.req("POST", "/api/jobs", {"title": "x", "versions": ["4k"], "clips_mode": "folder", "clips_folder": "/nope", "music_mode": "upload"})
        self.assertEqual(st, 400); self.assertIn("not found", e["error"])
        st, e, _ = self.req("POST", "/api/jobs", {"title": "x", "versions": ["4k"], "max_seconds": 2, "clips_mode": "upload", "music_mode": "upload"})
        self.assertEqual(st, 400); self.assertIn("between", e["error"])

    def test_upload_is_sanitised_and_streamed(self):
        st, j, _ = self.req("POST", "/api/jobs", {"title": "Up", "versions": ["4k"], "clips_mode": "upload", "music_mode": "upload",
                                                  "output_dir": os.path.join(TMP, "o")})
        self.assertEqual(st, 200)
        jid = j["id"]
        blob = os.urandom(3 * (1 << 20) + 17)
        st, r, _ = self.req("PUT", "/api/jobs/%s/upload/raw/..%%2F..%%2Fevil%%20clip.MP4" % jid, blob, {"Content-Type": "application/octet-stream"})
        self.assertEqual(st, 200, r)
        self.assertEqual(r["name"], "evil clip.MP4")
        with open(os.path.join(jobs.JOBS_DIR, jid, "raw", "evil clip.MP4"), "rb") as f:
            self.assertEqual(f.read(), blob)
        st, r, _ = self.req("PUT", "/api/jobs/%s/upload/raw/notes.txt" % jid, b"x", {"Content-Type": "application/octet-stream"})
        self.assertEqual(st, 400)
        for name in ("a.mp3", "b.wav"):                       # one track per job: the newest wins
            self.req("PUT", "/api/jobs/%s/upload/music/%s" % (jid, name), b"123", {"Content-Type": "application/octet-stream"})
        self.assertEqual(os.listdir(os.path.join(jobs.JOBS_DIR, jid, "music")), ["b.wav"])

    def test_media_range_and_traversal(self):
        st, j, _ = self.req("POST", "/api/jobs", {"title": "Media", "versions": ["4k"], "clips_mode": "upload", "music_mode": "upload",
                                                  "output_dir": os.path.join(TMP, "o")})
        d = os.path.join(jobs.JOBS_DIR, j["id"])
        os.makedirs(os.path.join(d, "out"))
        with open(os.path.join(d, "out", "f.mp4"), "wb") as f:
            f.write(bytes(range(256)) * 4)
        st, body, h = self.req("GET", "/media/%s/out/f.mp4" % j["id"], headers={"Range": "bytes=10-19"}, raw=True)
        self.assertEqual(st, 206)
        self.assertEqual(body, bytes(range(10, 20)))
        self.assertEqual(h["Content-Range"], "bytes 10-19/1024")
        self.assertEqual(self.req("GET", "/media/%s/out/../job.json" % j["id"], raw=True)[0], 404)
        self.assertEqual(self.req("GET", "/media/%s/settings.json" % j["id"], raw=True)[0], 404)

    def test_realestate_validation_and_logo(self):
        base = {"title": "1 Main St", "versions": ["4k"], "clips_mode": "upload", "music_mode": "upload", "style": "realestate",
                "output_dir": os.path.join(TMP, "o")}
        st, e, _ = self.req("POST", "/api/jobs", dict(base, title=""))
        self.assertEqual(st, 400); self.assertIn("address", e["error"])
        st, e, _ = self.req("POST", "/api/jobs", dict(base, listing={"email": "not-an-email"}))
        self.assertEqual(st, 400); self.assertIn("email", e["error"])
        st, j, _ = self.req("POST", "/api/jobs", dict(base, footage="exterior", colour="graded",
                                                      listing={"price": "  $900,000 ", "beds": "3", "bogus": "x"}))
        self.assertEqual(st, 200, j)
        s = j["settings"] if "settings" in j else server.MANAGER.jobs[j["id"]].data["settings"]
        self.assertEqual((s["style"], s["footage"], s["labels"], s["colour"]), ("realestate", "exterior", False, "graded"))
        self.assertEqual(s["listing"]["price"], "$900,000")
        self.assertNotIn("bogus", s["listing"])
        up = "/api/jobs/%s/upload/brand/" % j["id"]
        octet = {"Content-Type": "application/octet-stream"}
        self.assertEqual(self.req("PUT", up + "logo.gif", PNG, octet)[0], 400)            # not an accepted type
        self.assertEqual(self.req("PUT", up + "logo.png", b"<svg onload=x>", octet)[0], 400)   # named .png, is not one
        self.assertEqual(self.req("PUT", up + "a.png", PNG, octet)[0], 200)
        self.assertEqual(self.req("PUT", up + "b.png", PNG, octet)[0], 200)                # one logo: the newest wins
        self.assertEqual(os.listdir(os.path.join(jobs.JOBS_DIR, j["id"], "brand")), ["b.png"])

    def test_listing_card(self):
        s = {"title": "12 Ocean Dr", "sub": "Hollywood", "end": "Jane Doe",
             "listing": dict.fromkeys(jobs.LISTING, "") | {"price": "$1,250,000", "beds": "4", "area": "2850", "area_unit": "sq ft", "phone": "555"}}
        self.assertEqual(jobs.listing_card(s), ("12 Ocean Dr", "Hollywood", ["$1,250,000", "4 bd · 2,850 sq ft", "---", "Jane Doe", "555"]))
        s["end"], s["listing"]["phone"] = "", ""
        self.assertEqual(jobs.listing_card(s)[2], ["$1,250,000", "4 bd · 2,850 sq ft"])

    # ---- whole jobs
    def run_job(self, logo=False, **kw):
        st, j, _ = self.req("POST", "/api/jobs", self.settings(**kw))
        self.assertEqual(st, 200, j)
        if logo:
            with open(os.path.join(self.media, "logo.png"), "rb") as f:
                st, r, _ = self.req("PUT", "/api/jobs/%s/upload/brand/logo.png" % j["id"], f.read(), {"Content-Type": "application/octet-stream"})
            self.assertEqual(st, 200, r)
        self.assertEqual(self.req("POST", "/api/jobs/%s/start" % j["id"], {})[0], 200)
        return self.wait(j["id"])

    def realestate(self, spec, **kw):
        os.environ["DRONE_STUDIO_CLAUDE"] = fake_claude(os.path.join(TMP, "claude-re-%d" % len(spec["shots"])), spec)
        try:
            return self.run_job(editor="claude", style="realestate", title="12 Ocean Dr", sub="Hollywood, FL", end="Jane Doe",
                                listing={"price": "$1,250,000", "beds": "4", "baths": "3", "brokerage": "Coastal Realty"},
                                logo=True, **kw)
        finally:
            del os.environ["DRONE_STUDIO_CLAUDE"]

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg missing")
    def test_realestate_claude_job(self):
        j = self.realestate(RE_GOOD, footage="exterior", labels=True, versions=["1080p"])
        self.assertEqual(j["state"], "done", j.get("error") or j["log"][-3000:])
        self.assertEqual(j["editor_used"], "claude")
        d = os.path.join(jobs.JOBS_DIR, j["id"])
        with open(os.path.join(d, "prompt.txt")) as f:
            prompt = f.read()
        self.assertIn("EXTERIOR ONLY", prompt)
        self.assertIn(os.path.join("styles", "realestate.md"), prompt)
        self.assertIn("Room labels: ON", prompt)
        self.assertIn("--config build-cut.json", prompt)
        with open(os.path.join(d, "cutlist.json")) as f:
            C = json.load(f)
        self.assertEqual(C["style"], "realestate")
        self.assertTrue(C["labels"])
        self.assertEqual(C["shots"][0]["label"], "Pool")
        self.assertEqual(C["end"]["text"], "12 Ocean Dr")
        self.assertEqual(C["end"]["lines"], ["$1,250,000", "4 bd · 3 ba", "---", "Jane Doe · Coastal Realty"])
        self.assertTrue(C["end"]["logo"].endswith(os.path.join("brand", "logo.png")))
        self.assertTrue(j["outputs"][0]["verify_pass"], j["outputs"][0]["verify"])

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg missing")
    def test_realestate_style_violation_falls_back(self):
        j = self.realestate(RE_WHIP, versions=["1080p"])
        self.assertEqual(j["state"], "done", j.get("error") or j["log"][-3000:])
        self.assertEqual(j["editor_used"], "automatic")
        self.assertIn("not used in the realestate style", j["log"])
        with open(os.path.join(jobs.JOBS_DIR, j["id"], "cutlist.json")) as f:
            C = json.load(f)
        self.assertEqual(C["style"], "realestate")
        self.assertNotIn("zoomin", [s["transition"]["type"] for s in C["shots"]])
        self.assertIn("$1,250,000", C["end"]["lines"])

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg missing")
    def test_automatic_job_then_revision(self):
        j = self.run_job(versions=["1080p", "vertical"])
        self.assertEqual(j["state"], "done", j.get("error") or j["log"][-3000:])
        self.assertEqual(j["editor_used"], "automatic")
        self.assertLessEqual(j["cut"]["duration"], 14.01)
        self.assertEqual([o["version"] for o in j["outputs"]], ["1080p", "vertical"])
        for o in j["outputs"]:
            self.assertTrue(o["verify_pass"], o["verify"])
            self.assertTrue(os.path.exists(o["delivered"]))
            self.assertTrue(o["delivered"].startswith(os.path.join(TMP, "delivered", "test-film-")))
        self.assertEqual(self.req("POST", "/api/jobs/%s/revise" % j["id"], {"notes": "slower"})[0], 200)
        j2 = self.wait(j["id"])
        self.assertEqual(j2["state"], "done", j2.get("error"))
        self.assertEqual(j2["revisions"], ["slower"])
        self.assertEqual([s["key"] for s in j2["stages"]][:4], ["ingest", "analyze", "music", "edit"])

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg missing")
    def test_claude_cut_is_used(self):
        os.environ["DRONE_STUDIO_CLAUDE"] = fake_claude(os.path.join(TMP, "claude-good"), GOOD)
        try:
            j = self.run_job(editor="claude")
        finally:
            del os.environ["DRONE_STUDIO_CLAUDE"]
        self.assertEqual(j["state"], "done", j.get("error") or j["log"][-3000:])
        self.assertEqual(j["editor_used"], "claude")
        self.assertEqual([s["clip"] for s in j["cut"]["shots"]], ["0010", "0020", "0030"])
        self.assertIn("dissolve to 0020", j["summary"])
        self.assertIn("claude: Read", j["log"])

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg missing")
    def test_bad_claude_cut_falls_back(self):
        os.environ["DRONE_STUDIO_CLAUDE"] = fake_claude(os.path.join(TMP, "claude-bad"), BAD)
        try:
            j = self.run_job(editor="claude")
        finally:
            del os.environ["DRONE_STUDIO_CLAUDE"]
        self.assertEqual(j["state"], "done", j.get("error") or j["log"][-3000:])
        self.assertEqual(j["editor_used"], "automatic")
        self.assertIn("did not pass", j["summary"])
