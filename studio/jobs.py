"""jobs.py: one job = one drone-edit project folder in studio-jobs/, run stage by stage in a background thread.
State lives in <job>/job.json (rewritten on every change) and the full output of every step in <job>/job.log.
Stages: ingest -> analyze -> music -> edit -> render (one per version, with verify) -> deliver.
"Edit again" (revise) re-runs from the edit stage; render.py's shot cache keeps unchanged shots."""
import datetime, json, os, queue, re, shutil, signal, subprocess, sys, threading, time

import editor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, ".claude", "skills", "drone-edit")
SCRIPTS = os.path.join(SKILL, "scripts")
JOBS_DIR = os.environ.get("DRONE_STUDIO_JOBS") or os.path.join(ROOT, "studio-jobs")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mxf")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif")
LOGO_EXT = (".png", ".jpg", ".jpeg", ".webp")
VERSIONS = {"4k": "16:9 · 4K", "1080p": "16:9 · 1080p", "vertical": "9:16 · 1080×1920"}
STYLES = ("drone", "realestate")
FOOTAGE = ("exterior", "mixed")                 # real estate: exterior only, or interior & exterior
COLOURS = ("auto", "dlogm", "rec709", "graded")
LISTING = {"price": 30, "beds": 6, "baths": 6, "area": 12, "area_unit": 6, "brokerage": 60, "phone": 30, "email": 80, "website": 80}


class Cancelled(Exception):
    pass


class StageFailed(Exception):
    pass


def slug(text, fallback="film"):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or fallback


def safe_name(name):
    """A file name from the browser, reduced to something harmless (no folders, no leading dots)."""
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name[:120] or "file"


def is_image(path):
    with open(path, "rb") as f:
        head = f.read(12)
    return head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")


def listing_card(s):
    """Real estate: the end card as (text, sub, lines). Address and city, price, beds · baths · area, a rule, agent ·
    brokerage, phone · email · website: only the parts that were filled in."""
    L = s["listing"]

    def num(v):
        return "{:,}".format(int(v)) if re.fullmatch(r"\d{4,}", v) else v
    facts = " · ".join(x for x in (L["beds"] and "%s bd" % L["beds"], L["baths"] and "%s ba" % L["baths"],
                                    L["area"] and "%s %s" % (num(L["area"]), L["area_unit"] or "sq ft")) if x)
    lines = [x for x in (L["price"], facts) if x]
    people = [x for x in (" · ".join(y for y in (s["end"], L["brokerage"]) if y), " · ".join(y for y in (L["phone"], L["email"], L["website"]) if y)) if x]
    if people:
        lines += (["---"] if lines else []) + people
    return s["title"], s["sub"], lines


def full_path(p):
    """A path from the form, absolute: every step runs inside the job folder, where a relative path means nothing."""
    p = os.path.expanduser(str(p or "").strip())
    return os.path.abspath(p) if p else ""


def validate(s):
    """Settings from the form -> cleaned settings, or ValueError with a message for the page."""
    def num(key, lo, hi, default):
        try:
            v = float(s.get(key, default))
        except (TypeError, ValueError):
            raise ValueError("%s must be a number" % key)
        if not lo <= v <= hi:
            raise ValueError("%s must be between %g and %g seconds" % (key.replace("_", " "), lo, hi))
        return v
    out = {
        "title": str(s.get("title", "")).strip()[:80],
        "sub": str(s.get("sub", "")).strip()[:80],
        "end": str(s.get("end", "")).strip()[:80],
        "end_sub": str(s.get("end_sub", "Shot on DJI Mini 4 Pro")).strip()[:80],
        "style": s.get("style") if s.get("style") in STYLES else "drone",
        "footage": s.get("footage") if s.get("footage") in FOOTAGE else "mixed",
        "labels": bool(s.get("labels")),
        "colour": s.get("colour") if s.get("colour") in COLOURS else "auto",
        "listing": {k: re.sub(r"\s+", " ", str((s.get("listing") or {}).get(k, ""))).strip()[:n] for k, n in LISTING.items()},
        "notes": str(s.get("notes", "")).strip()[:4000],
        "max_seconds": num("max_seconds", 10, 600, 45),
        "vertical_max_seconds": num("vertical_max_seconds", 10, 90, 30),
        "versions": [v for v in s.get("versions", []) if v in VERSIONS],
        "quality": "fast" if s.get("quality") == "fast" else "best",
        "editor": "auto" if s.get("editor") == "auto" else "claude",
        "clips_mode": "folder" if s.get("clips_mode") == "folder" else "upload",
        "clips_folder": full_path(s.get("clips_folder")),
        "music_mode": "path" if s.get("music_mode") == "path" else "upload",
        "music_path": full_path(s.get("music_path")),
        "output_dir": os.path.expanduser(str(s.get("output_dir", "")).strip() or "~/Movies/Drone Studio"),
    }
    if not out["versions"]:
        raise ValueError("pick at least one version")
    if not out["title"]:
        raise ValueError("the address is empty" if out["style"] == "realestate" else "the title is empty")
    if out["style"] != "realestate":
        out["labels"] = False
    elif out["footage"] == "exterior" and "labels" not in s:
        out["labels"] = False
    if out["listing"]["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", out["listing"]["email"]):
        raise ValueError("the email does not look like an email address")
    if out["clips_mode"] == "folder":
        f = out["clips_folder"]
        if not os.path.isdir(f):
            raise ValueError("clips folder not found: %s" % f)
        if not any(n.lower().endswith(VIDEO_EXT) for n in os.listdir(f)):
            raise ValueError("no video files (%s) in %s" % (", ".join(VIDEO_EXT), f))
    if out["music_mode"] == "path":
        if not os.path.isfile(out["music_path"]):
            raise ValueError("music file not found: %s" % out["music_path"])
        if not out["music_path"].lower().endswith(AUDIO_EXT):
            raise ValueError("music must be an audio file (%s)" % ", ".join(AUDIO_EXT))
    od = os.path.abspath(out["output_dir"])
    parent = od
    while not os.path.exists(parent):
        parent = os.path.dirname(parent)
    if not os.access(parent, os.W_OK):
        raise ValueError("output folder is not writable: %s" % od)
    out["output_dir"] = od
    return out


class Job:
    def __init__(self, jid, settings=None):
        self.id = jid
        self.dir = os.path.join(JOBS_DIR, jid)
        self.lock = threading.RLock()
        self.proc = None
        self.cancel_flag = False
        path = os.path.join(self.dir, "job.json")
        if settings is None:
            with open(path) as f:
                self.data = json.load(f)
            if self.data["state"] in ("queued", "running"):        # the server stopped mid-run
                self.data.update(state="failed", error="the studio was restarted while this job ran")
        else:
            os.makedirs(os.path.join(self.dir, "raw"), exist_ok=True)
            os.makedirs(os.path.join(self.dir, "music"), exist_ok=True)
            self.data = {"id": jid, "created": datetime.datetime.now().isoformat(timespec="seconds"), "state": "draft",
                         "settings": settings, "stages": [], "activity": "", "editor_used": None, "summary": "",
                         "outputs": [], "error": None, "revisions": []}
        self.save()

    # ---- state
    def save(self):
        with self.lock:
            tmp = os.path.join(self.dir, "job.json.tmp")
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, os.path.join(self.dir, "job.json"))

    def update(self, **kw):
        with self.lock:
            self.data.update(kw)
            self.save()

    def stage(self, key, **kw):
        with self.lock:
            for s in self.data["stages"]:
                if s["key"] == key:
                    s.update(kw)
            self.save()

    def log(self, text):
        with open(os.path.join(self.dir, "job.log"), "a") as f:
            f.write(text if text.endswith("\n") else text + "\n")

    def log_tail(self, n=80):
        p = os.path.join(self.dir, "job.log")
        if not os.path.exists(p):
            return ""
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 20000))
            return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-n:])

    def view(self):
        with self.lock:
            d = json.loads(json.dumps(self.data))
        d["log"] = self.log_tail()
        cl = os.path.join(self.dir, "cutlist.json")
        if os.path.exists(cl):
            try:
                with open(cl) as f:
                    C = json.load(f)
                d["cut"] = {"duration": C.get("duration"), "shots": [{k: s.get(k) for k in ("at", "clip", "in", "dur", "speed", "transition", "note")}
                                                                     for s in C.get("shots", [])],
                            "vertical": {"duration": C["vertical"].get("duration"), "shots": len(C["vertical"].get("shots", []))} if C.get("vertical") else None}
            except (OSError, ValueError):
                pass
        idx = os.path.join(self.dir, "sheets", "shots", "index.json")
        if os.path.exists(idx):
            try:
                with open(idx) as f:
                    d["sheet"] = json.load(f)
            except (OSError, ValueError):
                pass
        return d

    def shot_frames(self):
        """The sheet the page shows after the edit: one frame per shot in film order (numbered 1..n, as notes refer to
        them), the same for the vertical short, and one frame of each clip the film leaves out. Frames come from the
        graded proxies, at the middle of what each shot really uses. -> sheets/shots/*.jpg + index.json"""
        with open(os.path.join(self.dir, "cutlist.json")) as f:
            C = json.load(f)
        with open(os.path.join(self.dir, "clips.json")) as f:
            clips = {c["id"]: c for c in json.load(f)["clips"]}
        out = os.path.join(self.dir, "sheets", "shots")
        shutil.rmtree(out, True)
        os.makedirs(out)

        def grab(clip, t, name, window=None):
            c = clips[clip]
            vf = "scale=480:-2"
            if window is not None:                                    # the 9:16 window the short shows
                x, y, w, h = c.get("proxy_box") or [0, 0, 960, 540]
                if c.get("orientation") == "portrait":
                    vf = "crop=%d:%d:%d:%d,scale=270:-2" % (w, h, x, y)
                else:
                    cw = int(h * 9 / 16) // 2 * 2
                    vf = "crop=%d:%d:%d:%d,scale=270:-2" % (cw, h, x + int((w - cw) * window), y)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0.0, t), "-i", os.path.join(self.dir, c["proxy"]),
                            "-frames:v", "1", "-vf", vf, "-q:v", "4", os.path.join(out, name)], check=True)
            return "sheets/shots/" + name

        def shots(seq, prefix, vertical):
            rows = []
            for n, s in enumerate(seq, 1):
                mid = float(s["in"]) + float(s["dur"]) * float(s.get("speed", 1.0)) / 2
                window = None
                if vertical:
                    window = sum(s["pan"]) / 2 if s.get("pan") else float(s.get("x", 0.5))
                rows.append({"n": n, "clip": s["clip"], "at": s.get("at"), "dur": s["dur"], "speed": s.get("speed", 1.0),
                             "label": s.get("label"), "img": grab(s["clip"], mid, "%s-%02d.jpg" % (prefix, n), window)})
            return rows
        sheet = {"version": int(time.time()), "film": shots(C["shots"], "film", False),
                 "short": shots(C["vertical"]["shots"], "short", True) if C.get("vertical") else []}
        used = {s["clip"] for s in C["shots"]} | {s["clip"] for s in (C.get("vertical") or {}).get("shots", [])}
        sheet["unused"] = [{"clip": cid, "img": grab(cid, c["duration"] / 2, "clip-%s.jpg" % cid)} for cid, c in clips.items() if cid not in used]
        with open(os.path.join(out, "index.json"), "w") as f:
            json.dump(sheet, f, indent=1)
        return sheet

    # ---- paths
    @property
    def raw_dir(self):
        s = self.data["settings"]
        return s["clips_folder"] if s["clips_mode"] == "folder" else os.path.join(self.dir, "raw")

    @property
    def logo_file(self):
        d = os.path.join(self.dir, "brand")
        files = sorted(f for f in os.listdir(d) if f.lower().endswith(LOGO_EXT)) if os.path.isdir(d) else []
        return os.path.join(d, files[0]) if files and self.data["settings"]["style"] == "realestate" else None

    @property
    def music_file(self):
        s = self.data["settings"]
        if s["music_mode"] == "path":
            return s["music_path"]
        files = sorted(f for f in os.listdir(os.path.join(self.dir, "music")) if f.lower().endswith(AUDIO_EXT))
        return os.path.join(self.dir, "music", files[0]) if files else None

    # ---- running a step
    def run(self, key, cmd, expect=None, pattern=None, cwd=None):
        """Run one command, stream its output to the log, turn matching lines into stage progress."""
        if self.cancel_flag:
            raise Cancelled()
        self.log("$ " + " ".join(cmd))
        self.proc = subprocess.Popen(cmd, cwd=cwd or self.dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, start_new_session=True)
        done, tail = 0, []
        for line in self.proc.stdout:
            self.log(line.rstrip("\n"))
            tail = (tail + [line.rstrip()])[-15:]
            m = re.match(r"PROGRESS (\d+)/(\d+)", line)
            if m:
                self.stage(key, progress=int(m.group(1)) / max(1, int(m.group(2))))
            elif pattern and expect and re.match(pattern, line):
                done += 1
                self.stage(key, progress=min(1.0, done / expect))
        code = self.proc.wait()
        self.proc.stdout.close()
        self.proc = None
        if self.cancel_flag:
            raise Cancelled()
        if code:
            raise StageFailed("%s failed (exit %d):\n%s" % (os.path.basename(cmd[1]) if len(cmd) > 1 else cmd[0], code, "\n".join(tail)))
        return "\n".join(tail)

    def py(self, script, *args):
        return [sys.executable, os.path.join(SCRIPTS, script)] + [str(a) for a in args]

    def cancel(self):
        self.cancel_flag = True
        p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    # ---- the pipeline
    def plan_stages(self, from_edit=False):
        s = self.data["settings"]
        keys = ([] if from_edit else [("ingest", "Prepare clips"), ("analyze", "Analyze footage"), ("music", "Read the music")])
        keys += [("edit", "Edit")] + [("render-" + v, "Render " + VERSIONS[v]) for v in s["versions"]] + [("deliver", "Deliver")]
        old = {x["key"]: x for x in self.data["stages"]}
        self.data["stages"] = ([old[k] for k in ("ingest", "analyze", "music") if from_edit and k in old] +
                               [{"key": k, "label": l, "state": "pending", "progress": 0.0, "detail": ""} for k, l in keys])
        self.save()

    def execute(self, from_edit=False):
        s = self.data["settings"]
        self.cancel_flag = False
        self.update(state="running", error=None, activity="", outputs=[] if not from_edit else self.data["outputs"])
        current = None
        try:
            if not from_edit:
                current = "ingest"
                n = len([f for f in os.listdir(self.raw_dir) if f.lower().endswith(VIDEO_EXT)])
                if not n:
                    raise StageFailed("no clips: upload some, or choose a folder with videos")
                self.stage("ingest", state="running", detail="%d clips" % n)
                colour = ["--profile", s["colour"]] if s.get("colour", "auto") != "auto" else []
                self.run("ingest", self.py("ingest.py", "--raw", self.raw_dir, "--project", self.dir, "--style", s.get("style", "drone"), *colour),
                         n, r"proxy ")
                self.stage("ingest", state="done", progress=1.0)

                current = "analyze"
                self.stage("analyze", state="running")
                self.run("analyze", self.py("analyze.py", "--project", self.dir, "--style", s.get("style", "drone")), n, r"analyzed ")
                with open(os.path.join(self.dir, "analysis.json")) as f:
                    cands = sum(len(c["candidates"]) for c in json.load(f)["clips"].values())
                self.stage("analyze", state="done", progress=1.0, detail="%d usable stretches" % cands)

                current = "music"
                mf = self.music_file
                if not mf:
                    raise StageFailed("no music: upload a track or choose a file")
                self.stage("music", state="running", detail=os.path.basename(mf))
                out = self.run("music", self.py("beats.py", "--music", mf, "--out", os.path.join(self.dir, "beats.json")))
                self.stage("music", state="done", progress=1.0, detail=out.strip().splitlines()[-1].split(" -> ")[0] if out.strip() else "")

            current = "edit"
            self.stage("edit", state="running", detail="Claude" if s["editor"] == "claude" else "automatic")
            used, summary = editor.edit(self)
            self.update(editor_used=used, summary=summary)
            with open(os.path.join(self.dir, "cutlist.json")) as f:
                C = json.load(f)
            try:
                self.shot_frames()
            except (subprocess.CalledProcessError, OSError, KeyError, ValueError) as e:   # the sheet is a convenience
                self.log("shot sheet: %s" % e)
            self.stage("edit", state="done", progress=1.0,
                       detail="%s, %d shots, %.0f s" % ("Claude" if used == "claude" else "automatic", len(C["shots"]), C["duration"]))

            outputs = []
            for v in s["versions"]:
                key = "render-" + v
                current = key
                self.stage(key, state="running")
                args = ["--cutlist", os.path.join(self.dir, "cutlist.json"), "--progress"]
                if v == "vertical":
                    args.append("--vertical")
                elif v == "1080p":
                    args += ["--size", "1920x1080"]
                if s["quality"] == "fast":
                    args.append("--gpu")
                self.run(key, self.py("render.py", *args))
                name = {"4k": "4k", "1080p": "1080p", "vertical": "vertical"}[v]
                path = os.path.join(self.dir, "out", "%s-%s.mp4" % (os.path.basename(self.dir), name))
                self.stage(key, detail="verifying")
                vr = subprocess.run(self.py("verify.py", "--render", path), capture_output=True, text=True)
                self.log(vr.stdout + vr.stderr)
                rows = [[c.strip().strip("*") for c in r.strip("|").split("|")] for r in vr.stdout.splitlines() if r.startswith("|")]
                outputs.append({"version": v, "label": VERSIONS[v], "file": os.path.relpath(path, self.dir), "verify_pass": vr.returncode == 0,
                                "verify": rows, "sheet": os.path.relpath(os.path.splitext(path)[0] + ".sheet.png", self.dir),
                                "size": os.path.getsize(path)})
                self.update(outputs=outputs)
                self.stage(key, state="done" if vr.returncode == 0 else "warn", progress=1.0,
                           detail="verified" if vr.returncode == 0 else "verify found problems")

            current = "deliver"
            self.stage("deliver", state="running")
            od = s["output_dir"]
            os.makedirs(od, exist_ok=True)
            base = slug(s["title"])
            for o in outputs:
                dst = os.path.join(od, "%s-%s.mp4" % (base, o["version"]))
                k = 2
                while os.path.exists(dst) and o.get("delivered") != dst:
                    dst = os.path.join(od, "%s-%s-%d.mp4" % (base, o["version"], k)); k += 1
                shutil.copy2(os.path.join(self.dir, o["file"]), dst)
                o["delivered"] = dst
            self.update(outputs=outputs)
            self.stage("deliver", state="done", progress=1.0, detail=od)
            self.update(state="done", activity="")
        except Cancelled:
            if current:
                self.stage(current, state="cancelled")
            self.update(state="cancelled", activity="")
        except (StageFailed, OSError, ValueError, KeyError) as e:
            if current:
                self.stage(current, state="failed")
            self.log("ERROR: %s" % e)
            self.update(state="failed", error=str(e), activity="")


class Manager:
    """Jobs on disk + one worker thread (renders use every core: one job at a time)."""
    def __init__(self):
        os.makedirs(JOBS_DIR, exist_ok=True)
        self.jobs = {}
        for jid in sorted(os.listdir(JOBS_DIR)):
            if os.path.exists(os.path.join(JOBS_DIR, jid, "job.json")):
                try:
                    self.jobs[jid] = Job(jid)
                except (OSError, ValueError, KeyError):
                    pass
        self.q = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        while True:
            job, from_edit = self.q.get()
            job.execute(from_edit)

    def create(self, settings):
        base = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), slug(settings["title"]))
        jid, k = base, 2
        while os.path.exists(os.path.join(JOBS_DIR, jid)):
            jid = "%s-%d" % (base, k); k += 1
        job = Job(jid, settings)
        self.jobs[jid] = job
        return job

    def start(self, job, from_edit=False):
        if job.data["state"] in ("queued", "running"):
            raise ValueError("this job is already running")
        if from_edit and not os.path.exists(os.path.join(job.dir, "beats.json")):
            raise ValueError("this job never got past the music step: create it again")
        job.plan_stages(from_edit)
        job.update(state="queued")
        self.q.put((job, from_edit))
