#!/usr/bin/env python3
"""plan-cut.py: a first cut list from analysis.json + beats.json, for Claude to refine after looking at the sheets.
Input : <project>/clips.json, analysis.json, beats.json (optional: without it shots sit on a fixed 3 s grid)
Output: <project>/cutlist.json (render.py's input; the format is documented in SKILL.md)
How the draft is built:
  length   the bar closest to --target seconds, or with --max the last bar at or under it (a ceiling): the music
           ends on a bar, never mid-phrase. When unique footage runs out first, the film ends early instead.
  pace     shot length follows the music energy: ~4.2 s in quiet parts down to ~2.4 s at full energy, snapped to
           the beat grid (half-bar steps). Each cut lands on a beat.
  shots    greedy, in order: the best unused stretch whose length fits, never the same clip twice in a row, each
           clip at most --max-uses times (never the same source moment twice), a penalty for repeating a clip or a
           camera move. The opener gets the best long stretch.
           Inside a candidate, the sub-span with the best mean window score is used.
  slow-mo  up to --slowmo shots in the quietest slots run at speed 0.5 (60 fps source -> clean 2x at 30 fps)
  joins    hard cuts by default; a 0.6 s dissolve where the music is quiet (energy < 0.3) or drops away; one
           zoomin whip at the biggest energy rise (the drop); fade in from black, fade out to black
  vertical the same, on its own --vertical-target seconds, a little faster, crop centred (x 0.5) to edit by hand
  style    --style (styles/<name>.json "plan"): the pace, a whole-bar grid with a shortest shot, no drop whip, and
           for real estate a walk through the clips in file order (shoot order usually follows the tour). Slow motion
           only on 50/60 fps clips; portrait clips only in the vertical short.
Usage:
  python3 plan-cut.py --project videos/<p> [--target 60 | --max 45] [--vertical-target 25 | --vertical-max 30] [--slowmo 2]
                      [--music-start 8.0] [--style realestate] [--title "..." --sub "..." --end "..." --end-sub "..."]
                      [--end-line "..." ...] [--logo x.png]
"""
import argparse, math, os
import numpy as np
import common

ap = argparse.ArgumentParser()
ap.add_argument("--project", required=True)
ap.add_argument("--target", type=float, default=60)
ap.add_argument("--max", type=float, help="length ceiling, seconds (overrides --target)")
ap.add_argument("--vertical-target", type=float, default=25, help="0 = no vertical version")
ap.add_argument("--vertical-max", type=float, help="the short's length ceiling (overrides --vertical-target)")
ap.add_argument("--max-uses", type=int, default=2, help="times one clip may appear")
ap.add_argument("--slowmo", type=int, default=2)
ap.add_argument("--music-start", type=float, default=0.0, help="skip this much of the track (an intro); beats shift with it")
ap.add_argument("--title", default="[TITLE]")
ap.add_argument("--sub", default="[LOCATION · DATE]")
ap.add_argument("--end", default="[YOUR NAME]")
ap.add_argument("--end-sub")
ap.add_argument("--end-line", action="append", default=[])
ap.add_argument("--logo")
ap.add_argument("--style")
ap.add_argument("--out")
args = ap.parse_args()

P = args.project
ST = common.load_style(args.style)
PL = ST["plan"]
clip_list = common.load(os.path.join(P, "clips.json"))["clips"]
clips = {c["id"]: c for c in clip_list}
order = {c["id"]: i for i, c in enumerate(clip_list)}
SLOW_CAP = float("inf") if ST["cut"].get("max_slowmo_shots") is None else int(ST["cut"]["max_slowmo_shots"])
args.slowmo = int(min(args.slowmo, SLOW_CAP))
A = common.load(os.path.join(P, "analysis.json"))
bpath = os.path.join(P, "beats.json")
B = common.load(bpath) if os.path.exists(bpath) else None
if B and args.music_start:                                             # film time = track time - start
    m0 = args.music_start
    B["beats"] = [round(t - m0, 3) for t in B["beats"] if t >= m0]
    B["bars"] = [round(t - m0, 3) for t in B["bars"] if t >= m0]
    B["sections"] = [[round(t - m0, 3), d] for t, d in B["sections"] if t >= m0]
    B["energy"] = [[round(t - m0, 2), e] for t, e in B["energy"] if t >= m0]
    B["duration"] -= m0
WIN = A["window"]


def energy_at(t):
    if not B:
        return 0.5
    e = B["energy"]
    return e[min(len(e) - 1, int(t / 0.5))][1]


def grid(target, pace, ceiling=False):
    """Cut times (beats) from 0 to the end bar, and the end time."""
    if B:
        beats, bars = np.array(B["beats"]), np.array(B["bars"])
        ends = bars[(bars > 8) & (bars <= B["duration"] - 0.5)]
        if ceiling:
            ends = ends[ends <= target + 0.01]
        if not len(ends):
            common.die("music too short for a cut" if not ceiling else "no bar of the music ends under %.1f s" % target)
        end = float(ends[np.argmin(np.abs(ends - target))])
        step = 2 * (beats[1] - beats[0]) if len(beats) > 1 else 1.0     # half-bar steps
    else:
        end, step = float(target), 0.5
        beats = np.arange(0, end + 1e-6, 0.5)
    shortest = 1.6
    if PL.get("min_bars"):                       # whole bars, and at least the style's shortest shot
        step *= 2
        shortest = step * max(int(PL["min_bars"]), math.ceil(float(ST["cut"].get("min_shot") or 0) / step - 0.02))
    cuts, t = [0.0], 0.0
    while True:
        want = pace(energy_at(t))
        n = max(1, round(want / step), round(shortest / step))
        nxt = t + n * step
        nxt = float(beats[np.argmin(np.abs(beats - nxt))]) if B else nxt
        # (up to 1.6 s past the planned cut too: closer than that, the next shot could not cut on it either)
        due = [s[0] for s in (B or {}).get("sections", []) if t + shortest <= s[0] < nxt + shortest]
        if due:                                                        # a drop or a breakdown always gets a cut
            nxt = float(due[0])
        if end - nxt < shortest:
            break
        cuts.append(nxt); t = nxt
    return cuts, end


def best_subspan(cid, a, b, span):
    """Start of the span-long stretch inside [a, b] with the best mean window score."""
    wins = [w for w in A["clips"][cid]["windows"] if a - 1e-6 <= w["t"] and w["t"] + WIN <= b + 1e-6]
    if not wins or b - a <= span + 1e-6:
        return a
    k = max(1, int(round(span / WIN)))
    best, start = -1.0, a
    for i in range(0, max(1, len(wins) - k + 1)):
        q = float(np.mean([w["q"] for w in wins[i:i + k]]))
        s = min(wins[i]["t"], b - span)
        if q > best:
            best, start = q, s
    return round(start, 2)


def plan(target, pace, n_slow, transitions=True, ceiling=False, vertical=False):
    cuts, end = grid(target, pace, ceiling)
    n = len(cuts)
    bounds = cuts + [end]
    lens = [bounds[i + 1] - bounds[i] for i in range(n)]
    energies = [energy_at(c + 0.5) for c in cuts]
    # joins: transition INTO shot i (centred on the cut)
    tr = [{"type": "cut", "dur": 0.0} for _ in range(n)]
    if transitions and B:
        sec = {round(s[0], 2): s[1] for s in B.get("sections", [])}
        rises = [(d, i) for i, c in enumerate(cuts) for t, d in sec.items() if i and abs(c - t) < 0.08 and d > 0]
        if rises and PL.get("drop_whip", True):
            tr[max(rises)[1]] = {"type": "zoomin", "dur": 0.4}
        for i in range(1, n):
            falls = any(abs(cuts[i] - t) < 0.08 and d < 0 for t, d in sec.items())
            if tr[i]["type"] == "cut" and (falls or (energies[i] < 0.3 and energies[i - 1] < 0.3)):
                tr[i] = {"type": "fade", "dur": float(PL.get("fade", 0.6))}
    td = [t["dur"] for t in tr] + [0.0]
    durs = [lens[i] + td[i] / 2 + td[i + 1] / 2 for i in range(n)]
    speed = [1.0] * n
    for i in sorted(range(n - 1), key=lambda i: energies[i])[:n_slow]:
        if energies[i] < 0.6:
            speed[i] = 0.5

    pool = [dict(c, clip=cid) for cid, v in A["clips"].items() for c in v["candidates"]
            if vertical or clips.get(cid, {}).get("orientation") != "portrait"]

    def slow_ok(c):
        return float(clips.get(c["clip"], {}).get("fps") or 60) >= 48

    used_clip, shots, prev = {}, [], None
    for i in range(n):
        span = durs[i] * speed[i]
        if speed[i] < 1.0 and not any(slow_ok(c) and c["out"] - c["in"] >= span for c in pool):
            speed[i], span = 1.0, durs[i]                                # no 50/60 fps footage left: no slow-mo
        best, bscore = None, -9
        for c in pool:
            if c["out"] - c["in"] < span - 1e-6 or (prev and c["clip"] == prev["clip"]) or used_clip.get(c["clip"], 0) >= args.max_uses:
                continue
            if speed[i] < 1.0 and not slow_ok(c):
                continue
            s = c["score"] - 0.25 * used_clip.get(c["clip"], 0) - (0.05 if prev and c["camera"] == prev["camera"] else 0)
            if PL.get("order") == "file" and prev and order.get(c["clip"], 0) < order.get(prev["clip"], 0):
                s -= 0.3                                                 # a tour walks forward through the shoot
            if i == 0:
                s += 0.02 * (c["out"] - c["in"])                         # the opener: a long, settled stretch
            if s > bscore:
                best, bscore = c, s
        if best is None and speed[i] == 1.0 and speed.count(0.5) < SLOW_CAP:
            # not enough footage: stretch with slow-mo
            speed[i] = 0.5; span = durs[i] * 0.5
            best = max((c for c in pool if c["out"] - c["in"] >= span and slow_ok(c) and not (prev and c["clip"] == prev["clip"])
                        and used_clip.get(c["clip"], 0) < args.max_uses), key=lambda c: c["score"], default=None)
        if best is None and i >= 2 and cuts[i] >= 8:
            # unique footage ran out: end here rather than repeat (the last shot loses its half of the next join)
            shots[-1]["dur"] = round(shots[-1]["dur"] - td[i] / 2, 3)
            print("note: unique footage runs out at %.1f s, the film ends there (not at %.1f s)" % (cuts[i], end))
            return shots, cuts[i]
        if best is None:
            common.die("not enough usable footage for a %.0f s cut: lower --target, or --min-q in analyze.py" % target)
        start = best_subspan(best["clip"], best["in"], best["out"], span)
        pool.remove(best)
        for a, b in ((best["in"], start), (start + span, best["out"])):  # what is left of the stretch stays usable
            if b - a >= 1.5:
                pool.append(dict(best, **{"in": round(a, 2), "out": round(b, 2)}))
        used_clip[best["clip"]] = used_clip.get(best["clip"], 0) + 1
        shots.append({"clip": best["clip"], "in": round(start, 3), "dur": round(durs[i], 3), "speed": speed[i],
                      "transition": tr[i], "at": round(cuts[i], 3), "camera": best["camera"], "score": best["score"]})
        prev = best
    return shots, end


q, l = float(PL["pace_quiet"]), float(PL["pace_loud"])
vq, vl = float(PL["vpace_quiet"]), float(PL["vpace_loud"])
shots, end = plan(args.max or args.target, lambda e: q - (q - l) * e, args.slowmo, ceiling=bool(args.max))
C = common.cutlist_skeleton(P, B, args.music_start, args.title, args.sub, args.end, args.end_sub, style=ST,
                            end_lines=args.end_line, logo=args.logo)
C.update(duration=round(end, 3), shots=shots)
if args.vertical_max or args.vertical_target:
    vshots, vend = plan(args.vertical_max or args.vertical_target, lambda e: vq - (vq - vl) * e, min(1, args.slowmo),
                        ceiling=bool(args.vertical_max), vertical=True)
    for s in vshots:
        s["x"] = 0.5
    C["vertical"] = {"width": 1080, "height": 1920, "duration": round(vend, 3), "shots": vshots,
                     "title": {"at": 0.8, "dur": 3.0}, "end": {"dur": 3.0}}
out = args.out or os.path.join(P, "cutlist.json")
common.save(out, C)
print("%d shots, %.1f s%s -> %s" % (len(shots), end, ", vertical %d shots %.1f s" % (len(C["vertical"]["shots"]), C["vertical"]["duration"])
                                     if "vertical" in C else "", out))
for s in shots:
    print("  %6.2f  %s %6.2f +%.2f  x%.1f  %-7s %s" % (s["at"], s["clip"], s["in"], s["dur"], s["speed"], s["transition"]["type"], s["camera"]))
