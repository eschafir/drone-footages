#!/usr/bin/env python3
"""Drone Studio: a local page for the drone-edit pipeline. No login, nothing leaves the machine except what the
`claude` CLI sends when Claude does the edit.
  python3 studio/server.py [--port 8765] [--no-browser]
Listens on 127.0.0.1 only and rejects requests whose Host or Origin is not this server (a web page elsewhere
cannot drive it). Uploads stream to disk in 1 MB chunks; media is served with Range so the players can seek."""
import argparse, json, mimetypes, os, re, shutil, subprocess, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import editor, jobs  # noqa: E402

STATIC = os.path.join(HERE, "static")
FONT = os.path.join(jobs.SKILL, "assets", "fonts", "DMSans-Variable.ttf")
LOGO_MAX = 10 << 20
CHUNK = 1 << 20
MANAGER = None
PORT = 8765


def check():
    def has(cmd):
        return bool(shutil.which(cmd))
    try:
        import numpy  # noqa: F401
        numpy_ok = True
    except ImportError:
        numpy_ok = False
    luts = [f for f in os.listdir(os.path.join(jobs.SKILL, "assets", "luts")) if f.endswith(".cube")] \
        if os.path.isdir(os.path.join(jobs.SKILL, "assets", "luts")) else []
    return {"ffmpeg": has("ffmpeg") and has("ffprobe"), "numpy": numpy_ok,
            "chrome": os.path.exists("/Applications/Google Chrome.app") or has("google-chrome"),
            "claude": bool(editor.claude_bin()), "lut": luts[0] if luts else None, "skill": os.path.isdir(jobs.SCRIPTS),
            "jobs_dir": jobs.JOBS_DIR, "default_output": os.path.expanduser("~/Movies/Drone Studio"),
            "picker": sys.platform == "darwin"}


def pick(kind):
    """macOS choose-folder / choose-file dialog -> POSIX path (None when the user cancels)."""
    script = {"folder": 'POSIX path of (choose folder with prompt "Choose the folder with your clips")',
              "output": 'POSIX path of (choose folder with prompt "Choose where the finished videos go")',
              "music": 'POSIX path of (choose file with prompt "Choose the music track" of type {"public.audio"})'}[kind]
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


class Handler(BaseHTTPRequestHandler):
    server_version = "DroneStudio/1"

    def log_message(self, fmt, *args):  # quiet: the page polls every second
        pass

    # ---- helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, msg):
        self.send_json({"error": msg}, code)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1 << 20:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def trusted(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return False
        return True

    def job(self, jid):
        j = MANAGER.jobs.get(jid)
        if not j:
            self.fail(404, "no such job")
        return j

    def send_file(self, path, ctype=None):
        size = os.path.getsize(path)
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
        if m and size:
            if m.group(1):
                start = int(m.group(1))
                end = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    buf = f.read(min(CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)
            except (BrokenPipeError, ConnectionResetError):
                pass

    # ---- routes
    def do_GET(self):
        if not self.trusted():
            return self.fail(403, "forbidden")
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            p = os.path.realpath(os.path.join(STATIC, path[len("/static/"):]))
            if p.startswith(STATIC + os.sep) and os.path.isfile(p):
                return self.send_file(p)
            return self.fail(404, "not found")
        if path == "/font/DMSans.ttf":
            return self.send_file(FONT, "font/ttf")
        if path == "/api/check":
            return self.send_json(check())
        if path == "/api/jobs":
            items = sorted(MANAGER.jobs.values(), key=lambda j: j.data["created"], reverse=True)
            return self.send_json([{"id": j.id, "title": j.data["settings"]["title"], "state": j.data["state"],
                                    "created": j.data["created"], "outputs": len(j.data["outputs"])} for j in items[:50]])
        m = re.match(r"^/api/jobs/([\w.-]+)$", path)
        if m:
            j = self.job(m.group(1))
            return j and self.send_json(j.view())
        m = re.match(r"^/media/([\w.-]+)/(.+)$", path)
        if m:
            j = self.job(m.group(1))
            if not j:
                return
            p = os.path.realpath(os.path.join(j.dir, m.group(2)))
            allowed = [os.path.realpath(os.path.join(j.dir, d)) + os.sep for d in ("out", "sheets")]
            if any(p.startswith(a) for a in allowed) and os.path.isfile(p):
                return self.send_file(p)
            return self.fail(404, "not found")
        self.fail(404, "not found")

    def do_POST(self):
        if not self.trusted():
            return self.fail(403, "forbidden")
        path = urlparse(self.path).path
        try:
            data = self.body_json()
        except ValueError as e:
            return self.fail(400, str(e))
        try:
            if path == "/api/jobs":
                j = MANAGER.create(jobs.validate(data))
                return self.send_json({"id": j.id})
            if path == "/api/pick":
                return self.send_json({"path": pick(data.get("kind", "folder"))})
            if path == "/api/reveal":
                j = MANAGER.jobs.get(data.get("id", ""))
                target = data.get("path", "")
                ok = j and any(o.get("delivered") == target or os.path.join(j.dir, o["file"]) == target for o in j.data["outputs"])
                if not ok or not os.path.exists(target):
                    return self.fail(404, "not a file of this job")
                subprocess.run(["open", "-R", target])
                return self.send_json({"ok": True})
            m = re.match(r"^/api/jobs/([\w.-]+)/(start|cancel|revise)$", path)
            if m:
                j = self.job(m.group(1))
                if not j:
                    return
                act = m.group(2)
                if act == "start":
                    MANAGER.start(j)
                elif act == "cancel":
                    j.cancel()
                    if j.data["state"] == "queued":
                        j.update(state="cancelled")
                else:
                    note = str(data.get("notes", "")).strip()[:2000]
                    if not note:
                        raise ValueError("write what to change")
                    if j.data["state"] in ("queued", "running"):
                        raise ValueError("wait for the current run to finish")
                    j.data.setdefault("revisions", []).append(note)
                    if data.get("editor") in ("claude", "auto"):
                        j.data["settings"]["editor"] = data["editor"]
                    j.save()
                    MANAGER.start(j, from_edit=True)
                return self.send_json({"ok": True})
        except ValueError as e:
            return self.fail(400, str(e))
        self.fail(404, "not found")

    def do_PUT(self):
        if not self.trusted():
            return self.fail(403, "forbidden")
        m = re.match(r"^/api/jobs/([\w.-]+)/upload/(raw|music|brand)/([^/]+)$", urlparse(self.path).path)
        if not m:
            return self.fail(404, "not found")
        j = self.job(m.group(1))
        if not j:
            return
        if j.data["state"] != "draft":
            return self.fail(409, "uploads are closed once the job started")
        name = jobs.safe_name(unquote(m.group(3)))
        kind = m.group(2)
        exts = {"raw": jobs.VIDEO_EXT, "music": jobs.AUDIO_EXT, "brand": jobs.LOGO_EXT}[kind]
        if not name.lower().endswith(exts):
            return self.fail(400, "%s is not a %s file" % (name, {"raw": "video", "music": "audio", "brand": "PNG, JPG or WebP"}[kind]))
        left = int(self.headers.get("Content-Length") or 0)
        if kind == "brand" and left > LOGO_MAX:
            return self.fail(400, "the logo is larger than %d MB" % (LOGO_MAX >> 20))
        os.makedirs(os.path.join(j.dir, kind), exist_ok=True)
        dest = os.path.join(j.dir, kind, name)
        part = dest + ".part"
        with open(part, "wb") as f:
            while left > 0:
                buf = self.rfile.read(min(CHUNK, left))
                if not buf:
                    break
                f.write(buf)
                left -= len(buf)
        if left:
            os.remove(part)
            return self.fail(400, "upload interrupted")
        if kind == "brand" and not jobs.is_image(part):         # it goes into Chrome as an <img>: a real image only
            os.remove(part)
            return self.fail(400, "%s is not a PNG, JPG or WebP image" % name)
        if kind in ("music", "brand"):                        # one track / one logo per job: the newest upload wins
            for old in os.listdir(os.path.join(j.dir, kind)):
                if old != name + ".part":
                    os.remove(os.path.join(j.dir, kind, old))
        os.replace(part, dest)
        self.send_json({"ok": True, "name": name, "size": os.path.getsize(dest)})


def main():
    global MANAGER, PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    PORT = a.port
    MANAGER = jobs.Manager()
    httpd = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = "http://127.0.0.1:%d/" % a.port
    print("Drone Studio on %s  (jobs in %s; Ctrl+C to stop)" % (url, jobs.JOBS_DIR), flush=True)
    if not a.no_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
