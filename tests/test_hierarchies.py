# test_hierarchies.py
"""
Orchestrator: run every hierarchy's own flow file, all users at once.

Each hierarchy has its own file (flow_<name>.py) exposing NAME, USERS_FILE and
run(request, email, password, login_only). This runner loads each hierarchy's
users from DATA/, then submits EVERY user across the selected hierarchies to one
thread pool so they all log in and act concurrently — each user on its own
Playwright request context, each worker thread with its own Playwright.

  python test_hierarchies.py                      # all hierarchies, all users
  python test_hierarchies.py BM Admin             # only these hierarchies
  python test_hierarchies.py --login-only         # login API only
  python test_hierarchies.py --concurrency 0      # fire ALL users at once (no cap)
  python test_hierarchies.py FO --concurrency 25 --limit 100

Flags:
  --login-only       call only each hierarchy's login API
  --concurrency N    parallel users in flight (default 20; 0 = all users at once)
  --limit N          cap users per hierarchy (default 0 = all)
  --offset N         skip the first N users of each file (e.g. --offset 1000 --limit 1000 = users 1001-2000)
"""
import json
import os
import re
import resource
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.sync_api import sync_playwright

import flow_common as fc
import flow_fo, flow_bm, flow_retail, flow_admin
import test_collection as col

# Registry: hierarchy name -> its flow module. Order defines run/summary order.
FLOWS = {m.NAME: m for m in (flow_fo, flow_bm, flow_retail, flow_admin)}

# Do NOT write anything to results/ during a hierarchy run: the collection GET
# sequence would otherwise create results/paid_loans_<date>.json via load_paid_loans.
def _read_only_paid_loans(date: str = col.COLLECTION_DATE) -> dict:
    path = col.paid_loans_path(date)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


col.load_paid_loans = _read_only_paid_loans  # read if present, never create/write

DEFAULT_CONCURRENCY = 20

# Default per-request timeout (ms). The prod sourcing-centers GET can take ~24s
# for a single user and much longer under concurrency, so 30s (Playwright's
# default) is too tight. 120s gives slow endpoints room without hanging forever.
REQUEST_TIMEOUT_MS = 120_000

# ------------------------------------------------------------
# Concurrency plumbing: one Playwright per worker thread (reused), a fresh
# request context per user, and a discard sink so the reused modules' verbose
# per-call dumps don't scramble the concise per-user output.
# ------------------------------------------------------------
_PRINT_LOCK = threading.Lock()
_TL = threading.local()
_REAL_STDOUT = sys.stdout


class _Sink:
    def write(self, *a, **k):
        return 0

    def flush(self):
        pass


def _emit(msg: str):
    with _PRINT_LOCK:
        _REAL_STDOUT.write(msg + "\n")
        _REAL_STDOUT.flush()


def _thread_playwright():
    if not hasattr(_TL, "pw"):
        _TL.pw = sync_playwright().start()
    return _TL.pw


def _stop_thread_playwright(barrier: threading.Barrier):
    """Stop this worker's Playwright. A sync Playwright must be stopped on the
    thread that started it; the barrier holds each worker until every worker has
    taken one stop task, so each thread gets exactly one. Without this the event
    loops are left for garbage collection at exit and print
    'Exception ignored in BaseEventLoop.__del__' tracebacks."""
    try:
        barrier.wait(timeout=60)
    except threading.BrokenBarrierError:
        pass
    pw = getattr(_TL, "pw", None)
    if pw is not None:
        try:
            pw.stop()
        except Exception:
            pass
        del _TL.pw


def _raise_open_file_limit():
    """Each worker's Playwright runs its own driver process and pipes, so high
    concurrency exhausts the default 1024 open-file limit. Event loops that can't
    open their socket pair then fail with "no attribute '_ssock'". Raise the soft
    limit to the hard limit."""
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < hard:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except (ValueError, OSError):
        pass


def run_one(name: str, user: dict, login_only: bool) -> dict:
    module = FLOWS[name]
    label = fc.user_label(user)
    request = _thread_playwright().request.new_context(timeout=REQUEST_TIMEOUT_MS)
    try:
        r = module.run(request, user, login_only)
    except Exception as e:
        r = {"login": "ERR", "ok": False, "steps": {}, "error": str(e)[:140]}
    finally:
        request.dispose()
    r["hierarchy"], r["email"] = name, label

    steps = "  ".join(f"{k}={v}" for k, v in (r.get("steps") or {}).items())
    if r.get("loans"):
        steps += f"  amount=₹{r['amount']:,.2f} ({r['loans']} loan(s))"
    _emit(f"  {'✅' if r.get('ok') else '❌'} {name:<7} {label:<40} login={r.get('login')}"
          f"{('  ' + steps) if steps else ''}{('  err=' + r['error']) if r.get('error') else ''}")

    return r


def _arg_int(flag: str, default: int) -> int:
    for i, a in enumerate(sys.argv):
        if a == flag and i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
        if a.startswith(flag + "="):
            return int(a.split("=", 1)[1])
    return default


def _positional_names() -> list:
    """Hierarchy names, skipping the values of space-form value flags."""
    value_flags = {"--concurrency", "--limit", "--offset"}
    names, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            if a in value_flags:
                skip = True
            continue
        names.append(a)
    return names


def main():
    _raise_open_file_limit()
    login_only = "--login-only" in sys.argv
    concurrency = _arg_int("--concurrency", DEFAULT_CONCURRENCY)
    limit = _arg_int("--limit", 0)
    offset = _arg_int("--offset", 0)

    names = _positional_names() or list(FLOWS)
    unknown = [n for n in names if n not in FLOWS]
    if unknown:
        raise SystemExit(f"Unknown hierarchy {unknown}. Choose from {list(FLOWS)}")

    # Build the full task list across all selected hierarchies.
    tasks = []
    for name in names:
        users = fc.load_users(FLOWS[name].USERS_FILE)[offset:]
        users = users[:limit] if limit else users
        if not users:
            print(f"  ⚠️  No credentials in DATA/{FLOWS[name].USERS_FILE} — add emails to run {name}.")
            continue
        tasks += [(name, user) for user in users]

    if not tasks:
        raise SystemExit("No users to run — fill the DATA credential files first.")

    # concurrency 0 = all users at the same time.
    workers = len(tasks) if concurrency == 0 else min(concurrency, len(tasks))

    print(f"🚀 Hierarchy runner — {', '.join(names)}"
          f"{'  (login only)' if login_only else ''}")
    print(f"   web root: {fc.WEB_ROOT}  |  {len(tasks)} user(s) · concurrency {workers} "
          f"· limit/hierarchy {limit or 'all'}{f' · offset {offset}' if offset else ''}\n")

    t0 = time.time()
    results = []
    sys.stdout = _Sink()  # hush reused modules; _emit writes to the real stdout
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_one, name, user, login_only) for (name, user) in tasks]
            for fut in as_completed(futures):
                results.append(fut.result())
            # Stop every worker's Playwright on its own thread before exit.
            barrier = threading.Barrier(workers)
            for f in [pool.submit(_stop_thread_playwright, barrier) for _ in range(workers)]:
                f.result()
    finally:
        sys.stdout = _REAL_STDOUT

    wall = time.time() - t0
    print(f"\n{'=' * 60}\n📊 SUMMARY — {len(results)} user(s) in {wall:.1f}s "
          f"({len(results) / max(wall, 0.001):.1f}/s, concurrency {workers})\n{'=' * 60}")
    for name in names:
        rows = [r for r in results if r["hierarchy"] == name]
        if not rows:
            continue
        ok = sum(1 for r in rows if r.get("ok"))
        print(f"\n  {name:<6} login OK {ok}/{len(rows)}"
              f"{('  · ' + str(len(rows) - ok) + ' failed') if ok < len(rows) else ''}")

        # Per-API status-code counts across all users of this hierarchy.
        # api_name -> {status_token: count}; "login" first, then steps in order seen.
        # api_times: api_name -> [ms, ...] parsed from the "(123ms)" suffix.
        api_counts, api_times, order = {}, {}, []

        def _tally(api, value):
            # one line for all per-center customer calls instead of one per center
            api = re.sub(r"^col\.customers_\w+$", "col.customers (per center)", api)
            text = str(value)
            # strip "(123ms)", but keep a skip reason like "skipped (no centers)"
            token = text if text.startswith("skipped") else text.split(" (", 1)[0]
            if api not in api_counts:
                api_counts[api] = {}
                api_times[api] = []
                order.append(api)
            api_counts[api][token] = api_counts[api].get(token, 0) + 1
            m = re.search(r"\((\d+)ms\)", text)
            if m:
                api_times[api].append(int(m.group(1)))

        for r in rows:
            _tally("login", r.get("login"))
            for step, val in (r.get("steps") or {}).items():
                _tally(step, val)

        for api in order:
            counts = api_counts[api]
            # most frequent status first
            parts = [f"{tok}×{n}" for tok, n in sorted(counts.items(), key=lambda kv: -kv[1])]
            times = api_times[api]
            timing = f"  min {min(times)}ms · max {max(times)}ms" if times else ""
            print(f"      {api:<28} {'  '.join(parts)}{timing}")

        # Total collected by make-payments (only flows that report "amount").
        paid = [r for r in rows if r.get("loans")]
        if paid:
            amount = sum(r.get("amount") or 0 for r in paid)
            loans = sum(r.get("loans") or 0 for r in paid)
            print(f"      {'TOTAL AMOUNT COLLECTED':<28} ₹{amount:,.2f}  "
                  f"({loans} loan(s) · {len(paid)} user(s))")
        unsure = [r for r in rows if r.get("uncertain")]
        if unsure:
            print(f"      {'POSSIBLY COLLECTED (5xx)':<28} ₹{sum(r['uncertain'] for r in unsure):,.2f}  "
                  f"({len(unsure)} user(s) — no confirmation; check the server)")


if __name__ == "__main__":
    main()
