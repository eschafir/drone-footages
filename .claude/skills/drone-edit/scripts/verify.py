#!/usr/bin/env python3
"""verify.py: unattended checks on the RENDERED file before anyone watches it (adapted from youtube-edit's verify-render).
Reads the MP4 and the .timeline.json render.py wrote next to it, and writes <render>.VERIFY.md + <render>.sheet.png:
  spec     size and 30 fps as rendered; color_primaries / color_transfer / color_space all bt709; on a final (not a
           preview) the video bitrate >= --min-mbps at 3840x2160, scaled by pixel count
  length   video frames == the timeline's frames (+-1); audio within 0.1 s of the video
  black    blackdetect (pix_th 0.08, 0.05 s and up) outside the intended windows (fade in, fade out, fadeblack): FAIL.
           A black flash at a join is the classic xfade offset bug
  freeze   freezedetect (0.5 s and up) outside the intended windows: FAIL. A shot whose source ran out is padded
           with its last frame (tpad), and that reads as a freeze
  loudness integrated within 1 LU of the timeline's target, true peak <= -0.5 dBTP
  sheet    one tile per shot (0.5 s in, and mid-shot), 4 columns at 480 px wide; VERIFY.md lists each tile's time
Usage:
  python3 verify.py --render videos/<p>/out/<p>-4k.mp4 [--min-mbps 40]
Exit 1 on any FAIL. Look at the sheet before handing the film over.
"""
import argparse, json, os, re, subprocess
import common

ap = argparse.ArgumentParser()
ap.add_argument("--render", required=True)
ap.add_argument("--timeline")
ap.add_argument("--min-mbps", type=float, default=40.0)
args = ap.parse_args()

R = args.render
T = common.load(args.timeline or os.path.splitext(R)[0] + ".timeline.json")
rows, fails = [], 0


def check(name, ok, detail):
    global fails
    fails += 0 if ok else 1
    rows.append("| %s | %s | %s |" % (name, "ok" if ok else "**FAIL**", detail))


def outside(a, b, windows, tol=0.1):
    return not any(a >= w0 - tol and b <= w1 + tol for w0, w1 in windows)


j = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate,nb_frames,duration,bit_rate,"
                    "color_primaries,color_transfer,color_space", "-of", "json", R], capture_output=True, text=True, check=True).stdout
streams = json.loads(j)["streams"]
v = next(s for s in streams if s["codec_type"] == "video")
a = next((s for s in streams if s["codec_type"] == "audio"), None)
check("size", (v["width"], v["height"]) == (T["width"], T["height"]), "%sx%s (want %sx%s)" % (v["width"], v["height"], T["width"], T["height"]))
check("fps", v["r_frame_rate"] in ("30/1", "%d/1" % T["fps"]), v["r_frame_rate"])
tags = [v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")]
check("bt709", tags == ["bt709"] * 3, "primaries %s, transfer %s, space %s" % tuple(tags))
mbps = int(v.get("bit_rate") or 0) / 1e6
floor = args.min_mbps * v["width"] * v["height"] / (3840 * 2160)
if T.get("preview"):
    rows.append("| bitrate | skip | %.1f Mb/s (preview) |" % mbps)
else:
    check("bitrate", mbps >= floor, "%.1f Mb/s (floor %.1f)" % (mbps, floor))
nf = int(v.get("nb_frames") or 0)
check("frames", abs(nf - T["frames"]) <= 1, "%d (timeline %d)" % (nf, T["frames"]))
if a:
    check("a/v length", abs(float(a["duration"]) - float(v["duration"])) <= 0.1, "audio %.3f s, video %.3f s" % (float(a["duration"]), float(v["duration"])))
else:
    check("audio", False, "no audio stream")

ok_windows = T.get("black_ok", [])
err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", R, "-map", "0:v:0", "-vf",
                      "scale=320:-2,blackdetect=d=0.05:pix_th=0.08,freezedetect=n=0.001:d=0.5", "-an", "-f", "null", "-"],
                     capture_output=True, text=True).stderr
blacks = [(float(x), float(y)) for x, y in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
bad = [(x, y) for x, y in blacks if outside(x, y, ok_windows)]
check("black", not bad, ", ".join("%.2f-%.2f s" % b for b in bad) or "%d intended black run(s) only" % len(blacks))
fs = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", err)]
fe = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", err)] + [T["duration"]] * (len(re.findall(r"freeze_start", err)) - len(re.findall(r"freeze_end", err)))
frz = [(x, y) for x, y in zip(fs, fe) if outside(x, y, ok_windows)]
check("freeze", not frz, ", ".join("%.2f-%.2f s" % f for f in frz) or "none")

if T.get("lufs") is not None and a:
    e = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", R, "-map", "0:a:0", "-af", "ebur128=framelog=quiet:peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    summ = e[e.rfind("Summary:"):]
    li = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ).group(1))
    tp = float(re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summ).group(1))
    check("loudness", abs(li - T["lufs"]) <= 1.0, "%.1f LUFS (target %s)" % (li, T["lufs"]))
    check("true peak", tp <= -0.5, "%.1f dBTP" % tp)

times = []
for c in T["cuts"]:
    times += [min(c["end"] - 0.05, c["start"] + 0.5), (c["start"] + c["end"]) / 2]
sheet = os.path.splitext(R)[0] + ".sheet.png"
sel = "+".join("eq(n\\,%d)" % round(t * T["fps"]) for t in times)
cols = 4
w = 480 if T["width"] >= T["height"] else 270
common.ffmpeg("-i", R, "-map", "0:v:0", "-vf", "select='%s',scale=%d:-2,tile=%dx%d:padding=4" % (sel, w, cols, -(-len(times) // cols)),
              "-fps_mode", "passthrough", "-frames:v", 1, "-update", 1, sheet)

md = ["# Verify: %s" % os.path.basename(R), "", "| Check | Result | Detail |", "|---|---|---|"] + rows
md += ["", "Sheet: `%s` (row-major, %d per row). Tiles:" % (os.path.basename(sheet), cols), ""]
md += ["- tile %d: %.2f s (shot %d, %s)" % (i + 1, t, i // 2 + 1, "start" if i % 2 == 0 else "middle") for i, t in enumerate(times)]
out = os.path.splitext(R)[0] + ".VERIFY.md"
open(out, "w").write("\n".join(md) + "\n")
print("\n".join(rows))
print("%s -> %s" % ("FAIL" if fails else "PASS", out))
raise SystemExit(1 if fails else 0)
