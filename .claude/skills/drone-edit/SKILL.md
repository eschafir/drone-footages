---
name: drone-edit
description: Edit raw drone footage (DJI clips, D-Log M or normal, mixed with phone or gimbal clips) into a finished film with music. Picks the best stretches of every clip, grades each by its colour profile (DJI LUT, Rec.709, HDR), cuts on the music's beats, adds transitions, slow motion, title and end cards and the music bed, then renders a verified 4K 16:9 master and a 9:16 vertical short with ffmpeg. Two styles: a drone travel film, and a real-estate property tour (room labels, listing end card). Use when the user points at a folder of drone clips or SD-card files and says "edit my drone footage", "make a drone video", "make a listing / property video", "cut these clips to music", "make a reel/short from the flight", "grade the D-Log footage", or gives notes on such an edit.
---

# drone-edit: raw drone clips to a finished film

Raw clips in, a graded, music-cut 4K film out (plus a 9:16 short). Everything runs locally with ffmpeg, numpy and
Chrome (for the cards). The scripts measure; Claude looks and decides: the analysis only filters out shaky, soft or
badly exposed footage, and the actual choice of shots, order and pace comes from looking at the contact sheets.

Every film has a **style** (`styles/<name>.md` = how to cut it, `styles/<name>.json` = the numbers the scripts
enforce): `drone` (the default, a travel / landscape film) or `realestate` (a calm property tour, exterior only or
interior & exterior, with room labels and a listing end card). Read the style's .md before cutting and pass
`--style <name>` to ingest, analyze, plan-cut and build-cut.

Commands run from the project folder with `SK=".claude/skills/drone-edit/scripts"` and `W="videos/<project>"` (one
working folder per film, e.g. `videos/2026-10-15`).

## Setup check (first use)

| Needs | Check | Install when missing |
|---|---|---|
| FFmpeg + ffprobe with lut3d, xfade, vignette, huesaturation | `ffmpeg -filters \| grep -wE "lut3d\|xfade"` | `brew install ffmpeg` |
| Python 3 + numpy | `python3 -c "import numpy"` | `python3 -m pip install --user numpy` |
| Google Chrome | cards.py finds it | google.com/chrome |
| The DJI LUT (D-Log M footage) | a `.cube` in `assets/luts/` | see Colour |

Then `cd "$SK" && python3 -m unittest tests` must end with `OK` (synthetic media, ~30 s). Run it after touching a script.
DM Sans (`assets/fonts/DMSans-Variable.ttf`, SIL OFL, `LICENSES/`) is the card font. No music, LUTs or footage ship.

## Pipeline

```bash
python3 $SK/ingest.py  --raw raw --project $W            # clips.json + graded 960x540 proxies (~20 s per minute of 4K)
#   (add --style realestate to every step of a property video; --profile dlogm|rec709 forces the colour profile)
python3 $SK/analyze.py --project $W                      # analysis.json, ANALYSIS.md, sheets/<id>.png, sheets/overview.png
#   -> LOOK at sheets/overview.png and every sheets/<id>.png (Read tool). Note the hero moments per clip.
#   -> music: suggest tracks (see Music), the user downloads one to $W/music/
python3 $SK/beats.py   --music $W/music/<track>.mp3      # beats.json: bpm, beats, bars, energy, sections (drops)
python3 $SK/plan-cut.py --project $W --max 45 --vertical-max 30 --title "..." --sub "..." --end "..."   # automatic draft
#   -> or, better: write $W/cut-spec.json from what the sheets show (shots in BARS, see "The cut spec") and
python3 $SK/build-cut.py --project $W --max 45 --vertical-max 30 --title "..." --sub "..." --end "..."
#   -> it places every cut on a bar and refuses repeats / out-of-range shots / too long: fix the spec, rerun
python3 $SK/render.py --cutlist $W/cutlist.json --preview              # 1080p, ~1 min: review round
python3 $SK/verify.py --render $W/out/<p>-preview.mp4                  # then LOOK at the .sheet.png
python3 $SK/render.py --cutlist $W/cutlist.json                        # 4K master (--gpu: VideoToolbox, ~4x faster;
                                                                       #   --size 1920x1080: a final-quality 1080p)
python3 $SK/render.py --cutlist $W/cutlist.json --vertical             # 1080x1920 short
python3 $SK/verify.py --render $W/out/<p>-4k.mp4 && python3 $SK/verify.py --render $W/out/<p>-vertical.mp4
```

Measured on an M2 Pro (16 GB), 60 s film from 8 clips: 1080p preview ~75 s; 4K master with `--gpu` ~5 min,
4.5 GB peak RAM, ~340 MB file at 45 Mb/s. Shots are cached in `$W/work/shots/` by content hash: a notes round only re-renders the shots that changed. Delete
`$W/work/` to reclaim disk after delivery (4K 10-bit shot files are ~100-200 MB each).

## The cut spec (`cut-spec.json`, for build-cut.py)

The way to hand-edit: shots in bars, not seconds. `music_start_bar` is an index into `beats.json` "bars" (start on a
phrase: every 4th bar from the first). `in` is where the shot starts AT THE CUT; a dissolve borrows half its length
before it, and build-cut shifts the source for you.

```json
{"music_start_bar": 4,
 "shots": [{"clip": "0064", "in": 2.0, "bars": 2, "speed": 0.5, "transition": ["cut", 0], "note": "open sea, title"},
           {"clip": "0066", "in": 1.5, "bars": 2, "transition": ["fade", 0.8]}],
 "vertical": {"music_start_bar": 14, "shots": [{"clip": "0070", "in": 1.0, "bars": 2, "x": 0.5}]}}
```

build-cut.py refuses (and lists every problem): a shot outside the clip's usable range, a film over `--max`, the same
clip twice in a row, a clip used more than `--max-uses` (2) times, two shots of the same source moment, slow motion
on a clip under 48 fps, a portrait clip in the 16:9 film, and what the style forbids (realestate: only `cut`, `fade`,
`fadeblack`, `dissolve`; no shot under 2.5 s; at most two slow-motion shots).

A shot may carry `"label": "Kitchen"` (at most 28 characters): a lower-third over that shot, kept only with
`--labels`. The end card takes more lines and a logo: `--end-line "$1,250,000" --end-line=--- --end-line "Jane Doe"
--logo logo.png` (`---` draws a short rule; write it with `=` so it is not read as a flag).

## The cutlist (`cutlist.json`)

```json
{
  "lut": "/abs/path/to/DJI_Mini4Pro_DLogM_to_Rec709.cube",   // null: the approximate curve
  "look": {"contrast": 0.35, "saturation": 0.05, "vibrance": 0.15, "warmth": 0.0, "vignette": 0.25},
  "fade_in": 1.0, "fade_out": 1.5,                            // from / to black, seconds
  "music": {"file": "music/track.mp3", "start": 0.0, "fade_in": 0.3, "fade_out": 2.5, "lufs": -14, "db": 0},
  "sfx": [{"file": "sfx/whoosh.wav", "at": 23.6, "db": -8}],
  "title": {"text": "Fort Lauderdale", "sub": "Florida · Oct 2026", "at": 1.2, "dur": 4.0},
  "end": {"text": "Your Name", "sub": "Shot on DJI Mini 4 Pro", "dur": 3.5,
          "lines": ["$1,250,000", "---", "Agent · Brokerage"], "logo": "/abs/logo.png"},   // lines / logo: optional
  "labels": false,                                            // true: draw the shots' "label" lower-thirds
  "duration": 59.98,                                          // informational; render computes the real length
  "shots": [
    {"clip": "0067", "in": 7.0, "dur": 3.5, "speed": 0.5, "transition": {"type": "cut", "dur": 0}, "at": 0.0}
  ],
  "vertical": {"shots": [{"clip": "0070", "in": 4.0, "dur": 2.6, "speed": 1.0, "x": 0.5, "pan": [0.3, 0.6],
                          "transition": {"type": "cut", "dur": 0}}], "title": {"at": 0.8, "dur": 3.0}, "end": {"dur": 3.0},
               "music": {"start": 31.65}}                       // optional: the short's own stretch of the track
}
```

- `in` is SOURCE seconds; `dur` is TIMELINE seconds; the shot uses `dur * speed` of source. speed 0.5 is clean 2x
  slow motion from 60p; never below 0.5 (frames would repeat).
- `transition` is the join INTO the shot, centred on the cut, and its `dur` overlaps both shots: the planner already
  lengthens both sides by half. When editing by hand, keep each cut on its beat: the shot before and after each
  grows by `dur / 2`. Types: `cut`, `fade`, `fadeblack`, `zoomin`, `smoothleft/right/up/down` (any ffmpeg xfade name).
- `x` (0-1) places the 9:16 window across the 16:9 frame; `pan: [x0, x1]` moves it over the shot. Look at the frame:
  the subject (the coastline, the building, the wave) must stay inside the window.
- After hand edits, the planner's `at` values are stale (informational only); render.py works from `dur`.

## Editing doctrine

The doctrine lives with the style: `styles/drone.md` (travel film: open strong, cut on the beat, the drop gets the one
whip, slow-mo on hero moments, a ceiling not a target) and `styles/realestate.md` (the tour order for exterior only
or interior & exterior, calm 3-6 s shots, dissolves within a room, labels only on unmistakable rooms, what a buyer
must not see). Rules that hold in every style:

- **The length is a ceiling, and shorter beats repeating.** Each clip at most twice, never the same source moment.
  Rough budget: about one 16:9 second per 2 s of usable footage.
- **Slow motion only from 50/60 fps** (ANALYSIS.md "Slow-mo: yes"); a 24/30 fps clip would repeat frames.
- **Portrait clips** (phones) only in the 9:16 short; ANALYSIS.md lists size, fps and colour profile per clip.

## Colour

Every clip is graded by its own profile, detected at ingest (`profile` in clips.json, listed in ANALYSIS.md):
`dlogm` (DJI 10-bit with the log footprint: the LUT), `rec709` (phones, normal profiles: no LUT, the look at half
strength), `hlg` / `pq` (HDR phone video, from the transfer tag: a generated BT.2020 -> Rec.709 tone-mapping cube,
`hdr_lut.py`, since this ffmpeg has no zscale), and `graded` (never detected: `--profile graded` for clips the user
already graded; no LUT, no look, the colour passes through). `ingest.py --profile` forces one profile for every clip when the
detection is wrong; sheets come from the same grade, so a wrong profile is visible there (log looks grey and flat,
a double-graded clip looks harsh and oversaturated).

DJI D-Log M (the Mini 4 Pro's 10-bit flat profile) needs its LUT. The user downloads it (free) from DJI's Mini 4 Pro
download page ("D-Log M to Rec.709 LUT") into `assets/luts/` (any `.cube`; the first one alphabetically is used).
Without it, `grade_vf` uses an approximate curve (looks fine, less accurate in skies and water). Tell-tale of log
footage: average saturation 6-13 and blacks at 61-88 (8-bit) on the Mini 4 Pro clips; the same clips graded measure
16 / 50, which is why detection also requires a DJI, 10-bit source.

The `look` sits on top of the LUT, in 16-bit RGB: contrast (S-curve), saturation, vibrance (protects already rich
colours, good for turquoise water), warmth (-1 cool to +1 warm), vignette (0-1). Ingest proxies use the same grade,
so the sheets show what the render will look like; re-ingest with `--force` after changing the LUT.

## Music

Suggest 3-4 tracks that fit the footage and the length (60 s film: a 2-3 min track, the planner uses the bar nearest the
target). Use royalty-free libraries whose licence allows YouTube / social use without a claim: Pixabay Music (Pixabay
Content License, no attribution), YouTube Audio Library (check each track's attribution requirement), Uppbeat (free
tier needs a credit line). Never a commercial song. State the licence and any credit line the description needs.
For drone films: a build (quiet intro, a drop or lift around 15-25 s) suits better than a flat loop. A property video
is commercial use: calm, 80-110 BPM, and a licence that allows commercial use (see `styles/realestate.md`).

`beats.py --bpm N` when the tempo is an octave off. To skip a long intro, pick a bar time from beats.json and plan
with `plan-cut.py --music-start <bar>`: the beat grid shifts with it and `music.start` is set to match.

## Sound effects

Silence plus music is the default. A sound has to earn its place: a soft whoosh on the one whip transition (peak
aligned to the cut, -8 to -12 dB), light wind or surf ambience under a calm opening if it matches the shot. Trim the
silence before the transient (`silenceremove=start_periods=1:start_threshold=-40dB`) and peak-normalise each file to
-3 dBFS before mixing. Pixabay sound effects are free with no attribution.

## Traps (each one cost a render)

- **DJI files carry an MJPEG thumbnail as a second video stream and two data streams.** Always `-map 0:v:0`.
- **Never resample 59.94 fps to a fixed rate for analysis**: dropped and duplicated frames read as jerky flying.
  Proxies take every 2nd frame, analysis every k-th proxy frame.
- **Water and sky have little fine detail**: sharpness is judged against the clip's own 90th percentile, not across
  clips, or every sea shot loses to the skyline.
- **This ffmpeg has no drawtext**: all text is a Chrome-rendered PNG (cards.py), sheet tiles are listed in the .md.
- **youtube-edit's DM Sans woff2 files are 7 KB glyph subsets** (no lowercase, no digits): use the variable TTF here.
- **Chrome lazy-loads fonts and screenshots before they arrive**, and drops elements at a fractional
  `--force-device-scale-factor`: cards.py loads the fonts first and renders at scale 1 at the output size.
- **loudnorm's second pass falls back to dynamic mode** on music with a quiet intro and misses the target: render.py
  measures (ebur128), applies a static gain and a -1 dBTP limiter, and tops up if the limiter ate loudness.
- **ffmpeg 9: frame colour properties override the output flags**: `setparams` tags the frames as BT.709 too.
- **concat and xfade refuse inputs whose sample aspect ratios differ**: a non-proportional scale (a portrait clip
  letterboxed, a cropped 9:16 window) leaves an odd SAR, so proxies and fitted shots end with `setsar=1`.
- **ffmpeg autorotates phone clips on decode**: probe() swaps width / height for a 90/270 display matrix.
- **xfade offsets drift if shot lengths are not whole frames**: render.py rounds every shot to frames first and
  computes offsets in frames. verify.py's black check catches a flash at a join.

## Verify and deliver

`verify.py` must PASS (spec, frame count, a/v length, no unintended black or freeze, loudness within 1 LU, true peak).
A freeze is a shot whose source ran out (`in + dur * speed` past the end of the clip). Then look at the `.sheet.png`
(two tiles per shot) for wrong grades, a card over a busy area, or two shots that look alike. Hand over the paths of
the 4K master and the vertical short, the music credit line if the licence needs one, and a one-line summary of the cut.

## Drone Studio (the UI)

`python3 studio/server.py` (in the project folder) opens a local page: style (drone film / real estate, exterior
only or interior & exterior), clips, music, colour, max length, title, end card or listing details and logo, room
labels, notes, versions, output folder, **Create video**. Each job is a project folder in `studio-jobs/`. The edit step runs
`claude -p` headless in the job folder with `studio/prompts/edit.md`: Claude reads this file, looks at the sheets,
writes `cut-spec.json` and runs build-cut.py until it passes. "Automatic" (or no `claude` CLI) uses plan-cut.py.

## Notes loop

The user watches and gives notes in plain words ("too many skyline shots", "slow-mo on the wave", "warmer"). Apply
them to `cutlist.json`, re-render the preview, verify, look. When a note is a taste rule that should hold for every
film ("never open on open water", "no dissolves"), and the user says "save this to the skill", add it as one plain
rule to the right section of this file.
