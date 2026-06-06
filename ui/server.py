#!/usr/bin/env python3
"""
The Enforcer — demo UI server (stdlib only, no dependencies).

Serves the 3-panel frontend and proxies runs to the deployed GraphN workflow by
shelling out to the `graphn wf run` CLI (reuses existing auth in ~/.graphn — no
API keys in the browser, no CORS headaches). Start it, open the printed URL.
"""
import json
import os
import subprocess
import http.server
import socketserver

WF_ID = os.environ.get("ENFORCER_WF_ID", "wf_936f8cee7140")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # the enforcer/ project root


def run_enforcer(payload: dict) -> dict:
    """Invoke the GraphN workflow; retry transient network blips; return parsed report."""
    inp = json.dumps({k: v for k, v in payload.items() if v not in (None, "")})
    last_err = "unknown error"
    for _ in range(3):
        try:
            p = subprocess.run(
                ["graphn", "wf", "run", WF_ID, "--input", inp],
                capture_output=True, text=True, timeout=160,
            )
            out = (p.stdout or "").strip()
            if not out:
                last_err = (p.stderr or "empty response from gateway").strip()
                continue
            d = json.loads(out)
            if d.get("status") == "completed":
                return {"ok": True, "report": d.get("output", {}).get("result", {})}
            return {"ok": False, "error": json.dumps(d.get("error") or "workflow failed")}
        except subprocess.TimeoutExpired:
            last_err = "timeout (try again, or use --mode async)"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    return {"ok": False, "error": last_err}


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/profile":
            with open(os.path.join(ROOT, "profile.json"), "rb") as f:
                self._send(200, f.read())
        elif self.path.startswith("/api/deliverable"):
            from urllib.parse import urlparse, parse_qs
            fx = (parse_qs(urlparse(self.path).query).get("fixture_id", [""])[0])
            try:
                with open(os.path.join(HERE, "fixtures.json")) as f:
                    fixtures = json.load(f)
            except Exception:
                fixtures = {}
            d = fixtures.get(fx)
            if d:
                self._send(200, json.dumps({"kind": d.get("kind"), "raw_text": d.get("raw_text", "")}))
            else:
                self._send(404, json.dumps({"error": f"unknown fixture '{fx}'"}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path == "/api/run":
            n = int(self.headers.get("Content-Length", 0) or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:  # noqa: BLE001
                payload = {}
            self._send(200, json.dumps(run_enforcer(payload)))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *args):  # quiet
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"\n  The Enforcer demo UI  →  http://127.0.0.1:{port}\n  (workflow: {WF_ID})  Ctrl-C to stop.\n")
        httpd.serve_forever()
