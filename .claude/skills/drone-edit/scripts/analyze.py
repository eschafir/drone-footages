#!/usr/bin/env python3
"""analyze.py: score every half second of every clip and list the usable stretches, so the cut starts from footage
that is smooth, sharp and well exposed. The scores filter; Claude's eyes on the contact sheets decide.
Input : <project>/clips.json and proxies/ (ingest.py)
Output: <project>/analysis.json   per clip: windows [{t, q, smooth, sharp, expo, flow}] and candidates
                                  [{in, out, score, camera, speed}] (runs of good windows, source seconds)
        <project>/sheets/<id>.png one tile per second (row-major, tile n = n seconds), 6 columns at 320 px
        <project>/sheets/overview.png   the middle frame of every clip, in clip order
        <project>/ANALYSIS.md     the candidate table and how to read the sheets
Per 0.5 s window, on 320x180 grey frames, ~10 per second (every k-th proxy frame), shifts in px per 0.1 s:
  smooth  exp(-jerk / --jerk-ref): jerk = mean |second difference| of the global image shift (phase correlation),
          in px/frame^2. A steady pan or a hover scores ~1; a stick snap, a gimbal nudge or wind shake scores low
  sharp   Laplacian variance relative to the clip's own 90th percentile (capped at 1): catches a soft moment
          (motion blur, focus hunt) inside a clip without marking calm water down against a city skyline
  expo    1 minus penalties for a dark/bright mean, clipped highlights and crushed blacks
  q       0.5 smooth + 0.3 sharp + 0.2 expo
The first --head and last --tail seconds of each clip are never candidates (take-off lurch, stick release).
--style takes --jerk-ref, --head and --tail from styles/<style>.json (handheld / gimbal interiors are less steady than a
drone, so the real-estate style is more forgiving); explicit flags win. A letterboxed proxy (portrait clip) is measured
inside its picture box only.
camera  the move, from the mean image shift: "yaw/truck right" means the content slides left.
Usage:
  python3 analyze.py --project videos/<p> [--style realestate] [--min-q 0.6] [--min-len 1.5]
"""
import argparse, os, subprocess
import numpy as np
import common

ap = argparse.ArgumentParser()
ap.add_argument("--project", required=True)
ap.add_argument("--min-q", type=float, default=0.6, help="a window at or above this is usable")
ap.add_argument("--min-len", type=float, default=1.5, help="shortest candidate run, seconds")
ap.add_argument("--style")
ap.add_argument("--head", type=float)
ap.add_argument("--tail", type=float)
ap.add_argument("--jerk-ref", type=float)
ap.add_argument("--no-sheets", action="store_true")
args = ap.parse_args()
for k, v in common.load_style(args.style)["analyze"].items():
    if getattr(args, k) is None:
        setattr(args, k, v)

P = args.project
C = common.load(os.path.join(P, "clips.json"))
AW, AH, AFPS, WIN = 320, 180, 10, 0.5
HANN = np.outer(np.hanning(AH), np.hanning(AW))


def frames(path, box=None):
    """Every k-th proxy frame (~AFPS per second) and the seconds between them. Picking frames, not an fps filter:
    resampling 29.97 to 10 spaces the samples unevenly, and uneven spacing looks exactly like jerky flying.
    box: [x, y, w, h] of the picture in a letterboxed proxy; the black bars would count as dark, static image."""
    fps = common.probe(path)["fps"]
    k = max(1, int(round(fps / AFPS)))
    crop = "crop=%d:%d:%d:%d," % (box[2], box[3], box[0], box[1]) if box and box[2:] != [960, 540] else ""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0", "-vf", "select='not(mod(n\\,%d))',%sscale=%d:%d,format=gray" % (k, crop, AW, AH),
           "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, AH, AW).astype(np.float32), k / fps


def shift(a, b):
    """Global shift of b relative to a in px (+x = content moved right), by phase correlation with a parabolic peak."""
    R = np.fft.fft2(b * HANN) * np.conj(np.fft.fft2(a * HANN))
    r = np.fft.ifft2(R / (np.abs(R) + 1e-9)).real
    y, x = np.unravel_index(int(r.argmax()), r.shape)

    def sub(c, m, n):
        d = m - 2 * c + n
        return 0.0 if d == 0 else 0.5 * (m - n) / d
    dx = x + sub(r[y, x], r[y, x - 1], r[y, (x + 1) % AW])
    dy = y + sub(r[y, x], r[y - 1, x], r[(y + 1) % AH, x])
    return (dx - AW if dx > AW / 2 else dx), (dy - AH if dy > AH / 2 else dy)


def laplace_var(f):
    lap = f[1:-1, :-2] + f[1:-1, 2:] + f[:-2, 1:-1] + f[2:, 1:-1] - 4 * f[1:-1, 1:-1]
    return float(lap.var())


def camera_label(dx, dy, speed):
    if speed < 0.15:
        return "hover / slow push"
    horiz = "yaw/truck " + ("right" if dx < 0 else "left")
    vert = "tilt/rise " + ("up" if dy > 0 else "down")
    if abs(dx) > 2 * abs(dy):
        return horiz
    if abs(dy) > 2 * abs(dx):
        return vert
    return horiz + " + " + vert.split()[-1]


raw = {}
for c in C["clips"]:
    fr, dt = frames(os.path.join(P, c["proxy"]), c.get("proxy_box"))
    n = len(fr)
    sh = np.array([shift(fr[i - 1], fr[i]) for i in range(1, n)] or [(0.0, 0.0)]) * (0.1 / dt)   # px per 0.1 s
    sh = np.vstack([sh[:1], sh])                                  # frame i: shift from i-1 to i
    # jerk on velocity smoothed over 3 samples: the sub-pixel estimate wobbles +-0.2 px sample to sample on a perfectly
    # steady pan, while a stick snap or a gimbal nudge plays out over 0.3-1 s and survives the smoothing
    vel = np.vstack([np.convolve(np.pad(sh[:, j], 1, mode="edge"), np.ones(3) / 3, mode="valid") for j in (0, 1)]).T
    acc = np.abs(np.diff(vel, n=2, axis=0)).sum(axis=1)
    acc = np.concatenate([[acc[0] if len(acc) else 0.0]] * 2 + [acc])
    sharp = np.array([laplace_var(f) for f in fr])
    luma = fr.reshape(n, -1)
    raw[c["id"]] = {"n": n, "dt": dt, "shift": sh, "acc": acc, "sharp": sharp,
                    "mean": luma.mean(axis=1), "p2": np.percentile(luma, 2, axis=1), "p98": np.percentile(luma, 98, axis=1),
                    "clip_pct": (luma >= 250).mean(axis=1)}
    print("analyzed %s  %d frames" % (c["id"], n))

out = {"window": WIN, "params": vars(args), "clips": {}}
for c in C["clips"]:
    r = raw[c["id"]]
    sharp_ref = float(np.percentile(r["sharp"], 90)) or 1.0
    wins, per = [], max(1, int(round(WIN / r["dt"])))
    for k in range(0, r["n"] - per + 1, per):
        s = slice(k, k + per)
        jerk = float(r["acc"][s].mean())
        smooth = float(np.exp(-jerk / args.jerk_ref))
        sharp = float(min(1.0, r["sharp"][s].mean() / sharp_ref))
        m, p2, p98, cp = (float(r[key][s].mean()) for key in ("mean", "p2", "p98", "clip_pct"))
        expo = 1.0 - min(1.0, max(0.0, (45 - m) / 45) + max(0.0, (m - 205) / 50) + min(0.5, cp * 10) + (0.15 if p2 < 2 else 0.0))
        q = 0.5 * smooth + 0.3 * sharp + 0.2 * expo
        dx, dy = (float(v) for v in r["shift"][s].mean(axis=0))
        wins.append({"t": round(k * r["dt"], 2), "q": round(q, 3), "smooth": round(smooth, 3), "sharp": round(sharp, 3),
                     "expo": round(expo, 3), "jerk": round(jerk, 3), "flow": [round(dx, 2), round(dy, 2)], "luma": round(m, 1)})
    lo, hi = args.head, c["duration"] - args.tail
    cands, run = [], []
    for w in wins + [None]:
        ok = w is not None and w["q"] >= args.min_q and w["t"] >= lo and w["t"] + WIN <= hi
        if ok:
            run.append(w)
            continue
        if run and len(run) * WIN >= args.min_len:
            dx = float(np.mean([x["flow"][0] for x in run])); dy = float(np.mean([x["flow"][1] for x in run]))
            spd = float(np.hypot(dx, dy))
            cands.append({"in": run[0]["t"], "out": round(run[-1]["t"] + WIN, 2), "score": round(float(np.mean([x["q"] for x in run])), 3),
                          "camera": camera_label(dx, dy, spd), "speed": round(spd, 2), "flow": [round(dx, 2), round(dy, 2)]})
        run = []
    cands.sort(key=lambda x: -x["score"])
    out["clips"][c["id"]] = {"duration": c["duration"], "candidates": cands, "windows": wins}
common.save(os.path.join(P, "analysis.json"), out)

lines = ["# Analysis", "", "Scores are 0-1 (q = 0.5 smooth + 0.3 sharp + 0.2 exposure). Source seconds. "
         "Sheet tiles are one per second, row-major, 6 per row: tile n is at n s.", "",
         "## Clips", "", "Slow motion (speed below 1) only on 50/60 fps clips: a 24/30 fps clip would repeat frames. "
         "A 24 fps clip is shown at 30 fps with a repeated frame every 4th (a slight judder on fast pans). "
         "Portrait clips only fit the 9:16 short. Profile: the colour conversion used (dlogm = DJI LUT, rec709 = "
         "normal, hlg / pq = HDR tone-mapped, graded = left exactly as delivered).", "",
         "| Clip | Length | Size | fps | Slow-mo | Profile |", "|---|---|---|---|---|---|"]
for c in C["clips"]:
    lines.append("| %s | %.1f s | %dx%d %s | %.2f | %s | %s |" % (c["id"], c["duration"], c.get("width", 0), c.get("height", 0),
                 c.get("orientation", "landscape"), c.get("fps", 0), "yes" if c.get("fps", 60) >= 48 else "no",
                 c.get("profile", "dlogm")))
lines += ["", "## Usable stretches", "", "| Clip | Length | Candidate | Score | Camera |", "|---|---|---|---|---|"]
for c in C["clips"]:
    cs = out["clips"][c["id"]]["candidates"]
    if not cs:
        lines.append("| %s | %.1f s | none above %.2f | | |" % (c["id"], c["duration"], args.min_q))
    for i, x in enumerate(cs):
        lines.append("| %s | %s | %.1f-%.1f s (%.1f s) | %.2f | %s |" % (c["id"] if i == 0 else "", "%.1f s" % c["duration"] if i == 0 else "",
                     x["in"], x["out"], x["out"] - x["in"], x["score"], x["camera"]))
open(os.path.join(P, "ANALYSIS.md"), "w").write("\n".join(lines) + "\n")

if not args.no_sheets:
    sd = os.path.join(P, "sheets")
    os.makedirs(sd, exist_ok=True)
    for c in C["clips"]:
        rows = max(1, -(-int(c["duration"] + 0.999) // 6))
        common.ffmpeg("-i", os.path.join(P, c["proxy"]), "-vf", "fps=1,scale=320:180,tile=6x%d:padding=4:color=black" % rows,
                      "-frames:v", 1, "-update", 1, os.path.join(sd, c["id"] + ".png"))
    ins = []
    for c in C["clips"]:
        ins += ["-ss", "%.2f" % (c["duration"] / 2), "-i", os.path.join(P, c["proxy"])]
    k = len(C["clips"])
    graph = "".join("[%d:v]scale=480:270,setsar=1,trim=end_frame=1[s%d];" % (i, i) for i in range(k))
    graph += "".join("[s%d]" % i for i in range(k)) + "concat=n=%d:v=1:a=0,tile=4x%d:padding=4:color=black" % (k, -(-k // 4))
    common.ffmpeg(*ins, "-filter_complex", graph, "-frames:v", 1, "-update", 1, os.path.join(sd, "overview.png"))
print("%d candidates -> %s, sheets in %s" % (sum(len(v["candidates"]) for v in out["clips"].values()),
                                              os.path.join(P, "ANALYSIS.md"), os.path.join(P, "sheets")))
