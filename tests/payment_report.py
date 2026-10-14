"""Print every customer submitted through make-payments on a given date.

Reads results/make_payments_<date>_*.json and shows, per center, each
customer with amount and result, then the overall counts.

    python payment_report.py               # today
    python payment_report.py 2026-10-14
"""
import glob
import json
import os
import sys
from datetime import date

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")


def load_logs(day):
    pattern = os.path.join(RESULTS_DIR, f"make_payments_{day}_*.json")
    logs = []
    for path in sorted(glob.glob(pattern)):
        with open(path) as f:
            logs.append(json.load(f))
    return sorted(logs, key=lambda log: log.get("timestamp", ""))


def customers_of(log):
    """Join request lines (amount, mode) with response status (name, ok)."""
    amounts = {}
    for group in (log.get("request_payload") or {}).get("group_payments", []):
        for line in group.get("individual_payment", []):
            amounts[str(line.get("application_id"))] = line

    body = log.get("response_body") or {}
    statuses = {str(s.get("application_id")): s
                for s in body.get("applications_status", []) or []}

    rows = []
    for app_id, line in amounts.items():
        status = statuses.get(app_id, {})
        rows.append({
            "application_id": app_id,
            "customer_name": status.get("customer_name", ""),
            "cif": status.get("customer_cif_id", ""),
            "amount": float(line.get("total_collection") or 0),
            "mode": line.get("payment_mode", ""),
            "ok": bool(status.get("is_successful")),
            "message": status.get("message", "no status in response"),
        })
    return rows, body


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    logs = load_logs(day)
    if not logs:
        print(f"No make-payments logs for {day} in {os.path.normpath(RESULTS_DIR)}")
        return

    total_customers = total_ok = 0
    total_amount = ok_amount = 0.0
    centers_ok = 0
    per_user = {}

    print(f"\nMake-payments submissions on {day}\n")
    for log in logs:
        rows, body = customers_of(log)
        center_name = next((s.get("center_name") for s in body.get("applications_status", []) or []
                            if s.get("center_name")), "")
        ok_rows = [r for r in rows if r["ok"]]
        center_amount = sum(r["amount"] for r in rows)
        submitted = log.get("submitted", True)

        print(f"■ {center_name or '?'} ({log.get('center_id')})  user={log.get('username')}  "
              f"HTTP {log.get('status')}  remittance={body.get('remittance_id', '-')}"
              f"{'' if submitted else '  [dry run]'}")
        print(f"   {'#':>3}  {'application_id':<18} {'cif':<10} {'customer':<28} {'amount':>11}  result")
        for i, r in enumerate(rows, 1):
            mark = "✅" if r["ok"] else "❌"
            print(f"   {i:>3}  {r['application_id']:<18} {r['cif']:<10} {r['customer_name'][:28]:<28} "
                  f"{r['amount']:>11,.2f}  {mark} {r['message']}")
        print(f"   → {len(ok_rows)}/{len(rows)} customers successful, ₹{center_amount:,.2f}\n")

        if not submitted:
            continue
        total_customers += len(rows)
        total_ok += len(ok_rows)
        total_amount += center_amount
        ok_amount += sum(r["amount"] for r in ok_rows)
        centers_ok += bool(body.get("success"))
        u = per_user.setdefault(log.get("username"), [0, 0, 0.0])
        u[0] += 1
        u[1] += len(ok_rows)
        u[2] += sum(r["amount"] for r in ok_rows)

    print("=" * 72)
    print(f"Centers submitted : {centers_ok}/{sum(1 for l in logs if l.get('submitted', True))} successful")
    print(f"Customers         : {total_ok}/{total_customers} successful")
    print(f"Amount            : ₹{ok_amount:,.2f} successful of ₹{total_amount:,.2f} submitted")
    print("Per user:")
    for user, (centers, customers, amount) in per_user.items():
        print(f"   {user:<32} centers={centers:<3} customers={customers:<4} ₹{amount:,.2f}")


if __name__ == "__main__":
    main()
