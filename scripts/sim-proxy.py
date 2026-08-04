#!/usr/bin/env python3
# Transparent HTTP proxy in front of a Dusk node, used by wire-contract.sh's
# --simulate-first: rusk-wallet builds and signs a transaction internally and
# POSTs it straight to the node's `preverify` endpoint. This proxy sits
# between them, on the loopback interface only, and intercepts that POST:
# it first replays the identical signed bytes against the node's
# `/on/transactions/simulate` endpoint (a real dry run against live state —
# no signing logic of our own, we just reuse whatever rusk-wallet already
# built) and inspects the result.
#
#   - If simulate reports an execution error, this returns a synthetic
#     preverify-shaped rejection (HTTP 400, `{"error": "..."}` — the same
#     shape a real preverify rejection uses, confirmed against
#     rusk/src/lib/http/error.rs's ApiError) instead of forwarding to the
#     real preverify. rusk-wallet then reports "preverify failed" and
#     never calls propagate — no gas spent, no silent false success.
#   - If simulate succeeds, the real gas-spent is written to
#     --result-file and the original request is forwarded through to the
#     real preverify/propagate flow unmodified.
#
# Every other request (balance checks, chain_id, the wait-for-inclusion
# polling loop) passes straight through unmodified.
#
# Usage: sim-proxy.py <upstream-base-url> <listen-port> <result-file>

import http.server
import json
import sys
import urllib.error
import urllib.request

UPSTREAM, LISTEN_PORT, RESULT_FILE = sys.argv[1], int(sys.argv[2]), sys.argv[3]


def write_result(payload):
    with open(RESULT_FILE, "w") as f:
        json.dump(payload, f)


class Handler(http.server.BaseHTTPRequestHandler):
    def _forward(self, url, body, method):
        req = urllib.request.Request(url, data=body if body else None, method=method)
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length"):
                req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=60)

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if method == "POST" and self.path == "/on/transactions/preverify":
            try:
                with self._forward(f"{UPSTREAM}/on/transactions/simulate", body, "POST") as resp:
                    sim = json.loads(resp.read())
            except Exception as e:
                # Simulate itself failing (network blip, etc.) — don't block
                # the real call on our own plumbing breaking; fall through
                # to the real preverify and let the normal flow decide.
                write_result({"proxy_error": str(e)})
                sim = None

            if sim is not None and sim.get("error"):
                write_result(sim)
                body_bytes = json.dumps({"error": f"simulate: {sim['error']}"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                return

            if sim is not None:
                write_result(sim)

        url = f"{UPSTREAM}{self.path}"
        try:
            with self._forward(url, body, method) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                        self.send_header(k, v)
                resp_body = resp.read()
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            resp_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body)

    def do_GET(self):
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def log_message(self, fmt, *args):
        print(f"[sim-proxy] {fmt % args}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler)
    print(f"[sim-proxy] listening on 127.0.0.1:{LISTEN_PORT} -> {UPSTREAM}", file=sys.stderr, flush=True)
    server.serve_forever()
