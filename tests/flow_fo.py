# flow_fo.py
"""
FO hierarchy (MOBILE). ONE login only — sourcing and collection share the same
mobile token (/api/v1/token/sourcing), so we log in once and reuse the token.

  1. login (once)
  2. Sourcing GET APIs (from test_business_sourcing, NO dedupe POST):
       products-categories -> workflow -> meeting-list-details
       -> centers -> customers -> customer details
  3. Collection APIs (from test_collection, same token):
       mobile/profile -> master_ref_data -> dashboard x2
       -> collection/dashboard/center -> collection/menu
       -> collections/centers -> v2/collections per center
       -> make-payments for the least-demand pending center
          (submitted when test_collection.SUBMIT_PAYMENTS is True)

The summary line shows each sourcing (src.*) and collection (col.*) API's HTTP code.
"""
import time

import flow_common as fc
import test_business_sourcing as bs
import test_collection as col

NAME = "FO"
USERS_FILE = "emails_PROD_FO.txt"


def _slug(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _sourcing_gets(request, auth: dict) -> None:
    """All sourcing GET APIs in app order (no customer/dedupe POST)."""
    bs.LAST_STATUS.clear()
    bs.get_products_categories(request, auth)
    bs.get_workflow_gl(request, auth)
    bs.get_meeting_list_details(request, auth)

    # Always use the FIRST center from the sourcing centers list.
    centers, _ = bs.get_sourcing_centers(request, auth)
    if centers:
        center = centers[0]
        cid = bs.extract_field(center, bs.CENTER_ID_FIELDS, "center_id")
        if cid:
            customers = bs.get_customers(request, auth, cid)
            if customers:
                custid = bs.extract_field(customers[0], bs.CUSTOMER_ID_FIELDS, "customer_id")
                if custid:
                    bs.get_customer_details(request, auth, custid)


def run(request, user: dict, login_only: bool = False) -> dict:
    email = user.get("email") or fc.user_label(user)
    password = fc.RESET_PASSWORD
    r = {"login": None, "ok": False, "steps": {}, "error": ""}

    # ---- ONE login; the same token is reused for sourcing AND collection ----
    t = time.time()
    auth = bs.perform_login(request, email, password)
    r["login"] = fc.fmt(200 if auth else "fail", round((time.time() - t) * 1000))
    r["ok"] = bool(auth)
    if not auth or login_only:
        return r

    # ---- Sourcing GET APIs (reusing the login) — commented out for now: collection only ----
    # _sourcing_gets(request, auth)
    # for name, code in bs.LAST_STATUS.items():
    #     r["steps"][f"src.{_slug(name)}"] = fc.fmt(code, bs.LAST_MS.get(name, "?"))

    # ---- Collection sequence (SAME token, no second login) + make-payments ----
    seq = col.run_collection_sequence(request, auth)
    skip = None
    if seq["center_id"] and seq["dues"]:
        col.LAST_PAID.clear()
        col.make_payments(request, auth, email, seq["center_id"], seq["dues"])
        r["amount"] = col.LAST_PAID.get("amount", 0.0)
        r["loans"] = col.LAST_PAID.get("loans", 0)
        r["uncertain"] = col.LAST_PAID.get("uncertain", 0.0)
        if "MAKE PAYMENTS" not in col.LAST_STATUS:
            skip = "skipped (already paid today)"
    elif col.LAST_STATUS.get("COLLECTION CENTERS") != 200:
        skip = f"skipped (centers API {col.LAST_STATUS.get('COLLECTION CENTERS')})"
    elif not seq["centers_list"]:
        skip = "skipped (no centers)"
    elif col.pending_centers_by_demand(seq["centers_list"]):
        skip = "skipped (pending centers, but v2/collections has no loans)"
    else:
        skip = "skipped (no pending demand)"

    for name, code in col.LAST_STATUS.items():
        r["steps"][f"col.{_slug(name)}"] = fc.fmt(code, col.LAST_MS.get(name, "?"))
    if skip:
        r["steps"]["col.make_payments"] = skip
    return r
