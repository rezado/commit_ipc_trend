#!/usr/bin/env python3
"""Serve the dashboard with HTTP caching disabled.

Plain ``python3 -m http.server`` only sends ``Last-Modified``.  Without a
``Cache-Control`` directive the browser falls back to heuristic freshness
(roughly 10% of the file age) and can keep rendering a stale ``index.html`` /
``app.js`` without ever revalidating against the server, which looks exactly
like "the page does not refresh" even though the files on disk are current.

This wrapper serves the same directory layout but marks every response
``no-store`` so a reload always picks up the files that are on disk.

    python3 tools/serve_dashboard.py --port 8000
    # open http://localhost:8000/xiangshan-performance-dashboard/
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:  # noqa: D102 - mirrors the stdlib signature
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    parser.add_argument("--bind", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--directory", default=str(ROOT), help="directory to serve (default: repository root)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handler = partial(NoCacheHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"serving {args.directory} on http://{args.bind}:{args.port}/ (cache disabled)")
    print(f"open http://localhost:{args.port}/xiangshan-performance-dashboard/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
