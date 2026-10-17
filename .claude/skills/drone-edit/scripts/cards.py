#!/usr/bin/env python3
"""cards.py: a title or end card as a transparent PNG (DM Sans, rendered by headless Chrome), for render.py to fade
over the footage. This ffmpeg build has no drawtext, and Chrome sets type better anyway.
  title  bottom left: a small spaced kicker line (sub), a thin rule, the big title (text)
  end    centred: the text, the sub line under it; optionally more lines ("---" draws a short rule) and a logo
         (PNG / JPG / WebP, at most 12% of the frame high) under them, over a soft scrim so a listing card reads
  label  a room name as a small lower-third (bottom left; higher in 9:16, clear of the apps' buttons)
All carry a soft shadow; title and end a faint bottom gradient so white type reads over bright sky or water.
Usage:
  python3 cards.py --kind title --text "Lago Puelo" --sub "PATAGONIA · OCT 2026" --out card-title.png [--size 3840x2160]
  python3 cards.py --kind end --text "Esteban Schafir" --sub "Shot on DJI Mini 4 Pro" --out card-end.png --size 1080x1920
  python3 cards.py --kind end --text "12 Ocean Dr" --sub "Hollywood, FL" --line "$1,250,000" --line "---" \
                   --line "Jane Doe · Coastal Realty" --logo logo.png --out card-end.png
  python3 cards.py --kind label --text "Kitchen" --out card-label.png
"""
import argparse, base64, html, json, os, shutil, subprocess, sys, tempfile
import common

CHROME = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "chromium", "chrome"]


def chrome():
    for c in CHROME:
        p = c if os.path.isabs(c) and os.path.exists(c) else shutil.which(c)
        if p:
            return p
    return None


def font_face():
    # inlined: Chrome treats a file:// font from another folder as cross-origin and silently falls back to a serif.
    # The full variable font (all Latin glyphs, weights 100-1000); youtube-edit's 7 KB woff2 files are glyph subsets.
    with open(os.path.join(common.FONT_DIR, "DMSans-Variable.ttf"), "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return "@font-face{font-family:DM;font-weight:100 1000;src:url(data:font/ttf;base64,%s) format('truetype')}" % data


LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def logo_img(path, css_h):
    ext = os.path.splitext(path)[1].lower()
    if ext not in LOGO_TYPES:
        common.die("logo must be PNG, JPG or WebP: %s" % path)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return "<img src='data:%s;base64,%s' style='display:block;margin:%.0fpx auto 0;max-height:%.0fpx;max-width:40%%'>" % (
        LOGO_TYPES[ext], data, css_h * 0.035, css_h * 0.12)


def page(kind, text, sub, css_w, css_h, lines=(), logo=None):
    font = font_face()
    vertical = css_h > css_w
    u = css_w / 1920 if not vertical else css_w / 1080                  # 1 unit = 1 px at 1080p
    shade = "text-shadow:0 %.1fpx %.1fpx rgba(0,0,0,.45),0 0 %.1fpx rgba(0,0,0,.25);" % (2 * u, 14 * u, 40 * u)
    scrim = ""
    if kind == "label":
        pos = ("left:%.0fpx;bottom:%.0fpx;" % (110 * u, 110 * u)) if not vertical else ("left:%.0fpx;bottom:%.0fpx;" % (70 * u, 400 * u))
        body = "<div class=b style='%s'><div class=r style='width:%.0fpx;margin:0 0 %.0fpx'></div><div class=l>%s</div></div>" % (
            pos, 40 * u, 14 * u, html.escape(text))
        size = 76
        return _html(font, css_w, css_h, shade, size, u, vertical, body, "", gradient=False)
    if kind == "title":
        pos = ("left:%.0fpx;bottom:%.0fpx;" % (110 * u, 120 * u)) if not vertical else ("left:%.0fpx;right:%.0fpx;bottom:%.0fpx;" % (70 * u, 70 * u, 330 * u))
        body = ("<div class=b style='%s'>%s<div class=r></div><div class=t>%s</div></div>" %
                (pos, "<div class=s>%s</div>" % html.escape(sub) if sub else "", html.escape(text)))
        size = 104 if not vertical else 92
    else:
        more = ""
        for ln in lines or ():
            more += ("<div class=r style='margin:%.0fpx auto'></div>" % (26 * u)) if ln.strip() == "---" else \
                    "<div class=m>%s</div>" % html.escape(ln)
        if logo:
            more += logo_img(logo, css_h)
        if more:            # a listing card: several lines over moving footage need a calmer ground
            more = "<div style='margin-top:%.0fpx'>%s</div>" % (26 * u, more)
            scrim = "<div style='position:absolute;inset:0;background:radial-gradient(ellipse at center,rgba(0,0,0,.5),rgba(0,0,0,.22))'></div>"
        body = ("<div class=b style='left:0;right:0;top:50%%;transform:translateY(-50%%);text-align:center'>"
                "<div class=t>%s</div>%s%s</div>" % (html.escape(text), "<div class=s style='margin-top:%.0fpx'>%s</div>" % (22 * u, html.escape(sub)) if sub else "", more))
        size = 76 if not vertical else 68
    return _html(font, css_w, css_h, shade, size, u, vertical, body, scrim)


def _html(font, css_w, css_h, shade, size, u, vertical, body, scrim, gradient=True):
    return """<!doctype html><html><head><meta charset=utf-8><style>%s
html,body{margin:0;width:%dpx;height:%dpx;background:transparent;overflow:hidden}
.g{position:absolute;left:0;right:0;bottom:0;height:45%%;background:linear-gradient(to top,rgba(0,0,0,.28),rgba(0,0,0,0))}
.b{position:absolute;color:#fff;font-family:DM;%s}
.t{font-weight:700;font-size:%.0fpx;line-height:1.02;letter-spacing:-.015em}
.s{font-weight:500;font-size:%.0fpx;letter-spacing:.32em;text-transform:uppercase;opacity:.92}
.m{font-weight:500;font-size:%.0fpx;line-height:1.5;opacity:.95}
.l{font-weight:600;font-size:%.0fpx;letter-spacing:.04em}
.r{width:%.0fpx;height:%.1fpx;background:#fff;opacity:.85;margin:%.0fpx 0 %.0fpx}
</style></head><body>%s%s<script>
// fonts load lazily and the screenshot does not wait for them: load all three, then put the type in
Promise.all([400, 500, 700].map(w => document.fonts.load(w + " 40px DM"))).then(() => document.body.insertAdjacentHTML("beforeend", %s));
</script></body></html>""" % (font, css_w, css_h, shade, size * u, (30 if vertical else 22) * u, (40 if vertical else 32) * u,
                               (46 if vertical else 40) * u, 64 * u, 2.5 * u, 22 * u, 20 * u,
                               "<div class=g></div>" if gradient else "", scrim, json.dumps(body))


def render_card(kind, text, sub, out, width, height, lines=(), logo=None):
    exe = chrome()
    if not exe:
        common.die("Google Chrome not found (cards need it)")
    # viewport = output size at device scale 1, the layout scales itself (page()'s unit u): a fractional
    # --force-device-scale-factor lays the page out wrong and drops elements
    tmp = tempfile.mkdtemp(prefix="drone-card-")
    try:
        src = os.path.join(tmp, "card.html")
        with open(src, "w") as f:
            f.write(page(kind, text, sub, width, height, lines, logo))
        out = os.path.abspath(out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                        "--force-device-scale-factor=1", "--window-size=%d,%d" % (width, height),
                        "--default-background-color=00000000", "--virtual-time-budget=3000", "--user-data-dir=" + os.path.join(tmp, "profile"),
                        "--screenshot=" + out, "file://" + src], capture_output=True, timeout=120)
    finally:
        shutil.rmtree(tmp, True)
    if not os.path.exists(out):
        common.die("Chrome did not write %s" % out)
    p = common.probe(out)
    if (p["width"], p["height"]) != (width, height):
        common.die("card %s is %dx%d, wanted %dx%d" % (out, p["width"], p["height"], width, height))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["title", "end", "label"], required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--sub", default="")
    ap.add_argument("--line", action="append", default=[])
    ap.add_argument("--logo")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="3840x2160")
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.lower().split("x"))
    print(render_card(a.kind, a.text, a.sub, a.out, w, h, a.line, a.logo))
