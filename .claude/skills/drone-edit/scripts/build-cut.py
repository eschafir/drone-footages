#!/usr/bin/env python3
"""build-cut.py: a hand-picked cut, written as a short spec in BARS, turned into a render-ready cutlist.json.
Claude (or anyone editing by hand) decides shots, order, speed and joins in cut-spec.json; this places every cut on a
bar of the music, works out the overlaps, and refuses a spec that would render badly.
Input : <project>/cut-spec.json, beats.json, clips.json
  {
    "music_start_bar": 4,                       # index into beats.json "bars": the film starts on this bar
    "shots": [
      {"clip": "0064", "in": 2.0, "bars": 2, "speed": 0.5, "transition": ["cut", 0], "note": "open sea under the title"},
      {"clip": "0066", "in": 1.5, "bars": 2, "transition": ["fade", 0.8]}
    ],
    "vertical": {"music_start_bar": 14, "shots": [{"clip": "0070", "in": 1.0, "bars": 2, "x": 0.5},
                                                  {"clip": "0066", "in": 3.0, "bars": 2, "pan": [0.78, 0.62]}]}
  }
  in        source seconds where the shot starts AT THE CUT (a dissolve into it borrows half its length before that)
  bars      whole bars of music the shot holds; speed 0.5-1.0 (0.5 = 2x slow motion)
  transition the join INTO the shot: cut | fade | fadeblack | zoomin | smoothleft | smoothright | smoothup | smoothdown
  x / pan   vertical only: where the 9:16 window sits across the frame (0-1), or [from, to] over the shot
  label     optional lower-third on the shot ("Kitchen"), at most 28 characters; dropped unless --labels
Output: <project>/cutlist.json (title/end/music settings from the flags; render.py's input)
Refuses (exit 1, every problem listed): a shot outside the clip's usable range (the style's head / tail: drone 0.9 s /
0.6 s), a film longer than --max, the same clip twice in a row, a clip used more than --max-uses times, two shots
showing the same source moment, a bar index past the end of the track, slow motion on a clip under 48 fps, a portrait
clip in the 16:9 film, and what the style forbids (styles/<style>.json "cut": transitions, shortest shot, number of
slow-motion shots).
Usage:
  python3 build-cut.py --project videos/<p> [--spec cut-spec.json] [--style realestate] [--max 45] [--vertical-max 30]
                       [--title "..." --sub "..." --end "..." --end-sub "..."] [--end-line "..." ...] [--logo x.png]
                       [--labels]
  python3 build-cut.py --project . --config build-cut.json     # the same flags from a JSON file (keys = flag names
                       with underscores; "end_line" a list): Drone Studio writes it, so nothing needs shell quoting
"""
import argparse, os, sys
import common

SLOWMO_FPS = 48

ap = argparse.ArgumentParser()
ap.add_argument("--project", required=True)
ap.add_argument("--spec")
ap.add_argument("--max", type=float, help="length ceiling of the 16:9 film, seconds")
ap.add_argument("--vertical-max", type=float, help="length ceiling of the 9:16 short, seconds")
ap.add_argument("--max-uses", type=int, default=2)
ap.add_argument("--title", default="[TITLE]")
ap.add_argument("--sub", default="[LOCATION · DATE]")
ap.add_argument("--end", default="[YOUR NAME]")
ap.add_argument("--end-sub")
ap.add_argument("--end-line", action="append", default=[], help="more end-card lines (repeat; \"---\" draws a rule)")
ap.add_argument("--logo", help="an image under the end card text")
ap.add_argument("--labels", action="store_true", help="keep the shots' room labels")
ap.add_argument("--style")
ap.add_argument("--config", help="JSON file of flag values; flags given on the command line win")
ap.add_argument("--out")
args = ap.parse_args()
if args.config:
    given = {a.dest for a in ap._actions for o in a.option_strings if any(x == o or x.startswith(o + "=") for x in sys.argv[1:])}
    for k, v in common.load(args.config).items():
        if not hasattr(args, k):
            common.die("%s: unknown setting %r" % (args.config, k))
        if k not in given:
            setattr(args, k, list(v) if k == "end_line" else v)

P = args.project
ST = common.load_style(args.style)
CUT = ST["cut"]
TRANSITIONS, HEAD, TAIL = set(CUT["transitions"]), float(CUT["head"]), float(CUT["tail"])
S = common.load(args.spec or os.path.join(P, "cut-spec.json"))
B = common.load(os.path.join(P, "beats.json"))
info = {c["id"]: c for c in common.load(os.path.join(P, "clips.json"))["clips"]}
clips = {k: c["duration"] for k, c in info.items()}
bars = B["bars"]


def build(spec, label, ceiling, vertical):
    """-> (shots, film length, music start seconds, problems)"""
    problems = []
    shots_in = spec.get("shots") or []
    if not shots_in:
        return [], 0.0, 0.0, ["%s: no shots" % label]
    k0 = int(spec.get("music_start_bar", 0))
    total_bars = sum(int(s.get("bars", 1)) for s in shots_in)
    if not 0 <= k0 < len(bars) or k0 + total_bars >= len(bars):
        return [], 0.0, 0.0, ["%s: bars %d-%d run past the track (it has %d bars)" % (label, k0, k0 + total_bars, len(bars))]
    t0 = bars[k0]
    cuts, k = [], k0
    for s in shots_in:
        cuts.append(bars[k] - t0)
        k += int(s.get("bars", 1))
    end = bars[k] - t0
    if ceiling and end > ceiling + 0.05:
        problems.append("%s: %.1f s is longer than the %.0f s maximum: drop or shorten shots (%.2f s a bar)"
                        % (label, end, ceiling, (bars[-1] - bars[0]) / (len(bars) - 1)))
    tr = []
    for i, s in enumerate(shots_in):
        t = s.get("transition") or ["cut", 0]
        if isinstance(t, dict):
            t = [t.get("type", "cut"), t.get("dur", 0)]
        typ, dur = str(t[0]), float(t[1] if len(t) > 1 else 0.5)
        if typ not in TRANSITIONS:
            problems.append("%s shot %d: transition %r is not used in the %s style (use: %s)"
                            % (label, i + 1, typ, ST["name"], ", ".join(sorted(TRANSITIONS))))
        if i == 0 or typ == "cut":
            typ, dur = ("cut", 0.0)
        tr.append((typ, dur))
    td = [d for _, d in tr] + [0.0]
    shots, used, windows = [], {}, {}
    for i, s in enumerate(shots_in):
        cid = s.get("clip")
        if cid not in clips:
            problems.append("%s shot %d: unknown clip %r (clips: %s)" % (label, i + 1, cid, ", ".join(sorted(clips))))
            continue
        speed = float(s.get("speed", 1.0))
        if not 0.5 <= speed <= 1.0:
            problems.append("%s shot %d: speed %.2f outside 0.5-1.0" % (label, i + 1, speed))
        fps = float(info[cid].get("fps") or 60)
        if speed < 1.0 and fps < SLOWMO_FPS:
            problems.append("%s shot %d: %s is %.0f fps: slow motion would stutter (only on 50/60 fps clips)" % (label, i + 1, cid, fps))
        if not vertical and info[cid].get("orientation") == "portrait":
            problems.append("%s shot %d: %s is a portrait clip: only in the 9:16 short" % (label, i + 1, cid))
        nxt = cuts[i + 1] if i + 1 < len(cuts) else end
        if CUT.get("min_shot") and nxt - cuts[i] < float(CUT["min_shot"]) - 0.05:
            problems.append("%s shot %d: %.1f s is too short for the %s style (at least %.1f s: give it more bars)"
                            % (label, i + 1, nxt - cuts[i], ST["name"], float(CUT["min_shot"])))
        dur = (nxt - cuts[i]) + td[i] / 2 + td[i + 1] / 2
        src_in = float(s["in"]) - (td[i] / 2) * speed
        span = dur * speed
        if src_in < HEAD - 1e-6 or src_in + span > clips[cid] - TAIL + 1e-6:
            lo, hi = HEAD + (td[i] / 2) * speed, clips[cid] - TAIL - span + (td[i] / 2) * speed
            problems.append("%s shot %d: %s needs %.2f s of source from %.2f, the clip allows in = %.2f-%.2f"
                            % (label, i + 1, cid, span, float(s["in"]), lo, hi) if hi >= lo else
                            "%s shot %d: %s is too short for %d bar(s) at speed %.1f (%.2f s needed)" % (label, i + 1, cid, s.get("bars", 1), speed, span))
        if i and shots_in[i - 1].get("clip") == cid:
            problems.append("%s shot %d: %s twice in a row" % (label, i + 1, cid))
        for j, (a, b) in windows.get(cid, []):
            if src_in < b - 0.2 and src_in + span > a + 0.2:
                problems.append("%s shots %d and %d both show %s %.1f-%.1f s (the same moment twice)"
                                % (label, j + 1, i + 1, cid, max(a, src_in), min(b, src_in + span)))
        windows.setdefault(cid, []).append((i, (src_in, src_in + span)))
        used[cid] = used.get(cid, 0) + 1
        shot = {"clip": cid, "in": round(src_in, 3), "dur": round(dur, 3), "speed": speed,
                "transition": {"type": tr[i][0], "dur": tr[i][1]}, "at": round(cuts[i], 3), "note": s.get("note", "")}
        if s.get("label"):
            if len(str(s["label"])) > 28:
                problems.append("%s shot %d: label %r is longer than 28 characters" % (label, i + 1, s["label"]))
            elif args.labels:
                shot["label"] = str(s["label"]).strip()
        if vertical:
            if s.get("pan"):
                shot["pan"] = [float(v) for v in s["pan"]]
            else:
                shot["x"] = float(s.get("x", 0.5))
        shots.append(shot)
    slow = sum(1 for s in shots_in if float(s.get("speed", 1.0)) < 1.0)
    if CUT.get("max_slowmo_shots") is not None and slow > int(CUT["max_slowmo_shots"]):
        problems.append("%s: %d slow-motion shots, the %s style allows %d" % (label, slow, ST["name"], int(CUT["max_slowmo_shots"])))
    for cid, n in used.items():
        if n > args.max_uses:
            problems.append("%s: %s is used %d times (max %d): shorter beats repeating" % (label, cid, n, args.max_uses))
    return shots, round(end, 3), round(t0, 3), problems


shots, end, m0, problems = build(S, "film", args.max, False)
C = common.cutlist_skeleton(P, B, m0, args.title, args.sub, args.end, args.end_sub, style=ST, end_lines=args.end_line,
                           logo=args.logo, labels=args.labels)
C["music"]["fade_out"] = 3.0
C.update(duration=end, shots=shots)
if S.get("vertical"):
    vs, vend, vm0, vp = build(S["vertical"], "vertical", args.vertical_max, True)
    problems += vp
    C["vertical"] = {"width": 1080, "height": 1920, "duration": vend, "shots": vs, "music": {"start": vm0, "fade_in": 0.05},
                     "title": {"at": 0.8, "dur": 3.0}, "end": {"dur": 3.0}}
if problems:
    print("cut-spec.json has %d problem(s):" % len(problems))
    print("\n".join("  - " + p for p in problems))
    sys.exit(1)
out = args.out or os.path.join(P, "cutlist.json")
common.save(out, C)
print("film: %d shots, %.2f s, music from %.2f s%s -> %s" % (len(shots), end, m0, "; vertical: %d shots, %.2f s, music from %.2f s"
      % (len(C["vertical"]["shots"]), C["vertical"]["duration"], C["vertical"]["music"]["start"]) if "vertical" in C else "", out))
for s in shots:
    print("  %6.2f  %s  in %5.2f  %4.2f s  x%.1f  %-6s %s%s" % (s["at"], s["clip"], s["in"], s["dur"], s["speed"], s["transition"]["type"],
                                                         "[%s] " % s["label"] if s.get("label") else "", s["note"]))
