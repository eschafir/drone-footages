"""common.py: shared bits for the drone-edit scripts (probe, ffmpeg, JSON, the grade, the encode settings).
The grade lives here once so the proxies Claude looks at and the final render use the exact same colour."""
import glob, json, os, subprocess, sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUT_DIR = os.path.join(SKILL, "assets", "luts")
GEN_LUT_DIR = os.path.join(LUT_DIR, "generated")          # HDR -> Rec.709 cubes from hdr_lut.py (never picked by find_lut)
STYLE_DIR = os.path.join(SKILL, "styles")
FONT_DIR = os.path.join(SKILL, "assets", "fonts")
FPS = 30
# ffmpeg 9: frame colour properties override the output flags, so tag the frames themselves as well
TAG_VF = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
TAG_ARGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
DEFAULT_LOOK = {"contrast": 0.35, "saturation": 0.05, "vibrance": 0.15, "warmth": 0.0, "vignette": 0.25}


def die(msg):
    sys.exit("error: " + msg)


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    return path


def resolve(path, base):
    """A path from a JSON file: absolute, ~, or relative to the file's folder."""
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base, path))


def ffmpeg(*args, quiet=True):
    cmd = ["ffmpeg", "-y", "-hide_banner"] + (["-v", "error"] if quiet else []) + [str(a) for a in args]
    subprocess.run(cmd, check=True)


def probe(path):
    """Duration, size, fps, pixel format and encoder of the FIRST video stream (DJI files carry a second, MJPEG
    thumbnail stream and two data streams: always map 0:v:0)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt,color_transfer,nb_frames,duration,bit_rate:stream_side_data=rotation",
                        "-show_entries", "format=duration,bit_rate:format_tags=encoder,creation_time", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        die("no video stream in %s" % path)
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    tags = j["format"].get("tags", {})
    rot = int(round(float(next((d["rotation"] for d in v.get("side_data_list", []) if "rotation" in d), 0)))) % 360
    w, h = (v["height"], v["width"]) if rot in (90, 270) else (v["width"], v["height"])   # ffmpeg autorotates on decode
    return {"duration": float(j["format"].get("duration") or 0), "width": w, "height": h, "rotation": rot,
            "fps": float(num) / float(den or 1), "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
            "transfer": v.get("color_transfer"), "bit_rate": int(j["format"].get("bit_rate") or 0),
            "audio": a is not None, "encoder": tags.get("encoder"), "created": tags.get("creation_time")}


def find_lut(explicit=None):
    """The LUT to use: an explicit path, else the first .cube in assets/luts/, else None (the fallback grade)."""
    if explicit:
        if not os.path.exists(explicit):
            die("LUT not found: %s" % explicit)
        return os.path.abspath(explicit)
    cubes = sorted(glob.glob(os.path.join(LUT_DIR, "*.cube")))
    return cubes[0] if cubes else None


def load_style(name=None):
    """styles/<name>.json merged over styles/drone.json (one level deep): the numbers the scripts read for a style."""
    with open(os.path.join(STYLE_DIR, "drone.json")) as f:
        base = json.load(f)
    if name and name != "drone":
        path = os.path.join(STYLE_DIR, "%s.json" % name)
        if not os.path.exists(path):
            die("unknown style %r (styles: %s)" % (name, ", ".join(sorted(os.path.splitext(n)[0] for n in os.listdir(STYLE_DIR) if n.endswith(".json")))))
        with open(path) as f:
            for k, v in json.load(f).items():
                base[k] = dict(base.get(k) or {}, **v) if isinstance(v, dict) else v
    base["name"] = name or "drone"
    return base


PROFILES = ("dlogm", "rec709", "hlg", "pq", "graded")   # graded: never detected, only chosen (--profile)


def detect_profile(path, p=None):
    """The colour profile of a clip: hlg / pq from the transfer tag (phones shooting HDR), dlogm for a DJI clip with the
    log footprint (low saturation, blacks lifted to ~75 of 255: measured on Mini 4 Pro D-Log M), else rec709.
    -> (profile, {"sat": mean chroma distance, "black": 2nd percentile luma}) on five frames."""
    import numpy as np
    p = p or probe(path)
    if p.get("transfer") == "arib-std-b67":
        return "hlg", {}
    if p.get("transfer") == "smpte2084":
        return "pq", {}
    sats, blacks = [], []
    for k in range(5):
        t = p["duration"] * (k + 1) / 6
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.2f" % t, "-i", path, "-map", "0:v:0", "-frames:v", "1",
                              "-vf", "scale=160:90,format=yuv444p", "-f", "rawvideo", "-"], capture_output=True).stdout
        if len(raw) < 160 * 90 * 3:
            continue
        y, u, v = (np.frombuffer(raw, np.uint8)[i * 14400:(i + 1) * 14400].astype(np.float32) for i in range(3))
        sats.append(float(np.hypot(u - 128, v - 128).mean()))
        blacks.append(float(np.percentile(y, 2)))
    if not sats:
        return "rec709", {}
    m = {"sat": round(float(np.median(sats)), 1), "black": round(float(np.median(blacks)), 1)}
    dji = "dji" in (p.get("encoder") or "").lower() or os.path.basename(path).upper().startswith("DJI_")
    # D-Log M only exists on DJI cameras and is always 10-bit. Measured: log clips sat 6-13 / black 61-88, the same
    # beach clips graded sat 16 / black 50; a pale white room has low blacks
    tenbit = "10" in (p.get("pix_fmt") or "")
    return ("dlogm" if dji and tenbit and m["sat"] < 14.5 and m["black"] > 56 else "rec709"), m


def fit_vf(W, H, w, h):
    """Scale a w x h source to fill W x H: same aspect -> a plain scale; otherwise fill and centre-crop (never stretch)."""
    if abs(w / h - W / H) < 0.01:
        return "scale=%d:%d:flags=lanczos" % (W, H)
    return "scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos,crop=%d:%d,setsar=1" % (W, H, W, H)


def hdr_lut(profile):
    """The generated HLG / PQ -> Rec.709 cube (written on first use)."""
    path = os.path.join(GEN_LUT_DIR, "%s-to-rec709.cube" % profile)
    if not os.path.exists(path):
        import hdr_lut as gen
        gen.write_cube(profile, path)
    return path


def _esc(path):
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def grade_vf(lut, look=None, profile="dlogm"):
    """To Rec.709, then the look. Per clip profile:
      dlogm   the DJI LUT (or an approximate curve when no LUT is installed)
      rec709  already display-ready: no LUT, and the look at about half strength so it does not look overcooked
      hlg/pq  a generated BT.2020 HDR -> Rec.709 tone-mapping cube (this ffmpeg has no zscale)
      graded  the user already graded it: no LUT, no look, the colour passes through untouched
    Everything up to the vignette runs in 16-bit RGB so 10-bit skies do not band."""
    if profile == "graded":
        return "format=yuv420p10le"
    lk = dict(DEFAULT_LOOK, **(look or {}))
    f = ["format=gbrp16le"]
    if profile == "rec709":
        lk = dict(lk, contrast=float(lk["contrast"]) * 0.5, vibrance=float(lk["vibrance"]) * 0.6, saturation=0.0)
    elif profile in ("hlg", "pq"):
        f.append("lut3d=file='%s':interp=tetrahedral" % _esc(hdr_lut(profile)))
    elif lut:
        f.append("lut3d=file='%s':interp=tetrahedral" % _esc(lut))
    else:  # approximate D-Log M -> Rec.709: pull the lifted blacks down, roll the highlights, restore saturation
        f += ["curves=all='0/0 0.10/0.02 0.30/0.22 0.55/0.58 0.80/0.88 1/1'", "huesaturation=saturation=0.45"]
    c = float(lk["contrast"])
    if c:
        d = 0.04 * c
        f.append("curves=all='0/0 0.25/%.4f 0.5/0.5 0.75/%.4f 1/1'" % (0.25 - d, 0.75 + d))
    if lk["saturation"]:
        f.append("huesaturation=saturation=%.3f" % float(lk["saturation"]))
    if lk["vibrance"]:
        f.append("vibrance=intensity=%.3f" % float(lk["vibrance"]))
    w = float(lk["warmth"])
    if w:
        f.append("colorbalance=rm=%.3f:bm=%.3f:rh=%.3f:bh=%.3f" % (0.04 * w, -0.04 * w, 0.02 * w, -0.02 * w))
    f.append("format=yuv420p10le")
    v = float(lk["vignette"])
    if v:
        f.append("vignette=angle=%.3f" % (0.15 + 0.35 * v))
    return ",".join(f)


def cutlist_skeleton(project, beats=None, music_start=0.0, title="[TITLE]", sub="[LOCATION · DATE]",
                     end="[YOUR NAME]", end_sub=None, style=None, end_lines=(), logo=None, labels=False):
    """Everything in a cutlist except the shots: grade, fades, music, cards (plan-cut.py and build-cut.py share it).
    style: a load_style() dict (its look and card timings); end_sub None = the style's default end line."""
    st = style or load_style()
    end_card = {"text": end, "sub": st["cards"]["end_sub"] if end_sub is None else end_sub, "dur": st["cards"]["end_dur"]}
    if end_lines:
        end_card["lines"] = list(end_lines)
    if logo:
        if not os.path.exists(logo):
            die("logo not found: %s" % logo)
        end_card["logo"] = os.path.abspath(logo)
    return {
        "project": os.path.basename(os.path.abspath(project)),
        "style": st["name"],
        "fps": FPS, "width": 3840, "height": 2160,
        "lut": load(os.path.join(project, "clips.json")).get("lut"),
        "look": dict(st["look"]),
        "fade_in": 1.0, "fade_out": 1.5,
        "music": {"file": os.path.relpath(beats["file"], project), "start": music_start, "fade_in": 0.3, "fade_out": 2.5,
                  "lufs": -14} if beats else None,
        "sfx": [],
        "labels": bool(labels),
        "title": {"text": title, "sub": sub, "at": 1.2, "dur": st["cards"]["title_dur"]},
        "end": end_card,
    }


def encode_args(preview=False, gpu=False, mezzanine=False, pixels=3840 * 2160):
    """Video encode settings. Mezzanine = the per-shot cache files (10-bit, near lossless); the deliverable is
    8-bit H.264 High, CRF 16 (or VideoToolbox with --gpu, ~4x faster: 45 Mb/s at 4K, scaled by pixel count, 16 Mb/s
    at least, so a 1080p file is not 4K-sized)."""
    if mezzanine:
        return ["-c:v", "libx264", "-preset", "ultrafast" if preview else "veryfast", "-crf", "20" if preview else "10",
                "-pix_fmt", "yuv420p10le", "-g", str(FPS)]
    if gpu:
        mbps = 12 if preview else max(16, round(45 * pixels / (3840 * 2160)))
        return ["-c:v", "h264_videotoolbox", "-b:v", "%dM" % mbps, "-profile:v", "high", "-allow_sw", "1",
                "-pix_fmt", "yuv420p", "-g", str(FPS)]
    return ["-c:v", "libx264", "-preset", "veryfast" if preview else "slow", "-crf", "21" if preview else "16",
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", str(FPS), "-movflags", "+faststart"]
