"""Shared bits for the tests: run a script the way the skill does, scratch dirs, synthetic drone footage and music.
Paths are relative to this file (SCRIPTS = the folder above tests/), so the tests run from any install location.
Footage is a static noise texture panned by whole pixels per frame (even motion, unlike testsrc2, whose own animated
elements fool the motion estimate); a shaky clip adds an 18 px, 6 Hz wobble after 5 s."""
import json, os, shutil, subprocess, sys, tempfile, unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = unittest.skipUnless(HAS_FFMPEG, "ffmpeg / ffprobe not on PATH")
sys.path.insert(0, SCRIPTS)


def run(script, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + [str(a) for a in args], capture_output=True, text=True)


def scratch(test=None):
    d = tempfile.mkdtemp(prefix="drone-edit-test-")
    if test is not None:
        test.addCleanup(shutil.rmtree, d, True)
    return d


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)
    return path


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error"] + [str(a) for a in args], check=True)


def make_footage(raw_dir, n=3, shaky=(3,), seconds=10):
    """n clips named like DJI files (ids 0010, 0020, ...), 1280x720 59.94 fps 10-bit, no audio."""
    os.makedirs(raw_dir, exist_ok=True)
    tex = os.path.join(raw_dir, "..", "tex.png")
    ffmpeg("-f", "lavfi", "-i", "color=c=gray:s=2400x1400,noise=alls=90:allf=u,gblur=sigma=2.5,eq=contrast=1.4", "-frames:v", 1, tex)
    for k in range(1, n + 1):
        x = "200+n+if(gt(t,5),18*sin(t*40),0)" if k in shaky else "200+%d*n" % k
        ffmpeg("-loop", 1, "-framerate", "60000/1001", "-t", seconds, "-i", tex,
               "-vf", "crop=1280:720:x='%s':y='100+trunc(n/2)',format=yuv420p10le" % x,
               "-c:v", "libx264", "-preset", "ultrafast", os.path.join(raw_dir, "DJI_2026_00%d0_D.MP4" % k))


def make_music(path, bpm=120, seconds=30, drop=12.0):
    """A click track: quiet clicks on every beat, louder on bar starts, everything louder from `drop` seconds."""
    p = 60.0 / bpm
    ffmpeg("-f", "lavfi", "-i", "aevalsrc='(0.15+0.85*gte(t,%g))*if(lt(mod(t,%g),0.03),(0.4+0.6*lt(mod(t,%g),0.03))*sin(2*PI*900*t)*exp(-60*mod(t,%g)),0)"
           "+(0.02+0.3*gte(t,%g))*sin(2*PI*110*t)':s=44100:d=%g" % (drop, p, 4 * p, p, drop, seconds), path)


def probe_frames(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames,width,height",
                        "-of", "json", path], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["streams"][0]
