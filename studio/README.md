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
- **Edit again** re-runs only the edit and the renders with your new notes; unchanged shots come from the cache.
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
