# Drone Footage

Tools for turning raw drone footage (DJI clips, D-Log M or normal, mixed with phone or gimbal clips) into a finished
film with music. It has two parts:

- **Drone Studio** (`studio/`): a local web page where you add clips and a music track, set the length, title and
  notes, and press Create video.
- **drone-edit skill** (`.claude/skills/drone-edit/`): the editing pipeline. It picks the best shots, grades each clip
  by its colour profile, cuts on the music's beats, adds title and end cards, and renders with ffmpeg. Drone Studio
  runs it for you, and you can also use it directly from Claude Code.

Two styles are supported: a drone travel film, and a real-estate property tour with room labels and a listing end card.

## Requirements

- macOS (the file and folder pickers use macOS dialogs)
- Python 3 with numpy: `python3 -m pip install --user numpy`
- FFmpeg and ffprobe: `brew install ffmpeg`
- Google Chrome (renders the title and end cards)
- Optional: the [Claude Code](https://claude.com/claude-code) CLI (`claude`), to have Claude write the edit from your
  notes. Without it, use the Automatic editor.
- Optional: the DJI LUT for D-Log M footage, as a `.cube` file in `.claude/skills/drone-edit/assets/luts/`

## Starting the service

From the project root:

```bash
python3 studio/server.py
```

This starts Drone Studio at http://127.0.0.1:8765 and opens it in your browser. It only listens on `127.0.0.1`.

Options:

| Flag | Description |
|---|---|
| `--port <n>` | Use a different port (default `8765`) |
| `--no-browser` | Start the server without opening a browser |

Environment variables:

| Variable | Description |
|---|---|
| `DRONE_STUDIO_JOBS` | Where job folders are written (default `studio-jobs/`) |
| `DRONE_STUDIO_CLAUDE` | Path to the `claude` CLI, if it is not on your `PATH` |

Stop the server with `Ctrl+C`.

## Usage

1. Add clips (upload them, or point at a folder) and a music track.
2. Choose the style, maximum length, versions (16:9 4K, 16:9 1080p, 9:16 short) and editor (Claude or Automatic).
3. Press Create video.

Each job gets a folder in `studio-jobs/<date>-<title>/`. Finished films are copied to the output folder (default
`~/Movies/Drone Studio`). 4K shot caches are large, so delete old job folders to free disk space.

See `studio/README.md` for details on each option.

## Project layout

```
studio/                       Drone Studio server, web page and tests
.claude/skills/drone-edit/    Editing pipeline scripts, styles, fonts and LUTs
raw/                          Source clips (not tracked)
videos/                       Working folders for edits run from Claude Code (not tracked)
studio-jobs/                  Drone Studio job folders (not tracked)
```

## Tests

```bash
python3 -m unittest discover studio/tests                       # Drone Studio, about 2 minutes
cd .claude/skills/drone-edit/scripts && python3 -m unittest tests  # pipeline, about 30 seconds
```

Both use synthetic media, so no footage is needed.
