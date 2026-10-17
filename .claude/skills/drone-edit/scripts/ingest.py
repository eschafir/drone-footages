#!/usr/bin/env python3
"""ingest.py: register the raw drone clips of one edit and make graded proxies for analysis and contact sheets.
Input : a folder of camera files (DJI_..._0064_D.MP4 and so on; anything ffprobe reads)
Output: <project>/clips.json   one entry per clip: id, absolute source path, duration, fps, size (as displayed, after
                               rotation), orientation, pixel format, encoder, colour profile
        <project>/proxies/<id>.mp4   960x540, every 2nd frame of a 50/60p source, H.264, graded with the same grade the
                               render uses; a portrait or non-16:9 clip is letterboxed (its picture box is in clips.json)
Colour profile per clip (common.detect_profile): dlogm (DJI D-Log M: the LUT), rec709 (phones, normal profiles: no
LUT, a lighter look), hlg / pq (HDR phone video: a generated tone-mapping cube). --profile forces one for every clip;
--profile graded is for clips the user already colour graded: no LUT and no look, the colour is kept as it is.
The id is the DJI file counter (0064) or the file stem. Proxies are skipped when they already exist, are newer than
the source and the LUT, and the profile is unchanged (--force to rebuild). Decoding uses VideoToolbox when available.
Usage:
  python3 ingest.py --raw raw --project videos/<p> [--style realestate] [--lut path/to.cube] [--profile auto|dlogm|rec709|hlg|pq] [--force]
"""
import argparse, glob, os, re, shutil, sys
import common

ap = argparse.ArgumentParser()
ap.add_argument("--raw", required=True, help="folder with the camera files")
ap.add_argument("--project", required=True)
ap.add_argument("--lut", help="D-Log M -> Rec.709 .cube (default: first .cube in assets/luts/)")
ap.add_argument("--style", help="styles/<name>.json: its look is used for the proxies (default: drone)")
ap.add_argument("--profile", default="auto", choices=("auto",) + common.PROFILES)
ap.add_argument("--force", action="store_true")
ap.add_argument("--no-hwaccel", action="store_true")
args = ap.parse_args()

exts = (".mp4", ".mov", ".mxf", ".m4v")
files = sorted(f for f in glob.glob(os.path.join(args.raw, "*")) if f.lower().endswith(exts))
if not files:
    common.die("no video files in %s" % args.raw)

lut = common.find_lut(args.lut)
look = common.load_style(args.style)["look"]
prox_dir = os.path.join(args.project, "proxies")
os.makedirs(prox_dir, exist_ok=True)
hw = [] if args.no_hwaccel or sys.platform != "darwin" else ["-hwaccel", "videotoolbox"]
cj = os.path.join(args.project, "clips.json")
before = {c["src"]: c for c in common.load(cj)["clips"]} if os.path.exists(cj) else {}
PW, PH = 960, 540

clips, seen = [], set()
for f in files:
    p = common.probe(f)
    m = re.search(r"_(\d{4})(?:_[A-Z])?$", os.path.splitext(os.path.basename(f))[0])
    cid = m.group(1) if m else re.sub(r"[^A-Za-z0-9_-]", "-", os.path.splitext(os.path.basename(f))[0])
    if cid in seen:
        cid = re.sub(r"[^A-Za-z0-9_-]", "-", os.path.splitext(os.path.basename(f))[0])
    seen.add(cid)
    proxy = os.path.join(prox_dir, cid + ".mp4")
    old = before.get(os.path.abspath(f)) or {}
    if args.profile != "auto":
        profile, stats = args.profile, {}
    elif old.get("profile_auto") and old.get("mtime") == os.path.getmtime(f):
        profile, stats = old["profile_auto"], old.get("profile_stats", {})
    else:
        profile, stats = common.detect_profile(f, p)
    if profile == "dlogm" and not lut:
        print("note: %s is D-Log M but there is no LUT in %s: the approximate curve is used (install the DJI LUT)" % (cid, common.LUT_DIR))
    # the picture inside the proxy: 16:9 fills it, anything else is letterboxed so the sheets show the whole frame
    w, h = p["width"], p["height"]
    if abs(w / h - PW / PH) < 0.01:
        bw, bh = PW, PH
    else:
        k = min(PW / w, PH / h)
        bw, bh = int(w * k) // 2 * 2, int(h * k) // 2 * 2
    box = [(PW - bw) // 2, (PH - bh) // 2, bw, bh]
    pad = "" if (bw, bh) == (PW, PH) else ",pad=%d:%d:%d:%d:black" % (PW, PH, box[0], box[1])
    grade = common.grade_vf(lut, look, profile) + ",format=yuv420p"
    newest = max(os.path.getmtime(f), os.path.getmtime(lut) if lut and profile == "dlogm" else 0)
    if args.force or not os.path.exists(proxy) or os.path.getmtime(proxy) < newest or old.get("profile", profile) != profile \
            or old.get("look", look) != look:
        print("proxy %s  (%dx%d %.2f fps %s, %s, %.1fs)" % (cid, w, h, p["fps"], p["pix_fmt"], profile, p["duration"]))
        tmp = proxy + ".part.mp4"
        # every 2nd frame of a 60p source, never an fps conversion: 59.94 -> 30 duplicates a frame now and then,
        # which reads as a motion stutter in analyze.py
        step = "framestep=2," if p["fps"] > 40 else ""
        # scale first (grading 4K is slow), grade the picture, letterbox last so the bars stay black
        vf = step + "scale=%d:%d:flags=bicubic," % (bw, bh) + grade + pad + ",setsar=1"
        common.ffmpeg(*hw, "-i", f, "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf, "-fps_mode", "passthrough",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-g", common.FPS, tmp)
        shutil.move(tmp, proxy)
    clips.append({"id": cid, "src": os.path.abspath(f), "proxy": os.path.relpath(proxy, args.project),
                  "duration": round(p["duration"], 3), "fps": round(p["fps"], 3), "width": w, "height": h, "rotation": p["rotation"],
                  "orientation": "portrait" if h > w else "landscape", "proxy_box": box,
                  "pix_fmt": p["pix_fmt"], "transfer": p["transfer"], "encoder": p["encoder"], "created": p["created"],
                  "has_audio": p["audio"], "profile": profile, "profile_auto": profile if args.profile == "auto" else old.get("profile_auto"),
                  "profile_stats": stats or old.get("profile_stats", {}), "mtime": os.path.getmtime(f), "look": look})

common.save(cj, {"lut": lut, "clips": clips})
total = sum(c["duration"] for c in clips)
kinds = sorted({c["profile"] for c in clips})
print("%d clips, %.1f s of footage (%s) -> %s" % (len(clips), total, ", ".join("%s %d" % (k, sum(c["profile"] == k for c in clips)) for k in kinds), cj))
