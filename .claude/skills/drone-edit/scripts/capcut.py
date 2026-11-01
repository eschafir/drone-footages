#!/usr/bin/env python3
"""capcut.py: cutlist.json -> a CapCut desktop project with the edit already on the timeline, to finish by hand.
  video     one segment per shot on the main track, pointing at the ORIGINAL source file (ungraded, 10-bit log as shot)
            at the shot's in point: no re-encode, and every shot has the rest of its clip as handles to trim into.
            Dissolves become cuts in the middle of the dissolve (the film keeps its length and its beat); the fade in
            from and out to black is left off. Slow motion is a segment speed. 9:16 shots fill the frame at the shot's
            crop position (a pan is held at its middle).
  overlay   the title, end and room-label cards as stills, at the times render.py uses (no fades).
  audio     the music from its start point with its fades, and the SFX on a second track. Not loudness-normalised.
  media     <project>/out/capcut[-vertical]/: the LUT, the card PNGs, the music and SFX the draft points at, and
            CUT-SHEET.md (every shot's source, in and out, place on the timeline and what was simplified). If CapCut
            will not open the draft, rebuild the edit from the sheet with these files.
The draft is written into CapCut's drafts folder; CapCut lists it the next time it starts. The LUT is not applied:
in CapCut select the D-Log M shots -> Adjustment -> LUT -> import the .cube from the media folder.
The draft format is CapCut's own and undocumented: the skeletons in assets/capcut-template/capcut-9.6.json were
captured from a CapCut 9.6 project, and only ids, times, paths and sizes are filled in. A newer CapCut gets a warning.
CapCut keeps the drafts it has loaded in memory and writes them back, so this refuses to write while CapCut runs
(exit 3, the media folder is still written). Writing again with the same name replaces a draft this script made.
Usage:
  python3 capcut.py --cutlist videos/<p>/cutlist.json [--vertical] [--name "Lago Puelo"] [--drafts <dir>] [--media-only]
CAPCUT_DRAFTS overrides the default drafts folder (the tests point it at a scratch folder).
"""
import argparse, copy, hashlib, json, os, plistlib, re, shutil, subprocess, sys, time, uuid
import cards as cardlib
import common

SCHEMA = (9, 6)
TEMPLATE = os.path.join(common.SKILL, "assets", "capcut-template", "capcut-9.6.json")
DRAFTS = os.environ.get("CAPCUT_DRAFTS") or os.path.expanduser("~/Movies/CapCut/User Data/Projects/com.lveditor.draft")
APP = "/Applications/CapCut.app"
MARK = ".drone-edit.json"                     # in drafts this script wrote, so a rerun may replace them
US = 1000000
EXIT_RUNNING = 3
VIDEO_REFS = [("speeds", "speed"), ("placeholder_infos", "placeholder_info"), ("canvases", "canvas"),
              ("material_animations", "material_animation"), ("sound_channel_mappings", "sound_channel_mapping"),
              ("material_colors", "material_color"), ("vocal_separations", "vocal_separation")]
AUDIO_REFS = [("speeds", "speed"), ("placeholder_infos", "placeholder_info"), ("sound_channel_mappings", "sound_channel_mapping"),
              ("vocal_separations", "vocal_separation")]


def uid():
    return str(uuid.uuid4()).upper()


def us(frames):
    return int(round(frames * US / common.FPS))


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def capcut_running():
    return subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True).returncode == 0


def capcut_version(app=APP):
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            v = plistlib.load(f).get("CFBundleShortVersionString", "")
        return tuple(int(x) for x in v.split(".")[:2])
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def safe_name(name):
    return re.sub(r"[/:\\]+", "-", name).strip(" .") or "film"


def timeline(shots, clips):
    """Each shot as a cut-to-cut segment in frames: dissolves cut in their middle, so the shots tile the film."""
    frames, tframes, starts, total_f = common.frame_timeline(shots)
    cuts = [0] + [starts[i] + tframes[i] // 2 for i in range(1, len(shots))] + [total_f]
    out = []
    for i, s in enumerate(shots):
        clip = clips.get(s["clip"]) or common.die("unknown clip %r in shot %d" % (s["clip"], i + 1))
        speed = float(s.get("speed", 1.0))
        a, b = cuts[i], cuts[i + 1]
        src_in = float(s["in"]) + (a - starts[i]) / common.FPS * speed
        src_dur = (b - a) / common.FPS * speed
        if src_in < 0 or src_in + src_dur > clip["duration"] + 0.02:
            common.die("shot %d: %s %.2f + %.2f s runs past the clip (%.2f s)" % (i + 1, s["clip"], src_in, src_dur, clip["duration"]))
        out.append({"shot": i + 1, "s": s, "clip": clip, "speed": speed, "start": a, "end": b,
                    "src_in": src_in, "src_dur": min(src_dur, clip["duration"] - src_in)})
    return out, starts, tframes, total_f


def fill(W, H, w, h, x=0.5):
    """CapCut fits a source inside the canvas at scale 1: the scale that fills it instead (render.py's centre crop),
    and the horizontal offset, in half canvas widths, that puts the crop window at x (0 left .. 1 right)."""
    fit = min(W / w, H / h)
    scale = max(W / w, H / h) / fit
    over = w * fit * scale - W                            # displayed width beyond the canvas, px
    return scale, (0.5 - x) * over / (W / 2)


class Draft:
    def __init__(self, proto, W, H):
        self.p = proto
        self.info = copy.deepcopy(proto["draft_info"])
        self.info.update(id=uid(), fps=float(common.FPS), canvas_config=dict(self.info["canvas_config"], width=W, height=H, ratio="original"))
        self.mat = self.info["materials"]
        self.media = []                                  # the project's media panel (draft_meta_info)
        self.by_path = {}

    def new(self, kind, coll, **kw):
        x = copy.deepcopy(self.p[kind])
        x.update(id=uid(), **kw)
        self.mat[coll].append(x)
        return x

    def track(self, kind):
        t = copy.deepcopy(self.p["track"])
        t.update(id=uid(), type=kind, segments=[])
        self.info["tracks"].append(t)
        return t

    def list_media(self, path, metetype, dur_us, w=0, h=0):
        item = copy.deepcopy(self.p["media_item"])
        now = int(time.time())
        item.update(id=str(uuid.uuid4()), file_Path=path, extra_info=os.path.basename(path), metetype=metetype, duration=dur_us,
                    width=w, height=h, create_time=now, import_time=now, import_time_ms=int(time.time() * US),
                    roughcut_time_range={"start": 0, "duration": dur_us})
        self.media.append(item)
        return item["id"]

    def visual(self, path, dur_s, w, h, photo=False):
        """A video or still material, one per file however many segments use it."""
        if path in self.by_path:
            return self.by_path[path]
        dur_us = 10800 * US if photo else int(round(dur_s * US))
        local = self.list_media(path, "photo" if photo else "video", dur_us, w, h)
        m = self.new("video_material", "videos", type="photo" if photo else "video", path=path, material_name=os.path.basename(path),
                     duration=dur_us, width=w, height=h, local_material_id=local,
                     unique_id=hashlib.md5(path.encode()).hexdigest(), has_audio=False)
        self.by_path[path] = m
        return m

    def segment(self, track, proto, material, src, target, speed=1.0, refs=VIDEO_REFS, extra=(), render_index=0, **kw):
        seg = copy.deepcopy(self.p[proto])
        ids = []
        for coll, kind in refs:
            x = self.new(kind, coll)
            if kind == "speed":
                x["speed"] = speed
            ids.append(x["id"])
        seg.update(id=uid(), material_id=material["id"], speed=speed, extra_material_refs=ids + list(extra),
                   source_timerange={"start": src[0], "duration": src[1]}, target_timerange={"start": target[0], "duration": target[1]},
                   render_index=render_index, track_render_index=render_index, **kw)
        track["segments"].append(seg)
        return seg

    def audio(self, track, path, src_start_s, start_us, dur_us, db=0.0, fade_in=0.0, fade_out=0.0):
        p = duration(path)
        mat = self.by_path.get(path)
        if not mat:
            local = self.list_media(path, "music", int(round(p * US)))
            mat = self.new("audio_material", "audios", path=path, name=os.path.basename(path), duration=int(round(p * US)),
                           local_material_id=local, music_id=str(uuid.uuid4()))
            self.by_path[path] = mat
        extra = []
        if fade_in or fade_out:
            extra.append(self.new("audio_fade", "audio_fades", fade_in_duration=int(fade_in * US), fade_out_duration=int(fade_out * US))["id"])
        dur_us = min(dur_us, int((p - src_start_s) * US))
        v = 10 ** (db / 20.0)
        return self.segment(track, "audio_segment", mat, (int(round(src_start_s * US)), dur_us), (start_us, dur_us),
                            refs=AUDIO_REFS, extra=extra, volume=v, last_nonzero_volume=v)

    def write(self, folder, name, duration_us, project):
        os.makedirs(folder)
        self.info.update(duration=duration_us, name="")
        tid = self.info["id"]
        now_us = int(time.time() * US)
        tdir = os.path.join(folder, "Timelines", tid)
        os.makedirs(tdir)
        for d in (folder, tdir):
            common.save(os.path.join(d, "draft_info.json"), self.info)
        proj = copy.deepcopy(self.p["project"])
        proj.update(id=uid(), main_timeline_id=tid, create_time=now_us, update_time=now_us,
                    timelines=[{"create_time": now_us, "id": tid, "is_marked_delete": False, "name": "Timeline 01", "update_time": now_us}])
        common.save(os.path.join(folder, "Timelines", "project.json"), proj)
        meta = copy.deepcopy(self.p["draft_meta_info"])
        draft_id = uid()
        meta.update(draft_fold_path=folder, draft_id=draft_id, draft_name=name, draft_root_path=os.path.dirname(folder),
                    tm_draft_create=now_us, tm_draft_modified=now_us, tm_duration=duration_us)
        next(m for m in meta["draft_materials"] if m["type"] == 0)["value"] = self.media
        common.save(os.path.join(folder, "draft_meta_info.json"), meta)
        with open(os.path.join(folder, "draft_settings"), "w") as f:
            f.write("[General]\ndraft_create_time=%d\ndraft_last_edit_time=%d\nreal_edit_keys=0\nreal_edit_seconds=0\n"
                    % (now_us // US, now_us // US))
        common.save(os.path.join(folder, "timeline_layout.json"),
                    {"dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [tid], "timelineNames": ["Timeline 01"]}], "layoutOrientation": 1})
        common.save(os.path.join(folder, MARK), {"project": project, "written": time.strftime("%Y-%m-%d %H:%M:%S")})
        entry = copy.deepcopy(self.p["root_entry"])
        entry.update(draft_fold_path=folder, draft_id=draft_id, draft_json_file=os.path.join(folder, "draft_info.json"), draft_name=name,
                     draft_root_path=os.path.dirname(folder), tm_draft_create=now_us, tm_draft_modified=now_us, tm_duration=duration_us)
        return entry


def register(drafts, entry):
    """List the draft in CapCut's root_meta_info.json (replacing an older entry for the same folder)."""
    path = os.path.join(drafts, "root_meta_info.json")
    root = common.load(path) if os.path.exists(path) else {"all_draft_store": [], "draft_ids": 0, "root_path": drafts}
    root["all_draft_store"] = [e for e in root.get("all_draft_store", []) if e.get("draft_fold_path") != entry["draft_fold_path"]] + [entry]
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(root, f, ensure_ascii=False)
    os.replace(tmp, path)


def draft_folder(drafts, name, project):
    """<drafts>/<name>, replacing a draft this script wrote for the same project; never one made in CapCut."""
    base, k = os.path.join(drafts, name), 2
    folder = base
    while os.path.exists(folder):
        mark = os.path.join(folder, MARK)
        if os.path.exists(mark) and common.load(mark).get("project") == project:
            shutil.rmtree(folder)
            break
        folder = "%s %d" % (base, k); k += 1
    return folder


def fmt(t):
    return "%d:%06.3f" % (t // 60, t % 60)


def cut_sheet(path, name, segs, cards, music, sfx, lut, media, draft, simplified, fps):
    L = ["# %s: CapCut cut sheet" % name, ""]
    L.append("Draft: `%s`" % draft if draft else "Draft: not written (see above). Rebuild the edit from this sheet.")
    L += ["", "Media folder: `%s`" % media, "",
          "## Finish in CapCut", "",
          "1. Open CapCut: the project is in the project list (quit and reopen CapCut if it was running).",
          "2. The footage is ungraded. Select the D-Log M shots, then Adjustment -> LUT -> Import -> `%s`." % os.path.basename(lut) if lut else
          "2. The footage is ungraded: grade it in Adjustment (no LUT was found for the D-Log M shots).",
          "3. Every shot has the rest of its source clip as handles: drag a shot's edge to extend it.",
          "4. Music is placed with its fades but not loudness-normalised: aim for about -14 LUFS on export.", ""]
    if simplified:
        L += ["## Left for you (CapCut needs its own effects for these)", ""] + ["- " + x for x in simplified] + [""]
    L += ["## Shots", "", "| # | clip | source file | source in-out | timeline | into it | colour | note |", "|---|---|---|---|---|---|---|---|"]
    for g in segs:
        s, c = g["s"], g["clip"]
        prof = c.get("profile", "dlogm")
        colour = {"dlogm": "D-Log M: LUT", "rec709": "Rec.709: no LUT", "graded": "graded: leave", "hlg": "HDR (HLG): tone map",
                  "pq": "HDR (PQ): tone map"}.get(prof, prof)
        tr = "start" if g["shot"] == 1 else (s.get("transition") or {}).get("type", "cut")
        L.append("| %d | %s | %s | %s-%s%s | %s-%s | %s | %s | %s |" % (
            g["shot"], s["clip"], os.path.basename(c["src"]), fmt(g["src_in"]), fmt(g["src_in"] + g["src_dur"]),
            " x%.2f" % g["speed"] if g["speed"] != 1 else "", fmt(g["start"] / fps), fmt(g["end"] / fps), tr, colour,
            (s.get("note") or "").replace("|", "/")))
    if cards:
        L += ["", "## Cards (overlay track)", "", "| card | file | timeline |", "|---|---|---|"]
        L += ["| %s: %s | %s | %s-%s |" % (k, text, os.path.basename(png), fmt(at), fmt(at + d)) for png, at, d, k, text in cards]
    if music:
        L += ["", "## Audio", "", "- Music `%s` from %s in the track, fade in %.1f s, fade out %.1f s." % (
            os.path.basename(music["file"]), fmt(music["start"]), music["fade_in"], music["fade_out"])]
        L += ["- SFX `%s` at %s, %s dB." % (os.path.basename(x["file"]), fmt(x["at"]), x["db"]) for x in sfx]
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutlist", required=True)
    ap.add_argument("--vertical", action="store_true")
    ap.add_argument("--name", help="the project's name in CapCut (default: the title, else the project)")
    ap.add_argument("--drafts", default=DRAFTS, help="CapCut's drafts folder")
    ap.add_argument("--media-only", action="store_true", help="write the media folder and cut sheet, not the draft")
    args = ap.parse_args()

    P = os.path.dirname(os.path.abspath(args.cutlist))
    C = common.load(args.cutlist)
    clips = {c["id"]: c for c in common.load(os.path.join(P, "clips.json"))["clips"]}
    seq = C["vertical"] if args.vertical else C
    if args.vertical and not seq:
        common.die("the cutlist has no vertical section")
    shots = seq["shots"]
    if not shots:
        common.die("no shots in the cutlist")
    W, H = int(seq.get("width", 1080 if args.vertical else 3840)), int(seq.get("height", 1920 if args.vertical else 2160))
    FPS = common.FPS
    project = C.get("project", os.path.basename(P))
    title = (C.get("title") or {}).get("text") or project
    name = safe_name((args.name or title) + (" (vertical)" if args.vertical else ""))
    work = os.path.join(P, "work")
    media = os.path.join(P, "out", "capcut-vertical" if args.vertical else "capcut")
    if os.path.exists(media):
        shutil.rmtree(media)
    os.makedirs(media)

    segs, starts, tframes, total_f = timeline(shots, clips)
    total = total_f / FPS
    lut = C.get("lut") and common.resolve(C["lut"], P)
    lut = lut if lut and os.path.exists(lut) else common.find_lut()
    if lut:
        shutil.copy2(lut, media)

    placed = []
    for k, (png, at, d, fd, kind, text) in enumerate(cardlib.placements(C, seq, args.vertical, starts, tframes, total, work, W, H)):
        dst = os.path.join(media, "%02d-%s-%s.png" % (k + 1, kind, safe_name(text)[:40]))
        shutil.copy2(png, dst)
        placed.append((dst, at, d, kind, text))

    music = C.get("music")
    if args.vertical and music and seq.get("music"):
        music = dict(music, **seq["music"])
    mus = None
    if music and music.get("file"):
        mf = common.resolve(music["file"], P)
        if not os.path.exists(mf):
            common.die("music not found: %s" % mf)
        dst = os.path.join(media, os.path.basename(mf))
        shutil.copy2(mf, dst)
        mus = {"file": dst, "start": float(music.get("start", 0.0)), "db": float(music.get("db", 0)),
               "fade_in": float(music.get("fade_in", 0.3)), "fade_out": float(music.get("fade_out", 2.5))}
    sfx = []
    for x in C.get("sfx") or []:
        sf = common.resolve(x["file"], P)
        if not os.path.exists(sf):
            common.die("sfx not found: %s" % sf)
        dst = os.path.join(media, os.path.basename(sf))
        if not os.path.exists(dst):
            shutil.copy2(sf, dst)
        sfx.append({"file": dst, "at": float(x["at"]), "db": float(x.get("db", -6))})

    simplified = []
    for g in segs[1:]:
        t = (g["s"].get("transition") or {}).get("type", "cut")
        if t != "cut":
            simplified.append("shot %d: a %s (%.1f s) into it, cut in its middle at %s (Transitions panel)"
                              % (g["shot"], t, float(g["s"]["transition"].get("dur", 0.5)), fmt(g["start"] / FPS)))
    if float(C.get("fade_in", 1.0)):
        simplified.append("fade in from black over the first %.1f s (Animation -> In -> Fade in, on shot 1)" % float(C.get("fade_in", 1.0)))
    if float(C.get("fade_out", 1.5)):
        simplified.append("fade out to black over the last %.1f s (Animation -> Out -> Fade out, on the last shot)" % float(C.get("fade_out", 1.5)))
    if placed:
        simplified.append("the cards fade in and out in the film; here they are plain stills (Animation -> Fade in / out)")
    if any(g["s"].get("pan") for g in segs) and args.vertical:
        simplified.append("panned 9:16 shots hold the middle of the pan: keyframe Position to move it")

    draft = None
    status = 0
    if not args.media_only:
        ver = capcut_version()
        if ver and ver > SCHEMA:
            print("warning: CapCut %d.%d is newer than the %d.%d draft template: if the project does not open, use the media folder"
                  % (ver + SCHEMA), flush=True)
        if capcut_running() and os.path.realpath(args.drafts) == os.path.realpath(DRAFTS):
            print("CapCut is running: it rewrites the drafts it has open, so the draft was not written. Quit CapCut and run "
                  "this again (the media folder and cut sheet are ready: %s)" % media, flush=True)
            status = EXIT_RUNNING
        else:
            proto = common.load(TEMPLATE)
            d = Draft(proto, W, H)
            main_t = d.track("video")
            for g in segs:
                c = g["clip"]
                x = g["s"].get("x", 0.5)
                if g["s"].get("pan"):
                    x = sum(g["s"]["pan"]) / 2
                scale, tx = fill(W, H, c["width"], c["height"], x if args.vertical else 0.5)
                m = d.visual(c["src"], c["duration"], c["width"], c["height"])
                clip = copy.deepcopy(proto["video_segment"]["clip"])
                clip["scale"] = {"x": scale, "y": scale}
                clip["transform"] = {"x": tx, "y": 0.0}
                d.segment(main_t, "video_segment", m, (int(round(g["src_in"] * US)), int(round(g["src_dur"] * US))),
                          (us(g["start"]), us(g["end"]) - us(g["start"])), g["speed"], clip=clip, volume=0.0, last_nonzero_volume=1.0)
            if placed:
                over = d.track("video")
                for png, at, dur, kind, text in placed:
                    m = d.visual(png, 0, W, H, photo=True)
                    d.segment(over, "video_segment", m, (0, int(dur * US)), (int(at * US), int(dur * US)), render_index=1)
            if mus:
                d.audio(d.track("audio"), mus["file"], mus["start"], 0, us(total_f), mus["db"], mus["fade_in"], mus["fade_out"])
            if sfx:
                t = d.track("audio")
                for x in sorted(sfx, key=lambda x: x["at"]):
                    d.audio(t, x["file"], 0.0, int(x["at"] * US), 3600 * US, x["db"])
            os.makedirs(args.drafts, exist_ok=True)
            folder = draft_folder(args.drafts, name, os.path.abspath(args.cutlist) + ("#vertical" if args.vertical else ""))
            entry = d.write(folder, os.path.basename(folder), us(total_f), os.path.abspath(args.cutlist) + ("#vertical" if args.vertical else ""))
            register(args.drafts, entry)
            draft = folder

    cut_sheet(os.path.join(media, "CUT-SHEET.md"), name, segs, placed, mus, sfx, lut, media, draft, simplified, FPS)
    if draft:
        print("CapCut project '%s': %d shots, %.1f s -> %s" % (os.path.basename(draft), len(segs), total, draft), flush=True)
    print("media -> %s" % media, flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
