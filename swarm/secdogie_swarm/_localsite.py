"""A real local HTTPS server with a self-signed cert, for the end-to-end test
and the demo. The node fetches it over a genuine TLS handshake; the client
trusts exactly this cert (test CA), so verification is real, not disabled."""

from __future__ import annotations

import datetime
import ipaddress
import ssl
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"


def _self_signed() -> tuple[bytes, bytes]:
    # Imported lazily so this module loads even where cryptography is absent;
    # only *building* a site needs it.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(HOST))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


class TlsSite:
    def __init__(self, html: bytes) -> None:
        self.html = html
        self.cert_pem, self.key_pem = _self_signed()
        self._dir = tempfile.TemporaryDirectory()
        self._certfile = Path(self._dir.name) / "cert.pem"
        self._keyfile = Path(self._dir.name) / "key.pem"
        self._certfile.write_bytes(self.cert_pem)
        self._keyfile.write_bytes(self.key_pem)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def __enter__(self) -> TlsSite:
        html = self.html

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *a: object) -> None:  # quiet
                pass

        self._server = ThreadingHTTPServer((HOST, 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(self._certfile), str(self._keyfile))
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._dir.cleanup()

    @property
    def origin(self) -> str:
        return f"https://{HOST}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/"

    def client_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cadata=self.cert_pem.decode("ascii"))
