# flow_common.py
"""
Shared config + helpers for the per-hierarchy flow files
(flow_fo, flow_bm, flow_rcm, flow_ccm, flow_rce, flow_admin).

Each flow module exposes:
    NAME        : hierarchy label, e.g. "BM"
    USERS_FILE  : credential file name under DATA/ (one email per line)
    run(request, email, password, login_only=False) -> dict
                  does that hierarchy's login + actions on the given Playwright
                  request context, and returns {"login", "ok", "steps", "error"}.
The orchestrator (test_hierarchies.py) loads each module's users and runs them
all concurrently, each user on its own request context.
"""
import json
import os
import threading
import time

from main import encode_data, decode_data


class ThreadDict:
    """A dict whose contents are private to each thread. LAST_STATUS / LAST_MS
    are module-level in the reused test modules; under the concurrent runner a
    plain dict is shared, so users overwrite each other's step results."""

    def __init__(self):
        self._tl = threading.local()

    def _d(self) -> dict:
        if not hasattr(self._tl, "d"):
            self._tl.d = {}
        return self._tl.d

    def __setitem__(self, k, v): self._d()[k] = v
    def __getitem__(self, k): return self._d()[k]
    def __contains__(self, k): return k in self._d()
    def __iter__(self): return iter(self._d())
    def __len__(self): return len(self._d())
    def get(self, k, default=None): return self._d().get(k, default)
    def items(self): return self._d().items()
    def keys(self): return self._d().keys()
    def values(self): return self._d().values()
    def clear(self): self._d().clear()


def fmt(status, ms) -> str:
    """Step label with response time, e.g. '200 (123ms)'."""
    return f"{status} ({ms}ms)"

# ============================================================
# ENVIRONMENT — all hierarchies point here (UAT).
# ============================================================
# WEB_ROOT = "https://guat-api.esafbank.com"          # UAT
# WEB_ROOT = "https://esaf-dev-api.esthenos.com"      # DEV
# WEB_ROOT = "https://gravity-sit-api.esafbank.com"   # SIT
WEB_ROOT = "https://gravity-api.esaf.bank.in"         # PROD

WEB_API            = f"{WEB_ROOT}/web/api/v1"
WEB_LOGIN_ENDPOINT = f"{WEB_API}/organisation/user_login"

# Password applied to every hierarchy's users. UAT accounts use Esaf@123
# (confirmed live for manasafo@guat); switch to Esaf@12345 if accounts are reset.
RESET_PASSWORD = "Esaf@12345"

# Web login (like the mobile one) takes an AES-encrypted text/plain body.
WEB_HEADERS = {
    "channel": "mobile",
    "device-type": "android",
    "app-version": "2.0.6-SIT",
    "Content-Type": "text/plain",
    "User-Agent": "GravityBank-Android/1.0",
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA"))


# ============================================================
# HELPERS
# ============================================================
def load_users(filename: str, limit: int = 0) -> list:
    """Read DATA/<filename> as rows of {column: value}. The first column is the
    identity (usually 'email', or 'web_email' for the retail file). Rows whose
    first column is blank or starts with '#' are skipped."""
    import csv
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        key0 = reader.fieldnames[0] if reader.fieldnames else "email"
        for raw in reader:
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
            first = row.get(key0, "")
            if first and not first.startswith("#"):
                rows.append(row)
    return rows[:limit] if limit else rows


def user_label(user: dict) -> str:
    """Primary identity shown in output."""
    return user.get("email") or user.get("web_email") or next(iter(user.values()), "")


def decode(text: str):
    """Decode an AES body to a dict, falling back to plain JSON, else None."""
    if not text or not text.strip():
        return None
    try:
        return json.loads(decode_data(text))
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            return None


def first_list(body):
    """First list found at top level or under a common container key."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "result", "applications", "customers", "list", "results"):
            v = body.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                nested = first_list(v)
                if nested is not None:
                    return nested
    return None


def web_login(request, email: str, password: str):
    """POST /web/api/v1/organisation/user_login (AES body).
    Returns (http_status, auth_headers | None, ms). The web user_login returns the
    token in 'message'; fall back to token / data.token."""
    payload = {"email": email, "password": password,
               "verify_two_factor_otp": True, "otp": "123456"}
    start = time.time()
    res = request.post(WEB_LOGIN_ENDPOINT, headers=WEB_HEADERS,
                       data=encode_data(json.dumps(payload)))
    ms = round((time.time() - start) * 1000)
    body = decode(res.text()) or {}

    token = None
    if isinstance(body, dict):
        msg = body.get("message")
        token = (msg if isinstance(msg, str) and len(msg) > 20 else None) \
            or body.get("token") or (body.get("data") or {}).get("token")

    if res.status != 200 or not token or len(str(token)) < 5:
        return res.status, None, ms
    return res.status, {**WEB_HEADERS, "instance-token": token}, ms


def web_get(request, url: str, headers: dict):
    """GET any API (web or mobile); return (http_status, parsed_body, ms)."""
    start = time.time()
    res = request.get(url, headers=headers)
    ms = round((time.time() - start) * 1000)
    return res.status, decode(res.text()), ms


def api_post(request, url: str, headers: dict, payload: dict):
    """POST an AES-encrypted body; return (http_status, parsed_body, ms)."""
    start = time.time()
    res = request.post(url, headers=headers, data=encode_data(json.dumps(payload)))
    ms = round((time.time() - start) * 1000)
    return res.status, decode(res.text()), ms


def web_put(request, url: str, headers: dict, payload: dict):
    """PUT an AES-encrypted body; return (http_status, parsed_body, ms)."""
    start = time.time()
    res = request.put(url, headers=headers, data=encode_data(json.dumps(payload)))
    ms = round((time.time() - start) * 1000)
    return res.status, decode(res.text()), ms
