# api_console.py
"""
Local mobile-API console with a live visual of every call.

    ../venv/bin/python api_console.py          # then open http://localhost:8765

- Login to the mobile API (/api/v1/token/sourcing, AES-encrypted like the tests)
- Fire any GET/POST against the logged-in session (bodies encrypted/decrypted)
- Every call shows up in the browser as a flow timeline with decrypted
  request/response, and is saved to results/api_console_<timestamp>.json
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import encode_data, decode_data

# ============================================================
# CONFIGURATION
# ============================================================
ENVIRONMENTS = {
    "UAT": "https://guat-api.esafbank.com",
    "SIT": "https://gravity-sit-api.esafbank.com",
    "DEV": "https://esaf-dev-api.esthenos.com",
}
DEFAULT_ENV = "UAT"
LOGIN_PATH  = "/api/v1/token/sourcing"

MOBILE_HEADERS = {
    "channel": "mobile",
    "device-type": "android",
    "app-version": "2.0.6-SIT",
    "Content-Type": "text/plain",
    "X-fos-APKVERSION": "1.0.7-DEBUG",
    "X-fos-FB-token": "fdcAHNg8Qy6YioQ1u-1IFX:APA91bHzWhQYE53zA-fXMEB0ydF2U9cNsh",
    "LATITUDE": "12.9646815",
    "LONGITUDE": "77.6439036",
    "device-mac-id": "f2166c84024b7977",
    "User-Agent": "okhttp/4.12.0",
}

PORT        = 8765
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
HTML_FILE   = os.path.join(BASE_DIR, "api_console.html")
RESULTS_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "results"))
LOG_FILE    = os.path.join(RESULTS_DIR, f"api_console_{datetime.now():%Y%m%d_%H%M%S}.json")
TIMEOUT     = 60

# ============================================================
# SESSION STATE
# ============================================================
session = {"env": DEFAULT_ENV, "user": None, "token": None}
calls   = []


def decode_body(text: str):
    """Decrypt an API body if possible; fall back to plain JSON or raw text."""
    if not text or not text.strip():
        return None
    raw = text.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "encryptedData" in parsed:
            raw = parsed["encryptedData"]
        else:
            return parsed
    except json.JSONDecodeError:
        pass
    try:
        decrypted = decode_data(raw)
        try:
            return json.loads(decrypted)
        except json.JSONDecodeError:
            return decrypted
    except Exception:
        return text


def auth_headers() -> dict:
    headers = dict(MOBILE_HEADERS)
    if session["token"]:
        headers["instance-token"] = session["token"]
    return headers


def perform_call(name: str, method: str, path: str, payload=None, encrypt=True,
                 headers=None, extra_headers=None) -> dict:
    url = path if path.startswith("http") else ENVIRONMENTS[session["env"]] + path
    headers = dict(headers or auth_headers())
    headers.update(extra_headers or {})

    data = None
    if payload is not None and method != "GET":
        if encrypt:
            data = encode_data(json.dumps(payload)).encode()
            headers["Content-Type"] = "text/plain"
        else:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.time()
    status, text, error = None, "", None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as res:
            status, text = res.status, res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        error = str(e)
    duration_ms = round((time.time() - start) * 1000)

    record = {
        "seq"        : len(calls) + 1,
        "name"       : name,
        "time"       : datetime.now().isoformat(timespec="seconds"),
        "started_at" : start,
        "env"        : session["env"],
        "user"       : session["user"],
        "method"     : method,
        "url"        : url,
        "status"     : status,
        "duration_ms": duration_ms,
        "error"      : error,
        "encrypted"  : encrypt,
        "headers"    : {k: v for k, v in headers.items() if k != "instance-token"},
        "request"    : payload,
        "response"   : decode_body(text),
    }
    calls.append(record)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(calls, f, indent=2, default=str)

    mark = "✅" if status and status < 400 else "❌"
    print(f"{mark} #{record['seq']} {method} {url} -> {status or error} ({duration_ms} ms)")
    return record


def login(env: str, email: str, password: str, otp: str) -> dict:
    session.update(env=env, user=email, token=None)
    payload = {"email": email, "password": password, "verify_two_factor_otp": True, "otp": otp}
    record = perform_call("LOGIN", "POST", LOGIN_PATH, payload, encrypt=True, headers=MOBILE_HEADERS)

    res = record["response"] if isinstance(record["response"], dict) else {}
    token = res.get("token") or (res.get("data") or {}).get("token")
    if record["status"] == 200 and isinstance(token, str) and len(token) > 5:
        session["token"] = token
    return record


# ============================================================
# HTTP SERVER
# ============================================================
class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def _send(self, code: int, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _state(self) -> dict:
        return {
            "env": session["env"], "user": session["user"],
            "logged_in": bool(session["token"]),
            "environments": ENVIRONMENTS, "log_file": LOG_FILE,
        }

    def do_GET(self):
        if self.path == "/":
            with open(HTML_FILE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(200, self._state())
        elif self.path == "/api/calls":
            self._send(200, calls)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._json()
            if self.path == "/api/login":
                record = login(body.get("env", DEFAULT_ENV), body["email"],
                               body.get("password", ""), body.get("otp", "123456"))
                self._send(200, {"record": record, "state": self._state()})
            elif self.path == "/api/call":
                if not session["token"]:
                    return self._send(400, {"error": "Login first"})
                record = perform_call(
                    body.get("name") or body["path"].split("?")[0],
                    body.get("method", "GET").upper(), body["path"],
                    body.get("payload"), bool(body.get("encrypt", True)),
                    extra_headers=body.get("headers"),
                )
                self._send(200, {"record": record})
            elif self.path == "/api/clear":
                calls.clear()
                self._send(200, {"ok": True})
            elif self.path == "/api/logout":
                session.update(user=None, token=None)
                self._send(200, {"state": self._state()})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"🚀 API console running at http://localhost:{PORT}")
    print(f"📁 Calls logged to {LOG_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Stopped.")
