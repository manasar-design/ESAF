# api_sniffer.py
"""
mitmproxy add-on: capture every API call the mobile app makes during a flow
(login -> center -> collection ...) and print request/response bodies decrypted.

Run:
    mitmdump -s api_sniffer.py --listen-port 8080
    mitmdump -s api_sniffer.py --set hosts=guat-api.esafbank.com   # filter host

Then point the phone/emulator Wi-Fi proxy at <this-PC-IP>:8080 and install the
mitmproxy CA from http://mitm.it on the device.

Each call is also appended to results/api_flow_<timestamp>.json.
"""
import json
import os
import sys
import time
from datetime import datetime

from mitmproxy import ctx, http

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import decode_data

DEFAULT_HOSTS = "esafbank.com,esthenos.com"
RESULTS_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")


def _decrypt_body(raw: str):
    """Return the body as decrypted JSON when possible, else as-is."""
    if not raw or not raw.strip():
        return None

    candidate = raw.strip()

    # Body may be {"encryptedData": "..."} or the plain encrypted string
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict) and "encryptedData" in parsed:
            candidate = parsed["encryptedData"]
        else:
            return parsed
    except json.JSONDecodeError:
        pass

    try:
        decrypted = decode_data(candidate)
        try:
            return json.loads(decrypted)
        except json.JSONDecodeError:
            return decrypted
    except Exception:
        return candidate


class ApiSniffer:

    def __init__(self):
        self.count    = 0
        self.log_file = None
        self.flow_log = []

    def load(self, loader):
        loader.add_option(
            name="hosts", typespec=str, default=DEFAULT_HOSTS,
            help="Comma-separated host substrings to capture",
        )

    def running(self):
        os.makedirs(RESULTS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(RESULTS_DIR, f"api_flow_{stamp}.json")
        ctx.log.info(f"📁 Logging API flow to {self.log_file}")

    def _wanted(self, flow: http.HTTPFlow) -> bool:
        hosts = [h.strip() for h in ctx.options.hosts.split(",") if h.strip()]
        return any(h in flow.request.pretty_host for h in hosts)

    def request(self, flow: http.HTTPFlow):
        if self._wanted(flow):
            flow.metadata["start"] = time.time()

    def response(self, flow: http.HTTPFlow):
        if not self._wanted(flow):
            return

        self.count += 1
        duration = round(time.time() - flow.metadata.get("start", time.time()), 2)
        req, res = flow.request, flow.response

        req_body = _decrypt_body(req.get_text(strict=False))
        res_body = _decrypt_body(res.get_text(strict=False))

        print(f"\n{'=' * 60}")
        print(f"  #{self.count}  {req.method} {req.path}")
        print(f"  Host: {req.pretty_host}   Status: {res.status_code}   Time: {duration}s")
        print(f"{'=' * 60}")
        print("  Headers:")
        for k in ("channel", "device-type", "app-version", "authorization", "token"):
            if k in req.headers:
                print(f"    {k}: {req.headers[k]}")
        if req_body is not None:
            print("  📤 Request:")
            print(json.dumps(req_body, indent=4) if not isinstance(req_body, str) else f"    {req_body}")
        if res_body is not None:
            print("  📥 Response:")
            print(json.dumps(res_body, indent=4) if not isinstance(res_body, str) else f"    {res_body}")

        self.flow_log.append({
            "seq"      : self.count,
            "time"     : datetime.now().isoformat(timespec="seconds"),
            "method"   : req.method,
            "url"      : req.pretty_url,
            "status"   : res.status_code,
            "duration" : duration,
            "headers"  : dict(req.headers),
            "request"  : req_body,
            "response" : res_body,
        })
        with open(self.log_file, "w") as f:
            json.dump(self.flow_log, f, indent=2, default=str)


addons = [ApiSniffer()]
