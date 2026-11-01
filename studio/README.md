# Drone Studio

A local page for the `drone-edit` pipeline: add clips and a track, set the length, title, end card and notes,
press **Create video**. Nothing to sign into; it only listens on `127.0.0.1`.

```bash
python3 studio/server.py            # opens http://127.0.0.1:8765  (--port, --no-browser)
```

- **Clips:** upload (copied into the job) or use a folder in place. **Music:** upload or pick a file.
- **Maximum length** is a ceiling: the edit ends early rather than repeat shots.
- **Editor:** *Claude* runs your installed `claude` CLI headless in the job folder (your Claude Code login, counts
  toward your plan; about 1–2 minutes and roughly $0.50 of usage per edit). It reads your notes, looks at every
  clip's contact sheet and writes the cut. *Automatic* uses the rule-based planner and ignores the notes. If Claude
  fails or writes a cut that does not pass the checks, the job falls back to Automatic and says so.
- **Versions:** 16:9 4K, 16:9 1080p, 9:16 short. **Quality:** Best (x264) or Fast (VideoToolbox).
- **Also a CapCut project:** after the renders, the edit is written into CapCut's project list (16:9, plus 9:16 if
  the short is on), to finish by hand. Each shot uses the original clip, ungraded, with the rest of the clip as
  handles. The title and end cards are on an overlay track and the music is on its own track. **Quit CapCut first**:
  while it runs, the project is not written (the stage warns; quit CapCut and press Edit again). In CapCut, apply the
  LUT to the D-Log M shots (Adjustment → LUT → import the `.cube` from the media folder). Dissolves arrive as cuts,
  and the fades to black, card fades and loudness are left to you. `CUT-SHEET.md` in the media folder lists every
  shot and each of these, and is the fallback if CapCut won't open the project.
- **Edit again** re-runs only the edit and the renders with your new notes; unchanged shots come from the cache.
- **The shot sheet:** after the edit, the page shows the cut as numbered frames in film order (and the short's, and
  the clips left out). Click one to play from there. In notes, say "shot 4", "short shot 2", or a clip number like
  0045: Claude reads them against the current cut.
- **Style:** *Drone film*, or *Real estate* with *Interior & exterior* or *Exterior only* footage. Real estate cuts a
  calm tour (dissolves, 3-6 s shots, no whips) in tour order, can label rooms ("Kitchen"), and ends on a listing card:
  address and city, price, beds · baths · area, agent · brokerage, phone · email · website, and an optional logo
  (PNG / JPG / WebP). Empty fields are left off. The rules are in `.claude/skills/drone-edit/styles/realestate.md`.
- **Colour:** detected per clip: DJI D-Log M gets the LUT, phone / normal clips a lighter grade, HDR (HLG / PQ) phone
  video a tone map. Force one profile for all clips if the detection gets it wrong, or choose *Already graded* for clips
  you graded yourself: their colour is left exactly as delivered (export at 50/60 fps if you want slow motion). Portrait phone clips are only used
  in the 9:16 short; slow motion only on 50/60 fps clips.

Jobs live in `studio-jobs/<date>-<title>/` (a normal drone-edit project: proxies, sheets, `cut-spec.json`,
`cutlist.json`, `out/`, `job.log`). Finished films are copied to the output folder as `<title>-<version>.mp4`.
Delete old job folders to free disk: 4K shot caches are large.

Tests: `python3 -m unittest discover studio/tests` (synthetic media, a stand-in for the `claude` CLI; ~2 minutes).
