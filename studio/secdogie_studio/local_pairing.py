"""Add a device on your own network, with nothing to set up and nothing to type.

The hard part of pairing used to be two addresses: a signaling gateway (``wss://…``) and a page for the
phone (``https://…/pair.html``). Both meant the owner had to deploy something first. On one local network
neither is needed: this machine is already reachable by the others, so Studio runs both itself while an
invitation is open —

  * a **signaling gateway** (:mod:`secdogie_transport.signal_server`), the same one that would run on the
    owner's own Cloudflare, bound to this machine's address on the network;
  * the **pairing page** (the files in ``pairpage/``, the same ones the browser build deploys), served over
    plain HTTP from this machine.

So the invitation it writes carries ``ws://<this-machine>:<port>/ws`` and ``http://<this-machine>:<port>/
pair.html``. Another computer pastes the invitation; a phone on the same Wi‑Fi scans the QR, opens the page
and connects. Both still compare the six digits and wait for the owner to admit — this only removes the
addresses, never a check. Nothing is served except while an invitation is open, and only on the local
network; a device across the internet still needs the owner's own gateway (the advanced option).
"""
from __future__ import annotations

import asyncio
import socket
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE_DIR = Path(__file__).resolve().parent / "pairpage"


def lan_host() -> str:
    """This machine's address on the local network, so the other devices can reach it. ``127.0.0.1`` when
    there is no network (the invitation then works only for another app on this same machine)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet leaves; the kernel just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _Quiet(SimpleHTTPRequestHandler):
    """Serve the pairing page and nothing else; say nothing to the console."""

    def log_message(self, *a):  # noqa: D102 - silence
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


class LocalPairing:
    """The signaling gateway and the pairing page, on this machine's network, for as long as it is needed."""

    def __init__(self, loop: asyncio.AbstractEventLoop, *, host: str | None = None):
        self._loop = loop
        self._host = host
        self._sig = None
        self._http: ThreadingHTTPServer | None = None
        self.signal_url = ""
        self.page_url = ""

    @property
    def running(self) -> bool:
        return self._http is not None

    def start(self) -> LocalPairing:
        from secdogie_transport.signal_server import serve_signaling

        host = self._host or lan_host()
        self._sig = asyncio.run_coroutine_threadsafe(serve_signaling(host, 0), self._loop).result(10)
        self.signal_url = self._sig.url
        handler = partial(_Quiet, directory=str(PAGE_DIR))
        self._http = ThreadingHTTPServer((host, 0), handler)
        threading.Thread(target=self._http.serve_forever, daemon=True, name="studio-pairpage").start()
        self.page_url = f"http://{host}:{self._http.server_address[1]}/pair.html"
        return self

    def stop(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            http.shutdown()
            http.server_close()
        sig, self._sig = self._sig, None
        if sig is not None:
            asyncio.run_coroutine_threadsafe(sig.close(), self._loop)
        self.signal_url = self.page_url = ""


__all__ = ["LocalPairing", "PAGE_DIR", "lan_host"]
