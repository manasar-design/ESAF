# flow_retail.py
"""
Retail hierarchy — merges the old RCM (web) and RCE (mobile) flows into one run.
Each retail user is a PAIR of logins (DATA/retail_credentials.txt):
    web_email    -> RCM web-app login
    mobile_email -> RCE mobile login
Password for both is flow_common.RESET_PASSWORD (Esaf@12345).

Phase A — RCM demand assignment (WEB, web_email):
    1. web login
    2. GET  /web/api/v1/organisation/retail_collection/demand_assignment?page=1&limit=10
    3. GET  /web/api/v1/organisation/retail_collection/demands/filters
    4. GET  /web/api/v1/organisation/collection_agency/<ASSIGN_AGENCY_ID>/executives

Phase B — RCE retail collection (MOBILE, mobile_email):
    5. mobile login (token/sourcing)
    6. GET  /api/v1/collection/dashboard/retail
    7. GET  /api/v1/collection/menu
    8. GET  /api/v1/collections?collection_date=<today>&modified_after=<ms>&loan_type=retail
    9. POST /api/v1/make-payments-retail   (DRY RUN by default — real money; see RETAIL_SUBMIT_PAYMENTS)

Phase C — demand re-assignment (WEB, web_email):
   10. web login
   11. GET  /web/api/v1/organisation/retail_collection/loans?page=1&limit=10
   12. GET  /web/api/v1/organisation/retail_collection/demands/filters
   13. GET  /web/api/v1/organisation/collection_agency/<REASSIGN_AGENCY_ID>/executives
   14. PUT  /web/api/v1/organisation/retail_collection/demand_reassignment/reassign
         payload: {loan_account_no, agency_id, executive_id, reason}
         agency_id = REASSIGN_AGENCY_ID (from the executives URL),
         executive_id = first executive id from step 13's response (falls back to REASSIGN_EXECUTIVE_ID).
"""
import time

import flow_common as fc
import test_collection as col  # reuse the mobile login + mobile BASE_URL (UAT)

NAME = "Retail"
USERS_FILE = "retail_credentials.txt"

# Agency to assign/re-assign to: a selection key, NOT request data. The agency_id
# itself is always resolved from the demands/filters response by this name.
TARGET_AGENCY_NAME = "Esthenos"

# "reason" strings for the PUT payloads (literal inputs you specified, not IDs).
ASSIGN_REASON = "Testing"
REASSIGN_REASON = "testing"

# ---- Phase toggles (which sub-flow to run) ----
# RCM = the WEB phases: A (demand assignment) + C (re-assignment).
# RCE = the MOBILE phase: B (retail collection).
# Set RUN_RCM=False to run RCE-only (mobile collection), excluding RCM.
RUN_RCM = False
RUN_RCE = True

# ---- Safety toggles ----
# make-payments-retail is a REAL-money POST and its payload schema is unconfirmed:
# False = build + return the payload without posting (dry run). Set True to submit.
RETAIL_SUBMIT_PAYMENTS = False
# These PUTs mutate data on UAT; requested explicitly, so on by default.
RETAIL_SUBMIT_ASSIGN = True
RETAIL_SUBMIT_REASSIGN = True

# ---- Web endpoints (RCM / assignment / re-assignment) ----
# Note: executives URLs are built at runtime from the agency_id resolved from
# demands/filters — no agency id is hardcoded anywhere.
DEMAND_ASSIGNMENT_URL = f"{fc.WEB_API}/organisation/retail_collection/demand_assignment?page=1&limit=10"
DEMANDS_FILTERS_URL   = f"{fc.WEB_API}/organisation/retail_collection/demands/filters"
# NOTE: assignment PUT endpoint is inferred from the reassign pattern — confirm it.
ASSIGN_URL            = f"{fc.WEB_API}/organisation/retail_collection/demand_assignment/assign"
LOANS_URL             = f"{fc.WEB_API}/organisation/retail_collection/loans?page=1&limit=10"
REASSIGN_URL          = f"{fc.WEB_API}/organisation/retail_collection/demand_reassignment/reassign"

# ---- Mobile endpoints (RCE) ----
MOBILE = f"{col.BASE_URL}/api/v1"
DASHBOARD_RETAIL_URL = f"{MOBILE}/collection/dashboard/retail"
COLLECTION_MENU_URL  = f"{MOBILE}/collection/menu"
MAKE_PAYMENTS_RETAIL_URL = f"{MOBILE}/make-payments-retail"


def _collections_retail_url() -> str:
    return (f"{MOBILE}/collections?collection_date={col.COLLECTION_DATE}"
            f"&modified_after={col.modified_after()}&loan_type=retail")


def _first_executive_id(executives_body) -> str:
    """Pull the first executive id from the executives response."""
    for e in (fc.first_list(executives_body) or []):
        if isinstance(e, dict):
            for k in ("executive_id", "id", "_id", "user_id"):
                if e.get(k):
                    return str(e[k])
    return ""


def _agencies(body) -> list:
    """Find the 'agencies' list anywhere in the demands/filters response."""
    if isinstance(body, dict):
        if isinstance(body.get("agencies"), list):
            return body["agencies"]
        for v in body.values():
            found = _agencies(v)
            if found:
                return found
    return []


def _agency_id_by_name(body, name: str) -> str:
    """agency_id of the agency whose agency_name matches `name` (case-insensitive)."""
    for a in _agencies(body):
        if isinstance(a, dict) and (a.get("agency_name") or "").strip().lower() == name.strip().lower():
            return str(a.get("agency_id") or "")
    return ""


def _executives_url(agency_id: str) -> str:
    return f"{fc.WEB_API}/organisation/collection_agency/{agency_id}/executives"


def _first_loan_account_no(loans_body) -> str:
    """First loan account number from the retail_collection/loans response."""
    for loan in (fc.first_list(loans_body) or []):
        if isinstance(loan, dict):
            for k in ("loan_account_no", "loan_account_number", "loan_account",
                      "account_no", "account_number", "loanAccountNo"):
                if loan.get(k):
                    return str(loan[k])
    return ""


def _demand_ids(demand_body, count: int = 1) -> list:
    """Pull up to `count` demand ids from the demand_assignment response."""
    ids = []
    for d in (fc.first_list(demand_body) or []):
        if isinstance(d, dict):
            for k in ("demand_id", "id", "_id"):
                if d.get(k):
                    ids.append(str(d[k]))
                    break
        if len(ids) >= count:
            break
    return ids


def run(request, user: dict, login_only: bool = False) -> dict:
    web_email = user.get("web_email") or user.get("email") or ""
    mobile_email = user.get("mobile_email") or ""
    pwd = fc.RESET_PASSWORD
    r = {"login": None, "ok": False, "steps": {}, "error": ""}

    # ===== Phase A — RCM web login + demand assignment =====
    web_auth = None
    if RUN_RCM:
        web_status, web_auth, ms = fc.web_login(request, web_email, pwd)
        r["login"] = fc.fmt(web_status, ms)
        r["ok"] = web_auth is not None
        if not web_auth:
            r["error"] = f"RCM web login failed for {web_email}"
            return r

    if RUN_RCM and not login_only:
        st, demand_body, ms = fc.web_get(request, DEMAND_ASSIGNMENT_URL, web_auth)
        r["steps"]["demand_assignment"] = fc.fmt(st, ms)
        st, filters_body, ms = fc.web_get(request, DEMANDS_FILTERS_URL, web_auth)
        r["steps"]["demands_filters"] = fc.fmt(st, ms)
        r["demands_filters_response"] = filters_body

        # agency_id resolved from demands/filters by agency_name (e.g. "Esthenos").
        agency_id = _agency_id_by_name(filters_body, TARGET_AGENCY_NAME)
        r["steps"]["agency"] = f"{TARGET_AGENCY_NAME}={agency_id}"
        st, assign_exec_body, ms = fc.web_get(request, _executives_url(agency_id), web_auth)
        r["steps"]["assign_executives"] = fc.fmt(st, ms)
        r["assign_executives_response"] = assign_exec_body

        # Assignment PUT — every id is fetched from the GETs above, nothing hardcoded:
        #   demand_ids   <- demand_assignment GET
        #   agency_id    <- demands/filters (Esthenos)
        #   executive_id <- that agency's executives GET (first row)
        assign_payload = {
            "demand_ids": _demand_ids(demand_body),
            "agency_id": agency_id,
            "executive_id": _first_executive_id(assign_exec_body),
            "reason": ASSIGN_REASON,
        }
        if RETAIL_SUBMIT_ASSIGN:
            st, _, ms = fc.web_put(request, ASSIGN_URL, web_auth, assign_payload)
            r["steps"]["demand_assign_put"] = fc.fmt(st, ms)
        else:
            r["steps"]["demand_assign_put"] = "DRY-RUN (not submitted)"
        r["assign_payload"] = assign_payload

    # ===== Phase B — RCE mobile login + retail collection =====
    if RUN_RCE and mobile_email:
        t = time.time()
        mob_auth = col.perform_login(request, mobile_email, pwd)
        mob_line = fc.fmt(200 if mob_auth else "fail", round((time.time() - t) * 1000))
        r["steps"]["rce_mobile_login"] = mob_line
        # When RCM is off, the mobile login is the primary login for this user.
        if not RUN_RCM:
            r["login"] = mob_line
            r["ok"] = mob_auth is not None
            if not mob_auth:
                r["error"] = f"RCE mobile login failed for {mobile_email}"
        if mob_auth and not login_only:
            st, _, ms = fc.web_get(request, DASHBOARD_RETAIL_URL, mob_auth)
            r["steps"]["dashboard_retail"] = fc.fmt(st, ms)
            st, _, ms = fc.web_get(request, COLLECTION_MENU_URL, mob_auth)
            r["steps"]["collection_menu"] = fc.fmt(st, ms)
            st, coll_body, ms = fc.web_get(request, _collections_retail_url(), mob_auth)
            r["steps"]["collections_retail"] = fc.fmt(st, ms)

            # make-payments-retail — dry run unless RETAIL_SUBMIT_PAYMENTS.
            payload = _build_retail_payment_payload(coll_body)
            if not RETAIL_SUBMIT_PAYMENTS:
                r["steps"]["make_payments_retail"] = "DRY-RUN (not submitted)"
                r["retail_payment_payload"] = payload
            else:
                st, _, ms = fc.api_post(request, MAKE_PAYMENTS_RETAIL_URL, mob_auth, payload)
                r["steps"]["make_payments_retail"] = fc.fmt(st, ms)
    elif RUN_RCE:
        r["steps"]["rce_mobile_login"] = "no mobile_email"

    # ===== Phase C — demand re-assignment (web) =====
    if RUN_RCM and not login_only:
        _, web_auth2, ms = fc.web_login(request, web_email, pwd)
        if web_auth2:
            st, loans_body, ms = fc.web_get(request, LOANS_URL, web_auth2)
            r["steps"]["loans"] = fc.fmt(st, ms)
            r["loans_response"] = loans_body
            st, filters_body2, ms = fc.web_get(request, DEMANDS_FILTERS_URL, web_auth2)
            r["steps"]["reassign_filters"] = fc.fmt(st, ms)
            agency_id2 = _agency_id_by_name(filters_body2, TARGET_AGENCY_NAME)
            st, exec_body, ms = fc.web_get(request, _executives_url(agency_id2), web_auth2)
            r["steps"]["reassign_executives"] = fc.fmt(st, ms)

            # Re-assignment PUT — every id fetched from the GETs above, nothing hardcoded:
            #   loan_account_no <- loans GET (first row)
            #   agency_id       <- demands/filters (Esthenos)
            #   executive_id    <- that agency's executives GET (first row)
            reassign_payload = {
                "loan_account_no": _first_loan_account_no(loans_body),
                "agency_id": agency_id2,
                "executive_id": _first_executive_id(exec_body),
                "reason": REASSIGN_REASON,
            }
            if RETAIL_SUBMIT_REASSIGN:
                st, _, ms = fc.web_put(request, REASSIGN_URL, web_auth2, reassign_payload)
                r["steps"]["reassign"] = fc.fmt(st, ms)
            else:
                r["steps"]["reassign"] = "DRY-RUN (not submitted)"
            r["reassign_payload"] = reassign_payload
        else:
            r["steps"]["reassign"] = "web login failed"

    return r


def _build_retail_payment_payload(collections_body) -> dict:
    """Best-effort make-payments-retail body from the retail collections rows.
    NOTE: the retail payment schema is UNCONFIRMED — verify before enabling
    RETAIL_SUBMIT_PAYMENTS. Mirrors the center payload's per-loan fields."""
    rows = fc.first_list(collections_body) or []
    payments = []
    for c in rows:
        if not isinstance(c, dict):
            continue
        demand = c.get("total_demand") or 0
        if demand <= 0:
            continue
        payments.append({
            "loan_account_no": c.get("loan_account_no") or c.get("application_id") or "",
            "application_id": c.get("application_id") or "",
            "total_collection": demand,
            "payment_mode": "CASH",
            "is_attended": True,
        })
    return {"collection_date": col.COLLECTION_DATE, "loan_type": "retail", "payments": payments}
