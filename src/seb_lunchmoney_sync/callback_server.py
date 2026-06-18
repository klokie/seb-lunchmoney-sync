"""One-shot HTTPS listener to catch the Enable Banking BankID redirect.

Enable Banking requires an https redirect URL even for localhost, so this
serves a self-signed cert (generated on the fly with openssl into a temp dir).
The browser will warn once — proceed past it. Returns the `code` query param.
"""

from __future__ import annotations

import ssl
import subprocess
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .config import config


def _self_signed_cert(dirpath: Path) -> tuple[Path, Path]:
    key = dirpath / "cb-key.pem"
    crt = dirpath / "cb-cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(crt), "-days", "1",
         "-subj", f"/CN={config.callback_host}"],
        check=True, capture_output=True,
    )
    return key, crt


def wait_for_code(timeout: int = 300) -> str:
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            code = (params.get("code") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if code:
                captured["code"] = code
                self.wfile.write(b"<h1>Consent captured.</h1>You can close this tab.")
            else:
                self.wfile.write(b"<h1>No code in callback.</h1>")

        def log_message(self, *args):  # silence
            pass

    with tempfile.TemporaryDirectory() as td:
        key, crt = _self_signed_cert(Path(td))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(crt), keyfile=str(key))
        httpd = HTTPServer((config.callback_host, config.callback_port), Handler)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        httpd.timeout = timeout
        while "code" not in captured:
            httpd.handle_request()
    return captured["code"]
