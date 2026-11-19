"""Start PaperFinder AI locally: ``python -m app`` (FR-94/95).

Binds to loopback only unless PAPERFINDER_ALLOW_LAN=true (NFR-12, R-20).
Stop safely with Ctrl+C – in-flight downloads are cancelled and temp files removed.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
import webbrowser

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    ap = argparse.ArgumentParser(prog="paperfinder", description="Run the PaperFinder AI local server.")
    ap.add_argument("--port", type=int, default=settings.port)
    ap.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically.")
    args = ap.parse_args()
    settings.port = args.port

    host = settings.host
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback and not settings.allow_lan:
        sys.exit(f"Refusing to bind to {host}: set PAPERFINDER_ALLOW_LAN=true to expose the app on your network.")

    from .main import create_app

    app = create_app(settings)
    url = f"http://127.0.0.1:{settings.port}/"
    print(f"PaperFinder AI running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser and settings.frontend_dist.is_dir():
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host=host, port=settings.port, log_level="warning", proxy_headers=False,
                server_header=False)


if __name__ == "__main__":
    main()
