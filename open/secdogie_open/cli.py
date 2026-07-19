from __future__ import annotations

import argparse
import sys
import webbrowser

from .controller import Controller
from .server import build_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secdogie-open",
        description="Split the screen by open window and drive one secdogie-agent instance per "
        "selected window at once. Opens a local page in your browser -- nothing is sent over "
        "the network, the server only ever binds 127.0.0.1.",
    )
    parser.add_argument("--port", type=int, default=0, help="port to bind (default: pick a free one)")
    parser.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser tab")
    args = parser.parse_args(argv)

    # No upfront display check here: the server itself needs no display to
    # start, only window enumeration does -- and that already degrades to a
    # clear banner in the page (via windows.NoWindowBackendError) rather than
    # a startup failure, so a --no-browser run over SSH with a forwarded port
    # still works if the *remote* box has a display.
    controller = Controller()
    server = build_server(controller, port=args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"

    print(f"secdogie-open: serving {url}")
    if args.no_browser:
        print("Open that URL in your browser. Press Ctrl+C here to stop.")
    else:
        webbrowser.open(url)
        print("Opened in your browser. Press Ctrl+C here to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
