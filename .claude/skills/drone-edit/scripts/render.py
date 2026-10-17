#!/usr/bin/env python3
"""render.py: cutlist.json -> the finished film (16:9 master, or --vertical for the 9:16 short).
Two passes:
  1. shots   each shot is cut from the SOURCE file (4K 10-bit), cropped (vertical; a non-16:9 source fills the frame
             and is centre-cropped, never stretched), scaled, graded by its clip's colour profile (D-Log M LUT, Rec.709,
             or HDR tone map, then the look, 16-bit RGB), retimed (speed 0.5 = 2x slow-mo from 60 fps) and conformed to 30 fps, into a 10-bit
             cache file of exactly round(dur * 30) frames. Cached by content hash in <project>/work/shots/, so a
             notes round re-renders only the shots that changed.
  2. master  shots joined in order: hard cuts with concat, transitions with xfade centred on the cut; fade in from
             black and out to black; title and end cards (cards.py PNGs) faded over the picture, and room labels
             ("label" on a shot, when the cutlist has "labels": true) from 0.4 s after the cut for up to 2.5 s; the audio mix
             (music trimmed and faded, SFX placed, two-pass loudnorm to the cutlist's LUFS) muxed in.
Output: <project>/out/<project>-<4k|preview|vertical|vertical-preview>.mp4 and a .timeline.json next to it
        (frame-exact cut times and the windows where black is intended, for verify.py)
Transitions: cut, fade (dissolve), fadeblack, fadewhite, zoomin, smoothleft, smoothright, smoothup, smoothdown,
             circleopen, wipeleft, wiperight, dissolve (any ffmpeg xfade name works; keep to the first five).
Usage:
  python3 render.py --cutlist videos/<p>/cutlist.json [--preview] [--vertical] [--gpu] [--res 1280x720] [--out file.mp4]
--preview: 1920x1080 (720x1280 vertical), fast encodes: for review rounds. --gpu: VideoToolbox for the final encode.
--size WxH: a final-quality render at another size (e.g. 1920x1080); --res is the same for quick test renders.
--progress: also print "PROGRESS <done>/<total> <stage>" lines (the studio's progress bar).
"""
import argparse, hashlib, json, os, shutil, subprocess, sys
import cards as cardlib
import common

ap = argparse.ArgumentParser()
ap.add_argument("--cutlist", required=True)
ap.add_argument("--vertical", action="store_true")
ap.add_argument("--preview", action="store_true")
ap.add_argument("--gpu", action="store_true")
ap.add_argument("--res", help="override the output size, WxH (marked as a preview)")
ap.add_argument("--size", help="final-quality output at this size, WxH")
ap.add_argument("--progress", action="store_true")
ap.add_argument("--out")
ap.add_argument("--no-hwaccel", action="store_true")
args = ap.parse_args()

VERSION = "1"                                                           # bump to invalidate the shot cache
FPS = common.FPS
P = os.path.dirname(os.path.abspath(args.cutlist))
C = common.load(args.cutlist)
clips = {c["id"]: c for c in common.load(os.path.join(P, "clips.json"))["clips"]}
seq = C["vertical"] if args.vertical else C
if args.vertical and not seq:
    common.die("the cutlist has no vertical section")
if args.size or args.res:
    W, H = (int(v) for v in (args.size or args.res).lower().split("x"))
elif args.preview:
    W, H = (720, 1280) if args.vertical else (1920, 1080)
else:
    W, H = int(seq.get("width", 3840)), int(seq.get("height", 2160))
lut = C.get("lut")
if lut:
    lut = common.resolve(lut, P)
    if not os.path.exists(lut):
        common.die("LUT not found: %s" % lut)
else:
    lut = common.find_lut()
    if not lut:
        print("note: no LUT, D-Log M clips use the approximate curve")
look = C.get("look") or {}
hw = [] if args.no_hwaccel or sys.platform != "darwin" else ["-hwaccel", "videotoolbox"]
kind = ("vertical" if args.vertical else "preview" if args.preview else "4k" if (W, H) == (3840, 2160) else "%dp" % min(W, H)) \
    + ("-preview" if args.vertical and args.preview else "")
out = args.out or os.path.join(P, "out", "%s-%s.mp4" % (C.get("project", "film"), kind))
work = os.path.join(P, "work")
os.makedirs(os.path.join(work, "shots"), exist_ok=True)
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

# ---- pass 1: shots
shots = seq["shots"]
if not shots:
    common.die("no shots in the cutlist")
# frame counts from the cumulative timeline, not shot by shot: rounding each shot alone drifts a frame every few
# shots, and late cuts slide off the beat
td = [0.0 if i == 0 or (s.get("transition") or {}).get("type", "cut") == "cut" else float(s["transition"].get("dur", 0.5))
      for i, s in enumerate(shots)]
pos = [0.0]
for i in range(1, len(shots)):
    pos.append(pos[-1] + float(shots[i - 1]["dur"]) - td[i])
files, frames, tframes = [], [], []
for i, s in enumerate(shots):
    clip = clips.get(s["clip"]) or common.die("unknown clip %r in shot %d" % (s["clip"], i + 1))
    speed = float(s.get("speed", 1.0))
    dur = float(s["dur"])
    n = int(round((pos[i] + dur) * FPS)) - int(round(pos[i] * FPS))
    span = dur * speed
    if s["in"] < 0 or s["in"] + span > clip["duration"] + 0.02:
        common.die("shot %d: %s %.2f + %.2f s at speed %.2f runs past the clip (%.2f s)" % (i + 1, s["clip"], s["in"], dur, speed, clip["duration"]))
    tf = 0 if i == 0 else int(round((pos[i - 1] + float(shots[i - 1]["dur"])) * FPS)) - int(round(pos[i] * FPS))
    vf = []
    if args.vertical:
        x0, x1 = (s["pan"] if s.get("pan") else (s.get("x", 0.5), s.get("x", 0.5)))
        # the 9:16 window; a portrait source is already narrower than that: keep its full width, crop the height
        cw, ch = "min(iw,trunc(ih*%d/%d/2)*2)" % (W, H), "min(ih,trunc(iw*%d/%d/2)*2)" % (H, W)
        vf.append("crop=w='%s':h='%s':x='(iw-ow)*(%.4f+%.4f*min(1,t/%.4f))':y='(ih-oh)/2'" % (cw, ch, x0, x1 - x0, max(span, 0.01)))
        vf.append("scale=%d:%d:flags=lanczos,setsar=1" % (W, H))   # a portrait source and a cropped landscape one must join
    else:
        vf.append(common.fit_vf(W, H, clip.get("width", W), clip.get("height", H)))
    vf.append(common.grade_vf(lut, look, clip.get("profile", "dlogm")))
    vf.append("setpts=(PTS-STARTPTS)/%.6f" % speed)
    vf += ["fps=%d" % FPS, "tpad=stop_mode=clone:stop_duration=1", "format=yuv420p10le", common.TAG_VF]
    key = json.dumps([VERSION, clip["src"], s["in"], n, speed, vf, args.preview], sort_keys=True)
    path = os.path.join(work, "shots", "%02d-%s-%s.mp4" % (i + 1, s["clip"], hashlib.sha1(key.encode()).hexdigest()[:10]))
    if not os.path.exists(path):
        print("shot %2d/%d  %s  %.2f s from %.2f%s" % (i + 1, len(shots), s["clip"], dur, s["in"], "  x%.2f" % speed if speed != 1 else ""), flush=True)
        common.ffmpeg(*hw, "-ss", "%.4f" % s["in"], "-t", "%.4f" % (span + 0.5), "-i", clip["src"], "-map", "0:v:0", "-an", "-sn", "-dn",
                      "-vf", ",".join(vf), "-frames:v", n, *common.encode_args(preview=args.preview, mezzanine=True),
                      *common.TAG_ARGS, path + ".part.mp4")
        shutil.move(path + ".part.mp4", path)
    files.append(path); frames.append(n); tframes.append(tf)
    if args.progress:
        print("PROGRESS %d/%d shots" % (i + 1, len(shots) + 1), flush=True)

# ---- timeline (frame exact)
starts, acc = [], 0
for i, n in enumerate(frames):
    start = acc - tframes[i]
    starts.append(start)
    acc = start + n
total_f = acc
total = total_f / FPS
fade_in, fade_out = float(C.get("fade_in", 1.0)), float(C.get("fade_out", 1.5))
black_ok = [[0.0, fade_in]] if fade_in else []
if fade_out:
    black_ok.append([total - fade_out, total])
for i, s in enumerate(shots):
    t = s.get("transition") or {}
    if i and t.get("type") in ("fadeblack", "fadewhite"):
        black_ok.append([starts[i] / FPS, (starts[i] + tframes[i]) / FPS])

# ---- cards
inputs = []
for f in files:
    inputs += ["-i", f]
cards = []
for kind_ in ("title", "end"):
    spec = dict(C.get(kind_) or {})
    if args.vertical:
        spec.update(seq.get(kind_) or {})
    if not spec.get("text"):
        continue
    key = json.dumps([spec.get("text"), spec.get("sub"), spec.get("lines"), spec.get("logo"),
                      os.path.getmtime(spec["logo"]) if spec.get("logo") and os.path.exists(spec["logo"]) else 0])
    png = os.path.join(work, "card-%s-%dx%d-%s.png" % (kind_, W, H, hashlib.sha1(key.encode()).hexdigest()[:8]))
    if not os.path.exists(png):
        cardlib.render_card(kind_, spec["text"], spec.get("sub", ""), png, W, H, spec.get("lines") or (), spec.get("logo"))
    d = float(spec.get("dur", 3.5))
    at = float(spec["at"]) if kind_ == "title" else max(0.0, total - d - 0.2)
    cards.append((png, at, d, min(0.8, d / 3)))
if C.get("labels"):
    for i, s in enumerate(shots):
        if not s.get("label"):
            continue
        cut = (starts[i] + tframes[i] / 2) / FPS                       # where the shot reads as on screen
        nxt = (starts[i + 1] + tframes[i + 1] / 2) / FPS if i + 1 < len(shots) else total
        d = min(2.5, nxt - cut - 0.8)
        if d < 1.0:
            continue
        png = os.path.join(work, "card-label-%dx%d-%s.png" % (W, H, hashlib.sha1(s["label"].encode()).hexdigest()[:8]))
        if not os.path.exists(png):
            cardlib.render_card("label", s["label"], "", png, W, H)
        cards.append((png, cut + 0.4, d, 0.4))
for png, at, d, fd in cards:
    inputs += ["-loop", "1", "-framerate", str(FPS), "-t", "%.3f" % d, "-i", png]

# ---- audio mix -> wav, loudness-normalised
mix = os.path.join(work, "mix-%s.wav" % kind)
music = C.get("music")
if args.vertical and music and seq.get("music"):       # the short can use its own stretch of the track
    music = dict(music, **seq["music"])
sfx = C.get("sfx") or []
ain, af, labels = [], [], []
if music and music.get("file"):
    mf = common.resolve(music["file"], P)
    if not os.path.exists(mf):
        common.die("music not found: %s" % mf)
    ain += ["-ss", "%.3f" % float(music.get("start", 0.0)), "-i", mf]
    mfo = float(music.get("fade_out", 2.5))
    af.append("[0:a]atrim=0:%.4f,asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume=%sdB,afade=t=in:d=%.3f,afade=t=out:st=%.4f:d=%.3f[m]"
              % (total, music.get("db", 0), float(music.get("fade_in", 0.3)), max(0, total - mfo), mfo))
    labels.append("[m]")
for k, x in enumerate(sfx):
    sf = common.resolve(x["file"], P)
    if not os.path.exists(sf):
        common.die("sfx not found: %s" % sf)
    idx = ain.count("-i")
    ain += ["-i", sf]
    ms = int(round(float(x["at"]) * 1000))
    af.append("[%d:a]aresample=48000,aformat=channel_layouts=stereo,volume=%sdB,adelay=%d|%d[s%d]" % (idx, x.get("db", -6), ms, ms, k))
    labels.append("[s%d]" % k)
lufs = (music or {}).get("lufs", -14)
if labels:
    graph = ";".join(af) + ";" + "".join(labels) + "amix=inputs=%d:duration=longest:normalize=0,apad,atrim=0:%.4f[a]" % (len(labels), total)
    raw_mix = mix + ".raw.wav"
    common.ffmpeg(*ain, "-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_s24le", "-ar", 48000, raw_mix)
    # measured static gain + a -1 dBTP limiter, not loudnorm's second pass: loudnorm drops to its dynamic mode when the
    # music has a wide range (a quiet intro, then the drop) and then misses the target and flattens the build
    def integrated(path):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        return float(r[r.rfind("I:"):].split()[1])
    li = integrated(raw_mix)
    if li > -70:
        gain = lufs - li
        for _ in range(3):        # the limiter eats loudness on spiky music: top up (<= +3 dB a pass) and limit again
            common.ffmpeg("-i", raw_mix, "-af", "volume=%.2fdB,alimiter=limit=0.891:attack=1:release=60:level=disabled" % gain,
                          "-ar", 48000, "-c:a", "pcm_s24le", mix)
            miss = lufs - integrated(mix)
            if abs(miss) <= 0.5:
                break
            gain += max(-3.0, min(3.0, miss))
    else:
        shutil.copy(raw_mix, mix)
    os.remove(raw_mix)
else:
    common.ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "%.4f" % total, "-c:a", "pcm_s24le", mix)

# ---- pass 2: master
g = []
for i in range(len(files)):
    g.append("[%d:v]settb=1/%d,setpts=PTS-STARTPTS[v%d]" % (i, FPS, i))
cur = "[v0]"
for i in range(1, len(files)):
    t = shots[i].get("transition") or {}
    nxt = "[j%d]" % i
    if tframes[i] == 0:      # concat outputs a 1/1000000 timebase, which a following xfade refuses: reset it
        g.append("%s[v%d]concat=n=2:v=1:a=0,settb=1/%d%s" % (cur, i, FPS, nxt))
    else:
        g.append("%s[v%d]xfade=transition=%s:duration=%.6f:offset=%.6f%s" % (cur, i, t.get("type", "fade"), tframes[i] / FPS, starts[i] / FPS, nxt))
    cur = nxt
post = []
if fade_in:
    post.append("fade=t=in:st=0:d=%.3f" % fade_in)
if fade_out:
    post.append("fade=t=out:st=%.4f:d=%.3f" % (total - fade_out, fade_out))
g.append("%s%s[base]" % (cur, ",".join(post) if post else "null"))
cur = "[base]"
for k, (png, at, d, fd) in enumerate(cards):
    idx = len(files) + k
    g.append("[%d:v]format=yuva444p,fade=t=in:st=0:d=%.3f:alpha=1,fade=t=out:st=%.3f:d=%.3f:alpha=1,setpts=PTS-STARTPTS+%.4f/TB[c%d]"
             % (idx, fd, d - fd, fd, at, k))
    g.append("%s[c%d]overlay=0:0:eof_action=pass:format=yuv420p10[o%d]" % (cur, k, k))
    cur = "[o%d]" % k
g.append("%sformat=yuv420p,%s[vout]" % (cur, common.TAG_VF))
aidx = len(files) + len(cards)
tmp = out + ".part.mp4"
if args.progress:
    print("PROGRESS %d/%d master" % (len(shots), len(shots) + 1), flush=True)
common.ffmpeg(*inputs, "-i", mix, "-filter_complex", ";".join(g), "-map", "[vout]", "-map", "%d:a" % aidx,
              "-frames:v", total_f, *common.encode_args(preview=args.preview, gpu=args.gpu, pixels=W * H), *common.TAG_ARGS,
              "-r", FPS, "-c:a", "aac", "-b:a", "256k", "-ar", 48000, "-ac", 2, "-shortest", tmp)
shutil.move(tmp, out)

cuts = [{"shot": i + 1, "clip": s["clip"], "start": round(starts[i] / FPS, 4), "end": round((starts[i] + frames[i]) / FPS, 4),
         "transition": (s.get("transition") or {}).get("type", "cut") if i else "start"} for i, s in enumerate(shots)]
common.save(os.path.splitext(out)[0] + ".timeline.json",
            {"file": os.path.abspath(out), "width": W, "height": H, "fps": FPS, "frames": total_f, "duration": round(total, 4),
             "preview": bool(args.preview or args.res),
             "lufs": lufs if labels else None, "black_ok": [[round(a, 3), round(b, 3)] for a, b in black_ok], "cuts": cuts,
             "cards": [{"png": p, "at": round(a, 3), "dur": d} for p, a, d, _ in cards]})
print("%s  %dx%d  %.2f s  %d shots -> %s" % (kind, W, H, total, len(shots), out))
