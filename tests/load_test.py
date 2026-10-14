# load_test.py
"""
Concurrent load test for the collection flow (preproduction).

Per user, in order, the same calls the app makes:
    1. POST /api/v1/token/sourcing            (login, AES-encrypted body)
    2. GET  /api/v1/collection/dashboard/center
    3. GET  /api/v1/collection/menu
    4. GET  /api/v1/collections/centers?collection_date=<date>&loan_type=center
    5. GET  /api/v2/collections?...&center_id=<each center>   (only if a center is returned)

For each user it shows the total centers and customers, and for every center
its customer count and total demand (sum of total_demand over that center's
v2/collections rows).

With --pay (off by default) it also, PER CENTER, least demand first and
skipping centers with 0 demand / nothing pending:
    6. POST /api/v1/make-payments     one group_payments entry for that center,
                                      every due loan marked collected in CASH
    7. GET  /api/v1/cash_remittance?remittance_id=<id>   status of that remittance
Loans already paid today (results/paid_loans_<date>.json) are never paid again.
--max-payments N caps the number of center payments in the whole run. It records
the status code and milliseconds of every call; at the end it prints latency
percentiles and status-code tallies. (Per-user results are not saved to disk.)

    ../venv/bin/python load_test.py --concurrency 20 --limit 200
    ../venv/bin/python load_test.py --concurrency 50            # all users in the file
    ../venv/bin/python load_test.py --limit 1 --pay             # REAL cash payments, every center
    ../venv/bin/python load_test.py --limit 1 --pay --max-payments 1   # only one center
"""
import argparse
import csv
import json
import os
import http.client
import resource
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
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
PAYMENTS     = f"{BASE_URL}/api/v1/make-payments"
REMITTANCE   = f"{BASE_URL}/api/v1/cash_remittance"
PASSWORD     = tc.DEFAULT_PASSWORD
RESULTS_DIR  = tc.RESULTS_DIR

# The edge (Akamai) 403s a generic client; the app's own User-Agent gets through.
BASE_HEADERS = {**tc.LOGIN_HEADERS, "User-Agent": "GravityBank-Android/1.0"}
SSL_CTX      = ssl.create_default_context()

STEPS = ["login", "dashboard", "menu", "centers", "collections", "payments", "remittance"]

# --pay settings (set from the CLI in main)
PAY = False
MAX_PAYMENTS = 0            # 0 = no cap
_PAY_LOCK = threading.Lock()
_PAYMENTS_MADE = 0
REMITTANCE_ATTEMPTS, REMITTANCE_WAIT_SEC = 3, 2


def _reserve_payment_slot() -> bool:
    """Take one slot from the --max-payments budget (thread-safe)."""
    global _PAYMENTS_MADE
    with _PAY_LOCK:
        if MAX_PAYMENTS and _PAYMENTS_MADE >= MAX_PAYMENTS:
            return False
        _PAYMENTS_MADE += 1
        return True


def collectible_dues(rows: list) -> list:
    """Same filter as tc.get_collections: only loans with a collection_object_id and a
    non-zero demand, each application_id once (the API sometimes repeats a row)."""
    dues, seen = [], set()
    for c in rows:
        app_id = c.get("application_id")
        if not c.get("collection_object_id") or (c.get("total_demand") or 0) <= 0 or app_id in seen:
            continue
        seen.add(app_id)
        dues.append(c)
    return dues


# ------------------------------------------------------------
# Client-side limits. Errno 16 "Device or resource busy" / Errno 24 "Too many open
# files" / Errno 101 come from THIS machine, not the backend: a fresh DNS lookup
# (systemd-resolved at 127.0.0.53) + TCP + TLS handshake for every call floods the
# local resolver and file-descriptor table. So: resolve the host once, keep one
# connection per user (like one phone), and lift the open-files soft limit.
# ------------------------------------------------------------
_DNS_CACHE, _DNS_LOCK = {}, threading.Lock()
_real_getaddrinfo = socket.getaddrinfo


def _cached_getaddrinfo(host, port, *args, **kwargs):
    key = (host, port, args, tuple(sorted(kwargs.items())))
    with _DNS_LOCK:
        if key in _DNS_CACHE:
            return _DNS_CACHE[key]
    result = _real_getaddrinfo(host, port, *args, **kwargs)
    with _DNS_LOCK:
        _DNS_CACHE[key] = result
    return result


socket.getaddrinfo = _cached_getaddrinfo


def _raise_open_files_limit():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < hard:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
        except (ValueError, OSError):
            pass
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


# Errors raised on this machine before the request reached the server.
CLIENT_ERRNOS = {16, 24, 99, 101, 104, 110, 111, 113}


class Session:
    """One keep-alive HTTPS connection per user, reconnecting once if the server closed it."""

    def __init__(self, timeout: float):
        self.timeout, self.conn, self.host = timeout, None, None

    def _connect(self, host):
        self.close()
        self.host = host
        self.conn = http.client.HTTPSConnection(host, timeout=self.timeout, context=SSL_CTX)

    def request(self, method: str, url: str, headers: dict, body: bytes = None):
        u = urllib.parse.urlsplit(url)
        path = u.path + (f"?{u.query}" if u.query else "")
        if self.conn is None or self.host != u.netloc:
            self._connect(u.netloc)
        for attempt in (1, 2):
            try:
                self.conn.request(method, path, body=body, headers=headers)
                res = self.conn.getresponse()
                return res.status, res.read().decode("utf-8", "replace")
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError,
                    http.client.CannotSendRequest, http.client.ResponseNotReady):
                if attempt == 2:
                    raise
                self._connect(u.netloc)  # stale keep-alive socket: reconnect and resend once

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def _request(method: str, url: str, headers: dict, body: bytes = None, timeout: float = 60,
             session: Session = None):
    """Fire one request; return (status, text, ms, error)."""
    start = time.time()
    if session is not None:
        try:
            status, text = session.request(method, url, headers, body)
            return status, text, round((time.time() - start) * 1000), None
        except Exception as e:
            session.close()
            return None, "", round((time.time() - start) * 1000), str(e)[:120]
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
    """Run the full collection sequence for one user over one keep-alive connection."""
    session = Session(timeout)
    try:
        return _run_user(session, username, date, timeout)
    finally:
        session.close()


def _run_user(session: Session, username: str, date: str, timeout: float) -> dict:
    """Capture status + ms per call for one user."""
    row = {"username": username}
    for s in STEPS:
        row[s], row[f"{s}_ms"] = "", ""
    row["center_id"], row["centers_n"], row["customers_total"], row["error"] = "", "", "", ""
    row["demand_total"] = ""
    row["paid_centers"], row["paid_amount"], row["remittance_ids"], row["_payments"] = "", "", "", []
    row["login_resp"] = ""
    row["_detail"] = []

    # 1. Login (encrypted body)
    payload = {"email": username, "password": PASSWORD, "verify_two_factor_otp": True, "otp": "123456"}
    body = encode_data(json.dumps(payload)).encode()
    headers = {**BASE_HEADERS, "Content-Type": "text/plain"}
    status, text, ms, err = _request("POST", LOGIN_URL, headers, body, timeout, session)
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
        status, _, ms, err = _request("GET", url, auth, None, timeout, session)
        row[name], row[f"{name}_ms"] = status if status else "ERR", ms
        if not row["error"] and (err or status != 200):
            row["error"] = f"{name}: {err or status}"

    # 4. Centers
    curl = f"{CENTERS}?collection_date={date}&loan_type={tc.LOAN_TYPE}"
    status, text, ms, err = _request("GET", curl, auth, None, timeout, session)
    row["centers"], row["centers_ms"] = status if status else "ERR", ms
    if not row["error"] and (err or status != 200):
        row["error"] = f"centers: {err or status}"
    centers = tc.find_list(_decode(text) or {}) or [] if status == 200 else []
    row["centers_n"] = len(centers)

    # 5. Collections per center: customers and total demand in each
    detail, total_customers, total_demand, coll_ms, coll_codes = [], 0, 0.0, 0, set()
    rows_by_center = {}
    for c in centers:
        cid = c.get("center_id") or c.get("center_code") or ""
        name = c.get("center_name") or cid
        if not cid:
            continue
        vurl = f"{COLLECTIONS}?collection_date={date}&loan_type={tc.LOAN_TYPE}&center_id={cid}"
        status, text, ms, err = _request("GET", vurl, auth, None, timeout, session)
        coll_ms += ms
        coll_codes.add(status if status else "ERR")
        coll_rows = tc.find_list(_decode(text) or {}) or [] if status == 200 else []
        rows_by_center[cid] = coll_rows
        n = len(coll_rows)
        demand = round(sum(r.get("total_demand") or 0 for r in coll_rows), 2)
        total_customers += n
        total_demand += demand
        detail.append((name, cid, n, demand, status if status else "ERR"))
        if not row["error"] and (err or status != 200):
            row["error"] = f"collections {cid}: {err or status}"
    if centers:
        row["collections"] = 200 if coll_codes == {200} else (sorted(str(x) for x in coll_codes)[0] if coll_codes else "")
        row["collections_ms"] = coll_ms
        row["center_id"] = detail[0][1] if detail else ""
    row["customers_total"] = total_customers
    row["demand_total"] = round(total_demand, 2)
    row["_detail"] = detail

    # ↓↓ PAYMENT TOGGLE: uncomment the next line to SUBMIT payments (needs --pay); comment it for a DRY RUN (GET APIs only) ↓↓
    if PAY: _pay_centers(session, auth, row, centers, rows_by_center, username, date, timeout)
    return row


def _remittance_status(session, auth, remittance_id: str, timeout: float):
    """GET cash_remittance and return (http_status, ms, entry-or-None) for `remittance_id`."""
    url = f"{REMITTANCE}?remittance_id={remittance_id}"
    total_ms = 0
    for attempt in range(1, REMITTANCE_ATTEMPTS + 1):
        status, text, ms, err = _request("GET", url, auth, None, timeout, session)
        total_ms += ms
        if status != 200:
            return status if status else "ERR", total_ms, None
        d = _decode(text) or {}
        entry = next((r for r in d.get("cash_remittances") or []
                      if r.get("remittance_id") == remittance_id), None)
        if entry or attempt == REMITTANCE_ATTEMPTS:
            return status, total_ms, entry
        time.sleep(REMITTANCE_WAIT_SEC)


def _pay_centers(session, auth, row, centers, rows_by_center, username, date, timeout):
    """make-payments PER CENTER (least demand first, 0-demand centers skipped),
    then the cash_remittance status of each remittance id."""
    pays = []  # one dict per center payment
    for target in tc.pending_centers_by_demand(centers):
        cid = target.get("center_id")
        dues = tc.claim_dues(collectible_dues(rows_by_center.get(cid, [])), date)
        if not dues:
            continue
        if not _reserve_payment_slot():
            tc.record_payment(dues, username, cid, 0, None, date)  # release the claim, nothing posted
            break
        payload = tc.build_payment_payload(cid, dues)
        payload["group_payments"][0]["payment_due_date"] = date
        raw = json.dumps(payload)
        body = (encode_data(raw) if tc.ENCRYPT_PAYLOADS else raw).encode()
        status, text, ms, err = _request("POST", PAYMENTS, auth, body, timeout, session)
        resp = _decode(text)
        resp = resp if isinstance(resp, dict) else {}
        tc.save_payment_log(username, cid, payload, status, resp or None, submitted=True)
        tc.record_payment(dues, username, cid, status, resp, date)

        pay = {"center_id": cid, "center_name": target.get("center_name") or cid,
               "loans": len(dues), "amount": round(sum(c["total_demand"] for c in dues), 2),
               "status": status if status else "ERR", "ms": ms,
               "message": resp.get("message") or err or "", "remittance_id": resp.get("remittance_id") or "",
               "rem_http": "", "rem_ms": 0, "rem_status": "", "rem_amount": ""}
        ok = status in (200, 201) and resp.get("success") is not False
        if not ok and not row["error"]:
            row["error"] = f"payments {cid}: {status or err} {pay['message']}".strip()
        if ok and pay["remittance_id"]:
            rem_http, rem_ms, entry = _remittance_status(session, auth, pay["remittance_id"], timeout)
            pay["rem_http"], pay["rem_ms"] = rem_http, rem_ms
            if entry:
                pay["rem_status"], pay["rem_amount"] = entry.get("status", ""), entry.get("amount_collected", "")
            else:
                pay["rem_status"] = "not found" if rem_http == 200 else f"HTTP {rem_http}"
            if rem_http != 200 and not row["error"]:
                row["error"] = f"remittance {pay['remittance_id']}: {rem_http}"
        pays.append(pay)

    row["_payments"] = pays
    if pays:
        codes = {p["status"] for p in pays}
        row["payments"] = 200 if codes <= {200, 201} else sorted(str(c) for c in codes if c not in (200, 201))[0]
        row["payments_ms"] = sum(p["ms"] for p in pays)
        rem = [p for p in pays if p["rem_http"] != ""]
        if rem:
            rcodes = {p["rem_http"] for p in rem}
            row["remittance"] = 200 if rcodes == {200} else sorted(str(c) for c in rcodes if c != 200)[0]
            row["remittance_ms"] = sum(p["rem_ms"] for p in rem)
        row["paid_centers"] = sum(1 for p in pays if p["status"] in (200, 201) and p["remittance_id"])
        row["paid_amount"] = round(sum(p["amount"] for p in pays if p["status"] in (200, 201) and p["remittance_id"]), 2)
        row["remittance_ids"] = " ".join(f"{p['remittance_id']}:{p['rem_status']}" for p in pays if p["remittance_id"])


def outcome(row: dict) -> str:
    """'ok'      every call returned 200
    'partial' the data was fetched (centers + every center's v2/collections = 200)
              but a side call (dashboard / menu) failed
    'failed'  login failed or centers / customer data could not be fetched"""
    if row["login"] == 200 and not row["error"]:
        return "ok"
    data_ok = (row["login"] == 200 and row["centers"] == 200
               and all(d[4] == 200 for d in row.get("_detail", [])))
    return "partial" if data_ok else "failed"


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
    ap.add_argument("--pay", action="store_true",
                    help="REAL payments: make-payments per center (least demand first) + remittance status")
    ap.add_argument("--max-payments", type=int, default=0,
                    help="with --pay: max center payments in the whole run (0 = no cap)")
    args = ap.parse_args()

    global PAY, MAX_PAYMENTS
    PAY, MAX_PAYMENTS = args.pay, args.max_payments
    nofile = _raise_open_files_limit()
    users = load_users(args.users, args.offset, args.limit)
    if not users:
        raise SystemExit(f"No users in {args.users}")

    os.makedirs(RESULTS_DIR, exist_ok=True)

    print(f"🌐 {BASE_URL}")
    print(f"👥 {len(users)} users · concurrency {args.concurrency} · date {args.date} · open-files limit {nofile}")
    print(f"💰 make-payments: " + (f"ON — REAL CASH payments per center"
                                    f"{f', max {MAX_PAYMENTS} center(s) in this run' if MAX_PAYMENTS else ', every center with demand'}"
                                    if PAY else "off (read-only; add --pay to submit)"))
    print()

    rows = []
    done = 0
    t0 = time.time()
    if True:  # per-user CSV output removed — keep on-screen summary only
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {pool.submit(run_user, u, args.date, args.timeout): u for u in users}
            for fut in as_completed(futures):
                row = fut.result()
                row["outcome"] = outcome(row)
                rows.append(row)
                done += 1
                mark = "✅" if row["login"] == 200 else "❌"  # mark = login result; other calls shown on the line
                lines = [f"{mark} {done:>5}/{len(users)}  {row['username']:<34} "
                         f"login={row['login']} dashboard={row['dashboard'] or '-'} menu={row['menu'] or '-'} "
                         f"centers={row['centers'] or '-'} collections={row['collections'] or '-'}  "
                         f"{('error=[' + row['error'] + ']  ') if row['error'] else ''}"
                         f"resp=[{row['login_resp']}]  "
                         f"{row['centers_n'] or 0} center(s)"
                         f"{(', ' + str(row['customers_total']) + ' customers') if row['customers_total'] != '' else ''}"
                         f"{(', total demand ' + format(row['demand_total'], ',.2f')) if row['demand_total'] != '' else ''}"
                         f"{(' (' + str(failed) + ' center(s) not fetched)') if (failed := sum(1 for d in row.get('_detail', []) if d[4] != 200)) else ''}"]
                pays = {p["center_id"]: p for p in row.get("_payments", [])}
                for name, cid, n, demand, st in row.get("_detail", []):
                    if st == 200:
                        lines.append(f"        • {name} ({cid}): {n} customer(s), total demand {demand:,.2f}")
                    else:
                        lines.append(f"        • {name} ({cid}): ❌ collections {st} — customers/demand not fetched")
                    p = pays.get(cid)
                    if p:
                        paid = p["status"] in (200, 201) and p["remittance_id"]
                        lines.append(f"            {'💰' if paid else '❌'} make-payments={p['status']} "
                                     f"{p['loans']} loan(s) {p['amount']:,.2f} CASH  "
                                     f"{('remittance=' + p['remittance_id'] + ' status=' + str(p['rem_status']) + (' amount=' + str(p['rem_amount']) if p['rem_amount'] != '' else '')) if p['remittance_id'] else ('[' + p['message'] + ']')}")
                if row.get("_payments"):
                    paid_customers = sum(p["loans"] for p in row["_payments"]
                                         if p["status"] in (200, 201) and p["remittance_id"])
                    lines.append(f"     💰 user {row['username']}: "
                                 f"{row['centers_n'] or 0} center(s), {row['customers_total'] or 0} customer(s) total · "
                                 f"paid {row['paid_centers']}/{len(row['_payments'])} center(s), "
                                 f"{paid_customers} customer(s), {row['paid_amount'] or 0:,.2f} collected")
                print("\n".join(lines))

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
    if PAY:
        tot_c = sum(r["paid_centers"] or 0 for r in rows)
        tot_a = sum(r["paid_amount"] or 0 for r in rows)
        print(f"\n  💰 make-payments: {tot_c} center(s) paid, {tot_a:,.2f} collected in CASH "
              f"(logs: results/make_payments_{args.date}_*.json, record: results/paid_loans_{args.date}.json)")
    ok = sum(1 for r in rows if outcome(r) == "ok")
    partial = sum(1 for r in rows if outcome(r) == "partial")
    with_centers = sum(1 for r in rows if isinstance(r["centers_n"], int) and r["centers_n"] > 0)
    client_err = sum(1 for r in rows if r["outcome"] == "failed" and r["error"] and any(f"[Errno {e}]" in r["error"] for e in CLIENT_ERRNOS))
    failed = len(rows) - ok - partial
    logged_in = sum(1 for r in rows if r["login"] == 200)
    print(f"\n  ✅ {logged_in}/{len(rows)} logged in (login=200) · ❌ {len(rows) - logged_in} login failed")
    print(f"  ✔️  {ok}/{len(rows)} fully OK · ⚠️  {partial} data fetched but dashboard/menu failed · "
          f"❌ {failed} failed (client-side/network: {client_err}, server/API: {failed - client_err})"
          f" · {with_centers} had centers")


if __name__ == "__main__":
    main()
