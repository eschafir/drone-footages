"""editor.py: the creative step. Claude (the local `claude` CLI, headless, the user's own login) looks at the contact
sheets, reads the notes and writes cut-spec.json; build-cut.py turns it into the cutlist and refuses bad specs.
If Claude is not chosen, not installed, or does not produce a valid cut, plan-cut.py makes an automatic one.
Claude may only Read, Write/Edit and run build-cut.py, with the job folder as its working directory."""
import json, os, shutil, subprocess, sys, threading

import jobs

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "prompts", "edit.md")
TIMEOUT = 15 * 60


def claude_bin():
    for c in (os.environ.get("DRONE_STUDIO_CLAUDE"), shutil.which("claude"), os.path.expanduser("~/.local/bin/claude")):
        if c and os.path.exists(c):
            return c
    return None


def cut_config(job):
    """The settings build-cut.py needs, as its --config file: length ceilings, style, cards, labels, logo."""
    s = job.data["settings"]
    c = {"max": s["max_seconds"], "style": s.get("style", "drone"), "title": s["title"], "sub": s["sub"]}
    if "vertical" in s["versions"]:
        c["vertical_max"] = s["vertical_max_seconds"]
    if c["style"] == "realestate":
        text, sub, lines = jobs.listing_card(s)
        c.update(end=text, end_sub=sub, end_line=lines, labels=bool(s.get("labels")))
        if job.logo_file:
            c["logo"] = job.logo_file
    else:
        c.update(end=s["end"] or s["title"], end_sub=s["end_sub"])
    return c


def plan_args(job):
    """The same settings as plan-cut.py flags (the automatic editor has no labels)."""
    c = cut_config(job)
    args = ["--max", "%g" % c["max"], "--style", c["style"], "--title", c["title"], "--sub", c["sub"], "--end", c["end"], "--end-sub", c["end_sub"]]
    args += ["--vertical-max", "%g" % c["vertical_max"]] if "vertical_max" in c else ["--vertical-target", "0"]
    args += ["--end-line=" + x for x in c.get("end_line", [])] + (["--logo", c["logo"]] if c.get("logo") else [])
    return args


def revision_text(revisions, automatic=False):
    """The notes for an "Edit again" run. The studio shows the cut as a numbered sheet, so notes say "shot 4": the
    numbers belong to the cut as it is now, which is why only the newest note is applied and older ones are history."""
    out = ["This is a revision. The current cut is in cut-spec.json: read it first. Apply the NEW NOTE below and keep "
           "everything it does not mention.",
           "How the user refers to things (the studio shows the cut as a numbered sheet):",
           "- \"shot N\" is the N-th entry of \"shots\" in cut-spec.json as it is now (1 = the first shot of the 16:9 film);",
           "- \"short shot N\" is the N-th entry of \"vertical\".\"shots\";",
           "- a four-digit number such as 0045 is a clip id (the sheet also shows the clips the cut does not use).",
           "", "NEW NOTE: " + revisions[-1]]
    if automatic:       # the last cut came from the planner: cut-spec.json is an older, rejected spec
        out[0:1] = ["This is a revision. The current cut was made by the automatic planner and is in cutlist.json (\"shots\", "
                    "in seconds); cut-spec.json is an older spec that did not pass the checks. Read cutlist.json first, rebuild "
                    "that cut as a new cut-spec.json in bars, apply the NEW NOTE below and keep everything it does not mention."]
        out = [x.replace("in cut-spec.json as it is now", "in cutlist.json as it is now") for x in out]
    if len(revisions) > 1:
        out += ["", "Earlier notes, already applied (their shot numbers referred to earlier cuts; do not apply them again):"]
        out += ["- " + r for r in revisions[:-1]]
    return "\n".join(out)


def brief(job):
    """The style-specific part of the prompt: footage, labels, listing."""
    s = job.data["settings"]
    if s.get("style", "drone") != "realestate":
        return "Style: drone film. Follow its Structure section."
    out = ["Style: real estate, a listing video. Follow its Structure section and its tour order for this footage:"]
    if s.get("footage") == "exterior":
        out.append("- Footage: EXTERIOR ONLY. Use only exterior and aerial shots. If a clip shows an interior, leave it out and "
                   "say so in your reply.")
    else:
        out.append("- Footage: INTERIOR & EXTERIOR. Exteriors open and close the film; the interiors come in one block, grouped "
                   "by room in tour order.")
    if s.get("labels"):
        out.append("- Room labels: ON. Put \"label\" on the first shot of each room you can identify with certainty (see the "
                   "style's Labels rule); no label when unsure. They also work in the vertical short.")
    else:
        out.append("- Room labels: off. Do not add \"label\" fields.")
    text, sub, lines = jobs.listing_card(s)
    facts = [x for x in [sub] + lines if x and x != "---"]
    out.append("- The listing: %s%s. The title card shows the address over the opening; the end card shows these details "
               "(%d lines), so the last shot must be calm and uncluttered." % (text, (" · " + " · ".join(facts)) if facts else "", len(lines) + 1))
    return "\n".join(out)


def music_map(job):
    """MUSIC.md: the bar grid with energy, phrase starts and section changes, so Claude never has to parse beats.json."""
    with open(os.path.join(job.dir, "beats.json")) as f:
        B = json.load(f)
    bars, energy = B["bars"], B["energy"]
    sec = {round(t, 2): d for t, d in B.get("sections", [])}

    def e_between(a, b):
        xs = [e for t, e in energy if a <= t < b]
        return sum(xs) / len(xs) if xs else 0.0
    bar_len = (bars[-1] - bars[0]) / max(1, len(bars) - 1)
    lines = ["# Music map", "", "%s · %.1f BPM · one bar = %.2f s · %d bars · %.0f s long" % (os.path.basename(B["file"]), B["bpm"], bar_len, len(bars), B["duration"]),
             "", "Phrases start every 4th bar from bar 0 (marked P). A section change (drop / breakdown) is marked with its energy jump.",
             "", "| Bar | Time (s) | Energy 0-1 | |", "|---|---|---|---|"]
    for i, t in enumerate(bars[:-1]):
        mark = ("P " if i % 4 == 0 else "") + ("SECTION %+.2f" % sec[round(t, 2)] if round(t, 2) in sec else "")
        lines.append("| %d | %.2f | %.2f | %s |" % (i, t, e_between(t, bars[i + 1]), mark))
    with open(os.path.join(job.dir, "MUSIC.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return bar_len


def automatic(job, note=""):
    s = job.data["settings"]
    job.data["activity"] = "Automatic edit" + (" (%s)" % note if note else "")
    job.save()
    job.run("edit", job.py("plan-cut.py", "--project", job.dir, *plan_args(job)))
    return "automatic", "Automatic edit%s." % (" (%s)" % note if note else "")


def describe(tool, inp):
    """A short line for the page from one of Claude's tool calls."""
    p = inp.get("file_path") or inp.get("path") or ""
    b = os.path.basename(p)
    if tool == "Read":
        if b == "overview.png":
            return "Looking at all the clips"
        if p.endswith(".png") and "/sheets/" in p:
            return "Looking at clip %s" % b[:-4]
        return {"ANALYSIS.md": "Reading the footage analysis", "MUSIC.md": "Reading the music map",
                "SKILL.md": "Reading the editing rules"}.get(b, "Reading %s" % b)
    if tool in ("Write", "Edit"):
        return "Writing the cut" if b == "cut-spec.json" else "Writing %s" % b
    if tool == "Bash":
        return "Checking the cut against the beat grid" if "build-cut" in inp.get("command", "") else "Running a check"
    return tool


def run_claude(job, exe, prompt):
    build_cut = os.path.join(jobs.SCRIPTS, "build-cut.py")
    cmd = [exe, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--allowedTools", "Read", "Write", "Edit", "Bash(%s %s:*)" % (sys.executable, build_cut), "Bash(python3 %s:*)" % build_cut,
           "--add-dir", jobs.SKILL]
    job.log("$ claude -p <prompt %d chars> --output-format stream-json (cwd %s)" % (len(prompt), job.dir))
    job.proc = subprocess.Popen(cmd, cwd=job.dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                start_new_session=True)
    result, last_text, steps = None, "", 0
    proc = job.proc

    def too_long():                       # stop Claude only: the edit then falls back to the automatic cut
        job.log("Claude took longer than %d minutes, stopping it" % (TIMEOUT // 60))
        try:
            os.killpg(proc.pid, 15)
        except ProcessLookupError:
            pass
    timer = threading.Timer(TIMEOUT, too_long)
    timer.start()
    try:
        for line in job.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                job.log(line.rstrip())
                continue
            if ev.get("type") == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") == "tool_use":
                        steps += 1
                        act = describe(c.get("name", ""), c.get("input") or {})
                        job.log("claude: %s %s" % (c.get("name"), json.dumps(c.get("input"))[:300]))
                        job.update(activity=act)
                        job.stage("edit", progress=min(0.95, steps / 30.0))
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        last_text = c["text"].strip()
                        job.log("claude: " + last_text[:2000])
            elif ev.get("type") == "user":                     # tool results: log the refused / failed ones
                for c in (ev.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                        body = c.get("content")
                        job.log("claude tool error: %s" % (body if isinstance(body, str) else json.dumps(body))[:400])
            elif ev.get("type") == "result":
                result = ev
                job.log("claude result: %s" % json.dumps({k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms", "total_cost_usd")}))
    finally:
        code = job.proc.wait()
        job.proc.stdout.close()
        timer.cancel()
        job.proc = None
    if job.cancel_flag:
        raise jobs.Cancelled()
    ok = code == 0 and result is not None and not result.get("is_error")
    return ok, (result or {}).get("result") or last_text


def edit(job):
    s = job.data["settings"]
    music_map(job)
    exe = claude_bin()
    if s["editor"] == "auto":
        return automatic(job)
    if not exe:
        return automatic(job, "the claude command was not found")
    revisions = job.data.get("revisions") or []
    spec = os.path.join(job.dir, "cut-spec.json")
    with open(PROMPT) as f:
        prompt = f.read()
    config = os.path.join(job.dir, "build-cut.json")
    with open(config, "w") as f:
        json.dump(cut_config(job), f, indent=1)
    style = s.get("style", "drone")
    fill = {
        "SKILL_MD": os.path.join(jobs.SKILL, "SKILL.md"),
        "STYLE_MD": os.path.join(jobs.SKILL, "styles", style + ".md"),
        "BRIEF": brief(job),
        "BUILD_CUT": "python3 %s --project . --config build-cut.json" % os.path.join(jobs.SCRIPTS, "build-cut.py"),
        "MAX": "%g" % s["max_seconds"],
        "VERTICAL": ("Also make the 9:16 short (\"vertical\" in the spec), at most %g s, its own stretch of the music (usually around "
                     "the drop), with x / pan placing the subject inside the 9:16 window." % s["vertical_max_seconds"])
                    if "vertical" in s["versions"] else "No vertical short this time: leave \"vertical\" out of the spec.",
        "TITLE": s["title"], "SUB": s["sub"] or "(none)",
        "END": cut_config(job)["end"], "END_SUB": cut_config(job)["end_sub"] or "(none)",
        "NOTES": s["notes"] or "(none: use your judgement and the editing rules)",
        "REVISION": revision_text(revisions, job.data.get("editor_used") == "automatic") if revisions and os.path.exists(os.path.join(job.dir, "cutlist.json")) else "",
    }
    for k, v in fill.items():
        prompt = prompt.replace("{{%s}}" % k, v)
    before = os.path.getmtime(spec) if os.path.exists(spec) else 0
    job.update(activity="Starting Claude")
    ok, summary = run_claude(job, exe, prompt)
    if not os.path.exists(spec) or os.path.getmtime(spec) <= before:
        return automatic(job, "Claude did not write a cut" + ("" if ok else ": " + (summary or "it stopped with an error")[:160]))
    try:                                                            # the studio's own check, whatever Claude reported
        job.run("edit", job.py("build-cut.py", "--project", job.dir, "--config", config))
    except jobs.StageFailed as e:
        job.log("Claude's cut did not pass build-cut.py: %s" % e)
        return automatic(job, "Claude's cut did not pass the checks")
    return "claude", summary or "Claude's cut."
