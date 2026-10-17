#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Leo Kuroshita (@kurogedelic), Hügelton Instruments
"""oergelfm1: the browser emulator, served locally with its BAKE button working.

  python3 tools/emu_server.py [PORT]        (default 8790; then open http://localhost:8790/)

Serves build/emu (web/emu/build.sh; built first when missing) like `python3 -m http.server`, on 127.0.0.1 only, plus:
  GET  /api/ping                    the page shows its BAKE button only when this answers
  POST /api/presets {flash}         the user presets in the emulator's flash, and what is baked
  POST /api/bake {flash, bake: [slot], remove: [ENGINE/NAME]}
                                    tools/bake_presets.py, then ./build.sh and web/emu/build.sh; the page reloads the
                                    new emulator. The firmware for the FM-1 is build/felucca.fwsc
flash: the page's saved sectors as it keeps them ({offset: base64}, localStorage "felucca-emu-flash")."""
import json
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMU = ROOT / "build" / "emu"
sys.path.insert(0, str(ROOT / "tools"))
import bake_presets as bp  # noqa: E402

LOCK = threading.Lock()                       # one bake (and build) at a time


def build():
    """the firmware, then the emulator: (ok, the last lines of the log)"""
    log = []
    for cmd in (["./build.sh"], ["web/emu/build.sh"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip().splitlines()
        log += [f"$ {' '.join(cmd)}"] + [x for x in out if "Warning" not in x and "warnings.warn" not in x][-6:]
        if r.returncode:
            return False, log
    return True, log


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("emu_server: " + (fmt % args) + "\n")

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")      # a reload after a bake gets the new wasm
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/ping":
            return self.reply(200, {"ok": True, "bake": True})
        return super().do_GET()

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 8 << 20:
                return self.reply(413, {"ok": False, "error": "too large"})
            req = json.loads(self.rfile.read(n) or b"{}")
            flash = req.get("flash") or {}
            if self.path == "/api/presets":
                return self.reply(200, {"ok": True} | bp.listing(flash))
            if self.path == "/api/bake":
                with LOCK:
                    d = bp.desc()
                    notes, done = [], []
                    if req.get("remove"):
                        notes.append(f"removed {bp.remove(req['remove'], d)}")
                    if req.get("bake"):
                        done, more = bp.bake(flash, req["bake"], d)
                        notes += more
                    ok, log = build()
                    return self.reply(200 if ok else 500, {"ok": ok, "baked": done, "notes": notes, "log": log,
                                                           "list": bp.listing(flash)})
            return self.reply(404, {"ok": False, "error": "no such call"})
        except Exception as e:                                  # the page shows it
            return self.reply(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    if not (EMU / "felucca.wasm").exists():
        ok, log = build()
        print("\n".join(log))
        if not ok:
            return 1
    srv = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(EMU)))
    print(f"emu_server: http://localhost:{port}/  (BAKE: user presets -> factory presets; Ctrl-C stops)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
