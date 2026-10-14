# load_test.py
"""
Concurrent load test for the collection flow (preproduction).

Per user, in order, the same calls the app makes:
    1. POST /api/v1/token/sourcing            (login, AES-encrypted body)
    2. GET  /api/v1/collection/dashboard/center
    3. GET  /api/v1/collection/menu
    4. GET  /api/v1/collections/centers?collection_date=<date>&loan_type=center
    5. GET  /api/v2/collections?...&center_id=<first center>   (only if a center is returned)

Read-only: it never calls make-payments. For each user it records the status
code and milliseconds of every call; at the end it prints only the total-run
summary (latency percentiles and status-code tallies). Nothing is saved to disk.

    ../venv/bin/python load_test.py --concurrency 20 --limit 200
    ../venv/bin/python load_test.py --concurrency 50            # all users in the file
"""
import argparse
import csv
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import encode_data, decode_data
import test_collection as tc

# Reuse the exact server, endpoints, headers and date from test_collection.py
BASE_URL     = tc.BASE_URL
LOGIN_URL    = tc.LOGIN_ENDPOINT
DASHBOARD    = tc.COLLECTION_DASHBOARD_ENDPOINT
MENU         = tc.COLLECTION_MENU_ENDPOINT
CENTERS      = tc.COLLECTION_CENTERS_ENDPOINT
COLLECTIONS  = tc.COLLECTIONS_ENDPOINT
PASSWORD     = tc.DEFAULT_PASSWORD
RESULTS_DIR  = tc.RESULTS_DIR

# The edge (Akamai) 403s a generic client; the app's own User-Agent gets through.
BASE_HEADERS = {**tc.LOGIN_HEADERS, "User-Agent": "GravityBank-Android/1.0"}
SSL_CTX      = ssl.create_default_context()

STEPS = ["login", "dashboard", "menu", "centers", "collections"]


def _request(method: str, url: str, headers: dict, body: bytes = None, timeout: float = 60):
    """Fire one request; return (status, text, ms, error)."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as res:
            text = res.read().decode("utf-8", "replace")
            return res.status, text, round((time.time() - start) * 1000), None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), round((time.time() - start) * 1000), None
    except Exception as e:
        return None, "", round((time.time() - start) * 1000), str(e)[:120]


def _decode(text: str):
    """Decode an AES body to a dict, falling back to plain JSON."""
    if not text or not text.strip():
        return None
    try:
        return json.loads(decode_data(text))
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            return None


def _login_summary(status, text: str, err: str) -> str:
    """One-line, loggable summary of a login response body."""
    if err:
        return err
    if not text or not text.strip():
        return "(empty body)"
    d = _decode(text)
    if isinstance(d, dict):
        msg = d.get("message", "")
        tok = d.get("token") or (d.get("data") or {}).get("token")
        return f"{msg} | token={'yes' if tok else 'no'}".strip(" |")
    # non-JSON (e.g. Akamai HTML denial page) — collapse whitespace
    return " ".join(text.split())[:120]


def run_user(username: str, date: str, timeout: float) -> dict:
    """Run the full collection sequence for one user; capture status + ms per call."""
    row = {"username": username}
    for s in STEPS:
        row[s], row[f"{s}_ms"] = "", ""
    row["center_id"], row["centers_n"], row["customers_total"], row["error"] = "", "", "", ""
    row["login_resp"] = ""
    row["_detail"] = []

    # 1. Login (encrypted body)
    payload = {"email": username, "password": PASSWORD, "verify_two_factor_otp": True, "otp": "123456"}
    body = encode_data(json.dumps(payload)).encode()
    headers = {**BASE_HEADERS, "Content-Type": "text/plain"}
    status, text, ms, err = _request("POST", LOGIN_URL, headers, body, timeout)
    row["login"], row["login_ms"] = status if status else f"ERR", ms
    row["login_resp"] = _login_summary(status, text, err)
    if err or status != 200:
        row["error"] = err or f"login {status}"
        return row

    data = _decode(text) or {}
    token = data.get("token") or (data.get("data") or {}).get("token")
    if not token or len(str(token)) < 5:
        row["error"] = "no token"
        return row

    auth = {**BASE_HEADERS, "Content-Type": "text/plain", "instance-token": token}

    # 2-3. Dashboard, menu
    for name, url in (("dashboard", DASHBOARD), ("menu", MENU)):
        status, _, ms, err = _request("GET", url, auth, None, timeout)
        row[name], row[f"{name}_ms"] = status if status else "ERR", ms
        if err and not row["error"]:
            row["error"] = f"{name}: {err}"

    # 4. Centers
    curl = f"{CENTERS}?collection_date={date}&loan_type={tc.LOAN_TYPE}"
    status, text, ms, err = _request("GET", curl, auth, None, timeout)
    row["centers"], row["centers_ms"] = status if status else "ERR", ms
    centers = tc.find_list(_decode(text) or {}) or [] if status == 200 else []
    row["centers_n"] = len(centers)

    # 5. Collections per center: count the customers in each
    detail, total_customers, coll_ms, coll_codes = [], 0, 0, set()
    for c in centers:
        cid = c.get("center_id") or c.get("center_code") or ""
        name = c.get("center_name") or cid
        if not cid:
            continue
        vurl = f"{COLLECTIONS}?collection_date={date}&loan_type={tc.LOAN_TYPE}&center_id={cid}"
        status, text, ms, err = _request("GET", vurl, auth, None, timeout)
        coll_ms += ms
        coll_codes.add(status if status else "ERR")
        n = len(tc.find_list(_decode(text) or {}) or []) if status == 200 else 0
        total_customers += n
        detail.append((name, cid, n))
        if err and not row["error"]:
            row["error"] = f"collections {cid}: {err}"
    if centers:
        row["center_id"] = detail[0][1] if detail else ""
        row["collections"] = 200 if coll_codes == {200} else (sorted(str(x) for x in coll_codes)[0] if coll_codes else "")
        row["collections_ms"] = coll_ms
    row["customers_total"] = total_customers
    row["_detail"] = detail
    return row


def load_users(path: str, offset: int, limit: int) -> list:
    with open(path, encoding="utf-8", newline="") as f:
        rows = [r.get("email", "").strip() for r in csv.DictReader(f)]
    users = [u for u in rows if u]
    users = users[offset:]
    return users[:limit] if limit else users


def pct(values: list, p: float):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def main():
    ap = argparse.ArgumentParser(description="Concurrent collection-flow load test")
    ap.add_argument("--concurrency", type=int, default=20, help="parallel users in flight (default 20)")
    ap.add_argument("--limit", type=int, default=0, help="max users (0 = all in the file)")
    ap.add_argument("--offset", type=int, default=0, help="skip this many users from the top")
    ap.add_argument("--users", default=tc.USERS_FILE, help="users file (email column)")
    ap.add_argument("--date", default=tc.COLLECTION_DATE, help="collection_date (default today)")
    ap.add_argument("--timeout", type=float, default=60, help="per-request timeout seconds")
    args = ap.parse_args()

    users = load_users(args.users, args.offset, args.limit)
    if not users:
        raise SystemExit(f"No users in {args.users}")

    print(f"🌐 {BASE_URL}")
    print(f"👥 {len(users)} users · concurrency {args.concurrency} · date {args.date}\n")

    rows = []
    done = 0
    t0 = time.time()
    if True:  # per-user CSV output disabled — only the total-run summary is shown
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(run_user, u, args.date, args.timeout): u for u in users}
            for fut in as_completed(futures):
                row = fut.result()
                rows.append(row)
                done += 1
                # Per-user lines are not printed — only the total-run summary is shown at the end.

    wall = time.time() - t0
    print(f"\n{'=' * 60}\n📊 SUMMARY — {len(rows)} users in {wall:.1f}s "
          f"({len(rows) / max(wall, 0.001):.1f} users/s, concurrency {args.concurrency})\n{'=' * 60}")
    for s in STEPS:
        lat = [r[f"{s}_ms"] for r in rows if isinstance(r[f"{s}_ms"], int) and r[s] != ""]
        codes = {}
        for r in rows:
            if r[s] != "":
                codes[r[s]] = codes.get(r[s], 0) + 1
        if not lat and not codes:
            continue
        tally = " ".join(f"{k}:{v}" for k, v in sorted(codes.items(), key=lambda x: str(x[0])))
        print(f"  {s:<12} n={len(lat):<5} "
              f"p50={pct(lat, 50):>5}ms  p95={pct(lat, 95):>6}ms  max={max(lat) if lat else 0:>6}ms   [{tally}]")
    ok = sum(1 for r in rows if r["login"] == 200 and not r["error"])
    with_centers = sum(1 for r in rows if isinstance(r["centers_n"], int) and r["centers_n"] > 0)
    print(f"\n  ✅ {ok}/{len(rows)} fully OK · {with_centers} had centers · errors: {len(rows) - ok}")


if __name__ == "__main__":
    main()
