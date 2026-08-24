"""TLS/HTTP E2E sink recording notification arrival and MariaDB state."""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymysql

EVENTS: list[dict] = []
CERT_DIR = Path("/certs")


def _match_exists(marker: str) -> bool:
    db = pymysql.connect(host="db", user=os.environ["MARIADB_USER"],
                         password=os.environ["MARIADB_PASSWORD"],
                         database=os.environ["MARIADB_DATABASE"])
    try:
        with db.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM objekt_einsatz oe "
                "JOIN incident i ON i.id=oe.incident_id WHERE i.external_key=%s "
                "OR i.report_text LIKE %s)", (marker, f"%{marker}%"),
            )
            return bool(cur.fetchone()[0])
    finally:
        db.close()


def _certificates() -> None:
    CERT_DIR.mkdir(exist_ok=True)
    cfg = CERT_DIR / "openssl.cnf"
    cfg.write_text("[req]\ndistinguished_name=dn\n[dn]\n[v3]\nsubjectAltName=DNS:fake-sink\n")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(CERT_DIR / "key.pem"), "-out", str(CERT_DIR / "ca.pem"),
                    "-days", "1", "-subj", "/CN=fake-sink", "-extensions", "v3",
                    "-config", str(cfg)], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, value) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True})
        elif parsed.path == "/events":
            self._json(200, EVENTS)
        elif parsed.path == "/match":
            marker = parse_qs(parsed.query).get("marker", [""])[0]
            self._json(200, {"exists": _match_exists(marker)})
        elif parsed.path == "/reset":
            EVENTS.clear()
            self._json(200, {"ok": True})
        else:
            self._json(404, {})

    def do_POST(self):
        size = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(size)
        if self.path == "/oauthapi/login":
            self._json(200, {"access_token": "e2e-token", "expires_in": 3600})
            return
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        encoded = json.dumps(payload, ensure_ascii=False)
        found = re.search(r"E2E-(?:API|GW)-\d+", encoded)
        marker = found.group(0) if found else "missing-marker"
        EVENTS.append({"path": self.path, "received_ns": time.time_ns(),
                       "marker": marker, "match_exists_at_receipt": _match_exists(marker),
                       "payload": payload})
        self._json(200, {"ok": True})

    def log_message(self, *_args):
        pass


def main() -> None:
    _certificates()
    http = ThreadingHTTPServer(("0.0.0.0", 8089), Handler)
    https = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT_DIR / "ca.pem", CERT_DIR / "key.pem")
    https.socket = ctx.wrap_socket(https.socket, server_side=True)
    import threading
    threading.Thread(target=http.serve_forever, daemon=True).start()
    https.serve_forever()


if __name__ == "__main__":
    main()
