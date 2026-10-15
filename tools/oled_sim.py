#!/usr/bin/env python3
"""
Serve the OLED simulator linked to the sites' config.json (the source of
truth for sensors and display settings):

    python3 tools/oled_sim.py                  # http://127.0.0.1:8765/
    python3 tools/oled_sim.py akvo01005        # opens the simulator on that site
    python3 tools/oled_sim.py --port 9000 --no-browser

Only these are served, never certificates or wifi.csv:
    /docs/OLED/*                              the simulator, its logo
    /sites.json                               site keys that have a config.json
    /sites/<key>/config_data/config.json      one site's built config

The simulator then loads a site with ?site=<key> (or its Sitio box).
Rebuild a site's config.json (config_manager.py build) to see CSV changes.
"""
import argparse
import json
import re
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SIM = "/docs/OLED/oled_simulator.html"
CONFIG_RE = re.compile(r"^/sites/([A-Za-z0-9_-]+)/config_data/config\.json$")
OLED_RE = re.compile(r"^/docs/OLED/[A-Za-z0-9_.-]+$")


def site_keys() -> list[str]:
    return sorted(p.parent.parent.name for p in (ROOT / "sites").glob("*/config_data/config.json"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            self.send_response(302)
            self.send_header("Location", SIM)
            self.end_headers()
        elif path == "/sites.json":
            body = json.dumps(site_keys()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif CONFIG_RE.match(path) or (OLED_RE.match(path) and ".." not in path):
            super().do_GET()
        else:
            self.send_error(404)

    def do_HEAD(self):
        self.send_error(405)

    def log_message(self, fmt, *args):
        pass   # quiet: one line per file request is noise here


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Serve the OLED simulator with the sites' config.json")
    ap.add_argument("site", nargs="?", help="site key to open, e.g. akvo01005")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="127.0.0.1", help="address to listen on (default: this computer only)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    if args.site and args.site not in site_keys():
        print(f"warning: sites/{args.site}/config_data/config.json not found; sites: {', '.join(site_keys())}")
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    url = f"http://{'127.0.0.1' if args.bind in ('0.0.0.0', '') else args.bind}:{args.port}{SIM}"
    if args.site:
        url += f"?site={quote(args.site)}"
    print(f"OLED simulator: {url}\nsites: {', '.join(site_keys()) or '(none built)'}\nCtrl+C to stop")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
